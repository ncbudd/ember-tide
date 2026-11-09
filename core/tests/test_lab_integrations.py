"""Integration resilience lab: Home Assistant entities, MQTT topics, the bed's MQTT, the HA bridge, calendar feeds and
context devices misbehaving. Errors in plain words, bounded retries, recovery, and devices that act up (flooding,
jumping, frozen, flapping) set aside until they settle, so the night carries on with what's left."""

import asyncio
import json
import socket
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from embertide.clock import FixedClock
from embertide.engine.context import person_context
from embertide.sources import SourceConfig, SourceManager
from embertide.store import Store

TZ = ZoneInfo("America/New_York")


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


def mgr(tmp_path, *cfgs, at="2026-11-05T22:00"):
    clock = FixedClock(ts(at), "America/New_York")
    store = Store(tmp_path / "s.db")
    m = SourceManager(store, clock, lambda: None)
    m.configs = list(cfgs)
    return m, clock, store


def ha(role="room_temp", kind="room", **kw):
    params = {"url": "http://ha.local:8123", "entity_id": "sensor.bedroom", "token": "t", "interval_s": 30} | kw.pop("params", {})
    return SourceConfig(id=kw.pop("id", "ha1"), name=kw.pop("name", "Bedroom"), driver="ha-entity", role=role, kind=kind, params=params, **kw)


def mq(role="presence", kind="radar", **kw):
    params = {"url": "mqtt://127.0.0.1:1", "topic": "x/y"} | kw.pop("params", {})
    return SourceConfig(id=kw.pop("id", "mq1"), name="Radar", driver="mqtt-topic", role=role, kind=kind, zone=kw.pop("zone", "left"), params=params, **kw)


def resp(state, **attrs):
    return httpx.Response(200, json={"entity_id": "sensor.bedroom", "state": state, "attributes": attrs})


async def poll(m, c, response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response)) as http:
        await m.poll_ha(c, http)


class FakeSleep:
    def __init__(self):
        self.calls = []

    async def __call__(self, s):
        self.calls.append(s)
        await asyncio.sleep(0)


async def run_until(task_factory, cond, steps=4000):
    t = asyncio.create_task(task_factory())
    try:
        for _ in range(steps):
            if cond():
                return True
            await asyncio.sleep(0)
        return cond()
    finally:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


