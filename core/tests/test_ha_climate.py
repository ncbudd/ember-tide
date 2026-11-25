"""Tests for the Home Assistant climate bed driver (BedJet and generic HA thermostats)."""

import asyncio
import json

import httpx
import pytest

from embertide.drivers.base import CommandError
from embertide.drivers.ha_climate import HaClimateBed, parse_entities

TOKEN = "test-ha-token-not-real"


def _state(state_val="off", **attrs):
    base = {"hvac_modes": ["off", "heat", "cool"], "min_temp": 16, "max_temp": 40, "current_temperature": 22.0, "temperature": 24.0}
    base.update(attrs)
    return {"entity_id": "climate.bed", "state": state_val, "attributes": base}


class FakeHA:
    def __init__(self, unit="°C", token=TOKEN, states=None):
        self.unit = unit
        self.token = token
        self.states = {}
        for eid, st in (states or {}).items():
            self.states[eid] = {**st, "entity_id": eid}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail_post_status: int | None = None
        self.ignore_mode = False          # a unit that never reports the new mode (out of Bluetooth range)
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401)
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        if request.method == "GET" and path == "/api/config":
            return httpx.Response(200, json={"unit_system": {"temperature": self.unit}})
        if request.method == "GET" and path.startswith("/api/states/"):
            eid = path.rsplit("/", 1)[1]
            if eid in self.states:
                return httpx.Response(200, json=self.states[eid])
            return httpx.Response(404)
        if request.method == "POST" and path.startswith("/api/services/climate/"):
            if self.fail_post_status is not None:
                return httpx.Response(self.fail_post_status)
            service = path.rsplit("/", 1)[1]
            st = self.states[body["entity_id"]]
            if service == "set_hvac_mode":
                if not self.ignore_mode:
                    st["state"] = body["hvac_mode"]
            elif service == "set_temperature":
                st["attributes"]["temperature"] = body["temperature"]
            elif service == "set_fan_mode":
                st["attributes"]["fan_mode"] = body["fan_mode"]
            return httpx.Response(200, json=[])
        return httpx.Response(404)


def test_parse_entities():
    assert parse_entities("climate.bed") == {"left": "climate.bed"}
    assert parse_entities(" right = climate.b , left = climate.a ") == {"left": "climate.a", "right": "climate.b"}
    for bad in ("center=climate.a", "sensor.temp", "left=switch.bed", ""):
        with pytest.raises(ValueError):
            parse_entities(bad)


