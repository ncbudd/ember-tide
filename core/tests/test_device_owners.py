"""Who a device is for: one person, both of you, or (for a bed sensor) a side, including one radar or scale that
covers the whole bed as a left and a right part (a group). Plus older setups brought up to this model, the
dial list, and Home Assistant entity filtering per kind."""

import httpx
import pytest
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.dials_api import from_states, parse_template
from embertide.engine.context import _mine
from embertide.ha_connection_api import fit_rank
from embertide.sources import SourceConfig, SourceManager, default_sim_sources

H = {"X-Embertide": "1"}
HA = {"url": "http://ha:8123", "token": "t"}


def make(tmp_path, bed="sim"):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", bed, tmp_path / "noweb", None, False
    return TestClient(create_app(cfg))


def ctl(c):
    return c.app.state.notifier.c


def feed(url):
    return {"name": "Calendar", "driver": "ics", "role": "calendar", "kind": "calendar", "enabled": False, "params": {"url": url}}


def test_two_calendars_for_one_person_and_a_shared_one(tmp_path):
    with make(tmp_path) as c:
        a = c.post("/api/sources", headers=H, json=feed("https://cal.example/a.ics") | {"person": "p1"})
        b = c.post("/api/sources", headers=H, json=feed("webcal://cal.example/b.ics") | {"name": "Work", "person": "p1"})
        assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
        shared = c.post("/api/sources", headers=H, json=feed("https://cal.example/family.ics") | {"name": "Family"})
        assert shared.status_code == 200 and shared.json()["person"] is None          # "both of you"
        # the same link again is still a duplicate, whoever it's for
        again = c.post("/api/sources", headers=H, json=feed("https://CAL.example/a.ics/") | {"person": "p2"})
        assert again.status_code == 409 and "Calendar" in again.json()["detail"]
        # a shared calendar counts for both people, a personal one only for its owner
        people = {p.id: p for p in ctl(c).household.people}
        s = SourceConfig(**{k: v for k, v in shared.json().items() if k in SourceConfig.model_fields} | {"enabled": True})
        own = SourceConfig(**{k: v for k, v in a.json().items() if k in SourceConfig.model_fields} | {"enabled": True})
        assert _mine(s, people["p1"]) and _mine(s, people["p2"])
        assert _mine(own, people["p1"]) and not _mine(own, people["p2"])
        # an unknown person is refused
        assert c.post("/api/sources", headers=H, json=feed("https://cal.example/x.ics") | {"person": "nobody"}).status_code == 422


def test_a_partners_wearable_follows_their_side_and_needs_an_owner(tmp_path):
    with make(tmp_path) as c:
        body = {"name": "Partner's ring", "driver": "ha-entity", "role": "skin_temp", "kind": "wearable", "zone": "left",
                "params": HA | {"entity_id": "sensor.ring_skin"}}
        assert c.post("/api/sources", headers=H, json=body).status_code == 422             # whose is it?
        r = c.post("/api/sources", headers=H, json=body | {"person": "p2"})
        assert r.status_code == 200 and r.json()["person"] == "p2" and r.json()["zone"] == "right"   # p2 sleeps right
        phone = {"name": "Phone", "driver": "sim", "role": "home", "kind": "phone", "params": {"kind": "home"}}
        assert c.post("/api/sources", headers=H, json=phone).status_code == 422
        assert c.post("/api/sources", headers=H, json=phone | {"person": "p1"}).json()["zone"] is None


def radar(parts, name="Bedroom radar"):
    return {"name": name, "kind": "radar", "driver": "ha-entity", "device": "Aqara FP2", "parts": parts}


def zone(z, ent, **kw):
    return {"zone": z, "params": HA | {"entity_id": ent}, **kw}


