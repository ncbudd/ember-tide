"""On/off devices: what each raw value means (tuning.value_map), the raw values Studio shows, and older setups that
only have "Reading is reversed" (invert) keep working."""

from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.sources import SourceConfig, SourceManager, tuning_for

H = {"X-Embertide": "1"}


def make(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False
    return TestClient(create_app(cfg))


LIGHT = {"name": "Bedroom light", "driver": "mqtt-topic", "role": "light", "kind": "light", "enabled": False,
         "params": {"url": "mqtt://broker.test:1883", "topic": "bedroom/light"}}


def test_value_map_is_saved_cleaned_and_turns_reversed_off(tmp_path):
    with make(tmp_path) as c:
        r = c.post("/api/sources", headers=H, json=LIGHT | {"tuning": {"invert": True, "value_map": {" ON ": "no", "off": "yes", "unavailable": "ignore"}}})
        assert r.status_code == 200, r.text
        t = r.json()["tuning_resolved"]
        assert t["value_map"] == {"on": "no", "off": "yes", "unavailable": "ignore"} and t["invert"] is False
        sid = r.json()["id"]
        got = next(s for s in c.get("/api/sources").json()["sources"] if s["id"] == sid)
        assert got["tuning_resolved"]["value_map"]["on"] == "no" and got["raw_seen"] == []
        bad = c.put(f"/api/sources/{sid}", headers=H, json=LIGHT | {"tuning": {"value_map": {"on": "maybe"}}})
        assert bad.status_code == 422


def test_older_reversed_setups_still_read_the_same(tmp_path):
    with make(tmp_path) as c:
        r = c.post("/api/sources", headers=H, json=LIGHT | {"tuning": {"invert": True}})
        assert r.status_code == 200
        t = r.json()["tuning_resolved"]
        assert t["invert"] is True and t["value_map"] == {}          # no map: the reversed flag still applies


def test_map_applies_to_raw_values_and_they_are_remembered():
    class Store(dict):
        def get(self, k, d=None):
            return super().get(k, d)

        def put(self, k, v):
            self[k] = v

    class Clock:
        def now_ts(self):
            return 1000.0

    m = SourceManager(Store(), Clock(), lambda: None)
    pres = SourceConfig(name="Radar", driver="sim", role="presence", kind="radar", zone="left",
                        tuning={"value_map": {"detected": "no", "clear": "yes", "unknown": "ignore"}})
    assert m.map_raw(pres, "Detected") == (0.0, None)
    assert m.map_raw(pres, "clear") == (1.0, None)
    assert m.map_raw(pres, "unknown")[0] is None
    assert m.map_raw(pres, "on") is None                        # not in the map: read the usual way
    old = SourceConfig(name="Pad", driver="sim", role="presence", kind="pad", zone="left", tuning={"invert": True})
    assert m.map_raw(old, "on") is None and tuning_for("pad", old.tuning)["invert"] is True
    assert [x["value"] for x in m.describe(pres)["raw_seen"]] == ["detected", "clear", "unknown", "on"]
