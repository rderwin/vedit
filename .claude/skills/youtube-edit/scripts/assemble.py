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
from render_text import (
    render_title_card,
    render_caption,
    render_caption_active,
    render_end_card,
    render_overlay_text,
)


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

# Encoder/quality presets.
QUALITY_PRESETS = {
    # Fast iteration — lower quality, smaller files, encodes ~2-3x faster.
    "fast":     {"codec": "libx264", "preset": "veryfast", "crf": "23", "audio_kbps": "160"},
    # Default — what we shipped before.
    "balanced": {"codec": "libx264", "preset": "medium",   "crf": "19", "audio_kbps": "192"},
    # Higher visual quality, bigger files, slower encode.
    "high":     {"codec": "libx264", "preset": "slow",     "crf": "17", "audio_kbps": "256"},
    # H.265/HEVC — half the file size at similar quality, slow encode.
    "h265":     {"codec": "libx265", "preset": "medium",   "crf": "21", "audio_kbps": "192"},
    # Archival — visually lossless, much bigger files.
    "archival": {"codec": "libx264", "preset": "slow",     "crf": "14", "audio_kbps": "320"},
}

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

# Synthesized comedy stings — short, punchy audio events to mix in at
# specific timestamps. Each value is an ffmpeg lavfi `-i` argument that
# generates the sound; the assembler delays each instance to its `t` and
# amix-es them into the voice. User-supplied wavs at <workdir>/sfx/<name>.wav
# override these — drop in a real vine-boom mp3 if the synthesized version
# isn't punchy enough.
STING_LAVFI = {
    # Bass thump — synthesized vine-boom. Low sine with sharp attack + decay.
    "boom":
        "aevalsrc='0.85*sin(2*PI*55*t)*exp(-t*4)':duration=0.6:sample_rate=44100",
    # Airhorn — rising tone with vibrato.
    "airhorn":
        "aevalsrc='0.55*sin(2*PI*(900+200*sin(2*PI*7*t))*t)*"
        "if(lt(t\\,0.05)\\,t/0.05\\,if(gt(t\\,0.55)\\,(0.6-t)/0.05\\,1))':"
        "duration=0.6:sample_rate=44100",
    # Riser — pitch sweep low to high; build-up before a reveal.
    "riser":
        "aevalsrc='0.5*sin(2*PI*(120+1500*t/1.0)*t)*"
        "if(gt(t\\,0.9)\\,(1.0-t)/0.1\\,t/0.4*(t<0.4)+(t>=0.4))':"
        "duration=1.0:sample_rate=44100",
    # Pop — bright cartoon click.
    "pop":
        "aevalsrc='0.7*sin(2*PI*1500*t)*exp(-t*30)':"
        "duration=0.15:sample_rate=44100",
    # Ding — bell tone.
    "ding":
        "aevalsrc='0.6*sin(2*PI*1200*t)*exp(-t*3)':"
        "duration=0.8:sample_rate=44100",
    # Sad trombone — pitch falls, womp-womp-womp.
    "trombone":
        "aevalsrc='0.5*sin(2*PI*220*pow(0.6\\,t)*t)*"
        "if(gt(t\\,1.0)\\,(1.1-t)/0.1\\,1)':"
        "duration=1.1:sample_rate=44100",
}

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
    # face_track is handled separately — needs the per-clip face_track
    # samples to build a time-varying crop expression. See build_face_track_filter.
    "face_track": "FACE_TRACK_PLACEHOLDER",
    # auto picks blur_fill / fill_height / face_track per-source — see
    # pick_auto_vertical_fit.
    "auto": "AUTO_PLACEHOLDER",
}


def pick_auto_vertical_fit(face_track, src_dims):
    """Heuristic for `vertical_fit: 'auto'`. Order:

    1. If a face_track is loaded AND ≥40% of samples are non-center AND
       the typical face spread is wide enough that the speaker actually
       moves, prefer face_track.
    2. Otherwise, if the source is wider than 16:9 (i.e. probably a
       desktop / screen recording with a sidebar), prefer fill_height.
    3. Otherwise, blur_fill (safest default for talking-head video).
    """
    src_w, src_h = src_dims
    if face_track:
        samples = face_track.get("samples") or []
        if samples:
            center = src_w / 2
            non_center = sum(
                1 for s in samples if abs(int(s["x"]) - center) > 60
            )
            if non_center / len(samples) >= 0.40:
                xs = [int(s["x"]) for s in samples]
                spread = max(xs) - min(xs)
                # Spread > ~12% of width → speaker actually moves around
                if spread >= src_w * 0.12:
                    return "face_track"
    # No reliable face — base on aspect ratio.
    if src_w / max(src_h, 1) > 1.85:
        return "fill_height"
    return "blur_fill"


