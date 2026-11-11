from pathlib import Path

from ship_reel.engine import editor, ffmpeg as F, voice


def test_mux_ducks_music_under_voice():
    cmd = F.mux_cmd("v.mp4", "sfx.wav", "o.mp4", 30.0, music="m.m4a", voice_wav="vo.wav")
    s = " ".join(cmd)
    assert "sidechaincompress" in s and "asplit=2[vo][vosc]" in s
    assert "loudnorm=I=-16" in s and "amix=inputs=3" in s
    assert cmd.index("vo.wav") < cmd.index("m.m4a")


def test_voice_without_music_has_no_sidechain():
    s = " ".join(F.mux_cmd("v.mp4", None, "o.mp4", 10.0, voice_wav="vo.wav"))
    assert "sidechaincompress" not in s and "[vo]" in s


def test_music_style_resolution():
    st = editor.merge_style({})
    assert st["music"] and Path(st["music"]).name == "bed.m4a" and Path(st["music"]).is_file()
    assert editor.merge_style({"music": "none"})["music"] is None
    assert editor.merge_style({"music": "/no/such/file.mp3"})["music"] is None
    assert editor.merge_style({"voice": "none"})["voice"] is None
    assert editor.merge_style({"voice": "am_michael"})["voice"] == "am_michael"
    assert editor.merge_style({"music_volume": 9})["music_volume"] == 0.0


def test_speakable():
    assert voice.speakable("Try it at nousresearch.com") == "Try it at nousresearch dot com."
    assert "A.I." in voice.speakable("Open source AI")
    assert voice.speakable("Locks on veBTC").startswith("Locks on vee B.T.C.")


def test_schedule_never_overlaps_and_reports_overrun():
    lines = [{"a": 0.0, "b": 2.0}, {"a": 1.0, "b": 3.0}, {"a": 5.0, "b": 7.0}]
    starts, worst = voice.schedule(lines, [1.5, 2.0, 1.0], gap=0.2)
    assert starts[1] >= 1.7 and starts[2] == 5.0
    assert abs(worst - (1.7 + 2.0 - 3.0)) < 1e-6


def test_narration_script_uses_say_override_and_cta():
    plan = {"title": "Acme", "tagline": "Ship faster", "url": "https://acme.dev",
            "steps": [{"caption": "Open the board", "say": "Here is the board everyone shares"}]}
    caps = [{"text": "Open the board", "a": 4.0, "b": 7.0}]
    lines = voice.narration_lines(caps, plan, 2.4, 20.0)
    assert lines[0]["text"] == "Acme. Ship faster"
    assert lines[1]["text"] == "Here is the board everyone shares"
    assert lines[-1]["text"] == "Try it at acme.dev" and lines[-1]["a"] > 20.0


def test_write_track(tmp_path):
    p = voice.write_track(tmp_path / "vo.wav", 1.0, [(0.1, [0.5] * 100)])
    assert p.stat().st_size > 48000 * 2
