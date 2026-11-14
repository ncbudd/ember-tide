"""Water leak emergency off: source config, engine hold, API/auth, HA bridge, urgent notifications and hub messages."""

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.drivers.profiles import ORION
from embertide.ha_bridge import BASE, HaBridge
from embertide.ha_connection_api import fit_rank, guess
from embertide.notify import Notifier
from embertide.sources import DEVICE_TYPES, TYPES, SourceConfig, SourceManager, _to_value
from test_auth import H, PW, invite_partner, setup_owner
from test_notify import Fake, providers
from test_scenarios import Night


def _add_leak_source(n: Night, sid: str = "leak1", name: str = "Under-bed sensor", *, driver: str = "mqtt-topic") -> SourceConfig:
    if n.c.sources is None:
        n.c.sources = SourceManager(n.c.store, n.clock, lambda: n.bed)
    c = SourceConfig(
        id=sid,
        name=name,
        driver=driver,
        role="leak",
        kind="leak",
        params={"topic": f"sensors/{sid}/state"} if driver == "mqtt-topic" else {"conn": "ha", "entity_id": f"binary_sensor.{sid}"},
    )
    n.c.sources.configs.append(c)
    return c


def _set_leak(n: Night, c: SourceConfig, raw_value, at: float | None = None) -> None:
    ts = n.clock.now_ts() if at is None else at
    mapped = n.c.sources.map_raw(c, raw_value)
    v = mapped[0] if mapped is not None else _to_value(raw_value)
    n.c.sources._sample(c, v, ts)


def test_leak_source_type_and_ha_picker():
    assert "leak" in TYPES
    dt = TYPES["leak"]
    assert dt in DEVICE_TYPES
    assert dt["label"] == "Water leak sensor"
    assert dt["role"] == "leak"
    assert dt["votes"] is False
    assert dt["side"] is False
    assert _to_value("wet") == 1.0
    assert _to_value("dry") == 0.0
    assert _to_value("water_leak") == 1.0
    assert _to_value("no_leak") == 0.0

    moisture = {"entity_id": "binary_sensor.bed_floor", "device_class": "moisture", "name": "Floor sensor"}
    named = {"entity_id": "binary_sensor.hose_leak_check", "device_class": "", "name": "Hose leak"}
    own = {"entity_id": "binary_sensor.embertide_leak_hold", "device_class": "moisture", "name": "Leak hold"}
    assert fit_rank("leak", moisture) == 0
    assert fit_rank("leak", named) == 1
    assert fit_rank("leak", own) is None
    assert guess(moisture) == ("leak", "leak")


