"""Optional voice-over with Kokoro (kokoro-onnx, Apache-2.0 model weights).

Nothing here is required. If `kokoro-onnx` is not installed, or the model cannot be
downloaded, narration is skipped and the reel renders without it. The model files
(~120 MB) are fetched once from the kokoro-onnx GitHub release into the plugin data
directory and checked against pinned SHA-256 hashes before use.
"""
from __future__ import annotations

import array
import hashlib
import re
import urllib.request
import wave
from pathlib import Path
from typing import Any

from . import store

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = {
    "kokoro-v1.0.int8.onnx": "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
DEFAULT_VOICE = "af_heart"
SR_OUT = 48000

_ENGINE: Any = None


class VoiceUnavailable(RuntimeError):
    pass


def installed() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
        return True
    except Exception:
        return False


def model_dir() -> Path:
    d = store.data_dir() / "models" / "kokoro"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model() -> Path:
    d = model_dir()
    for name, digest in FILES.items():
        p = d / name
        if p.is_file() and _sha256(p) == digest:
            continue
        tmp = p.with_suffix(p.suffix + ".part")
        try:
            with urllib.request.urlopen(RELEASE + name, timeout=60) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise VoiceUnavailable(f"could not download the voice model ({exc.__class__.__name__}); "
                                   f"rendering without narration") from exc
        if _sha256(tmp) != digest:
            tmp.unlink(missing_ok=True)
            raise VoiceUnavailable("voice model checksum mismatch; rendering without narration")
        tmp.replace(p)
    return d


def _engine():
    global _ENGINE
    if _ENGINE is None:
        if not installed():
            raise VoiceUnavailable("narration needs the optional 'kokoro-onnx' package "
                                   "(pip install 'kokoro-onnx>=0.4,<1'); rendering without it")
        from kokoro_onnx import Kokoro
        d = ensure_model()
        _ENGINE = Kokoro(str(d / "kokoro-v1.0.int8.onnx"), str(d / "voices-v1.0.bin"))
    return _ENGINE


_ABBR = {"AI": "A.I.", "UI": "U.I.", "API": "A.P.I.", "CLI": "C.L.I.", "BTC": "B.T.C.", "MUSD": "M.U.S.D."}


def speakable(text: str) -> str:
    """Make caption text read well aloud: domains, common acronyms, trailing punctuation."""
    t = re.sub(r"https?://", "", text.strip())
    t = re.sub(r"\b([a-z0-9-]+)\.(com|online|app|dev|io|ai|org|net|xyz|so|co)\b",
               lambda m: f"{m.group(1)} dot {m.group(2)}", t, flags=re.I)
    t = re.sub(r"\bve([A-Z]{2,})\b", lambda m: "vee " + m.group(1), t)
    for k, v in _ABBR.items():
        t = re.sub(rf"\b{k}\b", v, t)
    if t and t[-1] not in ".!?":
        t += "."
    return t


def synth(text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> list[float]:
    """Return mono float samples at 48 kHz."""
    eng = _engine()
    if voice not in eng.get_voices():
        voice = DEFAULT_VOICE
    samples, sr = eng.create(speakable(text), voice=voice, speed=speed, lang="en-us")
    vals = [float(x) for x in samples]
    if sr == SR_OUT:
        return vals
    # linear resample 24k -> 48k
    ratio = sr / SR_OUT
    n = int(len(vals) / ratio)
    out = []
    for i in range(n):
        x = i * ratio
        j = int(x)
        f = x - j
        a = vals[j]
        b = vals[j + 1] if j + 1 < len(vals) else a
        out.append(a + (b - a) * f)
    return out


def schedule(lines: list[dict[str, Any]], durations: list[float], gap: float = 0.18) -> tuple[list[float], float]:
    """Place each line at its slot start, never overlapping the previous one.

    lines: [{"a": slot start, "b": slot end}], durations: seconds per line.
    Returns (start times, worst overrun past a slot end in seconds)."""
    starts: list[float] = []
    prev_end = -1e9
    worst = 0.0
    for ln, d in zip(lines, durations):
        s = max(ln["a"], prev_end + gap)
        starts.append(round(s, 3))
        prev_end = s + d
        worst = max(worst, prev_end - ln["b"])
    return starts, worst


def write_track(path: Path, duration: float, clips: list[tuple[float, list[float]]], gain: float = 0.9) -> Path:
    n = int(duration * SR_OUT) + 1
    buf = array.array("f", bytes(4 * n))
    for start, samples in clips:
        o = int(start * SR_OUT)
        for i, v in enumerate(samples):
            k = o + i
            if k >= n:
                break
            buf[k] += v * gain
    pcm = array.array("h", (max(-32767, min(32767, int(v * 32767))) for v in buf))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR_OUT)
        w.writeframes(pcm.tobytes())
    return path


def narration_lines(rp_captions: list[dict[str, Any]], plan: dict[str, Any], title_end: float,
                    end_start: float) -> list[dict[str, Any]]:
    """Script: title + tagline over the title card, one line per caption (or the step's
    `say` override), and the call to action over the end card."""
    steps = plan.get("steps", [])
    out: list[dict[str, Any]] = []
    title = (plan.get("title") or "").strip()
    tagline = (plan.get("tagline") or "").strip().rstrip(".")
    if title or tagline:
        txt = f"{title}. {tagline}" if title and tagline else (title or tagline)
        out.append({"text": txt, "a": 0.35, "b": title_end + 0.8})
    say_by_caption = {s.get("caption"): s.get("say") for s in steps if s.get("say")}
    for c in rp_captions:
        txt = say_by_caption.get(c["text"]) or c["text"]
        out.append({"text": txt, "a": c["a"] + 0.12, "b": c["b"]})
    cta = plan.get("cta_url") or plan.get("url") or ""
    host = re.sub(r"^https?://(www\.)?", "", cta).rstrip("/")
    if host:
        out.append({"text": f"Try it at {host}", "a": end_start + 0.3, "b": end_start + 2.9})
    return out
