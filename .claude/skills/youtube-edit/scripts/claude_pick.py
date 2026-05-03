#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["anthropic>=0.40"]
# ///
"""Autonomous picker — calls Claude via the Anthropic API to write the EDL.

For unattended / batch runs (cron jobs, multi-video pipelines) where there
is no Claude in the loop. When the skill is invoked through Claude Code,
the picking happens *in* Claude Code and you don't need this script.

The transcript is the dominant cost; we put it inside a prompt-caching
breakpoint so a second call (e.g. picking shorts after picking highlights
on the same workdir) only pays the cache-read price (~10% of input cost).

Usage:
  ANTHROPIC_API_KEY=... \\
  uv run --quiet claude_pick.py <workdir> [--mode highlights|shorts]
                                          [--clips N] [--main-minutes M]
                                          [--preset NAME]
                                          [--model claude-sonnet-4-6]

Reads:  <workdir>/transcript.json, signals.json, source.info.json
Writes: <workdir>/auto_edl.json   (review and rename to edl.json)
"""
import argparse
import json
import os
import pathlib
import sys

import anthropic


# Default to Sonnet 4.6 — fast and cheap for a picker that gets called
# multiple times per video. Override with --model claude-opus-4-7 if you
# want stronger picks at higher cost.
DEFAULT_MODEL = "claude-sonnet-4-6"


SYSTEM_PROMPT = """You are an expert video editor picking highlight moments \
from a transcript and signals data. Your job: read the transcript, identify \
the funniest / most interesting / most quotable / most surprising moments, \
and emit an Edit Decision List (EDL) as JSON.

PICKING CRAFT (read carefully):
- The signals.json gives you `loud_peaks` — pre-screened "something happened \
here" candidates. Start there. For each peak, look at the transcript ±20s \
to confirm it's a real moment (reaction / punchline / wipeout / reveal) \
rather than music or background noise.
- Cross-check `wpm` spikes — fast talking often marks SETUP leading into a \
loudness peak. Look 5–15s BEFORE a loudness peak for the joke setup.
- Skim the transcript for content the audio doesn't surface: surprising \
claims, callbacks, "wait..." / "no way" / "look at this" beats.

CLIP REQUIREMENTS:
- Each clip must be SELF-CONTAINED — a stranger scrolling past should \
understand it without prior context. If understanding requires "earlier in \
the stream they were arguing about X", either include that setup IN the \
clip or drop the moment.
- Open with a hook in the first 2 seconds (often the punchline / reaction).
- 12-30 seconds is the sweet spot for shorts; 15-45s for highlights.
- Pad ~0.5–1s before the first word (auto-caption timestamps lag the audio) \
and ~0.5s after the last beat (let laughs land).

MAIN-COMPILATION CRAFT:
- Cold open: start with the single most arresting moment, often from late \
in the video. Hook before context.
- Build: alternate setups and payoffs. Vary energy.
- Order is NOT chronological — order for narrative impact.
- Strong close: end on a complete beat, not on something that begs "what \
happened next?"
"""


# JSON schema for the EDL — mirrors the EDL format documented in SKILL.md.
EDL_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "description": "Always 'source.mp4'"},
        "style": {
            "type": "object",
            "properties": {
                "preset": {"type": "string"},
            },
            "required": ["preset"],
            "additionalProperties": False,
        },
        "main": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "number"},
                            "end": {"type": "number"},
                            "label": {"type": "string"},
                        },
                        "required": ["start", "end", "label"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "segments"],
            "additionalProperties": False,
        },
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "vertical": {"type": "boolean"},
                    "reason": {
                        "type": "string",
                        "description": (
                            "One sentence on WHY this moment is good. "
                            "Helps a human reviewer judge picks fast."
                        ),
                    },
                },
                "required": [
                    "slug", "title", "start", "end", "vertical", "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["source", "style", "main", "clips"],
    "additionalProperties": False,
}