def build_face_track_filter(track, clip_start, clip_end, src_w, src_h):
    """For a 9:16 vertical crop that follows the speaker.

    Crop a (src_h * 9/16) wide × src_h tall window from the source,
    centered on the smoothed face x at each sample, then scale to
    1080×1920. The crop x is a piecewise step expression built from the
    track samples that fall inside the clip.
    """
    samples = [s for s in track.get("samples") or []
               if clip_start <= s["t"] <= clip_end + 0.01]
    if not samples:
        # No samples inside window — fall back to centered crop.
        crop_w = int(src_h * VERT_W / VERT_H)  # 9/16 of source height
        x_expr = str((src_w - crop_w) // 2)
    else:
        crop_w = int(src_h * VERT_W / VERT_H)
        half = crop_w // 2
        # Build (rel_time, clamped_x) pairs.
        pts = []
        for s in samples:
            rel_t = max(0.0, s["t"] - clip_start)
            x = max(0, min(src_w - crop_w, int(s["x"]) - half))
            pts.append((rel_t, x))
        # Cascade: starts with the LAST sample's x as the default for
        # t >= last threshold, then walks backward inserting if-tests.
        x_expr = str(pts[-1][1])
        for i in range(len(pts) - 2, -1, -1):
            next_t = pts[i + 1][0]
            x = pts[i][1]
            x_expr = "if(lt(t\\,{:.3f})\\,{}\\,{})".format(next_t, x, x_expr)
    return (
        "crop={cw}:{ch}:'{xe}':0,"
        "scale={W}:{H}:flags=lanczos,setsar=1[base]"
    ).format(
        cw=crop_w, ch=src_h, xe=x_expr,
        W=VERT_W, H=VERT_H,
    )


def slugify(s, fallback="clip"):
    s = SLUG_RE.sub("_", s or "").strip("_").lower()
    return s[:60] or fallback


def run(cmd):
    print("+ " + " ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd, check=True)


def encoder_args(quality="balanced"):
    """Return ffmpeg `-c:v ... -c:a ...` args for the given quality preset."""
    q = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["balanced"])
    return [
        "-c:v", q["codec"], "-preset", q["preset"], "-crf", q["crf"],
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", q["audio_kbps"] + "k",
    ]


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
    show_speaker_labels=False,
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

    # Speaker-label prefixing — prepend "[ALICE]" to the first entry of
    # each speaker block (only on speaker change, not every line).
    if show_speaker_labels:
        new_in_range = []
        last_speaker = None
        for entry in in_range:
            e = dict(entry)
            spk = e.get("speaker")
            if spk and spk != last_speaker:
                e["text"] = "[{}] {}".format(spk.upper(), e["text"])
                last_speaker = spk
            new_in_range.append(e)
        in_range = new_in_range

    if mode in ("word", "word_active"):
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
                    "_emphasis": _is_emphasis(w),
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
        for w in words:
            if w["end"] - w["start"] < min_dur:
                w["end"] = w["start"] + min_dur

        if mode == "word":
            return words

        # word_active: build a 3-word context window per chunk, marking
        # which word is currently active. The window slides — at word i,
        # show [w[i-1], w[i], w[i+1]] with the middle one highlighted.
        out = []
        for i, w in enumerate(words):
            window = []
            active_idx = 0
            if i > 0:
                window.append(words[i - 1]["text"])
                active_idx = 1
            window.append(w["text"])
            if i + 1 < len(words):
                window.append(words[i + 1]["text"])
            out.append({
                "start": w["start"],
                "end": w["end"],
                "text": " ".join(window),
                "_words": window,
                "_active_idx": active_idx,
                "_emphasis": w.get("_emphasis", False),
            })
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


def _is_emphasis(word):
    """Detect punchline words for caption emphasis.

    True when the word is ALLCAPS (>=3 letters, to skip acronyms like OK)
    or ends with double punctuation `!!`, `??`, `?!`, `!?` — this is the
    convention pickers use to mark intent-to-emphasize. The user can also
    hand-edit transcript.json to flag specific moments.
    """
    if not word:
        return False
    bare = word.strip(".,;:'\"()[]{}<>")
    if not bare:
        return False
    if bare.endswith(("!!", "??", "?!", "!?")):
        return True
    letters = [c for c in bare if c.isalpha()]
    if len(letters) >= 3 and all(c.isupper() for c in letters):
        return True
    return False


def shake_expressions(shakes, *, max_offset_frac=0.018, max_zoom=1.030):
    """Build (zoom_term, x_term, y_term) ffmpeg expressions for camera shake.

    Camera shake is implemented inside the existing `zoompan` filter — we
    add a small base zoom (so there's crop headroom to translate within)
    plus time-bounded sinusoidal x/y offsets in source-pixel fractions.

    Each shake event is a {"t": clip_rel_sec, "dur": s, "intensity": 0..1}.
    The envelope is a triangle (0 → 1 → 0) over [t, t+dur/2, t+dur] so the
    shake fades in and out symmetrically. Different sin/cos frequencies on
    x and y avoid a perfect circular wobble — feels handheld, not robotic.

    Returned terms slot into:
      zoom_term  → another summand inside the zoompan z= expression
      x_term     → multiplier of iw, added to the zoompan x= expression
      y_term     → multiplier of ih, added to the zoompan y= expression
    Returns (None, None, None) if `shakes` is empty.
    """
    if not shakes:
        return None, None, None
    z_terms = []
    x_terms = []
    y_terms = []
    for s in shakes:
        t = float(s["t"])
        intensity = max(0.0, min(1.0, float(s.get("intensity", 1.0))))
        dur = max(0.05, float(s.get("dur", s.get("duration", 0.4))))
        # Triangle envelope: 0 at t, 1 at t+dur/2, 0 at t+dur.
        env = "max(0\\,1-2*abs(time-{:.3f})/{:.3f})".format(t + dur / 2, dur)
        z_terms.append("{:.4f}*{}".format(intensity * (max_zoom - 1.0), env))
        # sin(40t) / cos(53t) — coprime-ish frequencies for non-circular jitter.
        x_terms.append(
            "{:.4f}*sin(40*time)*{}".format(intensity * max_offset_frac, env)
        )
        y_terms.append(
            "{:.4f}*cos(53*time)*{}".format(intensity * max_offset_frac, env)
        )
    return "+".join(z_terms), "+".join(x_terms), "+".join(y_terms)


def punch_expression(punches, *, up_dur=0.10, default_hold=0.30,
                     down_dur=0.40, max_zoom=1.20):
    """Build a snap-zoom expression for ffmpeg's `zoompan` filter.

    Sharper than `zoom_expression` — zoom snaps in over `up_dur`, holds at
    peak, releases over `down_dur`. Used for explicit `clip.punches` (e.g.
    a reaction shot you want to hit hard) where the smooth triangle
    envelope of auto-zoom would feel too gentle.

    `punches` is a list of {"t": clip_rel_sec, "intensity": 0..1, "dur": s}.
    intensity 1.0 → full max_zoom (1.20×); 0.5 → halfway. dur sets how long
    the hold lasts (default 0.30s); total visible time is up + hold + down.

    Backslash-escaped commas keep the expression intact when it sits inside
    a quoted ffmpeg filter argument.
    """
    if not punches:
        return None
    delta = max_zoom - 1.0
    envs = []
    for p in punches:
        t = float(p["t"])
        intensity = max(0.0, min(1.0, float(p.get("intensity", 1.0))))
        if "dur" in p or "duration" in p:
            hold = max(
                0.0,
                float(p.get("dur", p.get("duration"))) - up_dur - down_dur,
            )
        else:
            hold = default_hold
        # Asymmetric envelope:
        #   ramp up: 0 → 1 over [t-up_dur, t]
        #   hold:  1 over [t, t+hold]
        #   ramp dn: 1 → 0 over [t+hold, t+hold+down_dur]
        # env = max(0, min((time-t+up)/up, 1, 1-(time-t-hold)/down))
        env = (
            "max(0\\,min((time-{t:.3f}+{u:.3f})/{u:.3f}\\,"
            "min(1\\,1-(time-{t:.3f}-{h:.3f})/{d:.3f})))"
        ).format(t=t, u=up_dur, h=hold, d=down_dur)
        envs.append("{:.4f}*{}".format(delta * intensity, env))
    summed = "+".join(envs)
    return "1+min(1\\,{})".format(summed)


def export_audio(src_mp4, out_path, quality="balanced"):
    """Strip video and emit an .m4a — for podcast distribution."""
    q = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["balanced"])
    run([
        "ffmpeg", "-y", "-i", str(src_mp4),
        "-vn",
        "-c:a", "aac", "-b:a", q["audio_kbps"] + "k",
        "-movflags", "+faststart",
        str(out_path),
    ])


