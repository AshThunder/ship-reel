"""Procedural sound design: soft click and key ticks synthesised in Python.

No sample files are bundled, so there is nothing to license. Optional music is
whatever file the user passes (their responsibility to have rights to it).
"""
from __future__ import annotations

import array
import math
import random
import wave
from pathlib import Path

SR = 48000


def _click(gain: float = 0.32, seed: int = 0) -> list[float]:
    rnd = random.Random(seed)
    n = int(SR * 0.045)
    out = []
    prev = 0.0
    for i in range(n):
        t = i / SR
        env = math.exp(-t * 140)
        noise = rnd.uniform(-1, 1)
        hp = noise - prev  # crude high-pass so it reads as a 'tick', not a thump
        prev = noise
        tone = math.sin(2 * math.pi * 1650 * t) * math.exp(-t * 95)
        body = math.sin(2 * math.pi * 420 * t) * math.exp(-t * 60) * 0.5
        out.append(gain * env * (0.5 * hp + 0.6 * tone + body) * (min(1.0, i / 24)))
    return out


def _tick(gain: float = 0.09, seed: int = 0) -> list[float]:
    rnd = random.Random(seed)
    n = int(SR * 0.022)
    out = []
    prev = 0.0
    f = 2300 + rnd.uniform(-250, 250)
    for i in range(n):
        t = i / SR
        noise = rnd.uniform(-1, 1)
        hp = noise - prev
        prev = noise
        out.append(gain * math.exp(-t * 260) * (0.7 * hp + 0.5 * math.sin(2 * math.pi * f * t)) * min(1.0, i / 12))
    return out


def _swoosh(dur: float = 0.42, gain: float = 0.05, seed: int = 0) -> list[float]:
    rnd = random.Random(seed)
    n = int(SR * dur)
    out = []
    lp = 0.0
    for i in range(n):
        p = i / n
        env = math.sin(math.pi * p) ** 2
        noise = rnd.uniform(-1, 1)
        a = 0.04 + 0.25 * p  # sweep the one-pole low-pass open
        lp += a * (noise - lp)
        out.append(gain * env * lp * 3)
    return out


def synth_track(path: str | Path, duration: float, clicks: list[float], keys: list[float],
                swooshes: list[float] | None = None) -> str:
    n = int(SR * duration) + 1
    buf = [0.0] * n

    def mix(samples: list[float], at: float) -> None:
        start = int(at * SR)
        for i, v in enumerate(samples):
            j = start + i
            if 0 <= j < n:
                buf[j] += v

    for i, t in enumerate(clicks):
        mix(_click(seed=i), t)
    for i, t in enumerate(keys):
        mix(_tick(seed=i), t)
    for i, t in enumerate(swooshes or []):
        mix(_swoosh(seed=i), t)
    pcm = array.array("h", (max(-32767, min(32767, int(v * 32767))) for v in buf))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return str(path)
