"""draft_post: suggested X post text. Never posts anything."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .plan import strip_emoji

LIMIT = 280


def _fit(text: str, limit: int = LIMIT) -> str:
    text = "\n".join(strip_emoji(line) for line in text.split("\n"))
    if len(text) <= limit:
        return text
    return text[: limit - 3].rsplit(" ", 1)[0] + "..."


def draft(job: dict[str, Any], tone: str = "launch", mention: str | None = None) -> dict[str, Any]:
    plan = job.get("plan") or {}
    title = plan.get("title") or urlparse(job.get("url", "")).netloc
    tagline = (plan.get("tagline") or "").rstrip(".")
    url = plan.get("cta_url") or plan.get("url") or job.get("url", "")
    steps = [s for s in (job.get("recording") or {}).get("steps", []) if s.get("ok") and s.get("caption")]
    caps: list[str] = []
    for s in steps:
        c = s["caption"].rstrip(".")
        if c.lower() != tagline.lower() and c.lower() not in (x.lower() for x in caps):
            caps.append(c)
    caps = caps[:4]
    tag = f" {mention}" if mention else ""
    def compose(n: int) -> str:
        if tone == "short":
            return _fit(f"{title}{': ' + tagline if tagline else ''}.\n\n{url}{tag}")
        lines = [f"{title}{' - ' + tagline if tagline else ''}."]
        if n:
            lines.append("In this walkthrough:\n" + "\n".join(f"- {c}" for c in caps[:n]))
        lines.append(f"Try it: {url}{tag}")
        return "\n\n".join(lines)

    n = len(caps)
    main = compose(n)
    while len(main) > LIMIT and n > 0:
        n -= 1
        main = compose(n)
    main = _fit(main)
    alt = _fit(f"Screen recording of {urlparse(url).netloc}: " + "; ".join(caps), 1000)
    return {
        "post": main,
        "characters": len(main),
        "alt_text": alt,
        "video": (job.get("render") or {}).get("main"),
        "note": "Draft only. Ship Reel never posts; share it yourself after review.",
    }
