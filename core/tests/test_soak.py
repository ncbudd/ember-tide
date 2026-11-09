"""Soak: many nights back to back with everything switched on, checking invariants at every step.

Both sleepers, every device type (radar, scale, room, watch HR + skin, phone on Wi-Fi, GPS, calendar, light), and
random life: late nights, trips, getting up, flashes, taps, manual changes, pauses, "off tonight", rhythm edits,
one swap of sides, sensors dropping out, and server restarts in the middle of the night (a new Controller from the
same store must pick the night up where it was).

Invariants, checked after every tick:
- nothing raises;
- a side is never on without its person's engine asking for a temperature (rhythm, change, by-hand or flash);
- whenever the engine says the rhythm is running, the side is on at that temperature;
- someone lying in their side for 10+ minutes inside their night window has a started night (no device, context or
  restart may keep a person in bed in a cold or warm-less bed), unless they turned it off or paused it;
- every setpoint sits inside the bed's limits;
- commands to the bed stay rate-limited (few per hour);
- Autopilot stays inside its nightly limits;
- the store and the in-memory state grow per night by a bounded amount.

Default: 20 nights (well under 2 minutes). SOAK_NIGHTS=90 for the long run.
"""

from __future__ import annotations

import os
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import embertide.engine.controller as ctlmod
from embertide.clock import FixedClock
from embertide.drivers.sim import SimBed
from embertide.engine.controller import Controller
from embertide.seed import default_household
from embertide.sources import SourceConfig, SourceManager
from embertide.store import Store
from test_scenarios import TZ, ts

NIGHTS = int(os.environ.get("SOAK_NIGHTS", "20"))
REAL_DT = float(os.environ.get("SOAK_DT", "0"))           # e.g. 15: step at the server's real telemetry cadence (slow)


class CountingBed(SimBed):
    def __init__(self, clock, **kw):
        super().__init__(clock, **kw)
        self.cmds: list[tuple[float, str, bool, float | None]] = []

    async def set_zone(self, zone, *, on, temp_c=None):
        self.cmds.append((self.clock.now_ts(), zone, on, temp_c))
        return await super().set_zone(zone, on=on, temp_c=temp_c)


def devices() -> list[SourceConfig]:
    S = SourceConfig
    return [
        S(id="radl", name="Radar L", driver="sim", role="presence", zone="left", kind="radar", params={"kind": "presence"}),
        S(id="radr", name="Radar R", driver="sim", role="presence", zone="right", kind="radar", params={"kind": "presence"}),
        S(id="kgl", name="Scale L", driver="sim", role="weight", zone="left", kind="scale", params={"kind": "weight", "kg": 80}),
        S(id="kgr", name="Scale R", driver="sim", role="weight", zone="right", kind="scale", params={"kind": "weight", "kg": 62}),
        S(id="room", name="Room", driver="sim", role="room_temp", kind="room", params={"kind": "room_temp"}),
        S(id="hum", name="Humidity", driver="sim", role="room_humidity", kind="room", params={"kind": "room_humidity"}),
        S(id="hr1", name="Watch HR", driver="sim", role="wearable_hr", zone="left", person="p1", kind="wearable", params={"kind": "wearable_hr"}),
        S(id="skin1", name="Watch skin", driver="sim", role="skin_temp", zone="left", person="p1", kind="wearable", params={"kind": "skin_temp"}),
        S(id="ph1", name="Phone p1", driver="sim", role="home", kind="phone", person="p1", params={"kind": "home", "away": [], "naps": True}),
        S(id="gps1", name="GPS p1", driver="sim", role="location", kind="location", person="p1", params={"kind": "location"}),
        S(id="ph2", name="Phone p2", driver="sim", role="home", kind="phone", person="p2", params={"kind": "home", "away": [], "naps": True}),
        S(id="cal2", name="Calendar p2", driver="sim", role="calendar", kind="calendar", person="p2", params={"kind": "calendar", "events": []}),
        S(id="light", name="Bedroom light", driver="sim", role="light", kind="light", params={"kind": "light", "off": "23:00", "on": "07:00"}),
    ]