# ═════════════════ Home Assistant entities ═════════════════
class TestHaEntity:
    async def test_good_reading(self, tmp_path):
        m, _, _ = mgr(tmp_path, c := ha())
        await poll(m, c, resp("21.4", unit_of_measurement="°C"))
        assert m.latest["ha1"]["value"] == 21.4 and m.health_of(c)["status"] == "ok"

    async def test_unit_switch_fahrenheit_mid_stream(self, tmp_path):
        m, clock, _ = mgr(tmp_path, c := ha())
        await poll(m, c, resp("21.0", unit_of_measurement="°C"))
        clock.advance(60)
        await poll(m, c, resp("70.0", unit_of_measurement="°F"))          # HA user switched units: 70 °F = 21.1 °C
        assert m.latest["ha1"]["value"] == pytest.approx(21.11, abs=0.01)
        assert m.health_of(c)["status"] == "ok" and m.health["ha1"]["dropped"] == 0   # not mistaken for a spike

    @pytest.mark.parametrize("state", ["unavailable", "unknown", "None", "", "null"])
    async def test_unavailable_is_stale_not_zero(self, tmp_path, state):
        m, _, store = mgr(tmp_path, c := ha())
        await poll(m, c, resp(state))
        h = m.health_of(c)
        assert m.latest["ha1"]["value"] is None and h["status"] == "stale" and "Home Assistant says" in h["why"]
        assert store.samples("ha1", 0, 1e12) == []

    @pytest.mark.parametrize("state", ["nan", "NaN", "inf", "-inf", "warm-ish", "12,5", "1e999"])
    async def test_junk_values_never_become_readings(self, tmp_path, state):
        m, _, store = mgr(tmp_path, c := ha())
        await poll(m, c, resp(state))
        assert m.latest["ha1"]["value"] is None and store.samples("ha1", 0, 1e12) == []

    async def test_missing_attribute(self, tmp_path):
        m, _, _ = mgr(tmp_path, c := ha(params={"attribute": "current_temperature"}))
        await poll(m, c, resp("heat"))
        assert m.latest["ha1"]["value"] is None and m.health_of(c)["status"] == "stale"

    async def test_impossible_value_dropped(self, tmp_path):
        m, _, store = mgr(tmp_path, c := ha())
        await poll(m, c, resp("9999"))
        assert m.latest.get("ha1", {}).get("value") is None and store.samples("ha1", 0, 1e12) == []
        assert m.health["ha1"]["dropped"] == 1

    async def test_non_json_reply(self, tmp_path):
        m, _, _ = mgr(tmp_path, c := ha())
        with pytest.raises(ValueError):
            await poll(m, c, httpx.Response(200, text="<html>proxy error</html>"))

    @pytest.mark.parametrize("status,words", [(401, "refused the token"), (403, "refused the token"),
                                              (404, "renamed or removed"), (500, "had a problem (500)"), (502, "had a problem (502)")])
    async def test_http_errors_in_plain_words(self, tmp_path, status, words):
        m, _, _ = mgr(tmp_path, c := ha())
        m.http_transport = httpx.MockTransport(lambda req: httpx.Response(status))
        m._sleep = FakeSleep()
        assert await run_until(lambda: m._guard(c, m._run_ha), lambda: m.latest.get("ha1", {}).get("error"))
        assert words in m.latest["ha1"]["error"]
        assert m.health_of(c)["status"] == "error"

    async def test_timeouts_and_refused(self, tmp_path):
        for exc, words in ((httpx.ReadTimeout("slow"), "didn't answer in time"), (httpx.ConnectError("refused"), "Can't reach Home Assistant")):
            m, _, _ = mgr(tmp_path / type(exc).__name__, c := ha())
            (tmp_path / type(exc).__name__).mkdir(exist_ok=True)

            def boom(req, exc=exc):
                raise exc
            m.http_transport = httpx.MockTransport(boom)
            m._sleep = FakeSleep()
            assert await run_until(lambda: m._guard(c, m._run_ha), lambda: m.latest.get("ha1", {}).get("error"))
            assert words in m.latest["ha1"]["error"]

    async def test_ha_restart_backoff_is_bounded_and_recovers(self, tmp_path):
        m, _, _ = mgr(tmp_path, c := ha())
        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            if calls["n"] <= 9:
                raise httpx.ConnectError("connection refused")              # HA restarting
            return resp("20.5")
        m.http_transport = httpx.MockTransport(handler)
        m._sleep = sl = FakeSleep()
        assert await run_until(lambda: m._guard(c, m._run_ha), lambda: m.latest.get("ha1", {}).get("value") == 20.5)
        retries = sl.calls[:9]
        assert retries == [2, 4, 8, 16, 32, 64, 120, 120, 120]                # doubles, never past 2 minutes
        assert m.latest["ha1"]["error"] is None and m.health_of(c)["status"] == "ok"

    async def test_slow_but_working(self, tmp_path):
        m, _, _ = mgr(tmp_path, c := ha())

        async def slow(req):
            await asyncio.sleep(0.05)
            return resp("19.0")
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
            await m.poll_ha(c, http)
        assert m.latest["ha1"]["value"] == 19.0

    async def test_polled_device_goes_offline_and_stops_voting(self, tmp_path):
        m, clock, _ = mgr(tmp_path, c := ha(role="presence", kind="radar", zone="left", params={"interval_s": 30}))
        await poll(m, c, resp("on"))
        clock.advance(15)
        await poll(m, c, resp("on"))
        assert m.vote_value(c) == 1.0
        clock.advance(200)                                              # missed > 3 polls: HA or the device is gone
        assert m.health_of(c)["status"] == "offline" and m.vote_value(c) is None
        assert m.zone_signals("left") == {}

    async def test_room_sensor_stale_after_max_age(self, tmp_path):
        m, clock, _ = mgr(tmp_path, c := ha())
        await poll(m, c, resp("21"))
        clock.advance(1801)
        assert m.health_of(c)["status"] == "offline" and "nothing for" in m.health_of(c)["why"]

    async def test_describe_shows_health_and_hides_token(self, tmp_path):
        m, _, _ = mgr(tmp_path, c := ha())
        await poll(m, c, resp("unavailable"))
        d = m.describe(c)
        assert d["health"]["status"] == "stale" and d["params"]["token"] == "•••"


