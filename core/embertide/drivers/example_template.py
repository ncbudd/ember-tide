"""A minimal, complete bed driver to copy from (see docs/drivers.md).

It pretends to be a two-zone topper that follows commands instantly and reports no sleep sensor. Load it with
`ET_BED=python:embertide.drivers.example_template:ExampleBed`. A real driver replaces the in-memory state below with
calls to the bed (MQTT, HTTP, Bluetooth, a vendor library) and keeps the same shape.
"""

from __future__ import annotations

import time

from .base import BedFrame, Cap, CommandError, ZoneFrame
from . import profiles


class ExampleBed:
    id = "example"
    name = "Example topper"
    # List only what the hardware really does; the app hides controls for the rest.
    capabilities = {Cap.ZONE_TEMP, Cap.ZONE_POWER, Cap.MEASURED_TEMP}
    profile = profiles.GENERIC
    zones = ["left", "right"]

    def __init__(self, cfg=None, clock=None) -> None:       # called as Class(cfg, clock) by ET_BED=python:...
        self._z = {z: {"on": False, "set": 24.0, "now": 24.0} for z in self.zones}

    async def start(self) -> None:       # open connections, start a reader task
        return None

    async def stop(self) -> None:        # close them
        return None

    def latest(self) -> BedFrame | None:
        """The newest reading; the engine calls this every few seconds. Never block here."""
        return BedFrame(ts=time.time(), online=True, zones={
            z: ZoneFrame(on=v["on"], setpoint_c=v["set"], measured_c=v["now"]) for z, v in self._z.items()})

    async def set_zone(self, zone: str, *, on: bool, temp_c: float | None = None) -> bool:
        if zone not in self._z:
            raise CommandError(f"unknown zone {zone}")
        v = self._z[zone]
        v["on"] = on
        if temp_c is not None:
            v["set"] = v["now"] = float(temp_c)
        return True                      # True = the bed confirmed it (or this driver can't tell)

    async def command(self, raw: str) -> str:
        raise CommandError("this bed has no console")

    def status(self) -> dict:
        return {"driver": self.id, "name": self.name, "online": True, "detail": "example driver"}
