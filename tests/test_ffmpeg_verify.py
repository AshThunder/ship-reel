import shutil
import subprocess

import pytest

from ship_reel.engine import ffmpeg as F
from ship_reel.engine import verify as V
from ship_reel.engine import fix as X
from ship_reel.engine import post


def test_encode_chunk_cmd():
    cmd = F.encode_chunk_cmd(1920, 1080, 30, "/tmp/o.mp4", crf=18)
    s = " ".join(cmd)
    assert "-s 1920x1080" in s and "libx264" in s and "-pix_fmt yuv420p" in s and "-crf 18" in s
    assert cmd[-1] == "/tmp/o.mp4" and "-i -" in s


def test_mux_cmd_variants():
    plain = " ".join(F.mux_cmd("v.mp4", "sfx.wav", "o.mp4", 30.0))
    assert "+faststart" in plain and "alimiter" in plain and "loudnorm" not in plain and "-c:v copy" in plain
    music = F.mux_cmd("v.mp4", "sfx.wav", "o.mp4", 30.0, music="m.mp3")
    ms = " ".join(music)
    assert "loudnorm=I=-16" in ms and "amix=inputs=2" in ms and "afade=t=out" in ms
    assert music.index("-stream_loop") < music.index("m.mp3")
    silent = " ".join(F.mux_cmd("v.mp4", None, "o.mp4", 30.0))
    assert "-an" in silent


def test_parse_analysis():
    err = """
[blackdetect @ 0x1] black_start:0 black_end:0.5 black_duration:0.5
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 10.2
[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 7.1
[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 17.3
  Integrated loudness:
    I:         -23.4 LUFS
  True peak:
    Peak:       -3.2 dBFS
"""
    a = V.parse_analysis(err)
    assert a["black"][0]["duration"] == 0.5
    assert a["freezes"][0] == {"start": 10.2, "duration": 7.1}
    assert a["lufs"] == -23.4 and a["true_peak"] == -3.2


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_check_output_on_real_file(tmp_path):
    out = tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6",
                    "-f", "lavfi", "-i", "sine=f=440:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(out)], check=True)
    checks = {c["name"]: c for c in V.check_output(str(out), {"size": [640, 360], "fps": 30}, True, [])}
    for name in ("duration", "codec", "pixel format", "resolution", "frame rate", "faststart", "black frames", "audio stream"):
        assert checks[name]["ok"], (name, checks[name])
    assert F.moov_before_mdat(out)


def test_fix_mapping():
    rep = {"failures": [{"name": "duration"}, {"name": "file size"}], "warnings": [{"name": "steps"}]}
    ch, notes, rerec = X.plan_fixes(rep, {"dwell_rate": 1.0, "crf": 17, "max_seconds": 88}, rerecorded=False)
    assert ch["max_seconds"] <= 84 and ch["dwell_rate"] > 1.0 and ch["crf"] == 21 and rerec
    ch, notes, rerec = X.plan_fixes(rep, {}, rerecorded=True)
    assert not rerec


def test_draft_post():
    job = {"url": "https://a.dev", "plan": {"title": "Acme", "tagline": "Ship faster \U0001F680", "url": "https://a.dev"},
           "recording": {"steps": [{"ok": True, "caption": f"Step {i} " + "x" * 60} for i in range(6)]}}
    d = post.draft(job, mention="@NousResearch")
    assert d["characters"] <= 280 and "\U0001F680" not in d["post"]
    assert d["note"].startswith("Draft only")


def test_draft_post_keeps_lines_and_skips_tagline():
    job = {"url": "https://x.dev", "plan": {"title": "X", "tagline": "Fast notes", "url": "https://x.dev"},
           "recording": {"steps": [{"ok": True, "caption": "Fast notes"}, {"ok": True, "caption": "Search everything"}]}}
    d = post.draft(job)
    assert "\n- Search everything" in d["post"]
    assert d["post"].count("Fast notes") == 1