# ═════════════════ MQTT topics ═════════════════
class TestMqttTopic:
    def test_malformed_json_and_wrong_path(self, tmp_path):
        m, _, _ = mgr(tmp_path, c := mq(role="room_temp", kind="room", zone=None, params={"json_path": "temperature"}))
        m.on_mqtt(c, b"{not json")
        assert m.latest["mq1"]["value"] is None and "couldn't find" in m.latest["mq1"]["note"]
        m.on_mqtt(c, b'{"temp": 20}')
        assert m.latest["mq1"]["value"] is None
        m.on_mqtt(c, b'{"temperature": 20.5}')
        assert m.latest["mq1"]["value"] == 20.5

    def test_binary_and_huge_payloads(self, tmp_path):
        m, _, _ = mgr(tmp_path, c := mq(role="room_temp", kind="room", zone=None))
        m.on_mqtt(c, bytes(range(256)))
        assert m.latest["mq1"]["value"] is None
        m.on_mqtt(c, b"9" * 100000)
        assert m.latest["mq1"]["value"] is None and "too large" in m.latest["mq1"]["note"]

    def test_deeply_nested_json_doesnt_crash(self, tmp_path):
        m, _, _ = mgr(tmp_path, c := mq(role="room_temp", kind="room", zone=None, params={"json_path": "a"}))
        m.on_mqtt(c, b"[" * 5000 + b"]" * 5000)
        assert m.latest["mq1"]["value"] is None

    def test_retained_presence_is_not_trusted_but_retained_room_is(self, tmp_path):
        r, rm = mq(), mq(id="mq2", role="room_temp", kind="room", zone=None)
        m, _, _ = mgr(tmp_path, r, rm)
        m.on_mqtt(r, b"on", retained=True)                              # "occupied" from who knows when
        assert m.vote_value(r) is None
        m.on_mqtt(rm, b"21.5", retained=True)
        assert m.latest["mq2"]["value"] == 21.5
        m.on_mqtt(r, b"on")
        m.clock.advance(15)
        m.on_mqtt(r, b"on")
        assert m.vote_value(r) == 1.0

    def test_flood_is_absorbed(self, tmp_path):
        m, clock, store = mgr(tmp_path, c := mq(role="room_temp", kind="room", zone=None))
        for i in range(3000):                                           # a misconfigured sensor publishing 50x a second
            m.on_mqtt(c, f"{20 + (i % 3) * 0.01:.2f}".encode())
            if i % 50 == 0:
                clock.advance(1)
        assert len(store.samples("mq1", 0, 1e12)) <= 5                  # stored at most every 15 s
        assert m.health_of(c)["status"] == "ok"

    async def test_broker_down_then_up_then_drop_and_back(self, tmp_path):
        amqtt = pytest.importorskip("amqtt.broker")
        import aiomqtt
        port = _free_port()
        c = mq(role="room_temp", kind="room", zone=None, params={"url": f"mqtt://127.0.0.1:{port}", "topic": "home/bedroom/t"})
        m, _, _ = mgr(tmp_path, c)
        m.backoff = (0.2, 0.4)
        task = asyncio.create_task(m._guard(c, m._run_mqtt))
        try:
            await _wait(lambda: m.latest.get("mq1", {}).get("error"), 5)
            assert "Can't reach the MQTT broker" in m.latest["mq1"]["error"]
            broker = await _broker(amqtt, port)
            await _wait(lambda: m.latest.get("mq1", {}).get("error") is None, 6)
            async with aiomqtt.Client("127.0.0.1", port) as pub:
                await pub.publish("home/bedroom/t", b"21.7")
            await _wait(lambda: m.latest.get("mq1", {}).get("value") == 21.7, 5)
            await broker.shutdown()                                     # broker restarts
            await _wait(lambda: m.latest.get("mq1", {}).get("error"), 6)
            assert m.latest["mq1"]["value"] == 21.7                     # last good reading kept meanwhile
            broker = await _broker(amqtt, port)
            await _wait(lambda: m.latest.get("mq1", {}).get("error") is None, 8)
            async with aiomqtt.Client("127.0.0.1", port) as pub:
                await pub.publish("home/bedroom/t", b"21.9")
            await _wait(lambda: m.latest.get("mq1", {}).get("value") == 21.9, 5)
            await broker.shutdown()
        finally:
            task.cancel()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def _broker(amqtt, port):
    b = amqtt.Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
                      "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
    await b.start()
    return b


