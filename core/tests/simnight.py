"""Kit for the whole-night presence / sensor / engine simulations (test_sim_night_*.py).

`Sim` is the edge-case rig (real Controller, Notifier, a bed that can lose power) with a SourceManager always attached,
a recorder of what each person's night and each side of the bed did on every step, and small devices that behave like
the real ones: a Home Assistant entity (polled: silent when HA is down), an MQTT topic (event only: never repeats),
a phone (the phone_context store), a live watch heart rate.

Everything runs on a simulated clock. Nothing here touches the network."""

from __future__ import annotations

import random
from datetime import datetime

from embertide.drivers.base import SensorFrame
from embertide.engine import phone_context as PC
from embertide.engine import watch as WATCH
from embertide.engine.summary import summarize
from embertide.models import Household
from embertide.sources import SourceConfig, SourceManager
from sim_edges import TZ, ActiveBed, PowerBed, Rig, household, ts  # noqa: F401  (re-exported for the tests)

__all__ = ["TZ", "ts", "Sim", "Dev", "hm", "household", "GlitchBed"]


def hm(t: float | None) -> str:
    return datetime.fromtimestamp(t, TZ).strftime("%H:%M") if t else "-"


class GlitchBed(ActiveBed):
    """The real strip's quirks on top of the power bed: garbage frames, empty blocks, a stale sensor clock, a hub that
    ignores commands, a pad temperature that never moves, and a hub fault."""

    def __init__(self, clock, **kw) -> None:
        super().__init__(clock, **kw)
        self.garbage: dict[str, bool] = {}        # zone -> frames come back as NaN / garbage (status 255, hr/br None)
        self.empty_block: dict[str, bool] = {}    # zone -> the whole sensor block is missing (status None)
        self.junk: dict[str, int] = {}            # zone -> frames carry this unknown status code (hr/br present)
        self.ignore_commands = False              # the hub is online but takes no commands
        self.stuck_measured: float | None = None  # the pad reports this temperature whatever the setpoint
        self.commands: list[tuple[float, str, bool, float | None]] = []   # (ts, zone, on, temp) every command sent
        self.reading: dict[str, bool] = {}        # zone -> lying awake and still (reading): steady heart rate, a page turn now and then

    async def set_zone(self, zone, *, on, temp_c=None) -> bool:
        self.commands.append((self.clock.now_ts(), zone, on, temp_c))
        if self.ignore_commands:
            return False
        return await super().set_zone(zone, on=on, temp_c=temp_c)

    def _sensor(self, name, t, done):
        f = super()._sensor(name, t, done)
        if self.reading.get(name) and self.z[name].occupied:
            r = self._rr = getattr(self, "_rr", None) or random.Random(11)
            return SensorFrame(status=r.choice([0] * 19 + [4]), hr=round(70 + r.uniform(-2, 2)), br=round(15 + r.uniform(-1, 1)),
                               clock=f.clock)
        if self.garbage.get(name):
            return SensorFrame(status=255, hr=None, br=None, clock=f.clock)
        if self.junk.get(name) is not None and (self.z[name].occupied or self.junk[name] == 5):
            return SensorFrame(status=self.junk[name], hr=f.hr, br=f.br, clock=f.clock)
        if self.empty_block.get(name):
            return SensorFrame(status=None, hr=None, br=None, clock=f.clock)
        return f

    def _tick(self) -> None:
        super()._tick()
        if self.stuck_measured is not None and self._frame is not None:
            for z in self._frame.zones.values():
                z.measured_c = self.stuck_measured


