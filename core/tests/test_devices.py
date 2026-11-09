"""Device types: each kind of sensor is read the way it behaves in bed, with its own tuning, and counts by its own weight."""

from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.sources import DEVICE_TYPES, SourceConfig, SourceManager, tuning_for

H = {"X-Embertide": "1"}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t, self.speed = t, 1.0

    def now_ts(self):
        return self.t


class Store:
    def __init__(self):
        self.kv = {}

    def get(self, k, d=None):
        return self.kv.get(k, d)

    def put(self, k, v):
        self.kv[k] = v

    def sample(self, *a):
        pass


def mgr():
    clock = Clock()
    m = SourceManager(Store(), clock, lambda: None)
    return m, clock


def feed(m, clock, c, value, at):
    clock.t = 1_000_000.0 + at
    m._sample(c, value, clock.t)


def vote(m, clock, c, at):
    clock.t = 1_000_000.0 + at
    return m.vote_value(c)


def test_every_type_has_sane_tuning():
    for t in DEVICE_TYPES:
        for f in t["tuning"]:
            assert f["type"] in ("number", "bool", "select", "text", "map")
            if f["type"] == "number" and "default" in f:
                assert f.get("min", f["default"]) <= f["default"] <= f.get("max", f["default"]), (t["id"], f["key"])
    assert {t["id"] for t in DEVICE_TYPES} >= {"radar", "motion", "scale", "pad", "room", "wearable", "custom"}


def test_radar_bridges_still_sleeper_gaps_and_ignores_brief_detections():
    m, clock = mgr()
    c = SourceConfig(name="r", driver="sim", role="presence", zone="left", kind="radar", tuning={"hold_s": 45, "min_on_s": 10})
    feed(m, clock, c, 1, 0)
    assert vote(m, clock, c, 5) == 0.0          # not settled yet (someone leaning over the bed)
    assert vote(m, clock, c, 12) == 1.0         # settled
    feed(m, clock, c, 0, 60)                    # radar loses the still sleeper
    assert vote(m, clock, c, 90) == 1.0         # held
    assert vote(m, clock, c, 110) == 0.0        # gone longer than the hold: really empty
    m2, clock2 = mgr()
    feed(m2, clock2, c, 1, 0)
    feed(m2, clock2, c, 0, 4)                   # a pet walking past
    assert vote(m2, clock2, c, 20) == 0.0


def test_motion_is_a_long_weak_hint():
    m, clock = mgr()
    c = SourceConfig(name="m", driver="mqtt-topic", role="presence", zone="left", kind="motion")
    assert tuning_for("motion", {})["weight"] < tuning_for("radar", {})["weight"]
    feed(m, clock, c, 1, 0)
    feed(m, clock, c, 0, 2)
    assert vote(m, clock, c, 250) == 1.0        # 5-minute hold by default
    assert vote(m, clock, c, 320) == 0.0


def test_event_sensors_keep_their_state_but_polled_scales_go_stale():
    m, clock = mgr()
    radar = SourceConfig(name="r", driver="mqtt-topic", role="presence", zone="left", kind="radar", tuning={"min_on_s": 0})
    scale = SourceConfig(name="s", driver="ha-entity", role="weight", zone="left", kind="scale", tuning={"min_on_s": 0}, params={"url": "x", "entity_id": "y"})
    feed(m, clock, radar, 1, 0)
    feed(m, clock, scale, 70, 0)
    assert vote(m, clock, radar, 3600) == 1.0   # an MQTT radar only reports changes: still on an hour later
    assert vote(m, clock, scale, 3600) is None  # a scale that stopped reporting for an hour is left out


def test_scale_threshold_hysteresis_and_zero():
    m, clock = mgr()
    c = SourceConfig(name="s", driver="sim", role="weight", zone="left", kind="scale", tuning={"threshold_kg": 30, "hysteresis_kg": 8, "min_on_s": 0, "hold_s": 0})
    feed(m, clock, c, 25, 0)
    assert vote(m, clock, c, 1) == 0.0
    feed(m, clock, c, 31, 2)
    assert vote(m, clock, c, 3) == 1.0
    feed(m, clock, c, 24, 4)                    # shifting weight: below 30 but above 30-8
    assert vote(m, clock, c, 5) == 1.0
    feed(m, clock, c, 20, 6)
    assert vote(m, clock, c, 7) == 0.0
    m.calibration.tare_kg[c.id] = 3.5           # zeroed with an empty bed reading 3.5 kg
    feed(m, clock, c, 33.5, 8)
    assert m.latest[c.id]["value"] == 30.0


