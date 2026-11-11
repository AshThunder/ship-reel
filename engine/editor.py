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

from . import audio, ffmpeg as ff, theme as th, timeline as tl, voice

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
        "music": "default",         # default (bundled bed) | none | path to a file you have rights to
        "music_volume": -12.0,      # dB applied to the bed before the final loudness pass
        "voice": "auto",            # auto (narrate if kokoro-onnx is installed) | none | a Kokoro voice id
        "voice_speed": 1.05,
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
    m = str(s["music"] or "none").strip()
    if m.lower() in ("none", "off", "false", ""):
        s["music"] = None
    elif m.lower() in ("default", "on", "true", "auto"):
        s["music"] = str(DEFAULT_MUSIC) if DEFAULT_MUSIC.is_file() else None
    else:
        mp = Path(m).expanduser()
        s["music"] = str(mp) if mp.is_file() else None
    s["music_volume"] = max(-40.0, min(0.0, float(s["music_volume"])))
    v = str(s["voice"] or "none").strip()
    s["voice"] = None if v.lower() in ("none", "off", "false", "") else v
    s["voice_speed"] = max(0.8, min(1.3, float(s["voice_speed"])))
    return s


DEFAULT_MUSIC = Path(__file__).resolve().parent.parent / "assets" / "music" / "bed.m4a"


def _narrate(st: dict[str, Any], plan: dict[str, Any], rp: Any, cache: dict[str, list[float]]):
    """Return (clips, worst_overrun) or raise voice.VoiceUnavailable."""
    lines = voice.narration_lines(rp.captions, plan, rp.title_d, rp.duration - tl.END_D)
    voice_id = voice.DEFAULT_VOICE if st["voice"] == "auto" else st["voice"]
    clips_audio = []
    for ln in lines:
        key = f"{voice_id}|{st['voice_speed']}|{ln['text']}"
        if key not in cache:
            cache[key] = voice.synth(ln["text"], voice_id, st["voice_speed"])
        clips_audio.append(cache[key])
    durs = [len(c) / voice.SR_OUT for c in clips_audio]
    starts, worst = voice.schedule(lines, durs)
    title_need = None
    if lines and lines[0]["a"] < 1.0 and rp.captions:
        title_need = lines[0]["a"] + durs[0] + 0.25  # title card should outlast its line
    return list(zip(starts, clips_audio)), worst, voice_id, title_need


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
    narration_note = None
    vcache: dict[str, list[float]] = {}
    voice_id = None
    for fmt in formats:
        W, H = FORMATS[fmt]
        dwell_rate = st["dwell_rate"]
        rp = tl.build(events, plan, W, H, st["fps"], speed=st["speed"], dwell_rate=dwell_rate,
                      zoom=st["zoom"], max_seconds=st["max_seconds"])
        clips = None
        if st["voice"] and narration_note is None:
            if st["voice"] == "auto" and not voice.installed():
                narration_note = "no narration: install the optional kokoro-onnx package to enable it"
            else:
                try:
                    title_d = tl.TITLE_D
                    for _ in range(5):
                        clips, worst, voice_id, title_need = _narrate(st, plan, rp, vcache)
                        if title_need and title_need > title_d + 0.05 and title_d < 6.0:
                            # let the title card hold until its line finishes
                            title_d = min(6.0, title_need)
                            rp = tl.build(events, plan, W, H, st["fps"], speed=st["speed"], dwell_rate=dwell_rate,
                                          zoom=st["zoom"], max_seconds=st["max_seconds"], title_d=title_d)
                            continue
                        if worst <= 0.9 or dwell_rate <= 0.55:
                            break
                        # give the narration room: hold each shot a little longer and rebuild
                        dwell_rate = max(0.5, dwell_rate * 0.8)
                        rp = tl.build(events, plan, W, H, st["fps"], speed=st["speed"], dwell_rate=dwell_rate,
                                      zoom=st["zoom"], max_seconds=st["max_seconds"], title_d=title_d)
                except voice.VoiceUnavailable as exc:
                    narration_note = str(exc)
                    clips = None
                except Exception as exc:  # noqa: BLE001 - narration must never break a render
                    narration_note = f"narration skipped: {exc.__class__.__name__}: {str(exc)[:160]}"
                    clips = None
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
        vo_wav = None
        if clips:
            vo_wav = str(voice.write_track(out_dir / f"voice_{fmt}.wav", rp.duration, clips))
        r = ff.run(ff.mux_cmd(str(silent), sfx, str(final), rp.duration, music=st["music"],
                              voice_wav=vo_wav, music_db=st["music_volume"]))
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
            "narrated": bool(clips),
            "title_d": rp.title_d,
        }
    main = outputs[formats[0]]["path"]
    return {
        "main": main,
        "outputs": outputs,
        "style": {**st, "music": "default" if st["music"] == str(DEFAULT_MUSIC) else (st["music"] or "none")},
        "theme": {"background": th.rgb_to_hex(theme["bg"]), "accent": th.rgb_to_hex(theme["accent"])},
        "render_seconds": round(time.time() - t_start, 1),
        "audio": {
            "music": ("bundled bed (MIT, generated by Ship Reel)" if st["music"] == str(DEFAULT_MUSIC)
                      else st["music"]) or "none",
            "voice": (voice_id if any(o.get("narrated") for o in outputs.values()) else "none"),
            "note": narration_note,
        },
    }
