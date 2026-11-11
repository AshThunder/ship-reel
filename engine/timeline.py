"""Edit decision list: turns a recording's event log into a per-frame render plan.

Pure Python and deterministic, so it is unit-tested without a browser or ffmpeg.
Concepts:
  * source time  - wall-clock seconds of the recording (events.json)
  * output time  - seconds in the finished reel
  * canvas       - the output frame at zoom 1 (e.g. 1920x1080) with the browser window inset
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Any

TITLE_D = 2.4        # title card
INTRO_D = 0.75       # window rises in
OUTRO_D = 0.6        # window leaves
END_D = 3.0          # end card
XFADE_D = 0.42       # page-to-page cut
SMOOTH_T = 0.38      # camera smoothing time constant (s)
MAX_SECONDS = 88.0   # hard ceiling we edit to (X allows 140s; the brief asks <= 90s)


@dataclass
class Layout:
    W: int
    H: int
    win_x: float
    win_y: float
    win_w: float
    chrome_h: float
    content_h: float
    radius: float
    portrait: bool
    vw: int = 1440
    vh: int = 810

    @property
    def win_h(self) -> float:
        return self.chrome_h + self.content_h

    @property
    def content_y(self) -> float:
        return self.win_y + self.chrome_h

    def css_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        s = self.win_w / self.vw
        return self.win_x + x * s, self.content_y + y * s


def make_layout(W: int, H: int, vw: int = 1440, vh: int = 810) -> Layout:
    portrait = H > W
    if portrait:
        win_w = W - 2 * round(W * 0.045)
        content_h = win_w * vh / vw
        chrome_h = round(win_w * 0.032)
        win_x = (W - win_w) / 2
        win_y = H * 0.44 - (chrome_h + content_h) / 2
        radius = round(W * 0.018)
    else:
        win_w = round(W * 0.8334)
        content_h = win_w * vh / vw
        chrome_h = round(win_w * 0.026)
        win_x = (W - win_w) / 2
        win_y = (H - (chrome_h + content_h)) / 2
        radius = round(W * 0.0075)
    return Layout(W, H, win_x, win_y, win_w, chrome_h, content_h, radius, portrait, vw, vh)


# ---------------------------------------------------------------------------
# Source -> output time mapping
# ---------------------------------------------------------------------------
@dataclass
class Seg:
    s0: float
    s1: float
    mode: str          # "play" | "xfade"
    rate: float = 1.0  # playback speed for "play"
    o0: float = 0.0    # output start (filled by build_segments)

    @property
    def out_len(self) -> float:
        return XFADE_D if self.mode == "xfade" else (self.s1 - self.s0) / self.rate


def build_segments(events: dict[str, Any], dwell_rate: float = 1.0, speed: float = 1.0,
                   act_rate: float = 1.0) -> list[Seg]:
    """Slice the recording into played and cross-faded segments.

    * 'nav' intervals (page loads) become a short cross-fade.
    * failed steps are removed (cross-faded over).
    * dwell plays at dwell_rate, everything else at act_rate; both times `speed`.
    """
    t0, t1 = events["t0"], events["t1"]
    cuts: list[tuple[float, float]] = []
    for iv in events.get("intervals", []):
        if iv["kind"] == "nav":
            cuts.append((max(t0, iv["t0"]), min(t1, iv["t1"])))
    for st in events.get("steps", []):
        if not st.get("ok"):
            cuts.append((max(t0, st["t_start"]), min(t1, st["t_end"])))
    cuts = _merge([c for c in cuts if c[1] - c[0] > 0.02])
    dwell = _merge([(iv["t0"], iv["t1"]) for iv in events.get("intervals", []) if iv["kind"] == "dwell"])
    # time-lapse waits: compress each to about 4.5 s of output, at least 2.5x
    fast = [(iv["t0"], iv["t1"], max(2.5, (iv["t1"] - iv["t0"]) / 4.5))
            for iv in events.get("intervals", []) if iv["kind"] == "fast" and iv["t1"] - iv["t0"] > 0.5]

    segs: list[Seg] = []
    cur = t0
    for c0, c1 in cuts + [(t1, t1)]:
        if c0 > cur:
            segs += _split_rates(cur, c0, dwell, fast, dwell_rate * speed, act_rate * speed, speed)
        if c1 > c0:
            segs.append(Seg(c0, c1, "xfade"))
        cur = max(cur, c1)
    o = 0.0
    for s in segs:
        s.o0 = o
        o += s.out_len
    return segs


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _split_rates(a: float, b: float, dwell: list[tuple[float, float]], fast: list[tuple[float, float, float]],
                 d_rate: float, a_rate: float, speed: float) -> list[Seg]:
    pts = {a, b}
    for d0, d1 in dwell:
        if d1 > a and d0 < b:
            pts.add(max(a, d0))
            pts.add(min(b, d1))
    for f0, f1, _ in fast:
        if f1 > a and f0 < b:
            pts.add(max(a, f0))
            pts.add(min(b, f1))
    pts_l = sorted(pts)
    out: list[Seg] = []
    for x, y in zip(pts_l, pts_l[1:]):
        if y - x < 1e-4:
            continue
        mid = (x + y) / 2
        fr = next((r for f0, f1, r in fast if f0 <= mid <= f1), None)
        if fr is not None:
            rate = fr * speed
        elif any(d0 <= mid <= d1 for d0, d1 in dwell):
            rate = d_rate
        else:
            rate = a_rate
        if out and out[-1].mode == "play" and abs(out[-1].rate - rate) < 1e-9 and abs(out[-1].s1 - x) < 1e-6:
            out[-1].s1 = y
        else:
            out.append(Seg(x, y, "play", rate))
    return out


def body_length(segs: list[Seg]) -> float:
    return segs[-1].o0 + segs[-1].out_len if segs else 0.0


def src_at(segs: list[Seg], o: float) -> tuple:
    """Output body time -> ('play', s) or ('xfade', sA, sB, alpha)."""
    if not segs:
        return ("play", 0.0)
    starts = [s.o0 for s in segs]
    i = max(0, bisect.bisect_right(starts, o) - 1)
    sg = segs[i]
    if o >= sg.o0 + sg.out_len and i == len(segs) - 1:
        return ("play", sg.s1)
    local = max(0.0, o - sg.o0)
    if sg.mode == "xfade":
        p = min(1.0, local / XFADE_D)
        return ("xfade", sg.s0, sg.s1, _smooth(p))
    return ("play", sg.s0 + local * sg.rate)


def out_of_src(segs: list[Seg], s: float) -> float:
    """Source time -> output body time (times inside a cut map to the cut's start)."""
    for sg in segs:
        if sg.s0 <= s <= sg.s1:
            if sg.mode == "xfade":
                return sg.o0
            return sg.o0 + (s - sg.s0) / sg.rate
    if segs and s > segs[-1].s1:
        return body_length(segs)
    return 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _smooth(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return p * p * (3 - 2 * p)


def ease_out_cubic(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def ease_in_out(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def interp_path(path: list[list[float]], t: float) -> tuple[float, float]:
    """Linear interpolation of a [(t, x, y), ...] path."""
    if not path:
        return (720.0, 500.0)
    ts = [p[0] for p in path]
    i = bisect.bisect_right(ts, t)
    if i == 0:
        return path[0][1], path[0][2]
    if i >= len(path):
        return path[-1][1], path[-1][2]
    a, b = path[i - 1], path[i]
    span = b[0] - a[0]
    k = 0.0 if span <= 0 else (t - a[0]) / span
    return a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k


def frame_at(frame_ts: list[float], s: float) -> int:
    i = bisect.bisect_right(frame_ts, s) - 1
    return max(0, min(len(frame_ts) - 1, i))


class SmoothDamp:
    """Critically damped spring (Unity SmoothDamp) - the 'Screen Studio' camera feel."""

    def __init__(self, value: float, smooth_time: float = SMOOTH_T):
        self.v = value
        self.vel = 0.0
        self.t = smooth_time

    def step(self, target: float, dt: float) -> float:
        omega = 2.0 / self.t
        x = omega * dt
        exp = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
        change = self.v - target
        temp = (self.vel + omega * change) * dt
        self.vel = (self.vel - omega * temp) * exp
        self.v = target + (change + temp) * exp
        return self.v


def clamp_camera(z: float, cx: float, cy: float, L: Layout) -> tuple[float, float]:
    """Keep the zoomed view inside the browser window when it fits, else inside the canvas.

    The window rect is inset by its corner radius so a zoomed view never shows a sliver of
    background or a rounded corner at the frame edge.
    """
    if z <= 1.0001:
        return L.W / 2, L.H / 2
    vw, vh = L.W / z, L.H / z
    inset = L.radius + 2
    x0, x1 = L.win_x + inset, L.win_x + L.win_w - inset
    y0, y1 = L.win_y + inset, L.win_y + L.win_h - inset
    if vw <= x1 - x0:
        cx = min(max(cx, x0 + vw / 2), x1 - vw / 2)
    else:
        cx = min(max(cx, vw / 2), L.W - vw / 2) if vw <= L.W else L.W / 2
    if vh <= y1 - y0:
        cy = min(max(cy, y0 + vh / 2), y1 - vh / 2)
    else:
        cy = min(max(cy, vh / 2), L.H - vh / 2) if vh <= L.H else L.H / 2
    return cx, cy


# ---------------------------------------------------------------------------
# Plan building
# ---------------------------------------------------------------------------
@dataclass
class RenderPlan:
    fps: int
    W: int
    H: int
    layout: dict[str, Any]
    frames: list[dict[str, Any]] = field(default_factory=list)
    captions: list[dict[str, Any]] = field(default_factory=list)
    clicks_out: list[float] = field(default_factory=list)
    keys_out: list[float] = field(default_factory=list)
    duration: float = 0.0
    body: float = 0.0
    speed: float = 1.0
    urls: list[str] = field(default_factory=list)
    xfades_out: list[float] = field(default_factory=list)


def zoom_windows(events: dict[str, Any], plan_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Source-time windows where the camera zooms toward an interaction."""
    clicks = events.get("clicks", [])
    navs = [iv for iv in events.get("intervals", []) if iv["kind"] == "nav"]
    types = [iv for iv in events.get("intervals", []) if iv["kind"] == "type"]
    out = []
    for st in events.get("steps", []):
        i = st["index"]
        ps = plan_steps[i] if i < len(plan_steps) else {}
        if not st.get("ok") or not ps.get("zoom") or not st.get("focus"):
            continue
        t_act = st.get("t_action_end", st["t_start"])
        cl = [c for c in clicks if st["t_start"] <= c["t"] <= st["t_end"]]
        if cl:
            t_act = cl[-1]["t"]
        z0 = max(st["t_start"], t_act - 0.85)
        nav = next((n for n in navs if t_act <= n["t0"] <= t_act + 1.5), None)
        if nav or st.get("how") == "url":  # page or route change: pull back to reveal it
            z1 = t_act + (0.3 if nav else 0.55)
        else:
            ty = next((iv for iv in types if st["t_start"] <= iv["t0"] <= st["t_end"]), None)
            z1 = (ty["t1"] + 0.9) if ty else t_act + 1.5
            z1 = min(z1, st["t_end"] - 0.15)
        if z1 > z0:
            out.append({"z0": z0, "z1": z1, "zoom": ps.get("zoom_level", 1.6),
                        "focus": st["focus"], "t_act": t_act})
    return out


def build(events: dict[str, Any], plan: dict[str, Any], W: int = 1920, H: int = 1080, fps: int = 30,
          speed: float = 1.0, dwell_rate: float = 1.0, zoom: bool = True,
          max_seconds: float = MAX_SECONDS) -> RenderPlan:
    L = make_layout(W, H, *events.get("viewport", [1440, 810]))
    plan_steps = plan.get("steps", [])

    # Fit the body into the time budget: speed up dwell first, then everything.
    budget = max_seconds - (TITLE_D + INTRO_D + OUTRO_D + END_D)
    segs = build_segments(events, dwell_rate, speed)
    body = body_length(segs)
    if body > budget:
        extra_dwell = min(1.7, dwell_rate * body / budget)
        segs = build_segments(events, extra_dwell, speed)
        body = body_length(segs)
        for _ in range(6):  # cross-fades have fixed length, so converge iteratively
            if body <= budget:
                break
            speed = speed * body / budget * 1.01
            segs = build_segments(events, extra_dwell, speed)
            body = body_length(segs)
        dwell_rate = extra_dwell

    frame_ts = [f[0] for f in events["frames"]]
    cursor = events.get("cursor", [])
    zwins = zoom_windows(events, plan_steps) if zoom else []
    urls_ev = events.get("urls", [])
    url_list = []
    for _, u in urls_ev:
        if u not in url_list:
            url_list.append(u)

    def url_idx(s: float) -> int:
        cur = urls_ev[0][1] if urls_ev else ""
        for t, u in urls_ev:
            if t <= s + 0.05:
                cur = u
        return url_list.index(cur) if cur in url_list else 0

    body_start = TITLE_D + INTRO_D
    total = body_start + body + OUTRO_D + END_D
    rp = RenderPlan(fps=fps, W=W, H=H, layout=L.__dict__.copy(), duration=round(total, 3), body=round(body, 3),
                    speed=round(speed, 3), urls=url_list)

    # captions (output time)
    ok_steps = [st for st in events.get("steps", []) if st.get("ok")]
    caps = []
    for st in ok_steps:
        text = (plan_steps[st["index"]] if st["index"] < len(plan_steps) else {}).get("caption") or st.get("caption")
        if not text:
            continue
        a = body_start + out_of_src(segs, st["t_start"])
        b = body_start + out_of_src(segs, st["t_end"])
        caps.append({"text": text, "a": a, "b": b, "n": len(caps) + 1})
    for i, c in enumerate(caps):  # close gaps so captions hand over cleanly
        if i + 1 < len(caps):
            c["b"] = max(c["b"], caps[i + 1]["a"])
        c["b"] = min(c["b"], body_start + body)
        if c["b"] - c["a"] < 1.2:
            c["b"] = min(body_start + body, c["a"] + 1.2)
    rp.captions = [{**c, "a": round(c["a"], 3), "b": round(c["b"], 3)} for c in caps]
    rp.clicks_out = [round(body_start + out_of_src(segs, c["t"]), 3) for c in events.get("clicks", [])]
    rp.clicks_out = [c for c, cl in zip(rp.clicks_out, events.get("clicks", []))
                     if not _in_cut(segs, cl["t"])]
    rp.keys_out = [round(body_start + out_of_src(segs, k), 3) for k in events.get("keys", []) if not _in_cut(segs, k)]
    rp.xfades_out = [round(body_start + sg.o0, 3) for sg in segs if sg.mode == "xfade"]
    clicks_src = [(c["t"], c["x"], c["y"]) for c in events.get("clicks", []) if not _in_cut(segs, c["t"])]

    # Portrait: the 16:9 window is small in a 9:16 frame, so the camera rests at a higher
    # zoom and pans with the cursor; landscape rests on the whole window.
    base_z = 1.5 if L.portrait else 1.0
    zoom_mul = 1.22 if L.portrait else 1.0
    home = (W / 2, L.win_y + L.win_h / 2) if L.portrait else (W / 2, H / 2)
    cam_z, cam_x, cam_y = SmoothDamp(base_z), SmoothDamp(home[0]), SmoothDamp(home[1])
    dt = 1.0 / fps
    n_frames = int(round(total * fps))
    for fi in range(n_frames):
        t = fi * dt
        fr: dict[str, Any] = {"t": round(t, 4)}
        if t < TITLE_D:
            fr["phase"] = "title"
            fr["title_a"] = 1.0 if t < TITLE_D - 0.45 else 1 - _smooth((t - (TITLE_D - 0.45)) / 0.45)
            fr["title_a"] *= _smooth(t / 0.35)
            rp.frames.append(fr)
            continue
        if t >= body_start + body + OUTRO_D:
            fr["phase"] = "end"
            fr["end_a"] = _smooth((t - (body_start + body + OUTRO_D)) / 0.5)
            rp.frames.append(fr)
            continue
        ob = min(max(0.0, t - body_start), body)
        src = src_at(segs, ob)
        s_now = src[1] if src[0] == "play" else src[2]
        if src[0] == "play":
            fr["src"] = [frame_at(frame_ts, src[1])]
        else:
            fr["src"] = [frame_at(frame_ts, src[1]), frame_at(frame_ts, src[2]), round(src[3], 4)]
        fr["url"] = url_idx(s_now)
        if src[0] == "play":
            sg_now = segs[max(0, bisect.bisect_right([g.o0 for g in segs], ob) - 1)]
            if sg_now.rate >= 2.4 * speed:
                fr["ff"] = round(sg_now.rate, 1)
        if t < body_start:
            p = ease_out_cubic((t - TITLE_D) / INTRO_D)
            fr["phase"] = "intro"
            fr["win_a"] = round(_smooth((t - TITLE_D) / (INTRO_D * 0.7)), 4)
            fr["win_s"] = round(0.9 + 0.1 * p, 5)
            fr["win_dy"] = round((1 - p) * H * 0.05, 2)
        elif t >= body_start + body:
            p = ease_in_out((t - body_start - body) / OUTRO_D)
            fr["phase"] = "outro"
            fr["win_a"] = round(1 - p, 4)
            fr["win_s"] = round(1 - 0.05 * p, 5)
            fr["win_dy"] = 0.0
        else:
            fr["phase"] = "body"

        # camera target
        tz, tx, ty = base_z, home[0], home[1]
        if L.portrait:
            tx = L.css_to_canvas(*interp_path(cursor, s_now))[0]
        if fr["phase"] == "body":
            for zw in zwins:
                if zw["z0"] <= s_now <= zw["z1"]:
                    cxs, cys = interp_path(cursor, s_now) if s_now < zw["t_act"] else zw["focus"]
                    tz = min(2.4, zw["zoom"] * zoom_mul) if L.portrait else zw["zoom"]
                    tx, ty = L.css_to_canvas(cxs, cys)
                    break
        tx, ty = clamp_camera(tz, tx, ty, L)
        z = cam_z.step(tz, dt)
        cx = cam_x.step(tx, dt)
        cy = cam_y.step(ty, dt)
        cx, cy = clamp_camera(z, cx, cy, L)
        fr["cam"] = [round(z, 5), round(cx, 3), round(cy, 3)]

        # cursor
        px, py = interp_path(cursor, s_now)
        sc = 1.0
        for (ct, _, _) in clicks_src:
            d = s_now - ct
            if -0.02 <= d <= 0.22:
                sc = min(sc, 0.8 + 0.2 * _smooth(abs(d - 0.06) / 0.16))
        fr["cur"] = [round(px, 2), round(py, 2), round(sc, 3)]

        # click ripples (output-time based so speed-ups do not shorten them oddly)
        rips = []
        for co, (ct, cxp, cyp) in zip(rp.clicks_out, clicks_src):
            d = t - co
            if 0 <= d <= 0.55:
                rips.append([cxp, cyp, round(d / 0.55, 4)])
        if rips:
            fr["rip"] = rips

        # captions
        cl = []
        for ci, c in enumerate(rp.captions):
            if c["a"] - 0.01 <= t <= c["b"]:
                # hand-over without overlap: the old pill fades out in the last 0.2 s,
                # the next fades in over its first 0.22 s
                a_in = _smooth((t - c["a"]) / 0.22)
                a_out = 1 - _smooth((t - (c["b"] - 0.2)) / 0.2) if t > c["b"] - 0.2 else 1.0
                alpha = a_in * a_out
                if alpha > 0.01:
                    cl.append([ci, round(alpha, 3), round((1 - ease_out_cubic((t - c["a"]) / 0.3)) * 14, 2)])
        if fr["phase"] in ("intro", "outro"):
            cl = []
        if cl:
            fr["cap"] = cl
        rp.frames.append(fr)
    return rp


def _in_cut(segs: list[Seg], s: float) -> bool:
    return any(sg.mode == "xfade" and sg.s0 < s < sg.s1 for sg in segs)
