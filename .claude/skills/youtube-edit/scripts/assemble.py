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
from render_text import render_title_card, render_caption, render_end_card


SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")

VERT_W, VERT_H = 1080, 1920
TITLE_FADE = 0.4
TITLE_HOLD = 2.2
TITLE_TOTAL = TITLE_FADE * 2 + TITLE_HOLD  # 3.0s

LOOKS = {
    # Default — subtle pop, leaves the source mostly alone.
    "default":   "eq=contrast=1.05:saturation=1.10:gamma=1.02,"
                 "unsharp=5:5:0.6:5:5:0.0",
    # Cinematic — teal-orange-ish, slight contrast crush.
    "cinematic": "eq=contrast=1.12:saturation=1.05:gamma=0.98,"
                 "colorchannelmixer=rr=1.04:bb=0.92,"
                 "unsharp=5:5:0.5:5:5:0.0",
    # Warm — sunset / vlog warmth.
    "warm":      "eq=contrast=1.06:saturation=1.10:gamma=1.02,"
                 "colorchannelmixer=rr=1.08:gg=1.02:bb=0.90,"
                 "unsharp=5:5:0.4:5:5:0.0",
    # Cool — bluish, late-night / gameplay.
    "cool":      "eq=contrast=1.06:saturation=1.05:gamma=1.0,"
                 "colorchannelmixer=rr=0.92:bb=1.10,"
                 "unsharp=5:5:0.4:5:5:0.0",
    # Black-and-white — high-contrast docu look.
    "bw":        "eq=contrast=1.15:saturation=0:gamma=0.98,"
                 "format=gray,format=yuv420p,"
                 "unsharp=5:5:0.5:5:5:0.0",
    # Vibrant — pushed saturation for gameplay / sports.
    "vibrant":   "eq=contrast=1.10:saturation=1.30:gamma=1.04,"
                 "unsharp=5:5:0.8:5:5:0.0",
    # Punchy — flat-er but high-contrast, podcast-friendly.
    "punchy":    "eq=contrast=1.18:saturation=1.0:gamma=0.98,"
                 "unsharp=5:5:0.6:5:5:0.0",
}

LOUDNORM_CLIP = "loudnorm=I=-14:TP=-1.5:LRA=11"
LOUDNORM_MAIN = "loudnorm=I=-16:TP=-1.5:LRA=11"

# xfade transition catalog — accepted by ffmpeg's `xfade` filter. Listed so
# the SKILL.md and EDL author know what's available without diving into ffmpeg
# docs. Also used for validation.
XFADE_TRANSITIONS = {
    "fade", "fadeblack", "fadewhite", "fadegrays",
    "wipeleft", "wiperight", "wipeup", "wipedown",
    "wipetl", "wipetr", "wipebl", "wipebr",
    "slideleft", "slideright", "slideup", "slidedown",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "circlecrop",
    "rectcrop", "distance",
    "vertopen", "vertclose", "horzopen", "horzclose",
    "diagtl", "diagtr", "diagbl", "diagbr",
    "hlslice", "hrslice", "vuslice", "vdslice",
    "dissolve", "pixelize", "radial",
    "hblur", "squeezeh", "squeezev",
    "zoomin",
}

