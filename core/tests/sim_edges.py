"""Shared harness for the edge-case simulations (test_rhythm_edges, test_power_loss, test_active_period).

- `household()`: the default two-person household with a work-night rhythm of bed 22:30 / wake 07:00 (pre-cool,
  wind-down, fall asleep, deep, late night at 03:00, warm wake) and a day-off rhythm of 23:30 / 09:00.
- `PowerBed`: the simulated topper with a power switch and a broker link. Off = no reports at all and both zones
  really off; back on = a boot of ~90 s with both zones off at the topper's default, status 1 then 255, a frozen
  sensor clock and no pad temperature (the real topper reads 0.00 °C while booting); broker down = the bed keeps
  running its last setpoints but nobody hears from it or reaches it.
- `ActiveBed`: pads that see people awake and moving: codes 4/6/7 (and some 0), heart 90-140, breathing 20-35.
- `Rig`: the real Controller + Notifier on top, a server that can crash and come back from its store, steps of
  `dt` virtual seconds, and helpers to read events and states.
"""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

import embertide.engine.controller as ctlmod
from embertide.clock import FixedClock
from embertide.drivers.base import BedFrame, SensorFrame, ZoneFrame
from embertide.drivers.sim import SimBed
from embertide.engine.controller import Controller
from embertide.models import Household
from embertide.seed import default_household
from embertide.store import Store

TZ = ZoneInfo("America/New_York")


def ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


def hm(t: float | None) -> str:
    return datetime.fromtimestamp(t, TZ).strftime("%H:%M") if t else "-"


def household() -> Household:
    hh = default_household()
    for p in hh.people:
        for r in p.rhythms:
            if r.id == "weeknights":
                r.schedule.bedtime, r.schedule.wake = "22:30", "07:00"
            else:
                r.schedule.bedtime, r.schedule.wake = "23:30", "09:00"
    return hh


class PowerBed(SimBed):
    def __init__(self, clock, **kw) -> None:
        super().__init__(clock, **kw)
        self.power = True
        self.link = True
        self.boot_until: float | None = None
        self.boot_s = 90
        self.scripted, self.blips = False, False

    def latest(self):
        f = self._frame
        if f is not None and (not self.power or not self.link):
            f.online = False
        return f

    def status(self) -> dict:
        ok = bool(self.power and self.link and self._frame is not None and self.clock.now_ts() - self._frame.ts < 20)
        return {**super().status(), "online": ok, "detail": "simulator" if ok else "no word from the bed"}

    def power_off(self) -> None:
        self.power = False

    def power_on(self, boot_s: int = 90, zones_on_default: bool = False) -> None:
        self.power = True
        self.boot_s = boot_s
        self.boot_until = self.clock.now_ts() + boot_s
        for z in self.z.values():
            z.on, z.setpoint = zones_on_default, 27.0

    def booting(self) -> bool:
        return self.boot_until is not None and self.clock.now_ts() < self.boot_until

    async def set_zone(self, zone, *, on, temp_c=None) -> bool:
        if not self.power or not self.link or self.booting():
            return False
        return await super().set_zone(zone, on=on, temp_c=temp_c)

    def _tick(self) -> None:
        now = self.clock.now_ts()
        if not self.power:
            for z in self.z.values():
                z.on = False
            self._last_ts = now
            return
        if self.booting():
            code = 1 if now < self.boot_until - self.boot_s / 2 else 255
            self._frame = BedFrame(ts=now, zones={n: ZoneFrame(on=False, setpoint_c=z.setpoint, measured_c=None,
                                                             sensor=SensorFrame(status=code, clock=(0, 0, 0, 0)))
                                                  for n, z in self.z.items()}, system_state="SYSTEM_OFF", online=self.link)
            self._last_ts = now
            return
        if not self.link:
            keep = self._frame
            super()._tick()
            self._frame = keep
            return
        super()._tick()


