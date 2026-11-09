"""Use cases across features: extra sensors deciding when the night starts, settings changing what buttons do,
the rhythm editor changing tonight's plan, and the full click-path of the phone app's buttons over HTTP."""

import pytest
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.sources import SourceConfig, SourceManager
from embertide.units import f_to_c
from test_scenarios import Night, ts

H = {"X-Embertide": "1"}


def with_sensors(n: Night, *configs: SourceConfig) -> SourceManager:
    m = SourceManager(n.store, n.clock, lambda: n.bed)
    m.configs = list(configs)
    n.c.sources = m
    return m


async def run_with(n, m, until, readings, dt=30):
    """Step time; before every tick, feed each device the reading `readings(cfg, now)` returns."""
    while n.clock.now_ts() < ts(until):
        for c in m.configs:
            v = readings(c, n.clock.now_ts())
            if v is not None:
                m._sample(c, v, n.clock.now_ts())
        await n.step(1, dt)


# ---------- presence from extra devices ----------
async def test_radar_and_scale_start_the_night_when_the_bed_strip_misses_you(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:00")
    n.present("left", False)                                    # the topper's strip doesn't notice the sleeper
    radar = SourceConfig(name="r", driver="sim", role="presence", zone="left", kind="radar")
    scale = SourceConfig(name="s", driver="sim", role="weight", zone="left", kind="scale")
    m = with_sensors(n, radar, scale)
    await run_with(n, m, "2026-11-05T22:30", lambda c, t: 1 if c.kind == "radar" else 72)
    fz = n.c.fused["left"]
    assert fz["present"] and fz["score"] == pytest.approx((0.35 + 0.6) / (0.5 + 0.35 + 0.6), abs=0.01)
    assert n.e(n.p1).s.started                                   # the rhythm started from the sensors' vote


async def test_a_motion_sensor_alone_cannot_start_a_night(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:00")
    n.present("left", False)
    pir = SourceConfig(name="pir", driver="sim", role="presence", zone="left", kind="motion")
    m = with_sensors(n, pir)
    await run_with(n, m, "2026-11-05T22:40", lambda c, t: 1)  # someone walking around the bedroom
    assert not n.c.fused["left"]["present"] and not n.e(n.p1).s.started


async def test_scale_notices_you_getting_up(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:00")
    n.present("left", True)
    scale = SourceConfig(name="s", driver="sim", role="weight", zone="left", kind="scale", tuning={"weight": 0.9})
    m = with_sensors(n, scale)
    up_at = ts("2026-11-06T02:00")
    # in bed all evening; at 2 AM the bed strip still (wrongly) says occupied but the scale reads empty
    await run_with(n, m, "2026-11-06T03:00", lambda c, t: 72 if t < up_at else 2)
    assert n.e(n.p1).s.started
    assert n.c.fused["left"]["present"] is False                # the heavier scale outvotes the strip
    assert n.e(n.p1).s.state in ("out_of_bed_hold", "out_of_bed")


async def test_a_sensor_on_the_other_side_does_not_count(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:00")
    n.present("left", False)
    n.present("right", False)
    radar_r = SourceConfig(name="r", driver="sim", role="presence", zone="right", kind="radar", tuning={"weight": 1})
    m = with_sensors(n, radar_r)
    await run_with(n, m, "2026-11-05T22:30", lambda c, t: 1)
    assert n.c.fused["right"]["present"] and not n.c.fused["left"]["present"]
    assert n.e(n.p2).s.started and not n.e(n.p1).s.started


# ---------- settings change what buttons do ----------
async def test_flash_settings_change_the_flash(tmp_path):
    n = Night(tmp_path, "2026-11-06T01:00")
    n.present("left", True)
    await n.until("2026-11-06T01:10")
    s = n.p1.settings
    s.hot_flash = s.hot_flash.model_copy(update={"target_c": f_to_c(58), "duration_min": 25})
    f = n.e(n.p1).flash("hot", n.clock.now_ts())
    assert f.target_c == pytest.approx(f_to_c(58), abs=0.01) and f.end - f.start == 25 * 60
    s.hot_flash = s.hot_flash.model_copy(update={"relative": True, "delta_c": 3.0})
    base = n.e(n.p1).s.base_c
    f2 = n.e(n.p1).flash("hot", n.clock.now_ts())
    assert f2.target_c == pytest.approx(base - 3.0, abs=0.01)    # "3° cooler than now" instead of a fixed target


# ---------- the phone app's buttons, over HTTP ----------
def make(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False
    return TestClient(create_app(cfg))


def night(c, pid="p1"):
    return next(p for p in c.get("/api/snapshot").json()["people"] if p["id"] == pid)


def test_every_tonight_button_does_what_it_says(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T23:30", "scripted": False, "presence": {"left": True, "right": True}}, headers=H)
        c.post("/api/people/p1/tonight", json={"action": "start_now"}, headers=H)
        n = night(c)["night"]
        assert n["started"] and n["target_c"] is not None
        base = n["target_c"]
        # Hot flash → +5 min → Stop
        n = c.post("/api/people/p1/flash", json={"kind": "hot"}, headers=H).json()["people"][0]["night"]
        assert n["flash"]["kind"] == "hot" and n["target_c"] < base
        end = n["flash"]["end"]
        n = c.post("/api/people/p1/flash/extend", headers=H).json()["people"][0]["night"]
        assert n["flash"]["end"] > end
        n = c.post("/api/people/p1/flash/stop", headers=H).json()["people"][0]["night"]
        assert n["flash"]["end"] <= c.get("/api/snapshot").json()["now"] + 1
        # Cold flash
        n = c.post("/api/people/p1/flash", json={"kind": "cold"}, headers=H).json()["people"][0]["night"]
        assert n["flash"]["kind"] == "cold"
        c.post("/api/people/p1/flash/stop", headers=H)
        # Warmer / Cooler (until next phase) and Undo
        c.post("/api/people/p1/adjust", json={"delta_c": 0.56, "until": "next_phase"}, headers=H)
        c.post("/api/people/p1/adjust", json={"delta_c": 0.56, "until": "next_phase"}, headers=H)
        o = night(c)["night"]["override"]
        assert o["delta_c"] == pytest.approx(1.12, abs=0.01)
        c.post("/api/people/p1/adjust", json={"delta_c": -0.56, "until": "next_phase"}, headers=H)
        assert night(c)["night"]["override"]["delta_c"] == pytest.approx(0.56, abs=0.01)
        c.post("/api/people/p1/adjust/clear", headers=H)
        assert night(c)["night"]["override"] is None
        # Rest of the night
        c.post("/api/people/p1/adjust", json={"delta_c": -1.0, "until": "night"}, headers=H)
        assert night(c)["night"]["override"]["until"] == "night"
        c.post("/api/people/p1/adjust/clear", headers=H)
        # Pause for an hour → Resume
        n = c.post("/api/people/p1/tonight", json={"action": "pause", "minutes": 60}, headers=H).json()["people"][0]["night"]
        assert n["state"] == "paused" and n["target_c"] is None
        n = c.post("/api/people/p1/tonight", json={"action": "resume"}, headers=H).json()["people"][0]["night"]
        assert n["state"] != "paused" and n["target_c"] is not None
        # Off tonight → back on
        n = c.post("/api/people/p1/tonight", json={"action": "skip"}, headers=H).json()["people"][0]["night"]
        assert n["state"] == "skipped" and n["target_c"] is None
        n = c.post("/api/people/p1/tonight", json={"action": "resume"}, headers=H).json()["people"][0]["night"]
        assert n["target_c"] is not None
        # How does it feel
        assert c.post("/api/people/p1/feel", json={"feel": "too_hot"}, headers=H).json()["logged"] == "too_hot"
        # the partner's side never moved
        assert night(c, "p2")["night"]["override"] is None and night(c, "p2")["night"]["flash"] is None


def test_rhythm_editor_changes_tonights_plan(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T14:00", "scripted": False}, headers=H)
        rh = c.get("/api/people/p1/rhythms").json()
        rhythms = rh["rhythms"] if isinstance(rh, dict) else rh
        wk = next(r for r in rhythms if 4 in r["days"])          # Friday's rhythm
        deep = next(p for p in wk["phases"] if p["id"] == "deep")
        deep["temp_c"] = round(deep["temp_c"] - 1.0, 2)
        r = c.put(f"/api/people/p1/rhythms/{wk['id']}", json=wk, headers=H)
        assert r.status_code == 200, r.text
        plan = {p["id"]: p["temp_c"] for p in night(c)["phases"]}
        assert plan["deep"] == pytest.approx(deep["temp_c"], abs=0.01)


def test_settings_save_and_apply(tmp_path):
    with make(tmp_path) as c:
        s = night(c)["settings"]
        s["units"], s["simple_default"], s["notify"]["left_on"] = "C", True, False
        s["hot_flash"]["duration_min"] = 20
        r = c.put("/api/people/p1/settings", json=s, headers=H)
        assert r.status_code == 200, r.text
        got = night(c)["settings"]
        assert got["units"] == "C" and got["simple_default"] and got["notify"]["left_on"] is False and got["hot_flash"]["duration_min"] == 20
        c.post("/api/sim", json={"jump_to": "2026-11-06T23:30", "scripted": False, "presence": {"left": True}}, headers=H)
        n = c.post("/api/people/p1/flash", json={"kind": "hot"}, headers=H).json()["people"][0]["night"]
        assert n["flash"]["end"] - n["flash"]["start"] == pytest.approx(20 * 60, abs=1)   # the new duration is used
        bad = c.put("/api/people/p1/settings", json=s | {"hot_flash": s["hot_flash"] | {"target_c": 99}}, headers=H)
        assert bad.status_code == 422                            # out-of-range values are refused, not saved