# Style presets bundle a coherent look. Per-key options in style{} override.
PRESETS = {
    "default": {
        "vertical_fit": "blur_fill",
        "caption_style": "minimal",
        "caption_mode": "phrase",
        "title_anim": "fade",
        "look": "default",
        "use_music": True,   # only kicks in if a music file is present
        "main_transition": "fade",
    },
    "tiktok": {
        "vertical_fit": "blur_fill",
        "caption_style": "pop",
        "caption_mode": "word",
        "title_anim": "slide",
        "look": "vibrant",
        "use_music": True,
        "main_transition": "fade",
    },
    "gameplay": {
        "vertical_fit": "fill_height",
        "caption_style": "pop",
        "caption_mode": "word",
        "title_anim": "slide",
        "look": "vibrant",
        "use_music": True,
        "main_transition": "slideleft",
    },
    "podcast": {
        "vertical_fit": "blur_fill",
        "caption_style": "bold",
        "caption_mode": "phrase",
        "title_anim": "fade",
        "look": "warm",
        "use_music": True,
        "main_transition": "fade",
    },
    "cinematic": {
        "vertical_fit": "blur_fill",
        "caption_style": "minimal",
        "caption_mode": "phrase",
        "title_anim": "fade",
        "look": "cinematic",
        "use_music": True,
        "main_transition": "fadeblack",
    },
    "documentary": {
        "vertical_fit": "blur_fill",
        "caption_style": "bold",
        "caption_mode": "phrase",
        "title_anim": "fade",
        "look": "bw",
        "use_music": True,
        "main_transition": "fadeblack",
    },
}

MUSIC_NAMES = ("music.mp3", "music.m4a", "music.wav", "music.ogg")

VERT_BLUR_FILL = (
    "split=2[bgsrc][fgsrc];"
    "[bgsrc]scale={W}:{H}:force_original_aspect_ratio=increase,"
    "crop={W}:{H},gblur=sigma=25[bg];"
    "[fgsrc]scale={W}:-2:force_original_aspect_ratio=decrease[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
).format(W=VERT_W, H=VERT_H)

# Scale the source up so it fills the 9:16 height, then center-crop sides.
# Better for screen-recorded content (game streams, chess.com, etc.) where
# the action lives in the center column — the content fills the frame
# instead of sitting tiny inside a blurred background.
VERT_FILL_HEIGHT = (
    "scale={W}:{H}:force_original_aspect_ratio=increase,"
    "crop={W}:{H},setsar=1[base]"
).format(W=VERT_W, H=VERT_H)

VERT_FITS = {
    "blur_fill": VERT_BLUR_FILL,
    "fill_height": VERT_FILL_HEIGHT,
}


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


FILLERS = {
    "um", "uh", "uhh", "um,", "uh,", "uhh,",
    "like,", "like.",
    "you", "know,",          # "you know,"
    "i", "mean,",            # "i mean,"
    "kinda,", "kind", "of,", # "kind of"
    "sort", "sorta,",
    "literally,",
}


def caption_chunks_for(
    transcript,
    start,
    end,
    *,
    max_words=4,
    min_dur=0.7,
    mode="phrase",
    drop_fillers=False,
    max_chunks=80,
):
    """Find transcript phrases inside [start, end] and chunk them for captions.

    `mode='phrase'` — group into ≤max_words chunks (current default).
    `mode='word'`   — one word per chunk, TikTok-2024 style. Caps at
                      `max_chunks` words; falls back to phrase mode if the
                      clip is too word-dense for ffmpeg's filter graph.
    """
    in_range = [t for t in transcript if start <= t["start"] < end]
    if not in_range:
        return []

    if mode == "word":
        # Distribute each phrase's duration evenly across its words.
        words = []
        for i, entry in enumerate(in_range):
            next_start = in_range[i + 1]["start"] if i + 1 < len(in_range) else end
            ws = entry["text"].split()
            if not ws:
                continue
            wdur = (next_start - entry["start"]) / len(ws)
            for k, w in enumerate(ws):
                t0 = entry["start"] + k * wdur - start
                if drop_fillers and _is_filler(w):
                    continue
                words.append({
                    "start": max(0.0, t0),
                    "end": min(end - start, t0 + wdur),
                    "text": w,
                })
        # Filter graph explodes on >max_chunks overlays; fall back gracefully.
        if len(words) > max_chunks:
            print(
                "[captions] {} words in clip exceeds cap {} — falling back "
                "to phrase mode".format(len(words), max_chunks)
            )
            return caption_chunks_for(
                transcript, start, end,
                max_words=max_words, min_dur=min_dur,
                mode="phrase", drop_fillers=drop_fillers,
                max_chunks=max_chunks,
            )
        # Ensure each word is on screen for at least min_dur.
        out = []
        for w in words:
            if w["end"] - w["start"] < min_dur:
                w["end"] = w["start"] + min_dur
            out.append(w)
        return out

    # phrase mode (default)
    chunks = []
    for i, entry in enumerate(in_range):
        next_start = in_range[i + 1]["start"] if i + 1 < len(in_range) else end
        words = entry["text"].split()
        if drop_fillers:
            words = [w for w in words if not _is_filler(w)]
            if not words:
                continue
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


