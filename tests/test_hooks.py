from ship_reel import hooks
from ship_reel.engine import plan as P, store


def test_safe_plan_gets_no_directive():
    args = {"url": "https://x.dev", "brief": "b", "steps": [
        {"action": "click", "target": "Docs", "caption": "x"}, {"action": "scroll", "caption": "y"}]}
    assert hooks.pre_tool_call(tool_name="plan_reel", args=args) is None
    assert hooks.pre_tool_call(tool_name="plan_reel", args={"url": "https://x.dev", "brief": "b"}) is None


def test_risky_plan_requests_approval():
    args = {"url": "https://x.dev", "brief": "b", "steps": [
        {"action": "click", "target": "Docs", "caption": "x"}, {"action": "click", "target": "Sign in", "caption": "y"}]}
    r = hooks.pre_tool_call(tool_name="plan_reel", args=args, task_id="t", session_id="s")
    assert r["action"] == "approve" and "Sign in" in r["message"] and "x.dev" in r["message"]
    assert "step 2" in r["message"] and "Docs" not in r["message"]


def test_other_tools_ignored():
    assert hooks.pre_tool_call(tool_name="terminal", args={"command": "click Pay now"}) is None
    assert hooks.pre_tool_call(tool_name="edit_reel", args={"job_id": "x"}) is None
    assert hooks.pre_tool_call(tool_name="record_reel", args={"job_id": "missing-0000"}) is None


def test_record_and_fix_check_stored_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIP_REEL_HOME", str(tmp_path))
    job = store.create("https://x.dev", "b")
    plan, _ = P.normalize_plan({"url": "https://x.dev", "steps": [{"action": "click", "target": "Pay now", "caption": "x"}]})
    job["plan"] = plan
    store.save(job)
    for tool in ("record_reel", "fix_reel"):
        r = hooks.pre_tool_call(tool_name=tool, args={"job_id": job["id"]})
        assert r and r["action"] == "approve" and "Pay now" in r["message"]
    safe, _ = P.normalize_plan({"url": "https://x.dev", "steps": [{"action": "click", "target": "Pricing", "caption": "x"}]})
    job["plan"] = safe
    store.save(job)
    assert hooks.pre_tool_call(tool_name="record_reel", args={"job_id": job["id"]}) is None
