"""Home Assistant bridge: the entities it announces, the states it publishes, and the commands it accepts."""

import pytest

from embertide.ha_bridge import HaBridge
from embertide.units import f_to_c
from test_scenarios import Night


async def test_discovery_covers_what_the_dials_need(tmp_path):
    n = Night(tmp_path, "2026-11-05T23:30")
    b = HaBridge(n.c, "mqtt://x")
    ids = {c["default_entity_id"] for t, c in b.discovery()}
    for who in ("left", "right"):
        for e in (f"climate.embertide_{who}", f"button.embertide_{who}_hot_flash", f"button.embertide_{who}_cold_flash",
                  f"sensor.embertide_{who}_phase", f"binary_sensor.embertide_{who}_in_bed",
                  f"number.embertide_{who}_wind_down_temp", f"number.embertide_{who}_deep_temp"):
            assert e in ids, e
    clim = next(c for t, c in b.discovery() if t.endswith("climate/embertide_left/config"))
    assert clim["modes"] == ["off", "heat_cool"] and clim["temperature_unit"] == "F"
    assert all(c["unique_id"] for _, c in b.discovery())


async def test_object_ids_survive_a_rename(tmp_path):
    n = Night(tmp_path, "2026-11-05T23:30")
    b = HaBridge(n.c, "mqtt://x")
    assert b.oid(n.p1) == "embertide_left"
    n.p1.name = "Jordan"
    assert b.oid(n.p1) == "embertide_left"            # the dial's entity IDs don't change under it