def export_srt(transcript, out_path, *, max_chars=42, max_dur=5.0):
    """Emit an SRT subtitle file from transcript.json.

    SRT is the lingua franca of platform subtitle uploads (YouTube, Vimeo,
    Premiere) and gets you closed captions without re-uploading the video.
    Phrases are wrapped to ~`max_chars` per line and capped at `max_dur`
    seconds so subtitles don't sit on screen too long.
    """
    if not transcript:
        return False
    cues = []
    for i, e in enumerate(transcript):
        start = float(e["start"])
        # End of cue = next entry's start, capped at max_dur.
        if i + 1 < len(transcript):
            end = float(transcript[i + 1]["start"])
        else:
            end = start + max_dur
        end = min(end, start + max_dur)
        text = _wrap_subtitle(e["text"].strip(), max_chars)
        cues.append((start, end, text))

    def fmt_ts_srt(s):
        ms = int(round(s * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        sec, ms = divmod(ms, 1_000)
        return "{:02d}:{:02d}:{:02d},{:03d}".format(h, m, sec, ms)

    lines = []
    for n, (a, b, text) in enumerate(cues, 1):
        lines.append(str(n))
        lines.append("{} --> {}".format(fmt_ts_srt(a), fmt_ts_srt(b)))
        lines.append(text)
        lines.append("")
    out_path.write_text("\n".join(lines))
    return True


def _wrap_subtitle(text, max_chars):
    """Greedy wrap to ≤2 lines of ~max_chars each."""
    words = text.split()
    if not words:
        return ""
    lines = [""]
    for w in words:
        if not lines[-1]:
            lines[-1] = w
        elif len(lines[-1]) + 1 + len(w) <= max_chars:
            lines[-1] += " " + w
        elif len(lines) < 2:
            lines.append(w)
        else:
            # 3rd line — pile rest onto line 2 (will overflow but rare).
            lines[-1] += " " + w
    return "\n".join(lines)


def export_format(src_mp4, fmt, out_path, *, vertical_fit="blur_fill",
                  quality="balanced"):
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
    # The export pass doesn't re-encode audio (already mastered in main).
    # Take the video portion of encoder_args and override audio with -c:a copy.
    enc = encoder_args(quality)
    # Strip the audio args from enc and replace with copy.
    v_only = enc[:enc.index("-c:a")]
    if use_complex:
        run([
            "ffmpeg", "-y", "-i", str(src_mp4),
            "-filter_complex", "[0:v]" + vf + "[v]",
            "-map", "[v]", "-map", "0:a",
        ] + v_only + [
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(out_path),
        ])
    else:
        run([
            "ffmpeg", "-y", "-i", str(src_mp4),
            "-vf", vf,
        ] + v_only + [
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(out_path),
        ])


def make_end_card_part(spec, dims, accent, out_path, quality="balanced"):
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
    ] + encoder_args(quality) + [
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ])
    png_path.unlink(missing_ok=True)
    return out_path, duration


