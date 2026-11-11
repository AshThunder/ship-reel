"""Headless Chromium driver: probes pages, dry-runs plans and records reels.

Recording uses the Chrome DevTools screencast (Page.startScreencast) at device
scale factor 2, which gives ~50 fps JPEG frames at 2880x1620 for a 1440x810
viewport. That is far sharper than Playwright's built-in VP8 video and leaves
headroom for the editor's zooms. The cursor is not captured; every mouse
position is logged with a timestamp and the editor draws a clean cursor.
"""
from __future__ import annotations

import base64
import json
import math
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .plan import RISKY_RE

VIEWPORT = (1440, 810)
DPR = 2
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_PAGE_CSS = """
html { scroll-behavior: auto !important; scrollbar-width: none !important; }
::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; }
"""

_ROLE_HINT_RE = re.compile(
    r"^(button|link|text|heading|tab|menu|menuitem|nav|label|placeholder|checkbox|option)\s*:\s+(.+)$", re.I)

_SMOOTH_SCROLL_JS = """
([top, ms]) => new Promise((resolve) => {
  const max = Math.max(0, document.documentElement.scrollHeight - innerHeight);
  const y0 = scrollY, y1 = Math.max(0, Math.min(max, top)), dy = y1 - y0, t0 = performance.now();
  if (Math.abs(dy) < 2) return resolve(scrollY);
  const ease = (p) => p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
  function frame(now) {
    const p = Math.min(1, (now - t0) / ms);
    window.scrollTo(0, y0 + dy * ease(p));
    if (p < 1) requestAnimationFrame(frame); else setTimeout(() => resolve(scrollY), 60);
  }
  requestAnimationFrame(frame);
})
"""

_INVENTORY_JS = """
() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = (e) => (e.innerText || e.getAttribute('aria-label') || e.value || '').replace(/\\s+/g, ' ').trim();
  const heads = [...document.querySelectorAll('h1,h2,h3')].filter(vis).map(e => ({tag: e.tagName.toLowerCase(), text: txt(e).slice(0, 90), y: Math.round(e.getBoundingClientRect().top + scrollY)})).filter(h => h.text).slice(0, 14);
  const acts = [...document.querySelectorAll('a,button,[role=button],input,textarea,select')].filter(vis).map(e => ({
    kind: e.tagName === 'A' ? 'link' : (['INPUT','TEXTAREA','SELECT'].includes(e.tagName) ? 'field' : 'button'),
    text: txt(e).slice(0, 60) || e.getAttribute('placeholder') || '', href: e.getAttribute('href') || undefined,
    y: Math.round(e.getBoundingClientRect().top + scrollY)})).filter(a => a.text).slice(0, 60);
  const meta = (n) => (document.querySelector(`meta[name="${n}"],meta[property="${n}"]`) || {}).content || '';
  return { title: document.title, description: meta('description') || meta('og:description'),
           site_name: meta('og:site_name'), theme_color: meta('theme-color'),
           height: document.documentElement.scrollHeight, headings: heads, actions: acts };
}
"""


class StepFailed(Exception):
    pass