async def test_states_follow_the_night(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T00:30")
    b = HaBridge(n.c, "mqtt://x")
    st = b.states(n.c.snapshot())
    assert st["embertide/p1/mode"] == "heat_cool"
    assert st["embertide/p1/phase"] == "Deep" and st["embertide/p1/target"] == "63"
    assert st["embertide/p1/in_bed"] == "ON"
    assert st["embertide/p1/action"] in ("cooling", "idle", "heating")
    assert st["embertide/p1/phase_temp/deep"] == "63"


async def test_commands_from_home_assistant(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T00:30")
    b = HaBridge(n.c, "mqtt://x")
    e = n.e(n.p1)
    assert await b.handle("embertide/p1/set/target", "66") == "target 66"          # the dial turning
    assert e.s.target_c == pytest.approx(f_to_c(66), abs=0.05)
    assert await b.handle("embertide/p1/set/flash", "hot")                          # the dial's hot flash
    assert e.s.flash and e.s.flash.kind == "hot"
    await b.handle("embertide/p1/set/flash", "stop")
    assert await b.handle("embertide/p1/set/mode", "off") == "side off tonight"     # the dial's power button
    assert e.s.skip
    assert await b.handle("embertide/p1/set/mode", "heat_cool")
    assert not e.s.skip
    assert await b.handle("embertide/p1/set/phase_temp/deep", "61") == "Deep = 61"  # the offsets page
    assert next(p for p in n.p1.rhythms[0].phases if p.id == "deep").temp_c == pytest.approx(f_to_c(61), abs=0.01)
    # junk is ignored, never half-applied
    assert await b.handle("embertide/p1/set/target", "warm") is None
    assert await b.handle("embertide/p1/set/target", "500") is None
    assert await b.handle("embertide/nobody/set/target", "66") is None
    assert await b.handle("embertide/p1/set/phase_temp/nope", "61") is None
    assert await b.handle("other/p1/set/target", "66") is None


async def test_turning_on_in_the_daytime_means_on_by_hand(tmp_path):
    n = Night(tmp_path, "2026-11-05T14:00")
    await n.step(1)
    b = HaBridge(n.c, "mqtt://x")
    assert await b.handle("embertide/p1/set/mode", "heat_cool") == "side on by hand"
    await n.step(2)
    assert n.side("left").on


async def test_live_broker_round_trip(tmp_path):
    """Against a real MQTT broker: discovery is retained, states arrive, a command comes back in."""
    amqtt = pytest.importorskip("amqtt.broker")
    import asyncio
    import json
    import socket

    import aiomqtt
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    broker = amqtt.Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
                           "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
    await broker.start()
    try:
        n = Night(tmp_path, "2026-11-05T22:20")
        n.present("left", True)
        await n.until("2026-11-06T00:30")
        b = HaBridge(n.c, f"mqtt://127.0.0.1:{port}")
        await b.start()
        for _ in range(50):
            if b.state == "connected":
                break
            await asyncio.sleep(0.1)
        assert b.state == "connected"
        await b.publish_state(n.c.snapshot())
        async with aiomqtt.Client("127.0.0.1", port) as ha:                          # plays Home Assistant
            await ha.subscribe("homeassistant/climate/+/config")
            await ha.subscribe("embertide/p1/phase")
            got = {}
            async with asyncio.timeout(5):
                async for m in ha.messages:
                    got[str(m.topic)] = bytes(m.payload).decode()
                    if "homeassistant/climate/embertide_left/config" in got and "embertide/p1/phase" in got:
                        break
            assert json.loads(got["homeassistant/climate/embertide_left/config"])["default_entity_id"] == "climate.embertide_left"
            assert got["embertide/p1/phase"] == "Deep"
            await ha.publish("embertide/p1/set/target", b"67")
            for _ in range(50):
                if b.handled:
                    break
                await asyncio.sleep(0.1)
        assert b.handled == 1 and n.e(n.p1).s.target_c == pytest.approx(f_to_c(67), abs=0.05)
        await b.stop()
    finally:
        await broker.shutdown()


def test_saved_mqtt_password_is_kept_not_echoed(tmp_path):
    from fastapi.testclient import TestClient
    from embertide.app import create_app
    from embertide.config import Config
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth, cfg.ha_mqtt_url = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False, None
    H = {"X-Embertide": "1"}
    with TestClient(create_app(cfg)) as c:
        r = c.put("/api/integrations/ha", json={"host": "127.0.0.1", "port": 9, "username": "embertide", "password": "p@ss w0rd!"}, headers=H).json()
        assert r["password_saved"] and "p@ss" not in str(r)
        c.put("/api/integrations/ha", json={"host": "127.0.0.1", "port": 9, "username": "embertide", "password": "", "keep_password": True}, headers=H)
        assert c.app.state.ha.url == "mqtt://embertide:p%40ss%20w0rd%21@127.0.0.1:9"     # kept, special characters intact  (publish-check: ok, fake)
        c.put("/api/integrations/ha", json={"host": "", "port": 1883}, headers=H)


async def test_feel_buttons_from_a_dial(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T00:30")
    b = HaBridge(n.c, "mqtt://x")
    assert "button.embertide_left_feel_too_hot" in {c["default_entity_id"] for _, c in b.discovery()}
    before = n.e(n.p1).s.target_c
    assert await b.handle("embertide/p1/set/feel", "too_hot") == "feels too hot"
    assert n.events("feel")[-1]["feel"] == "too_hot" and n.events("feel")[-1]["source"] == "ha"
    assert n.e(n.p1).s.target_c == before                  # logged for Autopilot, nothing changed
    assert await b.handle("embertide/p1/set/feel", "lukewarm") is None


async def test_dial_helpers_tonight_plan_flash_pause(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T00:30")
    b = HaBridge(n.c, "mqtt://x")
    e = n.e(n.p1)
    e.flash("hot", n.clock.now_ts())
    await n.step(1)
    st = b.states(n.c.snapshot())
    assert st["embertide/p1/flash_end"] != "None" and st["embertide/p1/plan"] == "63"
    rows = st["embertide/p1/tonight"].split(";")
    assert rows[0].startswith("now|Deep|") and rows[-1].endswith("|Side off|") and len(st["embertide/p1/tonight"]) <= 250
    assert st["embertide/p1/sharing"] == "flash"                  # default: partner may flash, not adjust
    assert st["embertide/p1/color"] == "#5fb8b0"                  # left side default (teal)
    n.p1.settings.color = "rose"
    assert b.states(n.c.snapshot())["embertide/p1/color"] == "#ec8fae"
    assert await b.handle("embertide/p1/set/tonight", "pause") == "tonight pause"
    assert e.s.pause_until is not None
    assert await b.handle("embertide/p1/set/tonight", "explode") is None


async def test_on_by_hand_is_never_called_off(tmp_path):
    n = Night(tmp_path, "2026-11-05T14:00")
    await n.step(1)
    b = HaBridge(n.c, "mqtt://x")
    await b.handle("embertide/p1/set/target", "62")
    await n.step(1)
    st = b.states(n.c.snapshot())
    assert st["embertide/p1/mode"] == "heat_cool" and st["embertide/p1/phase"] == "On"


async def test_morning_checkin_from_the_dial(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T00:30")
    b = HaBridge(n.c, "mqtt://x")
    assert b.states(n.c.snapshot())["embertide/p1/checkin"] == "none"      # during the night: no
    assert b.states(n.c.snapshot())["embertide/p1/name"] == "Left"
    n.present("left", False)
    await n.until("2026-11-06T08:00")                                        # night over (wake 06:30 + after-wake)
    assert b.states(n.c.snapshot())["embertide/p1/checkin"] == "due"
    assert await b.handle("embertide/p1/set/feel", "too_hot") == "morning check-in: warm"
    assert n.store.get("feedback:p1:2026-11-05")["temp"] == "warm"
    await n.step(1)
    assert b.states(n.c.snapshot())["embertide/p1/checkin"] == "none"      # answered: not asked again


async def test_ha_phase_temp_clamps_to_household_limits(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:40")
    n.p1.settings.units = "C"
    b = HaBridge(n.c, "mqtt://x")
    await b.handle("embertide/p1/set/phase_temp/deep", "42")
    deep = next(ph for rh in n.p1.rhythms if rh.id == "weeknights" for ph in rh.phases if ph.id == "deep")
    assert deep.temp_c == n.hh.user_max_c


async def test_half_degree_fahrenheit_step_on_climate_and_states(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.p1.settings.units = "F"
    n.p1.settings.step_f = 0.5
    n.present("left", True)
    await n.until("2026-11-06T00:30")
    b = HaBridge(n.c, "mqtt://x")
    clim = next(c for t, c in b.discovery() if t.endswith("climate/embertide_left/config"))
    deep_num = next(c for t, c in b.discovery() if t.endswith("number/embertide_left_deep_temp/config"))
    assert clim["temp_step"] == 0.5 and clim["precision"] == 0.5
    assert deep_num["step"] == 0.5
    assert await b.handle("embertide/p1/set/target", "70.5") == "target 70.5"
    assert await b.handle("embertide/p1/set/phase_temp/deep", "63.5") == "Deep = 63.5"
    await n.step(1)
    st = b.states(n.c.snapshot())
    assert st["embertide/p1/target"] == "70.5"
    assert st["embertide/p1/phase_temp/deep"] == "63.5"
