#!/usr/bin/env python3
"""Parse VTT subtitles in <workdir> into transcript.json.

YouTube auto-captions arrive as a "rolling" stream where each cue contains
the previous line plus the new word(s). We dedupe so the output is one
entry per new phrase, with the timestamp of when that phrase appeared.
"""
import json
import pathlib
import re
import sys


TS_RE = re.compile(r"(\d+):(\d+):(\d+\.\d+)")
TIMING_RE = re.compile(
    r"(\d+:\d+:\d+\.\d+)\s*-->\s*(\d+:\d+:\d+\.\d+)"
)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def parse_ts(ts):
    m = TS_RE.match(ts)
    if not m:
        raise ValueError("bad timestamp: " + ts)
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


def parse_vtt(text):
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        timing_idx = None
        for i, ln in enumerate(lines):
            if "-->" in ln:
                timing_idx = i
                break
        if timing_idx is None:
            continue
        m = TIMING_RE.search(lines[timing_idx])
        if not m:
            continue
        start = parse_ts(m.group(1))
        end = parse_ts(m.group(2))
        body = " ".join(lines[timing_idx + 1 :])
        body = TAG_RE.sub("", body)
        body = WS_RE.sub(" ", body).strip()
        if body:
            cues.append({"start": start, "end": end, "text": body})
    return cues


def dedupe_rolling(cues):
    """For YouTube auto-captions, emit one entry per new phrase.

    If cue N+1 starts with cue N's text, only the suffix is new. Otherwise
    we emit the cue as-is.
    """
    out = []
    last_text = ""
    for c in cues:
        t = c["text"]
        if t == last_text:
            continue
        new = t
        if last_text and t.startswith(last_text):
            new = t[len(last_text):].strip()
        if new:
            out.append({"start": round(c["start"], 2), "text": new})
        last_text = t
    return out


def dedupe_simple(cues):
    """For whisper output (and other clean sources), drop only exact duplicates.

    The rolling-prefix logic in dedupe_rolling can mangle word-level cues
    where a word happens to be a prefix of the next ('the' → 'they' would
    keep just 'y'). Whisper doesn't produce rolling captions so we only
    need to filter exact dupes here.
    """
    out = []
    last_text = ""
    for c in cues:
        t = c["text"]
        if t == last_text:
            continue
        out.append({"start": round(c["start"], 2), "text": t})
        last_text = t
    return out


def looks_like_youtube_auto(text):
    """Heuristic: YouTube auto-caption files start with 'Kind: captions'."""
    return "Kind: captions" in text[:200]


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: parse_transcript.py <workdir>")
    workdir = pathlib.Path(sys.argv[1])
    vtts = sorted(workdir.glob("source*.vtt"))
    if not vtts:
        sys.exit("no source*.vtt in " + str(workdir))
    # Prefer manual over auto if both exist (manual filenames don't include .auto.)
    vtts.sort(key=lambda p: (".auto." in p.name, p.name))
    vtt = vtts[0]
    raw = vtt.read_text(encoding="utf-8")
    cues = parse_vtt(raw)
    if looks_like_youtube_auto(raw):
        transcript = dedupe_rolling(cues)
    else:
        transcript = dedupe_simple(cues)
    # Attach speaker labels from speaker_labels.json (produced by
    # transcribe_x.py with HF_TOKEN set). For each transcript entry,
    # find the speaker whose segment range contains the entry's start.
    sl_path = workdir / "speaker_labels.json"
    if sl_path.exists():
        try:
            speaker_segments = json.loads(sl_path.read_text())
        except Exception:
            speaker_segments = []
        speakers_attached = 0
        for e in transcript:
            t = float(e["start"])
            for seg in speaker_segments:
                if float(seg["start"]) <= t <= float(seg["end"]):
                    e["speaker"] = seg["speaker"]
                    speakers_attached += 1
                    break
        if speakers_attached:
            unique = len(set(e.get("speaker") for e in transcript if e.get("speaker")))
            print(
                "[parse] attached speaker labels to {} entries "
                "({} unique speakers)".format(speakers_attached, unique)
            )

    out = workdir / "transcript.json"
    out.write_text(json.dumps(transcript, indent=2))
    if transcript:
        last = transcript[-1]["start"]
        print(
            "wrote {} ({} entries, ~{:.0f}s of source from {})".format(
                out, len(transcript), last, vtt.name
            )
        )
    else:
        print("wrote {} (empty — VTT had no usable cues)".format(out))


if __name__ == "__main__":
    main()
