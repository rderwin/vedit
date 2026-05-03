#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["opencv-python-headless>=4.9"]
# ///
"""Track the speaker's face across <workdir>/source.mp4 to drive smart 9:16
vertical crops that follow the subject instead of center-locking.

Outputs <workdir>/crop_track.json:

    {
      "src_w": 1280, "src_h": 720, "fps": 30.0,
      "samples": [{"t": 0.0, "x": 640}, {"t": 1.0, "x": 624}, ...]
    }

`x` is the smoothed center-x of the largest detected face at time t (in
source coordinates). The assembler reads this when style.vertical_fit
is "face_track" and builds a time-varying crop expression so the speaker
stays centered in the 9:16 frame.

Why OpenCV's Haar cascade rather than mediapipe: cascade ships with
opencv-python, no model download, fast on Apple Silicon, and accurate
enough for crop-window tracking (we only need the center, not landmarks).

Usage:
    uv run --quiet smart_crop.py <workdir> [--every 0.5] [--smooth 5]

  --every  seconds between samples (default 0.5)
  --smooth window size for moving average smoothing (default 5)
"""
import argparse
import json
import pathlib
import sys

import cv2


def detect_track(src, every_secs=0.5):
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError("could not open " + str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    cascade_path = (
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        raise RuntimeError("cascade load failed: " + cascade_path)

    samples = []
    last_x = width // 2  # default to center when no face found
    frame_step = max(1, int(round(fps * every_secs)))
    n = 0
    for f_idx in range(0, total, frame_step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, frame = cap.read()
        if not ret:
            break
        n += 1
        # Downscale for detection speed; keep aspect ratio.
        if width > 960:
            scale = 960 / width
            small = cv2.resize(frame, None, fx=scale, fy=scale)
        else:
            scale = 1.0
            small = frame
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
        )
        if len(faces):
            x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
            face_cx = (x + w // 2) / scale
            last_x = int(face_cx)
        # When no face — keep last_x so the crop drifts smoothly.
        samples.append({
            "t": round(f_idx / fps, 3),
            "x": last_x,
        })

    cap.release()
    return {
        "src_w": width,
        "src_h": height,
        "fps": fps,
        "samples": samples,
    }


def smooth_track(track, window=5):
    """Moving-average smooth the x track so the crop window glides."""
    samples = track["samples"]
    if len(samples) < 2 or window < 2:
        return track
    half = window // 2
    smoothed = []
    for i, s in enumerate(samples):
        lo = max(0, i - half)
        hi = min(len(samples), i + half + 1)
        avg_x = sum(samples[j]["x"] for j in range(lo, hi)) / (hi - lo)
        smoothed.append({"t": s["t"], "x": int(round(avg_x))})
    track = dict(track)
    track["samples"] = smoothed
    return track


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--every", type=float, default=0.5,
                    help="seconds between samples")
    ap.add_argument("--smooth", type=int, default=5,
                    help="moving-average window size (samples)")
    args = ap.parse_args()

    workdir = pathlib.Path(args.workdir)
    src = workdir / "source.mp4"
    if not src.exists():
        sys.exit("source not found: " + str(src))

    print("[smart_crop] running face detection on {} (every {}s)".format(
        src.name, args.every
    ))
    track = detect_track(src, every_secs=args.every)
    print("[smart_crop]   {} samples, {}x{} @ {:.1f} fps".format(
        len(track["samples"]), track["src_w"], track["src_h"], track["fps"]
    ))

    track = smooth_track(track, window=args.smooth)
    out = workdir / "crop_track.json"
    out.write_text(json.dumps(track, indent=2))
    print("[smart_crop] wrote {}".format(out))


if __name__ == "__main__":
    main()
