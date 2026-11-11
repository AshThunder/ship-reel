"""Hermes lifecycle hooks.

``pre_tool_call`` returns ``{"action": "approve", "message": ...}`` when a Ship Reel tool is about to
drive the browser through a plan with risky clicks (pay, sign in, send, submit, delete, ...). Hermes
then shows the user its approval prompt and only runs the tool if they say yes; a denied or failed
prompt blocks the call. Safe plans return None and run normally. The model has no way to skip this.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .engine import plan as planmod, store

# Tools that run the browser through a plan's steps (plan_reel dry-runs them; fix_reel may re-record).
BROWSER_TOOLS = ("plan_reel", "record_reel", "fix_reel")


def _plan_steps(tool_name: str, args: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    if tool_name == "plan_reel":
        steps = []
        for i, raw in enumerate(args.get("steps") or []):
            try:
                steps.append(planmod.normalize_step(raw, i)[0])
            except planmod.PlanError:
                continue  # the tool itself will reject the plan
        return str(args.get("url") or ""), steps
    job = store.load(str(args.get("job_id") or ""))
    plan = job.get("plan") or {}
    return str(plan.get("url") or job.get("url") or ""), list(plan.get("steps") or [])


def pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> dict[str, str] | None:
    if tool_name not in BROWSER_TOOLS or not isinstance(args, dict):
        return None
    try:
        url, steps = _plan_steps(tool_name, args)
    except Exception:  # noqa: BLE001 - unknown job etc.: the tool reports the error itself
        return None
    risky = planmod.risky_steps(steps)
    if not risky:
        return None
    host = urlparse(url if "://" in url else "https://" + url).hostname or url or "the site"
    shown = "; ".join(risky[:6]) + (f"; and {len(risky) - 6} more" if len(risky) > 6 else "")
    verb = {"plan_reel": "dry-run", "record_reel": "record", "fix_reel": "re-record"}[tool_name]
    return {
        "action": "approve",
        "message": (f"Ship Reel wants to {verb} a walkthrough of {host} that clicks controls which look like "
                    f"payment, sign-in, send, submit or delete actions: {shown}. Approve only if you asked for this."),
        "rule_key": f"ship_reel:risky:{host}",
    }