class ActiveBed(PowerBed):
    def __init__(self, clock, **kw) -> None:
        super().__init__(clock, **kw)
        self.active: dict[str, bool] = {}
        self._r = random.Random(5)

    def _sensor(self, name, t, done):
        z = self.z[name]
        if self.active.get(name) and z.occupied:
            clock = (z.clock & 255, (z.clock >> 8) & 255, (z.clock >> 16) & 255, (z.clock >> 24) & 255)
            return SensorFrame(status=self._r.choice([4, 4, 6, 7, 7, 0]), hr=self._r.randint(90, 140),
                               br=self._r.randint(20, 35), clock=clock)
        return super()._sensor(name, t, done)


class RoomBed(PowerBed):
    """A topper that feels the bedroom: far from the room it moves more slowly and settles a little short of the
    setpoint (2026-11-08: room 18.4 °C, set 27.2, held at 26.4). `room_c` is the bedroom temperature."""
    LOSS = 0.09               # settles this share of (setpoint − room) short of the setpoint
    def _drive(self, z):
        if not (z.on and self.system_on):
            return self.room_c, self.tau_off_min
        gap = z.setpoint - self.room_c
        heading = z.setpoint - self.LOSS * gap
        slow = 4.0 * max(0.0, abs(gap) - 3.0)               # minutes added per °C beyond 3 °C from the room
        return heading, self.tau_on_min + slow


class Rig:
    def __init__(self, tmp_path, start: str, bed_cls=PowerBed, hh: Household | None = None, notifier: bool = True,
                 room: bool = False) -> None:
        ctlmod.MIN_CMD_GAP_S = 0
        self.clock = FixedClock(ts(start), "America/New_York")
        self.bed = bed_cls(self.clock)
        self.bed.echo = True
        self.store = Store(tmp_path / "rig.db")
        self.sources = None
        if room:
            from embertide.sources import SourceConfig, SourceManager
            self.sources = SourceManager(self.store, self.clock, lambda: self.bed)
            self.sources.configs.append(SourceConfig(id="room", name="Room", driver="sim", role="room_temp", kind="room"))
        self.store.put("household", (hh or household()).model_dump())
        self.with_notifier = notifier
        self.server = True
        self.boot()

    def boot(self) -> None:
        """Start (or restart) the server: a fresh Controller from what the store kept."""
        self.hh = Household.model_validate(self.store.get("household"))
        self.c = Controller(self.hh, self.bed, self.store, self.clock, sources=self.sources)
        if self.with_notifier:
            from embertide.notify import Notifier
            self.c.notifier = Notifier(self.c)
        self.p1, self.p2 = self.hh.people
        self.server = True

    def crash(self) -> None:
        self.server = False

    def e(self, p):
        return self.c.engine_for(p.id if hasattr(p, "id") else p)

    async def step(self, dt: float = 30) -> None:
        if self.sources is not None and int(self.clock.now_ts()) % 300 < dt:
            self.store.sample(self.clock.now_ts(), "room", round(self.bed.room_c, 2))     # the room sensor, every 5 min
        self.bed._tick()
        if self.server:
            await self.c.tick()
            for t in list(self.c._inflight.values()):
                await t
            self.c._persist()                 # the real loop saves every 10 s
        self.clock.advance(dt)

    async def run(self, until: str, dt: float = 30, script=None, watch=None) -> None:
        """Step to `until`. `script(rig, now)` sets the world before each step; `watch(rig, now)` records after."""
        end = ts(until)
        while self.clock.now_ts() < end:
            if script:
                script(self, self.clock.now_ts())
            await self.step(dt)
            if watch:
                watch(self, self.clock.now_ts() - dt)

    def events(self, kind: str | None = None, person: str | None = None) -> list[dict]:
        return sorted((x for x in self.store.events(limit=100000) if (kind is None or x["kind"] == kind)
                       and (person is None or x.get("person") == person)), key=lambda x: x["ts"])

    def notes(self) -> list[str]:
        return [x["title"] for x in self.events("notification")]

    def told(self, person: str | None = None) -> list[str]:
        """What each notification was about (its kind), sent or held for the morning (quiet hours, notify.py);
        the wording varies by tone and day (voice.py), the kind doesn't."""
        return [x["about"] for x in self.events() if x["kind"] in ("notification", "notification_held")
                and (person is None or x.get("person") == person)]
