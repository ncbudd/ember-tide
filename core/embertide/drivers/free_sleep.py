"""Eight Sleep Pod running free-sleep (github.com/throwaway31265/free-sleep), over its local HTTP API.

free-sleep replaces the Pod's own software (Pod 3 without SD card, Pod 4, Pod 5; installing it means opening the Pod,
see its README) and serves a small API on the home network, with no cloud and no sign-in:

  GET  /api/deviceStatus   -> {left: {currentTemperatureF, targetTemperatureF, isOn, secondsRemaining}, right: {...},
                               waterLevel, isPriming, ...}
  POST /api/deviceStatus   <- a partial of the same shape, e.g. {"left": {"targetTemperatureF": 68, "isOn": true}} -> 204

Temperatures are whole °F, 55-110. Turning a side on gives it free-sleep's 12-hour run time, so a night never stops
early by itself. Anyone who can reach the Pod can control it: keep it on a trusted network.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

import httpx

from . import profiles
from .base import BedFrame, Cap, CommandError, HubHealth, ZoneFrame

log = logging.getLogger(__name__)

MIN_F, MAX_F = 55, 110


def _now() -> float:
    return time.time()


def _f_to_c(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return (float(v) - 32.0) * 5.0 / 9.0


def _c_to_f(temp_c: float) -> int:
    return int(min(MAX_F, max(MIN_F, round(float(temp_c) * 9.0 / 5.0 + 32.0))))


class FreeSleepBed:
    id = "free-sleep"
    name = "Eight Sleep Pod (free-sleep)"
    capabilities = {Cap.ZONE_TEMP, Cap.ZONE_POWER, Cap.MEASURED_TEMP, Cap.HUB_HEALTH}
    untried = {Cap.ZONE_TEMP, Cap.ZONE_POWER}
    zones = ["left", "right"]
    profile = profiles.WATER_POD

    def __init__(self, url: str, poll_s: float = 10.0, transport=None) -> None:
        if not url:
            raise ValueError("The Pod's address is needed, e.g. http://192.168.1.60:3000")
        self._url = url.rstrip("/")
        self.poll_s = max(3.0, float(poll_s))
        self._http = httpx.AsyncClient(base_url=self._url, timeout=10.0, transport=transport)
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
        now = _now()
        return {"driver": self.id, "name": self.name, "detail": self._state,
                "online": bool(f and f.online and now - f.ts < 3 * self.poll_s + 10),
                "age_s": round(now - f.ts, 1) if f else None}

    def _frame_from(self, d: dict) -> BedFrame:
        zones: dict[str, ZoneFrame] = {}
        for side in self.zones:
            z = d.get(side)
            if not isinstance(z, dict) or not isinstance(z.get("isOn"), bool):
                zones[side] = ZoneFrame(on=None)
                continue
            zones[side] = ZoneFrame(on=z["isOn"], setpoint_c=_f_to_c(z.get("targetTemperatureF")),
                                    measured_c=_f_to_c(z.get("currentTemperatureF")))
        water = str(d.get("waterLevel") or "").strip().lower()
        water_ok = True if water in ("true", "ok", "good", "full") else False if water in ("false", "low", "empty") else None
        hub = HubHealth(water_ok=water_ok, water_raw={})
        return BedFrame(ts=_now(), zones=zones, online=all(z.on is not None for z in zones.values()), hub=hub,
                        system_state="priming" if d.get("isPriming") is True else None)

    async def _read(self) -> BedFrame:
        r = await self._http.get("/api/deviceStatus")
        r.raise_for_status()
        f = self._frame_from(r.json() or {})
        self._frame = f
        return f

    async def _poll_once(self) -> None:
        try:
            await self._read()
            self._state = "connected"
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as e:
            self._state = f"the Pod answered {e.response.status_code}"
            log.warning("free-sleep: HTTP %s", e.response.status_code)
        except Exception as e:  # noqa: BLE001
            self._state = f"can't reach the Pod ({type(e).__name__})"
            log.warning("free-sleep: %s", type(e).__name__)

    async def _run(self) -> None:
        while True:
            await self._poll_once()
            await asyncio.sleep(self.poll_s)

    async def set_zone(self, zone: str, *, on: bool, temp_c: float | None = None) -> bool:
        if zone not in self.zones:
            raise CommandError(f"Unknown zone {zone!r}.")
        side: dict = {"isOn": bool(on)}
        if on and temp_c is not None:
            side["targetTemperatureF"] = _c_to_f(temp_c)
        try:
            r = await self._http.post("/api/deviceStatus", json={zone: side})
            if r.status_code == 400:
                raise CommandError("The Pod refused that setting.")
            r.raise_for_status()
            f = await self._read()                  # local and fast: read back to confirm
        except httpx.HTTPStatusError as e:
            raise CommandError(f"The Pod answered {e.response.status_code}.") from None
        except httpx.HTTPError as e:
            raise CommandError(f"Can't reach the Pod ({type(e).__name__}).") from None
        z = f.zones.get(zone) or ZoneFrame()
        if z.on is None or z.on != on:
            return False
        if "targetTemperatureF" in side and z.setpoint_c is not None:
            return abs(_c_to_f(z.setpoint_c) - side["targetTemperatureF"]) <= 1
        return True

    async def command(self, raw: str) -> str:
        raise CommandError("This bed has no raw console.")
