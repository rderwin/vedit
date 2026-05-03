#!/usr/bin/env python3
"""Generate a starter EDL from signals.json + transcript.json.

Picks N candidate clip windows around the loudest non-overlapping moments,
pads each window so it doesn't strand half-sentences, and writes either:

  <workdir>/auto_edl.json  (default; review and rename to edl.json)

Usage:
  auto_edl.py <workdir> [--clips N] [--main-minutes M]
              [--clip-len-min S] [--clip-len-max S] [--preset NAME]

The output is a starting point. Read it, drop the misses (laughter at silence,
loud breaths, music swells), retitle the keepers, and rename to edl.json.
"""
import argparse
import json
import pathlib
import sys


def fmt_ts(s):
    s = int(round(s))
    return "{}:{:02d}".format(s // 60, s % 60)


def expand_window(transcript, peak_t, want_dur, min_dur=8.0, max_dur=45.0):
    """Pad [peak-want/2, peak+want/2] outward to nearest sentence boundaries."""
    half = want_dur / 2.0
    lo, hi = peak_t - half, peak_t + half
    # snap lo back to the start of whatever transcript line covers it
    for e in transcript:
        if e["start"] <= lo:
            lo = max(0.0, e["start"] - 0.4)
            break
    # snap hi forward — to the start of the line AFTER hi
    for e in transcript:
        if e["start"] > hi:
            hi = e["start"] - 0.1
            break
    dur = hi - lo
    if dur < min_dur:
        # Extend symmetrically until min_dur is met
        pad = (min_dur - dur) / 2
        lo, hi = max(0.0, lo - pad), hi + pad
    if dur > max_dur:
        # Trim symmetrically around the peak
        lo = max(lo, peak_t - max_dur / 2)
        hi = min(hi, peak_t + max_dur / 2)
    return round(lo, 2), round(hi, 2)


def first_sentence(transcript, lo, hi, max_words=8):
    """Best-effort one-line title from the transcript inside [lo, hi]."""
    in_range = [e for e in transcript if lo <= e["start"] <= hi]
    if not in_range:
        return None
    # Take the first non-trivial line.
    for e in in_range:
        text = e["text"].strip()
        if len(text.split()) >= 2:
            words = text.split()[:max_words]
            text = " ".join(words).rstrip(",.;:")
            return text[:60]
    return in_range[0]["text"][:60]


def slugify(s):
    out = []
    for ch in (s or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")[:40] or "clip"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--clips", type=int, default=8)
    ap.add_argument("--main-minutes", type=float, default=5.0)
    ap.add_argument("--clip-len", type=float, default=22.0,
                    help="target clip duration in seconds")
    ap.add_argument("--preset", default="tiktok",
                    help="style preset for the EDL (default | tiktok | gameplay | podcast | cinematic | documentary)")
    ap.add_argument("--out", default="auto_edl.json")
    args = ap.parse_args()

    workdir = pathlib.Path(args.workdir)
    sigs = json.loads((workdir / "signals.json").read_text())
    trans = json.loads((workdir / "transcript.json").read_text())

    peaks = list(sigs.get("loud_peaks") or [])
    # Skip the first 30s — usually intro music inflates loudness there.
    peaks = [p for p in peaks if float(p["t"]) >= 30.0]
    # Best peaks first.
    peaks.sort(key=lambda p: -float(p["lufs"]))

    # Build clips — one per peak, expanded outward, deduped against overlap.
    clips = []
    used_windows = []

    def overlaps(lo, hi):
        for ulo, uhi in used_windows:
            if not (hi < ulo or lo > uhi):
                return True
        return False

    for p in peaks:
        if len(clips) >= args.clips:
            break
        peak_t = float(p["t"])
        lo, hi = expand_window(trans, peak_t, args.clip_len)
        if overlaps(lo, hi):
            continue
        title = first_sentence(trans, lo, hi) or "Highlight at " + fmt_ts(peak_t)
        slug = slugify(title)
        clips.append({
            "slug": slug,
            "title": title.capitalize(),
            "start": lo,
            "end": hi,
            "vertical": True,
            "_peak_t": peak_t,
            "_peak_lufs": float(p["lufs"]),
        })
        used_windows.append((lo, hi))

    # Build main compilation: order by source time, target main_minutes.
    main_clips = sorted(clips, key=lambda c: c["start"])
    target_main_secs = args.main_minutes * 60
    main_segs = []
    total = 0.0
    for c in main_clips:
        seg_dur = c["end"] - c["start"]
        if total + seg_dur > target_main_secs * 1.25:
            break
        main_segs.append({
            "start": c["start"],
            "end": c["end"],
            "label": c["title"],
        })
        total += seg_dur

    # Strip private debug fields from the clips before writing.
    final_clips = [
        {k: v for k, v in c.items() if not k.startswith("_")}
        for c in clips
    ]

    edl = {
        "source": "source.mp4",
        "style": {"preset": args.preset},
        "main": {
            "title": "Highlights",
            "segments": main_segs,
        },
        "clips": final_clips,
    }

    out_path = workdir / args.out
    out_path.write_text(json.dumps(edl, indent=2))

    print("Wrote {} ({} clips, main {:.0f}s)".format(
        out_path, len(final_clips), total
    ))
    print()
    print("Clips:")
    for c in clips:
        print("  {} → {}  {:>5.1f}s  [{:.1f} LUFS]  {}".format(
            fmt_ts(c["start"]), fmt_ts(c["end"]),
            c["end"] - c["start"], c["_peak_lufs"], c["title"],
        ))
    print()
    print("Review the file, drop the misses, then:")
    print("  mv {} {}".format(out_path, workdir / "edl.json"))


if __name__ == "__main__":
    main()
