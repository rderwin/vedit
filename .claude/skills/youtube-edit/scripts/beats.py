#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["librosa>=0.10", "numpy<2"]
# ///
"""Detect beats in <workdir>/music.<ext> and emit <workdir>/beats.json.

Uses librosa.beat.beat_track (open source, ISC). Output:

    {
      "tempo_bpm": 120.5,
      "beats": [0.512, 1.014, 1.516, ...]   # output-timeline seconds
    }

Beats are in the *music track's* time coordinate, which equals the main
compilation's output timeline (the music plays from t=0 of the
compilation, looping if needed). So the assembler — or Claude when
picking — can snap segment boundaries to nearby beats.

Recommended usage: run after dropping music.mp3 in the workdir.

    uv run beats.py <workdir>

Then either:
  a) set `style.beat_sync: true` in the EDL — the assembler will shift
     each segment's end time to the nearest beat (within ±1.5s) before
     rendering. Cuts land on the music.
  b) read beats.json yourself when picking — choose segment ends near
     beat times for a tight musical edit.
"""
import json
import pathlib
import sys

import librosa


MUSIC_NAMES = ("music.mp3", "music.m4a", "music.wav", "music.ogg")


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: beats.py <workdir>")
    workdir = pathlib.Path(sys.argv[1])

    music = None
    for name in MUSIC_NAMES:
        p = workdir / name
        if p.exists():
            music = p
            break
    if music is None:
        sys.exit(
            "no music.<mp3|m4a|wav|ogg> in workdir " + str(workdir)
            + "\n(drop a track in first; see MUSIC.md for sources)"
        )

    print("[beats] loading {} ...".format(music.name))
    y, sr = librosa.load(str(music), sr=None, mono=True)

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    # tempo can be a numpy 0-d array
    try:
        tempo_bpm = float(tempo)
    except Exception:
        tempo_bpm = float(tempo[0])

    out = {
        "tempo_bpm": round(tempo_bpm, 2),
        "beats": [round(float(t), 3) for t in beat_times.tolist()],
    }
    out_path = workdir / "beats.json"
    out_path.write_text(json.dumps(out, indent=2))
    print("[beats] BPM≈{:.1f}, {} beats over {:.1f}s".format(
        tempo_bpm, len(out["beats"]),
        out["beats"][-1] if out["beats"] else 0.0,
    ))
    print("[beats] wrote {}".format(out_path))


if __name__ == "__main__":
    main()
