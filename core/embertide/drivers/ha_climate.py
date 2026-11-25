"""Home Assistant climate driver (BedJet via ESPHome/community integrations and other HA climate beds)."""

from __future__ import annotations

import asyncio
import logging
import math
import time

import httpx

from . import profiles
from .base import BedFrame, Cap, CommandError, ZoneFrame

log = logging.getLogger(__name__)

_REMEMBERED_ATTRS = ("hvac_modes", "fan_modes", "min_temp", "max_temp", "preset_modes", "friendly_name")


def _now() -> float:
    return time.time()


def parse_entities(spec: str) -> dict[str, str]:
    """Parse 'left=climate.a,right=climate.b' or a single 'climate.a' (left only) into {side: entity_id}."""
    raw = (spec or "").strip()
    if not raw:
        raise ValueError("ET_BED_ENTITIES cannot be empty; use 'climate.a' or 'left=climate.a,right=climate.b'.")
    parts = [p.strip() for p in raw.split(",")]
    if any(not p for p in parts):
        raise ValueError("ET_BED_ENTITIES has an empty entry; use 'climate.a' or 'left=climate.a,right=climate.b'.")

    if len(parts) == 1 and "=" not in parts[0]:
        ent = parts[0]
        if not ent.startswith("climate.") or len(ent) <= len("climate."):
            raise ValueError(f"Bed entity {ent!r} must start with 'climate.'.")
        return {"left": ent}

    out: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            raise ValueError(f"Expected 'side=climate.entity' in {part!r}; use 'left=climate.a,right=climate.b'.")
        side, _, ent = part.partition("=")
        side, ent = side.strip(), ent.strip()
        if side not in ("left", "right"):
            raise ValueError(f"Bed side {side!r} must be 'left' or 'right'.")
        if side in out:
            raise ValueError(f"Duplicate bed side {side!r} in ET_BED_ENTITIES.")
        if not ent.startswith("climate.") or len(ent) <= len("climate."):
            raise ValueError(f"Bed entity {ent!r} for {side} must start with 'climate.'.")
        out[side] = ent
    return {s: out[s] for s in ("left", "right") if s in out}


