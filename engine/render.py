"""Frame compositor. Renders one output frame from the render plan with Pillow.

Each frame: flat background -> browser window (chrome bar + recorded page,
cropped and scaled through the camera with Lanczos, sub-pixel accurate) ->
rounded corners -> click ripples -> cursor -> caption pill. Title and end cards
are flat typographic cards.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PIL import Image, ImageDraw

from . import theme as th


def _rgb(v: Any) -> tuple[int, int, int]:
    return tuple(int(x) for x in v[:3])  # type: ignore[return-value]


def wrap(draw: ImageDraw.ImageDraw, text: str, f, max_w: float, max_lines: int = 2) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=f) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "...", font=f) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    return lines


class Compositor:
    def __init__(self, ctx: dict[str, Any]):
        self.ctx = ctx
        rp = ctx["plan"]
        self.W, self.H, self.fps = rp["W"], rp["H"], rp["fps"]
        self.L = rp["layout"]
        self.frames_dir = Path(ctx["frames_dir"])
        self.names = ctx["frame_names"]
        self.th = ctx["theme"]
        self.bg = _rgb(self.th["bg"])
        self.vw, self.vh, self.dpr = ctx["viewport"][0], ctx["viewport"][1], ctx["dpr"]
        self.src_w = self.vw * self.dpr
        self.src_h = self.vh * self.dpr
        self.k = self.src_w / self.L["win_w"]  # source px per canvas px
        self.chrome_src_h = int(round(self.L["chrome_h"] * self.k))
        self._frames: OrderedDict = OrderedDict()
        self._windows: OrderedDict = OrderedDict()
        self._chrome: dict[int, Image.Image] = {}
        self._corner: dict[int, Image.Image] = {}
        self._caps: dict[int, Image.Image] = {}
        self._cursor: dict[int, Image.Image] = {}
        self.title_img = self._title_card()
        self.end_img = self._end_card()
        self.base_bg = Image.new("RGB", (self.W, self.H), self.bg)
        self.header = self._portrait_header() if self.L["portrait"] else None

    # ---------------- sources --------------------------------------------------
    def frame(self, idx: int) -> Image.Image:
        if idx in self._frames:
            self._frames.move_to_end(idx)
            return self._frames[idx]
        im = Image.open(self.frames_dir / self.names[idx]).convert("RGB")
        if im.size != (self.src_w, self.src_h):
            im = im.resize((self.src_w, self.src_h), Image.Resampling.LANCZOS)
        self._frames[idx] = im
        if len(self._frames) > 6:
            self._frames.popitem(last=False)
        return im

    def chrome(self, url_i: int) -> Image.Image:
        if url_i in self._chrome:
            return self._chrome[url_i]
        c = self.th["chrome"]
        h = self.chrome_src_h
        im = Image.new("RGB", (self.src_w, h), _rgb(c["bar"]))
        d = ImageDraw.Draw(im)
        r = h * 0.11
        for i in range(3):
            cx = h * 0.62 + i * r * 3.3
            d.ellipse((cx - r, h / 2 - r, cx + r, h / 2 + r), fill=_rgb(c["dot"]))
        pw, ph = self.src_w * 0.42, h * 0.6
        x0, y0 = (self.src_w - pw) / 2, (h - ph) / 2
        d.rounded_rectangle((x0, y0, x0 + pw, y0 + ph), radius=ph / 2, fill=_rgb(c["pill"]))
        urls = self.ctx["plan"].get("urls") or [self.ctx.get("url", "")]
        u = urls[min(url_i, len(urls) - 1)] if urls else ""
        pu = urlparse(u)
        label = (pu.netloc.replace("www.", "") + (pu.path if pu.path not in ("", "/") else "")).rstrip("/")
        f = th.font(int(h * 0.36), "medium")
        while d.textlength(label, font=f) > pw * 0.9 and len(label) > 8:
            label = label[:-2]
        d.text((self.src_w / 2, h / 2), label, font=f, fill=_rgb(c["text"]), anchor="mm")
        d.line((0, h - 1, self.src_w, h - 1), fill=_rgb(c["line"]), width=max(1, int(self.k)))
        self._chrome[url_i] = im
        return im

    def window(self, src: list, url_i: int) -> Image.Image:
        key = (tuple(src), url_i)
        if key in self._windows:
            self._windows.move_to_end(key)
            return self._windows[key]
        if len(src) == 1:
            page = self.frame(src[0])
        else:
            page = Image.blend(self.frame(src[0]), self.frame(src[1]), float(src[2]))
        im = Image.new("RGB", (self.src_w, self.chrome_src_h + self.src_h))
        im.paste(self.chrome(url_i), (0, 0))
        im.paste(page, (0, self.chrome_src_h))
        self._windows[key] = im
        if len(self._windows) > 4:
            self._windows.popitem(last=False)
        return im

    # ---------------- sprites --------------------------------------------------
    def corner(self, r: int) -> Image.Image:
        """Anti-aliased quarter-circle mask (top-left orientation), 255 inside."""
        if r in self._corner:
            return self._corner[r]
        ss = 4
        big = Image.new("L", (r * ss, r * ss), 0)
        d = ImageDraw.Draw(big)
        d.ellipse((0, 0, 2 * r * ss - 1, 2 * r * ss - 1), fill=255)
        im = big.resize((r, r), Image.Resampling.LANCZOS)
        self._corner[r] = im
        return im

    def cursor_sprite(self, h: int) -> Image.Image:
        h = max(12, h)
        if h in self._cursor:
            return self._cursor[h]
        ss = 4
        S = h * ss
        # classic arrow, tip at (0, 0); unit grid is 17 x 25
        pts = [(0, 0), (0, 21), (5, 16.5), (8.6, 24.4), (11.6, 23.1), (8.1, 15.4), (14.5, 15.4)]
        u = S / 25.0
        pad = 2.2 * u
        im = Image.new("RGBA", (int(17 * u + 2 * pad), int(S + 2 * pad)), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        poly = [(pad + x * u, pad + y * u) for x, y in pts]
        # white border = thick white outline drawn first; the dark fill then covers its inner half
        d.polygon(poly, fill=(255, 255, 255, 255))
        d.line(poly + [poly[0]], fill=(255, 255, 255, 255), width=int(3.0 * u), joint="curve")
        for x, y in poly:
            r = 1.5 * u
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 255))
        d.polygon(poly, fill=(17, 17, 19, 255))
        out = im.resize((max(1, im.width // ss), max(1, im.height // ss)), Image.Resampling.LANCZOS)
        self._cursor[h] = (out, pad / ss)  # type: ignore[assignment]
        return self._cursor[h]

    def badge(self, text: str) -> Image.Image:
        key = -hash(text)
        if key in self._caps:
            return self._caps[key]
        size = int(self.H * (0.016 if self.L["portrait"] else 0.024))
        f = th.font(size, "semibold")
        tmp = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        w = int(tmp.textlength(text, font=f) + size * 1.6)
        h = int(size * 2.0)
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h / 2, fill=(*_rgb(self.th["cap_bg"]), 235))
        d.text((w / 2, h / 2), text, font=f, fill=(*_rgb(self.th["cap_fg"]), 255), anchor="mm")
        self._caps[key] = im
        return im

    def caption_img(self, ci: int) -> Image.Image:
        if ci in self._caps:
            return self._caps[ci]
        cap = self.ctx["plan"]["captions"][ci]
        portrait = self.L["portrait"]
        size = int(self.H * (0.0235 if portrait else 0.0335))
        f = th.font(size, "semibold")
        fn = th.font(size, "semibold")
        tmp = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        num = f"{cap['n']:02d}"
        num_w = tmp.textlength(num, font=fn)
        gap = size * 0.7
        max_w = self.W * (0.82 if portrait else 0.62)
        lines = wrap(tmp, cap["text"], f, max_w - num_w - gap)
        lh = size * 1.32
        tw = max(tmp.textlength(ln, font=f) for ln in lines)
        px, py = size * 0.85, size * 0.62
        w = int(px * 2 + num_w + gap + tw)
        h = int(py * 2 + lh * len(lines) - (lh - size) * 0.35)
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        rad = min(h / 2, size * 0.9)
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=rad, fill=(*_rgb(self.th["cap_bg"]), 238))
        y = py + size * 0.5
        d.text((px, y), num, font=fn, fill=(*_rgb(self.th["cap_num"]), 255), anchor="lm")
        for i, ln in enumerate(lines):
            d.text((px + num_w + gap, y + i * lh), ln, font=f, fill=(*_rgb(self.th["cap_fg"]), 255), anchor="lm")
        self._caps[ci] = im
        return im

    # ---------------- cards ------------------------------------------------------
    def _title_card(self) -> Image.Image:
        W, H = self.W, self.H
        im = Image.new("RGB", (W, H), self.bg)
        d = ImageDraw.Draw(im)
        fg = _rgb(self.th["fg"])
        title = self.ctx.get("title") or ""
        tagline = self.ctx.get("tagline") or ""
        domain = urlparse(self.ctx.get("url", "")).netloc.replace("www.", "")
        big = int(H * (0.052 if self.L["portrait"] else 0.1))
        ft = th.font(big, "bold")
        lines = wrap(d, title, ft, W * 0.84, 2)
        while max(d.textlength(x, font=ft) for x in lines) > W * 0.86 and big > 20:
            big = int(big * 0.92)
            ft = th.font(big, "bold")
            lines = wrap(d, title, ft, W * 0.84, 2)
        fs = int(H * (0.017 if self.L["portrait"] else 0.03))
        fsm = th.font(fs, "regular")
        tag_lines = wrap(d, tagline, fsm, W * (0.8 if self.L["portrait"] else 0.56), 2) if tagline else []
        lh_t, lh_s = big * 1.08, fs * 1.45
        block = fs * 1.2 + fs * 1.6 + lh_t * len(lines) + (fs * 1.0 + lh_s * len(tag_lines) if tag_lines else 0)
        y = (H - block) / 2
        dim = tuple(int(fg[i] * 0.62 + self.bg[i] * 0.38) for i in range(3))
        d.text((W / 2, y + fs * 0.6), domain, font=th.font(fs, "medium"), fill=dim, anchor="mm")
        y += fs * 1.2 + fs * 1.6
        for ln in lines:
            d.text((W / 2, y + lh_t / 2), ln, font=ft, fill=fg, anchor="mm")
            y += lh_t
        if tag_lines:
            y += fs * 1.0
            dim2 = tuple(int(fg[i] * 0.8 + self.bg[i] * 0.2) for i in range(3))
            for ln in tag_lines:
                d.text((W / 2, y + lh_s / 2), ln, font=fsm, fill=dim2, anchor="mm")
                y += lh_s
        return im

    def _end_card(self) -> Image.Image:
        W, H = self.W, self.H
        im = Image.new("RGB", (W, H), self.bg)
        d = ImageDraw.Draw(im)
        fg = _rgb(self.th["fg"])
        title = self.ctx.get("end_title") or self.ctx.get("title") or ""
        url = self.ctx.get("cta_url") or self.ctx.get("url", "")
        label = urlparse(url).netloc.replace("www.", "") + (urlparse(url).path.rstrip("/") or "")
        big = int(H * (0.042 if self.L["portrait"] else 0.07))
        ft = th.font(big, "bold")
        lines = wrap(d, title, ft, W * 0.84, 2)
        fs = int(H * (0.02 if self.L["portrait"] else 0.032))
        fpill = th.font(fs, "semibold")
        pw = d.textlength(label, font=fpill) + fs * 2.2
        ph = fs * 2.3
        block = big * 1.08 * len(lines) + fs * 1.6 + ph
        y = (H - block) / 2 - H * 0.02
        for ln in lines:
            d.text((W / 2, y + big * 0.54), ln, font=ft, fill=fg, anchor="mm")
            y += big * 1.08
        y += fs * 1.6
        acc = _rgb(self.th["accent"])
        if th.contrast(acc, self.bg) < 1.5:
            acc = fg
        afg = th.text_on(acc)
        d.rounded_rectangle(((W - pw) / 2, y, (W + pw) / 2, y + ph), radius=ph / 2, fill=acc)
        d.text((W / 2, y + ph / 2), label, font=fpill, fill=afg, anchor="mm")
        foot = th.font(int(fs * 0.62), "medium")
        dim = tuple(int(fg[i] * 0.5 + self.bg[i] * 0.5) for i in range(3))
        d.text((W / 2, H - H * 0.06), "Made with Ship Reel for Hermes Agent", font=foot, fill=dim, anchor="mm")
        return im

    def _portrait_header(self) -> Image.Image:
        im = Image.new("RGBA", (self.W, int(self.H * 0.12)), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        f = th.font(int(self.H * 0.03), "bold")
        title = self.ctx.get("title") or ""
        lines = wrap(d, title, f, self.W * 0.86, 1)
        d.text((self.W / 2, im.height / 2), lines[0] if lines else "", font=f, fill=(*_rgb(self.th["fg"]), 255), anchor="mm")
        return im

    # ---------------- per-frame ----------------------------------------------------
    def render(self, fr: dict[str, Any]) -> Image.Image:
        ph = fr["phase"]
        if ph == "title":
            return Image.blend(self.base_bg, self.title_img, max(0.0, min(1.0, fr.get("title_a", 1.0))))
        if ph == "end":
            return Image.blend(self.base_bg, self.end_img, max(0.0, min(1.0, fr.get("end_a", 1.0))))
        out = self.base_bg.copy()
        if self.header is not None:
            a = fr.get("win_a", 1.0)
            hdr = self.header if a >= 0.999 else _fade(self.header, a)
            out.paste(hdr, (0, int(self.H * 0.075)), hdr)
        z, cx, cy = fr["cam"]
        s = fr.get("win_s", 1.0)
        dy = fr.get("win_dy", 0.0)
        win_a = fr.get("win_a", 1.0)
        L = self.L
        W, H = self.W, self.H
        wcx, wcy = L["win_x"] + L["win_w"] / 2, L["win_y"] + (L["chrome_h"] + L["content_h"]) / 2

        def to_out(px: float, py: float) -> tuple[float, float]:
            qx = wcx + (px - wcx) * s
            qy = wcy + (py - wcy) * s + dy
            return (qx - cx) * z + W / 2, (qy - cy) * z + H / 2

        ox, oy = to_out(L["win_x"], L["win_y"])
        ow = L["win_w"] * s * z
        oh = (L["chrome_h"] + L["content_h"]) * s * z
        win = self.window(fr["src"], fr.get("url", 0))
        m = ow / win.width
        bx0, by0 = max(0, math.floor(ox)), max(0, math.floor(oy))
        bx1, by1 = min(W, math.ceil(ox + ow)), min(H, math.ceil(oy + oh))
        if bx1 > bx0 and by1 > by0:
            box = (
                max(0.0, (bx0 - ox) / m), max(0.0, (by0 - oy) / m),
                min(float(win.width), (bx1 - ox) / m), min(float(win.height), (by1 - oy) / m),
            )
            region = win.resize((bx1 - bx0, by1 - by0), Image.Resampling.LANCZOS, box=box, reducing_gap=3.0)
            mask = Image.new("L", region.size, int(255 * win_a))
            r = max(1, int(round(L["radius"] * s * z)))
            cm = self.corner(r)
            if win_a < 0.999:
                cm = cm.point(lambda v, a=win_a: int(v * a))
            corners = [
                (ox, oy, cm),
                (ox + ow - r, oy, cm.transpose(Image.Transpose.FLIP_LEFT_RIGHT)),
                (ox, oy + oh - r, cm.transpose(Image.Transpose.FLIP_TOP_BOTTOM)),
                (ox + ow - r, oy + oh - r, cm.transpose(Image.Transpose.ROTATE_180)),
            ]
            for x, y, c in corners:
                px, py = int(round(x)) - bx0, int(round(y)) - by0
                if px + r > 0 and py + r > 0 and px < region.width and py < region.height:
                    mask.paste(c, (px, py))
            out.paste(region, (bx0, by0), mask)

        css_s = L["win_w"] / self.vw  # canvas px per css px

        def css_out(x: float, y: float) -> tuple[float, float]:
            return to_out(L["win_x"] + x * css_s, L["win_y"] + L["chrome_h"] + y * css_s)

        # ripples
        for rx, ry, p in fr.get("rip", []):
            X, Y = css_out(rx, ry)
            rad = (7 + 30 * (1 - (1 - p) ** 3)) * css_s * z * s
            alpha = (1 - p) ** 1.6
            self._ring(out, X, Y, rad, max(2.0, 3.2 * css_s * z), _rgb(self.th["ripple"]), alpha)

        # cursor
        if "cur" in fr and win_a > 0.05:
            x, y, sc = fr["cur"]
            X, Y = css_out(x, y)
            ch = int(round(27 * css_s * (z ** 0.85) * s * sc))
            spr, pad = self.cursor_sprite(ch)
            if win_a < 0.999:
                spr = _fade(spr, win_a)
            out.paste(spr, (int(round(X - pad)), int(round(Y - pad))), spr)

        # time-lapse badge
        if fr.get("ff"):
            badge = self.badge(f"{fr['ff']:.0f}x speed" if fr["ff"] >= 3 else f"{fr['ff']:.1f}x speed")
            m = int(H * 0.035)
            out.paste(badge, (W - m - badge.width, m), badge)

        # captions
        for ci, alpha, cdy in fr.get("cap", []):
            img = self.caption_img(ci)
            if L["portrait"]:
                y = H * 0.775
            else:
                y = H - H * 0.065 - img.height
            x = (W - img.width) / 2
            im2 = img if alpha >= 0.999 else _fade(img, alpha)
            out.paste(im2, (int(round(x)), int(round(y + cdy))), im2)
        return out

    def _ring(self, out: Image.Image, X: float, Y: float, rad: float, width: float, color, alpha: float) -> None:
        if alpha <= 0.02 or rad < 1:
            return
        ss = 3
        size = int((rad + width) * 2 + 4)
        big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        d = ImageDraw.Draw(big)
        c = size * ss / 2
        R = rad * ss
        d.ellipse((c - R, c - R, c + R, c + R), fill=(*color, int(60 * alpha)), outline=(*color, int(235 * alpha)), width=max(1, int(width * ss)))
        sm = big.resize((size, size), Image.Resampling.LANCZOS)
        out.paste(sm, (int(round(X - size / 2)), int(round(Y - size / 2))), sm)


def _fade(img: Image.Image, a: float) -> Image.Image:
    im = img.copy()
    alpha = im.getchannel("A").point(lambda v: int(v * a))
    im.putalpha(alpha)
    return im
