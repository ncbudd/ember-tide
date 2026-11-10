"""The bed's control unit ("hub"): status parsing, plain-language faults and water, who sees what, the maintenance
routes, targets going back out after a restart, and the hub notifications. Everything here is synthetic."""

import json

import pytest
from fastapi.testclient import TestClient

from embertide import hub as tw
from embertide.app import create_app
from embertide.config import Config
from embertide.drivers.base import Cap, CommandError, HubHealth
from embertide.drivers.orion_mqtt import FAULT_CODES, ZONE_FAULTS, OrionMqtt, parse_status, validate
from embertide.drivers.sim import SimBed
from embertide.clock import FixedClock
from sim_edges import Rig, ts
from test_power_loss import both_in_bed, with_events

H = {"X-Embertide": "1"}
PW = "correct horse battery"
SERIAL = "TESTUNIT01"
NAME, ADDR = "Test Network", "192.0.2.77"


def status(**over) -> dict:
    """A status message in the shape the unit sends, with invented identifiers."""
    raw = {
        "type": "status", "serial": SERIAL,
        "wifi": {"rssi": -58, "ip": ADDR, "ssid": NAME},
        "temps": {"ib_left": 21.5, "ib_right": 21.4},
        "water": {"reservoir": 1, "ctank_left": 1, "ctank_right": 1, "ctank_left_filtered": 1, "ctank_right_filtered": 1},
        "ambient": {"humidity_filtered": 36.9},
        "tec": {"left": {"setpoint": 29.4, "control_enabled": False}, "right": {"setpoint": 30.0, "control_enabled": False}},
        "fans": {"pwm": 100, "left_rpm": 2400, "right_rpm": 2390, "quiet_mode": False},
        "led": {"idle_r": 255, "idle_g": 255, "idle_b": 255, "led_brightness": 40},
        "system_state": "SYSTEM_ON",
        "fw": {"build": "Jan 1 2026 10:00:00", "version": "2.6.2", "ib_version": "2.6.1", "free_heap": 58912},
        "uptime_ms": 7_200_000,
        "ptc_ok": True, "rtec_ok": True, "ltec_ok": True, "safetyOk": "true",
        "errors": {"PUMP_CURRENT": 0, "FAN_RPM_OUT_OF_RANGE": 0, "IB_COMM_NOT_OK": 0, "RTEC_FAULT_DETECTED": 0, "LTEC_FAULT_DETECTED": 0},
        "previous_command_received": "SYSTEM ON",
    }
    raw.update(over)
    return raw


def parse(raw: dict):
    out = parse_status(f"orion/{SERIAL}/out", json.dumps(raw).encode(), 1000.0)
    assert out is not None
    return out[1]


# ---------- the parser ----------
def test_parses_a_healthy_unit():
    f = parse(status())
    t = f.hub
    assert t.water_ok is True and t.faults == [] and t.safety_ok is True and f.fault is False
    assert (t.wifi_dbm, t.wifi_name, t.wifi_address) == (-58, NAME, ADDR)
    assert t.uptime_s == 7200 and t.fw_version == "2.6.2" and t.fw_panel == "2.6.1" and t.fw_build.startswith("Jan 1")
    assert t.last_command == "SYSTEM ON" and t.fan_rpm == {"left": 2400, "right": 2390} and t.fan_pwm == 100
    assert t.humidity == 36.9 and t.free_memory_kb == 57
    assert tw.status_line(t) == "All good"


def test_water_only_the_normal_value_is_ok():
    ok = lambda w: parse(status(water=w)).hub.water_ok          # noqa: E731
    assert ok({"reservoir": 1, "ctank_left": 1}) is True
    assert ok({"reservoir": 0, "ctank_left": 1}) is False
    assert ok({"reservoir": 1, "ctank_left": 2}) is False         # any value other than the normal one: "check the water"
    assert ok({}) is None and parse(status(water="odd")).hub.water_ok is None
    t = parse(status(water={"reservoir": 0, "ctank_left": 1})).hub
    assert t.water_raw == {"reservoir": 0, "ctank_left": 1} and tw.status_line(t) == "Check the water"