async def _wait(cond, seconds):
    for _ in range(int(seconds * 20)):
        if cond():
            return
        await asyncio.sleep(0.05)
    assert cond(), "timed out waiting"


# ═════════════════ device health: erratic devices are set aside ═════════════════
class TestHealth:
    def test_one_spike_is_dropped_three_set_it_aside(self, tmp_path):
        m, clock, store = mgr(tmp_path, c := mq(role="room_temp", kind="room", zone=None))
        for v in (21.0, 21.1, 31.5, 21.2):                              # one 10-degree jump in a minute
            m._sample(c, v, clock.now_ts())
            clock.advance(60)
        assert m.health_of(c)["status"] == "ok" and "1 odd reading ignored" in m.health_of(c)["why"]
        assert 31.5 not in [v for _, v in store.samples("mq1", 0, 1e12)]
        for v in (35.0, 21.3, 5.0, 21.2, 38.0):                         # keeps jumping
            m._sample(c, v, clock.now_ts())
            clock.advance(60)
        h = m.health_of(c)
        assert h["status"] == "erratic" and "jumped" in h["why"] and m.quarantined(c)
        before = len(store.samples("mq1", 0, 1e12))
        m._sample(c, 21.4, clock.now_ts())
        assert len(store.samples("mq1", 0, 1e12)) == before             # set aside: nothing reaches Autopilot
        ev = [e for e in store.events(limit=50) if e["kind"] == "device_health"]
        assert ev and ev[0]["status"] == "erratic" and ev[0]["device"] == "mq1"

    def test_recovers_after_behaving_for_30_min(self, tmp_path):
        m, clock, store = mgr(tmp_path, c := mq(role="room_temp", kind="room", zone=None))
        for v in (21.0, 40.0, 21.0, 2.0, 21.0, 45.0):
            m._sample(c, v, clock.now_ts())
            clock.advance(30)
        assert m.quarantined(c)
        for _ in range(35):
            clock.advance(60)
            m._sample(c, 21.0, clock.now_ts())
        assert not m.quarantined(c) and m.health_of(c)["status"] == "ok"
        assert [e["status"] for e in store.events(limit=50) if e["kind"] == "device_health"][:2] == ["ok", "erratic"]

    def test_impossible_weight(self, tmp_path):
        m, clock, _ = mgr(tmp_path, c := mq(role="weight", kind="scale"))
        for v in (0.0, 300.0, 400.0, 999.0, 1200.0):
            m._sample(c, v, clock.now_ts())
            clock.advance(10)
        assert m.quarantined(c) and m.vote_value(c) is None

    def test_flapping_presence_is_set_aside(self, tmp_path):
        m, clock, _ = mgr(tmp_path, c := mq())                          # radar, hold 45 s
        for i in range(30):                                             # on 20 s, off 50 s, on, off... for 35 min
            m._sample(c, 1.0, clock.now_ts())
            clock.advance(20)
            m._sample(c, 0.0, clock.now_ts())
            clock.advance(50)
        assert m.quarantined(c) and "changed" in m.health_of(c)["why"]
        assert m.zone_signals("left") == {}

    def test_radar_losing_a_still_sleeper_is_healthy(self, tmp_path):
        """A real radar drops a perfectly still sleeper for a few seconds now and then: that's what hold_s is for."""
        import random
        rnd = random.Random(1)
        m, clock, _ = mgr(tmp_path, c := mq())
        for _ in range(3 * 3600 // 2):                                  # 3 hours of readings every 2 s
            m._sample(c, 0.0 if rnd.random() < 0.08 else 1.0, clock.now_ts())
            clock.advance(2)
        assert not m.quarantined(c) and m.vote_value(c) == 1.0

    def test_frozen_skin_temperature(self, tmp_path):
        m, clock, store = mgr(tmp_path, c := mq(id="sk", role="skin_temp", kind="wearable", zone="left", params={}))
        for _ in range(100):                                            # the same 34.12 for 100 minutes
            m._sample(c, 34.12, clock.now_ts())
            clock.advance(60)
        assert m.quarantined(c) and "same reading" in m.health_of(c)["why"]
        m._sample(c, 34.31, clock.now_ts())
        clock.advance(60)
        m._sample(c, 34.35, clock.now_ts())
        assert not m.quarantined(c)                                     # moving again: back in use

    def test_stuck_scale_stops_voting_occupied(self, tmp_path):
        m, clock, _ = mgr(tmp_path, c := mq(id="kg", role="weight", kind="scale"))
        for _ in range(130):                                            # exactly 78.0 kg for over two hours
            m._sample(c, 78.0, clock.now_ts())
            clock.advance(60)
        assert m.quarantined(c) and m.vote_value(c) is None

    def test_empty_scale_reading_zero_is_fine(self, tmp_path):
        m, clock, _ = mgr(tmp_path, c := mq(id="kg", role="weight", kind="scale"))
        for _ in range(200):
            m._sample(c, 1.8, clock.now_ts())                           # an empty bed reads the same all day: normal
            clock.advance(60)
        assert not m.quarantined(c)

    def test_trust_it_anyway(self, tmp_path):
        m, clock, _ = mgr(tmp_path, c := mq(role="room_temp", kind="room", zone=None, tuning={"ignore_health": True}))
        for v in (21.0, 40.0, 21.0, 2.0, 21.0, 45.0):
            m._sample(c, v, clock.now_ts())
            clock.advance(30)
        h = m.health_of(c)
        assert h["status"] == "erratic" and h["trusted"] and "trusted anyway" in h["why"] and not m.quarantined(c)

    def test_every_type_offers_trust_anyway(self):
        from embertide.sources import DEVICE_TYPES
        assert all(any(f["key"] == "ignore_health" for f in t["tuning"]) for t in DEVICE_TYPES)


# ═════════════════ dropouts during a real night ═════════════════
from test_context import CtxNight  # noqa: E402


class DropNight(CtxNight):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.stuck: dict[str, float] = {}

    async def step(self, n=1, dt=30):
        for _ in range(n):
            for sid, v in self.stuck.items():
                self.mgr._sample(self.mgr.get(sid), v, self.clock.now_ts())
            await super().step(1, dt)


def sim(id_, role, zone=None, kind=None, **kw):
    return SourceConfig(id=id_, name=id_, driver="sim", role=role, kind=kind or "", zone=zone, person=kw.pop("person", None),
                        params={"kind": role, **kw.pop("params", {})}, **kw)


async def test_devices_drop_out_mid_night_and_the_night_carries_on(tmp_path):
    radar = sim("rad", "presence", "left", "radar", tuning={"max_age_s": 120})
    scale = sim("kg", "weight", "left", "scale", params={"kg": 78})
    room = sim("room", "room_temp", kind="room", tuning={"max_age_s": 600})
    watch = sim("hr", "wearable_hr", "left", "wearable", person="p1")
    n = DropNight(tmp_path, "2026-11-05T22:00", [radar, scale, room, watch])
    n.p1.settings.autopilot.learn_nights = 0
    n.present("left", True)
    await n.until("2026-11-06T01:55", dt=60)
    assert n.e(n.p1).s.started and n.side("left").on
    fz = n.c.fused["left"]
    assert {v["signal"].split(":")[1] for v in fz["votes"] if ":" in v["signal"]} == {"rad", "kg"}
    radar.enabled = room.enabled = watch.enabled = False                # 2 AM: radar, room sensor and watch vanish
    n.stuck["kg"] = 78.4                                                # and the scale freezes on one number
    scale.enabled = False
    await n.until("2026-11-06T05:30", dt=60)
    assert n.mgr.quarantined(scale)
    assert n.e(n.p1).s.state == "rhythm" and n.side("left").on          # the side never turned off
    votes = {v["signal"] for v in n.c.fused["left"]["votes"]}
    assert votes == {"bed"}                                             # renormalised onto what's left
    acted = [e for e in n.events("autopilot") if e["status"] in ("suggested", "applied")]
    assert acted == [], [(e["direction"], [r["signal"] for r in e["reasons"]]) for e in acted]


async def test_lone_motion_sensor_is_not_an_answer(tmp_path):
    pir = sim("pir", "presence", "left", "motion")
    n = DropNight(tmp_path, "2026-11-05T22:30", [pir])
    n.bed.scripted = False
    n.side("left").frozen = n.side("right").frozen = True              # the bed's own strip has gone silent
    n.present("left", True)
    await n.step(40)
    assert n.c.fused["left"]["present"] is None                         # unknown: the schedule fallback decides, not a PIR


# ═════════════════ the bed's MQTT ═════════════════
class FakeClient:
    def __init__(self):
        self.sent = []

    async def publish(self, topic, payload, qos=0, retain=False):
        self.sent.append(payload.decode())


async def test_bed_that_ignores_a_command_isnt_flooded(monkeypatch):
    import embertide.drivers.orion_mqtt as om
    d = om.OrionMqtt("mqtt://127.0.0.1:1", serial="AABBCCDDEEFF")
    d._client = FakeClient()

    async def never(pred, timeout):
        return False
    d._wait_for = never
    t = {"now": 1000.0}
    monkeypatch.setattr(om.time, "monotonic", lambda: t["now"])
    assert await d.set_zone("left", on=True, temp_c=18.0) is False
    assert await d.set_zone("left", on=True, temp_c=18.0) is False      # same command, too soon: not sent again
    assert len(d._client.sent) == 1
    assert await d.set_zone("left", on=True, temp_c=19.0) is False      # a new temperature goes straight out
    assert len(d._client.sent) == 2
    t["now"] += 16
    await d.set_zone("left", on=True, temp_c=19.0)
    t["now"] += 16                                                      # second failure: now waits 30 s
    await d.set_zone("left", on=True, temp_c=19.0)
    assert len(d._client.sent) == 3
    t["now"] += 31
    await d.set_zone("left", on=True, temp_c=19.0)
    assert len(d._client.sent) == 4
    for _ in range(20):                                                 # an hour of a dead bed: waits cap at 5 min
        t["now"] += 301
        await d.set_zone("left", on=True, temp_c=19.0)
    assert len(d._client.sent) == 24


def test_garbage_frames_are_ignored():
    from embertide.drivers.orion_mqtt import parse_status
    for junk in (b"", b"\x00\xff\xfe", b"{", b'{"type":"status","sensors":"lol"}', b"[" * 3000, json.dumps({"type": "status", "temps": {"ib_left": "x"}}).encode()):
        r = parse_status("orion/AABBCCDDEEFF/out", junk, 0)
        assert r is None or r[1] is not None                            # never raises


def test_frozen_sensor_clock_means_unknown():
    from embertide.drivers.base import SensorFrame
    from embertide.engine.presence import PresenceFilter
    pf = PresenceFilter()
    for t in range(0, 60, 2):                                           # status says "in bed" but the clock never moves
        pf.update(float(t), {"left": SensorFrame(status=0, hr=60, br=14, clock=(1, 2, 3, 4))})
    assert pf.present("left") is None


async def test_bed_broker_restart_reconnects():
    amqtt = pytest.importorskip("amqtt.broker")
    import aiomqtt
    import embertide.drivers.orion_mqtt as om
    real = json.loads((Path(__file__).parent / "fixtures" / "orion_fw262_frames.json").read_text(encoding="utf-8"))
    port = _free_port()
    broker = await _broker(amqtt, port)
    d = om.OrionMqtt(f"mqtt://127.0.0.1:{port}", serial="AABBCCDDEEFF")
    await d.start()
    try:
        await _wait(lambda: d._state == "connected", 5)
        async with aiomqtt.Client("127.0.0.1", port) as bed:
            await bed.publish("orion/AABBCCDDEEFF/out", real["valid"].encode())
        await _wait(lambda: d._frame is not None, 5)
        await broker.shutdown()
        await _wait(lambda: d._state.startswith("reconnecting"), 5)
        for _ in range(3):                                              # a reconnect storm: broker flaps
            broker = await _broker(amqtt, port)
            await asyncio.sleep(0.3)
            await broker.shutdown()
        broker = await _broker(amqtt, port)
        await _wait(lambda: d._state == "connected", 12)
        d._frame = None
        async with aiomqtt.Client("127.0.0.1", port) as bed:
            await bed.publish("orion/AABBCCDDEEFF/out", real["garbage"].encode())
            await bed.publish("orion/AABBCCDDEEFF/out", real["valid"].encode())
        await _wait(lambda: d._frame is not None, 5)
    finally:
        await d.stop()
        await broker.shutdown()


# ═════════════════ the Home Assistant bridge ═════════════════
async def test_bridge_junk_commands(tmp_path):
    from embertide.ha_bridge import HaBridge
    from test_scenarios import Night
    n = Night(tmp_path, "2026-11-05T22:20")
    b = HaBridge(n.c, "mqtt://x")
    before = n.e(n.p1).s.target_c
    for topic, payload in (("embertide/p1/set/target", "abc"), ("embertide/p1/set/target", "nan"), ("embertide/p1/set/target", "inf"),
                           ("embertide/p1/set/target", "999"), ("embertide/p1/set/target", ""), ("embertide/p1/set/target", "7" * 500),
                           ("embertide/p1/set/mode", "explode"), ("embertide/p1/set/flash", "lukewarm"), ("embertide/nobody/set/flash", "hot"),
                           ("embertide/p1/set/phase_temp/nope", "65"), ("embertide/p1/get/target", "65"), ("other/p1/set/target", "65")):
        assert await b.handle(topic, payload) is None, (topic, payload)
    assert n.e(n.p1).s.target_c == before and n.e(n.p1).s.flash is None


async def test_bridge_waits_for_broker_and_ignores_replayed_commands(tmp_path):
    amqtt = pytest.importorskip("amqtt.broker")
    import aiomqtt
    from embertide.ha_bridge import HaBridge
    from test_scenarios import Night
    port = _free_port()
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T00:30")
    b = HaBridge(n.c, f"mqtt://127.0.0.1:{port}")
    await b.start()
    try:
        await _wait(lambda: "can't reach the broker" in b.state, 5)    # broker not there yet: plain state, keeps trying
        broker = await _broker(amqtt, port)
        async with aiomqtt.Client("127.0.0.1", port) as old:            # an old retained button press sits on the broker
            await old.publish("embertide/p1/set/flash", b"hot", retain=True)
        await _wait(lambda: b.state == "connected", 10)
        await asyncio.sleep(0.5)
        assert b.ignored_retained >= 1 and n.e(n.p1).s.flash is None     # not replayed
        async with aiomqtt.Client("127.0.0.1", port) as ha:
            await ha.publish("embertide/p1/set/flash", b"hot")            # a fresh press works
        await _wait(lambda: n.e(n.p1).s.flash is not None, 5)
        await b.stop()
        await broker.shutdown()
    finally:
        if b._task:
            b._task.cancel()


async def test_bridge_auth_failure_says_so(tmp_path, monkeypatch):
    import aiomqtt
    from embertide.ha_bridge import HaBridge
    from test_scenarios import Night
    n = Night(tmp_path, "2026-11-05T22:20")

    class Refuse:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise aiomqtt.MqttError("Connection refused: Not authorized")

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(aiomqtt, "Client", Refuse)
    b = HaBridge(n.c, "mqtt://u:p@127.0.0.1:1883")
    await b.start()
    try:
        await _wait(lambda: "not authorised" in b.state, 3)
    finally:
        b._task.cancel()


# ═════════════════ context devices acting up ═════════════════
def _loc(**kw):
    return SourceConfig(id="gps", name="Phone location", driver="mqtt-topic", role="location", kind="location", person="p1",
                        params={"url": "mqtt://x", "topic": "t"}, **kw)


def _phone(**kw):
    return SourceConfig(id="ph", name="Phone", driver="mqtt-topic", role="home", kind="phone", person="p1",
                        params={"url": "mqtt://x", "topic": "t"}, **kw)


def test_teleporting_gps_is_set_aside(tmp_path):
    from embertide.seed import default_household
    p1 = default_household().people[0]
    m, clock, _ = mgr(tmp_path, g := _loc())
    for i in range(12):                                                 # 0 km ↔ 400 km every minute
        m._sample(g, 0.0 if i % 2 == 0 else 400.0, clock.now_ts())
        clock.advance(60)
    assert m.quarantined(g)
    ctx = person_context(m, m.store, p1, clock.now_ts())
    assert ctx.home is None and ctx.distance_km is None                # ignored, not "away" and not "home"


def test_flapping_phone_is_set_aside(tmp_path):
    from embertide.seed import default_household
    p1 = default_household().people[0]
    m, clock, _ = mgr(tmp_path, ph := _phone())
    for i in range(20):                                                 # home / away every minute
        m._sample(ph, float(i % 2), clock.now_ts())
        clock.advance(60)
    assert m.quarantined(ph) and "changed" in m.health_of(ph)["why"]
    assert person_context(m, m.store, p1, clock.now_ts()).home is None


def test_phone_coming_and_going_normally_is_fine(tmp_path):
    m, clock, _ = mgr(tmp_path, ph := _phone())
    for v, minutes in ((1, 60), (0, 180), (1, 120), (0, 30), (1, 300)):
        for _ in range(minutes):
            m._sample(ph, float(v), clock.now_ts())
            clock.advance(60)
    assert not m.quarantined(ph)


ICS_OK = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTAMP:20261101T000000Z\r\n"
          "DTSTART;TZID=America/New_York:20261105T190000\r\nDTEND;TZID=America/New_York:20261105T233000\r\nSUMMARY:Show\r\n"
          "END:VEVENT\r\nEND:VCALENDAR\r\n")


