#!/usr/bin/env python3
"""Assemble main compilation + clips from <workdir>/edl.json using ffmpeg.

EDL schema:

    {
      "source": "source.mp4",
      "main": {
        "title": "...",
        "segments": [{"start": 12.0, "end": 48.0, "label": "..."}, ...]
      },
      "clips": [
        {"slug": "wipeout", "title": "...", "start": 412.0, "end": 433.5,
         "vertical": true}
      ]
    }
"""
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import sys


SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def slugify(s, fallback="clip"):
    s = SLUG_RE.sub("_", s or "").strip("_").lower()
    return (s[:60] or fallback)


def run(cmd):
    print("+ " + " ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd, check=True)


def extract_segment(src, start, end, out, vertical=False):
    """Cut [start, end] from src into out. Re-encodes for frame-accurate cuts."""
    common = [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
    ]
    if vertical:
        # 9:16 with blurred-fill background so we don't crop subjects out.
        vf = (
            "[0:v]split=2[bgsrc][fgsrc];"
            "[bgsrc]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=25[bg];"
            "[fgsrc]scale=1080:-2:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
        run([
            "ffmpeg", "-y",
            "-ss", "{:.3f}".format(start),
            "-to", "{:.3f}".format(end),
            "-i", str(src),
            "-filter_complex", vf,
        ] + common + [str(out)])
    else:
        run([
            "ffmpeg", "-y",
            "-ss", "{:.3f}".format(start),
            "-to", "{:.3f}".format(end),
            "-i", str(src),
        ] + common + [str(out)])


def concat(parts, out):
    """Concat already-encoded mp4 parts (same codec settings) without re-encoding."""
    listfile = out.parent / (out.stem + "_concat.txt")
    listfile.write_text(
        "\n".join("file '{}'".format(p.resolve()) for p in parts) + "\n"
    )
    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(listfile),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out),
    ])
    listfile.unlink(missing_ok=True)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: assemble.py <workdir>")
    workdir = pathlib.Path(sys.argv[1])
    edl_path = workdir / "edl.json"
    if not edl_path.exists():
        sys.exit("missing " + str(edl_path))
    edl = json.loads(edl_path.read_text())

    src = workdir / edl.get("source", "source.mp4")
    if not src.exists():
        sys.exit("source not found: " + str(src))

    out_dir = workdir / "out"
    out_dir.mkdir(exist_ok=True)
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(exist_ok=True)
    vert_dir = out_dir / "clips_vertical"
    tmp_dir = out_dir / "tmp"

    # Main compilation.
    main_segs = (edl.get("main") or {}).get("segments") or []
    if main_segs:
        tmp_dir.mkdir(exist_ok=True)
        parts = []
        for i, seg in enumerate(main_segs):
            part = tmp_dir / "part_{:03d}.mp4".format(i)
            extract_segment(src, float(seg["start"]), float(seg["end"]), part)
            parts.append(part)
        main_out = out_dir / "main.mp4"
        if len(parts) == 1:
            parts[0].rename(main_out)
        else:
            concat(parts, main_out)
        print("main → {}".format(main_out))
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Clips.
    for i, clip in enumerate(edl.get("clips") or [], 1):
        slug = slugify(clip.get("slug") or clip.get("title"), "clip_{:02d}".format(i))
        name = "{:02d}_{}.mp4".format(i, slug)
        out_path = clips_dir / name
        extract_segment(src, float(clip["start"]), float(clip["end"]), out_path)
        print("clip → {}".format(out_path))
        if clip.get("vertical"):
            vert_dir.mkdir(exist_ok=True)
            v_path = vert_dir / name
            extract_segment(
                src, float(clip["start"]), float(clip["end"]), v_path, vertical=True
            )
            print("clip → {}".format(v_path))

    print("\ndone. output in {}".format(out_dir))


if __name__ == "__main__":
    main()
