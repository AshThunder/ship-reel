"""fix_reel: map verification failures to targeted repairs, bounded retries."""
from __future__ import annotations

from typing import Any


def plan_fixes(report: dict[str, Any], style: dict[str, Any], rerecorded: bool) -> tuple[dict[str, Any], list[str], bool]:
    """Return (style_changes, notes, needs_rerecord). Pure, unit-tested."""
    changes: dict[str, Any] = {}
    notes: list[str] = []
    rerecord = False
    issues = report.get("failures", []) + report.get("warnings", [])
    names = {c["name"] for c in issues}
    if "duration" in names:
        changes["max_seconds"] = min(float(style.get("max_seconds", 88)), 84.0)
        changes["dwell_rate"] = round(min(2.5, float(style.get("dwell_rate", 1.0)) * 1.25), 3)
        notes.append("tightened dwell and lowered the time budget to fit 90s")
    if "frozen video" in names:
        changes["dwell_rate"] = round(min(2.5, float(changes.get("dwell_rate", style.get("dwell_rate", 1.0))) * 1.3), 3)
        notes.append("sped up static holds")
    if "file size" in names:
        changes["crf"] = min(28, int(style.get("crf", 17)) + 4)
        notes.append("raised CRF to shrink the file")
    if names & {"codec", "pixel format", "faststart", "frame rate", "resolution", "audio stream", "exists"}:
        changes.setdefault("crf", int(style.get("crf", 17)))
        notes.append("re-encoding with the standard H.264 / yuv420p / faststart profile")
    if "audio peak" in names:
        changes["sound"] = "clicks"
        changes["music"] = style.get("music")
        notes.append("re-mixing audio through the limiter")
    if "black frames" in names and not rerecorded:
        rerecord = True
        notes.append("black frames usually mean the page was still loading; re-recording")
    if "steps" in names and not rerecorded:
        rerecord = True
        notes.append("re-recording to retry skipped steps with alternate locators")
    if "captions" in names:
        changes["dwell_rate"] = round(max(0.7, float(changes.get("dwell_rate", style.get("dwell_rate", 1.0))) * 0.85), 3)
        notes.append("gave captions more time on screen")
    return changes, notes, rerecord
