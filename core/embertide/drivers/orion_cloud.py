"""Orion topper through Orion's cloud API with a personal API key (app.orionsleep.com/api-keys).

The easy path for most people: no DNS changes. Needs internet. Routes and shapes follow the community-documented
contract (github.com/borski/orion-sleep-api). The API rejects requests without a User-Agent.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .base import BedFrame, Cap, CommandError, SensorFrame, ZoneFrame

log = logging.getLogger(__name__)
API = "https://api1.orionbed.com"
UA = "okhttp/4.12.0 embertide"


class OrionCloud:
    id = "orion-cloud"
    name = "Orion topper (cloud API key)"
    capabilities = {Cap.ZONE_TEMP, Cap.ZONE_POWER, Cap.MEASURED_TEMP, Cap.OCCUPANCY, Cap.BIO_HR, Cap.BIO_BR, Cap.LED_DIM, Cap.FAN_QUIET}
    zones = ["left", "right"]

    def __init__(self, api_key: str, serial: str | None = None, zone_map: dict[str, str] | None = None, poll_s: float = 10) -> None:
        self.serial = serial
        self.zone_map = zone_map or {"left": "zone_a", "right": "zone_b"}
        self.poll_s = poll_s
        self._http = httpx.AsyncClient(base_url=API, timeout=20, headers={"Authorization": f"Bearer {api_key}", "User-Agent": UA})
        self._frame: BedFrame | None = None
        self._task: asyncio.Task | None = None
        self._state = "starting"

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._http.aclose()

    def latest(self) -> BedFrame | None:
        return self._frame

    def status(self) -> dict:
        f = self._frame
        return {"driver": self.id, "name": self.name, "online": bool(f and time.time() - f.ts < 3 * self.poll_s + 10),
                "detail": self._state, "serial": self.serial, "age_s": round(time.time() - f.ts, 1) if f else None}

    async def _run(self) -> None:
        while True:
            try:
                if not self.serial:
                    r = await self._http.get("/v1/devices")
                    r.raise_for_status()
                    devices = r.json().get("response", {}).get("devices", [])
                    if not devices:
                        raise CommandError("no devices on this account")
                    self.serial = devices[0]["serial_number"]
                r = await self._http.get(f"/v1/devices/{self.serial}/live")
                r.raise_for_status()
                self._frame = self._parse(r.json().get("response", {}))
                self._state = "connected"
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._state = f"error ({type(e).__name__})"
                log.warning("orion cloud: %s", e)
            await asyncio.sleep(self.poll_s)

    def _parse(self, live: dict) -> BedFrame:
        inv = {v: k for k, v in self.zone_map.items()}
        zones = {"left": ZoneFrame(), "right": ZoneFrame()}
        for z in live.get("zones", []) or []:
            side = inv.get(z.get("id"))
            if side:
                zones[side].on, zones[side].setpoint_c = z.get("on"), z.get("temp")
        st = live.get("status", {}) or {}
        for z in st.get("zones", []) or []:
            side = inv.get(z.get("id"))
            if side:
                zones[side].measured_c = z.get("temp")
        sensors = st.get("sensors", {}) or {}
        for side, key in (("left", "sensor1"), ("right", "sensor2")):
            s = sensors.get(key) or {}
            hr, br = s.get("heart_rate"), s.get("breath_rate")
            zones[side].sensor = SensorFrame(status=s.get("status") if isinstance(s.get("status"), int) else None,
                                             hr=hr if isinstance(hr, int) and 0 < hr < 255 else None,
                                             br=br if isinstance(br, int) and 0 < br < 255 else None,
                                             clock=(time.time(),))
        return BedFrame(ts=time.time(), zones=zones, firmware=(live.get("firmware") or {}).get("version") if isinstance(live.get("firmware"), dict) else None,
                        quiet_mode=live.get("quiet_mode"), brightness=live.get("led_brightness"))

    async def set_zone(self, zone: str, *, on: bool, temp_c: float | None = None) -> bool:
        if not self.serial:
            raise CommandError("bed not found yet")
        body: dict = {"id": self.zone_map[zone], "on": on}
        if on and temp_c is not None:
            body["temp"] = round(min(45.0, max(10.0, temp_c)), 2)
        r = await self._http.put(f"/v1/devices/{self.serial}/live", json={"zones": [body]})
        r.raise_for_status()
        self._frame = self._parse(r.json().get("response", {}))
        return True

    async def command(self, raw: str) -> str:
        raise CommandError("the cloud driver has no raw console; use the local MQTT driver")
