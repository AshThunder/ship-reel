from pathlib import Path

import ship_reel
from ship_reel.engine import theme

ROOT = Path(__file__).resolve().parent.parent


class FakeCtx:
    def __init__(self):
        self.tools, self.skills = {}, {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert schema["name"] == name and callable(handler)
        self.tools[name] = (toolset, schema, handler, kw)

    def register_skill(self, name, path):
        self.skills[name] = Path(path)


def test_register_matches_manifest():
    ctx = FakeCtx()
    ship_reel.register(ctx)
    declared = []
    in_tools = False
    for line in (ROOT / "plugin.yaml").read_text().splitlines():
        if line.startswith("provides_tools:"):
            in_tools = True
            continue
        if in_tools:
            if line.startswith("  - "):
                declared.append(line.strip()[2:])
            else:
                in_tools = False
    assert sorted(ctx.tools) == sorted(declared)
    assert "ship-reel" in ctx.skills and ctx.skills["ship-reel"].exists()


def test_handlers_return_json_errors_not_exceptions(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("SHIP_REEL_HOME", str(tmp_path))
    ctx = FakeCtx()
    ship_reel.register(ctx)
    r = json.loads(ctx.tools["reel_status"][2]({"job_id": "nope-0000"}))
    assert r["ok"] is False and "no reel job" in r["error"]
    r = json.loads(ctx.tools["plan_reel"][2]({"url": "ftp://x", "brief": "b"}))
    assert r["ok"] is False
    r = json.loads(ctx.tools["list_reels"][2]({}))
    assert r["ok"] is True and r["reels"] == []


def test_theme_contrast_and_font():
    assert theme.text_on((0, 0, 239)) == (255, 255, 255)
    assert theme.text_on((228, 164, 58)) == (17, 17, 19)
    f = theme.font(32, "semibold")
    assert f.getbbox("Ship Reel")[2] > 50