def fmt_ts(s):
    s = int(round(s))
    return "{}:{:02d}".format(s // 60, s % 60)


def load_workdir(workdir):
    """Load the three input files; tolerate missing signals/info."""
    t = json.loads((workdir / "transcript.json").read_text())
    s_path = workdir / "signals.json"
    signals = json.loads(s_path.read_text()) if s_path.exists() else {}
    i_path = workdir / "source.info.json"
    info = {}
    if i_path.exists():
        try:
            info = json.loads(i_path.read_text())
        except Exception:
            pass
    return t, signals, info


def transcript_block(transcript):
    """Format the transcript for the prompt — one line per phrase."""
    lines = []
    for e in transcript:
        t = float(e["start"])
        lines.append("{}  {}".format(fmt_ts(t), e["text"]))
    return "\n".join(lines)


def signals_block(signals):
    """Format the signal summary — only the parts useful to the picker."""
    if not signals:
        return "(no signals.json — pick from transcript only)"
    parts = []
    duration = signals.get("duration")
    if duration:
        parts.append("Duration: {:.0f}s ({})".format(duration, fmt_ts(duration)))
    peaks = signals.get("loud_peaks") or []
    # Skip the first 30s of peaks — usually intro music inflates loudness.
    peaks = [p for p in peaks if float(p["t"]) >= 30.0]
    if peaks:
        parts.append("\nLoud peaks (top 30, pre-screened candidates):")
        for p in peaks[:30]:
            parts.append("  {}  {:.1f} LUFS".format(
                fmt_ts(float(p["t"])), float(p["lufs"])
            ))
    scenes = signals.get("scenes") or []
    if scenes:
        parts.append("\nScene cuts (good cut boundaries):")
        for s in scenes[:50]:
            parts.append("  " + fmt_ts(float(s)))
    # Top wpm spikes — show only the highest, cap to 15 for compactness.
    wpm = signals.get("wpm") or []
    if wpm:
        top_wpm = sorted(wpm, key=lambda e: -float(e.get("wpm", 0)))[:15]
        top_wpm.sort(key=lambda e: float(e["t"]))
        parts.append("\nTop WPM windows (excited / fast talking):")
        for e in top_wpm:
            parts.append("  {}  {:.0f} wpm".format(
                fmt_ts(float(e["t"])), float(e["wpm"])
            ))
    return "\n".join(parts)


def build_user_prompt(transcript, signals, info, mode, clips, main_minutes,
                      preset):
    """Build the user message. The transcript+signals chunk is the cacheable
    prefix; the per-call instruction goes after."""
    title = info.get("title", "")
    uploader = info.get("uploader") or info.get("channel") or ""
    duration = info.get("duration", 0)

    metadata = "Video: {}\nUploader: {}\nDuration: {} s".format(
        title, uploader, duration,
    )

    body = (
        metadata
        + "\n\n=== SIGNALS ===\n"
        + signals_block(signals)
        + "\n\n=== TRANSCRIPT ===\n"
        + transcript_block(transcript)
    )

    if mode == "shorts":
        instruction = (
            "Pick {} short clips for vertical/short-form (TikTok / Reels / "
            "YT Shorts). Each clip should be 12–30s, self-contained, hook in "
            "the first 2s. NO main compilation — leave `main.segments` "
            "empty. Use style.preset = '{}'. Set vertical: true on every "
            "clip."
        ).format(clips, preset)
    else:
        instruction = (
            "Pick a {:.0f}-minute main compilation arc plus {} short vertical "
            "clips. Order the main segments for narrative impact (NOT "
            "chronologically). Use style.preset = '{}'."
        ).format(main_minutes, clips, preset)

    return body, instruction


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument(
        "--mode", choices=["highlights", "shorts"], default="highlights",
    )
    ap.add_argument("--clips", type=int, default=8)
    ap.add_argument("--main-minutes", type=float, default=5.0)
    ap.add_argument("--preset", default="tiktok")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default="auto_edl.json")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY not set in environment.")

    workdir = pathlib.Path(args.workdir)
    transcript, signals, info = load_workdir(workdir)

    cacheable_body, instruction = build_user_prompt(
        transcript, signals, info,
        args.mode, args.clips, args.main_minutes, args.preset,
    )

    print("[claude_pick] mode={} preset={} model={}".format(
        args.mode, args.preset, args.model,
    ))
    print("[claude_pick] transcript {} entries; signals {} peaks".format(
        len(transcript), len(signals.get("loud_peaks") or []),
    ))

    client = anthropic.Anthropic()

    # Cacheable user message: the transcript+signals dump is the same across
    # multiple modes (highlights, shorts, repeats), so we put `cache_control`
    # on it. The per-call `instruction` block sits after the breakpoint and
    # changes between calls without invalidating the cached prefix.
    response = client.messages.create(
        model=args.model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={
            "format": {"type": "json_schema", "schema": EDL_SCHEMA},
        },
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": cacheable_body,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": instruction,
                    },
                ],
            }
        ],
    )

    # Cache hit reporting — useful to confirm the prompt is set up right.
    u = response.usage
    print(
        "[claude_pick] usage: input={} cache_write={} cache_read={} output={}".format(
            u.input_tokens,
            getattr(u, "cache_creation_input_tokens", 0) or 0,
            getattr(u, "cache_read_input_tokens", 0) or 0,
            u.output_tokens,
        )
    )

    # Pull the JSON from the response.
    edl_text = next(
        (b.text for b in response.content if b.type == "text"), None
    )
    if not edl_text:
        sys.exit("Claude returned no text output. Stop reason: {}".format(
            response.stop_reason
        ))
    try:
        edl = json.loads(edl_text)
    except json.JSONDecodeError as e:
        sys.exit(
            "Failed to parse JSON from Claude:\n{}\n\nRaw output:\n{}".format(
                e, edl_text[:2000]
            )
        )

    # Force source field — Claude shouldn't be picking it.
    edl["source"] = "source.mp4"

    out_path = workdir / args.out
    out_path.write_text(json.dumps(edl, indent=2))
    print("[claude_pick] wrote {} ({} clips, {} main segments)".format(
        out_path,
        len(edl.get("clips") or []),
        len((edl.get("main") or {}).get("segments") or []),
    ))
    print()
    print("Review the picks, then:")
    print("  mv {} {}".format(out_path, workdir / "edl.json"))
    print("  uv run --quiet .claude/skills/youtube-edit/scripts/assemble.py {}".format(
        workdir
    ))


if __name__ == "__main__":
    main()
