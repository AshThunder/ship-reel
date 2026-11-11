"""ffmpeg / ffprobe command builders (pure, unit-tested) plus thin runners."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

X_MAX_BYTES = 512 * 1024 * 1024  # X upload ceiling for video


def require() -> tuple[str, str]:
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ff or not fp:
        raise RuntimeError("ffmpeg and ffprobe are required on PATH (e.g. apt install ffmpeg / brew install ffmpeg)")
    return ff, fp


def encode_chunk_cmd(W: int, H: int, fps: int, out: str, crf: int = 17, preset: str = "medium") -> list[str]:
    """Raw RGB frames on stdin -> H.264 chunk. Identical params per chunk so chunks concat losslessly."""
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-x264-params", f"keyint={fps * 2}:min-keyint={fps}:scenecut=0",
        "-r", str(fps), out,
    ]


def concat_cmd(list_file: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
            "-i", list_file, "-c", "copy", out]


def mux_cmd(video: str, sfx_wav: str | None, out: str, duration: float, music: str | None = None,
            voice_wav: str | None = None, music_db: float = -12.0) -> list[str]:
    """Final mux: video copy + mixed audio -> AAC, faststart.

    Layers: synthesised click/key sounds, an optional voice-over, and an optional looped
    music bed that fades in and out and is side-chain ducked under the voice."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video]
    labels = []
    filters = []
    idx = 1
    if sfx_wav:
        cmd += ["-i", sfx_wav]
        labels.append(f"[{idx}:a]")
        idx += 1
    if voice_wav:
        cmd += ["-i", voice_wav]
        split = ",asplit=2[vo][vosc]" if music else "[vo]"
        filters.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                       f"highpass=f=70,acompressor=threshold=0.12:ratio=3:attack=8:release=120:makeup=1.4{split}")
        labels.append("[vo]")
        idx += 1
    if music:
        cmd += ["-stream_loop", "-1", "-i", music]
        fade_out = max(0.0, duration - 2.5)
        filters.append(
            f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={music_db}dB,afade=t=in:st=0:d=1.5,afade=t=out:st={fade_out:.3f}:d=2.4"
            + ("[mu0]" if voice_wav else "[mu]")
        )
        if voice_wav:
            filters.append("[mu0][vosc]sidechaincompress=threshold=0.02:ratio=9:attack=25:release=420:knee=4[mu]")
        labels.append("[mu]")
        idx += 1
    if not labels:
        return cmd + ["-c:v", "copy", "-an", "-movflags", "+faststart", out]
    if voice_wav:
        norm = "loudnorm=I=-16:TP=-1.5:LRA=11"
    elif music:
        norm = "loudnorm=I=-20:TP=-2:LRA=9"
    else:
        norm = "alimiter=limit=0.89"
    if len(labels) == 1 and not filters:
        filters.append(f"{labels[0]}aformat=sample_rates=48000:channel_layouts=stereo,{norm},atrim=0:{duration:.3f}[a]")
    else:
        filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first,{norm},"
                       f"aresample=48000,atrim=0:{duration:.3f}[a]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", "-t", f"{duration:.3f}", out]
    return cmd


def reencode_cmd(inp: str, out: str, crf: int = 20, max_kbps: int | None = None, fps: int | None = None) -> list[str]:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", inp,
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-profile:v", "high", "-pix_fmt", "yuv420p"]
    if max_kbps:
        cmd += ["-maxrate", f"{max_kbps}k", "-bufsize", f"{max_kbps * 2}k"]
    if fps:
        cmd += ["-r", str(fps)]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    return cmd


def analyze_cmd(path: str, black_d: float = 0.25, freeze_d: float = 4.0) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
            "-vf", f"blackdetect=d={black_d}:pix_th=0.05:pic_th=0.98,freezedetect=n=0.0008:d={freeze_d}",
            "-af", "ebur128=peak=true", "-f", "null", "-"]


def probe_cmd(path: str) -> list[str]:
    return ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]


def run(cmd: list[str], timeout: float = 600, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, input=input_bytes, check=False)


def probe(path: str | Path) -> dict[str, Any]:
    r = run(probe_cmd(str(path)), timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr.decode(errors='replace')[:300]}")
    return json.loads(r.stdout.decode())


def moov_before_mdat(path: str | Path) -> bool:
    """True when the MP4 is 'faststart' (moov atom precedes mdat)."""
    with open(path, "rb") as fh:
        pos = 0
        size_total = Path(path).stat().st_size
        while pos < size_total:
            fh.seek(pos)
            hdr = fh.read(16)
            if len(hdr) < 8:
                return False
            size = int.from_bytes(hdr[:4], "big")
            kind = hdr[4:8]
            if size == 1:
                size = int.from_bytes(hdr[8:16], "big")
            if kind == b"moov":
                return True
            if kind == b"mdat":
                return False
            if size < 8:
                return False
            pos += size
    return False
