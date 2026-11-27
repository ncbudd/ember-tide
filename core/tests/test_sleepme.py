"""Sleepme Dock Pro driver against a fake of Sleepme's developer API."""

import asyncio
import json

import httpx
import pytest

from embertide.drivers.base import CommandError
from embertide.drivers.sleepme import SleepmeBed, parse_devices

TOKEN = "synthetic-sleepme-token"


class FakeSleepme:
    def __init__(self, devices=("dev-a", "dev-b")):
        self.dev = {d: {"about": {"model": "DP999NA"},
                        "control": {"set_temperature_c": 21.0, "thermal_control_status": "standby"},
                        "status": {"is_connected": True, "is_water_low": False, "water_temperature_c": 24.5}} for d in devices}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.rate_limited = False
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, req: httpx.Request) -> httpx.Response:
        if req.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        if self.rate_limited:
            return httpx.Response(429, headers={"Retry-After": "30"})
        body = json.loads(req.content) if req.content else None
        path = req.url.path.removeprefix("/v1")
        self.calls.append((req.method, path, body))
        if req.method == "GET" and path == "/devices":
            return httpx.Response(200, json=[{"id": d, "name": d, "attachments": ["CHILIPAD_PRO"]} for d in self.dev]
                                  + [{"id": "tracker", "name": "Tracker", "attachments": []}])
        dev = path.removeprefix("/devices/")
        if dev not in self.dev:
            return httpx.Response(404)
        if req.method == "GET":
            return httpx.Response(200, json=self.dev[dev])
        if req.method == "PATCH":
            self.dev[dev]["control"].update(body)
            return httpx.Response(200, json=self.dev[dev]["control"])
        return httpx.Response(405)


def run(coro):
    return asyncio.run(coro)


def test_parse_devices():
    assert parse_devices(None) is None and parse_devices("  ") is None
    assert parse_devices("abc") == {"left": "abc"}
    assert parse_devices("right=b, left=a") == {"left": "a", "right": "b"}
    for bad in ("left=a,,right=b", "middle=a", "left=a,left=b", "left="):
        with pytest.raises(ValueError):
            parse_devices(bad)


def test_finds_docks_and_reads_them():
    fake = FakeSleepme()
    bed = SleepmeBed(TOKEN, transport=fake.transport)
    run(bed._poll_once())
    f = bed.latest()
    assert bed.zones == ["left", "right"] and bed.status()["devices"] == {"left": "dev-a", "right": "dev-b"}
    assert f.zones["left"].on is False and f.zones["left"].measured_c == 24.5 and f.zones["left"].setpoint_c == 21.0
    assert f.hub.water_ok is True and f.online


def test_one_patch_per_change_and_confirmed_from_the_answer():
    fake = FakeSleepme()
    bed = SleepmeBed(TOKEN, {"left": "dev-a", "right": "dev-b"}, transport=fake.transport)
    assert run(bed.set_zone("right", on=True, temp_c=19.26)) is True
    assert fake.calls == [("PATCH", "/devices/dev-b", {"thermal_control_status": "active", "set_temperature_c": 19.5})]
    assert bed.latest().zones["right"].on is True and bed.latest().zones["right"].setpoint_c == 19.5
    fake.calls.clear()
    assert run(bed.set_zone("right", on=False)) is True
    assert fake.calls == [("PATCH", "/devices/dev-b", {"thermal_control_status": "standby"})]


def test_setpoints_stay_inside_the_api_range():
    fake = FakeSleepme()
    bed = SleepmeBed(TOKEN, {"left": "dev-a"}, transport=fake.transport)
    run(bed.set_zone("left", on=True, temp_c=5))
    assert fake.calls[-1][2]["set_temperature_c"] == 13.0
    run(bed.set_zone("left", on=True, temp_c=60))
    assert fake.calls[-1][2]["set_temperature_c"] == 48.0


def test_max_cold_and_heat_markers_are_not_temperatures():
    fake = FakeSleepme(devices=("dev-a",))
    fake.dev["dev-a"]["control"]["set_temperature_c"] = -1
    bed = SleepmeBed(TOKEN, {"left": "dev-a"}, transport=fake.transport)
    run(bed._poll_once())
    assert bed.latest().zones["left"].setpoint_c is None


def test_offline_dock_and_low_water():
    fake = FakeSleepme()
    fake.dev["dev-a"]["status"]["is_connected"] = False
    fake.dev["dev-b"]["status"]["is_water_low"] = True
    bed = SleepmeBed(TOKEN, {"left": "dev-a", "right": "dev-b"}, transport=fake.transport)
    run(bed._poll_once())
    f = bed.latest()
    assert f.zones["left"].on is None and not f.online and f.hub.water_ok is False


def test_rate_limit_backs_off_and_bad_token_is_plain():
    fake = FakeSleepme()
    bed = SleepmeBed(TOKEN, {"left": "dev-a"}, transport=fake.transport)
    fake.rate_limited = True
    with pytest.raises(CommandError, match="slow down"):
        run(bed.set_zone("left", on=True, temp_c=20))
    fake.rate_limited = False
    n = len(fake.calls)
    with pytest.raises(CommandError, match="slow down"):         # still inside the back-off: no request is sent
        run(bed.set_zone("left", on=True, temp_c=20))
    assert len(fake.calls) == n
    bad = SleepmeBed("synthetic-wrong-token", {"left": "dev-a"}, transport=fake.transport)
    with pytest.raises(CommandError, match="token"):
        run(bad.set_zone("left", on=False))
    run(bad._poll_once())
    assert "token" in bad.status()["detail"] and TOKEN not in str(bad.status())


def test_polls_no_faster_than_the_rate_limit_allows():
    assert SleepmeBed(TOKEN, poll_s=5).poll_s == 30.0
