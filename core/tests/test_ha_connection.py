"""One shared Home Assistant connection, the entity picker, plain errors per connection type, and secrets that never
leave the server."""

import asyncio

import httpx
import pytest

from embertide.sources import SourceConfig, plain_error
from test_autopilot_suite import H, make

STATES = [
    {"entity_id": "light.bedroom", "state": "on", "attributes": {"friendly_name": "Bedroom lamp"}},
    {"entity_id": "switch.fan", "state": "off", "attributes": {"friendly_name": "Fan"}},
    {"entity_id": "binary_sensor.bed_left_presence", "state": "off", "attributes": {"friendly_name": "Bed left", "device_class": "occupancy"}},
    {"entity_id": "binary_sensor.hall_motion", "state": "off", "attributes": {"friendly_name": "Hall motion", "device_class": "motion"}},
    {"entity_id": "sensor.bedroom_temperature", "state": "70.2", "attributes": {"friendly_name": "Bedroom temp", "device_class": "temperature", "unit_of_measurement": "°F"}},
    {"entity_id": "sensor.bedroom_humidity", "state": "44", "attributes": {"friendly_name": "Bedroom humidity", "device_class": "humidity", "unit_of_measurement": "%"}},
    {"entity_id": "sensor.bed_scale_left", "state": "165", "attributes": {"friendly_name": "Scale left", "unit_of_measurement": "lb"}},
    {"entity_id": "sensor.home_phone_distance", "state": "3.1", "attributes": {"friendly_name": "Phone distance", "unit_of_measurement": "mi"}},
    {"entity_id": "device_tracker.pixel", "state": "home", "attributes": {"friendly_name": "Pixel"}},
    {"entity_id": "calendar.family", "state": "off", "attributes": {"friendly_name": "Family"}},
]
TOKEN = "test-token-not-real"


def fake_ha(request: httpx.Request) -> httpx.Response:
    if request.headers.get("authorization") != f"Bearer {TOKEN}":
        return httpx.Response(401)
    path = request.url.path
    if path == "/api/config":
        return httpx.Response(200, json={"version": "2026.11.0", "location_name": "Home"})
    if path == "/api/states":
        return httpx.Response(200, json=STATES)
    if path.startswith("/api/states/"):
        eid = path.rsplit("/", 1)[1]
        for s in STATES:
            if s["entity_id"] == eid:
                return httpx.Response(200, json=s)
        return httpx.Response(404)
    return httpx.Response(404)


def setup(c):
    ctl = c.app.state.notifier.c
    ctl.sources.http_transport = httpx.MockTransport(fake_ha)
    return ctl


def connect(c, token=TOKEN):
    return c.put("/api/ha/connection", headers=H, json={"url": "http://ha.test:8123", "token": token})


def test_connection_crud_masks_the_token(tmp_path):
    with make(tmp_path) as c:
        setup(c)
        assert c.get("/api/ha/connection").json()["configured"] is False
        assert connect(c, "wrong").status_code == 422                       # tested before saving
        r = connect(c)
        assert r.status_code == 200 and r.json()["test"]["version"] == "2026.11.0" and r.json()["test"]["entities"] == len(STATES)
        g = c.get("/api/ha/connection").json()
        assert g["configured"] and g["token"] == "•••" and TOKEN not in str(g)
        # saving again without a token keeps the stored one
        assert c.put("/api/ha/connection", headers=H, json={"url": "http://ha.test:8123", "token": "•••"}).status_code == 200
        assert c.post("/api/ha/connection/test", headers=H, json={"url": "http://ha.test:8123"}).json()["ok"]
        assert c.delete("/api/ha/connection", headers=H).json()["configured"] is False


def test_entities_filtered_by_device_type(tmp_path):
    with make(tmp_path) as c:
        setup(c)
        assert c.get("/api/ha/entities?kind=light").status_code == 409        # no connection yet
        connect(c)
        ids = lambda kind, q="": [e["entity_id"] for e in c.get(f"/api/ha/entities?kind={kind}&q={q}").json()["entities"]]  # noqa: E731
        assert ids("light") == ["light.bedroom", "switch.fan"]                 # lights first, switches after
        assert ids("radar")[0] == "binary_sensor.bed_left_presence" and "binary_sensor.hall_motion" in ids("radar")
        assert set(ids("room")) == {"sensor.bedroom_temperature", "sensor.bedroom_humidity"}
        assert ids("scale") == ["sensor.bed_scale_left"]
        assert ids("location")[0] == "sensor.home_phone_distance"
        assert ids("calendar") == ["calendar.family"]
        assert len(ids("custom")) == len(STATES)
        assert ids("custom", "bedroom") == ["light.bedroom", "sensor.bedroom_humidity", "sensor.bedroom_temperature"] or \
            set(ids("custom", "bedroom")) == {"light.bedroom", "sensor.bedroom_humidity", "sensor.bedroom_temperature"}


