"""Device setup simulations: a fake Home Assistant with many kinds of entities, devices added the way Studio's picker
adds them (shared connection + per-entity setup), mixed and matched, then driven through state changes, outages,
token changes, disappearing entities and duplicates. Checks that every device does what its setup card promised
and that every failure reads as something a person can act on."""

import time

import httpx
import pytest

from test_autopilot_suite import H, make

TOKEN = "fake-token-for-tests"


def ent(eid, state, name=None, **attrs):
    a = {"friendly_name": name or eid}
    a.update(attrs)
    return {"entity_id": eid, "state": state, "attributes": a}


class FakeHA:
    """A small mutable Home Assistant: set states, drop entities, go offline, return errors, rotate the token."""

    def __init__(self):
        self.token = TOKEN
        self.offline = False
        self.status = None                 # force an HTTP status for every call (e.g. 500)
        self.states = {e["entity_id"]: e for e in [
            ent("light.bedroom", "off", "Bedroom lamp"),
            ent("switch.fan", "off", "Fan"),
            ent("binary_sensor.radar_left", "off", "Radar left", device_class="occupancy"),
            ent("binary_sensor.radar_right", "off", "Radar right", device_class="occupancy"),
            ent("binary_sensor.bed_right_empty", "on", "Right side empty (reversed)", device_class="occupancy"),
            ent("binary_sensor.hall_motion", "off", "Hall motion", device_class="motion"),
            ent("sensor.bedroom_temperature", "70.2", "Bedroom temperature", device_class="temperature", unit_of_measurement="°F"),
            ent("sensor.bedroom_temperature_c", "21.0", "Bedroom temperature (C)", device_class="temperature", unit_of_measurement="°C"),
            ent("sensor.bedroom_humidity", "44", "Bedroom humidity", device_class="humidity", unit_of_measurement="%"),
            ent("sensor.bed_scale_left", "2.0", "Bed scale left", device_class="weight", unit_of_measurement="lb"),
            ent("sensor.bed_scale_right", "1.0", "Bed scale right", device_class="weight", unit_of_measurement="kg"),
            ent("sensor.home_alex_distance", "0", "Alex distance home", unit_of_measurement="mi"),
            ent("sensor.home_sam_distance", "0", "Sam distance home", unit_of_measurement="km"),
            ent("device_tracker.pixel", "home", "Pixel"),
            ent("person.sam", "home", "Sam"),
            ent("calendar.family", "off", "Family", message="", start_time="", end_time=""),
            ent("sensor.watch_heart_rate", "58", "Watch heart rate", unit_of_measurement="bpm"),
            ent("sensor.watch_skin_temperature", "93.2", "Watch skin temperature", unit_of_measurement="°F"),
            ent("sensor.co2", "650", "CO₂ in the bedroom — a very long friendly name that goes on and on", unit_of_measurement="ppm"),
            ent("sensor.broken", "unavailable", "Broken sensor"),
            ent("sensor.words", "sunny", "Weather words"),
        ]}

    def set(self, eid, state, **attrs):
        self.states[eid]["state"] = state
        self.states[eid]["attributes"].update(attrs)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("connection refused", request=request)
        if self.status:
            return httpx.Response(self.status)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401)
        path = request.url.path
        if path == "/api/config":
            return httpx.Response(200, json={"version": "2026.11.0", "location_name": "Home"})
        if path == "/api/states":
            return httpx.Response(200, json=list(self.states.values()))
        if path.startswith("/api/states/"):
            s = self.states.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=s) if s else httpx.Response(404)
        return httpx.Response(404)


@pytest.fixture
def world(tmp_path):
    with make(tmp_path) as c:
        ha = FakeHA()
        ctl = c.app.state.notifier.c
        ctl.sources.http_transport = httpx.MockTransport(ha)
        ctl.sources._sleep = _fast_sleep
        assert c.put("/api/ha/connection", headers=H, json={"url": "http://ha.test:8123", "token": TOKEN}).status_code == 200
        yield c, ha, ctl


async def _fast_sleep(s):
    import asyncio
    await asyncio.sleep(0.02)


def add(c, eid, kind, role, **kw):
    body = {"name": kw.pop("name", eid), "driver": "ha-entity", "role": role, "kind": kind, "params": {"conn": "ha", "entity_id": eid}}
    body.update(kw)
    return c.post("/api/sources", headers=H, json=body)


