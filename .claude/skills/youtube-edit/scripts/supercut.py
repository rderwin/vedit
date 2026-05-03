#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow>=10"]
# ///
"""Combine clips from multiple YouTube workdirs into one supercut.

Reads a `multi_edl.json` whose segments and clips reference *other*
workdirs (each prepared via the normal download/transcribe/analyze
pipeline). Each segment renders against its own source.mp4, then all
parts are normalized to target_dims and assembled with crossfades.

Schema:

    {
      "style": {"preset": "tiktok"},
      "target_dims": [1280, 720],
      "main": {
        "title": "Best of channel X",
        "segments": [
          {"workdir": "vedit-runs/abc", "start": 100.0, "end": 130.0,
           "label": "From stream A"},
          {"workdir": "vedit-runs/xyz", "start":  20.0, "end":  45.0,
           "label": "From stream B", "transition": "wipeleft"}
        ]
      },
      "clips": [
        {"workdir": "vedit-runs/abc", "slug": "best_a", "title": "...",
         "start": 100.0, "end": 115.0, "vertical": true}
      ]
    }

Usage:

    uv run --quiet supercut.py <out_workdir>

`out_workdir` is where the combined output lands (out/main.mp4 + clips/).
Source video paths are taken from each segment's `workdir` field —
relative paths resolve against the directory of multi_edl.json itself,
so a multi_edl.json sitting in vedit-runs/supercut-1/ can reference
vedit-runs/abc by writing "../abc".
"""
import json
import pathlib
import sys

# Reuse the polished pipeline from assemble.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import assemble as A
from assemble import (
    LOOKS, PRESETS, VERT_FITS, MUSIC_NAMES,
    render_clip, assemble_main, slugify,
    probe_dims,
)


def _resolve(base, p):
    """Resolve `p` (string or path) against base if not absolute."""
    pp = pathlib.Path(p)
    return pp if pp.is_absolute() else (base / pp).resolve()


