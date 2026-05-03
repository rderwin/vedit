"""Pillow-based renderer for title cards and short-form caption chunks.

Used by assemble.py to make polished overlays without depending on ffmpeg
having drawtext / libass compiled in.
"""
from PIL import Image, ImageDraw, ImageFont


# Best-effort macOS-friendly font search (Bold preferred via ttc index).
FONT_CANDIDATES = [
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
    ("/System/Library/Fonts/HelveticaNeue.ttc", 0),
    ("/System/Library/Fonts/Helvetica.ttc", 1),
    ("/System/Library/Fonts/Helvetica.ttc", 0),
    ("/System/Library/Fonts/Avenir Next.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ("/Library/Fonts/Arial Bold.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
]


def _font(size):
    for path, idx in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size, index=idx)
        except Exception:
            continue
    return ImageFont.load_default()


def _hex_rgba(h, alpha=255):
    h = h.lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)
    return (255, 210, 74, alpha)


def _wrap(text, font, max_w, draw):
    words = text.split()
    lines, cur = [], []
    for w in words:
        candidate = " ".join(cur + [w])
        if draw.textlength(candidate, font=font) <= max_w:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines or [text]


def render_title_card(text, width, height, accent="#FFD24A"):
    """Lower-third title with a translucent gradient bar and accent stripe."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    is_vertical = height > width

    if is_vertical:
        font_size = max(52, int(width * 0.062))
        pad = int(font_size * 0.55)
        side_pad = int(width * 0.07)
        bottom_offset = int(height * 0.18)
    else:
        font_size = max(34, int(height * 0.060))
        pad = int(font_size * 0.55)
        side_pad = int(width * 0.045)
        bottom_offset = int(height * 0.10)

    font = _font(font_size)
    max_text_w = width - 2 * side_pad - 24
    lines = _wrap(text, font, max_text_w, draw)
    line_h = int(font_size * 1.18)
    text_h = line_h * len(lines)
    bar_h = text_h + 2 * pad
    bar_y = height - bottom_offset - bar_h

    # Vertical gradient bar — lighter at top, darker at bottom.
    grad = Image.new("RGBA", (width, bar_h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for i in range(bar_h):
        a = int(120 + (i / max(bar_h - 1, 1)) * 110)  # 120 → 230
        gd.line([(0, i), (width, i)], fill=(0, 0, 0, a))
    img.paste(grad, (0, bar_y), grad)

    # Left accent stripe.
    stripe_w = max(6, int(width * 0.008))
    draw.rectangle(
        [
            (side_pad - stripe_w - 14, bar_y + pad),
            (side_pad - 14, bar_y + bar_h - pad),
        ],
        fill=_hex_rgba(accent),
    )

    # Text with subtle drop shadow for readability over bright frames.
    y = bar_y + pad
    for line in lines:
        draw.text((side_pad + 2, y + 2), line, fill=(0, 0, 0, 180), font=font)
        draw.text((side_pad, y), line, fill=(255, 255, 255, 255), font=font)
        y += line_h

    return img


def render_end_card(title, subtitle, cta, width, height, accent="#FFD24A"):
    """Branded end-screen card: title, subtitle, CTA, and a fat accent stripe.

    Used as a 3-4s appended segment at the tail of the main compilation.
    Returns an RGB image (no transparency — it's a full frame).
    """
    img = Image.new("RGB", (width, height), (16, 18, 22))
    draw = ImageDraw.Draw(img)
    base = min(width, height)

    title_size = max(60, int(base * 0.10))
    sub_size = max(28, int(base * 0.038))
    cta_size = max(34, int(base * 0.046))

    tfont = _font(title_size)
    sfont = _font(sub_size)
    cfont = _font(cta_size)

    # Position the title block at vertical center (slightly above).
    side_pad = int(width * 0.08)
    tw = draw.textlength(title or "", font=tfont)
    sw = draw.textlength(subtitle or "", font=sfont)
    cw = draw.textlength(cta or "", font=cfont)

    block_h = (
        title_size
        + (int(sub_size * 1.4) if subtitle else 0)
        + (int(cta_size * 1.6) if cta else 0)
    )
    y = (height - block_h) // 2

    if title:
        draw.text(((width - tw) // 2, y), title,
                  fill=(245, 245, 248), font=tfont)
        y += int(title_size * 1.05)

    # Accent stripe between title and subtitle.
    stripe_w = max(80, int(width * 0.12))
    stripe_h = max(6, int(base * 0.010))
    sx = (width - stripe_w) // 2
    draw.rectangle(
        [(sx, y + int(sub_size * 0.2)), (sx + stripe_w, y + int(sub_size * 0.2) + stripe_h)],
        fill=_hex_rgba(accent)[:3],
    )
    y += int(sub_size * 0.7)

    if subtitle:
        draw.text(((width - sw) // 2, y), subtitle,
                  fill=(180, 180, 190), font=sfont)
        y += int(sub_size * 1.55)

    if cta:
        # CTA in accent color, centered at bottom third.
        cta_y = int(height * 0.78)
        draw.text(((width - cw) // 2, cta_y), cta,
                  fill=_hex_rgba(accent)[:3], font=cfont)

    return img


def render_caption(text, width, height, style="minimal", accent="#FFD24A"):
    """Centered, big, bold short-form caption (TikTok / Reels style).

    Styles:
      minimal — white text + thick black stroke (current default).
      bold    — white text on a black rounded pill (no stroke).
      pop     — black text on an accent-colored pill (MrBeast-adjacent).
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    is_vertical = height > width

    if is_vertical:
        font_size = max(72, int(width * 0.085))
        center_y_frac = 0.58
    else:
        font_size = max(40, int(height * 0.070))
        center_y_frac = 0.78

    font = _font(font_size)
    max_text_w = int(width * 0.85)
    lines = _wrap(text.upper(), font, max_text_w, draw)
    line_h = int(font_size * 1.10)
    block_h = line_h * len(lines)
    y0 = int(height * center_y_frac) - block_h // 2

    if style == "minimal":
        stroke_w = max(4, int(font_size * 0.085))
        for i, line in enumerate(lines):
            line_w = draw.textlength(line, font=font)
            x = (width - line_w) // 2
            y = y0 + i * line_h
            draw.text(
                (x, y),
                line,
                fill=(255, 255, 255, 255),
                font=font,
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0, 255),
            )
        return img

    # `bold` and `pop` use a colored rounded pill behind each line.
    if style == "pop":
        bg = _hex_rgba(accent)[:3] + (235,)
        fg = (16, 16, 18, 255)
    else:  # bold
        bg = (16, 16, 18, 220)
        fg = (255, 255, 255, 255)

    pad_x = int(font_size * 0.45)
    pad_y = int(font_size * 0.12)
    radius = int(font_size * 0.30)

    for i, line in enumerate(lines):
        line_w = draw.textlength(line, font=font)
        text_x = (width - line_w) // 2
        text_y = y0 + i * line_h
        # Pill rect bounds
        x1 = text_x - pad_x
        y1 = text_y - pad_y
        x2 = text_x + line_w + pad_x
        y2 = text_y + font_size + pad_y
        try:
            draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=bg)
        except Exception:
            draw.rectangle((x1, y1, x2, y2), fill=bg)
        draw.text((text_x, text_y), line, fill=fg, font=font)

    return img