def test_custom_device_scale_offset_compare_invert():
    m, clock = mgr()
    # a DIY strip that reads 0..4095 and drops when someone lies on it
    c = SourceConfig(name="diy", driver="mqtt-topic", role="presence", zone="right", kind="custom",
                     tuning={"scale": 0.1, "offset": -10, "compare": "<=", "threshold": 150, "min_on_s": 0, "hold_s": 0, "weight": 0.6})
    feed(m, clock, c, 3000, 0)                  # 3000 * 0.1 - 10 = 290 -> empty
    assert m.latest[c.id]["value"] == 290 and vote(m, clock, c, 1) == 0.0
    feed(m, clock, c, 1200, 2)                  # 110 -> occupied
    assert vote(m, clock, c, 3) == 1.0
    assert m.vote_weight(c) == 0.6
    c2 = SourceConfig(name="inv", driver="sim", role="presence", zone="right", kind="custom", tuning={"invert": True, "min_on_s": 0})
    feed(m, clock, c2, 1, 10)
    assert vote(m, clock, c2, 11) == 0.0
    temp = SourceConfig(name="t", driver="sim", role="room_temp", kind="custom", tuning={"offset": -0.8})
    m.configs = [c, c2, temp]
    feed(m, clock, temp, 22.0, 12)
    assert m.latest[temp.id]["value"] == 21.2 and m.zone_signals("right").keys() >= {f"presence:{c.id}"}
    assert f"presence:{temp.id}" not in m.zone_signals("right")


def test_old_setups_get_a_type_from_their_role():
    assert SourceConfig(name="a", driver="sim", role="presence").kind == "radar"
    assert SourceConfig(name="b", driver="sim", role="weight").kind == "scale"
    assert SourceConfig(name="c", driver="sim", role="room_humidity").kind == "room"