def zoom_expression(peaks, *, max_zoom=1.06, dur=0.8):
    """Build a time-varying zoom factor for ffmpeg's `zoompan` filter.

    zoompan is the only filter that supports per-frame zoom evaluation.
    Its expression uses `time` (output timestamp in seconds), NOT `t`.

    Each peak ramps zoom up to `max_zoom` over `dur/2` seconds then back
    down over `dur/2`. Multiple peaks combine via max so overlaps just
    hold the highest zoom rather than compounding.

    Backslashes escape commas so the expression sits cleanly inside a
    filter argument without being split on commas.
    """
    if not peaks:
        return "1"
    delta = max_zoom - 1.0
    triangles = [
        "max(0\\,1-2*abs(time-{:.3f})/{:.3f})".format(float(p), float(dur))
        for p in peaks
    ]
    summed = "+".join(triangles)
    return "1+{:.4f}*min(1\\,{})".format(delta, summed)


def peaks_inside_clip(signals, start, end, *, top_n=3, min_lufs=-25.0):
    """Pick up to `top_n` loudness peaks that fall inside [start, end].

    Returns peak times relative to the clip start. Filters out peaks
    quieter than `min_lufs` (don't zoom on background noise).
    """
    peaks = []
    for p in (signals or {}).get("loud_peaks") or []:
        t = float(p["t"])
        if start <= t <= end and float(p["lufs"]) >= min_lufs:
            peaks.append((t - start, float(p["lufs"])))
    # Loudest first.
    peaks.sort(key=lambda x: -x[1])
    return [t for t, _ in peaks[:top_n]]


def _is_filler(word):
    return word.lower().strip(".,!?;:") in {"um", "uh", "uhh", "uhm", "er"}


def export_format(src_mp4, fmt, out_path, *, vertical_fit="blur_fill"):
    """Re-encode `src_mp4` into one of the export formats.

    fmt:
      'square'   — 1080×1080, center-cropped
      'vertical' — 1080×1920, using either blur_fill (fit width, blur bg)
                   or fill_height (fit height, crop sides)
    """
    if fmt == "square":
        vf = (
            "scale=1080:1080:force_original_aspect_ratio=increase,"
            "crop=1080:1080,setsar=1"
        )
    elif fmt == "vertical":
        if vertical_fit == "fill_height":
            vf = (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,setsar=1"
            )
        else:
            vf = (
                "split=2[bgsrc][fgsrc];"
                "[bgsrc]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,gblur=sigma=25[bg];"
                "[fgsrc]scale=1080:-2:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
            )
    else:
        raise ValueError("unknown export format: " + fmt)

    use_complex = fmt == "vertical" and vertical_fit != "fill_height"
    if use_complex:
        run([
            "ffmpeg", "-y", "-i", str(src_mp4),
            "-filter_complex", "[0:v]" + vf + "[v]",
            "-map", "[v]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(out_path),
        ])
    else:
        run([
            "ffmpeg", "-y", "-i", str(src_mp4),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(out_path),
        ])


def make_end_card_part(spec, dims, accent, out_path):
    """Render a 3–4s end card as an mp4 to use as the last main segment."""
    w, h = dims
    duration = float(spec.get("duration", 3.5))
    img = render_end_card(
        spec.get("title", ""),
        spec.get("subtitle", ""),
        spec.get("cta", ""),
        w, h, accent=accent,
    )
    png_path = out_path.parent / "end_card.png"
    img.save(png_path)
    run([
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", "30", "-t", "{:.3f}".format(duration),
        "-i", str(png_path),
        "-f", "lavfi", "-t", "{:.3f}".format(duration),
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ])
    png_path.unlink(missing_ok=True)
    return out_path, duration


