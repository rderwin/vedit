#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow>=10"]
# ///
"""Assemble a polished main compilation + clips from <workdir>/edl.json.

Polish pipeline per cut:
  1. Frame-accurate trim from source.
  2. Color grade (mild contrast/saturation/sharpening).
  3. (vertical) blurred-fill 9:16 background with the source centered.
  4. Title card overlay with fade-in/out (when label/title present).
  5. (vertical clips) burned-in caption chunks from transcript.json.
  6. Audio: short fade-in/out at boundaries, then loudnorm to social target.

Main compilation parts are joined with short xfade + acrossfade transitions,
then a final loudnorm pass.

EDL schema:

    {
      "source": "source.mp4",
      "main": {
        "title": "...",
        "segments": [
          {"start": 12.0, "end": 48.0, "label": "shown briefly at start"}
        ]
      },
      "clips": [
        {
          "slug": "wipeout",
          "title": "Massive wipeout",      # title card, optional
          "start": 412.0,
          "end": 433.5,
          "vertical": true,                 # also produce 9:16 cut
          "captions": true                  # burn captions on the vertical
        }
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

# render_text lives next to this script; make sure it's importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from render_text import render_title_card, render_caption


SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")

VERT_W, VERT_H = 1080, 1920
TITLE_FADE = 0.4
TITLE_HOLD = 2.2
TITLE_TOTAL = TITLE_FADE * 2 + TITLE_HOLD  # 3.0s

COLOR_GRADE = "eq=contrast=1.05:saturation=1.10:gamma=1.02,unsharp=5:5:0.6:5:5:0.0"
LOUDNORM_CLIP = "loudnorm=I=-14:TP=-1.5:LRA=11"
LOUDNORM_MAIN = "loudnorm=I=-16:TP=-1.5:LRA=11"

VERT_BG_FILTER = (
    "split=2[bgsrc][fgsrc];"
    "[bgsrc]scale={W}:{H}:force_original_aspect_ratio=increase,"
    "crop={W}:{H},gblur=sigma=25[bg];"
    "[fgsrc]scale={W}:-2:force_original_aspect_ratio=decrease[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
).format(W=VERT_W, H=VERT_H)


def slugify(s, fallback="clip"):
    s = SLUG_RE.sub("_", s or "").strip("_").lower()
    return s[:60] or fallback


def run(cmd):
    print("+ " + " ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd, check=True)


def probe_dims(src):
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        str(src),
    ]).decode().strip()
    w, h = out.split("x")
    return int(w), int(h)


def caption_chunks_for(transcript, start, end, max_words=4, min_dur=0.7):
    """Find transcript phrases that fall inside [start, end].

    Returns a list of {start, end, text} relative to the *clip*, where
    each chunk is at most `max_words` long and lasts at least `min_dur`.
    """
    in_range = [t for t in transcript if start <= t["start"] < end]
    if not in_range:
        return []
    chunks = []
    for i, entry in enumerate(in_range):
        next_start = in_range[i + 1]["start"] if i + 1 < len(in_range) else end
        words = entry["text"].split()
        # split a long phrase into max_words chunks, divvying duration evenly
        sub_n = max(1, (len(words) + max_words - 1) // max_words)
        slice_dur = (next_start - entry["start"]) / sub_n
        for k in range(sub_n):
            seg_words = words[k * max_words:(k + 1) * max_words]
            if not seg_words:
                continue
            t0 = entry["start"] + k * slice_dur - start
            t1 = t0 + slice_dur
            chunks.append({
                "start": max(0.0, t0),
                "end": min(end - start, t1),
                "text": " ".join(seg_words),
            })
    # Enforce min_dur by extending too-short chunks (or merging trailing).
    out = []
    for c in chunks:
        if out and c["start"] - out[-1]["end"] < 0.05:
            out[-1]["end"] = c["start"]
        if c["end"] - c["start"] < min_dur:
            c["end"] = c["start"] + min_dur
        out.append(c)
    return out


def render_clip(
    src,
    start,
    end,
    out,
    *,
    vertical=False,
    title=None,
    captions=None,
    src_dims=None,
    work_assets=None,
    is_main_part=False,
):
    """Render one polished clip. Returns the output path."""
    dur = end - start
    if dur <= 0:
        raise ValueError("non-positive duration: {} → {}".format(start, end))

    if vertical:
        out_w, out_h = VERT_W, VERT_H
    else:
        out_w, out_h = src_dims

    # Build the video filter chain on stream [0:v]. Filters within a chain
    # flow with commas; chains are separated with semicolons.
    if vertical:
        full_chain = "[0:v]" + COLOR_GRADE + "," + VERT_BG_FILTER
    else:
        full_chain = "[0:v]" + COLOR_GRADE + "[base]"

    inputs = ["-i", str(src)]
    overlays = ["[base]"]
    next_input_idx = 1

    # PNG inputs get `-loop 1 -t DUR -framerate 30` so they become a
    # bounded video stream. Without this, the PNG is a single frame, which
    # breaks fade (sees one PTS) and lets the overlay-held frame outlive
    # the source.
    fps = 30
    png_input_prefix = ["-loop", "1", "-framerate", str(fps), "-t", "{:.3f}".format(dur)]

    # Caption overlays (captions list of {start,end,text} relative to clip)
    if captions:
        for i, c in enumerate(captions):
            cap_png = work_assets / "cap_{}.png".format(i)
            img = render_caption(c["text"], out_w, out_h)
            img.save(cap_png)
            inputs += png_input_prefix + ["-i", str(cap_png)]
            prev_label = overlays[-1]
            new_label = "[c{}]".format(i)
            overlays.append(new_label)
            full_chain += (
                ";{prev}[{idx}:v]overlay=0:0:format=auto:"
                "enable='between(t,{a},{b})'{nl}"
            ).format(
                prev=prev_label,
                idx=next_input_idx,
                a=round(c["start"], 3),
                b=round(c["end"], 3),
                nl=new_label,
            )
            next_input_idx += 1

    # Title overlay with alpha fade in/out.
    if title:
        title_png = work_assets / "title.png"
        img = render_title_card(title, out_w, out_h)
        img.save(title_png)
        inputs += png_input_prefix + ["-i", str(title_png)]
        prev_label = overlays[-1]
        full_chain += (
            ";[{idx}:v]format=rgba,"
            "fade=t=in:st=0:d={f}:alpha=1,"
            "fade=t=out:st={out_st}:d={f}:alpha=1[ttl];"
            "{prev}[ttl]overlay=0:0:format=auto:"
            "enable='between(t,0,{total})'[v]"
        ).format(
            idx=next_input_idx,
            f=TITLE_FADE,
            out_st=TITLE_FADE + TITLE_HOLD,
            total=TITLE_TOTAL,
            prev=prev_label,
        )
        next_input_idx += 1
    else:
        # rename last label to [v]
        full_chain += ";{}null[v]".format(overlays[-1])

    # Audio: short fade in/out, optional loudnorm (skip on parts that will
    # be normalized after concat to avoid double-normalization).
    a_filter_parts = [
        "afade=t=in:st=0:d=0.08",
        "afade=t=out:st={:.3f}:d=0.18".format(max(0.0, dur - 0.18)),
    ]
    if not is_main_part:
        a_filter_parts.append(LOUDNORM_CLIP)
    a_filter = ",".join(a_filter_parts)
    full_chain += ";[0:a]{}[a]".format(a_filter)

    cmd = [
        "ffmpeg", "-y",
        "-ss", "{:.3f}".format(start),
        "-to", "{:.3f}".format(end),
    ] + inputs + [
        "-filter_complex", full_chain,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd)
    return out


def assemble_main(parts, out, durations, xfade=0.5):
    """Concatenate `parts` (mp4 paths) with xfade + acrossfade transitions."""
    if len(parts) == 1:
        # one part — just transcode it through loudnorm
        run([
            "ffmpeg", "-y", "-i", str(parts[0]),
            "-c:v", "copy",
            "-af", LOUDNORM_MAIN,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out),
        ])
        return

    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]

    # Build xfade chain. After each xfade, total length = sum(durs) - xfade*(i)
    v_label = "[0:v]"
    a_label = "[0:a]"
    chain_parts = []
    cumulative = durations[0]
    for i in range(1, len(parts)):
        offset = cumulative - xfade
        new_v = "[v{}]".format(i)
        new_a = "[a{}]".format(i)
        chain_parts.append(
            "{prev}[{i}:v]xfade=transition=fade:duration={d}:offset={o}{nv}".format(
                prev=v_label, i=i, d=xfade, o=round(offset, 3), nv=new_v,
            )
        )
        chain_parts.append(
            "{prev}[{i}:a]acrossfade=d={d}:c1=tri:c2=tri{na}".format(
                prev=a_label, i=i, d=xfade, na=new_a,
            )
        )
        v_label = new_v
        a_label = new_a
        cumulative += durations[i] - xfade

    # Loudnorm has to live inside the filter graph since the audio stream
    # was produced by acrossfade (you can't mix -filter_complex with -af).
    chain_parts.append("{prev}{ln}[aout]".format(prev=a_label, ln=LOUDNORM_MAIN))
    a_label = "[aout]"

    filter_complex = ";".join(chain_parts)
    run([
        "ffmpeg", "-y",
    ] + inputs + [
        "-filter_complex", filter_complex,
        "-map", v_label, "-map", a_label,
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ])


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: assemble.py <workdir>")
    workdir = pathlib.Path(sys.argv[1])
    edl = json.loads((workdir / "edl.json").read_text())

    src = workdir / edl.get("source", "source.mp4")
    if not src.exists():
        sys.exit("source not found: " + str(src))
    src_dims = probe_dims(src)

    transcript = []
    tpath = workdir / "transcript.json"
    if tpath.exists():
        transcript = json.loads(tpath.read_text())

    out_dir = workdir / "out"
    out_dir.mkdir(exist_ok=True)
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(exist_ok=True)
    vert_dir = out_dir / "clips_vertical"
    tmp_dir = out_dir / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    # ---- main compilation ----
    main_segs = (edl.get("main") or {}).get("segments") or []
    if main_segs:
        parts = []
        durations = []
        for i, seg in enumerate(main_segs):
            assets = tmp_dir / "main_{:03d}_assets".format(i)
            assets.mkdir(exist_ok=True)
            part = tmp_dir / "main_part_{:03d}.mp4".format(i)
            render_clip(
                src,
                float(seg["start"]),
                float(seg["end"]),
                part,
                vertical=False,
                title=seg.get("label"),
                captions=None,
                src_dims=src_dims,
                work_assets=assets,
                is_main_part=True,
            )
            parts.append(part)
            durations.append(float(seg["end"]) - float(seg["start"]))
        main_out = out_dir / "main.mp4"
        assemble_main(parts, main_out, durations)
        print("main → {}".format(main_out))

    # ---- clips ----
    for i, clip in enumerate(edl.get("clips") or [], 1):
        slug = slugify(
            clip.get("slug") or clip.get("title"),
            "clip_{:02d}".format(i),
        )
        name = "{:02d}_{}.mp4".format(i, slug)

        start = float(clip["start"])
        end = float(clip["end"])
        title = clip.get("title")
        do_vertical = bool(clip.get("vertical"))
        # Captions default: on for vertical clips, off otherwise.
        caps_default = do_vertical
        do_caps = clip.get("captions", caps_default) and transcript

        # landscape version
        assets_l = tmp_dir / "clip_{:02d}_l".format(i)
        assets_l.mkdir(exist_ok=True)
        render_clip(
            src, start, end, clips_dir / name,
            vertical=False,
            title=title,
            captions=None,  # don't burn captions on landscape by default
            src_dims=src_dims,
            work_assets=assets_l,
        )
        print("clip → {}".format(clips_dir / name))

        if do_vertical:
            vert_dir.mkdir(exist_ok=True)
            assets_v = tmp_dir / "clip_{:02d}_v".format(i)
            assets_v.mkdir(exist_ok=True)
            caps = caption_chunks_for(transcript, start, end) if do_caps else None
            render_clip(
                src, start, end, vert_dir / name,
                vertical=True,
                title=title,
                captions=caps,
                src_dims=src_dims,
                work_assets=assets_v,
            )
            print("clip → {}".format(vert_dir / name))

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("\ndone. output in {}".format(out_dir))


if __name__ == "__main__":
    main()
