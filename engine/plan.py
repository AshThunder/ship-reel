"""Shot-list validation and normalisation (pure functions, no browser)."""
from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

ACTIONS = ("navigate", "click", "type", "scroll", "hover", "wait", "press")
ZOOM_DEFAULT = {"click": True, "type": True, "hover": True}
MAX_STEPS = 12
MIN_STEPS = 1
CAPTION_MAX = 72
TARGET_REEL_SECONDS = 80.0

# Clicks whose target text matches this are refused unless allow_risky is set:
# Ship Reel records public, read-only walkthroughs and must never pay, sign up,
# submit, delete or post on the user's behalf.
RISKY_RE = re.compile(
    r"\b(buy|pay|purchase|checkout|check out|order now|subscribe|sign ?up|register|"
    r"create account|log ?in|sign ?in|submit|send|delete|remove|confirm|donate|"
    r"transfer|withdraw|deposit|connect wallet|approve|publish|post now)\b",
    re.I,
)

_ALIASES = {
    "goto": "navigate", "open": "navigate", "visit": "navigate", "go": "navigate",
    "tap": "click", "press_button": "click", "select": "click",
    "fill": "type", "input": "type", "enter": "type",
    "scroll_to": "scroll", "scrolldown": "scroll", "scroll_down": "scroll",
    "pause": "wait", "dwell": "wait", "sleep": "wait",
    "key": "press", "keypress": "press", "mouseover": "hover",
}


class PlanError(ValueError):
    pass


def strip_emoji(text: str) -> str:
    """Remove emoji / pictographs and collapse whitespace. Captions stay plain text."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        cp = ord(ch)
        if cat == "So" or 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or cp in (0xFE0F, 0x200D):
            continue
        out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def clean_caption(text: str | None) -> str:
    t = strip_emoji(str(text or ""))
    t = t.strip(" \"'")
    if len(t) > CAPTION_MAX:
        cut = t[:CAPTION_MAX].rsplit(" ", 1)[0].rstrip(",;:-")
        t = cut
    return t


def check_url(url: str) -> str:
    url = (url or "").strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!//)", url) and not re.match(r"^[^/:]+:\d+", url):
        raise PlanError(f"url must be an http(s) address, got {url!r}")
    if url and "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise PlanError(f"url must be an http(s) address, got {url!r}")
    return url


def _num(v: Any, default: float, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


def _auto_caption(step: dict[str, Any]) -> str:
    a, tgt = step["action"], step.get("target") or ""
    label = re.sub(r"^(css|text|role)=", "", tgt).split(":")[-1].strip()
    if a == "click":
        return f"Open {label}" if label else "Click"
    if a == "type":
        return f"Type \"{step.get('value', '')}\""
    if a == "scroll":
        return "Scroll through the page"
    if a == "navigate":
        return "Open " + urlparse(step.get("url", "")).netloc
    return ""


def normalize_step(raw: Any, index: int, allow_risky: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Return (step, warnings). Raises PlanError for unusable steps."""
    warnings: list[str] = []
    if isinstance(raw, str):
        raw = {"action": "click", "target": raw}
    if not isinstance(raw, dict):
        raise PlanError(f"step {index + 1}: must be an object")
    action = str(raw.get("action") or raw.get("type_") or "").strip().lower().replace(" ", "_")
    action = _ALIASES.get(action, action)
    if action not in ACTIONS:
        raise PlanError(f"step {index + 1}: unknown action {action!r}; use one of {', '.join(ACTIONS)}")
    target = raw.get("target") or raw.get("selector") or raw.get("text") or raw.get("element")
    target = str(target).strip() if target else ""
    step: dict[str, Any] = {"action": action}

    if action == "navigate":
        step["url"] = check_url(str(raw.get("url") or target))
    elif action in ("click", "hover", "type"):
        if not target:
            raise PlanError(f"step {index + 1}: {action} needs a target (visible text, role=..., or css=...)")
        step["target"] = target
        if action == "type":
            value = raw.get("value", raw.get("text_to_type", raw.get("input")))
            if value is None or str(value) == "":
                raise PlanError(f"step {index + 1}: type needs a value")
            step["value"] = str(value)[:200]
        if action == "click" and not allow_risky and RISKY_RE.search(target):
            raise PlanError(
                f"step {index + 1}: refusing to click {target!r}; it looks like a payment, sign-up, "
                "submit or account action. Ship Reel records read-only walkthroughs. "
                "Pass allow_risky=true only if the user explicitly asked for it."
            )
    elif action == "scroll":
        if target:
            step["target"] = target
        to = str(raw.get("to") or "").lower()
        if to in ("top", "bottom"):
            step["to"] = to
        elif not target:
            step["amount"] = _num(raw.get("amount"), 0.75, -5, 5)  # viewports
    elif action == "press":
        key = str(raw.get("key") or raw.get("value") or target or "").strip()
        if not key:
            raise PlanError(f"step {index + 1}: press needs a key")
        if key.lower() == "enter" and not allow_risky:
            warnings.append(f"step {index + 1}: pressing Enter may submit a form; make sure it is a search box")
        step["key"] = key
    elif action == "wait":
        until = str(raw.get("until") or raw.get("wait_for") or target or "").strip()
        if until:
            step["until"] = until[:120]
            step["timeout"] = _num(raw.get("timeout"), 60, 3, 150)
            step["timelapse"] = bool(raw.get("timelapse", True))

    step["caption"] = clean_caption(raw.get("caption") or raw.get("label") or raw.get("narration"))
    if not step["caption"]:
        step["caption"] = clean_caption(_auto_caption(step))
        if action not in ("wait", "press", "hover"):
            warnings.append(f"step {index + 1}: no caption given, generated {step['caption']!r}")
    step["dwell"] = _num(raw.get("dwell", raw.get("hold", raw.get("seconds"))), 1.6 if action != "wait" else 2.0, 0.3, 6.0)
    zoom = raw.get("zoom")
    step["zoom"] = bool(ZOOM_DEFAULT.get(action, False) if zoom is None else zoom)
    step["zoom_level"] = _num(raw.get("zoom_level"), 1.6, 1.0, 2.2)
    if action == "scroll":
        step["duration"] = _num(raw.get("duration"), 1.4, 0.4, 4.0)
    return step, warnings