def snap_segments_to_beats(segments, beats, max_shift=1.5):
    """Adjust each (non-last) segment's end so the next cut lands on a beat.

    Returns a new list of segments (dicts) with `end` shifted by up to
    ±max_shift seconds when a beat is in range. The cumulative output
    duration is what gets aligned to the beat grid — the music plays
    from t=0 of the compilation, so this is also when the audio's
    downbeat hits.
    """
    if not beats or not segments:
        return segments
    cumulative = 0.0
    out = []
    for i, seg in enumerate(segments):
        seg = dict(seg)
        seg_dur = float(seg["end"]) - float(seg["start"])
        if i < len(segments) - 1:
            target = cumulative + seg_dur
            cands = [b for b in beats if abs(b - target) <= max_shift]
            if cands:
                nearest = min(cands, key=lambda b: abs(b - target))
                shift = nearest - target
                seg["end"] = float(seg["end"]) + shift
                seg_dur += shift
        out.append(seg)
        cumulative += seg_dur
    return out


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
    punches=None,
    shake=None,
    flashes=None,
    stabilize=False,
    face_track=None,
    quality="balanced",
    pip_path=None,
    pip_position="bottom_right",
    pip_scale=0.22,
    pip_offset=24,
    pip_round=True,
    stings=None,
    sfx_dir=None,
    text_overlays=None,
    cutaways=None,
    freezes=None,
):
    """Render one polished clip. Returns the output path."""
    dur = end - start
    if dur <= 0:
        raise ValueError("non-positive duration: {} → {}".format(start, end))

    # Freezes are conceptually a self-cutaway — pause video on a frame
    # while audio continues. We extract the frame at t and push it onto
    # the cutaways list, reusing the cutaway compositing path entirely.
    if freezes:
        cutaways = list(cutaways or [])
        for j, fr in enumerate(freezes):
            t_clip = float(fr.get("t", 0))
            t_dur = float(fr.get("dur", fr.get("duration", 0.5)))
            t_src = start + t_clip
            frame_png = (work_assets or pathlib.Path(".")) / "freeze_{}.png".format(j)
            (work_assets or pathlib.Path(".")).mkdir(parents=True, exist_ok=True)
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", "{:.3f}".format(t_src),
                "-i", str(src),
                "-frames:v", "1",
                str(frame_png),
            ], check=True)
            cutaways.append({
                "t": t_clip, "dur": t_dur, "src": str(frame_png.resolve()),
            })
    # Effective duration (post-speed). Used for PNG bounds, audio fades,
    # PiP trim, and the reverse-filter memory warning. abs() for negative
    # speed (reverse).
    eff_dur = dur / abs(speed) if speed != 0 else dur

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
    #
    # Negative `speed` reverses the clip. ffmpeg's `reverse` / `areverse`
    # filters buffer the entire stream into memory — fine for short clips,
    # expensive past ~30s. We use abs(speed) for the actual speed factor.
    do_reverse = speed < 0
    if do_reverse:
        if eff_dur > 30:
            print(
                "[reverse] {:.0f}s clip — reverse buffers the whole "
                "stream; this may use a lot of memory".format(eff_dur)
            )
        color_grade = color_grade + ",reverse"
        speed = abs(speed)
    if speed != 1.0:
        color_grade = color_grade + ",setpts={:.4f}*PTS,fps=30".format(
            1.0 / speed
        )
    if vertical:
        # Resolve `auto` → blur_fill | fill_height | face_track using the
        # heuristic in pick_auto_vertical_fit.
        if vertical_fit == "auto":
            chosen = pick_auto_vertical_fit(face_track, src_dims)
            print("[auto vertical_fit] picked '{}'".format(chosen))
            vertical_fit = chosen

        if vertical_fit == "face_track" and face_track is not None:
            fit_filter = build_face_track_filter(
                face_track, start, end,
                src_w=src_dims[0], src_h=src_dims[1],
            )
        else:
            fit_filter = VERT_FITS.get(vertical_fit, VERT_BLUR_FILL)
            if fit_filter == "FACE_TRACK_PLACEHOLDER":
                # Asked for face_track but no track loaded — fall back.
                print("[face_track] no crop_track.json — falling back to blur_fill")
                fit_filter = VERT_BLUR_FILL
        full_chain = "[0:v]" + color_grade + "," + fit_filter
    else:
        full_chain = "[0:v]" + color_grade + "[base]"

    # Auto-zoom + reaction-zoom punches + camera shake all feed the same
    # zoompan filter. Zoom factors combine via max() so overlapping peaks
    # don't compound past their own ceiling. Shake adds a small base zoom
    # (for crop headroom) plus x/y pixel offsets. Inserts AFTER the base
    # video is composed but BEFORE captions/title/logo so we don't zoom
    # into the title bar. Times are clip-relative in source seconds; map
    # to output time when speed != 1.
    if zoom_peaks or punches or shake:
        # Map all event times through speed so they hit at the right
        # OUTPUT time (zoompan's `time` is output seconds).
        zoom_parts = []
        if zoom_peaks:
            peaks_out = [t / speed for t in zoom_peaks]
            zoom_parts.append(zoom_expression(peaks_out))
        if punches:
            punches_out = []
            for p in punches:
                p2 = dict(p)
                p2["t"] = float(p["t"]) / speed
                if "dur" in p2:
                    p2["dur"] = float(p2["dur"]) / speed
                if "duration" in p2:
                    p2["duration"] = float(p2["duration"]) / speed
                punches_out.append(p2)
            pexpr = punch_expression(punches_out)
            if pexpr:
                zoom_parts.append(pexpr)
        shake_z = shake_x = shake_y = None
        if shake:
            shakes_out = []
            for s in shake:
                s2 = dict(s)
                s2["t"] = float(s["t"]) / speed
                if "dur" in s2:
                    s2["dur"] = float(s2["dur"]) / speed
                if "duration" in s2:
                    s2["duration"] = float(s2["duration"]) / speed
                shakes_out.append(s2)
            shake_z, shake_x, shake_y = shake_expressions(shakes_out)
            if shake_z:
                zoom_parts.append("1+{}".format(shake_z))

        # Reduce zoom_parts via max() into a single z= expression.
        if len(zoom_parts) == 1:
            zexpr = zoom_parts[0]
        else:
            zexpr = zoom_parts[0]
            for p in zoom_parts[1:]:
                zexpr = "max({}\\,{})".format(zexpr, p)

        if shake_x:
            # zoompan default x/y center the zoom; we add the shake delta.
            # Source-pixel scale: multiply iw/ih by the fractional offset.
            x_expr = "iw/2-(iw/zoom/2)+iw*({})".format(shake_x)
            y_expr = "ih/2-(ih/zoom/2)+ih*({})".format(shake_y)
            zoompan_str = (
                ",zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps=30[base]"
            ).format(z=zexpr, x=x_expr, y=y_expr, W=out_w, H=out_h)
        else:
            # `d=1` keeps frame rate (one out per in). `s` is the canvas size.
            zoompan_str = (
                ",zoompan=z='{z}':d=1:s={W}x{H}:fps=30[base]"
            ).format(z=zexpr, W=out_w, H=out_h)
        full_chain = full_chain.replace("[base]", zoompan_str)

    inputs = ["-i", str(src)]
    overlays = ["[base]"]
    next_input_idx = 1

    # PNG inputs get `-loop 1 -t DUR -framerate 30` so they become a
    # bounded video stream. Important: bound to the EFFECTIVE duration
    # (dur/speed), so a sped-up clip's overlay PNGs don't outlive the
    # video (overlay's default behavior is to extend to the longer input).
    fps = 30
    png_input_prefix = [
        "-loop", "1", "-framerate", str(fps),
        "-t", "{:.3f}".format(eff_dur),
    ]

    # Cutaways — insert an image over the source for [t, t+dur].
    # Captions / overlays / title still render on top so the cutaway feels
    # like the "video" is showing the cutaway image briefly.
    if cutaways:
        for j, cut in enumerate(cutaways):
            src_path = cut.get("src")
            if not src_path:
                continue
            src_p = pathlib.Path(src_path)
            if not src_p.is_absolute():
                # Resolve relative to workdir (one level up from work_assets).
                src_p = work_assets.parent.parent.parent / src_path
            if not src_p.exists():
                print("[cutaway] missing src '{}' — skipping".format(src_path))
                continue
            t_in = float(cut.get("t", 0)) / speed
            t_out = t_in + float(cut.get("dur", cut.get("duration", 1.5))) / speed
            # Loop the still image as a video stream for the cutaway duration.
            inputs += [
                "-loop", "1", "-framerate", str(fps),
                "-t", "{:.3f}".format(eff_dur),
                "-i", str(src_p),
            ]
            # Scale the cutaway image to fill the canvas; center-crop overflow.
            new_label = "[cut{}]".format(j)
            full_chain += (
                ";[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                "crop={W}:{H},setsar=1[cut_in_{j}];"
                "{prev}[cut_in_{j}]overlay=0:0:format=auto:"
                "enable='between(t,{a},{b})'{nl}"
            ).format(
                idx=next_input_idx,
                W=out_w, H=out_h,
                j=j,
                prev=overlays[-1],
                a=round(t_in, 3),
                b=round(t_out, 3),
                nl=new_label,
            )
            overlays.append(new_label)
            next_input_idx += 1

    # Flashes — single-frame impact "pop" via a solid-color full-canvas
    # overlay enabled for `dur` (default 50ms ≈ 1.5 frames). Generated
    # entirely inside the filter graph via `color` source — no PNG asset
    # required. Sit above cutaways but BELOW captions/title so they don't
    # obscure readable text. Color names ("white", "black") and 0xRRGGBB
    # both work; "#RRGGBB" gets rewritten to "0xRRGGBB" for ffmpeg.
    if flashes:
        for j, fl in enumerate(flashes):
            color = str(fl.get("color", "white"))
            if color.startswith("#"):
                color = "0x" + color[1:]
            intensity = max(0.0, min(1.0, float(fl.get("intensity", 1.0))))
            t_in = float(fl.get("t", 0)) / speed
            t_out = t_in + float(fl.get("dur", fl.get("duration", 0.05))) / speed
            flash_label = "[fls{}]".format(j)
            new_label = "[fl{}]".format(j)
            full_chain += (
                ";color=c={c}@{a:.3f}:s={W}x{H}:d={d:.3f}:r=30{flab};"
                "{prev}{flab}overlay=0:0:format=auto:eof_action=pass:"
                "enable='between(t,{a_t},{b_t})'{nl}"
            ).format(
                c=color, a=intensity, W=out_w, H=out_h, d=eff_dur,
                flab=flash_label, prev=overlays[-1],
                a_t=round(t_in, 3), b_t=round(t_out, 3),
                nl=new_label,
            )
            overlays.append(new_label)
            # Note: `color` is a source filter (no `-i`), so next_input_idx
            # does NOT advance — we didn't add an `-i` input.

    # Caption overlays (captions list of {start,end,text} relative to clip).
    # Caption timestamps are in source seconds; the output's `t` coordinate
    # is source_t / speed, so divide by speed when speed != 1.
    if captions:
        for i, c in enumerate(captions):
            cap_png = work_assets / "cap_{}.png".format(i)
            # word_active mode: chunk has `_words` + `_active_idx` for the
            # karaoke-style active-highlight renderer.
            emph = bool(c.get("_emphasis", False))
            if "_words" in c:
                img = render_caption_active(
                    c["_words"], c["_active_idx"], out_w, out_h,
                    style=caption_style, accent=accent,
                    emphasis=emph,
                )
            else:
                img = render_caption(
                    c["text"], out_w, out_h,
                    style=caption_style, accent=accent,
                    emphasis=emph,
                )
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

    # Free-form text overlays — `clip.overlays: [{t, dur, text, position, style}]`.
    # Sit between captions and the title bar (under the title, over the
    # base video + captions). Each overlay is its own PNG input.
    if text_overlays:
        for j, ov in enumerate(text_overlays):
            ov_text = ov.get("text", "")
            if not ov_text:
                continue
            ov_png = work_assets / "overlay_{}.png".format(j)
            img = render_overlay_text(
                ov_text, out_w, out_h,
                position=ov.get("position", "center"),
                style=ov.get("style", "comment"),
                accent=ov.get("accent", accent),
                font_scale=float(ov.get("font_scale", 1.0)),
            )
            img.save(ov_png)
            inputs += png_input_prefix + ["-i", str(ov_png)]
            t_in = float(ov.get("t", 0)) / speed
            t_out = t_in + float(ov.get("dur", ov.get("duration", 2.5))) / speed
            new_label = "[ov{}]".format(j)
            full_chain += (
                ";{prev}[{idx}:v]overlay=0:0:format=auto:"
                "enable='between(t,{a},{b})'{nl}"
            ).format(
                prev=overlays[-1],
                idx=next_input_idx,
                a=round(t_in, 3),
                b=round(t_out, 3),
                nl=new_label,
            )
            overlays.append(new_label)
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

    # Picture-in-picture overlay (e.g. webcam reaction cam). Goes BEFORE
    # the logo so the logo sits on top of the PiP if they share a corner.
    if pip_path:
        # Add the PiP video as another input. We trim it to match the
        # source's effective duration.
        inputs += [
            "-ss", "0",  # PiP starts at its own t=0; users can pre-trim
            "-t", "{:.3f}".format(eff_dur),
            "-i", str(pip_path),
        ]
        pip_w = max(80, int(out_w * pip_scale))
        pip_pad = max(8, int(pip_offset))
        positions = {
            "top_right":    "x=W-w-{p}:y={p}".format(p=pip_pad),
            "top_left":     "x={p}:y={p}".format(p=pip_pad),
            "bottom_right": "x=W-w-{p}:y=H-h-{p}".format(p=pip_pad),
            "bottom_left":  "x={p}:y=H-h-{p}".format(p=pip_pad),
        }
        ovl_pos = positions.get(pip_position, positions["bottom_right"])
        # Scale + (optional) drawbox border. Rounded corners aren't worth
        # the geq complexity; a 2-3px border looks cleaner anyway.
        if pip_round:
            # Add a thin dark border to separate the PiP from the source.
            pip_chain = (
                "scale={w}:-2,"
                "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.45:thickness=3"
                .format(w=pip_w)
            )
        else:
            pip_chain = "scale={w}:-2".format(w=pip_w)
        prev_label = pre_logo_label  # whatever feeds into the logo step
        # Reuse the existing pre_logo→[v] path: insert PiP overlay BEFORE
        # the logo step. We rewire by changing the label that the logo step
        # reads from.
        new_pre_logo = "[v_pre_pip_then_logo]"
        full_chain += (
            ";[{idx}:v]{pc}[pip];"
            "{prev}[pip]overlay={pos}:format=auto{nl}"
        ).format(
            idx=next_input_idx,
            pc=pip_chain,
            prev=prev_label,
            pos=ovl_pos,
            nl=new_pre_logo,
        )
        next_input_idx += 1
        pre_logo_label = new_pre_logo

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
    if do_reverse:
        # Reverse audio so it matches the reversed video.
        a_filter_parts.append("areverse")
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

    # Optional comedy stings — list of {t, name} to mix in at specific
    # times. `sfx_on_title` is rolled into this list with name="title_pulse"
    # at t=0.
    sting_events = list(stings or [])
    if sfx_on_title and title:
        sting_events.insert(0, {"t": 0, "name": "title_pulse"})

    if sting_events:
        # Each sting becomes its own audio input, delayed to its t, then
        # amix-ed with the voice chain.
        sting_input_indices = []
        for sting in sting_events:
            name = sting.get("name", "boom")
            user_wav = sfx_dir / "{}.wav".format(name) if sfx_dir else None
            if user_wav and user_wav.exists():
                inputs += ["-i", str(user_wav)]
            elif name == "title_pulse":
                # Backward-compat with the old sfx_on_title sound.
                inputs += [
                    "-f", "lavfi", "-t", "0.4",
                    "-i", "sine=frequency=200:duration=0.4:sample_rate=44100",
                ]
            elif name in STING_LAVFI:
                inputs += [
                    "-f", "lavfi", "-t", "1.2",
                    "-i", STING_LAVFI[name],
                ]
            else:
                print("[stings] unknown sting '{}' — skipping. "
                      "Built-in: {}, or drop a wav at sfx/{}.wav".format(
                          name, ", ".join(sorted(STING_LAVFI)), name,
                      ))
                continue
            sting_input_indices.append(
                (next_input_idx, float(sting["t"]),
                 float(sting.get("volume", 0.55)))
            )
            next_input_idx += 1

        if sting_input_indices:
            # Build per-sting filter chains.
            full_chain += ";[0:a]{}[v_aud]".format(a_voice_chain)
            sting_labels = []
            for j, (idx, t, vol) in enumerate(sting_input_indices):
                ms = max(0, int(round(t * 1000)))
                # Adjust for output-time when speed != 1.
                ms = int(ms / speed)
                full_chain += (
                    ";[{idx}:a]aformat=channel_layouts=mono,"
                    "adelay={ms}|{ms},volume={v}[sting_{j}]"
                ).format(idx=idx, ms=ms, v=vol, j=j)
                sting_labels.append("[sting_{}]".format(j))
            mix_in = "[v_aud]" + "".join(sting_labels)
            full_chain += (
                ";{m}amix=inputs={n}:duration=first:"
                "normalize=0[a]"
            ).format(m=mix_in, n=1 + len(sting_labels))
        else:
            full_chain += ";[0:a]{}[a]".format(a_voice_chain)
    else:
        full_chain += ";[0:a]{}[a]".format(a_voice_chain)

    cmd = [
        "ffmpeg", "-y",
        "-ss", "{:.3f}".format(start),
        "-to", "{:.3f}".format(end),
    ] + inputs + [
        "-filter_complex", full_chain,
        "-map", "[v]", "-map", "[a]",
    ] + encoder_args(quality) + [
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd)
    return out


