#!/usr/bin/env python3
"""Generate a small bundle of CC0 3D LUTs as .cube files.

A .cube file is a 3D look-up table — for each input (R, G, B) sample on
a regular grid, it lists the output (R', G', B'). ffmpeg's `lut3d` filter
loads them via `lut3d=file=path/to.cube`.

These LUTs are intentionally simple and tasteful — strong enough to make
each style obviously different, soft enough not to clip skin tones.

CC0 1.0 (public domain). Copy, modify, redistribute freely.
"""
import math
import pathlib


SIZE = 17  # 17^3 = 4913 entries — common LUT resolution, plenty for video


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def lerp(a, b, t):
    return a + (b - a) * t


def write_cube(path, title, mapping):
    """Write a SIZE×SIZE×SIZE LUT by sampling `mapping(r, g, b) → (r, g, b)`."""
    lines = [
        "# {}".format(title),
        "# Generated for the youtube-edit skill — CC0",
        "LUT_3D_SIZE {}".format(SIZE),
        "",
    ]
    n = SIZE - 1
    for b_i in range(SIZE):
        for g_i in range(SIZE):
            for r_i in range(SIZE):
                r = r_i / n
                g = g_i / n
                b = b_i / n
                r2, g2, b2 = mapping(r, g, b)
                lines.append("{:.6f} {:.6f} {:.6f}".format(
                    clamp(r2), clamp(g2), clamp(b2),
                ))
    path.write_text("\n".join(lines) + "\n")
    print("wrote {} ({} entries)".format(path.name, SIZE ** 3))


# ---------------------------------------------------------------------------
# Look definitions — each is just a float→float color transform.
# ---------------------------------------------------------------------------

def cinematic(r, g, b):
    """Teal-orange — lift shadows toward teal, push highlights toward orange.
    Slight contrast bump."""
    # Luma-weighted split — more shadow influence on dark pixels.
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    shadow_w = (1.0 - luma) ** 1.6
    highlight_w = luma ** 1.4

    # Shadow tint: slightly cyan-teal (boost B + G, reduce R)
    r2 = r - 0.04 * shadow_w
    g2 = g + 0.02 * shadow_w
    b2 = b + 0.06 * shadow_w
    # Highlight tint: warm orange (boost R + G, reduce B)
    r2 = r2 + 0.06 * highlight_w
    g2 = g2 + 0.02 * highlight_w
    b2 = b2 - 0.05 * highlight_w
    # Mild contrast (S-curve)
    r2 = _scurve(r2, 0.18)
    g2 = _scurve(g2, 0.18)
    b2 = _scurve(b2, 0.18)
    return r2, g2, b2


def warm_vintage(r, g, b):
    """Sun-faded vintage — warm tint, lifted shadows, rolled-off highlights."""
    # Lift shadows globally
    r = r + 0.04 * (1 - r)
    g = g + 0.03 * (1 - g)
    b = b + 0.02 * (1 - b)
    # Warm push: more red, slightly less blue
    r = r * 1.05 + 0.03
    g = g * 1.02 + 0.01
    b = b * 0.92 - 0.01
    # Slight highlight rolloff
    r = _rolloff(r, 0.85)
    g = _rolloff(g, 0.88)
    b = _rolloff(b, 0.92)
    return r, g, b


def cool_noir(r, g, b):
    """Cool blue cast, mild contrast, suitable for dramatic / late-night."""
    # Slight desaturation toward luma
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    desat = 0.15
    r = lerp(r, luma, desat)
    g = lerp(g, luma, desat)
    b = lerp(b, luma, desat)
    # Cool push
    r = r * 0.94
    b = b * 1.10 + 0.02
    # Contrast
    r = _scurve(r, 0.22)
    g = _scurve(g, 0.22)
    b = _scurve(b, 0.22)
    return r, g, b


def summer_pop(r, g, b):
    """Saturated vacation look — boosted reds and greens, sunny."""
    # HSL-ish saturation boost via simple distance from luma
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    sat = 1.18
    r = luma + (r - luma) * sat
    g = luma + (g - luma) * sat
    b = luma + (b - luma) * sat
    # Slight warm shift in highlights
    if luma > 0.5:
        r += 0.04
        g += 0.02
        b -= 0.01
    return r, g, b


def bw_high_contrast(r, g, b):
    """High-contrast black & white. Output is fully desaturated + S-curved."""
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    luma = _scurve(luma, 0.30)
    return luma, luma, luma


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _scurve(x, amount):
    """Sigmoid-ish S-curve. amount=0 → linear, amount→1 → harder."""
    if amount <= 0:
        return x
    # Piecewise — boost mid-tones via 1 - cos(pi*x) blended with linear.
    centered = (math.cos((1 - x) * math.pi) + 1) / 2
    return lerp(x, centered, amount)


def _rolloff(x, knee):
    """Soft compress everything above `knee` to avoid clipping."""
    if x <= knee:
        return x
    excess = x - knee
    # asymptote at 1.0
    return knee + (1 - knee) * (1 - math.exp(-excess / (1 - knee)))


def main():
    out_dir = pathlib.Path(__file__).resolve().parent
    write_cube(out_dir / "cinematic.cube",     "Cinematic — Teal/Orange",   cinematic)
    write_cube(out_dir / "warm_vintage.cube",  "Warm Vintage",              warm_vintage)
    write_cube(out_dir / "cool_noir.cube",     "Cool Noir",                 cool_noir)
    write_cube(out_dir / "summer_pop.cube",    "Summer Pop",                summer_pop)
    write_cube(out_dir / "bw_high_contrast.cube", "B&W High Contrast",      bw_high_contrast)


if __name__ == "__main__":
    main()