def test_every_fault_flag_maps_to_plain_words():
    for flag, code in FAULT_CODES.items():
        t = parse(status(errors={flag: 1})).hub
        assert t.faults == [(code, None)], flag
        assert tw.fault_name(code) != tw.UNKNOWN_FAULT[0], flag
        advice = tw.fault_advice(code).lower()
        assert advice and not any(w in advice for w in ("open the", "unscrew", "remove the cover", "take apart"))
    for flag, zone in ZONE_FAULTS.items():
        assert parse(status(errors={flag: 1})).hub.faults == [("zone_fault", zone)]
    assert tw.fault_advice("fan_speed") == "Check the hub's vents aren't blocked."
    assert tw.fault_name("fan_speed") == "Fan speed out of range"
    # an unheard-of flag is still a problem, in generic words
    t = parse(status(errors={"SOMETHING_NEW": 1})).hub
    assert t.faults == [("unknown", None)] and tw.status_line(t) == "The hub reported a problem"


def test_status_flags_and_the_old_any_error_boolean():
    f = parse(status(ptc_ok=False, rtec_ok=False, errors={"PUMP_CURRENT": 0}))
    assert ("power", None) in f.hub.faults and ("zone_fault", "right") in f.hub.faults
    assert parse(status(errors={"PUMP_CURRENT": 1})).fault is True                     # BedFrame.fault is unchanged
    assert parse(status()).fault is False
    assert parse(status(safetyOk="false")).hub.safety_ok is False and parse(status(safetyOk=True)).hub.safety_ok is True
    assert tw.status_line(parse(status(safetyOk="false")).hub) == "The hub's safety check isn't passing"
    # two flags for the same thing are one fault
    both = parse(status(errors={"IB_COMM_NOT_OK": 1, "NO_IB_VERSION": 1})).hub
    assert tw.summary(both)["faults"] == [{"code": "panel_link", "name": "Light panel isn't answering", "zone": None}]


def test_the_first_fault_is_the_status_line_and_beats_the_water():
    t = parse(status(water={"reservoir": 0}, errors={"FAN_RPM_OUT_OF_RANGE": 1})).hub
    assert tw.status_line(t) == "Fan speed out of range"


def test_wifi_in_words():
    assert [tw.wifi_words(x) for x in (-40, -65, -75, -90, None)] == ["Strong", "Good", "Fair", "Weak", None]


def test_command_acknowledgement_match():
    assert tw.same_command("set left 29.40", "SET LEFT 29.4") and tw.same_command("FAN  QUIET ON", "FAN QUIET ON")
    assert not tw.same_command("SYSTEM ON", "FAN QUIET ON") and not tw.same_command(None, "SYSTEM ON")


# ---------- the allowlist ----------
def test_new_commands_allowed_and_dangerous_ones_still_blocked():
    assert validate("fill start") == "FILL START" and validate("reboot") == "REBOOT"
    assert validate("IB DIMMER 40") == "IB DIMMER 40" and validate("IB RGB 1 2 3") == "IB RGB 1 2 3"
    for bad in ("OTA START", "ERASE WIFI", "IB UPDATE", "FILLVALVE ON", "AIRVALVE ON", "RPUMP ON", "LPUMP ON", "LTEC 50",
                "RTEC 50", "FAN PWM 50", "FILL STOP", "IB DIMMER 101"):
        with pytest.raises(CommandError):
            validate(bad)


# ---------- who sees what ----------
def test_summary_and_snapshot_never_carry_the_network_name_or_address():
    t = parse(status(errors={"PUMP_CURRENT": 1})).hub
    assert NAME not in json.dumps(tw.summary(t)) and ADDR not in json.dumps(tw.summary(t))
    d = tw.detail(t, untried=["water_fill"])
    assert d["wifi"] == {"dbm": -58, "words": "Strong", "name": NAME, "address": ADDR}
    assert d["faults"][0]["advice"] and d["untried"] == ["water_fill"]


def test_the_acknowledgement_reaches_the_driver(monkeypatch):
    d = OrionMqtt("mqtt://localhost:1883", SERIAL)
    d.sent, d.acked = ("SET LEFT 29.4", 1.0), False
    d._check_ack(parse(status(previous_command_received="SYSTEM ON")))
    assert d.acked is False
    d._check_ack(parse(status(previous_command_received="SET LEFT 29.40")))
    assert d.acked is True


