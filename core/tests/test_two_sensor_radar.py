"""Presence for both sides from two single-zone sensors, one by each nightstand (e.g. two Aqara FP1E): saved as one
group with layout "two_sensors", so Edit and Remove act on the pair; the Home Assistant picker offers only on/off
occupancy sensors; and fusion allows for each sensor seeing a little of the partner (crosstalk)."""

import time

import httpx
import pytest

from embertide.ha_connection_api import fit_rank
from test_device_setup_sims import TOKEN, FakeHA, _fast_sleep, ent, wait
from test_autopilot_suite import H, make

FP1E_L = "binary_sensor.fp1e_left_occupancy"
FP1E_R = "binary_sensor.fp1e_right_presence"


@pytest.fixture
def world(tmp_path):
    with make(tmp_path) as c:
        ha = FakeHA()
        ha.states[FP1E_L] = ent(FP1E_L, "off", "Nightstand left occupancy", device_class="occupancy")
        ha.states[FP1E_R] = ent(FP1E_R, "off", "Nightstand right presence", device_class="presence")
        ctl = c.app.state.notifier.c
        ctl.sources.http_transport = httpx.MockTransport(ha)
        ctl.sources._sleep = _fast_sleep
        assert c.put("/api/ha/connection", headers=H, json={"url": "http://ha.test:8123", "token": TOKEN}).status_code == 200
        yield c, ha, ctl


def pair(left, right, ids=None, **kw):
    ids = ids or {}
    parts = [{"zone": z, "params": {"conn": "ha", "entity_id": e}, **({"id": ids[z]} if z in ids else {})}
             for z, e in (("left", left), ("right", right)) if e]
    return {"name": "Nightstand radars", "kind": "radar", "driver": "ha-entity", "device": "Aqara FP1E",
            "layout": "two_sensors", "parts": parts, **kw}


def test_two_sensors_are_one_device_add_swap_remove(world):
    c, ha, ctl = world
    r = c.post("/api/sources/group", headers=H, json=pair(FP1E_L, FP1E_R))
    assert r.status_code == 200, r.text
    gid, parts = r.json()["group"], r.json()["sources"]
    assert r.json()["layout"] == "two_sensors"
    assert {(s["zone"], s["params"]["entity_id"], s["params"]["layout"]) for s in parts} == {
        ("left", FP1E_L, "two_sensors"), ("right", FP1E_R, "two_sensors")}
    assert all(s["group"] == gid and s["votes"] for s in parts)
    ids = {s["zone"]: s["id"] for s in parts}
    # Edit: swap sides. The layout is kept even when Edit doesn't send it, and each side keeps its id (history)
    body = pair(FP1E_R, FP1E_L, ids=ids)
    body.pop("layout")
    sw = c.put(f"/api/sources/group/{gid}", headers=H, json=body)
    assert sw.status_code == 200, sw.text
    assert {(s["id"], s["params"]["entity_id"], s["params"]["layout"]) for s in sw.json()["sources"]} == {
        (ids["left"], FP1E_R, "two_sensors"), (ids["right"], FP1E_L, "two_sensors")}
    # turning it into a zoned radar drops the marker
    z = c.put(f"/api/sources/group/{gid}", headers=H, json=pair(FP1E_R, FP1E_L, ids=ids, layout="zones"))
    assert all("layout" not in s["params"] for s in z.json()["sources"])
    # Remove takes both sides
    assert c.delete(f"/api/sources/group/{gid}", headers=H).json()["removed"] == 2
    assert not [s for s in c.get("/api/sources").json()["sources"] if s["group"] == gid]


def test_the_same_sensor_on_both_sides_is_refused(world):
    c, ha, ctl = world
    r = c.post("/api/sources/group", headers=H, json=pair(FP1E_L, FP1E_L))
    assert r.status_code == 422 and "same sensor" in r.json()["detail"]
    # and a scale can't be "two presence sensors"
    bad = c.post("/api/sources/group", headers=H, json=pair("sensor.bed_scale_left", "sensor.bed_scale_right", kind="scale"))
    assert bad.status_code == 422


def test_picker_offers_only_occupancy_sensors():
    def row(eid, dc=None, unit=None):
        return {"entity_id": eid, "name": eid, "device_class": dc, "unit": unit}
    assert fit_rank("radar_single", row(FP1E_L, "occupancy")) == 0
    assert fit_rank("radar_single", row(FP1E_R, "presence")) == 0
    assert fit_rank("radar_single", row("binary_sensor.fp1e_occupancy")) is not None     # no device class, but its name says so
    assert fit_rank("radar_single", row("sensor.fp2_zone_1_target_count")) is None
    assert fit_rank("radar_single", row("binary_sensor.hall_motion", "motion")) is None
    assert fit_rank("radar_single", row("binary_sensor.embertide_alex_in_bed", "occupancy")) is None