class HaClimateBed:
    id = "ha-climate"
    name = "Bed through Home Assistant"
    capabilities = {Cap.ZONE_TEMP, Cap.ZONE_POWER, Cap.MEASURED_TEMP}
    untried = {Cap.ZONE_TEMP, Cap.ZONE_POWER}
    profile = profiles.AIR

    def __init__(
        self,
        url: str,
        token: str,
        entities: dict[str, str],
        kind: str = "auto",
        neutral_c: float = 26.0,
        poll_s: float = 10.0,
        confirm_s: float = 6.0,
        transport=None,
    ) -> None:
        self._url = url.rstrip("/")
        self._entities = {s: entities[s] for s in ("left", "right") if s in entities}
        if not self._entities:
            raise ValueError("At least one side ('left' or 'right') must be configured.")
        self.zones = list(self._entities.keys())
        kind_norm = (kind or "auto").strip().lower()
        if kind_norm not in ("auto", "bedjet", "thermostat"):
            raise ValueError(f"ET_BED_KIND must be 'auto', 'bedjet', or 'thermostat', got {kind!r}.")
        self._kind = kind_norm
        self._neutral_c = float(neutral_c)
        self.poll_s = float(poll_s)
        self.confirm_s = float(confirm_s)      # how long set_zone waits for HA to show the new mode (Bluetooth is slow)
        self._http = httpx.AsyncClient(
            base_url=self._url,
            timeout=15.0,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )
        self._frame: BedFrame | None = None
        self._task: asyncio.Task | None = None
        self._state = "starting"
        self._unit: str | None = None
        self._attrs: dict[str, dict] = {}
        self._last_state: dict[str, str] = {}
        self._want: dict[str, float] = {}       # the temperature the engine last asked for, per side, while on

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
        return {
            "driver": self.id,
            "name": self.name,
            "online": bool(f and f.online and now - f.ts < 3 * self.poll_s + 10),
            "detail": self._state,
            "entities": dict(self._entities),
            "unit": self._unit,
            "age_s": round(now - f.ts, 1) if f else None,
        }

    def _kind_for(self, side: str) -> str:
        if self._kind in ("bedjet", "thermostat"):
            return self._kind
        eid = self._entities[side].lower()
        fn = str((self._attrs.get(side) or {}).get("friendly_name") or "").lower()
        return "bedjet" if ("bedjet" in eid or "bedjet" in fn) else "thermostat"

    def _to_c(self, val) -> float | None:
        if val is None or isinstance(val, bool):
            return None
        try:
            v = float(val)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v):
            return None
        if self._unit == "°F":
            return (v - 32.0) * 5.0 / 9.0
        return v

    def _from_c(self, temp_c: float, attrs: dict) -> float:
        if self._unit == "°F":
            val = float(round(temp_c * 9.0 / 5.0 + 32.0))
        else:
            val = round(temp_c * 2.0) / 2.0
        lo, hi = attrs.get("min_temp"), attrs.get("max_temp")
        if isinstance(lo, (int, float)) and not isinstance(lo, bool) and math.isfinite(lo):
            val = max(float(lo), val)
        if isinstance(hi, (int, float)) and not isinstance(hi, bool) and math.isfinite(hi):
            val = min(float(hi), val)
        return val

    def _fan_mode_for(self, temp_c: float, fan_modes: list) -> str | None:
        if not fan_modes:
            return None
        if self._neutral_c <= 16.0:
            t = 1.0
        else:
            t = max(0.0, min(1.0, (self._neutral_c - temp_c) / (self._neutral_c - 16.0)))
        idx = round((0.3 + 0.7 * t) * (len(fan_modes) - 1))
        idx = max(0, min(len(fan_modes) - 1, idx))
        return str(fan_modes[idx])

    async def _ensure_unit(self) -> None:
        if self._unit is not None:
            return
        r = await self._http.get("/api/config")
        r.raise_for_status()
        temp_unit = str(((r.json() or {}).get("unit_system") or {}).get("temperature") or "°C").strip()
        self._unit = "°F" if "F" in temp_unit.upper() else "°C"

    async def _fetch_entity(self, side: str) -> tuple[str, dict, ZoneFrame]:
        eid = self._entities[side]
        r = await self._http.get(f"/api/states/{eid}")
        r.raise_for_status()
        data = r.json() or {}
        attrs = data.get("attributes") or {}
        self._attrs[side] = {k: attrs.get(k) for k in _REMEMBERED_ATTRS}
        raw_state = str(data.get("state") or "").strip().lower()
        self._last_state[side] = raw_state
        return raw_state, attrs, self._build_zone_frame(side, raw_state, attrs)

    def _build_zone_frame(self, side: str, raw_state: str, attrs: dict) -> ZoneFrame:
        if raw_state in ("unavailable", "unknown", ""):
            return ZoneFrame(on=None)
        is_on = raw_state != "off"
        measured_c = self._to_c(attrs.get("current_temperature"))
        setpoint_c = self._want[side] if (is_on and side in self._want) else self._to_c(attrs.get("temperature"))
        return ZoneFrame(on=is_on, setpoint_c=setpoint_c, measured_c=measured_c)

    async def _post_service(self, service: str, payload: dict) -> None:
        r = await self._http.post(f"/api/services/climate/{service}", json=payload)
        r.raise_for_status()

    async def _send_heat(self, side: str, temp_c: float, attrs: dict) -> None:
        eid = self._entities[side]
        await self._post_service("set_hvac_mode", {"entity_id": eid, "hvac_mode": "heat"})
        await self._post_service("set_temperature", {"entity_id": eid, "temperature": self._from_c(temp_c, attrs)})

    async def _run(self) -> None:
        while True:
            await self._poll_once()
            await asyncio.sleep(self.poll_s)

    async def _poll_once(self) -> None:
        try:
            await self._ensure_unit()
            zones: dict[str, ZoneFrame] = {}
            for side in self.zones:
                # No re-arming here: if a BedJet ends heat by itself (its ~4 h timer), the engine sees the side off
                # while the plan wants it on and sends the command again, as it does for any bed.
                _, _, zf = await self._fetch_entity(side)
                zones[side] = zf
            self._frame = BedFrame(ts=_now(), zones=zones, online=all(z.on is not None for z in zones.values()))
            self._state = "connected"
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self._state = "Home Assistant refused the token"
                log.warning("ha-climate: Home Assistant refused the token")
            else:
                self._state = f"error ({type(e).__name__})"
                log.warning("ha-climate: %s (HTTP %s)", type(e).__name__, e.response.status_code)
        except Exception as e:  # noqa: BLE001
            self._state = f"error ({type(e).__name__})"
            log.warning("ha-climate: %s", type(e).__name__)

    async def set_zone(self, zone: str, *, on: bool, temp_c: float | None = None) -> bool:
        if zone not in self._entities:
            raise CommandError(f"Unknown zone {zone!r}.")
        eid = self._entities[zone]
        try:
            await self._ensure_unit()
            if zone not in self._attrs:
                await self._fetch_entity(zone)
            attrs = self._attrs.get(zone) or {}
            hvac_modes = list(attrs.get("hvac_modes") or [])
            kind = self._kind_for(zone)

            if not on:
                mode = "off"
                await self._post_service("set_hvac_mode", {"entity_id": eid, "hvac_mode": "off"})
                self._want.pop(zone, None)
            elif temp_c is None:
                cur = self._last_state.get(zone, "")
                if cur not in ("", "off", "unavailable", "unknown"):
                    mode = cur
                elif kind == "bedjet":
                    mode = "heat"
                else:
                    non_off = [m for m in hvac_modes if m != "off"]
                    mode = non_off[0] if non_off else "heat"
                await self._post_service("set_hvac_mode", {"entity_id": eid, "hvac_mode": mode})
            elif kind == "bedjet":
                if temp_c > self._neutral_c:
                    mode = "heat"
                    await self._send_heat(zone, temp_c, attrs)
                else:
                    # A BedJet's "cool" is its fan blowing room air; some setups call that fan_only.
                    mode = "cool" if ("cool" in hvac_modes or "fan_only" not in hvac_modes) else "fan_only"
                    await self._post_service("set_hvac_mode", {"entity_id": eid, "hvac_mode": mode})
                    fan_mode = self._fan_mode_for(temp_c, list(attrs.get("fan_modes") or []))
                    if fan_mode is not None:
                        await self._post_service("set_fan_mode", {"entity_id": eid, "fan_mode": fan_mode})
                self._want[zone] = float(temp_c)
            else:
                if "heat_cool" in hvac_modes:
                    mode = "heat_cool"
                elif "auto" in hvac_modes:
                    mode = "auto"
                else:
                    want_mode = "heat" if temp_c > self._neutral_c else "cool"
                    if want_mode in hvac_modes:
                        mode = want_mode
                    elif "heat" in hvac_modes:
                        mode = "heat"
                    elif "cool" in hvac_modes:
                        mode = "cool"
                    else:
                        non_off = [m for m in hvac_modes if m != "off"]
                        mode = non_off[0] if non_off else want_mode
                await self._post_service("set_hvac_mode", {"entity_id": eid, "hvac_mode": mode})
                await self._post_service("set_temperature", {"entity_id": eid, "temperature": self._from_c(temp_c, attrs)})
                self._want[zone] = float(temp_c)

            # Bluetooth beds take a moment to report the change: look a few times before calling it unconfirmed.
            deadline = _now() + self.confirm_s
            while True:
                raw_state, _, zf = await self._fetch_entity(zone)
                if raw_state == mode or _now() >= deadline:
                    break
                await asyncio.sleep(1.0)
            prev_zones = self._frame.zones if self._frame else {}
            zones = {s: (zf if s == zone else prev_zones.get(s, ZoneFrame())) for s in self.zones}
            polled_online = all(zones[s].on is not None for s in zones if s in self._last_state)
            self._frame = BedFrame(ts=_now(), zones=zones, online=polled_online)
            return raw_state == mode
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise CommandError("Home Assistant refused the token.") from None
            raise CommandError(f"Home Assistant returned HTTP {e.response.status_code}.") from None
        except httpx.HTTPError as e:
            raise CommandError(f"Could not reach Home Assistant ({type(e).__name__}).") from None

    async def command(self, raw: str) -> str:
        raise CommandError("This bed has no raw console.")
