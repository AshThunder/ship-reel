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


def test_risky_clicks_flagged_not_model_approvable():
    for target in ("Buy now", "Sign up", "Connect wallet", "Log in", "Delete project", "Checkout", "Send"):
        plan, warns = P.normalize_plan({"url": "https://x.dev", "steps": [{"action": "click", "target": target, "caption": "x"}]})
        assert plan["steps"][0]["risky"] is True and any("approve" in w for w in warns)
    # a model-supplied allow_risky / risky flag has no effect on safe steps and is not echoed
    plan, _ = P.normalize_plan({"url": "https://x.dev", "allow_risky": True,
                                "steps": [{"action": "click", "target": "Blog posts", "caption": "x", "risky": True}]})
    assert "allow_risky" not in plan and "risky" not in plan["steps"][0]


def test_password_type_refused():
    for target in ("Password", "css=input[type=password]", "css=input[type='password']", "label=Passcode",
                   "css=#current-password"):
        with pytest.raises(P.PlanError, match="never enters"):
            P.normalize_plan({"url": "https://x.dev", "steps": [{"action": "type", "target": target, "value": "x"}]})


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


def test_caption_quotes_and_mechanical_lint():
    from ship_reel.engine import plan as P
    assert P.clean_caption("'Scroll to 'Pricing'") == "Scroll to Pricing" or "'" not in P.clean_caption("Scroll to 'Pricing")
    assert P.clean_caption('"Fast notes"') == "Fast notes"
    assert P.mechanical_caption("Scroll to Terminal Velocity")
    assert P.mechanical_caption("Hover over HERMES menu")
    assert not P.mechanical_caption("Every Hermes surface, one menu away")
    plan, _ = P.normalize_plan({"url": "https://x.dev", "steps": [
        {"action": "press", "key": "Escape", "caption": "Close search palette"},
        {"action": "scroll", "amount": 0.5, "caption": "Docs that get you running"}]})
    assert plan["steps"][0]["caption"] == "" and plan["steps"][1]["caption"] == "Docs that get you running"


def test_role_hint_targets_are_recognised():
    from ship_reel.engine import browser as B
    m = B._ROLE_HINT_RE.match("button: Hermes : About Nous")
    assert m and m.group(1) == "button" and m.group(2) == "Hermes : About Nous"
    assert B._ROLE_HINT_RE.match("heading: Terminal Velocity").group(1) == "heading"
    assert B._ROLE_HINT_RE.match("Hermes : About Nous") is None  # a colon inside a label is not a hint
    assert B._ROLE_HINT_RE.match("css=.btn") is None


def test_dry_run_failure_suggests_nearby_labels():
    from ship_reel import tools
    inv = {"actions": [{"text": "INSTALL VIA TERMINAL"}, {"text": "Docs"}], "headings": [{"text": "Terminal Velocity"}]}
    msg = tools._suggest("step 1 did not work", {"target": "button: Install via terminl"}, inv)
    assert "INSTALL VIA TERMINAL" in msg