def test_radar_for_both_maps_a_zone_to_each_side(tmp_path):
    with make(tmp_path) as c:
        r = c.post("/api/sources/group", headers=H, json=radar([zone("left", "binary_sensor.fp2_zone_1"), zone("right", "binary_sensor.fp2_zone_2")]))
        assert r.status_code == 200, r.text
        gid, parts = r.json()["group"], r.json()["sources"]
        assert {(s["zone"], s["params"]["entity_id"]) for s in parts} == {("left", "binary_sensor.fp2_zone_1"), ("right", "binary_sensor.fp2_zone_2")}
        assert all(s["group"] == gid and s["votes"] and s["device"] == "Aqara FP2" and s["params"]["token"] == "•••" for s in parts)
        # the same zone on both sides makes no sense
        same = c.post("/api/sources/group", headers=H, json=radar([zone("left", "binary_sensor.x"), zone("right", "binary_sensor.x")]))
        assert same.status_code == 422
        # an entity already used by another device is a duplicate
        c.post("/api/sources", headers=H, json={"name": "Old pad", "driver": "ha-entity", "role": "presence", "kind": "pad", "zone": "left",
                                                "params": HA | {"entity_id": "binary_sensor.pad"}})
        dup = c.post("/api/sources/group", headers=H, json=radar([zone("left", "binary_sensor.pad"), zone("right", "binary_sensor.y")]))
        assert dup.status_code == 409 and "Old pad" in dup.json()["detail"]
        # swapping the two zones is not a duplicate of itself, and each side keeps its id (history)
        ids = {s["zone"]: s["id"] for s in parts}
        sw = c.put(f"/api/sources/group/{gid}", headers=H, json=radar([zone("left", "binary_sensor.fp2_zone_2", id=ids["left"]),
                                                                       zone("right", "binary_sensor.fp2_zone_1", id=ids["right"])]))
        assert sw.status_code == 200, sw.text
        assert {(s["id"], s["params"]["entity_id"]) for s in sw.json()["sources"]} == {(ids["left"], "binary_sensor.fp2_zone_2"), (ids["right"], "binary_sensor.fp2_zone_1")}
        # unmapping a side removes that part only
        one = c.put(f"/api/sources/group/{gid}", headers=H, json=radar([zone("left", "binary_sensor.fp2_zone_2", id=ids["left"])]))
        assert one.status_code == 200
        mine = [s for s in c.get("/api/sources").json()["sources"] if s["group"] == gid]
        assert [s["id"] for s in mine] == [ids["left"]]
        # a room sensor can't be split into sides
        bad = c.post("/api/sources/group", headers=H, json={"name": "Room", "kind": "room", "driver": "sim", "parts": [{"zone": "left", "params": {"kind": "room_temp"}}]})
        assert bad.status_code == 422
        assert c.delete(f"/api/sources/group/{gid}", headers=H).json()["removed"] == 1
        assert not [s for s in c.get("/api/sources").json()["sources"] if s["group"] == gid]


def test_an_existing_one_sided_radar_can_become_both(tmp_path):
    with make(tmp_path) as c:
        old = c.post("/api/sources", headers=H, json={"name": "Radar", "driver": "ha-entity", "role": "presence", "kind": "radar", "zone": "left",
                                                      "params": HA | {"entity_id": "binary_sensor.ld2450_zone_1"}}).json()
        ctl(c).store.sample(1000.0, old["id"], 1.0)
        r = c.post("/api/sources/group", headers=H, json=radar([zone("left", "binary_sensor.ld2450_zone_1", id=old["id"]),
                                                                zone("right", "binary_sensor.ld2450_zone_2")], name="LD2450"))
        assert r.status_code == 200, r.text
        assert old["id"] in {s["id"] for s in r.json()["sources"]}
        assert ctl(c).store.samples(old["id"], 0, 2000) == [(1000.0, 1.0)]                # history kept
        assert len([s for s in c.get("/api/sources").json()["sources"] if s["name"] == "LD2450"]) == 2