def estimate_seconds(steps: list[dict[str, Any]]) -> float:
    """Rough final-reel length: title + end cards + per-action time + dwell."""
    per = {"navigate": 0.6, "click": 1.1, "type": 1.0, "scroll": 0.0, "hover": 0.9, "wait": 0.0, "press": 0.4}
    total = 2.6 + 3.2
    for s in steps:
        total += per[s["action"]] + s["dwell"]
        if s.get("until"):
            total += 5.0
        if s["action"] == "scroll":
            total += s.get("duration", 1.4)
        if s["action"] == "type":
            total += 0.06 * len(s.get("value", ""))
    return round(total, 1)


def normalize_plan(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate a whole plan. Returns (plan, warnings); raises PlanError when unusable."""
    url = check_url(str(raw.get("url") or ""))
    steps_in = raw.get("steps") or []
    if not isinstance(steps_in, list):
        raise PlanError("steps must be a list")
    allow_risky = bool(raw.get("allow_risky"))
    steps, warnings = [], []
    for i, s in enumerate(steps_in):
        step, w = normalize_step(s, i, allow_risky)
        steps.append(step)
        warnings += w
    # Leading navigate to the start URL is implicit; drop a duplicate.
    if steps and steps[0]["action"] == "navigate" and steps[0]["url"].rstrip("/") == url.rstrip("/"):
        steps = steps[1:]
    if len(steps) < MIN_STEPS:
        raise PlanError("plan needs at least one step after opening the page")
    if len(steps) > MAX_STEPS:
        raise PlanError(f"plan has {len(steps)} steps; keep it to {MAX_STEPS} or fewer (5-8 reads best)")
    if not 4 <= len(steps) <= 9:
        warnings.append(f"{len(steps)} steps; 5-8 usually makes the tightest reel")
    title = clean_caption(raw.get("title")) or urlparse(url).netloc.replace("www.", "")
    tagline = clean_caption(raw.get("tagline") or raw.get("brief") or "")
    est = estimate_seconds(steps)
    if est > TARGET_REEL_SECONDS:
        warnings.append(f"estimated {est}s; aim under {TARGET_REEL_SECONDS:.0f}s (trim dwell or steps). edit_reel will speed up to fit 90s.")
    plan = {
        "url": url,
        "title": title,
        "tagline": tagline,
        "steps": steps,
        "allow_risky": allow_risky,
        "estimated_seconds": est,
    }
    return plan, warnings
