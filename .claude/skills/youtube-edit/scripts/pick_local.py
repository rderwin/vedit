#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["ollama>=0.3"]
# ///
"""Autonomous picker via Ollama — same job as claude_pick.py but runs
the LLM locally. No API key, no per-call cost; you trade quality for
privacy + zero spend.

Requires:
  - Ollama installed and running:  https://ollama.com
  - A model pulled, e.g.:  ollama pull llama3.1:8b

Usage:
  uv run --quiet pick_local.py <workdir>
                              [--mode highlights|shorts]
                              [--clips N] [--main-minutes M]
                              [--preset NAME]
                              [--model llama3.1:8b]
                              [--host http://localhost:11434]

Reads:  <workdir>/transcript.json, signals.json, source.info.json
Writes: <workdir>/auto_edl.json   (review and rename to edl.json)

Quality varies by model. Defaults to llama3.1:8b which works on most
modern laptops with 16GB+ RAM. For better picks at higher cost, try
qwen2.5:14b or llama3.1:70b on a beefy machine. For faster picks,
gemma2:9b.

When in Claude Code, the picker IS Claude — you don't need this. Use
this only for unattended runs without API access (cron jobs, batch
pipelines, privacy-restricted environments).
"""
import argparse
import json
import pathlib
import sys


DEFAULT_MODEL = "llama3.1:8b"


SYSTEM_PROMPT = """You are an expert video editor. Read a transcript + \
audio signals and pick the best moments. Emit ONLY valid JSON matching \
this schema (no prose, no markdown fences, no commentary):

{
  "source": "source.mp4",
  "style": {"preset": "<preset>"},
  "main": {
    "title": "<headline>",
    "segments": [
      {"start": <number>, "end": <number>, "label": "<short>"}
    ]
  },
  "clips": [
    {"slug": "<short_id>", "title": "<headline>",
     "start": <number>, "end": <number>, "vertical": true,
     "reason": "<why this moment is good>"}
  ]
}

PICKING CRAFT:
- Open with loud_peaks — pre-screened candidates. For each, check the \
transcript ±20s to confirm it's a real moment.
- Look 5–15s BEFORE a loudness peak for the SETUP (often a wpm spike).
- Each clip must be SELF-CONTAINED — no setup the viewer doesn't have.
- 12–30 seconds is the sweet spot; hook in the first 2 seconds.
- Pad ~0.5–1s before the first word, ~0.5s after the last beat.

MAIN COMPILATION:
- Cold open: most arresting moment first (often from late in the video).
- Order is NOT chronological — order for narrative impact.
- End on a strong beat, not on something that begs "what next?"

Pick exactly the requested number of clips and target the requested \
main duration. Use the requested preset string."""


def fmt_ts(s):
    s = int(round(s))
    return "{}:{:02d}".format(s // 60, s % 60)


def load_workdir(workdir):
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


def signals_block(signals):
    if not signals:
        return "(no signals.json)"
    parts = []
    duration = signals.get("duration")
    if duration:
        parts.append("Duration: {:.0f}s".format(duration))
    peaks = [p for p in (signals.get("loud_peaks") or [])
             if float(p["t"]) >= 30.0][:30]
    if peaks:
        parts.append("\nLoud peaks (top 30, ≥30s in):")
        for p in peaks:
            parts.append("  {}  {:.1f} LUFS".format(
                fmt_ts(float(p["t"])), float(p["lufs"])
            ))
    wpm_top = sorted(signals.get("wpm") or [], key=lambda e: -float(e.get("wpm", 0)))[:15]
    wpm_top.sort(key=lambda e: float(e["t"]))
    if wpm_top:
        parts.append("\nTop WPM windows (excited talking):")
        for e in wpm_top:
            parts.append("  {}  {:.0f} wpm".format(
                fmt_ts(float(e["t"])), float(e["wpm"])
            ))
    return "\n".join(parts)


def transcript_block(transcript):
    return "\n".join(
        "{}  {}".format(fmt_ts(float(e["start"])), e["text"])
        for e in transcript
    )


def build_prompt(transcript, signals, info, mode, clips, main_minutes,
                 preset):
    title = info.get("title", "")
    uploader = info.get("uploader") or info.get("channel") or ""
    duration = info.get("duration", 0)

    if mode == "shorts":
        instruction = (
            "Pick {} short vertical clips (12–30s each). NO main "
            "compilation — leave main.segments empty. Use preset='{}'. "
            "Set vertical: true on every clip."
        ).format(clips, preset)
    else:
        instruction = (
            "Pick a {:.0f}-minute main compilation arc plus {} short "
            "vertical clips. Order main segments for narrative impact "
            "(not chronologically). Use preset='{}'."
        ).format(main_minutes, clips, preset)

    body = (
        "Video: {}\nUploader: {}\nDuration: {} s\n\n"
        "=== SIGNALS ===\n{}\n\n"
        "=== TRANSCRIPT ===\n{}\n\n"
        "=== INSTRUCTION ===\n{}\n\n"
        "Emit only the JSON object. No prose."
    ).format(title, uploader, duration,
             signals_block(signals), transcript_block(transcript),
             instruction)
    return body


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
    ap.add_argument("--host", default=None,
                    help="Ollama host URL (default uses OLLAMA_HOST or http://localhost:11434)")
    ap.add_argument("--out", default="auto_edl.json")
    args = ap.parse_args()

    try:
        import ollama
    except ImportError:
        sys.exit(
            "ollama package not installed (uv run should have installed it).\n"
            "If you're invoking the script directly with python3, run:\n"
            "    pip install ollama"
        )

    workdir = pathlib.Path(args.workdir)
    transcript, signals, info = load_workdir(workdir)
    user_prompt = build_prompt(
        transcript, signals, info,
        args.mode, args.clips, args.main_minutes, args.preset,
    )

    print("[pick_local] mode={} preset={} model={}".format(
        args.mode, args.preset, args.model,
    ))
    print("[pick_local] transcript {} entries; signals {} peaks".format(
        len(transcript), len(signals.get("loud_peaks") or []),
    ))

    client = ollama.Client(host=args.host) if args.host else ollama.Client()

    # Use json format mode — Ollama enforces that the response is valid JSON.
    # Combined with the schema in the system prompt, gets us parseable output.
    try:
        resp = client.chat(
            model=args.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            format="json",
            options={
                "temperature": 0.6,  # some variance for creative picks
                "num_predict": 4096,
            },
        )
    except Exception as e:
        sys.exit(
            "[pick_local] Ollama call failed: {}\n"
            "  Is the daemon running? Start with: ollama serve\n"
            "  Have you pulled the model? ollama pull {}".format(e, args.model)
        )

    text = resp["message"]["content"]
    try:
        edl = json.loads(text)
    except json.JSONDecodeError as e:
        sys.exit(
            "[pick_local] failed to parse JSON from {}:\n{}\n\nRaw output (truncated):\n{}".format(
                args.model, e, text[:2000]
            )
        )

    edl["source"] = "source.mp4"
    out_path = workdir / args.out
    out_path.write_text(json.dumps(edl, indent=2))
    print("[pick_local] wrote {} ({} clips, {} main segments)".format(
        out_path,
        len(edl.get("clips") or []),
        len((edl.get("main") or {}).get("segments") or []),
    ))
    print()
    print("Review the picks (small local models often miss subtleties), then:")
    print("  mv {} {}".format(out_path, workdir / "edl.json"))
    print("  uv run --quiet .claude/skills/youtube-edit/scripts/assemble.py {}".format(
        workdir
    ))


if __name__ == "__main__":
    main()
