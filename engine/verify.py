"""verify_reel: objective checks on the rendered reel (ffprobe + ffmpeg analysis)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import ffmpeg as ff

MAX_SECONDS = 90.0
FREEZE_FAIL = 6.5


def parse_analysis(stderr: str) -> dict[str, Any]:
    black = [
        {"start": float(a), "end": float(b), "duration": float(c)}
        for a, b, c in re.findall(r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)", stderr)
    ]
    freezes = []
    starts = re.findall(r"freeze_start: ([\d.]+)", stderr)
    durs = re.findall(r"freeze_duration: ([\d.]+)", stderr)
    for i, s in enumerate(starts):
        d = float(durs[i]) if i < len(durs) else None
        freezes.append({"start": float(s), "duration": d})
    integrated = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", stderr)
    peak = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", stderr)
    return {
        "black": black,
        "freezes": freezes,
        "lufs": _f(integrated[-1]) if integrated else None,
        "true_peak": _f(peak[-1]) if peak else None,
    }


def _f(v: str) -> float | None:
    try:
        return float(v)
    except ValueError:
        return None


def check_output(path: str, expect: dict[str, Any], sound: bool, card_spans: list[tuple[float, float]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, level: str = "fail") -> None:
        checks.append({"name": name, "ok": bool(ok), "level": "ok" if ok else level, "detail": detail})

    p = Path(path)
    if not p.exists():
        add("exists", False, f"{path} missing")
        return checks
    info = ff.probe(p)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(info.get("format", {}).get("duration") or 0)
    size = p.stat().st_size
    add("duration", 5 <= dur <= MAX_SECONDS, f"{dur:.2f}s (limit {MAX_SECONDS:.0f}s)")
    if v is None:
        add("video stream", False, "no video stream")
        return checks
    add("codec", v.get("codec_name") == "h264", f"{v.get('codec_name')} {v.get('profile', '')}".strip())
    add("pixel format", v.get("pix_fmt") == "yuv420p", str(v.get("pix_fmt")))
    W, H = expect.get("size", [v.get("width"), v.get("height")])
    add("resolution", (v.get("width"), v.get("height")) == (W, H), f"{v.get('width')}x{v.get('height')}")
    num, _, den = str(v.get("r_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0
    add("frame rate", abs(fps - expect.get("fps", fps)) < 0.5, f"{fps:.2f} fps")
    add("faststart", ff.moov_before_mdat(p), "moov atom before mdat" if ff.moov_before_mdat(p) else "moov after mdat")
    add("file size", size <= ff.X_MAX_BYTES, f"{size / 1e6:.1f} MB (X limit 512 MB)")
    if sound:
        add("audio stream", a is not None and a.get("codec_name") == "aac", str(a.get("codec_name") if a else "none"))

    r = ff.run(ff.analyze_cmd(str(p)), timeout=900)
    an = parse_analysis(r.stderr.decode(errors="replace"))
    add("black frames", not an["black"], "none" if not an["black"] else f"{len(an['black'])} black span(s) at " +
        ", ".join(f"{b['start']:.1f}s" for b in an["black"][:4]))
    body_freezes = []
    for fz in an["freezes"]:
        d = fz["duration"] if fz["duration"] is not None else max(0.0, dur - fz["start"])
        inside_card = any(a0 - 0.3 <= fz["start"] and fz["start"] + d <= b0 + 0.3 for a0, b0 in card_spans)
        if not inside_card:
            body_freezes.append({"start": fz["start"], "duration": round(d, 2)})
    long_fz = [f for f in body_freezes if f["duration"] >= FREEZE_FAIL]
    if long_fz:
        add("frozen video", False, "static for " + ", ".join(f"{f['duration']}s at {f['start']:.1f}s" for f in long_fz))
    elif body_freezes:
        add("frozen video", False, "static holds " + ", ".join(f"{f['duration']}s at {f['start']:.1f}s" for f in body_freezes) +
            " (readable, but could be tighter)", level="warn")
    else:
        add("frozen video", True, "no holds over 4s")
    if sound:
        tp = an["true_peak"]
        add("audio peak", tp is None or tp <= -0.5, f"true peak {tp} dBFS" if tp is not None else "silent")
    return checks


def verify(job: dict[str, Any]) -> dict[str, Any]:
    ff.require()
    render = job.get("render") or {}
    if not render.get("outputs"):
        return {"pass": False, "checks": [{"name": "rendered", "ok": False, "level": "fail", "detail": "run edit_reel first"}]}
    st = render.get("style", {})
    sound = st.get("sound", "clicks") != "none" or bool(st.get("music"))
    report: dict[str, Any] = {"outputs": {}}
    all_checks = []
    rec = job.get("recording") or {}
    steps = rec.get("steps", [])
    skipped = [s for s in steps if not s.get("ok")]
    for fmt, out in render["outputs"].items():
        d = out["duration"]
        cards = [(0.0, 3.2), (d - 3.7, d + 1)]
        checks = check_output(out["path"], out, sound, cards)
        spans = out.get("caption_spans", [])
        n_cap_expected = len([s for s in steps if s.get("ok") and s.get("caption")])
        short = [i + 1 for i, (a, b) in enumerate(spans) if b - a < 1.15]
        checks.append({"name": "captions", "ok": len(spans) >= max(1, n_cap_expected) and not short,
                       "level": "ok" if (len(spans) >= max(1, n_cap_expected) and not short) else "fail",
                       "detail": f"{len(spans)} captions" + (f"; too short to read: {short}" if short else "")})
        report["outputs"][fmt] = checks
        all_checks += [{**c, "format": fmt} for c in checks]
    if skipped:
        all_checks.append({"name": "steps", "ok": False, "level": "warn",
                           "detail": "skipped: " + "; ".join(f"step {s['index'] + 1} ({s.get('error', '')[:90]})" for s in skipped)})
    else:
        all_checks.append({"name": "steps", "ok": True, "level": "ok", "detail": f"all {len(steps)} steps recorded"})
    fails = [c for c in all_checks if c["level"] == "fail"]
    warns = [c for c in all_checks if c["level"] == "warn"]
    report.update({"pass": not fails, "failures": fails, "warnings": warns,
                   "summary": f"{'PASS' if not fails else 'FAIL'}: {len(fails)} failure(s), {len(warns)} warning(s)"})
    return report
