"""Sleepme Dock Pro (ChiliPad Dock Pro) through Sleepme's official developer API.

https://docs.developer.sleep.me . A personal API token from the Sleepme account (Account details → Developer API →
Create new token) is sent as a Bearer token. Each Dock Pro is one zone, so a two-sided bed has two docks: one per side.

  GET   /v1/devices            -> [{id, name, attachments}]
  GET   /v1/devices/{id}       -> {about, control: {set_temperature_c, thermal_control_status}, status: {water_temperature_c,
                                   is_connected, is_water_low}}
  PATCH /v1/devices/{id}       <- flat control fields, e.g. {"set_temperature_c": 20.5, "thermal_control_status": "active"}

The API allows 10 requests a minute per account, so this polls once a minute (two docks = two calls), turns a change
into a single PATCH, and trusts the PATCH answer instead of reading the device again. A 429 backs off.
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

API = "https://api.developer.sleep.me/v1"
MIN_C, MAX_C = 13.0, 48.0          # the API's own range for set_temperature_c (half-degree steps)


def _now() -> float:
    return time.time()


def parse_devices(spec: str | None) -> dict[str, str] | None:
    """'left=<id>,right=<id>' or a single '<id>' (left only) -> {side: device id}. Empty -> None (find them)."""
    raw = (spec or "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    if any(not p for p in parts):
        raise ValueError("ET_SLEEPME_DEVICES has an empty entry; use '<id>' or 'left=<id>,right=<id>'.")
    if len(parts) == 1 and "=" not in parts[0]:
        return {"left": parts[0]}
    out: dict[str, str] = {}
    for part in parts:
        side, sep, dev = (x.strip() for x in part.partition("="))
        if not sep or not dev:
            raise ValueError(f"Expected 'side=<device id>' in {part!r}.")
        if side not in ("left", "right"):
            raise ValueError(f"Bed side {side!r} must be 'left' or 'right'.")
        if side in out:
            raise ValueError(f"Duplicate bed side {side!r} in ET_SLEEPME_DEVICES.")
        out[side] = dev
    return {s: out[s] for s in ("left", "right") if s in out}


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _setpoint(temp_c: float) -> float:
    """The engine's temperature as the API takes it: half degrees, inside 13-48 °C (the extremes -1/999 are never sent)."""
    return min(MAX_C, max(MIN_C, round(float(temp_c) * 2) / 2))


