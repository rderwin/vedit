#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow>=10"]
# ///
"""Generate thumbnail candidates for a YouTube video.

Reads <workdir>/thumbnails.json:

    {
      "source": "source.mp4",
      "format": "youtube",                     # youtube (1280x720) | square | vertical
      "thumbnails": [
        {"t": 412.5, "headline": "I JUST LOST MY QUEEN", "subhead": "live reaction"},
        ...
      ],
      "accent": "#FFD24A"
    }

For each entry, grabs the source frame at `t`, applies a subtle vignette,
and overlays a YouTube-style headline (huge bold caps, thick stroke,
optional small subhead). Output: <workdir>/out/thumbnails/NN_slug.jpg.

The source frame is graded with a slight contrast/saturation bump so
thumbnails pop in the YouTube grid.
"""
import json
import pathlib
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from render_text import _font, _hex_rgba, _wrap


SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")

DIMS = {
    "youtube":  (1280, 720),
    "square":   (1080, 1080),
    "vertical": (1080, 1920),
}


def slugify(s, fallback="thumb"):
    s = SLUG_RE.sub("_", s or "").strip("_").lower()
    return s[:50] or fallback


def grab_frame(src, t, out_png):
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", "{:.3f}".format(t),
            "-i", str(src),
            "-frames:v", "1",
            str(out_png),
        ],
        check=True,
    )


def fit_to(img, target_w, target_h):
    """Scale + center-crop img to (target_w, target_h)."""
    sw, sh = img.size
    src_ratio = sw / sh
    tgt_ratio = target_w / target_h
    if src_ratio > tgt_ratio:
        # source is wider — match height, crop sides
        new_h = target_h
        new_w = int(round(sh * tgt_ratio * (new_h / sh)))
        # actually simpler:
        scale = target_h / sh
        new_w, new_h = int(round(sw * scale)), target_h
    else:
        scale = target_w / sw
        new_w, new_h = target_w, int(round(sh * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def grade(img):
    """Subtle pop: more contrast and saturation."""
    img = ImageEnhance.Contrast(img).enhance(1.10)
    img = ImageEnhance.Color(img).enhance(1.18)
    img = ImageEnhance.Sharpness(img).enhance(1.20)
    return img


def vignette(img, strength=0.45):
    """Darken the corners with a radial gradient."""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    # Big white ellipse, then blur.
    pad = int(min(w, h) * 0.05)
    md.ellipse([(pad, pad), (w - pad, h - pad)], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=int(min(w, h) * 0.15)))
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(img, dark, mask) if strength >= 1.0 else \
        Image.blend(dark, img, 1.0 - strength) if False else \
        Image.composite(
            img,
            Image.blend(img, Image.new("RGB", (w, h), (0, 0, 0)), strength),
            mask,
        )


def render_thumbnail(src_frame_png, headline, subhead, w, h, accent="#FFD24A"):
    img = Image.open(src_frame_png).convert("RGB")
    img = fit_to(img, w, h)
    img = grade(img)
    img = vignette(img, strength=0.45)

    draw = ImageDraw.Draw(img)
    base = min(w, h)
    head_size = max(56, int(base * 0.13))
    sub_size = max(28, int(base * 0.04))

    side_pad = int(w * 0.05)
    bottom_pad = int(h * 0.06)
    max_text_w = w - 2 * side_pad

    # Headline: bold caps, white, thick black stroke.
    text = (headline or "").upper()
    hfont = _font(head_size)
    lines = _wrap(text, hfont, max_text_w, draw)
    # Shrink if more than 3 lines.
    while len(lines) > 3 and head_size > 48:
        head_size -= 6
        hfont = _font(head_size)
        lines = _wrap(text, hfont, max_text_w, draw)

    line_h = int(head_size * 1.05)
    block_h = line_h * len(lines)

    sfont = _font(sub_size)
    sub_h = int(sub_size * 1.1) if subhead else 0

    block_top = h - bottom_pad - block_h - sub_h - (10 if subhead else 0)

    # Optional accent bar above headline.
    bar_y = block_top - int(line_h * 0.3)
    bar_w = int(w * 0.18)
    bar_h = max(8, int(base * 0.012))
    draw.rectangle(
        [(side_pad, bar_y - bar_h), (side_pad + bar_w, bar_y)],
        fill=_hex_rgba(accent)[:3] + (255,),
    )

    stroke_w = max(4, int(head_size * 0.07))
    y = block_top
    for line in lines:
        draw.text(
            (side_pad, y),
            line,
            fill=(255, 255, 255, 255),
            font=hfont,
            stroke_width=stroke_w,
            stroke_fill=(0, 0, 0, 255),
        )
        y += line_h

    if subhead:
        draw.text(
            (side_pad + 2, y + 8),
            subhead,
            fill=(220, 220, 225, 255),
            font=sfont,
            stroke_width=2,
            stroke_fill=(0, 0, 0, 255),
        )

    return img


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: thumbnails.py <workdir>")
    workdir = pathlib.Path(sys.argv[1])
    spec_path = workdir / "thumbnails.json"
    if not spec_path.exists():
        sys.exit("missing " + str(spec_path))
    spec = json.loads(spec_path.read_text())

    src = workdir / spec.get("source", "source.mp4")
    if not src.exists():
        sys.exit("source not found: " + str(src))
    fmt = spec.get("format", "youtube")
    if fmt not in DIMS:
        sys.exit("unknown format: " + fmt)
    w, h = DIMS[fmt]
    accent = spec.get("accent", "#FFD24A")

    out_dir = workdir / "out" / "thumbnails"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "_tmp_frames"
    tmp_dir.mkdir(exist_ok=True)

    for i, t in enumerate(spec.get("thumbnails") or [], 1):
        ts = float(t["t"])
        head = t.get("headline", "")
        sub = t.get("subhead", "")
        slug = slugify(head, "thumb_{:02d}".format(i))
        frame_png = tmp_dir / "frame_{:02d}.png".format(i)
        grab_frame(src, ts, frame_png)
        thumb = render_thumbnail(frame_png, head, sub, w, h, accent=accent)
        out_path = out_dir / "{:02d}_{}.jpg".format(i, slug)
        thumb.save(out_path, quality=92, optimize=True)
        print("thumb → {}".format(out_path))

    # leave tmp frames for debugging — small impact
    print("\ndone. {} thumbnails in {}".format(i, out_dir))


if __name__ == "__main__":
    main()