class Dev:
    """One extra sensor. `mode`: live (re-sent every step, like a polled Home Assistant entity), event (sent only when its
    value changes, like an MQTT topic), down (the link is out: nothing arrives), unavailable (HA says so)."""

    def __init__(self, sim: "Sim", cfg: SourceConfig, mode: str) -> None:
        self.sim, self.cfg, self.mode = sim, cfg, mode
        self.value: float | None = None
        self._sent = object()

    @property
    def id(self) -> str:
        return self.cfg.id

    def set(self, v: float | None) -> None:
        self.value = v
        if self.mode == "event":
            self.push()

    def push(self) -> None:
        s = self.sim.sources
        if self.value is None:
            s._sample(self.cfg, None, note="unavailable")
        elif self.cfg.driver == "mqtt-topic":
            s.on_mqtt(self.cfg, str(self.value).encode())
        else:
            s._sample(self.cfg, float(self.value))

    def feed(self) -> None:
        if self.mode == "live" and self.value is not None:
            self.push()
        elif self.mode == "unavailable":
            self.sim.sources._sample(self.cfg, None, note="Home Assistant says it's unavailable")


class Sim(Rig):
    def __init__(self, tmp_path, start: str, hh: Household | None = None, bed_cls=GlitchBed, notifier: bool = True) -> None:
        self.devs: dict[str, Dev] = {}
        self.rec: list[tuple[float, dict]] = []
        self.watch_hr: dict[str, bool] = {}
        self.tick_failures: list[tuple[float, str]] = []
        self.dt = 120.0
        super().__init__(tmp_path, start, bed_cls=bed_cls, hh=hh, notifier=notifier)

    def boot(self) -> None:
        self.sources = SourceManager(self.store, self.clock, lambda: self.bed)
        super().boot()

    # ---------- devices ----------
    def add(self, kind: str, zone: str | None, *, mode: str = "live", driver: str = "ha-entity", sid: str | None = None,
            person: str | None = None, role: str | None = None, **tuning) -> Dev:
        role = role or {"radar": "presence", "motion": "presence", "pad": "presence", "scale": "weight",
                        "room": "room_temp", "wearable": "wearable_hr"}.get(kind, "presence")
        sid = sid or f"{kind}{len(self.devs)}"
        cfg = SourceConfig(id=sid, name=sid, driver=driver, role=role, kind=kind, zone=zone, person=person, tuning=tuning,
                           params={"entity_id": f"binary_sensor.{sid}"} if driver == "ha-entity" else {"topic": f"home/{sid}", "url": "mqtt://localhost"})
        self.sources.configs.append(cfg)
        self.sources.save()
        if kind in ("radar", "scale"):
            self.dt = min(self.dt, 60.0)          # the settle-in check wants a sample every minute or so
        d = self.devs[sid] = Dev(self, cfg, mode)
        return d

    def retune(self, dev: Dev, **tuning) -> None:
        dev.cfg.tuning.update(tuning)
        self.sources.save()

    def phone(self, p, **fields) -> None:
        PC.update(self.store, p.id, fields, self.clock.now_ts(), sharing=["location", "wifi", "calendar", "alarm", "charging", "watch", "dial", "watch_hr"])

    def watch(self, p, bpm: int) -> None:
        WATCH.record(self.store, p.id, [(self.clock.now_ts(), bpm)], [], self.clock.now_ts())

    def pad(self, zone: str, v: bool) -> None:
        self.bed.set_presence(zone, v)

    # ---------- time ----------
    async def step(self, dt: float | None = None) -> None:
        dt = dt or self.dt
        for d in self.devs.values():
            d.feed()
        self.bed._tick()
        if self.server:
            try:                                  # the real loop logs a failed tick and goes on (Controller._run)
                await self.c.tick()
            except Exception as ex:  # noqa: BLE001
                self.tick_failures.append((self.clock.now_ts(), repr(ex)))
            for t in list(self.c._inflight.values()):
                await t
            try:
                self.c._persist()
            except Exception as ex:  # noqa: BLE001
                self.tick_failures.append((self.clock.now_ts(), repr(ex)))
        self.clock.advance(dt)
        self.record()

    def fail_once(self, method: str, *, when=None) -> None:
        """The next call to store.<method> (that `when(args, kwargs)` accepts) raises 'database is locked', once."""
        import sqlite3
        real = getattr(self.store, method)
        state = {"left": 1}

        def wrapped(*a, **k):
            if state["left"] and (when is None or when(a, k)):
                state["left"] -= 1
                raise sqlite3.OperationalError("database is locked")
            return real(*a, **k)
        setattr(self.store, method, wrapped)

    def record(self) -> None:
        now = self.clock.now_ts()
        row = {}
        for p in self.hh.people:
            e = self.e(p)
            z = self.bed.z[p.zone]
            row[p.id] = (e.s.state, bool(e.s.started), e.s.phase_id, bool(z.on), z.setpoint)
        self.rec.append((now, row))

    async def run(self, until: str, dt: float | None = None, script=None, watch=None) -> None:
        await super().run(until, dt=dt or self.dt, script=script, watch=watch)

    # ---------- reading it back ----------
    def timeline(self, p) -> list[tuple[str, str]]:
        """[(HH:MM, state)] one entry per change of the person's night state."""
        out, last = [], None
        for t, row in self.rec:
            st = row[p.id][0]
            if st != last:
                out.append((hm(t), st))
                last = st
        return out

    def states(self, p, a: str, b: str) -> set[str]:
        return {row[p.id][0] for t, row in self.rec if ts(a) <= t < ts(b)}

    def at(self, p, when: str):
        """(state, started, phase, on, setpoint) at the first step at/after `when`."""
        t0 = ts(when)
        for t, row in self.rec:
            if t >= t0:
                return row[p.id]
        raise AssertionError(f"nothing recorded at {when}")

    def started_count(self, p) -> int:
        return len(self.events("rhythm_started", p.id))

    def summary(self, p, night: str | None = None) -> dict:
        d = datetime.fromisoformat(night).date() if night else self.e(p).night.date
        return summarize(self.store, p, d, TZ, self.clock.now_ts()) or {}

    def kinds(self, person: str | None = None) -> list[str]:
        return self.told(person)