def test_multiple_leak_sensors_allowed_via_api(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False
    with TestClient(create_app(cfg)) as c:
        r1 = c.post("/api/sources", headers=H, json={
            "name": "Under hub leak sensor", "driver": "mqtt-topic", "role": "leak", "kind": "leak",
            "params": {"url": "mqtt://broker.local", "topic": "zigbee/hub_leak/water_leak"},
        })
        r2 = c.post("/api/sources", headers=H, json={
            "name": "Headboard leak sensor", "driver": "mqtt-topic", "role": "leak", "kind": "leak",
            "params": {"url": "mqtt://broker.local", "topic": "zigbee/headboard_leak/water_leak"},
        })
        assert r1.status_code == 200 and r2.status_code == 200
        # Leak sensors are bed-wide (not tied to a side) and do not vote on presence.
        assert r1.json()["zone"] is None and r1.json()["votes"] is False
        assert len([s for s in c.get("/api/sources").json()["sources"] if s["kind"] == "leak"]) == 2


async def test_wet_sensor_turns_both_sides_off_immediately_even_mid_flash_and_stays_held(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    leak = _add_leak_source(n, name="Under-hub sensor")
    _set_leak(n, leak, "dry")
    n.present("left", True)
    n.present("right", True)
    await n.until("2026-11-05T23:00")
    assert n.side("left").on and n.side("right").on

    # Start a hot flash on left and simulate a very recent command so normal backoff would wait.
    now = n.clock.now_ts()
    n.e(n.p1).flash("hot", now)
    await n.step(1)
    assert n.e(n.p1).s.flash is not None
    n.c._last_cmd["left"] = time.monotonic()
    n.c._last_cmd["right"] = time.monotonic()

    # Leak sensor gets wet -> both sides must turn off on the very next tick (drain inflight send task).
    _set_leak(n, leak, "wet")
    snap = await n.c.tick()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not n.side("left").on
    assert not n.side("right").on
    assert snap["leak_hold"] is not None
    assert snap["leak_hold"]["sensor"] == "Under-hub sensor"
    assert snap["leak_hold"]["wet"] is True

    # Multiple ticks while wet do not turn sides back on and record leak_detected once.
    await n.step(5)
    assert not n.side("left").on and not n.side("right").on
    detected = [e for e in n.c.store.events(limit=100) if e["kind"] == "leak_detected"]
    assert len(detected) == 1
    assert detected[0]["sensor"] == "Under-hub sensor"

    # Sensor dries out -> bed STILL stays off until cleared in the app.
    _set_leak(n, leak, "dry")
    await n.step(5)
    snap2 = n.c.snapshot()
    assert snap2["leak_hold"] is not None and snap2["leak_hold"]["wet"] is False
    assert not n.side("left").on and not n.side("right").on

    # Direct engine mutations cannot bypass the hold on tick either.
    n.e(n.p1).adjust(n.clock.now_ts(), temp_c=28.0, until="next_phase")
    n.e(n.p2).flash("cold", n.clock.now_ts())
    await n.c.tick()
    assert not n.side("left").on and not n.side("right").on

    # Clearing while dry releases the hold, records leak_cleared, and lets the running night resume.
    ok, err = n.c.clear_leak_hold(actor="p1")
    assert ok and err is None
    await n.c.tick(force={n.p1.id, n.p2.id})
    await n.step(2)
    assert n.c.snapshot()["leak_hold"] is None
    assert any(e["kind"] == "leak_cleared" for e in n.c.store.events(limit=100))
    assert n.side("left").on and n.side("right").on


async def test_hub_low_water_does_not_trigger_emergency_off(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-05T23:00")
    n.bed.low_water = True
    await n.step(5)
    assert n.c.leak_hold is None
    assert n.side("left").on


def test_api_refuses_turn_on_with_409_and_clear_permissions(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, True
    with TestClient(create_app(cfg)) as owner:
        setup_owner(owner, person="p1")
        sleeper_code = invite_partner(owner, person="p2", role="sleeper")
        viewer_code = invite_partner(owner, person="p2", role="viewer")

        sleeper = TestClient(owner.app)
        assert sleeper.post("/api/auth/accept", json={"code": sleeper_code, "username": "sam", "password": PW}).status_code == 200
        viewer = TestClient(owner.app)
        assert viewer.post("/api/auth/accept", json={"code": viewer_code, "username": "guest", "password": PW}).status_code == 200

        ctl = owner.app.state.notifier.c
        leak = SourceConfig(id="l1", name="Hose sensor", driver="mqtt-topic", role="leak", kind="leak", params={"topic": "bed/leak"})
        ctl.sources.configs.append(leak)
        ctl.sources._sample(leak, 1.0, ctl.clock.now_ts())
        snap = owner.get("/api/snapshot").json()
        # Snapshot has leak_hold once tick runs (initial tick triggers it).
        if not snap.get("leak_hold"):
            import asyncio
            asyncio.run(ctl.tick())
            snap = owner.get("/api/snapshot").json()
        assert snap["leak_hold"]["sensor"] == "Hose sensor"

        expected = "The bed is off because a leak sensor went off. Check for water, then clear it in the app."
        for method, path, body in [
            ("post", "/api/people/p1/adjust", {"delta_c": 1.0}),
            ("post", "/api/people/p1/flash", {"kind": "hot"}),
            ("post", "/api/people/p1/flash/extend", None),
            ("post", "/api/people/p1/tonight", {"action": "start_now"}),
            ("post", "/api/people/p1/tonight", {"action": "resume"}),
            ("post", "/api/guest/left/start", {"temp_c": 21.0}),
        ]:
            r = getattr(owner, method)(path, headers=H, json=body) if body is not None else getattr(owner, method)(path, headers=H)
            assert r.status_code == 409, (path, r.text)
            assert r.json()["detail"] == expected

        # nothing that moves water runs from the console during the hold either
        r = owner.post("/api/console", headers=H, json={"command": "FILL START"})
        assert r.status_code == 409 and r.json()["detail"] == expected

        # Still wet: even owner cannot clear yet.
        assert owner.post("/api/leak/clear", headers=H).status_code == 409

        # Dry the sensor: viewer is forbidden (403); sleeper is allowed (200).
        ctl.sources._sample(leak, 0.0, ctl.clock.now_ts() + 5)
        assert viewer.post("/api/leak/clear", headers=H).status_code == 403
        cleared = sleeper.post("/api/leak/clear", headers=H)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["leak_hold"] is None

        # Re-wet and check owner can also clear once dry.
        ctl.sources._sample(leak, 1.0, ctl.clock.now_ts() + 10)
        import asyncio
        asyncio.run(ctl.tick())
        assert ctl.leak_hold is not None
        ctl.sources._sample(leak, 0.0, ctl.clock.now_ts() + 15)
        assert owner.post("/api/leak/clear", headers=H).status_code == 200
        assert ctl.leak_hold is None


async def test_ha_bridge_exposes_leak_hold_and_refuses_turn_on(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    leak = _add_leak_source(n, name="Mat sensor")
    b = HaBridge(n.c, "mqtt://x")

    disc = dict(b.discovery())
    cfg_topic = "homeassistant/binary_sensor/embertide_leak_hold/config"
    assert cfg_topic in disc
    assert disc[cfg_topic]["name"] == "Leak hold"
    assert disc[cfg_topic]["device_class"] == "moisture"

    assert b.states(await n.c.tick())[f"{BASE}/bed/leak_hold"] == "OFF"

    _set_leak(n, leak, "wet")
    snap = await n.c.tick()
    assert b.states(snap)[f"{BASE}/bed/leak_hold"] == "ON"

    for topic, payload in [
        ("embertide/p1/set/mode", "heat_cool"),
        ("embertide/p1/set/target", "72"),
        ("embertide/p1/set/flash", "hot"),
        ("embertide/p1/set/tonight", "resume"),
    ]:
        res = await b.handle(topic, payload)
        assert res and res.startswith("refused:")
        assert n.e(n.p1).s.target_c is None
        assert n.e(n.p1).s.flash is None

    # Safe off/skip commands remain accepted.
    assert await b.handle("embertide/p1/set/mode", "off") == "side off tonight"


async def test_leak_notification_urgent_quiet_hours_household_and_morning(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:30")
    fake = Fake()
    n.c.notifier = Notifier(n.c, "https://bed.example", client=fake.client())
    n.c.notifier.save_providers(providers())
    leak = _add_leak_source(n, name="Under-hub pad")
    n.present("left", True)
    await n.until("2026-11-05T23:30")          # quiet hours + sleeping in bed

    _set_leak(n, leak, "wet")
    snap = await n.c.tick()
    assert n.c.notifier._snap is snap
    # Drain async delivery tasks spawned by controller.tick() -> notifier.observe().
    for t in list(n.c.notifier._tasks):
        await t

    # Sent immediately in quiet hours, priority 5, urgent, to everyone (p1 ntfy + p2 HA + household).
    assert any(c["json"].get("title") == "Possible leak: bed turned off" for c in fake.calls)
    leak_calls = [c for c in fake.calls if "Possible leak" in str(c["json"])]
    assert {c["json"].get("topic") or c["url"] for c in leak_calls} == {
        "alex-bed",
        "house",
        "http://ha.local:8123/api/services/notify/mobile_app_sam",
    }
    ntfy_call = next(c["json"] for c in leak_calls if c["json"].get("topic") == "alex-bed")
    assert ntfy_call["priority"] == 5
    assert "Under-hub pad" in ntfy_call["message"]
    assert "check" in ntfy_call["message"].lower()

    # Ticking again does not resend the notification.
    before = len(fake.calls)
    await n.step(3)
    for t in list(n.c.notifier._tasks):
        await t
    assert len(fake.calls) == before

    # Advance to morning after a night in bed -> morning summary mentions the overnight leak.
    await n.until("2026-11-06T07:30")
    msg = n.c.notifier._morning(n.p1, "2026-11-05", n.clock.now_ts())
    assert msg is not None
    assert "Possible leak detected overnight" in msg.body
    assert "Under-hub pad" in msg.body


async def test_hub_water_and_fault_notifications_plain_english(tmp_path):
    n = Night(tmp_path, "2026-11-05T14:00")
    n.bed.profile = ORION
    fake = Fake()
    n.c.notifier = Notifier(n.c, None, client=fake.client())
    snap = n.c.snapshot()
    snap["system"]["hub"] = {
        "line": "Check the water",
        "ok": False,
        "water_ok": False,
        "safety_ok": True,
        "faults": [
            {"code": "fan_stopped", "name": "Fan isn't running", "zone": None},
            {"code": "E99", "name": "The hub reported a problem", "zone": "left"},
        ],
    }
    out = n.c.notifier.observe(n.clock.now_ts(), snap)
    hub_msgs = [m for m in out if m.kind == "hub"]
    assert len(hub_msgs) == 3
    by_text = [(m.title + " " + m.body) for m in hub_msgs]
    assert any("Water is low" in t and "distilled water" in t.lower() for t in by_text)
    assert any("Fan isn't running" in t and "vents" in t.lower() for t in by_text)
    assert any("The hub reported a fault (E99)" in t and "Check the hub, hoses and vents." in t for t in by_text)