class TestCalendarFeed:
    @pytest.mark.parametrize("response,words", [
        (httpx.Response(200, text="<!doctype html><html>Sign in</html>"), "web page, not a calendar feed"),
        (httpx.Response(500), "returned"),
        (httpx.Response(200, headers={"content-length": "50000000"}, text="BEGIN:VCALENDAR"), "too large (over 5 MB)"),
    ])
    async def test_bad_feeds(self, tmp_path, response, words):
        from embertide.sources import plain_error
        m, _, _ = mgr(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response)) as http:
            with pytest.raises(Exception) as ei:
                await m.fetch_ics(http, "https://cal.example/x.ics")
        text = plain_error(ei.value, "the calendar feed")
        assert words in text or "had a problem" in text, text

    async def test_endless_feed_is_cut_off(self, tmp_path):
        m, _, _ = mgr(tmp_path)

        async def endless():
            yield b"BEGIN:VCALENDAR\r\n"
            for _ in range(10000):
                yield b"X" * 1024

        class Endless(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                return httpx.Response(200, stream=_Stream(endless()))
        async with httpx.AsyncClient(transport=Endless()) as http:
            with pytest.raises(ValueError, match="larger than 5 MB"):
                await m.fetch_ics(http, "https://cal.example/x.ics", limit=1_000_000)

    async def test_keeps_last_good_result_when_the_feed_breaks(self, tmp_path):
        c = SourceConfig(id="cal", name="Calendar", driver="ics", role="calendar", kind="calendar", person="p1",
                         params={"url": "https://user.fm/cal.ics"})
        m, clock, _ = mgr(tmp_path, c, at="2026-11-05T18:00")
        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            return httpx.Response(200, text=ICS_OK) if calls["n"] == 1 else httpx.Response(500)
        m.http_transport = httpx.MockTransport(handler)
        m._sleep = FakeSleep()
        assert await run_until(lambda: m._guard(c, m._run_ics), lambda: m.latest.get("cal", {}).get("error"))
        assert m.latest["cal"]["value"] == ts("2026-11-05T23:30")       # the last good answer is still used
        assert "had a problem (500)" in m.latest["cal"]["error"] or "500" in m.latest["cal"]["error"]
        from embertide.seed import default_household
        ctx = person_context(m, m.store, default_household().people[0], clock.now_ts())
        assert ctx.event_end == ts("2026-11-05T23:30")


class _Stream(httpx.AsyncByteStream):
    def __init__(self, gen):
        self.gen = gen

    async def __aiter__(self):
        async for c in self.gen:
            yield c

    async def aclose(self):
        pass


async def test_controller_backs_off_a_command_the_bed_never_confirms(tmp_path, monkeypatch):
    """The bed is connected but never confirms: the same command goes out less and less often (not every 8 s)."""
    import embertide.engine.controller as ctlmod
    from test_scenarios import Night
    n = Night(tmp_path, "2026-11-05T21:40")
    ctlmod.MIN_CMD_GAP_S = 8.0
    fake = {"t": 1000.0}
    monkeypatch.setattr(ctlmod.time, "monotonic", lambda: fake["t"])
    sent = []

    async def never(zone, *, on, temp_c=None):
        sent.append((zone, on, temp_c))
        return False
    n.bed.set_zone = never
    for _ in range(120):                       # 20 minutes in 10 s steps
        await n.step(1, dt=10)
        fake["t"] += 10
    left = [x for x in sent if x[0] == "left"]
    assert 3 <= len(left) <= 10, len(left)    # 8, 16, 32, 64, 128, 256 s… instead of ~150 sends
    ctlmod.MIN_CMD_GAP_S = 0