def _atempo_chain(speed):
    """ffmpeg atempo accepts 0.5..2.0 per filter; chain stages for any ratio."""
    if 0.5 <= speed <= 2.0:
        return "atempo={:.4f}".format(speed)
    parts = []
    s = speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append("atempo={:.4f}".format(s))
    return ",".join(parts)


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
    vertical_fit="blur_fill",
    caption_style="minimal",
    title_anim="fade",
    accent="#FFD24A",
    look="default",
    logo_path=None,
    logo_position="top_right",
    logo_opacity=0.85,
    logo_scale=0.10,
    lut_path=None,
    speed=1.0,
    audio_clean=False,
    sfx_on_title=False,
    zoom_peaks=None,
    stabilize=False,
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
    color_grade = LOOKS.get(look, LOOKS["default"])
    if stabilize:
        # Single-pass deshake. Prepend so subsequent grade/scale work on
        # stable frames. (For top-tier results, vidstab two-pass is better,
        # but it requires a libvidstab-built ffmpeg — not in brew default.)
        color_grade = "deshake=blocksize=8:edge=mirror," + color_grade
    if lut_path is not None:
        # Apply 3D LUT BEFORE the local color grade so the LUT establishes
        # the look and the grade just nudges it.
        lut_filter = "lut3d=file='{}'".format(str(lut_path).replace("'", r"\'"))
        color_grade = lut_filter + "," + color_grade
    # Speed change via setpts (video) — audio handled below with atempo.
    # `fps=30` after setpts is essential: setpts only changes PTS values,
    # but ffmpeg's muxer needs a constant frame rate to compute the
    # correct output duration. Without it, fast-forwards don't actually
    # shorten the file.
    if speed != 1.0:
        color_grade = color_grade + ",setpts={:.4f}*PTS,fps=30".format(
            1.0 / speed
        )
    if vertical:
        fit_filter = VERT_FITS.get(vertical_fit, VERT_BLUR_FILL)
        full_chain = "[0:v]" + color_grade + "," + fit_filter
    else:
        full_chain = "[0:v]" + color_grade + "[base]"

    # Auto-zoom on reaction peaks. Inserts a time-varying zoompan AFTER
    # the base video is composed but BEFORE captions/title/logo so we
    # don't zoom into the title bar. zoom_peaks are clip-relative times
    # in source seconds; map to output time when speed != 1.
    if zoom_peaks:
        peaks_out = [t / speed for t in zoom_peaks]
        zexpr = zoom_expression(peaks_out)
        # `d=1` keeps frame rate (one out per in). `s` is the canvas size.
        full_chain = full_chain.replace(
            "[base]",
            ",zoompan=z='{z}':d=1:s={W}x{H}:fps=30[base]".format(
                z=zexpr, W=out_w, H=out_h,
            ),
        )

    inputs = ["-i", str(src)]
    overlays = ["[base]"]
    next_input_idx = 1

    # PNG inputs get `-loop 1 -t DUR -framerate 30` so they become a
    # bounded video stream. Important: bound to the EFFECTIVE duration
    # (dur/speed), so a sped-up clip's overlay PNGs don't outlive the
    # video (overlay's default behavior is to extend to the longer input).
    fps = 30
    eff_dur = dur / speed
    png_input_prefix = [
        "-loop", "1", "-framerate", str(fps),
        "-t", "{:.3f}".format(eff_dur),
    ]

    # Caption overlays (captions list of {start,end,text} relative to clip).
    # Caption timestamps are in source seconds; the output's `t` coordinate
    # is source_t / speed, so divide by speed when speed != 1.
    if captions:
        for i, c in enumerate(captions):
            cap_png = work_assets / "cap_{}.png".format(i)
            img = render_caption(c["text"], out_w, out_h,
                                 style=caption_style, accent=accent)
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
                a=round(c["start"] / speed, 3),
                b=round(c["end"] / speed, 3),
                nl=new_label,
            )
            next_input_idx += 1

    # Title overlay. Always uses an alpha fade; `title_anim="slide"` adds a
    # slide-in from the left + slide-out to the left, combined with the fade.
    pre_logo_label = "[v_pre_logo]"
    if title:
        title_png = work_assets / "title.png"
        img = render_title_card(title, out_w, out_h, accent=accent)
        img.save(title_png)
        inputs += png_input_prefix + ["-i", str(title_png)]
        prev_label = overlays[-1]
        out_st = TITLE_FADE + TITLE_HOLD
        if title_anim == "slide":
            x_expr = (
                "if(lt(t\\,{f}),-w*({f}-t)/{f},"
                "if(gt(t\\,{out_st}),-w*(t-{out_st})/{f},0))"
            ).format(f=TITLE_FADE, out_st=out_st)
            overlay_x = "x='{}'".format(x_expr)
        else:
            overlay_x = "x=0"
        full_chain += (
            ";[{idx}:v]format=rgba,"
            "fade=t=in:st=0:d={f}:alpha=1,"
            "fade=t=out:st={out_st}:d={f}:alpha=1[ttl];"
            "{prev}[ttl]overlay={ox}:y=0:format=auto:"
            "enable='between(t,0,{total})'{pl}"
        ).format(
            idx=next_input_idx,
            f=TITLE_FADE,
            out_st=out_st,
            total=TITLE_TOTAL,
            prev=prev_label,
            ox=overlay_x,
            pl=pre_logo_label,
        )
        next_input_idx += 1
    else:
        full_chain += ";{}null{}".format(overlays[-1], pre_logo_label)

    # Logo overlay (always-on watermark in a corner). Drop logo.png in the
    # workdir and the assembler scales + positions it at `logo_position`.
    if logo_path:
        # Logo PNG: scale to logo_scale * out_w wide (preserve aspect), then
        # apply opacity by chaining through `colorchannelmixer` on the alpha.
        logo_w = max(40, int(out_w * logo_scale))
        # Padding from frame edge.
        edge_pad = max(20, int(out_w * 0.022))
        positions = {
            "top_right":    "x=W-w-{p}:y={p}".format(p=edge_pad),
            "top_left":     "x={p}:y={p}".format(p=edge_pad),
            "bottom_right": "x=W-w-{p}:y=H-h-{p}".format(p=edge_pad),
            "bottom_left":  "x={p}:y=H-h-{p}".format(p=edge_pad),
        }
        ovl_pos = positions.get(logo_position, positions["top_right"])
        inputs += png_input_prefix + ["-i", str(logo_path)]
        full_chain += (
            ";[{idx}:v]scale={lw}:-2,format=rgba,"
            "colorchannelmixer=aa={op}[logo];"
            "{pl}[logo]overlay={pos}:format=auto[v]"
        ).format(
            idx=next_input_idx,
            lw=logo_w,
            op=logo_opacity,
            pl=pre_logo_label,
            pos=ovl_pos,
        )
        next_input_idx += 1
    else:
        full_chain += ";{}null[v]".format(pre_logo_label)

    # Audio chain. Order matters:
    #   1. (optional) afftdn FFT-based denoise — runs before everything else
    #      so loudnorm doesn't normalize against noise.
    #   2. atempo for speed change (must come before fades since it changes
    #      the duration).
    #   3. fades at boundaries.
    #   4. (per-clip) loudnorm to social target.
    a_filter_parts = []
    if audio_clean:
        # afftdn defaults are conservative; nr=12 is gentle, nf=-30 noise floor.
        a_filter_parts.append("afftdn=nr=12:nf=-30")
    if speed != 1.0:
        # atempo accepts 0.5..2.0 per stage; chain stages for bigger ratios.
        a_filter_parts.append(_atempo_chain(speed))
    a_filter_parts.append("afade=t=in:st=0:d=0.08")
    a_filter_parts.append(
        "afade=t=out:st={:.3f}:d=0.18".format(max(0.0, eff_dur - 0.18))
    )
    if not is_main_part:
        a_filter_parts.append(LOUDNORM_CLIP)
    a_voice_chain = ",".join(a_filter_parts)

    # Optional whoosh SFX synced to the title-in. We synthesize a quick
    # tone via lavfi as an *appended* input (so the existing PNG indices
    # stay stable), then mix it into the voice audio at t=0.
    if sfx_on_title and title:
        sfx_idx = next_input_idx
        # 0.35s sine "thump" — short and unobtrusive; landed sounds rather
        # than sustained tones work better as title accents.
        inputs += [
            "-f", "lavfi",
            "-t", "0.4",
            "-i", "sine=frequency=200:duration=0.4:sample_rate=44100",
        ]
        next_input_idx += 1
        full_chain += (
            ";[0:a]{voice}[v_aud];"
            "[{sfx}:a]volume=0.45,"
            "afade=t=in:st=0:d=0.02,afade=t=out:st=0.22:d=0.18[sfx];"
            "[v_aud][sfx]amix=inputs=2:duration=first:weights=1.0 1.0[a]"
        ).format(voice=a_voice_chain, sfx=sfx_idx)
    else:
        full_chain += ";[0:a]{}[a]".format(a_voice_chain)

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