def test_device_uses_shared_connection_and_never_shows_the_token(tmp_path):
    with make(tmp_path) as c:
        ctl = setup(c)
        connect(c)
        body = {"name": "Bedroom temp", "driver": "ha-entity", "role": "room_temp", "kind": "room",
                "params": {"conn": "ha", "entity_id": "sensor.bedroom_temperature", "url": "x", "token": "should-be-dropped"}}
        r = c.post("/api/sources", headers=H, json=body)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        cfg = ctl.sources.get(sid)
        assert "token" not in cfg.params and "url" not in cfg.params
        asyncio.run(_poll(ctl, cfg))
        assert ctl.sources.latest[sid]["value"] == pytest.approx((70.2 - 32) * 5 / 9, abs=0.01)   # °F → °C
        listing = c.get("/api/sources").json()
        assert TOKEN not in str(listing)


async def _poll(ctl, cfg):
    _, token = ctl.sources.ha_creds(cfg)
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, transport=ctl.sources.http_transport) as http:
        await ctl.sources.poll_ha(cfg, http)


def test_units_lb_and_miles(tmp_path):
    with make(tmp_path) as c:
        ctl = setup(c)
        connect(c)
        scale = SourceConfig(id="s1", name="Scale", driver="ha-entity", role="weight", kind="scale", zone="left",
                             params={"conn": "ha", "entity_id": "sensor.bed_scale_left"})
        dist = SourceConfig(id="d1", name="Dist", driver="ha-entity", role="location", kind="location", person="p1",
                            params={"conn": "ha", "entity_id": "sensor.home_phone_distance"})
        ctl.sources.configs += [scale, dist]
        asyncio.run(_poll(ctl, scale))
        asyncio.run(_poll(ctl, dist))
        assert ctl.sources.latest["s1"]["value"] == pytest.approx(165 * 0.45359237, abs=0.01)
        assert ctl.sources.latest["d1"]["value"] == pytest.approx(3.1 * 1.609344, abs=0.01)


def test_migrate_moves_per_device_tokens_onto_the_shared_connection(tmp_path):
    with make(tmp_path) as c:
        ctl = setup(c)
        old = SourceConfig(id="o1", name="Old light", driver="ha-entity", role="light", kind="light",
                           params={"url": "http://ha.test:8123", "token": TOKEN, "entity_id": "light.bedroom"})
        other = SourceConfig(id="o2", name="Other HA", driver="ha-entity", role="light", kind="light",
                             params={"url": "http://other:8123", "token": "x", "entity_id": "light.x"})
        ctl.sources.configs += [old, other]
        connect(c)
        assert c.get("/api/ha/connection").json()["can_migrate"] == ["o1"]
        r = c.post("/api/ha/connection/migrate", headers=H).json()
        assert r["moved"] == ["o1"] and ctl.sources.get("o1").params == {"entity_id": "light.bedroom", "conn": "ha"}
        assert ctl.sources.get("o2").params["url"] == "http://other:8123"     # a different HA is left alone


def test_editing_a_calendar_keeps_the_masked_link_and_accepts_a_new_one(tmp_path):
    with make(tmp_path) as c:
        ctl = setup(c)
        body = {"name": "Cal", "driver": "ics", "role": "calendar", "kind": "calendar", "person": "p1", "enabled": False,
                "params": {"url": "https://user.fm/calendar/v1-SECRETONE/cal.ics"}}
        sid = c.post("/api/sources", headers=H, json=body).json()["id"]
        shown = next(s for s in c.get("/api/sources").json()["sources"] if s["id"] == sid)
        assert "SECRET" not in str(shown)
        # Tune → save without touching the link: the masked value comes back and the real link is kept
        c.put(f"/api/sources/{sid}", headers=H, json={**body, "name": "My calendar", "params": shown["params"]})
        assert ctl.sources.get(sid).params["url"].endswith("v1-SECRETONE/cal.ics")
        # a new link replaces it
        c.put(f"/api/sources/{sid}", headers=H, json={**body, "params": {"url": "https://user.fm/calendar/v1-NEWTWO/cal.ics"}})
        assert ctl.sources.get(sid).params["url"].endswith("v1-NEWTWO/cal.ics")


