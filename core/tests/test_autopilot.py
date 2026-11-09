"""Autopilot: what it notices, why, what stops it, and what it does in each mode."""

import pytest
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.engine.autopilot import Window, evaluate
from embertide.models import AutopilotPolicy
from test_scenarios import Night, ts

H = {"X-Embertide": "1"}


def win(now=10_000.0, **kw):
    base = dict(now=now, rows=[], in_bed=now - 7200, state="rhythm", phase="deep", flash_active=False, events=[], skin=[],
                persona="tide", wake=now + 5 * 3600)
    base.update(kw)
    return Window(**base)


def rows(now, n=40, status=0, hr=58, target=17.0, measured=17.2, step=15):
    return [{"ts": now - (n - i) * step, "status": status, "hr": hr, "target": target, "measured": measured, "occupied": 1, "phase": "deep"}
            for i in range(n)]


def test_quiet_night_does_nothing():
    d = evaluate(win(rows=rows(10_000)), AutopilotPolicy(), 17.0, [])
    assert d.direction == "none" and d.status == "watching" and d.score == 0


def test_too_hot_tap_is_enough_and_explained():
    w = win(events=[{"kind": "feel", "ts": 9_900, "feel": "too_hot"}], rows=rows(10_000))
    d = evaluate(w, AutopilotPolicy(mode="suggest"), 17.0, [])
    assert d.direction == "cooler" and d.status == "suggested" and d.delta_c < 0
    assert d.reasons[0].signal == "feedback" and "tapped" in d.reasons[0].detail


def test_restless_alone_waits_for_a_second_signal():
    now = 10_000
    r = rows(now, n=200) [:-40] + rows(now, n=40, status=4)           # last 10 min: moving
    d = evaluate(win(rows=r), AutopilotPolicy(sensitivity="high"), 17.0, [])
    assert d.direction == "none" or "second" in " ".join(d.blocked_by) or d.status == "watching"


def test_skin_temperature_and_restlessness_together_mean_too_warm():
    now = 20_000
    r = rows(now, n=400, hr=56)[:-40] + rows(now, n=40, status=4, hr=66)
    skin = [(now - 7000 + i * 120, 34.0) for i in range(50)] + [(now - 500 + i * 60, 34.7) for i in range(8)]
    d = evaluate(win(now=now, rows=r, skin=skin, in_bed=now - 7400), AutopilotPolicy(), 17.0, [])
    sigs = {x.signal for x in d.reasons}
    assert {"skin_temp", "movement", "hr"} <= sigs and d.direction == "cooler"


def test_cold_skin_and_ember_restlessness_mean_warmer():
    now = 20_000
    r = rows(now, n=400)[:-40] + rows(now, n=40, status=6)
    skin = [(now - 7000 + i * 120, 34.2) for i in range(50)] + [(now - 500 + i * 60, 33.5) for i in range(8)]
    d = evaluate(win(now=now, rows=r, skin=skin, persona="ember", in_bed=now - 7400), AutopilotPolicy(), 17.0, [])
    assert d.direction == "warmer" and d.delta_c > 0


def test_warm_humid_room_counts():
    now = 10_000
    room = [(now - 600 + i * 60, 26.0) for i in range(10)]
    hum = [(now - 600 + i * 60, 70.0) for i in range(10)]
    w = win(rows=rows(now), room=room, humidity=hum, events=[{"kind": "feel", "ts": now - 60, "feel": "too_hot"}])
    d = evaluate(w, AutopilotPolicy(), 17.0, [])
    assert sum(1 for x in d.reasons if x.signal == "room") == 2


@pytest.mark.parametrize("change,why", [
    (dict(phase="wake"), "not allowed"),
    (dict(flash_active=True), "flash"),
    (dict(events=[{"kind": "feel", "ts": 9_900, "feel": "too_hot"}, {"kind": "adjust", "ts": 9_500, "source": "app"}]), "yourself"),
    (dict(wake=10_000 + 600), "quiet time"),
])
def test_guardrails_block_and_say_why(change, why):
    base = dict(events=[{"kind": "feel", "ts": 9_900, "feel": "too_hot"}], rows=rows(10_000))
    base.update(change)
    d = evaluate(win(**base), AutopilotPolicy(), 17.0, [])
    assert d.status == "blocked" and any(why in b for b in d.blocked_by), d.blocked_by


def test_limits_per_night():
    w = win(events=[{"kind": "feel", "ts": 9_900, "feel": "too_hot"}], rows=rows(10_000))
    pol = AutopilotPolicy(max_actions=2, max_delta_c=1.0, min_gap_min=20)
    hist = [{"ts": 1_000, "status": "applied", "delta_c": -0.56}, {"ts": 3_000, "status": "applied", "delta_c": -0.56}]
    d = evaluate(w, pol, 17.0, hist)
    assert d.status == "blocked" and any("already 2" in b for b in d.blocked_by) and any("limit for tonight" in b for b in d.blocked_by)
    d2 = evaluate(w, AutopilotPolicy(), 14.6, [])                      # floor is 14.5 °C
    assert d2.status == "blocked" and any("never-below" in b for b in d2.blocked_by)


