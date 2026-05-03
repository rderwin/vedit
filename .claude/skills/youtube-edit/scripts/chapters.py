#!/usr/bin/env python3
"""Generate YouTube chapter markers from <workdir>/chapters.json.

Reads:

    {
      "chapters": [
        {"t": 0,    "title": "Intro"},
        {"t": 92,   "title": "First game starts"},
        {"t": 1018, "title": "First win"},
        ...
      ]
    }

Writes <workdir>/out/chapters.txt, formatted for pasting into a YouTube
description (or anywhere that wants `MM:SS Title` / `HH:MM:SS Title`).
YouTube's rules: the first chapter must be at 00:00, chapters must be in
ascending order, and there must be at least three. The script enforces
the first two; the count is on you.
"""
import json
import pathlib
import sys


def fmt_ts(secs, force_hours=False):
    secs = int(round(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h or force_hours:
        return "{:d}:{:02d}:{:02d}".format(h, m, s)
    return "{:d}:{:02d}".format(m, s)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: chapters.py <workdir>")
    workdir = pathlib.Path(sys.argv[1])
    spec = json.loads((workdir / "chapters.json").read_text())
    chapters = sorted(
        (c for c in spec.get("chapters") or []),
        key=lambda c: float(c["t"]),
    )
    if not chapters:
        sys.exit("no chapters in chapters.json")

    if float(chapters[0]["t"]) != 0:
        # YouTube requires the first chapter to start at 0:00.
        chapters.insert(0, {"t": 0, "title": chapters[0]["title"]})

    has_hours = any(float(c["t"]) >= 3600 for c in chapters)
    lines = []
    for c in chapters:
        ts = fmt_ts(float(c["t"]), force_hours=has_hours)
        lines.append("{} {}".format(ts, c["title"]))

    out_dir = workdir / "out"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "chapters.txt"
    out_path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("\nwrote {}".format(out_path))


if __name__ == "__main__":
    main()
