"""edit_reel: render plan -> parallel frame rendering -> concat -> audio mux."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PIL import Image

from . import audio, ffmpeg as ff, theme as th, timeline as tl

WORKER = Path(__file__).resolve().parent / "render_worker.py"

FORMATS = {
    "landscape": (1920, 1080),
    "landscape720": (1280, 720),
    "vertical": (1080, 1920),
}


def default_style() -> dict[str, Any]:
    return {
        "format": "landscape",      # landscape | landscape720 | vertical
        "vertical": True,           # also export a 9:16 cut
        "fps": 30,
        "zoom": True,
        "background": "auto",       # hex or auto (brand colour from the page)
        "accent": "auto",
        "sound": "clicks",          # clicks | none
        "music": None,              # optional path to a music file you have rights to
        "speed": 1.0,
        "dwell_rate": 1.0,
        "crf": 17,
        "max_seconds": 88.0,
    }


def merge_style(style: dict[str, Any] | None, base: dict[str, Any] | None = None) -> dict[str, Any]:
    s = {**default_style(), **(base or {})}
    for k, v in (style or {}).items():
        if k in s and v is not None:
            s[k] = v
    s["fps"] = 60 if int(s["fps"]) >= 45 else 30
    if s["format"] not in FORMATS:
        s["format"] = "landscape"
    s["speed"] = max(0.5, min(3.0, float(s["speed"])))
    s["dwell_rate"] = max(0.5, min(3.0, float(s["dwell_rate"])))
    s["crf"] = max(12, min(30, int(s["crf"])))
    s["max_seconds"] = max(15.0, min(90.0, float(s["max_seconds"])))
    if s["music"]:
        mp = Path(str(s["music"])).expanduser()
        s["music"] = str(mp) if mp.is_file() else None
    return s


def render_parallel(ctx_path: Path, n_frames: int, out: Path, workdir: Path, workers: int | None = None) -> None:
    workers = workers or max(1, min(8, (os.cpu_count() or 2)))
    workers = max(1, min(workers, n_frames // 30 or 1))
    bounds = [round(i * n_frames / workers) for i in range(workers + 1)]
    procs = []
    chunks = []
    for i in range(workers):
        chunk = workdir / f"chunk_{i:02d}.mp4"
        chunks.append(chunk)
        procs.append(subprocess.Popen([sys.executable, str(WORKER), str(ctx_path), str(bounds[i]), str(bounds[i + 1]), str(chunk)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
    errors = []
    for p in procs:
        _, err = p.communicate(timeout=1800)
        if p.returncode != 0:
            errors.append(err.decode(errors="replace")[-800:])
    if errors:
        raise RuntimeError("render worker failed: " + errors[0])
    lst = workdir / "chunks.txt"
    lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in chunks))
    r = ff.run(ff.concat_cmd(str(lst), str(out)))
    if r.returncode != 0:
        raise RuntimeError("concat failed: " + r.stderr.decode(errors="replace")[-400:])
    for c in chunks:
        c.unlink(missing_ok=True)


def edit(job: dict[str, Any], job_dir: Path, style: dict[str, Any] | None = None) -> dict[str, Any]:
    ff.require()
    cap = job_dir / "capture"
    events = json.loads((cap / "events.json").read_text())
    if not events.get("frames"):
        raise RuntimeError("the recording has no frames; run record_reel again")
    plan = job["plan"]
    st = merge_style(style, job.get("style"))
    out_dir = job_dir / "render"
    out_dir.mkdir(exist_ok=True)
    first = Image.open(cap / "frames" / events["frames"][0][1])
    theme = th.build_theme(first, st, events.get("page"))

    outputs: dict[str, Any] = {}
    formats = [st["format"]] + (["vertical"] if st["vertical"] and st["format"] != "vertical" else [])
    t_start = time.time()
    for fmt in formats:
        W, H = FORMATS[fmt]
        rp = tl.build(events, plan, W, H, st["fps"], speed=st["speed"], dwell_rate=st["dwell_rate"],
                      zoom=st["zoom"], max_seconds=st["max_seconds"])
        ctx = {
            "plan": asdict(rp),
            "frames_dir": str(cap / "frames"),
            "frame_names": [f[1] for f in events["frames"]],
            "theme": {k: (list(v) if isinstance(v, tuple) else v) for k, v in theme.items()},
            "viewport": events["viewport"],
            "dpr": events["dpr"],
            "title": plan.get("title"),
            "tagline": plan.get("tagline"),
            "url": plan.get("url"),
            "cta_url": plan.get("cta_url") or plan.get("url"),
            "crf": st["crf"],
        }
        ctx["theme"]["chrome"] = {k: list(v) for k, v in theme["chrome"].items()}
        ctx_path = out_dir / f"ctx_{fmt}.json"
        ctx_path.write_text(json.dumps(ctx))
        silent = out_dir / f"video_{fmt}.mp4"
        render_parallel(ctx_path, len(rp.frames), silent, out_dir)
        final = out_dir / f"reel_{fmt}.mp4"
        sfx = None
        if st["sound"] == "clicks":
            sw = [max(0.0, x - 0.05) for x in rp.xfades_out]  # soft swoosh under page cross-fades
            sfx = audio.synth_track(out_dir / f"sfx_{fmt}.wav", rp.duration, rp.clicks_out, rp.keys_out, sw)
        r = ff.run(ff.mux_cmd(str(silent), sfx, str(final), rp.duration, music=st["music"]))
        if r.returncode != 0:
            raise RuntimeError("mux failed: " + r.stderr.decode(errors="replace")[-400:])
        silent.unlink(missing_ok=True)
        outputs[fmt] = {
            "path": str(final),
            "duration": rp.duration,
            "size": [W, H],
            "fps": rp.fps,
            "captions": len(rp.captions),
            "caption_spans": [[c["a"], c["b"]] for c in rp.captions],
            "speed": rp.speed,
            "frames": len(rp.frames),
        }
    main = outputs[formats[0]]["path"]
    return {
        "main": main,
        "outputs": outputs,
        "style": st,
        "theme": {"background": th.rgb_to_hex(theme["bg"]), "accent": th.rgb_to_hex(theme["accent"])},
        "render_seconds": round(time.time() - t_start, 1),
    }
