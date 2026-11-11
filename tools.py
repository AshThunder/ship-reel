"""Tool handlers. Each returns a JSON string and never raises."""
from __future__ import annotations

import json
import re
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from .engine import editor, fix as fixmod, plan as planmod, post, store, verify as verifymod


def _ok(**data: Any) -> str:
    return json.dumps({"ok": True, **data}, default=str)


def _err(msg: str, **data: Any) -> str:
    return json.dumps({"ok": False, "error": msg, **data}, default=str)


def _guard(fn: Callable[..., str]) -> Callable[..., str]:
    def wrapper(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
        try:
            return fn(args or {}, **kwargs)
        except (planmod.PlanError, FileNotFoundError, ValueError) as exc:
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001 - tools must not raise
            return _err(f"{type(exc).__name__}: {exc}", trace=traceback.format_exc(limit=3)[-600:])
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _browser():
    from .engine import browser  # imported lazily: Playwright is only needed for plan/record
    return browser


# ---------------------------------------------------------------------------
def auto_plan(url: str, inventory: dict[str, Any], brief: str) -> list[dict[str, Any]]:
    """A sensible default shot list from the page inventory. The model is expected to refine it."""
    steps: list[dict[str, Any]] = []
    heads = [h for h in inventory.get("headings", []) if h.get("text")]
    acts = inventory.get("actions", [])
    h1 = next((h["text"] for h in heads if h["tag"] == "h1"), inventory.get("title", ""))
    steps.append({"action": "wait", "caption": (h1 or brief)[:70], "dwell": 1.8})
    later = [h for h in heads if h["y"] > 500][:3]
    for h in later[:2]:
        steps.append({"action": "scroll", "target": f"text={h['text'][:60]}", "caption": h["text"][:70], "dwell": 1.6})
    host = url.split("/")[2] if "//" in url else url
    cta = None
    for a in acts:
        t = a.get("text", "")
        href = a.get("href") or ""
        if not t or len(t) > 40 or planmod.RISKY_RE.search(t):
            continue
        if a["kind"] in ("link", "button") and a.get("y", 0) < 1400 and (href.startswith("/") or host in href or a["kind"] == "button"):
            cta = a
            break
    if cta:
        steps.append({"action": "click", "target": cta["text"], "caption": f"Open {cta['text'].title() if cta['text'].isupper() else cta['text']}", "dwell": 1.8})
        steps.append({"action": "scroll", "amount": 0.8, "caption": "A closer look", "dwell": 1.4})
    else:
        steps.append({"action": "scroll", "amount": 1.2, "caption": "More of the product", "dwell": 1.4})
    return steps


@_guard
def plan_reel(args: dict[str, Any], **kwargs: Any) -> str:
    url = planmod.check_url(str(args.get("url") or ""))
    brief = str(args.get("brief") or "").strip()
    job_id = args.get("job_id")
    job = store.load(job_id) if job_id else store.create(url, brief)
    b = _browser()
    raw = {
        "url": url,
        "steps": args.get("steps") or [],
        "title": args.get("title"),
        "tagline": args.get("tagline") or brief,
        "allow_risky": bool(args.get("allow_risky")),
    }
    auto = False
    if not raw["steps"]:
        pr = b.probe({"url": url, "steps": []})
        raw["steps"] = auto_plan(url, pr["inventory"], brief)
        auto = True
        if not raw.get("title"):
            inv = pr["inventory"]
            raw["title"] = inv.get("site_name") or (inv.get("title") or "").split("|")[0].split(" - ")[0].strip()
    plan, warnings = planmod.normalize_plan(raw)
    if args.get("cta_url"):
        plan["cta_url"] = planmod.check_url(str(args["cta_url"]))
    do_dry = args.get("dry_run", args.get("probe", True)) is not False
    probe = b.probe(plan) if do_dry else b.probe({"url": url, "steps": []})
    resolved = []
    for r in probe.get("results", []):
        resolved.append({k: r.get(k) for k in ("index", "action", "ok", "strategy", "error", "url_after", "note")})
    failed = [r for r in resolved if not r["ok"]]
    mech = [i + 1 for i, st in enumerate(plan["steps"]) if planmod.mechanical_caption(st.get("caption", ""))]
    job.update({"url": url, "brief": brief, "plan": plan, "status": "planned", "probe": resolved})
    store.log(job, "planned", steps=len(plan["steps"]), auto=auto, failed=len(failed))
    store.save(job)
    inv = probe.get("inventory") or {}
    compact_steps = [{k: v for k, v in st.items() if k in ("action", "target", "url", "value", "key", "amount", "to", "until", "caption")}
                     for st in plan["steps"]]
    out: dict[str, Any] = dict(
        job_id=job["id"],
        auto_generated=auto,
        title=plan.get("title"),
        tagline=plan.get("tagline"),
        steps=compact_steps,
        estimated_seconds=plan.get("estimated_seconds"),
        dry_run=f"{len(resolved) - len(failed)}/{len(resolved)} steps resolved on the live page",
        warnings=warnings + [_suggest(f"step {r['index'] + 1} did not work in the dry run: {r['error']}", plan["steps"][r["index"]], inv)
                             for r in failed],
        caption_advice=(f"Captions for steps {mech} describe the cursor, not the product. Rewrite them as what "
                        "the viewer learns (e.g. 'Every Hermes surface, one menu away' instead of 'Hover over "
                        "HERMES menu') and call plan_reel again with this job_id.") if mech else None,
        next=("Fix the failed steps and call plan_reel again with job_id" if failed else
              "Rewrite the flagged captions and call plan_reel again with job_id" if mech else
              "Call record_reel with this job_id (refine steps first if the auto plan is generic)"),
    )
    if auto or failed:  # the model needs the page's vocabulary only when it has to write or repair steps
        out["page"] = {
            "title": inv.get("title"),
            "headings": [h["text"][:60] for h in inv.get("headings", [])][:8],
            "clickable": [a["text"][:40] for a in inv.get("actions", []) if a.get("text")][:20],
        }
    return _ok(**{k: v for k, v in out.items() if v not in (None, [], "")})


def _suggest(msg: str, step: dict[str, Any], inv: dict[str, Any]) -> str:
    """Append the closest visible labels so the model can repair a target in one go."""
    import difflib
    tgt = re.sub(r"^\w+\s*[:=]\s*", "", str(step.get("target") or "")).strip()
    if not tgt:
        return msg
    labels = [a["text"].strip() for a in inv.get("actions", []) if a.get("text")]
    labels += [h["text"].strip() for h in inv.get("headings", []) if h.get("text")]
    low = {lab.lower(): lab for lab in labels if 0 < len(lab) <= 80}
    near = difflib.get_close_matches(tgt.lower(), list(low), n=3, cutoff=0.45)
    return msg + (f"; closest visible labels: {[low[n] for n in near]}" if near else "")


@_guard
def record_reel(args: dict[str, Any], **kwargs: Any) -> str:
    job = store.load(str(args.get("job_id") or ""))
    if not job.get("plan"):
        return _err("job has no plan; call plan_reel first")
    jd = store.job_path(job["id"])
    t = time.time()
    events = _browser().record(job["plan"], jd / "capture")
    steps = events["steps"]
    job["recording"] = {
        "frames": len(events["frames"]),
        "seconds": round(events["t1"] - events["t0"], 2),
        "steps": [{k: s.get(k) for k in ("index", "action", "caption", "ok", "error", "strategy", "attempts", "how", "url_after")} for s in steps],
        "recorded_at": time.time(),
    }
    ok = sum(1 for s in steps if s.get("ok"))
    job["status"] = "recorded"
    store.log(job, "recorded", ok=ok, total=len(steps), frames=len(events["frames"]), took=round(time.time() - t, 1))
    store.save(job)
    return _ok(job_id=job["id"], steps_ok=ok, steps_total=len(steps), frames=len(events["frames"]),
               raw_seconds=job["recording"]["seconds"],
               skipped=[{"step": st["index"] + 1, "error": st.get("error")} for st in steps if not st.get("ok")] or None,
               next="Call edit_reel" + ("" if ok == len(steps) else " (skipped steps are cut from the reel; or fix them and re-plan)"))


@_guard
def edit_reel(args: dict[str, Any], **kwargs: Any) -> str:
    job = store.load(str(args.get("job_id") or ""))
    if not job.get("recording"):
        return _err("job has not been recorded; call record_reel first")
    style = args.get("style") or {}
    for k in ("format", "vertical", "fps", "zoom", "background", "accent", "sound", "music", "music_volume", "voice", "voice_speed", "speed"):
        if k in args and args[k] is not None:
            style[k] = args[k]
    res = editor.edit(job, store.job_path(job["id"]), style)
    job["render"] = res
    job["style"] = res["style"]
    job["status"] = "edited"
    store.log(job, "edited", main=res["main"], took=res["render_seconds"])
    store.save(job)
    outs = {fmt: {"path": o["path"], "seconds": round(o["duration"], 1), "size": f"{o['size'][0]}x{o['size'][1]}",
                  "narrated": o.get("narrated", False)} for fmt, o in res["outputs"].items()}
    return _ok(job_id=job["id"], video=res["main"], outputs=outs, theme=res["theme"],
               audio=res.get("audio"), render_seconds=res["render_seconds"], next="Call verify_reel")


@_guard
def verify_reel(args: dict[str, Any], **kwargs: Any) -> str:
    job = store.load(str(args.get("job_id") or ""))
    rep = verifymod.verify(job)
    job["verify"] = {k: rep.get(k) for k in ("pass", "summary", "failures", "warnings")}
    job["status"] = "verified" if rep["pass"] else "needs_fix"
    store.log(job, "verified", passed=rep["pass"], summary=rep.get("summary"))
    store.save(job)
    def brief(items):
        return [f"{i.get('format', '')} {i['name']}: {i.get('detail', '')}".strip() for i in items]
    n_checks = sum(len(v) for v in (rep.get("outputs") or {}).values())
    return _ok(job_id=job["id"], passed=rep["pass"], summary=rep.get("summary"), checks_run=n_checks,
               failures=brief(rep.get("failures", [])), warnings=brief(rep.get("warnings", [])),
               next="Call draft_post" if rep["pass"] else "Call fix_reel")


@_guard
def fix_reel(args: dict[str, Any], **kwargs: Any) -> str:
    job = store.load(str(args.get("job_id") or ""))
    attempts = max(1, min(3, int(args.get("max_attempts") or 3)))
    jd = store.job_path(job["id"])
    log = []
    rerecorded = False
    rep = verifymod.verify(job)
    for i in range(attempts):
        if rep["pass"] and not rep.get("warnings"):
            break
        changes, notes, rerecord = fixmod.plan_fixes(rep, job.get("style") or {}, rerecorded)
        if not changes and not rerecord:
            if rep["pass"]:
                break
            log.append({"attempt": i + 1, "notes": ["no automatic fix for: " + ", ".join(c["name"] for c in rep["failures"])]})
            break
        if rerecord:
            events = _browser().record(job["plan"], jd / "capture")
            job["recording"]["steps"] = [{k: s.get(k) for k in ("index", "action", "caption", "ok", "error", "strategy", "attempts", "how", "url_after")} for s in events["steps"]]
            job["recording"]["frames"] = len(events["frames"])
            rerecorded = True
        style = {**(job.get("style") or {}), **changes}
        job["render"] = editor.edit(job, jd, style)
        job["style"] = job["render"]["style"]
        rep = verifymod.verify(job)
        log.append({"attempt": i + 1, "changes": changes, "rerecorded": rerecord, "notes": notes, "result": rep.get("summary")})
        if rep["pass"] and not [w for w in rep.get("warnings", []) if w["name"] != "steps"]:
            break
    job["verify"] = {k: rep.get(k) for k in ("pass", "summary", "failures", "warnings")}
    job["status"] = "verified" if rep["pass"] else "needs_fix"
    store.log(job, "fixed", attempts=len(log), passed=rep["pass"])
    store.save(job)
    return _ok(job_id=job["id"], passed=rep["pass"], summary=rep.get("summary"), attempts=log,
               failures=rep.get("failures", []), warnings=rep.get("warnings", []), video=(job.get("render") or {}).get("main"))


@_guard
def list_reels(args: dict[str, Any], **kwargs: Any) -> str:
    return _ok(reels=store.list_jobs(int(args.get("limit") or 20)), data_dir=str(store.data_dir()))


@_guard
def reel_status(args: dict[str, Any], **kwargs: Any) -> str:
    job = store.load(str(args.get("job_id") or ""))
    return _ok(job_id=job["id"], status=job.get("status"), url=job.get("url"), plan=job.get("plan"),
               recording=job.get("recording"), render={k: (job.get("render") or {}).get(k) for k in ("main", "outputs", "theme")},
               verify=job.get("verify"), history=job.get("history", [])[-12:], dir=str(store.job_path(job["id"])))


@_guard
def draft_post(args: dict[str, Any], **kwargs: Any) -> str:
    job = store.load(str(args.get("job_id") or ""))
    d = post.draft(job, str(args.get("tone") or "launch"), args.get("mention"))
    job["post_draft"] = d["post"]
    store.save(job)
    return _ok(job_id=job["id"], **d)


HANDLERS = {
    "plan_reel": plan_reel,
    "record_reel": record_reel,
    "edit_reel": edit_reel,
    "verify_reel": verify_reel,
    "fix_reel": fix_reel,
    "list_reels": list_reels,
    "reel_status": reel_status,
    "draft_post": draft_post,
}