def hm(t: datetime) -> str:
    return t.strftime("%H:%M")


class Soak:
    def __init__(self, tmp_path, start: str, seed: int = 5):
        ctlmod.MIN_CMD_GAP_S = 0
        self.rnd = random.Random(seed)
        self.clock = FixedClock(ts(start), "America/New_York")
        self.bed = CountingBed(self.clock, seed=seed)
        self.bed.scripted = False
        self.store = Store(tmp_path / "soak.db")
        self.db_path = tmp_path / "soak.db"
        self.hh = default_household()
        self.mgr = SourceManager(self.store, self.clock, lambda: self.bed)
        self.mgr.configs = devices()
        self.c = Controller(self.hh, self.bed, self.store, self.clock, sources=self.mgr)
        p1, p2 = self.hh.people
        p1.settings.autopilot.mode, p1.settings.autopilot.learn_skip = "act", True
        p2.settings.autopilot.mode = "suggest"
        self.violations: list[str] = []
        self.in_since: dict[str, float | None] = {"p1": None, "p2": None}
        self.plan: dict[str, dict] = {}
        self.restarts = 0
        self.swapped = False
        self.stats = defaultdict(int)
        self.mem: list[dict] = []
        self.rows: list[dict] = []

    # ---------- helpers ----------
    def person(self, pid):
        return next(p for p in self.hh.people if p.id == pid)

    def bad(self, msg):
        if len(self.violations) < 200:
            self.violations.append(f"{datetime.fromtimestamp(self.clock.now_ts(), TZ):%m-%d %H:%M} {msg}")

    def at(self, base: datetime, minutes: float) -> float:
        return (base + timedelta(minutes=minutes)).timestamp()

    # ---------- the plan for one night ----------
    def make_plan(self, evening: datetime):
        r = self.rnd
        weekend = evening.weekday() in (4, 5)
        for p in self.hh.people:
            bed_min = (23 * 60 if weekend else 22 * 60 + 15) - 17 * 60 - 30          # minutes after 17:30
            wake_min = (8 * 60 if weekend else 6 * 60 + 30) + 24 * 60 - 17 * 60 - 30
            pl = {"away_all": r.random() < 0.04, "trip": None, "event": None, "getups": [], "actions": []}
            inb = bed_min + r.gauss(10, 35)
            if r.random() < 0.12:                                                     # out late, driving home
                arrive = bed_min + r.uniform(30, 160)
                pl["trip"] = (self.at(evening, 60 + r.uniform(0, 60)), self.at(evening, arrive), r.uniform(5, 35))
                inb = arrive + r.uniform(5, 30)
            if r.random() < 0.08:                                                     # a calendar event in the evening
                end = bed_min + r.uniform(-60, 120)
                pl["event"] = (self.at(evening, end - 150), self.at(evening, end))
                inb = max(inb, end + 25)
            pl["in"] = self.at(evening, inb)
            pl["out"] = self.at(evening, wake_min + r.gauss(0, 20))
            for _ in range(r.choice([0, 0, 1, 1, 2])):
                t = r.uniform(pl["in"] + 3600, pl["out"] - 3600)
                pl["getups"].append((t, t + r.uniform(4, 25) * 60))
            for kind, prob in (("flash", 0.3), ("adjust", 0.35), ("tap", 0.25), ("pause", 0.05), ("skip", 0.03),
                               ("start_now", 0.05), ("manual_nap", 0.04)):
                if r.random() < prob:
                    if kind == "manual_nap":
                        pl["actions"].append((self.at(evening, r.uniform(0, 120)), kind))
                    elif kind == "start_now":
                        pl["actions"].append((pl["in"] - r.uniform(10, 40) * 60, kind))
                    elif kind == "skip":
                        pl["actions"].append((pl["in"] - 600, kind))
                    else:
                        pl["actions"].append((r.uniform(pl["in"] + 900, pl["out"] - 1800), kind))
            pl["actions"].sort()
            self.plan[p.id] = pl
        # context devices follow the plan (phones on Wi-Fi, GPS trip, calendar)
        pl1, pl2 = self.plan["p1"], self.plan["p2"]
        by = {c.id: c for c in self.mgr.configs}

        def hhmm(t):
            return hm(datetime.fromtimestamp(t, TZ))
        by["ph1"].params["away"] = [["17:31", "11:59"]] if pl1["away_all"] else ([[hhmm(pl1["trip"][0]), hhmm(pl1["trip"][1])]] if pl1["trip"] else [])
        by["gps1"].params["trip"] = ({"leave": hhmm(pl1["trip"][0]), "arrive": hhmm(pl1["trip"][1]), "km": pl1["trip"][2], "speed": 45}
                                     if pl1["trip"] else None)
        if pl1["away_all"]:
            by["gps1"].params["trip"] = {"leave": "17:31", "arrive": "11:59", "km": 200, "speed": 90}
        by["ph2"].params["away"] = [["17:31", "11:59"]] if pl2["away_all"] else []
        by["cal2"].params["events"] = [[hhmm(pl2["event"][0]), hhmm(pl2["event"][1]), "Out"]] if pl2["event"] else []
        by["light"].params["off"] = hhmm(min(pl1["in"], pl2["in"]) + 1200)
        by["radl"].enabled = by["radr"].enabled = self.rnd.random() > 0.1                  # radars drop out some nights
        by["hr1"].enabled = by["skin1"].enabled = self.rnd.random() > 0.15                 # watch not worn some nights

    # ---------- the world each step ----------
    def world(self, now: float):
        for p in self.hh.people:
            pl = self.plan[p.id]
            inside = not pl["away_all"] and pl["in"] <= now < pl["out"] and not any(a <= now < b for a, b in pl["getups"])
            if self.bed.z[p.zone].occupied != inside:
                self.bed.set_presence(p.zone, inside)
            self.in_since[p.id] = (self.in_since[p.id] or now) if inside else None
            e = self.c.engine_for(p.id)
            while pl["actions"] and pl["actions"][0][0] <= now:
                _, kind = pl["actions"].pop(0)
                self.stats[kind] += 1
                if kind == "flash":
                    e.flash(self.rnd.choice(["hot", "hot", "cold"]), now, source="app")
                elif kind == "adjust":
                    e.adjust(now, delta_c=self.rnd.choice([-0.56, -1.11, 0.56]), until="next_phase", source="app")
                elif kind == "tap":
                    self.store.event(now, "feel", p.id, "app", feel=self.rnd.choice(["too_hot", "too_cold"]), phase=e.s.phase_id)
                elif kind == "pause":
                    e.tonight(now, "pause", 30)
                elif kind == "skip":
                    e.tonight(now, "skip")
                elif kind == "start_now":
                    e.tonight(now, "start_now")
                elif kind == "manual_nap":
                    e.adjust(now, temp_c=24.0, minutes=60, source="app")

    # ---------- invariants ----------
    def check(self, now: float):
        hh = self.hh
        for p in hh.people:
            e = self.c.engine_for(p.id)
            z = self.bed.z[p.zone]
            if z.on and e.s.target_c is None:
                self.bad(f"{p.id} {p.zone} side ON but engine target None (state {e.s.state})")
            if e.s.state == "rhythm" and e.s.target_c is not None:
                if not z.on or abs(z.setpoint - round(max(hh.user_min_c, min(hh.user_max_c, e.s.target_c)) * 10) / 10) > 0.11:
                    if not e.s.flash:
                        self.bad(f"{p.id} rhythm running but side on={z.on} setpoint={z.setpoint} target={e.s.target_c}")
            if z.on and not (hh.bed_min_c - 0.01 <= z.setpoint <= hh.bed_max_c + 0.01):
                self.bad(f"{p.id} setpoint {z.setpoint} outside bed limits")
            n = e.night
            since = self.in_since[p.id]
            if (n and since and now - since >= 600 and n.window_start + 600 <= now < n.wake - 3600 and since >= n.window_start
                    and not e.s.skip and not (e.s.pause_until and now < e.s.pause_until + 600) and not e.s.started):
                self.bad(f"{p.id} in bed {round((now - since) / 60)} min inside the night window but night not started "
                         f"(state {e.s.state}, home {e.s.home})")

    def mem_snapshot(self) -> dict:
        ap = self.c.autopilot
        return {k: len(v) for k, v in {
            "ctx_home": self.c._ctx_home, "context": self.c.context, "fused": self.c.fused,
            "ap_last": ap._last, "ap_last_log": ap._last_log, "ap_logged": ap._last_logged_decision,
            "ap_nights_cache": ap.__dict__.get("_nights_cache", {}), "ap_base_cache": ap.__dict__.get("_base_cache", {}),
            "ap_room_cache": ap.__dict__.get("_room_cache", {}),
            "src_latest": self.mgr.latest, "src_occ": self.mgr._occ, "src_last_saved": self.mgr._last_saved,
            "src_last_event": self.mgr.__dict__.get("_last_event", {}), "src_stage_t": self.mgr._stage_t,
            "presence_sides": self.c.presence.sides, "inflight": self.c._inflight,
        }.items()}

    def counts(self) -> dict:
        db = self.store._db
        out = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("telemetry", "samples", "events", "stages", "kv")}
        out["kv_bytes"] = db.execute("SELECT COALESCE(SUM(LENGTH(v)),0) FROM kv").fetchone()[0] if self._kv_has_v() else 0
        return out

    def _kv_has_v(self) -> bool:
        cols = [r[1] for r in self.store._db.execute("PRAGMA table_info(kv)")]
        self._kv_col = "v" if "v" in cols else ("value" if "value" in cols else None)
        return self._kv_col == "v"

    # ---------- running ----------
    async def step(self, dt: float):
        now = self.clock.now_ts()
        self.world(now)
        self.bed._tick()
        self.mgr.sim_step(now)
        await self.c.tick()
        for t in list(self.c._inflight.values()):
            await t
        self.check(now)
        self.clock.advance(dt)

    def restart(self):
        """The server restarts: state persisted, a new Controller built from the same store."""
        before = {p.id: self.c.engine_for(p.id).s for p in self.hh.people}
        self.c._persist()
        self.c = Controller(self.hh, self.bed, self.store, self.clock, sources=self.mgr)
        self.restarts += 1
        for pid, s in before.items():
            a = self.c.engine_for(pid).s
            for f in ("key", "started", "in_bed", "skip", "pause_until"):
                if getattr(a, f) != getattr(s, f):
                    self.bad(f"restart lost {pid}.{f}: {getattr(s, f)} -> {getattr(a, f)}")
            if (s.override is None) != (a.override is None) or (s.flash is None) != (a.flash is None):
                self.bad(f"restart lost {pid} override/flash")

    def swap_sides(self):
        from embertide.models import person_color
        a, b = self.hh.people
        for p in (a, b):
            p.settings.color = person_color(p)
        a.zone, b.zone = b.zone, a.zone
        self.c.reload_household(self.hh)
        self.c.presence.sides.clear()
        for e in self.c.engines.values():
            e.s.presence_since = None
            e.s.absent_since = None
        for p in self.hh.people:                 # devices that belong to a side follow the bed, people's follow them
            self.in_since[p.id] = None
        self.swapped = True

    async def run(self, nights: int):
        for i in range(nights):
            day = datetime.fromtimestamp(self.clock.now_ts(), TZ)
            evening = day.replace(hour=17, minute=30, second=0, microsecond=0)
            if day >= evening + timedelta(hours=2):
                evening += timedelta(days=1)
            if i == nights // 2 and not self.swapped:
                self.clock.jump_to(evening.timestamp() - 3 * 3600)
                await self.step(60)
                self.swap_sides()
            self.clock.jump_to(evening.timestamp())
            self.make_plan(evening)
            restart_at = self.at(evening, self.rnd.uniform(5 * 60, 12 * 60)) if self.rnd.random() < 0.25 else None
            freeze_at = self.at(evening, self.rnd.uniform(6 * 60, 11 * 60)) if self.rnd.random() < 0.1 else None
            stop = self.at(evening, 16 * 60)                                       # 09:30 next morning
            c0 = self.counts()
            cmds0 = len(self.bed.cmds)
            while self.clock.now_ts() < stop:
                now = self.clock.now_ts()
                if restart_at and now >= restart_at:
                    restart_at = None
                    self.restart()
                if freeze_at and now >= freeze_at:
                    for z in self.bed.z.values():
                        z.frozen = True
                if freeze_at and now >= freeze_at + 1800:
                    freeze_at = None
                    for z in self.bed.z.values():
                        z.frozen = False
                near = any(abs(now - x) < 1800 for pl in self.plan.values() for x in (pl["in"], pl["out"]))
                await self.step(REAL_DT or (60 if near else 180))
            for z in self.bed.z.values():
                z.frozen = False
            c1 = self.counts()
            self.rows.append({k: c1[k] - c0[k] for k in c1} | {"cmds": len(self.bed.cmds) - cmds0})
            self.mem.append(self.mem_snapshot())
            # Autopilot nightly limits
            pol = self.person("p1").settings.autopilot
            applied = [x for x in self.store.events(since=evening.timestamp(), limit=5000, person="p1")
                       if x["kind"] == "autopilot" and x.get("status") == "applied" and x["ts"] <= stop]
            if len(applied) > pol.max_actions or abs(sum(x["delta_c"] for x in applied)) > pol.max_delta_c + 0.01:
                self.bad(f"autopilot over its limits: {len(applied)} changes, {sum(x['delta_c'] for x in applied):+.2f} °C")