def test_fahrenheit_conversion():
    ha = FakeHA(unit="°F", states={"climate.bed": _state("heat", current_temperature=80, temperature=75, min_temp=66, max_temp=104)})
    d = HaClimateBed("http://ha.test:8123/", TOKEN, {"left": "climate.bed"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            assert d.latest().zones["left"].measured_c == pytest.approx(26.7, abs=0.1)
            assert await d.set_zone("left", on=True, temp_c=35.0) is True
            temp_calls = [b for m, p, b in ha.calls if m == "POST" and p.endswith("/set_temperature")]
            assert temp_calls == [{"entity_id": "climate.bed", "temperature": 95}]
        finally:
            await d.stop()

    asyncio.run(run())


def test_celsius_passthrough():
    ha = FakeHA(unit="°C", states={"climate.bed": _state("heat", current_temperature=24.5, temperature=28.0)})
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.bed"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            zf = d.latest().zones["left"]
            assert zf.measured_c == 24.5 and zf.setpoint_c == 28.0
            assert await d.set_zone("left", on=True, temp_c=30.2) is True
            temp_calls = [b for m, p, b in ha.calls if m == "POST" and p.endswith("/set_temperature")]
            assert temp_calls == [{"entity_id": "climate.bed", "temperature": 30.0}]
        finally:
            await d.stop()

    asyncio.run(run())


def test_thermostat_heat_cool():
    ha = FakeHA(states={"climate.pad": _state("off", hvac_modes=["off", "heat_cool", "heat", "cool"], friendly_name="Mattress Pad")})
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.pad"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            ha.calls.clear()
            assert await d.set_zone("left", on=True, temp_c=22.0) is True
            posts = [(p.rsplit("/", 1)[1], b) for m, p, b in ha.calls if m == "POST"]
            assert posts == [
                ("set_hvac_mode", {"entity_id": "climate.pad", "hvac_mode": "heat_cool"}),
                ("set_temperature", {"entity_id": "climate.pad", "temperature": 22.0}),
            ]
        finally:
            await d.stop()

    asyncio.run(run())


def test_bedjet_warm_clamped():
    ha = FakeHA(states={"climate.bedjet_left": _state("off", min_temp=19, max_temp=40, fan_modes=["10%", "50%", "100%"])})
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.bedjet_left"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            ha.calls.clear()
            assert await d.set_zone("left", on=True, temp_c=44.0) is True
            posts = [(p.rsplit("/", 1)[1], b) for m, p, b in ha.calls if m == "POST"]
            assert posts == [
                ("set_hvac_mode", {"entity_id": "climate.bedjet_left", "hvac_mode": "heat"}),
                ("set_temperature", {"entity_id": "climate.bedjet_left", "temperature": 40.0}),
            ]
        finally:
            await d.stop()

    asyncio.run(run())


def test_bedjet_cool_fan_speed_and_no_temp():
    fans = ["5%", "25%", "50%", "75%", "100%"]
    ha = FakeHA(states={"climate.bedjet_left": _state("off", fan_modes=fans)})
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.bedjet_left"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            ha.calls.clear()
            assert await d.set_zone("left", on=True, temp_c=24.0) is True
            assert not any(p.endswith("/set_temperature") for _, p, _ in ha.calls)
            fan1 = next(b["fan_mode"] for _, p, b in ha.calls if p.endswith("/set_fan_mode"))

            ha.calls.clear()
            assert await d.set_zone("left", on=True, temp_c=16.0) is True
            assert not any(p.endswith("/set_temperature") for _, p, _ in ha.calls)
            fan2 = next(b["fan_mode"] for _, p, b in ha.calls if p.endswith("/set_fan_mode"))
            assert fans.index(fan2) > fans.index(fan1) and fan2 == "100%"
        finally:
            await d.stop()

    asyncio.run(run())


def test_bedjet_cool_reports_own_setpoint():
    ha = FakeHA(states={"climate.bedjet_left": _state("cool", temperature=30.0, fan_modes=["5%", "50%", "100%"])})
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.bedjet_left"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            await d.set_zone("left", on=True, temp_c=19.0)
            await d._poll_once()
            assert d.latest().zones["left"].setpoint_c == 19.0
        finally:
            await d.stop()

    asyncio.run(run())


def test_off():
    ha = FakeHA(states={"climate.bed": _state("heat", temperature=30.0)})
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.bed"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            assert await d.set_zone("left", on=False) is True
            assert any(p.endswith("/set_hvac_mode") and b["hvac_mode"] == "off" for _, p, b in ha.calls if b)
            assert d.latest().zones["left"].on is False
        finally:
            await d.stop()

    asyncio.run(run())


def test_unexpected_off_is_left_to_the_engine():
    """A BedJet ending heat by itself (its timer) or a remote press: the driver reports it and sends nothing on its own;
    the engine decides whether to turn it back on."""
    ha = FakeHA(states={"climate.bedjet_left": _state("off", min_temp=19, max_temp=40)})
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.bedjet_left"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            await d.set_zone("left", on=True, temp_c=32.0)
            ha.states["climate.bedjet_left"]["state"] = "off"
            ha.calls.clear()
            await d._poll_once()
            assert not [c for c in ha.calls if c[0] == "POST"]
            assert d.latest().zones["left"].on is False
        finally:
            await d.stop()

    asyncio.run(run())


def test_unconfirmed_change_returns_false():
    ha = FakeHA(states={"climate.bed": _state("off")})
    ha.ignore_mode = True
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.bed"}, confirm_s=0, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            assert await d.set_zone("left", on=True, temp_c=30.0) is False
        finally:
            await d.stop()

    asyncio.run(run())


def test_fan_only_when_there_is_no_cool_mode():
    ha = FakeHA(states={"climate.bedjet": _state("off", hvac_modes=["off", "heat", "fan_only"], fan_modes=["low", "high"])})
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.bedjet"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            assert await d.set_zone("left", on=True, temp_c=18.0) is True
            assert ha.states["climate.bedjet"]["state"] == "fan_only"
            assert ha.states["climate.bedjet"]["attributes"]["fan_mode"] == "high"
        finally:
            await d.stop()

    asyncio.run(run())


def test_unavailable_entity():
    ha = FakeHA(states={"climate.bed": _state("unavailable")})
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.bed"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            assert d.latest().online is False
            assert d.latest().zones["left"].on is None
        finally:
            await d.stop()

    asyncio.run(run())


def test_401_masks_token():
    secret = "secret-ha-token-123"
    ha = FakeHA(token="different-token", states={"climate.bed": _state("off")})
    d = HaClimateBed("http://ha.test:8123", secret, {"left": "climate.bed"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            st = d.status()
            assert "token" in st["detail"].lower()
            assert secret not in str(st)
        finally:
            await d.stop()

    asyncio.run(run())


def test_set_zone_http_500_raises_command_error():
    ha = FakeHA(states={"climate.bed": _state("off")})
    d = HaClimateBed("http://ha.test:8123", TOKEN, {"left": "climate.bed"}, transport=ha.transport)

    async def run():
        try:
            await d._poll_once()
            ha.fail_post_status = 500
            with pytest.raises(CommandError):
                await d.set_zone("left", on=True, temp_c=30.0)
        finally:
            await d.stop()

    asyncio.run(run())
