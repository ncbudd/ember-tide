"""Sides of the bed set up the wrong way round, and fixing it: "swap sides" moves tonight's started night to whoever
is really lying there, a person's own devices and the bedside dials follow them, bed sensors stay with the side.
Plus the snapshot's per-side presence (setup's "check your side") and hiding old dials."""

import httpx
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.dials_api import parse_template
from embertide.sources import SourceConfig, SourceManager
from test_scenarios import Night

H = {"X-Embertide": "1"}


def make(tmp_path, auth=False):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, auth
    return TestClient(create_app(cfg))


def ctl(c):
    return c.app.state.notifier.c


# ---------- tonight's night moves with the side ----------
async def test_swap_moves_the_started_night_to_whoever_is_lying_there(tmp_path):
    """She lies on the side the bed calls left while the app had her on the right: the left person's night starts.
    After the swap, her night is the started one (not a hold on an empty side, not a new wait)."""
    n = Night(tmp_path, "2026-11-05T22:20")
    left, right = n.p1, n.p2                     # left = the person who is NOT in bed; right = the one who is
    n.present("left", True)
    n.present("right", False)
    await n.until("2026-11-05T23:00")
    assert n.e(left).s.started and not n.e(right).s.started
    n.c.swap_sides()
    assert (left.zone, right.zone) == ("right", "left")
    await n.step(4)
    assert n.e(right).s.started and n.e(right).s.state == "rhythm"
    assert not n.e(left).s.started and n.e(left).s.state != "out_of_bed_hold"
    assert n.side("left").on                                                     # the occupied side keeps working
    assert abs(n.side("left").setpoint - n.e(right).s.target_c) < 0.1