async def test_suggest_logs_and_act_applies(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.p1.settings.autopilot.learn_nights = 0
    n.present("left", True)
    await n.until("2026-11-06T00:30")                                     # Deep
    e = n.e(n.p1)
    before = e.s.target_c
    n.store.event(n.clock.now_ts(), "feel", "p1", "app", feel="too_hot", phase="deep")
    n.c.autopilot._last.clear()
    await n.step(2)
    recs = n.events("autopilot")
    assert recs and recs[-1]["status"] == "suggested" and e.s.target_c == pytest.approx(before, abs=0.05)
    # now let it act
    n.p1.settings.autopilot.mode = "act"
    n.c.autopilot._last.clear()
    n.c.autopilot._last_logged_decision.clear()
    await n.until("2026-11-06T00:52")                                     # past the 20 min gap
    n.store.event(n.clock.now_ts(), "feel", "p1", "app", feel="too_hot", phase="deep")
    n.c.autopilot._last.clear()
    n.c.autopilot._last_logged_decision.clear()
    await n.step(2)
    applied = [x for x in n.events("autopilot") if x["status"] == "applied"]
    assert applied and e.s.override is not None and e.s.override.source == "autopilot"
    assert e.s.target_c < before


def make(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False
    return TestClient(create_app(cfg))


def test_api_feel_replay_wearable_recheck_trends(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T23:30"}, headers=H)
        assert c.post("/api/people/p1/feel", json={"feel": "too_hot", "note": "sweaty feet"}, headers=H).status_code == 200
        assert c.post("/api/people/p1/feel", json={"feel": "lava"}, headers=H).status_code == 422
        st = c.get("/api/people/p1/autopilot").json()
        assert st["policy"]["mode"] == "suggest" and "room" in st["policy"]["signals"]
        night = c.get("/api/people/p1/autopilot/night?date=2026-11-05").json()
        assert night["feel"] and night["feel"][0]["note"] == "sweaty feet"
        rp = c.post("/api/people/p1/autopilot/replay", json={"date": "2026-11-05", "policy": {"sensitivity": "high"}}, headers=H).json()
        assert rp["policy"]["sensitivity"] == "high" and rp["policy"]["mode"] == "suggest"
        base = ts("2026-11-05T22:00")
        pts = [[base + i * 300, 34.0 + (0.8 if i > 6 else 0)] for i in range(10)]
        w = c.post("/api/people/p1/wearable", json={"device": "Test watch", "metric": "skin_temp", "points": pts}, headers=H).json()
        assert w["stored"] == 10 and w["rechecked"] and w["rechecked"][0]["date"] == "2026-11-05"
        bad = c.post("/api/people/p1/wearable", json={"device": "x", "metric": "skin_temp", "points": [[base, 99]]}, headers=H).json()
        assert bad["stored"] == 0                                         # 99 °C isn't a skin temperature
        f = c.post("/api/people/p1/wearable", json={"device": "Oura", "metric": "skin_temp", "relative": True, "points": [[base, 0.4]]}, headers=H).json()
        assert f["stored"] == 1
        tr = c.get("/api/people/p1/autopilot/trends").json()
        assert tr and tr[0]["title"]
        rv = c.post("/api/people/p1/autopilot/ai-review", json={"date": "2026-11-05"}, headers=H).json()
        assert "built-in" in rv["answer"]                                 # no model connected in tests
        assert c.get("/api/people/p1/autopilot/ai-review?date=2026-11-05").json()["answer"] == rv["answer"]
        fb = c.post("/api/people/p1/feedback", json={"date": "2026-11-05", "temp": "warm", "note": "woke up hot around 3"}, headers=H)
        assert fb.status_code == 200
        fb2 = c.post("/api/people/p1/feedback", json={"date": "2026-11-05", "rested": 3}, headers=H)
        assert fb2.status_code == 200
        assert c.get("/api/people/p1/autopilot/night?date=2026-11-05").json()["feedback"]["note"] == "woke up hot around 3"


def test_old_saved_settings_are_migrated():
    old = AutopilotPolicy.model_validate({"mode": "suggest", "signals": ["bed_hr", "movement", "room_temp"]})
    assert {"feedback", "skin_temp", "hr", "room", "movement", "flashes", "pad"} <= set(old.signals)
    assert old.signals.count("hr") == 1


async def test_morning_suggestion_from_own_changes_and_cadence(tmp_path):
    from embertide.autopilot_api import _release, _suggest
    n = Night(tmp_path, "2026-11-05T22:20")
    n.p1.settings.autopilot.learn_nights = 0
    n.present("left", True)
    await n.until("2026-11-06T00:30")                                     # Deep
    e = n.e(n.p1)
    for _ in range(2):                                                    # two presses on the dial: 2 °F cooler
        e.adjust(n.clock.now_ts(), delta_c=-0.56, until="next_phase", source="ha")
        await n.step(1)
    await n.until("2026-11-06T09:00")                                     # morning
    items = _suggest(n.c, n.p1)
    man = [x for x in items if x["kind"] == "manual"]
    assert man and man[0]["phase"] == "deep" and "2°F cooler" in man[0]["title"] and "dial" in man[0]["detail"]
    deep = next(p for p in n.p1.rhythms[0].phases if p.id == "deep")
    assert man[0]["proposal"]["to"] == pytest.approx(deep.temp_c - 1.12, abs=0.01)
    # weekly cadence holds them until the chosen morning (2026-11-06 is a Friday = 4)
    n.p1.settings.autopilot.suggest_cadence = "weekly"
    n.p1.settings.autopilot.suggest_weekday = 6
    rel = _release(n.c, n.p1, items)
    assert rel["items"] == [] and rel["waiting"] == len(items) and rel["next"] == "2026-11-08"
    assert _release(n.c, n.p1, items, show_all=True)["items"]
    n.p1.settings.autopilot.suggest_weekday = 4
    assert _release(n.c, n.p1, items)["items"]
    # "don't suggest my own changes" turns that kind off for good
    n.p1.settings.autopilot.suggest_manual = False
    assert not [x for x in _release(n.c, n.p1, _suggest(n.c, n.p1))["items"] if x["kind"] == "manual"]