def assemble_main(parts, out, durations, xfade=0.5, music_path=None,
                  transitions=None):
    """Concatenate `parts` (mp4 paths) with xfade + acrossfade transitions.

    `transitions[i]` is the xfade type to use *between part i-1 and part i*.
    Index 0 is unused. Defaults to "fade" everywhere.
    """
    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]

    music_idx = None
    if music_path is not None:
        music_idx = len(parts)
        # -stream_loop -1 makes ffmpeg loop the input file indefinitely so
        # the bed covers the whole compilation regardless of music length.
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]

    if len(parts) == 1 and music_idx is None:
        # Fast path — single part, no music — keep video stream copy.
        run([
            "ffmpeg", "-y", "-i", str(parts[0]),
            "-c:v", "copy",
            "-af", LOUDNORM_MAIN,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out),
        ])
        return

    # Build xfade chain. After each xfade, total length = sum(durs) - xfade*(i)
    v_label = "[0:v]"
    a_label = "[0:a]"
    chain_parts = []
    cumulative = durations[0]
    transitions = transitions or []
    for i in range(1, len(parts)):
        offset = cumulative - xfade
        new_v = "[v{}]".format(i)
        new_a = "[a{}]".format(i)
        tname = transitions[i] if i < len(transitions) and transitions[i] else "fade"
        if tname not in XFADE_TRANSITIONS:
            tname = "fade"
        chain_parts.append(
            "{prev}[{i}:v]xfade=transition={t}:duration={d}:offset={o}{nv}".format(
                prev=v_label, i=i, t=tname, d=xfade, o=round(offset, 3), nv=new_v,
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

    if music_idx is not None:
        # Split the voice into two streams: one for the mix, one as the
        # sidechain trigger that ducks the music. Mix voice + ducked music,
        # then loudnorm the result.
        chain_parts.append(
            "{prev}asplit=2[voice_a][voice_sc]".format(prev=a_label)
        )
        chain_parts.append(
            "[{m}:a]aformat=channel_layouts=stereo,volume=0.40[music_in]".format(
                m=music_idx
            )
        )
        chain_parts.append(
            "[music_in][voice_sc]sidechaincompress="
            "threshold=0.03:ratio=10:attack=5:release=400:level_sc=4[music_duck]"
        )
        chain_parts.append(
            "[voice_a][music_duck]amix=inputs=2:duration=first:"
            "weights=1.0 0.65,{ln}[aout]".format(ln=LOUDNORM_MAIN)
        )
    else:
        # No music — just loudnorm the voice stream directly.
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

    # Top-level style. A `preset` fills in defaults; explicit keys override.
    style = edl.get("style") or {}
    preset_name = style.get("preset", "default")
    if preset_name not in PRESETS:
        sys.exit(
            "unknown preset: {!r} (valid: {})".format(
                preset_name, ", ".join(sorted(PRESETS))
            )
        )
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
    default_sfx_on_title = bool(style.get("sfx_on_title", False))
    default_auto_zoom = bool(style.get("auto_zoom", False))
    default_stabilize = bool(style.get("stabilize", False))

    # Load signals.json (used for auto-zoom peaks).
    signals = {}
    spath = workdir / "signals.json"
    if spath.exists():
        signals = json.loads(spath.read_text())

    if default_vertical_fit not in VERT_FITS:
        sys.exit("unknown vertical_fit: {!r} (valid: {})".format(
            default_vertical_fit, ", ".join(sorted(VERT_FITS))))
    if default_look not in LOOKS:
        sys.exit("unknown look: {!r} (valid: {})".format(
            default_look, ", ".join(sorted(LOOKS))))

    # Music detection: look for music.<ext> in the workdir.
    music_path = None
    if default_use_music:
        for name in MUSIC_NAMES:
            p = workdir / name
            if p.exists():
                music_path = p
                break

    # Logo detection: look for logo.png / logo.jpg in the workdir.
    logo_path = None
    for name in ("logo.png", "logo.PNG", "logo.jpg"):
        p = workdir / name
        if p.exists():
            logo_path = p
            break
    logo_position = style.get("logo_position", "top_right")
    logo_opacity = float(style.get("logo_opacity", 0.85))
    logo_scale = float(style.get("logo_scale", 0.10))

    # 3D LUT detection: prefer style.lut path; otherwise look for lut.cube
    # in the workdir.
    lut_path = None
    style_lut = style.get("lut")
    if style_lut:
        cand = pathlib.Path(style_lut)
        if not cand.is_absolute():
            cand = workdir / style_lut
        if cand.exists():
            lut_path = cand
        else:
            sys.exit("style.lut not found: {}".format(cand))
    elif (workdir / "lut.cube").exists():
        lut_path = workdir / "lut.cube"

    # Audio cleanup default — on for podcast-y presets, off otherwise.
    default_audio_clean = bool(
        style.get("audio_clean",
                  preset_name in {"podcast", "documentary", "cinematic"})
    )

    print(
        "[style] preset={} look={} vfit={} caps={}/{} title={} music={} logo={}".format(
            preset_name, default_look, default_vertical_fit,
            default_caption_style, default_caption_mode,
            default_title_anim,
            music_path.name if music_path else "off",
            logo_path.name if logo_path else "off",
        )
    )

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
        transitions = [None]  # index 0 unused; one entry per segment.
        for i, seg in enumerate(main_segs):
            assets = tmp_dir / "main_{:03d}_assets".format(i)
            assets.mkdir(exist_ok=True)
            part = tmp_dir / "main_part_{:03d}.mp4".format(i)
            seg_speed = float(seg.get("speed", 1.0))
            seg_auto_zoom = bool(seg.get("auto_zoom", default_auto_zoom))
            seg_zoom_peaks = (
                peaks_inside_clip(signals, float(seg["start"]), float(seg["end"]))
                if seg_auto_zoom else None
            )
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
                title_anim=default_title_anim,
                accent=default_accent,
                look=seg.get("look", default_look),
                logo_path=logo_path,
                logo_position=logo_position,
                logo_opacity=logo_opacity,
                logo_scale=logo_scale,
                lut_path=lut_path,
                speed=seg_speed,
                audio_clean=default_audio_clean,
                sfx_on_title=default_sfx_on_title,
                zoom_peaks=seg_zoom_peaks,
                stabilize=bool(seg.get("stabilize", default_stabilize)),
            )
            parts.append(part)
            # Effective duration after speed change (matters for xfade offsets).
            seg_dur = (float(seg["end"]) - float(seg["start"])) / seg_speed
            durations.append(seg_dur)
            # transition[i] = transition INTO segment i (used between i-1 and i)
            transitions.append(seg.get("transition", default_main_transition))

        # Optional end-screen card appended as a final segment.
        end_card_spec = style.get("end_card")
        if end_card_spec:
            ec_path = tmp_dir / "main_end_card.mp4"
            ec_path, ec_dur = make_end_card_part(
                end_card_spec, src_dims, default_accent, ec_path,
            )
            parts.append(ec_path)
            durations.append(ec_dur)
            transitions.append(end_card_spec.get("transition", "fadeblack"))

        main_out = out_dir / "main.mp4"
        assemble_main(parts, main_out, durations,
                      music_path=music_path, transitions=transitions)
        print("main → {}".format(main_out))

        # Optional alternate-aspect exports of the main compilation.
        export_formats = style.get("export_formats") or []
        for fmt in export_formats:
            if fmt == "square":
                fmt_out = out_dir / "main_square.mp4"
            elif fmt == "vertical":
                fmt_out = out_dir / "main_vertical.mp4"
            else:
                print("[export] skipping unknown format: " + fmt)
                continue
            export_format(main_out, fmt, fmt_out, vertical_fit=default_vertical_fit)
            print("main → {}".format(fmt_out))

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

        cap_style = clip.get("caption_style", default_caption_style)
        cap_mode = clip.get("caption_mode", default_caption_mode)
        t_anim = clip.get("title_anim", default_title_anim)
        accent = clip.get("accent", default_accent)
        look = clip.get("look", default_look)

        clip_speed = float(clip.get("speed", 1.0))
        clip_audio_clean = bool(clip.get("audio_clean", default_audio_clean))
        clip_sfx = bool(clip.get("sfx_on_title", default_sfx_on_title))
        clip_auto_zoom = bool(clip.get("auto_zoom", default_auto_zoom))
        clip_zoom_peaks = (
            peaks_inside_clip(signals, start, end)
            if clip_auto_zoom else None
        )

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
            title_anim=t_anim,
            accent=accent,
            look=look,
            logo_path=logo_path,
            logo_position=logo_position,
            logo_opacity=logo_opacity,
            logo_scale=logo_scale,
            lut_path=lut_path,
            speed=clip_speed,
            audio_clean=clip_audio_clean,
            sfx_on_title=clip_sfx,
            zoom_peaks=clip_zoom_peaks,
            stabilize=bool(clip.get("stabilize", default_stabilize)),
        )
        print("clip → {}".format(clips_dir / name))

        if do_vertical:
            vert_dir.mkdir(exist_ok=True)
            assets_v = tmp_dir / "clip_{:02d}_v".format(i)
            assets_v.mkdir(exist_ok=True)
            caps = (
                caption_chunks_for(
                    transcript, start, end,
                    mode=cap_mode, drop_fillers=drop_fillers,
                ) if do_caps else None
            )
            v_fit = clip.get("vertical_fit", default_vertical_fit)
            render_clip(
                src, start, end, vert_dir / name,
                vertical=True,
                title=title,
                captions=caps,
                src_dims=src_dims,
                work_assets=assets_v,
                vertical_fit=v_fit,
                caption_style=cap_style,
                title_anim=t_anim,
                accent=accent,
                look=look,
                logo_path=logo_path,
                logo_position=logo_position,
                logo_opacity=logo_opacity,
                logo_scale=logo_scale,
                lut_path=lut_path,
                speed=clip_speed,
                audio_clean=clip_audio_clean,
                sfx_on_title=clip_sfx,
                zoom_peaks=clip_zoom_peaks,
                stabilize=bool(clip.get("stabilize", default_stabilize)),
            )
            print("clip → {}".format(vert_dir / name))

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("\ndone. output in {}".format(out_dir))


if __name__ == "__main__":
    main()