def _setup(c, ha, ctl, weight=0.9):
    for x in c.get("/api/sources").json()["sources"]:                 # the bed strip alone to begin with
        c.delete(f"/api/sources/{x['id']}", headers=H)
    ctl.driver.scripted = False
    tune = {"weight": weight, "min_on_s": 0, "hold_s": 0}
    r = c.post("/api/sources/group", headers=H, json=pair(FP1E_L, FP1E_R, tuning=tune)).json()
    return {s["zone"]: s["id"] for s in r["sources"]}


def _both_fire(ha, ctl, ids, left="on", right="on"):
    ha.set(FP1E_L, left)
    ha.set(FP1E_R, right)
    wait(ctl, ids["left"], lambda l: l.get("value") == (1.0 if left == "on" else 0.0), timeout=10)
    wait(ctl, ids["right"], lambda l: l.get("value") == (1.0 if right == "on" else 0.0), timeout=10)
    time.sleep(0.4)


def _strip(ctl, left, right):
    ctl.driver.set_presence("left", left)
    ctl.driver.set_presence("right", right)
    # start the strip's filter fresh: the simulator's scripted sleepers may have been "in bed" a moment ago (the test
    # clock is the real time of day), and an occupied side only lets go after its hold time
    ctl.presence.sides.clear()
    t = time.time()
    while time.time() - t < 10 and (ctl.presence.present("left"), ctl.presence.present("right")) != (left, right):
        time.sleep(0.05)
    # the bed strip runs on the app's real-time loop: on a busy machine it can take a while; never go on unsettled
    assert (ctl.presence.present("left"), ctl.presence.present("right")) == (left, right), "bed strip didn't settle"


def test_partner_turning_over_is_not_a_false_in_bed(world):
    c, ha, ctl = world
    ids = _setup(c, ha, ctl)                 # radar trusted a lot (0.9): alone it would outvote the strip (0.5)
    _strip(ctl, True, False)                 # the left sleeper is in bed, the right side is empty
    _both_fire(ha, ctl, ids)                 # the left sleeper turns over: both nightstand sensors see it
    assert ctl.fuse("left") is True, ctl.fused.get("left")
    assert ctl.fuse("right") is False, ctl.fused.get("right")
    f = ctl.fused["right"]
    assert f["crosstalk"] == [f"presence:{ids['right']}"]
    assert all(v["signal"] != f"presence:{ids['right']}" for v in f["votes"])


def test_crosstalk_rule_stays_narrow(world):
    c, ha, ctl = world
    ids = _setup(c, ha, ctl)
    _strip(ctl, False, False)
    # only this side's sensor fires (partner quiet): not crosstalk, the trusted radar keeps its say
    _both_fire(ha, ctl, ids, left="off", right="on")
    assert ctl.fuse("right") is True, ctl.fused.get("right")
    assert "crosstalk" not in ctl.fused["right"]


def test_a_real_in_bed_is_still_detected(world):
    c, ha, ctl = world
    ids = _setup(c, ha, ctl, weight=0.35)    # recommended weight
    _strip(ctl, True, True)                  # both in bed: both sensors fire, both strips agree
    _both_fire(ha, ctl, ids)
    assert ctl.fuse("left") is True and ctl.fuse("right") is True
    assert "crosstalk" not in ctl.fused["left"] and "crosstalk" not in ctl.fused["right"]
    # the right sleeper gets in before the strip notices, but a scale on that side sees the weight: radar kept
    sc = c.post("/api/sources", headers=H, json={"name": "Scale right", "driver": "ha-entity", "role": "weight", "kind": "scale",
                                                 "zone": "right", "tuning": {"min_on_s": 0}, "params": {"conn": "ha", "entity_id": "sensor.bed_scale_right"}}).json()["id"]
    _strip(ctl, True, False)
    for v in ("20", "40", "60", "80"):
        ha.set("sensor.bed_scale_right", v)
        time.sleep(0.15)
    wait(ctl, sc, lambda l: (l.get("value") or 0) > 60)
    time.sleep(0.4)
    assert ctl.fuse("right") is True, ctl.fused.get("right")
    assert "crosstalk" not in ctl.fused["right"]