def make(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False
    return TestClient(create_app(cfg))


def test_api_lists_types_and_checks_devices(tmp_path):
    with make(tmp_path) as c:
        d = c.get("/api/sources").json()
        assert [t["id"] for t in d["types"]][:3] == ["radar", "motion", "scale"]
        assert all("kind" in s and "kind_label" in s for s in d["sources"])
        base = {"name": "Radar", "driver": "sim", "role": "presence", "kind": "radar", "params": {"kind": "presence"}}
        assert c.post("/api/sources", json=base, headers=H).status_code == 422            # no side
        ok = c.post("/api/sources", json=base | {"zone": "left", "tuning": {"hold_s": 9999, "min_on_s": "5"}}, headers=H)
        assert ok.status_code == 200 and ok.json()["tuning"] == {"hold_s": 600, "min_on_s": 5.0}   # clamped, typed
        bad = c.post("/api/sources", json=base | {"kind": "custom", "zone": "left", "tuning": {"compare": "~"}}, headers=H)
        assert bad.status_code == 422
        ha = c.post("/api/sources", json={"name": "x", "driver": "ha-entity", "role": "presence", "kind": "radar", "zone": "left", "params": {}}, headers=H)
        assert ha.status_code == 422 and "Home Assistant" in ha.json()["detail"]
        room = c.post("/api/sources", json={"name": "Room", "driver": "sim", "role": "presence", "kind": "room", "zone": "left", "params": {"kind": "room_temp"}}, headers=H).json()
        assert room["role"] == "room_temp" and room["zone"] is None                         # role fixed to the type, no side


def test_fusion_uses_each_devices_own_weight(tmp_path):
    with make(tmp_path) as c:
        ctl = c.app.state.controller if hasattr(c.app.state, "controller") else None
        from embertide.app import create_app  # noqa: F401
        snap = c.get("/api/snapshot").json()
        votes = [v for p in snap["people"] for v in ((p["presence"]["fusion"] or {}).get("votes") or [])]
        radar_votes = [v for v in votes if v["signal"].startswith("presence:simrad")]
        assert all(abs(v["weight"] - 0.35) < 1e-9 for v in radar_votes)


async def test_changing_tuning_takes_effect_at_once():
    m, clock = mgr()
    m._launch = lambda c: None
    c = SourceConfig(name="d", driver="sim", role="presence", zone="left", kind="custom", tuning={"hold_s": 30, "min_on_s": 0})
    await m.add(c)
    feed(m, clock, c, 1, 0)
    assert vote(m, clock, c, 1) == 1.0
    c2 = c.model_copy(update={"tuning": {"hold_s": 30, "min_on_s": 0, "invert": True}})
    await m.add(c2)                              # flipped: the old "in bed" mustn't be held for another 30 s
    feed(m, clock, c2, 1, 2)
    assert vote(m, clock, c2, 3) == 0.0


def test_on_off_activity_and_fast_polling(tmp_path):
    from embertide.clock import FixedClock
    from embertide.sources import SourceConfig, SourceManager, poll_s
    from embertide.store import Store
    clock = FixedClock(1_800_000_000.0, "America/New_York")
    m = SourceManager(Store(tmp_path / "s.db"), clock, lambda: None)
    light = SourceConfig(id="l", name="Light", driver="ha-entity", role="light", kind="light", params={"url": "http://x", "entity_id": "light.a", "token": "t"})
    room = SourceConfig(id="r", name="Room", driver="ha-entity", role="room_temp", kind="room", params={"url": "http://x", "entity_id": "sensor.t", "token": "t"})
    m.configs = [light, room]
    assert poll_s(light) == 5.0 and poll_s(room) == 30.0
    for v in (1, 1, 0, 0, 1, 0):
        m._sample(light, float(v), clock.now_ts())
        clock.advance(60)
    d = m.describe(light)
    assert d["changes_today"] == 3 and d["last_change"] is not None and d["poll_s"] == 5.0


def test_editing_a_device_changes_everything_but_its_history(tmp_path):
    """Studio → Devices → Edit: name, side, type/role, connection and tuning in one save; same id so past readings
    stay attached; the duplicate check ignores the device itself but still catches a clash with another one."""
    with make(tmp_path) as c:
        mk = lambda name, ent: c.post("/api/sources", headers=H, json={"name": name, "driver": "ha-entity", "role": "presence", "kind": "radar",  # noqa: E731
                                                                        "zone": "left", "params": {"url": "http://ha:8123", "token": "t", "entity_id": ent}}).json()
        a, b = mk("Radar", "binary_sensor.a"), mk("Other", "binary_sensor.b")
        ctl = c.app.state.notifier.c
        ctl.store.sample(1000.0, a["id"], 1.0)                              # a past reading
        shown = next(s for s in c.get("/api/sources").json()["sources"] if s["id"] == a["id"])
        body = {**shown, "name": "Bed scale left", "kind": "scale", "role": "weight", "zone": "right", "device": "Load cells",
                "params": {**shown["params"], "entity_id": "sensor.bed_weight"}, "tuning": {"threshold_kg": 25, "hold_s": 5, "invert": True}}
        r = c.put(f"/api/sources/{a['id']}", headers=H, json=body)
        assert r.status_code == 200, r.text
        got = r.json()
        assert (got["id"], got["name"], got["kind"], got["role"], got["zone"], got["device"]) == (a["id"], "Bed scale left", "scale", "weight", "right", "Load cells")
        assert got["params"]["entity_id"] == "sensor.bed_weight" and got["params"]["token"] == "•••"
        assert got["tuning"]["threshold_kg"] == 25 and "invert" not in got["tuning"]       # a scale has no "reversed"
        assert ctl.store.samples(a["id"], 0, 2000) == [(1000.0, 1.0)]                       # history kept
        # saving it unchanged is not a duplicate of itself
        assert c.put(f"/api/sources/{a['id']}", headers=H, json={**body, "tuning": got["tuning"]}).status_code == 200
        # pointing the other device at the same entity and role is
        ob = next(s for s in c.get("/api/sources").json()["sources"] if s["id"] == b["id"])
        clash = c.put(f"/api/sources/{b['id']}", headers=H, json={**ob, "kind": "scale", "role": "weight", "params": {**ob["params"], "entity_id": "sensor.bed_weight"}})
        assert clash.status_code == 409 and "Bed scale left" in clash.json()["detail"]
        # reversed on a presence device
        r = c.put(f"/api/sources/{b['id']}", headers=H, json={**ob, "tuning": {"invert": True}})
        assert r.status_code == 200 and r.json()["tuning_resolved"]["invert"] is True