def cmd_rate(cmds) -> int:
    """Most commands to one side in any hour. Ramps and flash ease-backs move in 0.1 °C steps, so a busy hour is
    bounded by the slope (a 3 °C ramp = 30 small steps), never a stream."""
    per_hour = Counter((int(t // 3600), z) for t, z, *_ in cmds)
    return max(per_hour.values()) if per_hour else 0


async def test_soak(tmp_path):
    s = Soak(tmp_path, "2026-09-01T12:00")
    await s.run(NIGHTS)
    half = len(s.rows) // 2
    avg = lambda rows, k: sum(r[k] for r in rows) / max(1, len(rows))      # noqa: E731
    s.store._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size = s.db_path.stat().st_size
    per_night = size / NIGHTS
    # numbers for the long run (printed with -s), before the checks
    print(f"\nSOAK nights={NIGHTS} dt={REAL_DT or 'coarse'} restarts={s.restarts} db={size / 1e6:.1f} MB "
          f"({per_night / 1e3:.0f} kB/night, ~{per_night * 365 / 1e6:.0f} MB/year) cmds/side/h max={cmd_rate(s.bed.cmds)} "
          f"stats={dict(s.stats)} rows/night={ {k: round(avg(s.rows, k)) for k in ('telemetry', 'samples', 'events')} } "
          f"mem={s.mem[-1]}")
    assert not s.violations, "\n".join(s.violations[:40])
    assert s.restarts >= 1 and s.swapped
    # commands: ramps and ease-backs move in 0.1 °C steps, so about one small step a minute at most, never a stream
    assert cmd_rate(s.bed.cmds) <= 60, cmd_rate(s.bed.cmds)
    dup = sum(1 for a, b in zip(s.bed.cmds, s.bed.cmds[1:]) if a[1:] == b[1:] and b[0] - a[0] < 600)
    assert dup <= len(s.bed.cmds) * 0.02, f"{dup} repeated identical commands"
    # growth per night stays flat: compare the second half with the first
    if half:
        for k in ("telemetry", "samples", "events"):
            assert avg(s.rows[half:], k) <= 1.5 * avg(s.rows[:half], k) + 50, (k, s.rows)
    assert max(r["events"] for r in s.rows) < 600, max(r["events"] for r in s.rows)
    assert s.counts()["kv"] < 200                                                  # kv doesn't sprout a key per night
    # in-memory state: bounded, not growing with the number of nights
    first, last = s.mem[min(1, len(s.mem) - 1)], s.mem[-1]
    for k, v in last.items():
        assert v <= max(first[k] * 2, first[k] + 10), (k, first[k], v)
