"""A clock that can run faster than real time and jump, so the simulator can replay a night in minutes."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo


class Clock:
    def __init__(self, tz: str = "UTC", speed: float = 1.0) -> None:
        self.tz = ZoneInfo(tz)
        self._real0 = time.time()
        self._virt0 = self._real0
        self._speed = speed

    @property
    def speed(self) -> float:
        return self._speed

    def now_ts(self) -> float:
        return self._virt0 + (time.time() - self._real0) * self._speed

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.now_ts(), self.tz)

    def set_speed(self, speed: float) -> None:
        cur = self.now_ts()
        self._real0, self._virt0, self._speed = time.time(), cur, max(0.0, float(speed))

    def jump_to(self, ts: float) -> None:
        self._real0, self._virt0 = time.time(), float(ts)


class FixedClock(Clock):
    """For tests: time only moves when told to."""

    def __init__(self, ts: float, tz: str = "UTC") -> None:
        super().__init__(tz)
        self._ts = ts

    def now_ts(self) -> float:
        return self._ts

    def advance(self, seconds: float) -> None:
        self._ts += seconds

    def jump_to(self, ts: float) -> None:
        self._ts = ts
