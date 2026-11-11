from ship_reel.engine import timeline as T


def synthetic_events(t0=1000.0, nav=True):
    # 30 s recording at 50 fps, two steps: a click that navigates, then a scroll
    frames = [[t0 + i / 50, f"{i:06d}.jpg"] for i in range(30 * 50)]
    ev = {
        "t0": t0, "t1": t0 + 30, "viewport": [1440, 810], "dpr": 2, "frames": frames,
        "cursor": [[t0, 720, 500], [t0 + 4, 300, 200], [t0 + 30, 300, 200]],
        "clicks": [{"t": t0 + 4.2, "x": 300, "y": 200}],
        "keys": [],
        "intervals": [
            {"kind": "dwell", "t0": t0, "t1": t0 + 2},
            {"kind": "nav", "t0": t0 + 4.6, "t1": t0 + 8.0} if nav else {"kind": "dwell", "t0": t0 + 5, "t1": t0 + 6},
            {"kind": "scroll", "t0": t0 + 9, "t1": t0 + 10.5},
            {"kind": "dwell", "t0": t0 + 10.5, "t1": t0 + 13},
            {"kind": "fast", "t0": t0 + 13, "t1": t0 + 28},
        ],
        "urls": [[t0, "https://a.dev/"], [t0 + 8, "https://a.dev/docs"]],
        "steps": [
            {"index": 0, "ok": True, "t_start": t0 + 2, "t_end": t0 + 9, "t_action_end": t0 + 8, "focus": [300, 200], "caption": "Open docs"},
            {"index": 1, "ok": True, "t_start": t0 + 9, "t_end": t0 + 13, "caption": "Scroll"},
            {"index": 2, "ok": False, "t_start": t0 + 13, "t_end": t0 + 28, "caption": "Broken"},
        ],
    }
    return ev


PLAN = {"steps": [{"caption": "Open docs", "zoom": True, "zoom_level": 1.6}, {"caption": "Scroll", "zoom": False}, {"caption": "Broken"}]}


def test_segments_cut_nav_and_failed_steps():
    ev = synthetic_events()
    segs = T.build_segments(ev)
    modes = [s.mode for s in segs]
    assert modes.count("xfade") == 2  # nav + failed step
    # contiguous coverage of source time
    for a, b in zip(segs, segs[1:]):
        assert abs(a.s1 - b.s0) < 1e-6
    assert abs(segs[0].s0 - ev["t0"]) < 1e-9 and abs(segs[-1].s1 - ev["t1"]) < 1e-9
    body = T.body_length(segs)
    assert body < 30 - 3.4 - 15 + 2 * T.XFADE_D + 0.01


def test_time_maps_roundtrip():
    ev = synthetic_events(nav=False)
    segs = T.build_segments(ev, dwell_rate=1.5)
    for s in [ev["t0"] + x for x in (0.5, 3.0, 9.7, 12.0)]:
        o = T.out_of_src(segs, s)
        kind, back = T.src_at(segs, o)[:2]
        assert kind == "play" and abs(back - s) < 1e-6


def test_fast_intervals_are_time_lapsed():
    ev = synthetic_events(nav=False)
    ev["steps"][2]["ok"] = True
    segs = T.build_segments(ev)
    fast = [s for s in segs if s.mode == "play" and s.s0 >= ev["t0"] + 13 - 1e-6 and s.s1 <= ev["t0"] + 28 + 1e-6]
    assert fast and all(s.rate >= 2.5 for s in fast)
    assert abs(sum(s.out_len for s in fast) - 4.5) < 0.05


def test_camera_clamp_stays_in_window():
    L = T.make_layout(1920, 1080)
    cx, cy = T.clamp_camera(1.2, 0, 0, L)  # view wider than the window: stays on the canvas
    assert cx - 1920 / 1.2 / 2 >= -1e-6 and cy - 1080 / 1.2 / 2 >= -1e-6
    for z in (1.6, 2.2):
        cx, cy = T.clamp_camera(z, 0, 0, L)
        assert cx - 1920 / z / 2 >= L.win_x - 1e-6
        assert cy - 1080 / z / 2 >= L.win_y - 1e-6
        cx, cy = T.clamp_camera(z, 5000, 5000, L)
        assert cx + 1920 / z / 2 <= L.win_x + L.win_w + 1e-6
    assert T.clamp_camera(1.0, 3, 3, L) == (960, 540)


def test_build_plan_landscape_and_vertical():
    ev = synthetic_events()
    for W, H in ((1920, 1080), (1080, 1920)):
        rp = T.build(ev, PLAN, W, H, fps=30)
        assert rp.duration <= T.MAX_SECONDS
        assert len(rp.frames) == round(rp.duration * 30)
        phases = [f["phase"] for f in rp.frames]
        assert phases[0] == "title" and phases[-1] == "end" and "body" in phases
        # captions: only successful steps, ordered, readable, non-overlapping fade windows
        assert [c["text"] for c in rp.captions] == ["Open docs", "Scroll"]
        for c in rp.captions:
            assert c["b"] - c["a"] >= 1.2
        on_screen = [len(f.get("cap", [])) for f in rp.frames]
        assert max(on_screen) == 1
        # camera zooms in for the click and settles back
        zs = [f["cam"][0] for f in rp.frames if "cam" in f]
        assert max(zs) > 1.3 * (1.5 if H > W else 1.0) - 0.2
        assert rp.clicks_out and rp.xfades_out


def test_build_fits_budget_by_speeding_up():
    ev = synthetic_events()
    # stretch the recording: 300 s of dwell
    ev["t1"] = ev["t0"] + 300
    ev["intervals"].append({"kind": "dwell", "t0": ev["t0"] + 30, "t1": ev["t0"] + 300})
    ev["frames"] = [[ev["t0"] + i / 10, f"{i:06d}.jpg"] for i in range(3000)]
    rp = T.build(ev, PLAN, 1280, 720, fps=30, max_seconds=60)
    assert rp.duration <= 60.05
    assert rp.speed > 1.0


def test_smoothdamp_converges_without_overshoot():
    sd = T.SmoothDamp(1.0)
    vals = [sd.step(2.0, 1 / 30) for _ in range(90)]
    assert abs(vals[-1] - 2.0) < 0.01
    assert max(vals) <= 2.0 + 1e-6
