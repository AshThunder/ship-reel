import pytest

from ship_reel.engine import plan as P


def test_normalize_basic_and_aliases():
    plan, warns = P.normalize_plan({
        "url": "example.com",
        "steps": [
            {"action": "goto", "url": "https://example.com/"},
            {"action": "tap", "target": "Docs", "caption": "Open the docs"},
            {"action": "fill", "target": "Search", "value": "agents", "caption": "Search"},
            {"action": "scroll_down", "caption": "Scroll"},
            {"action": "pause", "dwell": 99},
        ],
    })
    assert plan["url"] == "https://example.com"
    acts = [s["action"] for s in plan["steps"]]
    assert acts == ["click", "type", "scroll", "wait"]  # leading navigate to start URL dropped
    assert plan["steps"][0]["zoom"] is True and plan["steps"][2]["zoom"] is False
    assert plan["steps"][3]["dwell"] == 6.0  # clamped
    assert plan["steps"][2]["amount"] == 0.75


def test_risky_clicks_refused_unless_allowed():
    for target in ("Buy now", "Sign up", "Connect wallet", "Log in", "Delete project", "Checkout"):
        with pytest.raises(P.PlanError):
            P.normalize_plan({"url": "https://x.dev", "steps": [{"action": "click", "target": target}]})
    plan, _ = P.normalize_plan({"url": "https://x.dev", "allow_risky": True,
                                "steps": [{"action": "click", "target": "Sign up", "caption": "x"}]})
    assert plan["steps"][0]["target"] == "Sign up"
    # ordinary words are fine
    P.normalize_plan({"url": "https://x.dev", "steps": [{"action": "click", "target": "Blog posts", "caption": "x"}]})


def test_captions_are_clean():
    assert P.clean_caption("Ship it \U0001F680 now \u2728") == "Ship it now"
    long = "word " * 40
    c = P.clean_caption(long)
    assert len(c) <= P.CAPTION_MAX and not c.endswith(" ")


def test_bad_inputs():
    with pytest.raises(P.PlanError):
        P.check_url("javascript:alert(1)")
    with pytest.raises(P.PlanError):
        P.check_url("file:///etc/passwd")
    with pytest.raises(P.PlanError):
        P.normalize_plan({"url": "https://x.dev", "steps": [{"action": "dance"}]})
    with pytest.raises(P.PlanError):
        P.normalize_plan({"url": "https://x.dev", "steps": [{"action": "click"}]})
    with pytest.raises(P.PlanError):
        P.normalize_plan({"url": "https://x.dev", "steps": [{"action": "wait"}] * 13})
    with pytest.raises(P.PlanError):
        P.normalize_plan({"url": "https://x.dev", "steps": []})


def test_wait_until_and_estimate():
    plan, warns = P.normalize_plan({"url": "https://x.dev", "steps": [
        {"action": "click", "target": "Run demo", "caption": "Run it"},
        {"action": "wait", "until": "Done", "timeout": 999, "caption": "Working"},
    ]})
    w = plan["steps"][1]
    assert w["until"] == "Done" and w["timeout"] == 150 and w["timelapse"] is True
    assert plan["estimated_seconds"] > 10
    assert any("5-8" in x for x in warns)