def _normalize_part(src_part, target_dims, work_dir):
    """If src_part isn't at target_dims, re-encode with scale+pad."""
    w, h = target_dims
    cur_w, cur_h = probe_dims(src_part)
    if (cur_w, cur_h) == (w, h):
        return src_part
    out = work_dir / (src_part.stem + "_norm.mp4")
    A.run([
        "ffmpeg", "-y", "-i", str(src_part),
        "-vf",
        "scale={W}:{H}:force_original_aspect_ratio=decrease,"
        "pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1".format(W=w, H=h),
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out),
    ])
    return out


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: supercut.py <out_workdir>")
    out_workdir = pathlib.Path(sys.argv[1])
    spec_path = out_workdir / "multi_edl.json"
    if not spec_path.exists():
        sys.exit("missing " + str(spec_path))
    spec = json.loads(spec_path.read_text())

    # Resolve workdir refs against the multi_edl.json's directory so that
    # `../abc` style paths Just Work.
    base = spec_path.parent

    style = spec.get("style") or {}
    preset_name = style.get("preset", "default")
    if preset_name not in PRESETS:
        sys.exit("unknown preset: " + preset_name)
    preset = PRESETS[preset_name]

    def style_get(key):
        return style.get(key, preset[key])

    default_vertical_fit = style_get("vertical_fit")
    default_caption_style = style_get("caption_style")
    default_caption_mode = style_get("caption_mode")
    default_title_anim = style_get("title_anim")
    default_use_music = style_get("use_music")
    default_look = style_get("look")
    default_main_transition = style_get("main_transition")
    default_accent = style.get("accent", "#FFD24A")
    drop_fillers = bool(style.get("drop_fillers", False))

    target_dims = tuple(spec.get("target_dims") or [1280, 720])

    # Music & logo are looked for in the OUTPUT workdir, not source workdirs.
    music_path = None
    if default_use_music:
        for name in MUSIC_NAMES:
            p = out_workdir / name
            if p.exists():
                music_path = p
                break
    logo_path = None
    for name in ("logo.png", "logo.PNG", "logo.jpg"):
        p = out_workdir / name
        if p.exists():
            logo_path = p
            break

    out_dir = out_workdir / "out"
    out_dir.mkdir(exist_ok=True)
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(exist_ok=True)
    vert_dir = out_dir / "clips_vertical"
    tmp_dir = out_dir / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    print("[supercut] preset={} target={} sources from {}".format(
        preset_name, "x".join(str(x) for x in target_dims), base
    ))

    # ---- main compilation ----
    main_segs = (spec.get("main") or {}).get("segments") or []
    if main_segs:
        parts = []
        durations = []
        transitions = [None]
        for i, seg in enumerate(main_segs):
            seg_workdir = _resolve(base, seg["workdir"])
            seg_src = seg_workdir / "source.mp4"
            if not seg_src.exists():
                sys.exit("missing source for segment {}: {}".format(i, seg_src))
            seg_dims = probe_dims(seg_src)

            assets = tmp_dir / "main_{:03d}_assets".format(i)
            assets.mkdir(exist_ok=True)
            part = tmp_dir / "main_part_{:03d}.mp4".format(i)
            seg_speed = float(seg.get("speed", 1.0))
            render_clip(
                seg_src,
                float(seg["start"]),
                float(seg["end"]),
                part,
                vertical=False,
                title=seg.get("label"),
                captions=None,
                src_dims=seg_dims,
                work_assets=assets,
                is_main_part=True,
                title_anim=default_title_anim,
                accent=default_accent,
                look=seg.get("look", default_look),
                logo_path=logo_path,
                speed=seg_speed,
            )
            # Normalize cross-source dimensions if needed.
            part = _normalize_part(part, target_dims, tmp_dir)
            parts.append(part)
            seg_dur = (float(seg["end"]) - float(seg["start"])) / seg_speed
            durations.append(seg_dur)
            transitions.append(seg.get("transition", default_main_transition))

        end_card_spec = style.get("end_card")
        if end_card_spec:
            ec_path = tmp_dir / "main_end_card.mp4"
            ec_path, ec_dur = A.make_end_card_part(
                end_card_spec, target_dims, default_accent, ec_path,
            )
            parts.append(ec_path)
            durations.append(ec_dur)
            transitions.append(end_card_spec.get("transition", "fadeblack"))

        main_out = out_dir / "main.mp4"
        assemble_main(parts, main_out, durations,
                      music_path=music_path, transitions=transitions)
        print("main → {}".format(main_out))

    # ---- clips ----
    for i, clip in enumerate(spec.get("clips") or [], 1):
        cw = _resolve(base, clip["workdir"])
        c_src = cw / "source.mp4"
        if not c_src.exists():
            sys.exit("missing source for clip {}: {}".format(i, c_src))
        c_dims = probe_dims(c_src)
        c_transcript = []
        c_tpath = cw / "transcript.json"
        if c_tpath.exists():
            c_transcript = json.loads(c_tpath.read_text())

        slug = slugify(
            clip.get("slug") or clip.get("title"),
            "clip_{:02d}".format(i),
        )
        name = "{:02d}_{}.mp4".format(i, slug)
        start = float(clip["start"])
        end = float(clip["end"])
        title = clip.get("title")
        do_vertical = bool(clip.get("vertical"))
        cap_style = clip.get("caption_style", default_caption_style)
        cap_mode = clip.get("caption_mode", default_caption_mode)
        do_caps = clip.get("captions", do_vertical) and c_transcript

        assets_l = tmp_dir / "clip_{:02d}_l".format(i)
        assets_l.mkdir(exist_ok=True)
        render_clip(
            c_src, start, end, clips_dir / name,
            vertical=False,
            title=title,
            captions=None,
            src_dims=c_dims,
            work_assets=assets_l,
            title_anim=default_title_anim,
            accent=default_accent,
            look=clip.get("look", default_look),
            logo_path=logo_path,
            speed=float(clip.get("speed", 1.0)),
        )
        print("clip → {}".format(clips_dir / name))

        if do_vertical:
            vert_dir.mkdir(exist_ok=True)
            assets_v = tmp_dir / "clip_{:02d}_v".format(i)
            assets_v.mkdir(exist_ok=True)
            caps = (
                A.caption_chunks_for(
                    c_transcript, start, end,
                    mode=cap_mode, drop_fillers=drop_fillers,
                ) if do_caps else None
            )
            render_clip(
                c_src, start, end, vert_dir / name,
                vertical=True,
                title=title,
                captions=caps,
                src_dims=c_dims,
                work_assets=assets_v,
                vertical_fit=clip.get("vertical_fit", default_vertical_fit),
                caption_style=cap_style,
                title_anim=default_title_anim,
                accent=default_accent,
                look=clip.get("look", default_look),
                logo_path=logo_path,
                speed=float(clip.get("speed", 1.0)),
            )
            print("clip → {}".format(vert_dir / name))

    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("\ndone. output in {}".format(out_dir))


if __name__ == "__main__":
    main()
