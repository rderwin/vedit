#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow>=10"]
# ///
"""Generate shareable PNG quote cards from a list of quotes.

Reads <workdir>/quotes.json:

    {
      "title": "Billy Markus — Road to 1000 ELO",
      "uploader": "Billy Markus",
      "format": "square",                # square | vertical | landscape
      "accent": "#FFD24A",
      "quotes": [
        {"text": "Just some shit poster who's 426. Who's ass at this game? I'm all right.",
         "attribution": "Billy Markus"},
        ...
      ]
    }

Produces <workdir>/out/quotecards/NN_quote.png (one per quote).

Cards have a punchy aesthetic: large bold quote text, dark background,
accent stripe + small attribution + show title at the bottom.
"""
import json
import pathlib
import re
import sys

from PIL import Image, ImageDraw, ImageFont


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from render_text import _font, _hex_rgba, _wrap


SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")

DIMS = {
    "square":    (1080, 1080),
    "vertical":  (1080, 1920),
    "landscape": (1920, 1080),
}


def slugify(s, fallback="quote"):
    s = SLUG_RE.sub("_", s or "").strip("_").lower()
    return s[:60] or fallback


def render_card(quote, attribution, show_title, w, h, accent="#FFD24A"):
    img = Image.new("RGB", (w, h), (16, 18, 22))
    draw = ImageDraw.Draw(img)

    # Font sizing scales with the smaller dimension so it reads on any aspect.
    base = min(w, h)
    quote_font_size = int(base * 0.072)
    quote_font_size = max(40, min(quote_font_size, 120))
    attr_font_size = max(22, int(base * 0.028))
    title_font_size = max(20, int(base * 0.022))

    qfont = _font(quote_font_size)
    afont = _font(attr_font_size)
    tfont = _font(title_font_size)

    side_pad = int(w * 0.075)
    top_pad = int(h * 0.09)
    bottom_pad = int(h * 0.10)

    # Big leading quote mark.
    mark_size = int(quote_font_size * 1.4)
    mark_font = _font(mark_size)
    mark_color = _hex_rgba(accent)[:3] + (255,)
    draw.text(
        (side_pad - 4, top_pad - int(mark_size * 0.6)),
        "“",
        font=mark_font,
        fill=mark_color,
    )

    # Quote body, wrapped.
    max_text_w = w - 2 * side_pad
    lines = _wrap(quote, qfont, max_text_w, draw)
    line_h = int(quote_font_size * 1.18)
    body_h = line_h * len(lines)
    body_top = top_pad + int(mark_size * 0.5)

    # If the body is going to overflow vertically, shrink iteratively.
    while body_top + body_h > h - bottom_pad - 200 and quote_font_size > 36:
        quote_font_size -= 4
        qfont = _font(quote_font_size)
        lines = _wrap(quote, qfont, max_text_w, draw)
        line_h = int(quote_font_size * 1.18)
        body_h = line_h * len(lines)

    y = body_top
    for line in lines:
        draw.text((side_pad, y), line, fill=(245, 245, 248, 255), font=qfont)
        y += line_h

    # Accent stripe under the quote.
    stripe_y = y + int(line_h * 0.2)
    stripe_w = int(w * 0.08)
    stripe_h = max(6, int(base * 0.008))
    draw.rectangle(
        [(side_pad, stripe_y), (side_pad + stripe_w, stripe_y + stripe_h)],
        fill=mark_color,
    )

    # Attribution.
    if attribution:
        attr_y = stripe_y + stripe_h + int(line_h * 0.4)
        draw.text(
            (side_pad, attr_y),
            "— " + attribution,
            fill=(220, 220, 225, 255),
            font=afont,
        )

    # Show title at the very bottom.
    if show_title:
        tw = draw.textlength(show_title, font=tfont)
        ty = h - bottom_pad - title_font_size
        draw.text(
            (side_pad, ty),
            show_title,
            fill=(150, 150, 158, 255),
            font=tfont,
        )

    return img


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: quotecards.py <workdir>")
    workdir = pathlib.Path(sys.argv[1])
    qpath = workdir / "quotes.json"
    if not qpath.exists():
        sys.exit("missing " + str(qpath))
    spec = json.loads(qpath.read_text())

    fmt = spec.get("format", "square")
    if fmt not in DIMS:
        sys.exit("unknown format: " + fmt)
    w, h = DIMS[fmt]
    accent = spec.get("accent", "#FFD24A")
    show_title = spec.get("title", "")

    out_dir = workdir / "out" / "quotecards"
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, q in enumerate(spec.get("quotes") or [], 1):
        text = q["text"].strip()
        attr = q.get("attribution") or spec.get("uploader") or ""
        slug = slugify(text.split(".")[0][:40], "quote_{:02d}".format(i))
        name = "{:02d}_{}.png".format(i, slug)
        img = render_card(text, attr, show_title, w, h, accent=accent)
        img.save(out_dir / name)
        print("card → {}".format(out_dir / name))

    print("\ndone. {} cards in {}".format(i, out_dir))


if __name__ == "__main__":
    main()