def T(hhmm: str, evening: str = "2026-11-05") -> float:
    """HH:MM on the night that began on `evening`: before noon means the next morning."""
    from datetime import date, timedelta
    d = date.fromisoformat(evening)
    nxt = hhmm.startswith("+")                 # "+13:30" = 13:30 the next day, whatever the hour
    hhmm = hhmm.lstrip("+")
    h = int(hhmm[:2])
    return ts(f"{(d + timedelta(days=1 if (h < 12 or nxt) else 0)).isoformat()}T{hhmm}")


class Plan:
    """Who is where, minute by minute, as the world sees it: pad presence per side, devices that follow a side, and
    one-shot actions at a time. Pass the plan as `script=` to Sim.run."""

    def __init__(self, evening: str = "2026-11-05") -> None:
        self.evening = evening
        self.iv: dict[str, list[tuple[float, float]]] = {"left": [], "right": []}
        self.follow: list[tuple] = []          # (device, zone, fn(occupied) -> value, lag_s)
        self.pad_on = {"left": True, "right": True}
        self.actions: list[tuple[float, object]] = []
        self._done: set[int] = set()

    def sleep(self, zone: str, a: str, b: str, ups: list[tuple[str, str]] = ()) -> "Plan":
        """In bed from a to b, except for the (from, to) pairs."""
        edges = [T(a, self.evening)]
        for u, v in ups:
            edges += [T(u, self.evening), T(v, self.evening)]
        edges.append(T(b, self.evening))
        for i in range(0, len(edges), 2):
            self.iv[zone].append((edges[i], edges[i + 1]))
        return self

    def occupied(self, zone: str, now: float) -> bool:
        return any(a <= now < b for a, b in self.iv[zone])

    def drive(self, dev, zone: str, fn=lambda occ: 1.0 if occ else 0.0, lag_s: float = 0.0) -> "Plan":
        self.follow.append((dev, zone, fn, lag_s))
        return self

    def at(self, hhmm: str, fn) -> "Plan":
        self.actions.append((T(hhmm, self.evening), fn))
        return self

    def __call__(self, r, now: float) -> None:
        for z in ("left", "right"):
            if self.pad_on[z]:
                r.pad(z, self.occupied(z, now))
        for dev, zone, fn, lag in self.follow:
            dev.set(fn(self.occupied(zone, now - lag)))
        for i, (t, fn) in enumerate(self.actions):
            if i not in self._done and now >= t:
                self._done.add(i)
                fn(r)