def test_older_setups_get_owners_and_the_sim_pairs_become_one_device(tmp_path):
    class Store(dict):
        def get(self, k, d=None):
            return super().get(k, d)

        def put(self, k, v):
            self[k] = v

    class P:
        def __init__(self, id, zone):
            self.id, self.zone = id, zone

    legacy = [c.model_dump(exclude={"group"}) for c in default_sim_sources()]
    for c in legacy:
        if c["kind"] in ("radar", "scale"):
            c["name"] = c["name"] + " · " + c["zone"]
    legacy.append({"id": "w2", "name": "Ring", "driver": "sim", "role": "skin_temp", "kind": "wearable", "zone": "right", "params": {}})
    legacy.append({"id": "w3", "name": "Watch", "driver": "sim", "role": "wearable_hr", "kind": "wearable", "zone": "left", "person": "p2", "params": {}})
    m = SourceManager(Store(sources=legacy), None, lambda: None)                 # old dicts without "group" load
    assert m.migrate([P("p1", "left"), P("p2", "right")]) is True
    by = {c.id: c for c in m.configs}
    assert by["w2"].person == "p2"                                              # a side-only wearable gets that side's sleeper
    assert by["w3"].zone == "right"                                             # a person's wearable follows their side
    assert by["simradl"].group == by["simradr"].group == "sim-radar" and by["simkgl"].group == "sim-scale"
    assert m.migrate([P("p1", "left"), P("p2", "right")]) is False             # nothing more to do


def test_dials_from_home_assistant_and_bluetooth(tmp_path):
    text = ("dev1|Bedside Dial Left|2025.10.3|CrowPanel|sensor.bedside_dial_left_wifi|-61\n"
            "dev1|Bedside Dial Left|2025.10.3|CrowPanel|button.bedside_dial_left_restart|unknown\n"
            "dev2|Bedside Dial Right|None|None|sensor.bedside_dial_right_wifi|unavailable\n"
            "dev3|Kitchen speaker|1|x|sensor.kitchen|1\n")
    d = {x["name"]: x for x in parse_template(text)}
    assert set(d) == {"Bedside Dial Left", "Bedside Dial Right"}
    assert d["Bedside Dial Left"]["online"] and d["Bedside Dial Left"]["entities"] == 2 and d["Bedside Dial Left"]["firmware"] == "2025.10.3"
    assert not d["Bedside Dial Right"]["online"] and d["Bedside Dial Right"]["firmware"] is None
    st = [{"entity_id": "sensor.my_dial_main_wifi", "state": "-50", "attributes": {"friendly_name": "My Dial Main WiFi"}},
          {"entity_id": "sensor.embertide_left_phase", "state": "deep", "attributes": {"friendly_name": "Left dial phase"}}]
    assert [x["name"] for x in from_states(st)] == ["My Dial Main"]

    def fake_ha(request):
        if request.url.path == "/api/template":
            return httpx.Response(200, text=text)
        return httpx.Response(404)

    with make(tmp_path) as c:
        r = c.get("/api/dials").json()
        assert r["ha_configured"] is False and r["dials"] == [] and [b["side"] for b in r["beacons"]] == ["left", "right"]
        ctl(c).store.put("ha_connection", {"url": "http://ha.test:8123", "token": "secret"})
        ctl(c).sources.http_transport = httpx.MockTransport(fake_ha)
        dials = c.get("/api/dials").json()["dials"]
        did = next(x["id"] for x in dials if x["name"] == "Bedside Dial Left")
        assert c.put(f"/api/dials/{did}", headers=H, json={"side": "left"}).status_code == 200
        assert next(x for x in c.get("/api/dials").json()["dials"] if x["id"] == did)["side"] == "left"
        assert "secret" not in c.get("/api/dials").text
        pkg = c.get("/api/dials/files/embertide-dial-common.yaml")
        assert pkg.status_code == 200 and "beacon_minor" in pkg.text and "!secret wifi_ssid" in pkg.text
        assert c.get("/api/dials/files/..%2Fapp.py").status_code == 404


@pytest.mark.parametrize("kind,e,fits", [
    ("wearable", {"entity_id": "sensor.bed_left_heart_rate", "name": "Bed left heart rate"}, False),
    ("wearable", {"entity_id": "sensor.embertide_left_hr", "name": "Left HR"}, False),
    ("wearable", {"entity_id": "sensor.pixel_watch_heart_rate", "name": "Pixel Watch heart rate"}, True),
    ("radar", {"entity_id": "sensor.ld2450_zone_1_target_count", "name": "Zone 1 targets"}, True),
    ("radar", {"entity_id": "binary_sensor.presence_sensor_fp2_zone_left", "name": "FP2 Left", "device_class": "occupancy"}, True),
])
def test_entity_list_fits_the_kind(kind, e, fits):
    assert (fit_rank(kind, e) is not None) is fits
