"""Eight Sleep Pod (free-sleep) driver against a fake of free-sleep's local API."""

import asyncio
import json

import httpx
import pytest

from embertide.drivers.base import CommandError
from embertide.drivers.free_sleep import FreeSleepBed


class FakePod:
    def __init__(self):
        side = {"currentTemperatureF": 80, "targetTemperatureF": 83, "isOn": False, "secondsRemaining": 0,
                "currentTemperatureLevel": 0, "isAlarmVibrating": False}
        self.state = {"left": dict(side), "right": dict(side), "waterLevel": "true", "isPriming": False}
        self.calls: list[tuple[str, dict | None]] = []
        self.reject = False
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, body))
        if req.url.path != "/api/deviceStatus":
            return httpx.Response(404)
        if req.method == "GET":
            return httpx.Response(200, json=self.state)
        if self.reject:
            return httpx.Response(400, json={"error": "bad"})
        for side, v in body.items():
            self.state[side].update(v)
        return httpx.Response(204)


def run(coro):
    return asyncio.run(coro)


def test_reads_both_sides_in_celsius():
    pod = FakePod()
    bed = FreeSleepBed("http://pod.local:3000", transport=pod.transport)
    run(bed._poll_once())
    f = bed.latest()
    assert f.online and f.zones["left"].on is False
    assert round(f.zones["left"].measured_c, 1) == 26.7 and round(f.zones["left"].setpoint_c, 1) == 28.3
    assert f.hub.water_ok is True


def test_set_sends_whole_fahrenheit_and_confirms_by_reading_back():
    pod = FakePod()
    bed = FreeSleepBed("http://pod.local:3000", transport=pod.transport)
    assert run(bed.set_zone("right", on=True, temp_c=20.0)) is True
    assert pod.calls[0] == ("POST", {"right": {"isOn": True, "targetTemperatureF": 68}})
    assert pod.state["right"]["isOn"] is True and pod.state["left"]["isOn"] is False
    assert run(bed.set_zone("right", on=False)) is True
    assert pod.state["right"]["isOn"] is False


def test_range_is_clamped_to_the_pods_limits():
    pod = FakePod()
    bed = FreeSleepBed("http://pod.local:3000", transport=pod.transport)
    run(bed.set_zone("left", on=True, temp_c=5))
    assert pod.state["left"]["targetTemperatureF"] == 55
    run(bed.set_zone("left", on=True, temp_c=60))
    assert pod.state["left"]["targetTemperatureF"] == 110


def test_refused_and_unreachable_are_plain_errors():
    pod = FakePod()
    pod.reject = True
    bed = FreeSleepBed("http://pod.local:3000", transport=pod.transport)
    with pytest.raises(CommandError, match="refused"):
        run(bed.set_zone("left", on=True, temp_c=20))

    def down(req):
        raise httpx.ConnectError("no route")
    off = FreeSleepBed("http://pod.local:3000", transport=httpx.MockTransport(down))
    with pytest.raises(CommandError, match="reach the Pod"):
        run(off.set_zone("left", on=False))
    run(off._poll_once())
    assert "reach the Pod" in off.status()["detail"] and off.latest() is None


def test_a_side_missing_from_the_answer_is_unknown():
    pod = FakePod()
    del pod.state["right"]
    bed = FreeSleepBed("http://pod.local:3000", transport=pod.transport)
    run(bed._poll_once())
    assert bed.latest().zones["right"].on is None and not bed.latest().online