def assemble_main(parts, out, durations, xfade=0.5, music_path=None,
                  transitions=None, quality="balanced", sfx_on_xfade=False):
    """Concatenate `parts` (mp4 paths) with xfade + acrossfade transitions.

    `transitions[i]` is the xfade type to use *between part i-1 and part i*.
    Index 0 is unused. Defaults to "fade" everywhere.

    `sfx_on_xfade=True` mixes a synthesized whoosh tone in at the midpoint
    of each xfade — adds polish to long compilations without needing an
    SFX asset library.
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

    # SFX input — generated tone we'll split + delay across the xfade
    # midpoints. Skip if there are no xfades to fire on.
    sfx_idx = None
    if sfx_on_xfade and len(parts) > 1:
        sfx_idx = (
            (music_idx + 1) if music_idx is not None else len(parts)
        )
        inputs += [
            "-f", "lavfi",
            "-t", "0.5",
            "-i", "sine=frequency=320:duration=0.5:sample_rate=44100",
        ]

    if len(parts) == 1 and music_idx is None:
        # Fast path — single part, no music — keep video stream copy.
        q = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["balanced"])
        run([
            "ffmpeg", "-y", "-i", str(parts[0]),
            "-c:v", "copy",
            "-af", LOUDNORM_MAIN,
            "-c:a", "aac", "-b:a", q["audio_kbps"] + "k",
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
    xfade_midpoints = []  # output-time seconds for SFX firing
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
        xfade_midpoints.append(offset + xfade / 2)
        v_label = new_v
        a_label = new_a
        cumulative += durations[i] - xfade

    # Build the SFX side-chain — generates one tone, splits it into K
    # streams, delays each to its xfade midpoint, mixes the lot into one
    # [sfx_all] stream. K = number of xfades.
    sfx_label = None
    if sfx_idx is not None and xfade_midpoints:
        K = len(xfade_midpoints)
        env = (
            "[{}:a]atrim=duration=0.4,"
            "afade=t=in:st=0:d=0.04,afade=t=out:st=0.30:d=0.10".format(sfx_idx)
        )
        if K == 1:
            ms = int(round(xfade_midpoints[0] * 1000))
            chain_parts.append(
                "{e},adelay={ms}|{ms},volume=0.4[sfx_all]".format(e=env, ms=ms)
            )
        else:
            split_outs = "".join("[sfx_s{}]".format(j) for j in range(K))
            chain_parts.append("{e},asplit={k}{outs}".format(
                e=env, k=K, outs=split_outs,
            ))
            for j, mid_t in enumerate(xfade_midpoints):
                ms = int(round(mid_t * 1000))
                chain_parts.append(
                    "[sfx_s{j}]adelay={ms}|{ms},volume=0.4[sfx_d{j}]".format(
                        j=j, ms=ms,
                    )
                )
            mix_inputs = "".join("[sfx_d{}]".format(j) for j in range(K))
            chain_parts.append(
                "{m}amix=inputs={k}:duration=longest:normalize=0[sfx_all]".format(
                    m=mix_inputs, k=K,
                )
            )
        sfx_label = "[sfx_all]"

    if music_idx is not None:
        # Split the voice into two streams: one for the mix, one as the
        # sidechain trigger that ducks the music. Mix voice + ducked music
        # (+ SFX if enabled), then loudnorm the result.
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
        if sfx_label:
            chain_parts.append(
                "[voice_a][music_duck]{sfx}amix=inputs=3:duration=first:"
                "weights=1.0 0.65 0.7,{ln}[aout]".format(
                    sfx=sfx_label, ln=LOUDNORM_MAIN,
                )
            )
        else:
            chain_parts.append(
                "[voice_a][music_duck]amix=inputs=2:duration=first:"
                "weights=1.0 0.65,{ln}[aout]".format(ln=LOUDNORM_MAIN)
            )
    elif sfx_label:
        # No music, but SFX — mix voice + SFX, then loudnorm.
        chain_parts.append(
            "{prev}{sfx}amix=inputs=2:duration=first:"
            "weights=1.0 0.7,{ln}[aout]".format(
                prev=a_label, sfx=sfx_label, ln=LOUDNORM_MAIN,
            )
        )
    else:
        # No music, no SFX — just loudnorm the voice stream directly.
        chain_parts.append("{prev}{ln}[aout]".format(prev=a_label, ln=LOUDNORM_MAIN))
    a_label = "[aout]"

    filter_complex = ";".join(chain_parts)
    run([
        "ffmpeg", "-y",
    ] + inputs + [
        "-filter_complex", filter_complex,
        "-map", v_label, "-map", a_label,
    ] + encoder_args(quality) + [
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
    show_speaker_labels = bool(style.get("show_speaker_labels", False))
    # Optional speaker map: {"SPEAKER_00": "ALICE", "SPEAKER_01": "BOB"}
    speaker_map = style.get("speaker_map") or {}
    # Translation: render additional vertical clips with translated captions.
    # Requires translate.py to have produced transcript.<lang>.json first.
    translate_to = style.get("translate_to") or []
    if isinstance(translate_to, str):
        translate_to = [translate_to]
    translated_transcripts = {}
    for lang in translate_to:
        tpath = workdir / "transcript.{}.json".format(lang)
        if not tpath.exists():
            print(
                "[translate_to] missing {} — run scripts/translate.py first. "
                "Skipping this language.".format(tpath.name)
            )
            continue
        translated_transcripts[lang] = json.loads(tpath.read_text())
    if show_speaker_labels and speaker_map and transcript:
        # Apply rename in-place on the loaded transcript so all downstream
        # caption-chunk calls see the friendly names.
        for e in transcript:
            spk = e.get("speaker")
            if spk and spk in speaker_map:
                e["speaker"] = speaker_map[spk]
    default_quality = style.get("quality", "balanced")
    if default_quality not in QUALITY_PRESETS:
        sys.exit("unknown quality preset: {!r} (valid: {})".format(
            default_quality, ", ".join(sorted(QUALITY_PRESETS))))
    default_beat_sync = bool(style.get("beat_sync", False))
    default_sfx_on_xfade = bool(style.get("sfx_on_xfade", False))

    # Load signals.json (used for auto-zoom peaks).
    signals = {}
    spath = workdir / "signals.json"
    if spath.exists():
        signals = json.loads(spath.read_text())

    # Load crop_track.json if it exists (used by vertical_fit="face_track").
    face_track = None
    ftpath = workdir / "crop_track.json"
    if ftpath.exists():
        face_track = json.loads(ftpath.read_text())

    # Load beats.json if it exists (used by style.beat_sync).
    beats = None
    bpath = workdir / "beats.json"
    if bpath.exists():
        beats = json.loads(bpath.read_text()).get("beats") or []

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

    # Picture-in-picture detection: webcam.mp4 / cam.mp4 / pip.mp4 in workdir.
    pip_path = None
    for name in ("webcam.mp4", "cam.mp4", "pip.mp4"):
        p = workdir / name
        if p.exists():
            pip_path = p
            break
    pip_position = style.get("pip_position", "bottom_right")
    pip_scale = float(style.get("pip_scale", 0.22))
    pip_round = bool(style.get("pip_round", True))
    logo_position = style.get("logo_position", "top_right")
    logo_opacity = float(style.get("logo_opacity", 0.85))
    logo_scale = float(style.get("logo_scale", 0.10))

    # 3D LUT detection. style.lut resolves in this order:
    #   1. Bundled name (e.g. "cinematic" → luts/cinematic.cube next to the
    #      skill scripts).
    #   2. Absolute or workdir-relative path to a .cube file.
    # If style.lut is unset, we look for lut.cube in the workdir.
    lut_path = None
    style_lut = style.get("lut")
    if style_lut:
        bundled = pathlib.Path(__file__).resolve().parent.parent / "luts" / (
            style_lut + ".cube"
        )
        if bundled.exists():
            lut_path = bundled
        else:
            cand = pathlib.Path(style_lut)
            if not cand.is_absolute():
                cand = workdir / style_lut
            if cand.exists():
                lut_path = cand
            else:
                bundled_dir = bundled.parent
                bundled_names = sorted(
                    p.stem for p in bundled_dir.glob("*.cube")
                ) if bundled_dir.exists() else []
                sys.exit(
                    "style.lut not found: {!r}. Tried bundled name (have: {}) "
                    "and path {}".format(
                        style_lut,
                        ", ".join(bundled_names) or "(none)",
                        cand,
                    )
                )
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
    if main_segs and default_beat_sync and beats:
        snapped = snap_segments_to_beats(main_segs, beats)
        shifted = sum(
            1 for a, b in zip(main_segs, snapped) if a["end"] != b["end"]
        )
        print("[beat_sync] snapped {}/{} segment ends to beats".format(
            shifted, len(main_segs) - 1
        ))
        main_segs = snapped
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
                punches=seg.get("punches"),
                shake=seg.get("shake"),
                flashes=seg.get("flashes"),
                stabilize=bool(seg.get("stabilize", default_stabilize)),
                quality=default_quality,
                pip_path=pip_path,
                pip_position=pip_position,
                pip_scale=pip_scale,
                pip_round=pip_round,
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
                quality=default_quality,
            )
            parts.append(ec_path)
            durations.append(ec_dur)
            transitions.append(end_card_spec.get("transition", "fadeblack"))

        main_out = out_dir / "main.mp4"
        assemble_main(parts, main_out, durations,
                      music_path=music_path, transitions=transitions,
                      quality=default_quality,
                      sfx_on_xfade=default_sfx_on_xfade)
        print("main → {}".format(main_out))

        # Optional alternate-aspect exports + sidecar files for the main.
        export_formats = style.get("export_formats") or []
        for fmt in export_formats:
            if fmt == "square":
                fmt_out = out_dir / "main_square.mp4"
                export_format(main_out, fmt, fmt_out,
                              vertical_fit=default_vertical_fit,
                              quality=default_quality)
                print("main → {}".format(fmt_out))
            elif fmt == "vertical":
                fmt_out = out_dir / "main_vertical.mp4"
                export_format(main_out, fmt, fmt_out,
                              vertical_fit=default_vertical_fit,
                              quality=default_quality)
                print("main → {}".format(fmt_out))
            elif fmt == "audio":
                fmt_out = out_dir / "main.m4a"
                export_audio(main_out, fmt_out, quality=default_quality)
                print("main → {}".format(fmt_out))
            elif fmt == "srt":
                fmt_out = out_dir / "main.srt"
                # Note: this exports the FULL source transcript, not the
                # compilation timeline. For accurate subs aligned to the
                # cuts, we'd need to remap cue times through the EDL — that's
                # a TODO. This is still useful as a "what was said" sidecar.
                if export_srt(transcript, fmt_out):
                    print("main → {} (full source transcript)".format(fmt_out))
                else:
                    print("[export] no transcript — skipping srt")
            else:
                print("[export] skipping unknown format: " + fmt)

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
            punches=clip.get("punches"),
            shake=clip.get("shake"),
            flashes=clip.get("flashes"),
            stabilize=bool(clip.get("stabilize", default_stabilize)),
            face_track=face_track,
            quality=default_quality,
            pip_path=pip_path,
            pip_position=pip_position,
            pip_scale=pip_scale,
            pip_round=pip_round,
            stings=clip.get("stings"),
            sfx_dir=workdir / "sfx",
            text_overlays=clip.get("overlays"),
            cutaways=clip.get("cutaways"),
            freezes=clip.get("freezes"),
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
                    show_speaker_labels=show_speaker_labels,
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
                punches=clip.get("punches"),
                shake=clip.get("shake"),
                flashes=clip.get("flashes"),
                stabilize=bool(clip.get("stabilize", default_stabilize)),
                face_track=face_track,
                quality=default_quality,
                stings=clip.get("stings"),
                sfx_dir=workdir / "sfx",
                text_overlays=clip.get("overlays"),
                cutaways=clip.get("cutaways"),
                freezes=clip.get("freezes"),
            )
            print("clip → {}".format(vert_dir / name))

            # Translation fan-out: render additional vertical copies with
            # translated captions, one per target language. Audio is the
            # original (no dubbing); only captions change.
            for lang, lang_transcript in translated_transcripts.items():
                if not do_caps:
                    continue
                lang_caps = caption_chunks_for(
                    lang_transcript, start, end,
                    mode=cap_mode, drop_fillers=drop_fillers,
                    show_speaker_labels=show_speaker_labels,
                )
                lang_vert_dir = out_dir / "clips_vertical_{}".format(lang)
                lang_vert_dir.mkdir(parents=True, exist_ok=True)
                lang_assets = tmp_dir / "clip_{:02d}_v_{}".format(i, lang)
                lang_assets.mkdir(exist_ok=True)
                render_clip(
                    src, start, end, lang_vert_dir / name,
                    vertical=True,
                    title=title,
                    captions=lang_caps,
                    src_dims=src_dims,
                    work_assets=lang_assets,
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
                    punches=clip.get("punches"),
                    shake=clip.get("shake"),
                    flashes=clip.get("flashes"),
                    stabilize=bool(clip.get("stabilize", default_stabilize)),
                    face_track=face_track,
                    quality=default_quality,
                )
                print("clip → {} ({})".format(lang_vert_dir / name, lang))

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("\ndone. output in {}".format(out_dir))


if __name__ == "__main__":
    main()
