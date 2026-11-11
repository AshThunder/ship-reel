"""Colours, fonts and brand detection. Flat colours only: no gradients, no glow."""
from __future__ import annotations

import colorsys
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT_PATH = ASSETS / "fonts" / "Inter-Variable.ttf"
_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
WEIGHTS = {"regular": 400, "medium": 500, "semibold": 600, "bold": 700, "extrabold": 800}

DEFAULT_DARK = "#141416"


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"bad colour {h!r}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(c: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c[:3])


def luminance(c: tuple[int, int, int]) -> float:
    def ch(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in c[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def text_on(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    return (255, 255, 255) if contrast(bg, (255, 255, 255)) >= contrast(bg, (17, 17, 19)) else (17, 17, 19)


@lru_cache(maxsize=64)
def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    size = max(6, int(size))
    if FONT_PATH.exists():
        f = ImageFont.truetype(str(FONT_PATH), size)
        try:
            axes = f.get_variation_axes()
            vals = []
            for ax in axes:
                name = ax.get("name", b"")
                name = name.decode() if isinstance(name, bytes) else str(name)
                if "Weight" in name:
                    vals.append(WEIGHTS.get(weight, 400))
                elif "Optical" in name:
                    vals.append(max(ax["minimum"], min(ax["maximum"], size * 0.75)))
                else:
                    vals.append(ax.get("default", 0))
            f.set_variation_by_axes(vals)
        except Exception:
            pass
        return f
    for fb in _FALLBACKS:
        if Path(fb).exists():
            return ImageFont.truetype(fb, size)
    return ImageFont.load_default(size)


def brand_color(img: Image.Image) -> tuple[int, int, int] | None:
    """Dominant vivid colour in a screenshot (hue-binned), or None for neutral pages."""
    small = img.convert("RGB").resize((240, 135))
    total = 240 * 135
    bins: dict[int, list[tuple[int, int, int]]] = {}
    for r, g, b in small.getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s >= 0.5 and v >= 0.4:
            bins.setdefault(int(h * 18) % 18, []).append((r, g, b))
    if not bins:
        return None
    key, px = max(bins.items(), key=lambda kv: len(kv[1]))
    if len(px) / total < 0.0015:
        return None
    # most vivid half of the bin, averaged: closer to the real brand swatch than a plain mean
    px.sort(key=lambda c: colorsys.rgb_to_hsv(c[0] / 255, c[1] / 255, c[2] / 255)[1] * max(c), reverse=True)
    top = px[: max(1, len(px) // 2)]
    return tuple(int(sum(c[i] for c in top) / len(top)) for i in range(3))  # type: ignore[return-value]


def page_is_dark(img: Image.Image) -> bool:
    top = img.convert("RGB").crop((0, 0, img.width, max(4, img.height // 12))).resize((64, 4))
    px = list(top.getdata())
    avg = tuple(sum(p[i] for p in px) / len(px) for i in range(3))
    return luminance(avg) < 0.22  # type: ignore[arg-type]


def build_theme(first_frame: Image.Image | None, style: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve the reel palette. style.background / style.accent may be hex or 'auto'."""
    meta = meta or {}
    dark_page = page_is_dark(first_frame) if first_frame is not None else False
    brand = None
    if first_frame is not None:
        brand = brand_color(first_frame)
    tc = (meta.get("theme_color") or "").strip()
    if not brand and tc.startswith("#"):
        try:
            c = hex_to_rgb(tc)
            if colorsys.rgb_to_hsv(*(v / 255 for v in c))[1] > 0.4:
                brand = c
        except ValueError:
            pass
    bg_s = str(style.get("background") or "auto")
    if bg_s != "auto":
        bg = hex_to_rgb(bg_s)
    elif brand:
        bg = brand
    else:
        bg = hex_to_rgb(DEFAULT_DARK)
    # the window must stand apart from the background
    if first_frame is not None:
        edge = first_frame.convert("RGB").resize((32, 18)).getpixel((16, 2))
        if contrast(bg, edge) < 1.25:
            bg = hex_to_rgb(DEFAULT_DARK) if not dark_page else hex_to_rgb("#E9E9EC")
    ac_s = str(style.get("accent") or "auto")
    accent = hex_to_rgb(ac_s) if ac_s != "auto" else (brand or (250, 250, 250))
    fg = text_on(bg)
    # captions sit on the page, so they take the opposite tone of the page
    cap_bg, cap_fg = ((250, 250, 250), (17, 17, 19)) if dark_page else ((17, 17, 19), (250, 250, 250))
    cap_num = accent if contrast(accent, cap_bg) >= 3.0 else ((113, 113, 122) if dark_page else (161, 161, 170))
    if dark_page:
        chrome = {"bar": (31, 31, 35), "pill": (45, 45, 51), "text": (190, 190, 198), "dot": (70, 70, 78), "line": (45, 45, 51)}
    else:
        chrome = {"bar": (244, 244, 245), "pill": (228, 228, 231), "text": (82, 82, 91), "dot": (212, 212, 216), "line": (228, 228, 231)}
    return {
        "bg": bg, "fg": fg, "accent": accent, "accent_fg": text_on(accent),
        "cap_bg": cap_bg, "cap_fg": cap_fg, "cap_num": cap_num,
        "chrome": chrome, "ripple": accent if contrast(accent, (245, 245, 245)) > 1.6 else (17, 17, 19),
        "dark_page": dark_page,
    }