def test_ha_endpoints_are_owner_only(tmp_path):
    from fastapi.testclient import TestClient
    from embertide.app import create_app
    from embertide.config import Config
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", True
    with TestClient(create_app(cfg)) as c:
        for m, path in (("get", "/api/ha/connection"), ("get", "/api/ha/entities"), ("delete", "/api/ha/connection")):
            assert getattr(c, m)(path, headers=H).status_code in (401, 403)


def resp(status):
    req = httpx.Request("GET", "https://x")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(status, request=req))


def test_plain_errors_per_connection_type():
    assert "calendar link no longer works" in plain_error(resp(404), "the calendar feed", "ics")
    assert "refused the link" in plain_error(resp(403), "the calendar feed", "ics")
    assert "web page" in plain_error(ValueError("it's a web page, not a calendar feed"), "the calendar feed", "ics")
    assert "entity" not in plain_error(resp(404), "the calendar feed", "ics")            # no HA wording for calendars
    assert "renamed or removed" in plain_error(resp(404), "Home Assistant", "ha-entity")
    assert "MQTT broker" in plain_error(ConnectionRefusedError("x"), "the MQTT broker", "mqtt-topic")
    assert "Integrations" in plain_error(KeyError("no shared"), "Home Assistant", "ha-entity")


def test_entities_come_with_a_guessed_type(tmp_path):
    with make(tmp_path) as c:
        setup(c)
        connect(c)
        g = {e["entity_id"]: (e["guess_kind"], e["guess_role"]) for e in c.get("/api/ha/entities?kind=custom").json()["entities"]}
        assert g["light.bedroom"] == ("light", "light")
        assert g["binary_sensor.bed_left_presence"] == ("radar", "presence")
        assert g["binary_sensor.hall_motion"][0] in ("motion", "radar")
        assert g["sensor.bedroom_temperature"] == ("room", "room_temp")
        assert g["sensor.bedroom_humidity"] == ("room", "room_humidity")
        assert g["sensor.bed_scale_left"] == ("scale", "weight")
        assert g["sensor.home_phone_distance"] == ("location", "location")
        assert g["device_tracker.pixel"] == ("phone", "home")
        assert g["calendar.family"] == ("calendar", "calendar")


def test_mqtt_broker_password_is_masked_and_kept(tmp_path):
    with make(tmp_path) as c:
        ctl = setup(c)
        body = {"name": "Scale", "driver": "mqtt-topic", "role": "weight", "kind": "scale", "zone": "left", "enabled": False,
                "params": {"url": "mqtt://bed:pw-not-real@broker.test:1883", "topic": "scale/left"}}
        sid = c.post("/api/sources", headers=H, json=body).json()["id"]
        shown = next(s for s in c.get("/api/sources").json()["sources"] if s["id"] == sid)
        assert "pw-not-real" not in str(shown) and shown["params"]["url"] == "mqtt://bed:•••@broker.test:1883"
        # Tune → change only the topic: the broker password stays
        c.put(f"/api/sources/{sid}", headers=H, json={**body, "params": {**shown["params"], "topic": "scale/left2"}})
        p = ctl.sources.get(sid).params
        assert p["url"] == "mqtt://bed:pw-not-real@broker.test:1883" and p["topic"] == "scale/left2"


def test_saved_token_never_goes_to_a_changed_address(tmp_path):
    """A stolen session can't point the connection at its own server and collect the saved token."""
    seen = []
    with make(tmp_path) as c:
        setup(c)
        connect(c)
        orig = c.app.state.oura.c.sources.http_transport
        c.app.state.oura.c.sources.http_transport = httpx.MockTransport(lambda req: (seen.append(str(req.url)), fake_ha(req))[1])
        r = c.post("/api/ha/connection/test", headers=H, json={"url": "http://attacker.test:8123"})
        assert r.status_code == 422 and "again" in r.json()["detail"]
        r = c.put("/api/ha/connection", headers=H, json={"url": "http://attacker.test:8123", "token": "•••"})
        assert r.status_code == 422
        assert not [u for u in seen if "attacker" in u]
        c.app.state.oura.c.sources.http_transport = orig
