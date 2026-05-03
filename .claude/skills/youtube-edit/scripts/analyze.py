#!/usr/bin/env python3
"""Extract editing signals from <workdir>/source.mp4 and transcript.json.

Writes <workdir>/signals.json:

    {
      "duration": 7321.4,
      "loudness": [{"t": 0.0, "lufs": -23.4}, ...],   # 1s windows, EBU R128 momentary
      "scenes":   [12.4, 48.7, 121.0, ...],           # scene-cut timestamps
      "wpm":      [{"t": 0.0, "wpm": 130.0}, ...],    # 10s sliding window of words/min
      "loud_peaks": [{"t": 412.3, "lufs": -8.1}, ...] # local loudness maxima (top ~30)
    }

These are advisory signals to help pick highlights:
- High `loudness` + high `wpm` often marks reactions / excitement / laughter.
- `scenes` are good cut boundaries when present.
- `loud_peaks` is a quick "where did things get loud" shortlist.
"""
import json
import pathlib
import re
import subprocess
import sys


LOUDNESS_RE = re.compile(
    r"t:\s*([0-9.]+)\s+TARGET:[^\n]*\s+M:\s*(-?[0-9.]+|-inf)"
)
SHOWINFO_RE = re.compile(r"pts_time:([0-9.]+)")


def probe_duration(src):
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(src),
    ]).decode().strip()
    return float(out)


def loudness_curve(src):
    """Run ebur128 and parse momentary loudness samples."""
    cmd = [
        "ffmpeg", "-nostats", "-hide_banner",
        "-i", str(src),
        "-af", "ebur128=metadata=0:framelog=verbose",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    text = proc.stderr.decode("utf-8", errors="replace")
    samples = []
    # ebur128 emits a "Parsed_ebur128" log line every ~100ms with t and M values
    for m in LOUDNESS_RE.finditer(text):
        t = float(m.group(1))
        v = m.group(2)
        if v == "-inf":
            continue
        samples.append((t, float(v)))
    return samples


def downsample_loudness(samples, window=1.0):
    """Average loudness within `window`-second buckets."""
    if not samples:
        return []
    buckets = {}
    for t, v in samples:
        b = int(t // window)
        buckets.setdefault(b, []).append(v)
    out = []
    for b in sorted(buckets):
        vs = buckets[b]
        out.append({"t": round(b * window, 2), "lufs": round(sum(vs) / len(vs), 2)})
    return out


def find_loud_peaks(curve, top_n=30, min_gap=15.0):
    """Pick the top loudest 1s windows, separated by at least `min_gap` seconds."""
    if not curve:
        return []
    sorted_by_loud = sorted(curve, key=lambda e: -e["lufs"])
    peaks = []
    for entry in sorted_by_loud:
        if all(abs(entry["t"] - p["t"]) >= min_gap for p in peaks):
            peaks.append(entry)
        if len(peaks) >= top_n:
            break
    peaks.sort(key=lambda e: e["t"])
    return peaks


def scene_cuts(src, threshold=0.4):
    """Run ffmpeg's scene detector and parse showinfo timestamps."""
    cmd = [
        "ffmpeg", "-nostats", "-hide_banner",
        "-i", str(src),
        "-vf", "select='gt(scene,{})',showinfo".format(threshold),
        "-vsync", "vfr",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    text = proc.stderr.decode("utf-8", errors="replace")
    return sorted({round(float(m.group(1)), 2) for m in SHOWINFO_RE.finditer(text)})


def word_density(transcript, duration, window=10.0, step=2.0):
    """Sliding-window words-per-minute."""
    if not transcript:
        return []
    out = []
    t = 0.0
    while t < duration:
        lo, hi = t, t + window
        words = sum(
            len(e["text"].split())
            for e in transcript
            if lo <= e["start"] < hi
        )
        wpm = words * (60.0 / window)
        out.append({"t": round(t, 2), "wpm": round(wpm, 1)})
        t += step
    return out


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: analyze.py <workdir>")
    workdir = pathlib.Path(sys.argv[1])
    src = workdir / "source.mp4"
    if not src.exists():
        sys.exit("source not found: " + str(src))

    duration = probe_duration(src)
    print("[analyze] duration {:.1f}s".format(duration))

    print("[analyze] loudness curve …")
    raw = loudness_curve(src)
    curve = downsample_loudness(raw, window=1.0)
    peaks = find_loud_peaks(curve)
    print("[analyze]   {} samples, top peak {} LUFS".format(
        len(curve), peaks[0]["lufs"] if peaks else "n/a"
    ))

    print("[analyze] scene cuts …")
    scenes = scene_cuts(src)
    print("[analyze]   {} cuts".format(len(scenes)))

    transcript = []
    tpath = workdir / "transcript.json"
    if tpath.exists():
        transcript = json.loads(tpath.read_text())
    print("[analyze] word density …")
    wpm = word_density(transcript, duration)

    out = {
        "duration": round(duration, 2),
        "loudness": curve,
        "loud_peaks": peaks,
        "scenes": scenes,
        "wpm": wpm,
    }
    out_path = workdir / "signals.json"
    out_path.write_text(json.dumps(out, indent=2))
    print("[analyze] wrote {}".format(out_path))


if __name__ == "__main__":
    main()
