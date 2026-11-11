"""Generate assets/music/bed.m4a, Ship Reel's default music bed.

The bed is synthesised from scratch here (no samples, no third-party audio), so it is
covered by this repository's MIT licence. Requires numpy and ffmpeg; only needed to
regenerate the asset, not at runtime.

    python scripts/make_music_bed.py
"""
from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 48000
BPM = 88
BEAT = 60 / BPM
BAR = 4 * BEAT
BARS = 16
rng = np.random.default_rng(7)


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


# D major, gentle: Dmaj9, Bm9, Gmaj7(#11), A6sus  (one bar each, looped)
CHORDS = [
    [50, 57, 61, 64, 66],   # D A C# E F#
    [47, 54, 57, 61, 62],   # B F# A C# D
    [43, 50, 54, 59, 61],   # G D F# B C#
    [45, 52, 54, 59, 62],   # A E F# B D
]
ARP = [[62, 66, 69, 73], [59, 62, 66, 69], [59, 62, 66, 71], [57, 62, 64, 69]]


def saw(f: float, t: np.ndarray, harmonics: int = 14) -> np.ndarray:
    out = np.zeros_like(t)
    for n in range(1, harmonics + 1):
        if f * n > 9000:
            break
        out += np.sin(2 * np.pi * f * n * t) / n
    return out


def onepole_lp(x: np.ndarray, cutoff: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):  # fine for a one-off asset build
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


def pad_layer(total: int) -> np.ndarray:
    L = np.zeros(total)
    R = np.zeros(total)
    n_bar = int(BAR * SR)
    for b in range(BARS):
        chord = CHORDS[b % 4]
        start = b * n_bar
        length = int(n_bar * 1.6)  # overlap into next bar for a smooth crossfade
        t = np.arange(length) / SR
        env = np.minimum(1, t / 0.9) * np.where(t < BAR, 1.0, np.maximum(0, 1 - (t - BAR) / (0.6 * BAR)))
        for k, m in enumerate(chord):
            f = hz(m + 12 if k else m + 12)
            for side, det in ((L, -0.06), (R, 0.06)):
                v = saw(f * 2 ** (det / 12), t, 10) * env * (0.05 if k else 0.035)
                end = min(total, start + length)
                side[start:end] += v[: end - start]
    return np.stack([onepole_lp(L, 950), onepole_lp(R, 950)])


def bass_layer(total: int) -> np.ndarray:
    out = np.zeros(total)
    n_bar = int(BAR * SR)
    for b in range(BARS):
        root = CHORDS[b % 4][0] - 12 + 12
        for beat in (0, 2.5):
            s = b * n_bar + int(beat * BEAT * SR)
            length = int(BEAT * 1.4 * SR)
            t = np.arange(length) / SR
            env = np.minimum(1, t / 0.02) * np.exp(-t * 2.2)
            v = np.sin(2 * np.pi * hz(root - 12) * t) * env * 0.085
            e = min(total, s + length)
            if s < total:
                out[s:e] += v[: e - s]
    return np.stack([out, out])


def arp_layer(total: int) -> np.ndarray:
    dry = np.zeros(total)
    n_bar = int(BAR * SR)
    step = BEAT / 2
    for b in range(4, BARS):
        notes = ARP[b % 4]
        for i in range(8):
            m = notes[[0, 1, 2, 3, 2, 1, 3, 2][i]] + 12
            s = b * n_bar + int(i * step * SR)
            length = int(0.9 * SR)
            t = np.arange(length) / SR
            env = np.minimum(1, t / 0.004) * np.exp(-t * 6.5)
            f = hz(m)
            v = (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 12)) * env
            vel = 0.05 * (0.85 + 0.15 * rng.random()) * (1.0 if i % 2 == 0 else 0.8)
            e = min(total, s + length)
            if s < total:
                dry[s:e] += v[: e - s] * vel
    # ping-pong delay at a dotted eighth
    d = int(0.75 * BEAT * SR)
    L = dry.copy()
    R = dry * 0.8
    fbL = np.zeros(total)
    fbR = np.zeros(total)
    for rep in range(1, 5):
        g = 0.38 ** rep
        sh = d * rep
        tgt = fbR if rep % 2 else fbL
        tgt[sh:] += dry[:-sh] * g
    return np.stack([L + fbL, R + fbR])


def hats(total: int) -> np.ndarray:
    out = np.zeros(total)
    n_bar = int(BAR * SR)
    for b in range(8, BARS):
        for i in range(8):
            s = b * n_bar + int(i * BEAT / 2 * SR)
            length = int(0.06 * SR)
            noise = rng.uniform(-1, 1, length)
            noise = np.diff(np.concatenate([[0], noise]))  # high-pass
            env = np.exp(-np.arange(length) / SR * 70)
            e = min(total, s + length)
            if s < total:
                out[s:e] += (noise * env * (0.018 if i % 2 else 0.01))[: e - s]
    return np.stack([out * 0.9, out])


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    loop = int(BARS * BAR * SR)
    total = int((BARS + 1) * BAR * SR)
    mix = pad_layer(total) + bass_layer(total) + arp_layer(total) + hats(total)
    # fold the tail past the loop point back onto the start so the bed loops seamlessly
    body = mix[:, :loop].copy()
    tail = mix[:, loop:]
    body[:, : tail.shape[1]] += tail
    body *= 10 ** (-20 / 20) / (np.sqrt(np.mean(body ** 2)) + 1e-9)
    body = np.tanh(body * 1.2) / 1.2
    pcm = (np.clip(body.T, -1, 1) * 32767).astype("<i2")
    out_dir = root / "assets" / "music"
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / "bed.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "128k",
                    str(out_dir / "bed.m4a")], check=True)
    wav.unlink()
    print("wrote", out_dir / "bed.m4a", f"{loop / SR:.1f}s")


if __name__ == "__main__":
    sys.exit(main())
