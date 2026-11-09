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
    def status(self) -> dict: ...