def run_isolated(fn: Callable[[], Any], timeout: float = 900) -> Any:
    """Run sync-Playwright work on a fresh thread (no asyncio loop), return its result."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in caller
            box["error"] = exc

    t = threading.Thread(target=target, name="ship-reel-browser", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"browser work exceeded {timeout:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _launch(p):
    try:
        return p.chromium.launch(headless=True, args=["--hide-scrollbars", "--force-color-profile=srgb"])
    except Exception as exc:  # browser binary missing
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            raise RuntimeError(
                "Chromium for Playwright is not installed. Run once: python -m playwright install chromium"
            ) from exc
        raise


def _ease(p: float) -> float:
    return 4 * p * p * p if p < 0.5 else 1 - math.pow(-2 * p + 2, 3) / 2


class Screencast:
    """Collects CDP screencast frames to disk with their wall-clock timestamps."""

    def __init__(self, ctx, page, out_dir: Path):
        self.ctx, self.page, self.dir = ctx, page, out_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.frames: list[tuple[float, str]] = []
        self.session = None
        self.active = False

    def _on_frame(self, f: dict[str, Any]) -> None:
        try:
            self.session.send("Page.screencastFrameAck", {"sessionId": f["sessionId"]})
        except Exception:
            pass
        if not self.active:
            return
        ts = float(f.get("metadata", {}).get("timestamp") or time.time())
        name = f"{len(self.frames):06d}.jpg"
        (self.dir / name).write_bytes(base64.b64decode(f["data"]))
        self.frames.append((ts, name))

    def start(self) -> None:
        if self.session is None:
            self.session = self.ctx.new_cdp_session(self.page)
            self.session.on("Page.screencastFrame", self._on_frame)
        self.active = True
        self.session.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 92, "maxWidth": VIEWPORT[0] * DPR, "maxHeight": VIEWPORT[1] * DPR, "everyNthFrame": 1},
        )

    def restart(self) -> None:
        """After a cross-document navigation the renderer may change; re-arm the screencast."""
        try:
            self.session.send("Page.stopScreencast")
        except Exception:
            pass
        try:
            self.session.detach()
        except Exception:
            pass
        self.session = None
        self.start()

    def stop(self) -> None:
        try:
            self.session.send("Page.stopScreencast")
        except Exception:
            pass
        self.active = False


class Driver:
    """Executes a normalised plan. mode='dry' is fast (probe), mode='record' is paced for video."""

    def __init__(self, page, ctx, mode: str = "record", capture_dir: Path | None = None):
        self.page, self.ctx, self.mode = page, ctx, mode
        self.dry = mode == "dry"
        self.cursor = (VIEWPORT[0] * 0.5, VIEWPORT[1] * 0.62)
        self.cursor_log: list[tuple[float, float, float]] = []
        self.clicks: list[dict[str, Any]] = []
        self.keys: list[float] = []
        self.intervals: list[dict[str, Any]] = []
        self.cast = Screencast(ctx, page, capture_dir) if (capture_dir and not self.dry) else None
        self.urls: list[tuple[float, str]] = []

    # ---- timing helpers -------------------------------------------------
    def sleep(self, s: float) -> None:
        self.page.wait_for_timeout(int((0.05 if self.dry else s) * 1000))

    def mark(self, kind: str, t0: float, t1: float, **extra: Any) -> None:
        if t1 > t0:
            self.intervals.append({"kind": kind, "t0": round(t0, 4), "t1": round(t1, 4), **extra})

    def log_cursor(self) -> None:
        self.cursor_log.append((round(time.time(), 4), round(self.cursor[0], 1), round(self.cursor[1], 1)))

    # ---- page prep --------------------------------------------------------
    def prepare(self) -> None:
        try:
            self.page.add_style_tag(content=_PAGE_CSS)
        except Exception:
            pass
        try:
            self.page.evaluate("document.fonts && document.fonts.ready")
        except Exception:
            pass

    def settle(self, max_idle: float = 4.0) -> None:
        try:
            self.page.wait_for_load_state("load", timeout=20000)
        except Exception:
            pass
        try:
            self.page.wait_for_load_state("networkidle", timeout=int(max_idle * 1000))
        except Exception:
            pass
        self.prepare()
        self.page.wait_for_timeout(250 if self.dry else 500)

    def dismiss_overlays(self) -> list[str]:
        """Close cookie / consent banners with their own polite buttons (never 'accept and pay')."""
        closed = []
        for label in ("Accept all", "Accept", "I agree", "Got it", "Allow all", "OK", "Dismiss", "Close"):
            try:
                loc = self.page.get_by_role("button", name=label, exact=True)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=1500)
                    closed.append(label)
                    self.page.wait_for_timeout(300)
            except Exception:
                continue
        return closed

    def inventory(self) -> dict[str, Any]:
        try:
            return self.page.evaluate(_INVENTORY_JS)
        except Exception:
            return {}

    # ---- locators ---------------------------------------------------------
    def candidates(self, target: str) -> list[tuple[str, Any]]:
        p, t = self.page, target.strip()
        hint = _ROLE_HINT_RE.match(t)
        if hint:  # models often write 'button: Docs' or 'heading: Pricing'; treat the prefix as a hint
            kind, rest = hint.group(1).lower(), hint.group(2).strip()
            role = {"menu": "button", "menuitem": "menuitem", "nav": "link"}.get(kind, kind)
            base = self.candidates(rest)
            first = []
            if role in ("button", "link", "tab", "heading", "menuitem", "checkbox", "option"):
                first.append((f"{role}-hinted", p.get_by_role(role, name=rest)))
            elif role == "label":
                first.append(("label", p.get_by_label(rest)))
            elif role == "placeholder":
                first.append(("placeholder", p.get_by_placeholder(rest)))
            return first + base
        if t.startswith("css="):
            return [("css", p.locator(t[4:]))]
        if t.startswith("text="):
            return [("text", p.get_by_text(t[5:], exact=False))]
        if t.startswith("label="):
            return [("label", p.get_by_label(t[6:]))]
        if t.startswith("placeholder="):
            return [("placeholder", p.get_by_placeholder(t[12:]))]
        if t.startswith("role="):
            role, _, name = t[5:].partition(":")
            return [("role", p.get_by_role(role.strip(), name=name.strip() or None))]
        exact = re.compile(r"^\s*" + re.escape(t) + r"\s*$", re.I)
        out = [
            ("link-exact", p.get_by_role("link", name=exact)),
            ("button-exact", p.get_by_role("button", name=exact)),
            ("tab-exact", p.get_by_role("tab", name=exact)),
            ("text-exact", p.get_by_text(exact)),
            ("heading", p.get_by_role("heading", name=t)),
            ("link", p.get_by_role("link", name=t)),
            ("button", p.get_by_role("button", name=t)),
            ("label", p.get_by_label(t)),
            ("placeholder", p.get_by_placeholder(t)),
            ("text", p.get_by_text(t)),
        ]
        if t[:1] in "#.[" or "/" in t[:2]:
            out.insert(0, ("css", p.locator(t)))
        return out

    def resolve(self, target: str, skip: int = 0, prefer: str | None = None, _short: bool = False) -> tuple[str, Any, int]:
        """Return (strategy, element, index) for the first visible match, skipping `skip` earlier hits."""
        seen = 0
        cands = self.candidates(target)
        if prefer:
            cands = sorted(cands, key=lambda c: 0 if c[0] == prefer else 1)
        for idx, (name, loc) in enumerate(cands):
            try:
                n = min(loc.count(), 8)
            except Exception:
                continue
            for i in range(n):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    bb = el.bounding_box()
                except Exception:
                    continue
                if not bb or bb["width"] < 3 or bb["height"] < 3:
                    continue
                if seen >= skip:
                    return name, el, idx
                seen += 1
                break  # one hit per strategy, so retries move to a different strategy
        words = target.split()
        if not _short and len(words) > 3 and not target.startswith(("css=", "role=", "text=", "label=", "placeholder=")):
            # trailing decorations ('TOGGLE ⌘ K', arrows) often are not part of the accessible name
            for cut in (1, 2, 3):
                if len(words) - cut < 2:
                    break
                try:
                    return self.resolve(" ".join(words[:-cut]), skip, prefer, _short=True)
                except StepFailed:
                    continue
        raise StepFailed(f"no visible element matches {target!r}")

    # ---- motion -------------------------------------------------------------
    def scroll_to(self, top: float, seconds: float, kind: str = "scroll") -> None:
        t0 = time.time()
        try:
            self.page.evaluate(_SMOOTH_SCROLL_JS, [top, int((0.05 if self.dry else seconds) * 1000)])
        except Exception:
            self.page.evaluate("(y) => window.scrollTo(0, y)", top)
        self.mark(kind, t0, time.time())

    def bring_into_view(self, el) -> dict[str, float]:
        bb = el.bounding_box()
        vh = VIEWPORT[1]
        if bb and (bb["y"] < 90 or bb["y"] + bb["height"] > vh - 140):
            y = self.page.evaluate("scrollY")
            self.scroll_to(y + bb["y"] - vh * 0.38, 0.9, kind="scroll")
            self.page.wait_for_timeout(120)
            bb = el.bounding_box()
        if not bb:
            raise StepFailed("element has no box after scrolling")
        return bb

    def move_cursor(self, x: float, y: float) -> None:
        x0, y0 = self.cursor
        dist = math.hypot(x - x0, y - y0)
        if self.dry:
            self.page.mouse.move(x, y)
            self.cursor = (x, y)
            return
        dur = max(0.38, min(0.95, 0.32 + dist / 1700))
        n = max(8, int(dur * 60))
        # gentle arc: control point offset perpendicular to the path
        nx, ny = (-(y - y0) / dist, (x - x0) / dist) if dist > 1 else (0.0, 0.0)
        bend = min(60.0, dist * 0.08) * (1 if (x0 + y0) % 2 < 1 else -1)
        cx, cy = (x0 + x) / 2 + nx * bend, (y0 + y) / 2 + ny * bend
        t_start = time.time()
        self.log_cursor()
        for i in range(1, n + 1):
            p = _ease(i / n)
            bx = (1 - p) ** 2 * x0 + 2 * (1 - p) * p * cx + p * p * x
            by = (1 - p) ** 2 * y0 + 2 * (1 - p) * p * cy + p * p * y
            self.page.mouse.move(bx, by)
            self.cursor = (bx, by)
            self.log_cursor()
            lag = t_start + dur * i / n - time.time()
            if lag > 0.004:
                # wait_for_timeout keeps Playwright's event loop pumping, so screencast
                # frames keep being acknowledged while the cursor travels
                self.page.wait_for_timeout(int(lag * 1000))
        self.cursor = (x, y)
        self.log_cursor()

    def point_in(self, bb: dict[str, float]) -> tuple[float, float]:
        x = bb["x"] + bb["width"] / 2
        y = bb["y"] + bb["height"] / 2
        return (max(6, min(VIEWPORT[0] - 6, x)), max(6, min(VIEWPORT[1] - 6, y)))

    # ---- actions --------------------------------------------------------------
    def wait_scroll_settled(self, limit: float = 3.0) -> None:
        """Wait until the page stops scrolling (native smooth scroll from anchor links)."""
        try:
            self.page.wait_for_function(
                """() => { const w = window; const y = Math.round(scrollY);
                  if (w.__srLastY !== y) { w.__srLastY = y; w.__srStill = performance.now(); return false; }
                  return performance.now() - w.__srStill > 280; }""",
                timeout=int(limit * 1000), polling=60)
        except Exception:
            pass

    def wait_quiet(self, limit: float = 4.0) -> None:
        """Wait for a client-rendered view to finish: no skeleton / busy placeholders and
        a short pause in DOM mutations. Bounded so a busy page cannot stall the recording."""
        try:
            self.page.wait_for_function(
                """() => {
                  const busy = document.querySelector('[aria-busy="true"],[class*="skeleton" i],[class*="animate-pulse"],[class*="shimmer" i]');
                  if (!window.__srQ) { window.__srQ = {n: 0, t: performance.now()};
                    new MutationObserver(() => { window.__srQ.t = performance.now(); })
                      .observe(document.body, {subtree: true, childList: true}); }
                  return !busy && performance.now() - window.__srQ.t > 450;
                }""",
                timeout=int(limit * 1000),
                polling=150,
            )
        except Exception:
            pass

    def _arm_change_probe(self) -> None:
        try:
            self.page.evaluate(
                """() => { window.__srDoc = 1; window.__srMut = 0; window.__srY = scrollY;
                   try { new MutationObserver(m => window.__srMut += m.length).observe(document.body,
                   {subtree: true, childList: true, attributes: true, characterData: true}); } catch (e) {} }"""
            )
        except Exception:
            pass

    def _changed(self, url_before: str) -> tuple[bool, bool, str]:
        """Return (changed, new_document, how)."""
        if self.page.url != url_before:
            try:
                same_doc = self.page.evaluate("window.__srDoc === 1")
            except Exception:
                same_doc = False
            return True, not same_doc, "url"
        try:
            r = self.page.evaluate("[window.__srDoc === 1, window.__srMut || 0, Math.abs(scrollY - (window.__srY || 0))]")
        except Exception:
            return True, True, "document"
        if not r[0]:
            return True, True, "document"
        if r[1] > 0:
            return True, False, "dom"
        if r[2] > 4:
            return True, False, "scroll"
        return False, False, ""

    def click(self, step: dict[str, Any], skip: int) -> dict[str, Any]:
        strategy, el, _ = self.resolve(step["target"], skip)
        try:
            is_heading = el.evaluate(
                "e => (/^H[1-6]$/.test(e.tagName) || e.getAttribute('role') === 'heading') && !e.closest('a,button,[role=button]')")
        except Exception:
            is_heading = False
        if is_heading:
            # 'click the Pricing heading' means 'take me to that section': scroll there instead
            y = self.page.evaluate("scrollY")
            bb = el.bounding_box()
            if self.cursor[1] < 140 and not self.dry:
                self.move_cursor(VIEWPORT[0] * 0.56, VIEWPORT[1] * 0.58)
            self.scroll_to(y + bb["y"] - VIEWPORT[1] * 0.18, step.get("duration", 1.4))
            return {"strategy": strategy, "changed": True, "how": "scroll-to-heading", "t_visual": round(time.time(), 4)}
        text = ""
        try:
            text = (el.inner_text(timeout=800) or "")[:80]
        except Exception:
            pass
        if text and not step.get("_allow_risky") and RISKY_RE.search(text):
            raise StepFailed(f"matched element reads {text!r}, which looks like an account or payment action; skipped")
        try:
            el.evaluate("e => { const a = e.closest('a'); if (a) a.removeAttribute('target'); }")
        except Exception:
            pass
        bb = self.bring_into_view(el)
        x, y = self.point_in(bb)
        self.move_cursor(x, y)
        self.sleep(0.16)
        url_before = self.page.url
        self._arm_change_probe()
        t_click = time.time()
        self.page.mouse.down()
        self.clicks.append({"t": round(t_click, 4), "x": round(x, 1), "y": round(y, 1)})
        self.page.wait_for_timeout(70)
        self.page.mouse.up()
        self.page.wait_for_timeout(450)
        changed, new_doc, how = self._changed(url_before)
        waited = 0
        while not changed and waited < 3000:
            # slow apps (streams, server actions) get up to ~3.5 s before we call it a miss;
            # a miss is retried with a different locator, so be patient to avoid double clicks
            self.page.wait_for_timeout(500)
            waited += 500
            changed, new_doc, how = self._changed(url_before)
        t_visual = None
        if how == "scroll":
            # anchor links scroll natively; the caption should land with the section
            self.wait_scroll_settled()
            t_visual = time.time()
        if new_doc:
            t_nav = time.time()
            self.settle()
            if self.cast:
                self.cast.restart()
            self.page.wait_for_timeout(150)
            self.wait_quiet(2.5)
            t_visual = time.time()
            self.mark("nav", t_click + 0.35, time.time())
            self.urls.append((time.time(), self.page.url))
        elif how == "url":
            # client-side route change: let the new view load, then cross-fade to it
            # instead of showing skeletons
            try:
                self.page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            try:
                fresh = not self.page.evaluate("window.__srDoc === 1")
            except Exception:
                fresh = True
            if fresh:  # it was a full navigation after all
                self.settle()
                if self.cast:
                    self.cast.restart()
            self.wait_quiet()
            self.page.wait_for_timeout(250)
            t_visual = time.time()
            self.mark("nav", t_click + 0.4, time.time())
            self.urls.append((time.time(), self.page.url))
        if (new_doc or how == "url") and not self.dry:
            # the old click point now sits on whatever the new page put there (often a
            # header button); drift the cursor back toward the content
            self.move_cursor(VIEWPORT[0] * 0.55, VIEWPORT[1] * 0.5)
        out = {"strategy": strategy, "focus": [x, y], "changed": changed, "how": how, "text": text}
        if t_visual:
            out["t_visual"] = round(t_visual, 4)
        return out

    def do_step(self, step: dict[str, Any], skip: int = 0) -> dict[str, Any]:
        a = step["action"]
        info: dict[str, Any] = {}
        if a == "navigate":
            t0 = time.time()
            self.page.goto(step["url"], wait_until="domcontentloaded", timeout=30000)
            self.settle()
            if self.cast:
                self.cast.restart()
            self.page.wait_for_timeout(150)
            self.mark("nav", t0, time.time())
            self.urls.append((time.time(), self.page.url))
            info["how"] = "navigate"
        elif a == "click":
            info = self.click(step, skip)
            if not info["changed"]:
                raise StepFailed(f"clicked {step['target']!r} but nothing on the page changed")
        elif a == "hover":
            strategy, el, _ = self.resolve(step["target"], skip)
            bb = self.bring_into_view(el)
            x, y = self.point_in(bb)
            self.move_cursor(x, y)
            info = {"strategy": strategy, "focus": [x, y]}
        elif a == "type":
            strategy, el, _ = self.resolve(step["target"], skip)
            bb = self.bring_into_view(el)
            x, y = self.point_in(bb)
            self.move_cursor(x, y)
            self.sleep(0.12)
            t = time.time()
            self.page.mouse.click(x, y)
            self.clicks.append({"t": round(t, 4), "x": round(x, 1), "y": round(y, 1)})
            self.sleep(0.25)
            t0 = time.time()
            for ch in step["value"]:
                self.page.keyboard.type(ch)
                self.keys.append(round(time.time(), 4))
                if not self.dry:
                    self.page.wait_for_timeout(int(random.uniform(45, 85)))
            self.mark("type", t0, time.time())
            try:
                val = el.input_value(timeout=800)
                if step["value"] not in val:
                    raise StepFailed(f"typed into {step['target']!r} but the field reads {val!r}")
            except StepFailed:
                raise
            except Exception:
                pass
            info = {"strategy": strategy, "focus": [x, y]}
        elif a == "scroll":
            # park the cursor away from hover menus before the page moves
            if self.cursor[1] < 140 and not self.dry:
                self.move_cursor(VIEWPORT[0] * 0.56, VIEWPORT[1] * 0.58)
            y = self.page.evaluate("scrollY")
            if step.get("target"):
                strategy, el, _ = self.resolve(step["target"], skip, prefer="heading")
                bb = el.bounding_box()
                top = y + bb["y"] - VIEWPORT[1] * 0.18
                info["strategy"] = strategy
            elif step.get("to") == "top":
                top = 0
            elif step.get("to") == "bottom":
                top = self.page.evaluate("document.documentElement.scrollHeight")
            else:
                top = y + step.get("amount", 0.75) * VIEWPORT[1]
            self.scroll_to(top, step.get("duration", 1.4))
            info["t_visual"] = round(time.time(), 4)
            after = self.page.evaluate("scrollY")
            info["scrolled"] = round(after - y)
            if abs(after - y) < 3 and step.get("to") != "top":
                info["note"] = "page did not move (already at the end?)"
        elif a == "press":
            self.page.keyboard.press(step["key"])
            self.keys.append(round(time.time(), 4))
            self.page.wait_for_timeout(500)
        elif a == "wait" and step.get("until"):
            t0 = time.time()
            loc = self.page.get_by_text(step["until"])
            try:
                loc.first.wait_for(state="visible", timeout=int(step.get("timeout", 60) * 1000))
            except Exception as exc:
                raise StepFailed(f"{step['until']!r} did not appear within {step.get('timeout', 60):.0f}s") from exc
            if step.get("timelapse", True):
                self.mark("fast", t0, time.time())
            info["waited"] = round(time.time() - t0, 2)
        return info

    def run_plan(self, plan: dict[str, Any], on_progress: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
        results = []
        for i, step in enumerate(plan["steps"]):
            step = {**step, "_allow_risky": plan.get("allow_risky", False)}
            res: dict[str, Any] = {"index": i, "action": step["action"], "caption": step["caption"], "attempts": 0}
            res["t_start"] = round(time.time(), 4)
            last_err = None
            attempts = 3 if step["action"] in ("click", "hover", "type") else 1
            for attempt in range(attempts):
                res["attempts"] = attempt + 1
                try:
                    info = self.do_step(step, skip=attempt)
                    res.update(info)
                    res["ok"] = True
                    last_err = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    self.page.wait_for_timeout(200)
            if last_err is not None:
                res["ok"] = False
                res["error"] = str(last_err).split("\n")[0][:240]
            res["t_action_end"] = round(time.time(), 4)
            res["t_caption"] = res.pop("t_visual", res["t_start"])
            t_d = time.time()
            self.sleep(step["dwell"] if res["ok"] else 0.2)
            self.mark("dwell", t_d, time.time(), step=i)
            res["t_end"] = round(time.time(), 4)
            res["url_after"] = self.page.url
            results.append(res)
            if on_progress:
                on_progress(f"step {i + 1}/{len(plan['steps'])} {'ok' if res['ok'] else 'skipped'}")
        return results


def _new_page(p):
    browser = _launch(p)
    ctx = browser.new_context(
        viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]},
        device_scale_factor=DPR,
        user_agent=UA,
        locale="en-US",
        color_scheme="light",
    )
    page = ctx.new_page()
    page.on("popup", lambda pop: pop.close())
    return browser, ctx, page


def probe(plan: dict[str, Any]) -> dict[str, Any]:
    """Load the start page, collect an inventory and dry-run the steps (fast, no capture)."""
    from playwright.sync_api import sync_playwright

    def work():
        with sync_playwright() as p:
            browser, ctx, page = _new_page(p)
            try:
                page.goto(plan["url"], wait_until="domcontentloaded", timeout=30000)
                d = Driver(page, ctx, mode="dry")
                d.settle()
                closed = d.dismiss_overlays()
                inv = d.inventory()
                results = d.run_plan(plan) if plan.get("steps") else []
                return {"inventory": inv, "overlays_closed": closed, "results": results, "final_url": page.url}
            finally:
                browser.close()

    return run_isolated(work, timeout=300)


def record(plan: dict[str, Any], capture_dir: Path, on_progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Record the plan. Writes frames + events.json into capture_dir and returns the events dict."""
    from playwright.sync_api import sync_playwright

    capture_dir = Path(capture_dir)
    frames_dir = capture_dir / "frames"
    if frames_dir.exists():
        for f in frames_dir.glob("*.jpg"):
            f.unlink()

    def work():
        with sync_playwright() as p:
            browser, ctx, page = _new_page(p)
            try:
                page.goto(plan["url"], wait_until="domcontentloaded", timeout=30000)
                d = Driver(page, ctx, mode="record", capture_dir=frames_dir)
                d.settle(max_idle=6)
                d.dismiss_overlays()
                page.mouse.move(*d.cursor)
                page.wait_for_timeout(600)
                meta = d.inventory()
                d.cast.start()
                t0 = time.time()
                d.log_cursor()
                d.urls.append((t0, page.url))
                page.wait_for_timeout(900)
                d.mark("dwell", t0, time.time(), step=-1)
                results = d.run_plan(plan, on_progress)
                t_hold = time.time()
                page.wait_for_timeout(700)
                d.mark("dwell", t_hold, time.time(), step=len(results))
                t1 = time.time()
                d.log_cursor()
                d.cast.stop()
                events = {
                    "version": 1,
                    "viewport": list(VIEWPORT),
                    "dpr": DPR,
                    "t0": round(t0, 4),
                    "t1": round(t1, 4),
                    "frames": [[round(ts, 4), name] for ts, name in d.cast.frames],
                    "cursor": d.cursor_log,
                    "clicks": d.clicks,
                    "keys": d.keys,
                    "intervals": d.intervals,
                    "urls": d.urls,
                    "steps": results,
                    "page": {k: meta.get(k) for k in ("title", "description", "site_name", "theme_color")},
                }
                (capture_dir / "events.json").write_text(json.dumps(events))
                return events
            finally:
                browser.close()

    return run_isolated(work, timeout=900)