def wait(ctl, sid, pred, timeout=3.0):
    t = time.time()
    while time.time() - t < timeout:
        l = ctl.sources.latest.get(sid) or {}
        if pred(l):
            return l
        time.sleep(0.03)
    return ctl.sources.latest.get(sid) or {}


def listed(c, sid):
    return next(s for s in c.get("/api/sources").json()["sources"] if s["id"] == sid)


# ───────────── mix (a): radar per side + scale + room °F + light ─────────────
def test_mix_bedroom_sensors_read_and_vote(world):
    c, ha, ctl = world
    ids = {}
    for eid, kind, role, extra in [("binary_sensor.radar_left", "radar", "presence", {"zone": "left"}),
                                   ("binary_sensor.radar_right", "radar", "presence", {"zone": "right"}),
                                   ("sensor.bed_scale_left", "scale", "weight", {"zone": "left"}),
                                   ("sensor.bedroom_temperature", "room", "room_temp", {}),
                                   ("light.bedroom", "light", "light", {})]:
        r = add(c, eid, kind, role, **extra)
        assert r.status_code == 200, r.text
        ids[eid] = r.json()["id"]
    room = wait(ctl, ids["sensor.bedroom_temperature"], lambda l: l.get("value") is not None)
    assert room["value"] == pytest.approx(21.222, abs=0.01)                      # 70.2 °F → °C
    scale = wait(ctl, ids["sensor.bed_scale_left"], lambda l: l.get("value") is not None)
    assert scale["value"] == pytest.approx(0.907, abs=0.01)                      # lb → kg
    ha.set("binary_sensor.radar_left", "on")
    ha.set("sensor.bed_scale_left", "160")                                       # 72.6 kg
    wait(ctl, ids["sensor.bed_scale_left"], lambda l: (l.get("value") or 0) > 50)
    wait(ctl, ids["binary_sensor.radar_left"], lambda l: l.get("value") == 1.0)
    time.sleep(0.3)                                                              # settle times are short in tests? no: real
    for s in (ids["binary_sensor.radar_left"], ids["sensor.bed_scale_left"]):
        d = listed(c, s)
        assert d["votes"] and "token" not in str(d.get("params")) and TOKEN not in str(d)
    light = listed(c, ids["light.bedroom"])
    assert light["votes"] is False and light["value"] in (0.0, None)
    ha.set("light.bedroom", "on")
    assert wait(ctl, ids["light.bedroom"], lambda l: l.get("value") == 1.0)["value"] == 1.0


# ───────────── mix (b): phones, proximity in miles, HA calendar ─────────────
def test_phone_in_a_named_zone_counts_as_away(world):
    c, ha, ctl = world
    r = add(c, "device_tracker.pixel", "phone", "home", person="p1")
    sid = r.json()["id"]
    assert wait(ctl, sid, lambda l: l.get("value") == 1.0)["value"] == 1.0
    ha.set("device_tracker.pixel", "Work")                                       # HA names the zone it's in
    l = wait(ctl, sid, lambda l: l.get("value") == 0.0)
    assert l.get("value") == 0.0, l                                              # away, not "not a reading"
    ha.set("device_tracker.pixel", "not_home")
    assert wait(ctl, sid, lambda l: l.get("value") == 0.0)["value"] == 0.0


def test_person_entity_and_proximity_miles(world):
    c, ha, ctl = world
    p = add(c, "person.sam", "phone", "home", person="p2").json()["id"]
    ha.set("sensor.home_alex_distance", "10")                                    # already 10 mi out when it's added
    d = add(c, "sensor.home_alex_distance", "location", "location", person="p1").json()["id"]
    ha.set("person.sam", "Gym")
    assert wait(ctl, p, lambda l: l.get("value") == 0.0)["value"] == 0.0
    assert wait(ctl, d, lambda l: (l.get("value") or 0) > 15)["value"] == pytest.approx(16.09, abs=0.02)   # 10 mi → km


