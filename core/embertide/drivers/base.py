"""Driver contracts. The engine only ever talks to these, never to a specific bed."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class Cap(str, Enum):
    ZONE_TEMP = "zone_temp"
    ZONE_POWER = "zone_power"
    MEASURED_TEMP = "measured_temp"
    OCCUPANCY = "occupancy"
    BIO_HR = "bio_hr"
    BIO_BR = "bio_br"
    LED_RGB = "led_rgb"
    LED_DIM = "led_dim"
    FAN_QUIET = "fan_quiet"
    WATER_FILL = "water_fill"
    REBOOT = "reboot"
    RAW_CONSOLE = "raw_console"
    HUB_HEALTH = "hub_health"      # reports water, faults, signal, uptime and versions of the control unit
    WATER_STATUS = "water_status"      # ... including whether the water needs attention
    FAULTS = "faults"                  # ... including which fault flags are raised


@dataclass
class SensorFrame:
    status: int | None = None        # Sleepace status code (0 in bed … 5 empty, 255 invalid)
    hr: int | None = None
    br: int | None = None
    clock: tuple | None = None       # opaque token; must change for the sensor to count as alive


@dataclass
class ZoneFrame:
    on: bool | None = None
    setpoint_c: float | None = None
    measured_c: float | None = None
    sensor: SensorFrame = field(default_factory=SensorFrame)


@dataclass
class HubHealth:
    """How the control unit ("hub") is doing. Driver-neutral: a driver translates its own flags into these.
    `faults` holds generic codes (see hub.FAULTS) with an optional zone; the wording lives in hub.py."""
    water_ok: bool | None = None            # True only for the value a driver knows to be normal; None = not reported
    water_raw: dict[str, float] = field(default_factory=dict)   # the raw readings, shown to the owner only
    faults: list[tuple[str, str | None]] = field(default_factory=list)   # (generic fault code, zone or None)
    safety_ok: bool | None = None
    wifi_dbm: int | None = None
    wifi_name: str | None = None            # owner-only, never logged
    wifi_address: str | None = None         # owner-only, never logged
    uptime_s: int | None = None
    fw_version: str | None = None
    fw_build: str | None = None
    fw_panel: str | None = None             # the light/button panel's own version
    last_command: str | None = None         # the last command the unit says it received
    fan_rpm: dict[str, int] = field(default_factory=dict)
    fan_pwm: int | None = None
    humidity: float | None = None           # the unit's own room humidity reading (%)
    room_c: float | None = None
    free_memory_kb: int | None = None


@dataclass
class BedFrame:
    ts: float
    zones: dict[str, ZoneFrame]
    system_state: str | None = None
    firmware: str | None = None
    quiet_mode: bool | None = None
    brightness: int | None = None
    rgb: tuple[int, int, int] | None = None
    fault: bool | None = None
    online: bool = True
    hub: HubHealth | None = None


class CommandError(Exception):
    pass


class BedDriver(Protocol):
    id: str
    name: str
    capabilities: set[Cap]
    zones: list[str]

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def latest(self) -> BedFrame | None: ...
    async def set_zone(self, zone: str, *, on: bool, temp_c: float | None = None) -> bool:
        """Returns True once telemetry confirms the change (or the driver cannot confirm)."""
        ...
    async def command(self, raw: str) -> str: ...
    # Optional control-unit operations; a driver offers one only if it lists the matching capability
    # (LED_DIM/LED_RGB, FAN_QUIET, REBOOT, WATER_FILL). They raise CommandError when refused.
    async def set_light(self, *, brightness: int | None = None, rgb: tuple[int, int, int] | None = None) -> str: ...
    async def set_quiet_fan(self, on: bool) -> str: ...
    async def restart(self) -> str: ...
    async def start_water_fill(self) -> str: ...
    def status(self) -> dict: ...
