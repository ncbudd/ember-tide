"""Simulation lab: sleepers who react to temperature, extra devices, and many nights in a row.

The real Controller, NightEngine and Autopilot run unchanged. Only the world around them is simulated:
- LabBed: the simulated topper, but each side's sleeper has a comfort preference per phase. When the pad is warmer or
  cooler than they like, they move more and their heart rate rises (what the bed's sensor strip would see).
- a watch per sleeper (optional) that writes skin temperature the way a Health Connect import would
- room temperature / humidity sensors (Autopilot reads them through adopted sources)
- taps ("too hot" / "too cold") and hot flashes, decided by the sleeper's discomfort
Everything is seeded, so a scenario gives the same nights every run.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import embertide.engine.controller as ctlmod
from embertide.clock import FixedClock
from embertide.drivers.base import SensorFrame
from embertide.drivers.sim import SimBed
from embertide.engine.controller import Controller
from embertide.seed import default_household
from embertide.sources import SourceConfig, SourceManager
from embertide.store import Store

TZ = ZoneInfo("America/New_York")


def ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


@dataclass
class Wearable:
    """A watch or ring on one sleeper. Writes skin temperature and/or heart rate the way an import (or the live
    watch heart-rate path, `live=True`) would, with its own bias, sampling rate and gaps. Flip `on` to take it off."""
    name: str = "LabWatch"
    skin: bool = True
    hr: bool = False
    skin_bias: float = 0.0           # reads this much high (+) or low (−), °C
    hr_bias: float = 0.0             # bpm
    every_s: float = 120             # one reading this often (rounded to the lab's step)
    gap_prob: float = 0.0            # chance per reading of a dropped reading (gaps)
    live: bool = False               # heart rate through the live watch path (wear:<pid>:hr:watch_live)
    on: bool = True


@dataclass
class Sleeper:
    """How one person reacts to their side. `pref_c[phase]` = how much cooler (−) or warmer (+) than the rhythm they
    actually want. 0 everywhere = perfectly comfortable on their rhythm."""
    pref_c: dict[str, float] = field(default_factory=dict)
    restless: float = 0.0            # extra movement probability, whatever the temperature (a restless sleeper)
    watch: bool = False              # writes skin temperature samples
    watch_name: str = "LabWatch"
    tap_prob: float = 0.0            # chance per check (every ~2 min) of tapping when clearly uncomfortable
    tap_phases: set[str] | None = None
    flash_phase: str | None = None   # a hot flash once a night, in this phase
    flash_minute: int = 20           # minutes into that phase
    skin_bias: float = 0.0           # this watch reads high / low
    wearables: list[Wearable] = field(default_factory=list)   # more devices (or instead of `watch`)

    def pref(self, phase: str | None) -> float:
        return self.pref_c.get(phase or "", self.pref_c.get("*", 0.0))


class LabBed(SimBed):
    def __init__(self, clock, lab: Lab, **kw) -> None:
        super().__init__(clock, **kw)
        self.lab = lab

    def _sensor(self, name: str, ts: float, done: dict[str, SensorFrame]) -> SensorFrame:
        f = super()._sensor(name, ts, done)
        z = self.z[name]
        sl = self.lab.sleepers.get(name)
        if not z.occupied or sl is None or f.status == 5:
            return f
        disc = self.lab.discomfort(name)
        move = sl.restless
        if abs(disc) >= 0.8:
            move = max(move, min(0.6, 0.15 + 0.25 * abs(disc)))
        status = f.status
        if move and self._rand.random() < move:
            status = self._rand.choice([4, 4, 6])
        hr = f.hr
        if hr is not None and abs(disc) >= 0.8:
            hr = round(hr + 4 * min(3.0, abs(disc)))
        return SensorFrame(status=status, hr=hr, br=f.br, clock=f.clock)


class Lab:
    """A household on the simulator with optional devices. Step it like test_scenarios.Night."""

    def __init__(self, tmp_path, start: str, sleepers: dict[str, Sleeper] | None = None, room_c: float | None = None,
                 humidity: float | None = None, room_profile=None, seed: int = 11, echo: bool = True, blips: bool = False,
                 household=None) -> None:
        ctlmod.MIN_CMD_GAP_S = 0
        self.clock = FixedClock(ts(start), "America/New_York")
        self.sleepers = sleepers or {}
        self.bed = LabBed(self.clock, self, room_c=21.5, seed=seed)
        self.bed.echo, self.bed.blips = echo, blips
        self.store = Store(tmp_path / "lab.db")
        self.hh = household or default_household()
        self.sources = SourceManager(self.store, self.clock, lambda: self.bed)
        self.c = Controller(self.hh, self.bed, self.store, self.clock, sources=self.sources)
        self.p1, self.p2 = self.hh.people
        for p in self.hh.people:                    # only the sleepers under test run Autopilot (keeps the lab fast)
            if p.zone not in self.sleepers:
                p.settings.autopilot.mode = "off"
        self.rand = random.Random(seed)
        self.room_c, self.humidity, self.room_profile = room_c, humidity, room_profile
        if room_c is not None or room_profile is not None:
            self.sources.configs.append(SourceConfig(id="labroom", name="Room", driver="sim", role="room_temp", kind="room"))
        if humidity is not None:
            self.sources.configs.append(SourceConfig(id="labhum", name="Humidity", driver="sim", role="room_humidity", kind="room"))
        self._last_tap: dict[str, float] = {}
        self._last_wear: dict[tuple[str, str], float] = {}
        self._flashed: set[tuple[str, str]] = set()
        self._phase_since: dict[str, tuple[str | None, float]] = {}
        self.mornings: list[dict] = []

    # ---------- the world ----------
    def person_on(self, zone: str):
        return next(p for p in self.hh.people if p.zone == zone)

    def discomfort(self, zone: str) -> float:
        """+ = too warm, − = too cold, in °C, against what this sleeper would like right now."""
        p = self.person_on(zone)
        e = self.c.engine_for(p.id)
        sl = self.sleepers.get(zone)
        if sl is None or e.s.base_c is None:
            return 0.0
        want = e.s.base_c + sl.pref(e.s.phase_id)
        room = self.current_room()
        extra = 0.0 if room is None else max(0.0, room - 22.0) * 0.35       # a warm room makes any side feel warmer
        if self.humidity is not None and self.humidity >= 65 and (room or 22) >= 22:
            extra += 0.3
        return round(self.bed.z[zone].measured - want + extra, 2)

    def current_room(self) -> float | None:
        if self.room_profile is not None:
            return self.room_profile(self.clock.now_ts())
        return self.room_c

    def _world(self) -> None:
        now = self.clock.now_ts()
        room = self.current_room()
        if room is not None:
            self.store.sample(now, "labroom", round(room + self.rand.uniform(-0.1, 0.1), 2))
        if self.humidity is not None:
            self.store.sample(now, "labhum", round(self.humidity + self.rand.uniform(-1, 1), 1))
        for zone, sl in self.sleepers.items():
            z = self.bed.z[zone]
            p = self.person_on(zone)
            e = self.c.engine_for(p.id)
            if not z.occupied:
                continue
            disc = self.discomfort(zone)
            if sl.watch:
                skin = 34.0 + sl.skin_bias + 0.35 * max(-2.0, min(2.0, disc)) + self.rand.uniform(-0.04, 0.04)
                self.store.sample(now, f"wear:{p.id}:skin_temp:{sl.watch_name}", round(skin, 2))
            for wd in sl.wearables:
                self._wear(p, zone, wd, disc, now)
            ph = e.s.phase_id
            prev = self._phase_since.get(zone)
            if not prev or prev[0] != ph:
                self._phase_since[zone] = (ph, now)
            if (sl.tap_prob and abs(disc) >= 1.0 and e.s.state == "rhythm" and (sl.tap_phases is None or ph in sl.tap_phases)
                    and now - self._last_tap.get(zone, -1e18) >= 45 * 60 and self.rand.random() < sl.tap_prob):
                self._last_tap[zone] = now
                self.tap(p, "too_hot" if disc > 0 else "too_cold")
            if sl.flash_phase and ph == sl.flash_phase and e.s.state == "rhythm" and e.night:
                key = (zone, e.night.date.isoformat())
                if key not in self._flashed and now - self._phase_since[zone][1] >= sl.flash_minute * 60:
                    self._flashed.add(key)
                    e.flash("hot", now, source="app")

    def _wear(self, p, zone: str, wd: Wearable, disc: float, now: float) -> None:
        key = (zone, wd.name)
        if not wd.on or now - self._last_wear.get(key, -1e18) < wd.every_s - 1:
            return
        self._last_wear[key] = now
        if wd.gap_prob and self.rand.random() < wd.gap_prob:
            return
        if wd.skin:
            skin = 34.0 + wd.skin_bias + 0.35 * max(-2.0, min(2.0, disc)) + self.rand.uniform(-0.04, 0.04)
            self.store.sample(now, f"wear:{p.id}:skin_temp:{wd.name}", round(skin, 2))
        if wd.hr:
            fr = self.bed.latest()
            hr = fr.zones[zone].sensor.hr if fr else None
            if hr is not None:
                v = round(hr + wd.hr_bias + self.rand.uniform(-1, 1))
                if wd.live:
                    from embertide.engine import watch
                    watch.record(self.store, p.id, [(now, v)], [], now)
                else:
                    self.store.sample(now, f"wear:{p.id}:hr:{wd.name}", float(v))

    def wearable(self, zone: str, name: str) -> Wearable:
        return next(w for w in self.sleepers[zone].wearables if w.name == name)

    def tap(self, p, feel: str) -> None:
        e = self.c.engine_for(p.id)
        z = self.bed.z[p.zone]
        self.store.event(self.clock.now_ts(), "feel", p.id, "app", feel=feel, phase=e.s.phase_id, target_c=e.s.target_c,
                         measured_c=round(z.measured, 2))

    # ---------- time ----------
    async def step(self, n: int = 1, dt: float = 120) -> None:
        for _ in range(n):
            self.bed._tick()
            self._world()
            await self.c.tick()
            for t in list(self.c._inflight.values()):
                await t
            self.clock.advance(dt)

    async def until(self, when: str | float, dt: float = 120) -> None:
        end = ts(when) if isinstance(when, str) else when
        while self.clock.now_ts() < end:
            await self.step(1, dt)

    async def nights(self, count: int, *, dt: float = 120, morning=None, evening: str = "21:00", end: str = "08:30") -> None:
        """Run `count` nights back to back, skipping the daytime. `morning(lab, date)` runs at 08:45 each day."""
        for _ in range(count):
            d = self.clock.now()
            start = d.replace(hour=int(evening[:2]), minute=int(evening[3:]), second=0, microsecond=0)
            if d >= start + timedelta(hours=1):
                start += timedelta(days=1)
            self.clock.jump_to(start.timestamp())
            self.c.autopilot._last.clear()
            stop = (start + timedelta(days=1)).replace(hour=int(end[:2]), minute=int(end[3:]))
            await self.until(stop.timestamp(), dt)
            self.clock.jump_to((stop + timedelta(minutes=15)).timestamp())
            await self.step(1, dt)
            if morning:
                r = morning(self, start.date())
                if r is not None:
                    self.mornings.append(r)

    # ---------- reading results ----------
    def events(self, kind: str, person: str | None = None) -> list[dict]:
        return sorted((x for x in self.store.events(limit=100000) if x["kind"] == kind and (person is None or x.get("person") == person)),
                      key=lambda x: x["ts"])

    def decisions(self, pid: str, status: str | None = None) -> list[dict]:
        return [x for x in self.events("autopilot", pid) if status is None or x["status"] == status]

    def suggest(self, p, show_all: bool = True) -> dict:
        from embertide.autopilot_api import _release, _suggest
        return _release(self.c, p, _suggest(self.c, p), show_all=show_all)