def test_ha_calendar_entity_gives_busy_until(world):
    from datetime import datetime, timedelta
    c, ha, ctl = world
    now = ctl.clock.now_ts()
    tz = ctl.clock.tz
    end = datetime.fromtimestamp(now, tz) + timedelta(hours=2)
    ha.set("calendar.family", "on", message="Concert", start_time=(end - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
           end_time=end.strftime("%Y-%m-%d %H:%M:%S"))
    sid = add(c, "calendar.family", "calendar", "calendar", person="p1").json()["id"]
    l = wait(ctl, sid, lambda l: (l.get("value") or 0) > 0)
    assert l["value"] == pytest.approx(end.timestamp(), abs=60)


# ───────────── mix (c): reversed presence, custom threshold, wearable ─────────────
def test_reversed_presence_custom_threshold_and_wearable(world):
    c, ha, ctl = world
    rev = add(c, "binary_sensor.bed_right_empty", "radar", "presence", zone="right", tuning={"invert": True, "min_on_s": 0, "hold_s": 0}).json()["id"]
    cus = add(c, "sensor.co2", "custom", "presence", zone="left", tuning={"compare": ">=", "threshold": 800, "min_on_s": 0}).json()["id"]
    hr = add(c, "sensor.watch_heart_rate", "wearable", "wearable_hr", zone="left", person="p1").json()["id"]
    skin = add(c, "sensor.watch_skin_temperature", "wearable", "skin_temp", zone="left", person="p1").json()["id"]
    wait(ctl, rev, lambda l: l.get("value") is not None)
    assert listed(c, rev)["vote"] == 0.0                                         # "on" means empty here
    ha.set("binary_sensor.bed_right_empty", "off")
    wait(ctl, rev, lambda l: l.get("value") == 0.0)
    time.sleep(0.1)
    assert listed(c, rev)["vote"] == 1.0
    wait(ctl, cus, lambda l: l.get("value") is not None)
    assert listed(c, cus)["vote"] == 0.0
    ha.set("sensor.co2", "900")
    wait(ctl, cus, lambda l: l.get("value") == 900.0)
    time.sleep(0.1)
    assert listed(c, cus)["vote"] == 1.0
    assert wait(ctl, hr, lambda l: l.get("value") is not None)["value"] == 58.0
    assert wait(ctl, skin, lambda l: l.get("value") is not None)["value"] == pytest.approx(34.0, abs=0.05)   # 93.2 °F


# ───────────── (d) wrong type, (e) duplicates, (f) many at once ─────────────
def test_wrong_role_for_type_is_corrected_and_side_required(world):
    c, ha, ctl = world
    r = add(c, "binary_sensor.radar_left", "radar", "room_temp", zone="left")
    assert r.status_code == 200 and r.json()["role"] == "presence"               # a radar measures presence
    r = add(c, "binary_sensor.radar_right", "radar", "presence")
    assert r.status_code == 422 and "side" in r.json()["detail"].lower()


def test_same_entity_twice_is_refused_plainly(world):
    c, ha, ctl = world
    assert add(c, "light.bedroom", "light", "light", name="Lamp").status_code == 200
    r = add(c, "light.bedroom", "light", "light", name="Lamp again")
    assert r.status_code == 409 and "already" in r.json()["detail"].lower() and "Lamp" in r.json()["detail"]

    feed = {"driver": "ics", "role": "calendar", "kind": "calendar", "person": "p1", "enabled": False}
    assert c.post("/api/sources", headers=H, json=dict(feed, name="Cal", params={"url": "https://Cal.Example.com/u/abc.ics"})).status_code == 200
    for same in ("webcal://cal.example.com/u/abc.ics", "https://cal.example.com/u/abc.ics/"):
        r = c.post("/api/sources", headers=H, json=dict(feed, name="Cal 2", params={"url": same}))
        assert r.status_code == 409, same
    other = c.post("/api/sources", headers=H, json=dict(feed, name="Cal 3", params={"url": "https://cal.example.com/u/xyz.ics"}))
    assert other.status_code == 200


def test_dropped_connection_is_worded_plainly():
    from embertide.sources import plain_error
    e = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    assert plain_error(e) == "Home Assistant dropped the connection (it may be restarting). Retrying."
    assert "dropped the connection" in plain_error(e, "the calendar feed", "ics")

def test_many_devices_at_once_all_report(world):
    c, ha, ctl = world
    spec = [("binary_sensor.radar_left", "radar", "presence", {"zone": "left"}), ("binary_sensor.radar_right", "radar", "presence", {"zone": "right"}),
            ("sensor.bed_scale_left", "scale", "weight", {"zone": "left"}), ("sensor.bed_scale_right", "scale", "weight", {"zone": "right"}),
            ("sensor.bedroom_temperature", "room", "room_temp", {}), ("sensor.bedroom_humidity", "room", "room_humidity", {}),
            ("light.bedroom", "light", "light", {}), ("device_tracker.pixel", "phone", "home", {"person": "p1"}),
            ("person.sam", "phone", "home", {"person": "p2"}), ("sensor.home_sam_distance", "location", "location", {"person": "p2"})]
    ids = [add(c, e, k, r, **x).json()["id"] for e, k, r, x in spec]
    for sid in ids:
        assert wait(ctl, sid, lambda l: l.get("value") is not None).get("value") is not None, sid
    assert c.get("/api/ha/connection").json()["devices"] == len(ids)


# ───────────── (g) entity disappears / goes unavailable / junk ─────────────
def test_disappearing_unavailable_and_junk_entities_explain_themselves(world):
    c, ha, ctl = world
    gone = add(c, "sensor.bedroom_humidity", "room", "room_humidity").json()["id"]
    wait(ctl, gone, lambda l: l.get("value") is not None)
    del ha.states["sensor.bedroom_humidity"]
    l = wait(ctl, gone, lambda l: bool(l.get("error")))
    assert "renamed or removed" in l["error"] and "Tune" in l["error"]
    broken = add(c, "sensor.broken", "room", "room_temp").json()["id"]
    l = wait(ctl, broken, lambda l: bool(l.get("note")))
    assert "unavailable" in l["note"]
    words = add(c, "sensor.words", "custom", "other").json()["id"]
    l = wait(ctl, words, lambda l: bool(l.get("note")))
    assert "isn't a reading" in l["note"]


# ───────────── (h) migrate an old light, (i) rotate the token, outages ─────────────
def test_migrate_old_device_and_rotate_token(world):
    c, ha, ctl = world
    old = c.post("/api/sources", headers=H, json={"name": "Old lamp", "driver": "ha-entity", "role": "light", "kind": "light",
                                                 "params": {"url": "http://ha.test:8123", "token": TOKEN, "entity_id": "light.bedroom"}}).json()["id"]
    assert old in c.get("/api/ha/connection").json()["can_migrate"]
    assert c.post("/api/ha/connection/migrate", headers=H).json()["moved"] == [old]
    assert "token" not in ctl.sources.get(old).params and ctl.sources.get(old).params["conn"] == "ha"
    room = add(c, "sensor.bedroom_temperature_c", "room", "room_temp").json()["id"]
    wait(ctl, room, lambda l: l.get("value") is not None)
    ha.token = "rotated-token"                                                   # HA token revoked
    ha.set("sensor.bedroom_temperature_c", "22.0")
    l = wait(ctl, room, lambda l: bool(l.get("error")))
    assert "token" in l["error"].lower() and "Integrations" in l["error"]
    assert c.put("/api/ha/connection", headers=H, json={"url": "http://ha.test:8123", "token": "rotated-token"}).status_code == 200
    assert wait(ctl, room, lambda l: l.get("value") == 22.0 and not l.get("error"))["value"] == 22.0
    ha.offline = True
    l = wait(ctl, room, lambda l: bool(l.get("error")))
    assert "Can't reach" in l["error"]
    ha.offline = False
    ha.set("sensor.bedroom_temperature_c", "22.5")
    assert wait(ctl, room, lambda l: l.get("value") == 22.5, timeout=5)["value"] == 22.5


def test_connection_errors_when_setting_up(world):
    c, ha, ctl = world
    r = c.put("/api/ha/connection", headers=H, json={"url": "http://ha.test:8123", "token": "nope"})
    assert r.status_code == 422 and "token" in r.json()["detail"].lower()
    r = c.put("/api/ha/connection", headers=H, json={"url": "not a url", "token": TOKEN})
    assert r.status_code == 422 and "address" in r.json()["detail"].lower()
    ha.offline = True
    r = c.put("/api/ha/connection", headers=H, json={"url": "http://ha.test:8123", "token": TOKEN})
    assert r.status_code == 422 and "reach" in r.json()["detail"].lower()
    ha.offline = False
    ha.status = 500
    assert c.get("/api/ha/entities?kind=light").status_code == 502
    ha.status = None


def test_removing_the_shared_connection_and_a_device(world):
    c, ha, ctl = world
    sid = add(c, "light.bedroom", "light", "light").json()["id"]
    wait(ctl, sid, lambda l: l.get("value") is not None)
    c.delete("/api/ha/connection", headers=H)
    l = wait(ctl, sid, lambda l: bool(l.get("error")))
    assert "Integrations" in l["error"]
    assert c.delete(f"/api/sources/{sid}", headers=H).status_code == 200
    assert all(s["id"] != sid for s in c.get("/api/sources").json()["sources"])


# ───────────── picker guesses ─────────────
def test_picker_guesses_and_filters(world):
    c, ha, ctl = world
    rows = {e["entity_id"]: e for e in c.get("/api/ha/entities?kind=custom").json()["entities"]}
    assert rows["sensor.bed_scale_left"]["guess_kind"] == "scale"
    assert rows["sensor.bedroom_temperature"]["guess_kind"] == "room" and rows["sensor.bedroom_humidity"]["guess_role"] == "room_humidity"
    assert rows["device_tracker.pixel"]["guess_kind"] == "phone" and rows["person.sam"]["guess_kind"] == "phone"
    assert rows["sensor.home_alex_distance"]["guess_kind"] == "location"
    assert rows["calendar.family"]["guess_kind"] == "calendar"
    assert rows["sensor.watch_skin_temperature"]["guess_role"] == "skin_temp"
    assert rows["binary_sensor.hall_motion"]["guess_kind"] in ("motion", "radar")
    lights = [e["entity_id"] for e in c.get("/api/ha/entities?kind=light").json()["entities"]]
    assert lights[0] == "light.bedroom" and "switch.fan" in lights and "sensor.co2" not in lights
    add(c, "light.bedroom", "light", "light")
    assert next(e for e in c.get("/api/ha/entities?kind=light").json()["entities"] if e["entity_id"] == "light.bedroom")["added"]


# ───────────── behaviour: votes combine as the setup cards promise; phones drive home/away ─────────────
def test_mixed_votes_combine_and_phone_zone_means_away(world):
    from datetime import datetime
    c, ha, ctl = world
    for x in c.get("/api/sources").json()["sources"]:                            # start from the bed strip alone
        c.delete(f"/api/sources/{x['id']}", headers=H)
    ctl.driver.scripted = False
    ctl.driver.set_presence("left", False)                                       # the bed strip says empty
    rad = add(c, "binary_sensor.radar_left", "radar", "presence", zone="left", tuning={"min_on_s": 0, "hold_s": 0}).json()["id"]
    sc = add(c, "sensor.bed_scale_left", "scale", "weight", zone="left", tuning={"min_on_s": 0}).json()["id"]
    ha.set("binary_sensor.radar_left", "on")
    ha.set("sensor.bed_scale_left", "2.0")
    wait(ctl, rad, lambda l: l.get("value") == 1.0)
    time.sleep(0.4)
    f = ctl.fuse("left")
    assert f is False, ctl.fused.get("left")                                     # strip 0.5 empty + scale 0.6 empty beat radar 0.35
    for v in ("40", "80", "120", "160"):                                         # someone lies down: weight climbs
        ha.set("sensor.bed_scale_left", v)
        time.sleep(0.15)
    wait(ctl, sc, lambda l: (l.get("value") or 0) > 60)
    time.sleep(0.4)
    assert ctl.fuse("left") is True, ctl.fused.get("left")                       # radar + scale (0.95) beat the strip (0.5)
    # a phone that's at "Work" for 25 minutes makes its person away
    ph = add(c, "device_tracker.pixel", "phone", "home", person="p1").json()["id"]
    wait(ctl, ph, lambda l: l.get("value") == 1.0)
    ha.set("device_tracker.pixel", "Work")
    wait(ctl, ph, lambda l: l.get("value") == 0.0)
    time.sleep(1.3)                                                              # (real phones are read every 5 s)
    later = datetime.fromtimestamp(ctl.clock.now_ts() + 25 * 60, ctl.clock.tz).strftime("%Y-%m-%dT%H:%M:%S")
    c.post("/api/sim", headers=H, json={"jump_to": later})
    time.sleep(0.6)
    from embertide.engine.context import person_context
    ctx = person_context(ctl.sources, ctl.store, ctl.household.people[0], ctl.clock.now_ts())
    assert ctx.home is False and "Wi-Fi" in ctx.why