async def test_swap_with_both_in_bed_keeps_both_nights_running(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    n.present("right", True)
    await n.until("2026-11-05T23:30")
    n.c.swap_sides()
    await n.step(4)
    assert all(n.e(p).s.started and n.e(p).s.state == "rhythm" for p in (n.p1, n.p2))


# ---------- devices that belong to a person follow them ----------
def test_own_devices_follow_their_owner_bed_sensors_stay(tmp_path):
    with make(tmp_path) as c:
        k = ctl(c)
        ppl = {p.zone: p.id for p in k.household.people}
        add = lambda body: c.post("/api/sources", headers=H, json={"driver": "sim", "enabled": False, "params": {}} | body)
        w = add({"name": "Watch", "role": "wearable_hr", "kind": "wearable", "person": ppl["left"]}).json()
        ph = add({"name": "Phone", "role": "home", "kind": "phone", "person": ppl["left"]}).json()
        rad = add({"name": "Radar", "role": "presence", "kind": "radar", "zone": "left"}).json()
        assert w["zone"] == "left"
        k.store.put("dial_sides", {"dialabc": "left"})
        assert c.post("/api/household/swap-sides", headers=H).status_code == 200
        by = {s["id"]: s for s in c.get("/api/sources").json()["sources"]}
        assert by[w["id"]]["zone"] == "right" and by[w["id"]]["person"] == ppl["left"]   # the watch went with its owner
        assert by[ph["id"]]["zone"] is None and by[ph["id"]]["person"] == ppl["left"]
        assert by[rad["id"]]["zone"] == "left"                                         # the radar watches the same spot
        assert k.store.get("dial_sides") == {"dialabc": "right"}
        b = c.get("/api/dial-beacons").json()["dials"]
        assert {x["minor"]: x["side"] for x in b} == {1: "right", 2: "left"}
        assert {x["minor"]: x["name"] for x in b} == {1: "Right dial", 2: "Left dial"}
        assert c.post("/api/household/swap-sides", headers=H).status_code == 200        # and back again
        assert {x["minor"]: x["side"] for x in c.get("/api/dial-beacons").json()["dials"]} == {1: "left", 2: "right"}


def test_follow_owners_only_touches_personal_devices():
    class Store(dict):
        def get(self, k, d=None):
            return super().get(k, d)

        def put(self, k, v):
            self[k] = v

    class P:
        def __init__(self, id, zone):
            self.id, self.zone = id, zone

    m = SourceManager(Store(sources=[
        SourceConfig(id="w", name="Ring", driver="sim", role="skin_temp", kind="wearable", zone="left", person="p1").model_dump(),
        SourceConfig(id="r", name="Pad", driver="sim", role="presence", kind="pad", zone="left", person=None).model_dump(),
    ]), None, lambda: None)
    assert m.follow_owners([P("p1", "right"), P("p2", "left")]) is True
    assert {c.id: c.zone for c in m.configs} == {"w": "right", "r": "left"}
    assert m.follow_owners([P("p1", "right"), P("p2", "left")]) is False


# ---------- the snapshot says where the bed sees someone ----------
def test_snapshot_has_presence_per_side(tmp_path):
    with make(tmp_path) as c:
        z = c.get("/api/snapshot").json()["zones"]
        assert set(z) == {"left", "right"} and all(set(v) == {"present", "bed_present"} for v in z.values())


async def test_snapshot_zones_follow_the_bed_not_the_people(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    n.present("right", False)
    await n.step(6)
    z = n.c.snapshot()["zones"]
    assert z["left"]["bed_present"] is True and z["right"]["bed_present"] is False
    n.c.swap_sides()
    await n.step(2)
    assert n.c.snapshot()["zones"]["left"]["bed_present"] is True


# ---------- dials: hide old ones, the side the dial reports ----------
TEXT = ("dev1|Bedside Dial|2026.10.1|CrowPanel|sensor.bedside_dial_wifi|-61|1762500000\n"
        "dev1|Bedside Dial|2026.10.1|CrowPanel|text_sensor.bedside_dial_home_side|embertide_right|1762500000\n"
        "dev2|Old Dial|2026.5.3|M5|sensor.old_dial_wifi|unavailable|1760000000\n")


def test_dial_reports_its_side_last_seen_and_can_be_hidden(tmp_path):
    d = {x["name"]: x for x in parse_template(TEXT)}
    assert d["Bedside Dial"]["home"] == "embertide_right" and d["Old Dial"]["home"] is None
    assert d["Old Dial"]["last_seen"] == 1760000000 and not d["Old Dial"]["online"]

    def fake_ha(request):
        return httpx.Response(200, text=TEXT) if request.url.path == "/api/template" else httpx.Response(404)

    with make(tmp_path) as c:
        k = ctl(c)
        k.store.put("ha_connection", {"url": "http://ha.test:8123", "token": "secret"})
        k.sources.http_transport = httpx.MockTransport(fake_ha)
        right = next(p for p in k.household.people if p.zone == "right")
        k.store.put("ha_object_ids", {})
        dials = {x["name"]: x for x in c.get("/api/dials").json()["dials"]}
        new, old = dials["Bedside Dial"], dials["Old Dial"]
        assert new["side"] == "right" and new["side_from"] == "dial"                # the package default names the side
        assert old["side"] is None and old["side_from"] is None and old["hidden"] is False
        # a named person: their object id → their side; after a swap the dial follows them
        k.store.put("ha_object_ids", {right.id: "embertide_right"})
        assert c.post("/api/household/swap-sides", headers=H).status_code == 200
        assert next(x for x in c.get("/api/dials").json()["dials"] if x["name"] == "Bedside Dial")["side"] == "left"
        # what Studio saves wins and persists
        assert c.put(f"/api/dials/{new['id']}", headers=H, json={"side": "right"}).status_code == 200
        got = next(x for x in c.get("/api/dials").json()["dials"] if x["id"] == new["id"])
        assert got["side"] == "right" and got["side_from"] == "set"
        # hide the old one, then show it again
        assert c.put(f"/api/dials/{old['id']}/hidden", headers=H, json={"hidden": True}).status_code == 200
        assert next(x for x in c.get("/api/dials").json()["dials"] if x["id"] == old["id"])["hidden"] is True
        assert c.put(f"/api/dials/{old['id']}/hidden", headers=H, json={"hidden": False}).status_code == 200
        assert next(x for x in c.get("/api/dials").json()["dials"] if x["id"] == old["id"])["hidden"] is False
        assert c.put("/api/dials/BAD!/hidden", headers=H, json={"hidden": True}).status_code in (404, 422)


def test_only_the_owner_hides_dials():
    from embertide.auth import Principal, authorize
    sleeper = Principal(kind="user", id="u2", name="b", role="sleeper", person="p2")
    assert authorize(sleeper, "PUT", "/api/dials/abc/hidden", lambda pid: (True, True)) == "Only the owner can do that."
    # the dial list too (review 2026-11-09): it queries HA with the owner's token, writes beacon links, and shows
    # which person's phone was heard by which dial
    assert authorize(sleeper, "GET", "/api/dials", lambda pid: (True, True)) == "Only the owner can do that."


# ---------- each dial's Bluetooth beacon is part of that dial ----------
TEXT2 = ("dev1|Dial Main|2026.10.1|CrowPanel|sensor.dial_main_wifi|-61|1762500000\n"
         "dev2|Dial Two|2026.10.1|CrowPanel|sensor.dial_two_wifi|-60|1762500000\n"
         "dev2|Dial Two|2026.10.1|CrowPanel|sensor.dial_two_beacon|2|1762500000\n")


def test_beacons_belong_to_their_dial_and_follow_its_side(tmp_path):
    def fake_ha(request):
        return httpx.Response(200, text=TEXT2) if request.url.path == "/api/template" else httpx.Response(404)

    with make(tmp_path) as c:
        k = ctl(c)
        k.store.put("ha_connection", {"url": "http://ha.test:8123", "token": "secret"})
        k.sources.http_transport = httpx.MockTransport(fake_ha)
        # a hand-made setup (beacon names = dial names, one dial side saved) is kept and read as links
        k.store.put("dial_beacons", [{"minor": 1, "side": "right", "name": "Dial Main"}, {"minor": 2, "side": "left", "name": "Dial Two"}])
        r = c.get("/api/dials").json()
        d = {x["name"]: x for x in r["dials"]}
        assert d["Dial Main"]["beacon"] == 1 and d["Dial Main"]["side"] == "right" and d["Dial Main"]["side_from"] == "beacon"
        assert d["Dial Two"]["beacon"] == 2 and d["Dial Two"]["beacon_from"] == "dial"
        assert {b["minor"]: (b["dial"] or {}).get("name") for b in r["beacons"]} == {1: "Dial Main", 2: "Dial Two"}
        assert {b["minor"]: b["side"] for b in r["beacons"]} == {1: "right", 2: "left"}
        # picking a dial's side moves its beacon too
        assert c.put(f"/api/dials/{d['Dial Two']['id']}", headers=H, json={"side": "right"}).status_code == 200
        assert {b["minor"]: b["side"] for b in c.get("/api/dial-beacons").json()["dials"]}[2] == "right"
        # a phone hearing beacon 1 shows on that dial's row
        pid = k.household.people[0].id
        assert c.post(f"/api/people/{pid}/phone-context", headers=H, json={"sharing": ["dial"], "dials": [{"minor": 1, "rssi": -60}]}).status_code == 200
        main = next(x for x in c.get("/api/dials").json()["dials"] if x["name"] == "Dial Main")
        assert main["heard"] and main["heard"]["person"] == pid
        # the owner can say which beacon number a dial has
        assert c.put(f"/api/dials/{main['id']}", headers=H, json={"side": "left", "beacon": 3}).status_code == 200
        b = {x["minor"]: x for x in c.get("/api/dial-beacons").json()["dials"]}
        assert b[3]["side"] == "left" and b[3]["name"] == "Dial Main"


def test_a_hidden_old_dial_with_the_same_name_never_takes_the_current_dials_beacon(tmp_path):
    text = ("cur|Bedside Dial|2026.10.2|CrowPanel|sensor.cur_wifi|-60|1762500000\n"
            "main|Dial Main|2026.10.2|CrowPanel|sensor.main_wifi|-60|1762500000\n"
            "old|Bedside Dial|2026.5.3|M5Dial|sensor.old_wifi|unavailable|1750000000\n")

    def fake_ha(request):
        return httpx.Response(200, text=text) if request.url.path == "/api/template" else httpx.Response(404)

    with make(tmp_path) as c:
        k = ctl(c)
        k.store.put("ha_connection", {"url": "http://ha.test:8123", "token": "secret"})
        k.sources.http_transport = httpx.MockTransport(fake_ha)
        k.store.put("dial_beacons", [{"minor": 1, "side": "right", "name": "Dial Main"}, {"minor": 2, "side": "left", "name": "Bedside Dial"}])
        ids = {(x["name"], x["online"]): x["id"] for x in c.get("/api/dials").json()["dials"]}
        old = ids[("Bedside Dial", False)]
        # two dials share the name: the beacon isn't guessed for either until the old one is hidden
        assert c.put(f"/api/dials/{old}/hidden", headers=H, json={"hidden": True}).status_code == 200
        for _ in range(3):                                   # stable across reloads (no flip-flopping)
            d = {(x["name"], x["online"]): x for x in c.get("/api/dials").json()["dials"]}
            assert d[("Bedside Dial", True)]["beacon"] == 2
            assert d[("Bedside Dial", False)]["beacon"] is None
        assert {b["minor"]: (b["dial"] or {}).get("id") for b in c.get("/api/dials").json()["beacons"]}[2] == ids[("Bedside Dial", True)]


def test_bed_orientation_is_display_only_and_owner_set(tmp_path):
    from embertide.auth import Principal, authorize
    with make(tmp_path) as c:
        k = ctl(c)
        before = {p.id: p.zone for p in k.household.people}
        assert c.get("/api/bed-orientation").json() == {"from_foot": False}
        assert c.put("/api/bed-orientation", headers=H, json={"from_foot": True}).json() == {"from_foot": True}
        assert c.get("/api/bed-orientation").json() == {"from_foot": True}
        assert {p.id: p.zone for p in k.household.people} == before        # zones never move
    sleeper = Principal(kind="user", id="u2", name="b", role="sleeper", person="p2")
    assert authorize(sleeper, "PUT", "/api/bed-orientation", lambda pid: (True, True)) == "Only the owner can do that."
    assert authorize(sleeper, "GET", "/api/bed-orientation", lambda pid: (True, True)) is None