# ---------- the simulator ----------
def test_sim_reports_health_and_can_inject_a_fault_and_low_water():
    bed = SimBed(FixedClock(ts("2026-11-05T22:00"), "America/New_York"))
    bed._tick()
    assert bed.latest().hub.water_ok is True and bed.latest().hub.faults == [] and bed.latest().fault is False
    bed.low_water = True
    bed.inject_fault("fan_speed")
    bed._tick()
    t = bed.latest().hub
    assert t.water_ok is False and t.faults == [("fan_speed", None)] and bed.latest().fault is True
    bed.clear_faults()
    bed._tick()
    assert bed.latest().hub.faults == []
    assert {Cap.WATER_STATUS, Cap.FAULTS, Cap.HUB_HEALTH, Cap.WATER_FILL, Cap.REBOOT} <= bed.capabilities


# ---------- the routes ----------
def make(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, True
    return TestClient(create_app(cfg))


def sign_in(owner):
    assert owner.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": "p1"}).status_code == 200
    code = owner.post("/api/auth/invites", json={"person": "p2", "role": "sleeper"}, headers=H).json()["code"]
    g = TestClient(owner.app)
    assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
    code = owner.post("/api/auth/invites", json={"person": None, "role": "viewer"}, headers=H).json()["code"]
    v = TestClient(owner.app)
    assert v.post("/api/auth/accept", json={"code": code, "username": "guest", "password": PW}).status_code == 200
    return g, v


def ctl(client):
    return client.app.state.notifier.c


def test_detail_is_the_owners_and_the_short_status_is_everyones(tmp_path):
    with make(tmp_path) as owner:
        sleeper, viewer = sign_in(owner)
        owner.post("/api/sim", json={"fault": "fan_speed"}, headers=H)
        d = owner.get("/api/hub")
        assert d.status_code == 200 and d.json()["wifi"]["name"] == "Home network" and d.json()["faults"][0]["advice"]
        for who in (sleeper, viewer):
            assert who.get("/api/hub").status_code == 403
            snap = who.get("/api/snapshot").json()
            assert snap["system"]["hub"]["line"] == "Fan speed out of range"
            assert "Home network" not in json.dumps(snap) and "192.0.2.10" not in json.dumps(snap)


def test_light_and_fan_routes_are_the_owners(tmp_path):
    with make(tmp_path) as owner:
        sleeper, viewer = sign_in(owner)
        bed = ctl(owner).driver
        assert owner.put("/api/hub/light", json={"brightness": 35, "rgb": [10, 20, 30]}, headers=H).status_code == 200
        assert bed.brightness == 35 and bed.rgb == (10, 20, 30)
        assert owner.put("/api/hub/light", json={"brightness": 0}, headers=H).status_code == 200 and bed.brightness == 0
        assert owner.put("/api/hub/light", json={}, headers=H).status_code == 422
        assert owner.put("/api/hub/light", json={"brightness": 101}, headers=H).status_code == 422
        assert owner.put("/api/hub/light", json={"rgb": [1, 2, 300]}, headers=H).status_code == 422
        assert owner.put("/api/hub/fan", json={"quiet": False}, headers=H).status_code == 200 and bed.quiet is False
        assert owner.put("/api/hub/fan", json={"quiet": True}, headers=H).status_code == 200 and bed.quiet is True
        for who in (sleeper, viewer):
            assert who.put("/api/hub/light", json={"brightness": 90}, headers=H).status_code == 403
            assert who.put("/api/hub/fan", json={"quiet": False}, headers=H).status_code == 403
        assert bed.brightness == 0 and bed.quiet is True


@pytest.mark.parametrize("path", ["restart", "fill"])
def test_restart_and_fill_need_confirm_and_wait_for_a_night(tmp_path, monkeypatch, path):
    with make(tmp_path) as owner:
        sleeper, viewer = sign_in(owner)
        c = ctl(owner)
        url = f"/api/hub/{path}"
        sent = []
        orig = c.driver.command

        async def spy(raw):
            sent.append(raw)
            return await orig(raw)
        monkeypatch.setattr(c.driver, "command", spy)
        monkeypatch.setattr(c, "running_sides", lambda: ["left"])
        assert owner.post(url, json={}, headers=H).status_code == 400                       # no confirm
        assert owner.post(url, json={"confirm": False}, headers=H).status_code == 400
        assert owner.post(url, json={"confirm": True}, headers=H).status_code == 409        # a night is running
        assert sent == []
        for who in (sleeper, viewer):
            assert who.post(url, json={"confirm": True, "force": True}, headers=H).status_code == 403
        assert owner.post(url, json={"confirm": True, "force": True}, headers=H).status_code == 200
        assert sent == ["REBOOT" if path == "restart" else "FILL START"]
        monkeypatch.setattr(c, "running_sides", lambda: [])


def test_restart_when_nobody_is_in_a_night_and_rate_limit(tmp_path, monkeypatch):
    with make(tmp_path) as owner:
        sign_in(owner)
        c = ctl(owner)
        monkeypatch.setattr(c, "running_sides", lambda: [])
        r = owner.post("/api/hub/restart", json={"confirm": True}, headers=H)
        assert r.status_code == 200 and "back in about a minute" in r.json()["message"]
        assert c.driver.booting()
        again = owner.post("/api/hub/restart", json={"confirm": True, "force": True}, headers=H)
        assert again.status_code == 429 and "Try again" in again.json()["detail"]
        c.__dict__["_hub_restart_at"] -= 301                                                # five minutes later
        assert owner.post("/api/hub/restart", json={"confirm": True}, headers=H).status_code == 200
        assert any(e["kind"] == "hub_action" for e in c.store.events(limit=50))


def test_routes_refuse_what_the_driver_cannot_do(tmp_path, monkeypatch):
    with make(tmp_path) as owner:
        sign_in(owner)
        c = ctl(owner)
        monkeypatch.setattr(c.driver, "capabilities", set(c.driver.capabilities) - {Cap.WATER_FILL, Cap.REBOOT, Cap.FAN_QUIET})
        assert owner.post("/api/hub/fill", json={"confirm": True}, headers=H).status_code == 409
        assert owner.post("/api/hub/restart", json={"confirm": True}, headers=H).status_code == 409
        assert owner.put("/api/hub/fan", json={"quiet": True}, headers=H).status_code == 409


def test_the_simulator_api_injects_faults(tmp_path):
    with make(tmp_path) as owner:
        sign_in(owner)
        assert owner.post("/api/sim", json={"fault": "nonsense"}, headers=H).status_code == 422
        r = owner.post("/api/sim", json={"fault": "fan_speed", "low_water": True}, headers=H).json()
        assert r["faults"] == ["fan_speed"] and r["low_water"] is True
        snap = owner.get("/api/snapshot").json()["system"]["hub"]
        assert snap["line"] == "Fan speed out of range" and snap["water_ok"] is False
        assert owner.post("/api/sim", json={"clear_faults": True, "low_water": False}, headers=H).json()["faults"] == []


# ---------- a restart resets the unit: targets go back out ----------
async def test_targets_are_re_applied_after_a_restart(tmp_path):
    r = Rig(tmp_path, "2026-11-05T22:00", notifier=False)
    seen = {}

    def restart(rig):
        seen["before"] = {z: (rig.bed.z[z].on, rig.bed.z[z].setpoint) for z in ("left", "right")}
        rig.bed.restart_now()                       # what the unit does on REBOOT: both sides off at its own default
        rig.c.note_restart()                        # what the restart route does
        seen["during"] = {z: rig.bed.z[z].on for z in ("left", "right")}

    script = with_events(both_in_bed(), {"2026-11-06T02:30": restart})
    await r.run("2026-11-06T02:45", script=script)
    assert seen["before"]["left"][0] and seen["before"]["right"][0]
    assert seen["during"] == {"left": False, "right": False}
    for p, zone in ((r.p1, "left"), (r.p2, "right")):
        e = r.e(p)
        assert e.s.started and e.s.state == "rhythm"
        assert r.bed.z[zone].on and r.bed.z[zone].setpoint == pytest.approx(e.s.target_c, abs=0.1)
        assert r.bed.z[zone].setpoint == pytest.approx(seen["before"][zone][1], abs=0.5)


# ---------- notifications ----------
def snap_with(hub: dict, state: str = "day", online: bool = True) -> dict:
    return {"system": {"online": online, "hub": hub}, "people": [{"id": "p1", "zone": "left", "night": {"state": state}}]}


def problem(**kw) -> dict:
    return tw.summary(HubHealth(**kw))


def collect(r, snap) -> list:
    out: list = []
    r.c.notifier._snap = snap
    r.c.notifier._hub(snap, r.clock.now_ts(), out)
    return out


def test_a_fault_is_told_once_a_day_in_plain_words(tmp_path):
    r = Rig(tmp_path, "2026-11-05T15:00")
    snap = snap_with(problem(faults=[("fan_speed", None)], water_ok=True, safety_ok=True))
    out = collect(r, snap)
    assert len(out) == 1 and out[0].kind == "hub" and out[0].person is None and not out[0].urgent
    assert "Fan speed out of range" in out[0].title + out[0].body and "vents" in out[0].body
    assert collect(r, snap) == []                                    # same problem, same day: not again
    r.clock.advance(24 * 3600)
    assert len(collect(r, snap)) == 1                                # a new day, still there: once more
    assert len(collect(r, snap_with(problem(faults=[("pump", None)])))) == 1      # a different problem is its own message


def test_low_water_is_told_and_nothing_when_all_is_well_or_offline(tmp_path):
    r = Rig(tmp_path, "2026-11-05T15:00")
    out = collect(r, snap_with(problem(water_ok=False, safety_ok=True)))
    assert len(out) == 1 and "water" in (out[0].title + out[0].body).lower()
    assert collect(r, snap_with(problem(water_ok=True, safety_ok=True))) == []
    assert collect(r, snap_with(problem(water_ok=False), online=False)) == []
    assert collect(r, {"system": {"online": True}, "people": []}) == []             # a driver with no health report


def test_a_fault_in_the_small_hours_waits_for_the_morning(tmp_path):
    r = Rig(tmp_path, "2026-11-06T03:00")
    snap = snap_with(problem(faults=[("fan_speed", None)], safety_ok=True), state="rhythm")
    snap["people"][0]["schedule"] = {"bedtime": ts("2026-11-05T22:30"), "wake": ts("2026-11-06T07:00"), "end": ts("2026-11-06T07:30")}
    out = collect(r, snap)
    assert not out[0].urgent
    assert r.c.notifier.send(out[0]) is False
    assert r.told() == ["hub"] and r.events("notification_held") and not r.events("notification")


def test_a_failing_safety_check_during_a_running_night_goes_out_at_once(tmp_path):
    r = Rig(tmp_path, "2026-11-06T03:00")
    snap = snap_with(problem(faults=[("hub_hot", None)], safety_ok=False), state="rhythm")
    snap["people"][0]["schedule"] = {"bedtime": ts("2026-11-05T22:30"), "wake": ts("2026-11-06T07:00"), "end": ts("2026-11-06T07:30")}
    out = collect(r, snap)
    assert out and all(m.urgent for m in out)
    assert r.c.notifier.send(out[0]) is True and r.events("notification") and not r.events("notification_held")
    # the same thing with no night running is not urgent
    (tmp_path / "b").mkdir()
    r2 = Rig(tmp_path / "b", "2026-11-05T15:00")
    assert not any(m.urgent for m in collect(r2, snap_with(problem(faults=[("hub_hot", None)], safety_ok=False))))


def test_the_hub_switch_is_in_the_notification_settings():
    from embertide.models import NotifyPrefs
    assert NotifyPrefs().hub is True


def test_only_owner_phones_get_hub_pushes(tmp_path):
    r = Rig(tmp_path, "2026-11-05T15:00")
    n = r.c.notifier
    n.add_device("p1", "https://push.example/aaaaaa", "k", "a", "Phone")
    n.add_device("p2", "https://push.example/bbbbbb", "k", "a", "Phone")
    from embertide.notify import Message
    m = Message("hub", "t", "b")
    assert {pid for pid, _ in n.push_targets(m)} == {"p1", "p2"}          # no owner linked: everyone who wants it
    n.owner_persons = lambda: {"p1"}
    assert {pid for pid, _ in n.push_targets(m)} == {"p1"}
    r.c.person("p1").settings.notify.hub = False
    assert n.push_targets(m) == []


def test_the_ignored_alert_says_whether_the_hub_got_the_command(tmp_path):
    r = Rig(tmp_path, "2026-11-05T15:00")
    n = r.c.notifier
    for got, word in ((False, "hasn't confirmed"), (True, "did report receiving"), (None, "")):
        n._snap = {"system": {"hub": {"command_received": got}}}
        assert (word in n._ack_note()) if word else n._ack_note() == ""
