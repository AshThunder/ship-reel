"""Assemble the meta demo: Hermes typing, tool calls, the live browser capture, the verified reel.

    python scripts/compose_demo.py SESSION.cast JOB_DIR OUT.mp4 [--term-mp4 term.mp4]

The terminal session is an asciinema cast of a real `hermes chat` run; agg renders it to
video. Long waits (recording, rendering, rate-limit retries) are sped up and labelled,
never hidden. Needs ffmpeg, agg and Pillow. Not used by the plugin at runtime.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = str(ROOT / "assets" / "fonts" / "Inter-Variable.ttf")
W, H, FPS = 1920, 1080, 30
BG = (14, 14, 18)
BLUE = (0, 0, 239)


def font(size: int, weight: str = "SemiBold") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(FONT, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        sys.exit(r.stderr.decode(errors="replace")[-2000:])


def cast_markers(cast: Path) -> tuple[dict[str, float], float]:
    lines = cast.read_text().splitlines()
    t = 0.0
    marks: dict[str, float] = {}
    keys = {
        # 'Preparing <tool>' is Hermes' tool-start line; the capitalised name is its completion line.
        # Plain tool names also appear in the model's visible reasoning, so they are not used.
        "plan": "Preparing plan_reel", "plan_done": "Plan_reel", "rec": "Preparing record_reel",
        "rec_done": "Record_reel", "edit": "Preparing edit_reel", "edit_done": "Edit_reel",
        "verify_done": "Verify_reel", "fix": "Preparing fix_reel", "draft_done": "Draft_post", "exit": "/exit",
    }
    submitted = None
    for ln in lines[1:]:
        ev = json.loads(ln)
        t += ev[0]
        if ev[1] != "o":
            continue
        if submitted is None:
            # the banner lists tool names; only count tool activity after the agent starts thinking
            if "Thought" in ev[2] or "∴" in ev[2]:
                submitted = t
                marks["submit"] = t
            continue
        for k, needle in keys.items():
            if needle in ev[2]:
                if k not in marks:
                    marks[k] = t
                marks[k + "_last"] = t
    return marks, t


def label_png(text: str, path: Path, sub: str | None = None) -> Path:
    f = font(30)
    fs = font(22, "Medium")
    pad_x, pad_y = 26, 16
    tw = f.getbbox(text)[2]
    sw = fs.getbbox(sub)[2] if sub else 0
    w = max(tw, sw) + pad_x * 2
    h = 30 + pad_y * 2 + (34 if sub else 0)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=14, fill=(255, 255, 255, 245))
    d.text((pad_x, pad_y - 4), text, font=f, fill=(12, 12, 16))
    if sub:
        d.text((pad_x, pad_y + 34), sub, font=fs, fill=(90, 90, 100))
    im.save(path)
    return path


def card_png(title: str, sub: str, path: Path, small: str | None = None) -> Path:
    im = Image.new("RGB", (W, H), BLUE)
    d = ImageDraw.Draw(im)
    ft, fs, fm = font(84, "Bold"), font(38, "Medium"), font(28, "Medium")
    tw = d.textlength(title, font=ft)
    d.text(((W - tw) / 2, H * 0.40), title, font=ft, fill="white")
    sw = d.textlength(sub, font=fs)
    d.text(((W - sw) / 2, H * 0.40 + 118), sub, font=fs, fill=(220, 224, 255))
    if small:
        mw = d.textlength(small, font=fm)
        d.text(((W - mw) / 2, H * 0.86), small, font=fm, fill=(200, 206, 255))
    im.save(path)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cast", type=Path)
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--term-mp4", type=Path)
    a = ap.parse_args()
    a.out, a.cast, a.job_dir = a.out.resolve(), a.cast.resolve(), a.job_dir.resolve()
    work = a.out.parent / "demo_work"
    work.mkdir(parents=True, exist_ok=True)
    marks, cast_end = cast_markers(a.cast)
    print("markers", {k: round(v, 1) for k, v in marks.items() if not k.endswith("_last")}, "end", round(cast_end, 1))

    term = a.term_mp4 or work / "term.mp4"
    if not term.exists():
        gif = work / "term.gif"
        run([str(Path.home() / "bin" / "agg"), "--font-family", "DejaVu Sans Mono", "--font-size", "22",
             "--line-height", "1.3", "--idle-time-limit", "1000", "--fps-cap", "30",
             "--theme", "0e0e12,e6e6ea,1b1b22,ff6b6b,7ee787,f0c674,6cb6ff,d2a8ff,76e3ea,c9c9d1,"
             "5c5c66,ff8f8f,9df0a8,ffd88a,9cd0ff,e3c6ff,a5f0f5,ffffff",
             str(a.cast), str(gif)])
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(gif), "-vf",
             f"fps={FPS},scale={W - 160}:{H - 150}:force_original_aspect_ratio=decrease:flags=lanczos,"
             f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2-40:color=0x0e0e12",
             "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(term)])

    job = json.loads((a.job_dir / "job.json").read_text())
    reel = Path(job["render"]["outputs"]["landscape"]["path"])
    ev = json.loads((a.job_dir / "capture" / "events.json").read_text())

    # browser capture as a video at real speed (frames carry wall-clock timestamps)
    cap = work / "browser.mp4"
    if not cap.exists():
        lst = work / "frames.txt"
        fr = ev["frames"]
        with open(lst, "w") as f:
            for (t0, name), (t1, _) in zip(fr, fr[1:] + [[fr[-1][0] + 0.04, None]]):
                f.write(f"file '{(a.job_dir / 'capture' / 'frames' / name).as_posix()}'\nduration {max(0.005, t1 - t0):.4f}\n")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf",
             f"fps={FPS},scale=1040:-2:flags=lanczos", "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p", str(cap)])
    cap_len = ev["t1"] - ev["t0"]

    m = marks
    if "rec" not in m:  # record_reel can start without a 'Preparing' line; it follows the last plan
        m["rec"] = max(v for k, v in m.items() if k.startswith("plan_done") and v < m["rec_done"]) + 0.3
    seg = []  # (start, end, out_len, label, sub, pip)
    t_type = 7.5
    seg.append((t_type, m["plan"] - 0.2, 16.0, "The prompt, typed into Hermes", "Hermes Agent + Ship Reel, model on Groq", False))
    seg.append((m["plan"] - 0.2, m["rec"] - 0.2, 5.0, "plan_reel: shot list, dry-run on the live site", None, False))
    seg.append((m["rec"] - 0.2, m["rec_done"] + 0.5, 13.0, "record_reel: real Chromium, cursor, every click checked",
                "Live browser capture, sped up", True))
    seg.append((m["rec_done"] + 0.5, m["edit_done"] + 0.5, 5.0, "edit_reel: zoom, captions, voice-over, music, 9:16 cut",
                "Rendering, sped up", False))
    end_t = m.get("exit", cast_end) - 0.6
    seg.append((m["edit_done"] + 0.5, end_t, 7.0, "verify_reel passes, draft_post writes the post", "It never posts", False))

    parts = []
    for i, (s0, s1, out_len, label, sub, pip) in enumerate(seg):
        src = s1 - s0
        speed = 1.0 if out_len is None else max(1.0, src / out_len)
        lab = label_png(label, work / f"label_{i}.png", sub)
        part = work / f"part_{i}.mp4"
        inputs = ["-ss", f"{s0:.3f}", "-t", f"{src:.3f}", "-i", str(term), "-i", str(lab)]
        fc = f"[0:v]setpts=(PTS-STARTPTS)/{speed:.4f},fps={FPS}"
        if i == 0:  # the banner's block-letter logo is half scrolled off a 34-row terminal; mask the stub
            fc += ",drawbox=x=0:y=0:w=iw:h=118:color=0x0e0e12:t=fill"
        hold = 2.8 if i == len(seg) - 1 else 0.0  # let the drafted post sit on screen
        if hold:
            fc += f",tpad=stop_mode=clone:stop_duration={hold}"
        fc += "[t];"
        if pip:
            inputs += ["-i", str(cap)]
            cap_speed = cap_len / (src / speed)
            fc += (f"[t]scale=1240:-2,pad={W}:{H}:40:(oh-ih)/2:color=0x0e0e12[tb];"
                   f"[2:v]setpts=(PTS-STARTPTS)/{cap_speed:.4f},fps={FPS}[c];"
                   f"[tb][c]overlay=W-w-40:(H-h)/2:shortest=0:eof_action=repeat[v0];")
        else:
            fc += "[t]null[v0];"
        fc += "[v0][1:v]overlay=(W-w)/2:H-h-36[v]"
        run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fc, "-map", "[v]",
             "-t", f"{src / speed + hold:.3f}", "-r", str(FPS), "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p",
             "-an", str(part)])
        parts.append(part)
        print(f"part {i}: {src:.1f}s at {speed:.2f}x -> {src / speed:.1f}s  {label}")

    title = card_png("Hermes makes its own launch reel", "One prompt. A real browser. A verified, narrated reel.",
                     work / "title.png", "Ship Reel, a Hermes Agent plugin")
    bridge = card_png("The reel Hermes made", "Unedited output of edit_reel", work / "bridge.png")
    end = card_png("hermes plugins install AshThunder/ship-reel", "github.com/AshThunder/ship-reel", work / "end.png")
    cards = []
    for name, png, d in (("title", title, 2.8), ("bridge", bridge, 1.8), ("end", end, 3.5)):
        out = work / f"card_{name}.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", str(d), "-i", str(png), "-r", str(FPS),
             "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)])
        cards.append(out)
    reel_v = work / "reel_v.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(reel), "-an", "-r", str(FPS), "-c:v", "libx264", "-crf", "16",
         "-pix_fmt", "yuv420p", str(reel_v)])

    order = [cards[0], *parts, cards[1], reel_v, cards[2]]
    lst = work / "order.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in order))
    silent = work / "silent.mp4"
    # re-encode rather than stream-copy: the parts come from different encoders and copying
    # their timestamps verbatim misplaces segments
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", f"fps={FPS}",
         "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", str(silent)])

    def dur(p: Path) -> float:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                           capture_output=True, text=True)
        return float(r.stdout.strip())

    pre = sum(dur(p) for p in order[: order.index(reel_v)])
    total = dur(silent)
    reel_len = dur(reel_v)
    bed = ROOT / "assets" / "music" / "bed.m4a"
    # audio: the music bed under the terminal part, then the reel's own soundtrack (narration, music, clicks)
    fc = (f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{pre + 0.6:.3f},volume=-10dB,"
          f"afade=t=in:d=1.2,afade=t=out:st={pre - 0.6:.3f}:d=1.2[m];"
          f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={int(pre * 1000)}|{int(pre * 1000)}[r];"
          f"[m][r]amix=inputs=2:normalize=0:duration=longest,atrim=0:{total:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-stream_loop", "-1", "-i", str(bed), "-i", str(reel),
         "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-t", f"{total:.3f}", str(a.out)])
    print(f"wrote {a.out}  {total:.1f}s (terminal part {pre:.1f}s, reel {reel_len:.1f}s)")


if __name__ == "__main__":
    main()