class SleepmeBed:
    id = "sleepme"
    name = "Sleepme Dock Pro"
    capabilities = {Cap.ZONE_TEMP, Cap.ZONE_POWER, Cap.MEASURED_TEMP, Cap.HUB_HEALTH, Cap.WATER_STATUS}
    untried = {Cap.ZONE_TEMP, Cap.ZONE_POWER}
    profile = profiles.WATER_DOCK

    def __init__(self, token: str, devices: dict[str, str] | None = None, poll_s: float = 60.0, transport=None) -> None:
        if not token:
            raise ValueError("A Sleepme API token is needed.")
        self._devices = dict(devices) if devices else {}
        self.zones = list(self._devices) or ["left"]
        self.poll_s = max(30.0, float(poll_s))        # the 10-a-minute limit leaves room for commands only above ~30 s
        self._http = httpx.AsyncClient(base_url=API, timeout=15.0, transport=transport,
                                       headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        self._frame: BedFrame | None = None
        self._task: asyncio.Task | None = None
        self._state = "starting"
        self._backoff_until = 0.0
        self._zones: dict[str, ZoneFrame] = {}
        self._water_low: dict[str, bool] = {}

    # ---------- lifecycle ----------
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
                "online": bool(f and f.online and now - f.ts < 3 * self.poll_s + 30),
                "devices": dict(self._devices), "age_s": round(now - f.ts, 1) if f else None}

    # ---------- reading ----------
    async def _discover(self) -> None:
        """No device ids given: one dock is the left side, two docks are left and right in the order Sleepme lists them."""
        if self._devices:
            return
        r = await self._call("GET", "/devices")
        # a Dock Pro lists "CHILIPAD_PRO" in attachments; a Sleep Tracker (sensor only, no control) doesn't
        docks = [d for d in (r.json() or []) if isinstance(d, dict) and d.get("id")
                 and (d.get("attachments") is None or "CHILIPAD_PRO" in (d.get("attachments") or []))]
        if not docks:
            raise CommandError("No Dock Pro found on this Sleepme account.")
        sides = ("left", "right")
        self._devices = {sides[i]: str(d["id"]) for i, d in enumerate(docks[:2])}
        self.zones = list(self._devices)
        for side, d in zip(sides, docks):
            log.info("sleepme: %s side = dock %r (id %s)", side, str(d.get("name") or ""), d["id"])
        if len(docks) > 1:
            log.info("sleepme: set ET_SLEEPME_DEVICES=left=<id>,right=<id> if the sides are swapped")

    def _zone_from(self, side: str, d: dict) -> ZoneFrame:
        control = d.get("control") or {}
        status = d.get("status") or {}
        if status.get("is_connected") is False:
            return ZoneFrame(on=None)
        state = str(control.get("thermal_control_status") or "").lower()
        on = True if state == "active" else False if state == "standby" else None
        if "is_water_low" in status:
            self._water_low[side] = bool(status.get("is_water_low"))
        sp = _num(control.get("set_temperature_c"))
        if sp is not None and not (MIN_C <= sp <= MAX_C):
            sp = None                                   # -1 / 999 mean "max cold" / "max heat", not a temperature
        return ZoneFrame(on=on, setpoint_c=sp, measured_c=_num(status.get("water_temperature_c")))

    def _publish(self) -> None:
        zones = {s: self._zones.get(s, ZoneFrame()) for s in self.zones}
        water_ok = None if not self._water_low else not any(self._water_low.values())
        self._frame = BedFrame(ts=_now(), zones=zones, online=all(z.on is not None for z in zones.values()),
                               hub=HubHealth(water_ok=water_ok))

    async def _call(self, method: str, path: str, **kw) -> httpx.Response:
        if _now() < self._backoff_until:
            raise CommandError("Sleepme asked us to slow down; trying again shortly.")
        r = await self._http.request(method, path, **kw)
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("Retry-After") or 60)
            except ValueError:
                wait = 60.0
            self._backoff_until = _now() + max(10.0, min(wait, 600.0))
            raise CommandError("Sleepme asked us to slow down; trying again shortly.")
        if r.status_code == 401:
            raise CommandError("Sleepme refused the API token. Make a new one in your Sleepme account.")
        r.raise_for_status()
        return r

    async def _poll_once(self) -> None:
        try:
            await self._discover()
            for side, dev in self._devices.items():
                r = await self._call("GET", f"/devices/{dev}")
                self._zones[side] = self._zone_from(side, r.json() or {})
            self._publish()
            self._state = "connected"
        except asyncio.CancelledError:
            raise
        except CommandError as e:
            self._state = str(e)
        except httpx.HTTPStatusError as e:
            self._state = f"Sleepme answered {e.response.status_code}"
            log.warning("sleepme: HTTP %s", e.response.status_code)
        except Exception as e:  # noqa: BLE001
            self._state = f"error ({type(e).__name__})"
            log.warning("sleepme: %s", type(e).__name__)

    async def _run(self) -> None:
        while True:
            await self._poll_once()
            await asyncio.sleep(self.poll_s)

    # ---------- control ----------
    async def set_zone(self, zone: str, *, on: bool, temp_c: float | None = None) -> bool:
        try:
            await self._discover()
        except httpx.HTTPError as e:
            raise CommandError(f"Could not reach Sleepme ({type(e).__name__}).") from None
        if zone not in self._devices:
            raise CommandError(f"Unknown zone {zone!r}.")
        body: dict = {"thermal_control_status": "active" if on else "standby"}
        if on and temp_c is not None:
            body["set_temperature_c"] = _setpoint(temp_c)
        try:
            r = await self._call("PATCH", f"/devices/{self._devices[zone]}", json=body)
        except httpx.HTTPStatusError as e:
            raise CommandError(f"Sleepme answered {e.response.status_code}.") from None
        except httpx.HTTPError as e:
            raise CommandError(f"Could not reach Sleepme ({type(e).__name__}).") from None
        try:
            answer = r.json()
        except ValueError:
            answer = None
        prev = self._zones.get(zone, ZoneFrame())
        if isinstance(answer, dict) and ("control" in answer or "status" in answer):
            zf = self._zone_from(zone, answer)
        elif isinstance(answer, dict) and "thermal_control_status" in answer:
            zf = self._zone_from(zone, {"control": answer, "status": {}})
        else:
            zf = ZoneFrame(on=on, setpoint_c=body.get("set_temperature_c", prev.setpoint_c))
        if zf.measured_c is None:
            zf.measured_c = prev.measured_c
        self._zones[zone] = zf
        self._publish()
        # Confirmed when the answer shows what was asked for; an answer without the fields can't confirm or deny.
        if zf.on is None:
            return False
        ok = zf.on == on
        if ok and on and "set_temperature_c" in body and zf.setpoint_c is not None:
            ok = abs(zf.setpoint_c - body["set_temperature_c"]) < 0.26
        return ok

    async def command(self, raw: str) -> str:
        raise CommandError("This bed has no raw console.")
