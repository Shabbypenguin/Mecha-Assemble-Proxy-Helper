"""Original card-back art: deep red embossed hex tiles with a diagonal light sweep.

Generated procedurally at any size (Pillow only), so unit, tactics and token backs share
one style. Contains no third-party logos or text.

How it's built
  1. a smooth light layer (diagonal sweep + soft vignette), computed on a small grid and
     upscaled;
  2. a hex-tile layer drawn as polygons: each tile gets a slight random tone, a bevelled
     rim lit from the top-left, and a dark seam;
  3. the two are added and mapped onto a dark-to-light red ramp.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

STYLE_VERSION = "e2"
HEX_MM = 8.0          # hex circumradius in mm, so tiles look the same on every card size
SUPERSAMPLE = 2       # draw tiles at 2x and downsample for smooth edges
DARK = (70, 6, 12)
LIGHT = (215, 50, 56)
SQ3 = math.sqrt(3.0)
LIGHT_DIR = (0.6, 0.8)  # rims facing up-left are lit (screen coords, y down)
# bevel rings from the outer edge inwards: (outer radius, inner radius, bevel strength, seam darkening)
RINGS = ((1.0, 0.97, 0.85, -0.07), (0.97, 0.935, 0.5, -0.01), (0.935, 0.90, 0.17, 0.0))


def _light(u: float, v: float, landscape: bool, aspect: float) -> tuple[float, float]:
    """(sweep, base luminance) at normalised trim-box coordinates u, v."""
    diag = u * 0.8 + v * 0.5 if landscape else u * 0.5 + v * 0.8
    sweep = math.exp(-((diag - 0.55) / 0.18) ** 2)
    rad = math.hypot((u - 0.5) * (1.25 if landscape else 1.0),
                     (v - 0.5) * (1.0 if landscape else 1.25)) * (1.0 if aspect < 1.6 else 0.9)
    return sweep, 0.30 + 0.35 * sweep - 0.25 * rad


def _noise(q: int, r: int) -> float:
    n = math.sin(q * 127.1 + r * 311.7 + 4 * 74.7) * 43758.5453
    return n - math.floor(n)


def _to_l(value: float) -> int:
    return max(0, min(255, round(value * 255)))


def _offset(value: float) -> int:
    """Signed luminance change stored around mid-grey (128 = no change)."""
    return max(0, min(255, 128 + round(value * 255)))


def render_back(width_mm: float, height_mm: float, dpi: int = 300, bleed_mm: float = 0.0) -> Image.Image:
    """Render the back at card size plus bleed on every side."""
    tw, th = width_mm + 2 * bleed_mm, height_mm + 2 * bleed_mm
    w, h = round(tw / 25.4 * dpi), round(th / 25.4 * dpi)
    b = bleed_mm / 25.4 * dpi
    landscape, aspect = tw >= th, max(tw, th) / min(tw, th)

    def uv(x: float, y: float) -> tuple[float, float]:  # normalised to the trim box
        return (x - b) / max(w - 2 * b, 1), (y - b) / max(h - 2 * b, 1)

    # 1. smooth light layer, small grid -> upscale
    gw = 120
    gh = max(8, round(gw * h / w))
    base_small = Image.new("L", (gw, gh))
    base_small.putdata([_to_l(_light(*uv((i + 0.5) * w / gw, (j + 0.5) * h / gh), landscape, aspect)[1])
                        for j in range(gh) for i in range(gw)])
    base = base_small.resize((w, h), Image.BICUBIC)

    # 2. hex tiles as offsets around mid-grey (128 = no change), drawn supersampled
    ss = SUPERSAMPLE
    size = HEX_MM / 25.4 * dpi
    ox, oy = size * 0.16, size * 0.63  # pattern offset (keeps the original tile placement)
    tiles = Image.new("L", (w * ss, h * ss), 128)
    draw = ImageDraw.Draw(tiles)
    corner_angles = [math.radians(30 + 60 * k) for k in range(6)]  # pointy-top hexagon
    r_min, r_max = math.floor(-oy / (1.5 * size)) - 2, math.ceil((h + oy) / (1.5 * size)) + 2
    for r in range(r_min, r_max + 1):
        q_min = math.floor((-ox - SQ3 / 2 * size * r) / (SQ3 * size)) - 2
        q_max = math.ceil((w + ox - SQ3 / 2 * size * r) / (SQ3 * size)) + 2
        for q in range(q_min, q_max + 1):
            cx = size * (SQ3 * q + SQ3 / 2 * r) - ox
            cy = size * 1.5 * r - oy
            if cx < -size or cy < -size or cx > w + size or cy > h + size:
                continue
            sweep, _ = _light(*uv(cx, cy), landscape, aspect)
            tone = (_noise(q, r) - 0.5) * 0.10

            def ring(f: float) -> list[tuple[float, float]]:
                return [((cx + f * size * math.cos(a)) * ss, (cy + f * size * math.sin(a)) * ss)
                        for a in corner_angles]

            for outer_f, inner_f, bevel, seam in RINGS:
                outer, inner = ring(outer_f), ring(inner_f)
                for k in range(6):
                    k2 = (k + 1) % 6
                    mid = math.radians(60 * k + 60)  # outward normal of edge k -> k+1
                    shade = LIGHT_DIR[0] * -math.cos(mid) + LIGHT_DIR[1] * -math.sin(mid)
                    value = tone + bevel * shade * (0.05 + 0.10 * sweep) + seam
                    draw.polygon([outer[k], outer[k2], inner[k2], inner[k]], fill=_offset(value))
            draw.polygon(ring(RINGS[-1][1]), fill=_offset(tone))
    tiles = tiles.resize((w, h), Image.BOX)

    # 3. combine and colour
    lum = ImageChops.add(base, tiles, scale=1.0, offset=-128)
    channels = [lum.point(lambda v, lo=lo, hi=hi: round(lo + (hi - lo) * v / 255)) for lo, hi in zip(DARK, LIGHT)]
    return Image.merge("RGB", channels)


def back_path(cache_dir: Path, width_mm: float, height_mm: float, bleed_mm: float, dpi: int = 300) -> Path:
    """Generate (once) and return the cached back image for a size."""
    name = f"back_{STYLE_VERSION}_{width_mm:g}x{height_mm:g}_b{bleed_mm:g}_{dpi}.png"
    path = Path(cache_dir) / name
    if not path.exists():
        img = render_back(width_mm, height_mm, dpi=dpi, bleed_mm=bleed_mm)
        tmp = path.with_suffix(".part.png")
        img.save(tmp, dpi=(dpi, dpi))
        tmp.replace(path)
    return path
