"""Combinations lab: the bed plus context devices (phone on Wi-Fi, phone location, calendar, bedroom light), a watch
and a room sensor, over many nights. Each night follows a plan (when the person is out, gets into bed, turns the
light off), the devices report what that plan implies, and the real controller, engine, Autopilot and tuning guide
run unchanged.

Devices here are fed by hand from the plan (no driver task runs), so noise, gaps and dead batteries can be scripted.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import pytest

from embertide.engine.autopilot import Window, evaluate
from embertide.engine.context import person_context
from embertide.engine.guide import apply_schedule, tuning_guide
from embertide.engine.rhythm import resolve
from embertide.models import AutopilotPolicy
from embertide.sources import SourceConfig, _calendar_value
from lab import TZ, Lab, Sleeper, ts


# ───────────────────────── plan-driven world ─────────────────────────
def at(d: str, hm: str) -> float:
    """Evening-relative time: 'HH:MM' on the evening of d, or the next morning when before noon."""
    day = date.fromisoformat(d)
    h, m = (int(x) for x in hm.split(":"))
    if h < 12:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ).timestamp()


def evening_of(t: float) -> str:
    x = datetime.fromtimestamp(t, TZ)
    return ((x - timedelta(days=1)) if x.hour < 12 else x).date().isoformat()


def dev(id_, role, kind, person="p1", **tuning):
    return SourceConfig(id=id_, name=id_, driver="mqtt-topic", role=role, kind=kind, person=person,
                        params={"url": "mqtt://x", "topic": id_}, tuning=tuning)


class ComboLab(Lab):
    """Lab + a nightly plan per person + hand-fed context devices.

    plan[person_id][evening ISO date] = {"in": "22:20", "out": "06:30", "away": [("18:00", "22:10")],
        "trip": ("18:30", "23:20", km, speed), "events": [("19:00", "23:30")], "lights_off": "22:45",
        "ups": [("03:00", 10)], "phone_dead_from": "21:30"}
    Missing evenings use `default[person_id]`.
    """

    def __init__(self, tmp_path, start, plans, defaults, devices=(), feeders=None, **kw):
        kw.setdefault("echo", False)
        super().__init__(tmp_path, start, **kw)
        self.bed.scripted = False
        self.plans, self.defaults = plans, defaults
        self.sources.configs += list(devices)
        self.feeders = dict(feeders or {})
        self.timeline: list[tuple] = []
        self.rnd = random.Random(5)

    def plan(self, pid, t):
        d = evening_of(t)
        return d, {**self.defaults.get(pid, {}), **self.plans.get(pid, {}).get(d, {})}

    def in_bed(self, pid, t) -> bool:
        d, pl = self.plan(pid, t)
        if not pl.get("in"):
            return False
        if not at(d, pl["in"]) <= t < at(d, pl["out"]):
            return False
        for hm, mins in pl.get("ups", []):
            if at(d, hm) <= t < at(d, hm) + mins * 60:
                return False
        return True

    def home(self, pid, t) -> bool:
        d, pl = self.plan(pid, t)
        if any(at(d, a) <= t < at(d, b) for a, b in pl.get("away", [])):
            return False
        tr = pl.get("trip")
        return not (tr and at(d, tr[0]) <= t < at(d, tr[1]))

    def distance(self, pid, t):
        d, pl = self.plan(pid, t)
        tr = pl.get("trip")
        if not tr:
            return 0.0
        leave, arrive, km, sp = at(d, tr[0]), at(d, tr[1]), tr[2], tr[3]
        drive = km / sp * 3600
        if t < leave or t >= arrive:
            return 0.0
        if t < leave + drive:
            return km * (t - leave) / drive
        if t >= arrive - drive:
            return km * (arrive - t) / drive
        return km

    def calendar(self, pid, t):
        d, pl = self.plan(pid, t)
        for a, b in pl.get("events", []):
            ta, tb = at(d, a), at(d, b)
            if ta - 6 * 3600 <= t < tb:
                return float(tb)
        return 0.0

    def light(self, t):
        pl = self.plan("p1", t)[1]
        d = evening_of(t)
        if pl.get("lights_never_off"):
            return 1.0
        if not pl.get("lights_off"):
            return 0.0
        off, on = at(d, pl["lights_off"]), at(d, pl["out"])
        for hm, mins in pl.get("light_ups", pl.get("ups", [])):
            if at(d, hm) <= t < at(d, hm) + mins * 60:
                return 1.0
        return 0.0 if off <= t < on else 1.0

    # standard feeders for the standard device ids
    def feed(self, now):
        for c in self.sources.configs:
            if c.driver != "mqtt-topic" or not c.enabled:
                continue
            fn = self.feeders.get(c.id)
            if fn is not None:
                v = fn(self, now)
            elif c.role == "home":
                d, pl = self.plan(c.person, now)
                dead = pl.get("phone_dead_from")
                if dead and now >= at(d, dead):
                    v = None
                else:
                    v = 1.0 if self.home(c.person, now) else 0.0
            elif c.role == "location":
                v = round(self.distance(c.person, now), 3)
            elif c.role == "calendar":
                v = self.calendar(c.person, now)
            elif c.role == "light":
                v = self.light(now)
            else:
                v = None
            if v is not None:
                self.sources._sample(c, v, now)

    async def step(self, n=1, dt=120):
        for _ in range(n):
            now = self.clock.now_ts()
            for p in self.hh.people:
                self.bed.set_presence(p.zone, self.in_bed(p.id, now))
            self.feed(now)
            self.bed._tick()
            self._world()
            await self.c.tick()
            for t in list(self.c._inflight.values()):
                await t
            for p in self.hh.people:
                e = self.c.engine_for(p.id)
                ctx = self.c.context.get(p.id) or {}
                self.timeline.append((now, p.id, e.s.state, ctx.get("home"), self.bed.z[p.zone].on, e.s.started,
                                      self.in_bed(p.id, now), ctx.get("heading_home"), ctx.get("eta_min"), dict(ctx)))
            self.clock.advance(dt)

    async def evenings(self, dates, start="17:00", end="08:40", dt=120, morning=True):
        """Run each evening from `start` to `end` (next morning by default), skipping the day between."""
        for d in dates:
            self.clock.jump_to(at(d, start))
            self.c.autopilot._last.clear()
            await self.until(at(d, end), dt)
            if morning:
                self.clock.jump_to(at(d, "08:55"))
                await self.step(1, dt)

    def tl(self, pid, a, b):
        return [x for x in self.timeline if x[1] == pid and a <= x[0] < b]


def days(first: str, n: int) -> list[str]:
    d0 = date.fromisoformat(first)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def ids(items):
    return {i["id"]: i for i in items}


FORTNIGHT = days("2026-11-01", 14)              # Sun 1 Nov .. Sat 14 Nov
WEEKNIGHT = {"in": "22:20", "out": "06:30", "lights_off": "22:45"}
WEEKEND = {"in": "23:05", "out": "07:50", "lights_off": "23:30"}


def weekday(d):
    return date.fromisoformat(d).weekday()       # Mon 0 .. Sun 6


def base_plan(extra=None, shift_in=0):
    """Nights by weekday: Sun-Thu weeknights, Fri/Sat weekends, plus Tuesday gym, Thursday dinner, Saturday concert."""
    plan = {}
    for d in FORTNIGHT:
        wd = weekday(d)
        pl = dict(WEEKEND if wd in (4, 5) else WEEKNIGHT)
        if shift_in:
            for k in ("in", "lights_off"):
                h, m = map(int, pl[k].split(":"))
                t = h * 60 + m + shift_in
                pl[k] = f"{(t // 60) % 24:02d}:{t % 60:02d}"
        if wd == 1:                              # Tuesday gym, back by 19:45
            pl["events"] = [("18:00", "19:30")]
            pl["away"] = [("17:45", "19:45")]
        if wd == 3:                              # Thursday dinner out, driving home
            pl["trip"] = ("18:30", "23:20", 18.0, 45.0)
            pl["events"] = [("19:00", "22:30")]
            pl.update({"in": "23:40", "lights_off": "23:55"})
        if wd == 5:                              # Saturday concert until 00:30
            pl["events"] = [("20:00", "00:30")]
            pl["away"] = [("19:15", "00:55")]
            pl.update({"in": "01:10", "lights_off": "01:20"})
        plan[d] = pl
    if extra:
        for d, v in extra.items():
            plan.setdefault(d, {}).update(v)
    return plan


def full_devices(person="p1", **gps_tuning):
    return [dev("phone", "home", "phone", person), dev("gps", "location", "location", person, **gps_tuning),
            dev("cal", "calendar", "calendar", person), dev("light", "light", "light", None)]


# ───────────────────────── 1. a realistic fortnight ─────────────────────────
@pytest.fixture(scope="module")
def fortnight(tmp_path_factory):
    import asyncio
    tmp = tmp_path_factory.mktemp("fortnight")
    lab = ComboLab(tmp, "2026-11-01T16:00", plans={"p1": base_plan()}, defaults={"p1": WEEKNIGHT},
                   devices=full_devices(), sleepers={"left": Sleeper(watch=True)}, room_c=21.0)
    asyncio.get_event_loop_policy()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(lab.evenings(FORTNIGHT))
    loop.close()
    return lab


def test_fortnight_precool_held_while_out_to_dinner(fortnight):
    lab = fortnight
    thu = "2026-11-05"
    rows = lab.tl("p1", at(thu, "21:40"), at(thu, "22:30"))
    assert rows and all(r[2] == "away" and r[3] is False and not r[4] for r in rows), \
        [(datetime.fromtimestamp(r[0], TZ).strftime("%H:%M"), r[2], r[3], r[4]) for r in rows][:6]


def test_fortnight_bed_ready_before_arriving_home(fortnight):
    lab = fortnight
    thu = "2026-11-05"
    arrive = at(thu, "23:20")
    rows = lab.tl("p1", at(thu, "22:00"), arrive)
    on = [r for r in rows if r[2] == "waiting" and r[4]]
    assert on, "pre-condition never resumed before the arrival"
    first = on[0][0]
    drive_start = arrive - 18.0 / 45.0 * 3600                    # a 24-minute drive: shorter than ready_min (30)
    # heading home needs a few readings showing a steady fall (so GPS jitter can't fake it): ~6-10 min into the drive
    assert first <= drive_start + 10 * 60, f"pre-condition resumed {(first - drive_start) / 60:.0f} min into the drive home"
    assert any(r[7] for r in rows), "never saw 'heading home'"


async def test_long_drive_home_bed_ready_ahead_of_arrival(tmp_path):
    """A 60-minute drive home: pre-condition starts about ready_min (30) before arriving, not at the start of the drive."""
    night = "2026-11-03"
    plan = {night: dict(WEEKNIGHT, trip=("18:00", "23:30", 60.0, 60.0), **{"in": "23:45"})}
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT},
                   devices=[dev("gps", "location", "location")])
    await lab.evenings([night], start="21:00", end="23:40", dt=30)
    arrive = at(night, "23:30")
    on = [r for r in lab.tl("p1", at(night, "22:00"), arrive) if r[2] == "waiting" and r[4]]
    assert on
    lead = (arrive - on[0][0]) / 60
    assert 25 <= lead <= 40, f"pre-condition started {lead:.0f} min before arriving"


def test_fortnight_gym_evening_resumes_after_return(fortnight):
    lab = fortnight
    tue = "2026-11-03"
    rows = lab.tl("p1", at(tue, "21:30"), at(tue, "22:15"))
    assert rows and all(r[3] is True for r in rows)              # home long before pre-condition
    assert any(r[2] == "waiting" and r[4] for r in rows)


def test_fortnight_every_night_started_when_in_bed(fortnight):
    lab = fortnight
    for d in FORTNIGHT:
        pl = lab.plan("p1", at(d, "21:00"))[1]
        t = at(d, pl["in"]) + 15 * 60
        rows = [r for r in lab.tl("p1", t, t + 600)]
        assert rows and all(r[5] for r in rows), (d, rows[:1])


def test_fortnight_learning_finishes_and_comfortable_sleeper_gets_nothing(fortnight):
    lab = fortnight
    now = lab.clock.now_ts()
    lr = lab.c.autopilot.learning(lab.p1, now)
    assert lr["done"] and lr["have"] == 7
    acted = [x for x in lab.decisions("p1") if x["status"] in ("suggested", "applied")]
    assert len(acted) <= 1, [(x["phase"], x["direction"], [r["signal"] for r in x["reasons"]]) for x in acted]
    assert lab.suggest(lab.p1, show_all=False)["items"] == []


def test_fortnight_guide_notes_events_and_away_and_keeps_bedtime(fortnight):
    lab = fortnight
    g = ids(tuning_guide(lab.c, lab.p1, lab.clock.now_ts()))
    assert "guide-late-events" in g, list(g)
    assert "guide-away" in g, list(g)
    assert not any(k.startswith("guide-bedtime") or k.startswith("guide-wake") for k in g), list(g)
    assert "guide-lights-reading" in g                               # lights off ~25 min after getting in
    assert "guide-add-context" not in g                               # it has context devices
    assert "guide-add-room" not in g                                  # it has a room sensor


def test_fortnight_gym_is_not_a_late_event(fortnight):
    g = ids(tuning_guide(fortnight.c, fortnight.p1, fortnight.clock.now_ts()))
    ev = g["guide-late-events"]["evidence"]
    assert not any(e.startswith("2026-11-03") or e.startswith("2026-11-10") for e in ev), ev    # Tuesdays: gym ends 19:30


def test_fortnight_snapshot_context_is_plain(fortnight):
    snap = fortnight.c.snapshot()
    ctx = next(p for p in snap["people"] if p["id"] == "p1")["night"]["context"]
    assert set(ctx) >= {"home", "why", "distance_km", "eta_min", "heading_home", "busy_until", "lights_on"}


# ───────────────────────── 2. consistently late to bed ─────────────────────────
async def test_late_every_night_proposes_bedtime_and_applying_moves_precool(tmp_path):
    week = days("2026-11-01", 7)
    plan = {d: (dict(WEEKNIGHT, **{"in": "23:05", "lights_off": "23:30"}) if weekday(d) not in (4, 5)
                else dict(WEEKEND, **{"in": "23:50", "lights_off": "00:10"})) for d in week}
    lab = ComboLab(tmp_path, "2026-11-01T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT},
                   devices=[dev("light", "light", "light", None)], room_c=21.0)
    await lab.evenings(week, start="21:00", dt=180)
    g = ids(tuning_guide(lab.c, lab.p1, lab.clock.now_ts()))
    b = g.get("guide-bedtime-weeknights")
    assert b and b["proposal"]["to"] in ("23:00", "23:15"), list(g)                 # in bed ~23:05-23:10
    rh = next(r for r in lab.p1.rhythms if r.id == "weeknights")
    before = resolve(rh, date(2026, 11, 9), TZ).precool
    apply_schedule(lab.p1, b["proposal"])
    after = resolve(rh, date(2026, 11, 9), TZ).precool
    h, m = map(int, b["proposal"]["to"].split(":"))
    assert after - before == ((h * 60 + m) - (22 * 60 + 15)) * 60                    # pre-condition moved with bedtime


# fixed (was a known bug)
async def test_late_on_both_rhythms_gets_a_proposal_for_each(tmp_path):
    week = days("2026-11-01", 14)
    plan = {d: (dict(WEEKNIGHT, **{"in": "23:05"}) if weekday(d) not in (4, 5) else dict(WEEKEND, **{"in": "23:50"}))
            for d in week}
    lab = ComboLab(tmp_path, "2026-11-01T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT}, room_c=21.0)
    await lab.evenings(week, start="21:30", dt=240)
    g = ids(tuning_guide(lab.c, lab.p1, lab.clock.now_ts()))
    assert "guide-bedtime-weeknights" in g and "guide-bedtime-weekends" in g, list(g)


# ───────────────────────── 3. noisy, stale and missing phone data ─────────────────────────
async def test_in_bed_always_runs_the_night_even_if_context_says_away(tmp_path):
    """Phone left in the car (Wi-Fi: away all night) and GPS saying 12 km: the bed says someone is in it, so the
    night must start and keep running."""
    night = "2026-11-03"
    plan = {night: dict(WEEKNIGHT, away=[("17:00", "09:00")], trip=("17:00", "09:00", 12.0, 40.0))}
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT},
                   devices=[dev("phone", "home", "phone"), dev("gps", "location", "location")])
    await lab.evenings([night], start="20:30", dt=60)
    rows = lab.tl("p1", at(night, "22:30"), at(night, "06:15"))
    assert rows and all(r[3] is False for r in rows)               # context really did say away
    assert all(r[5] and r[4] for r in rows), "a side someone is lying in was off"
    assert any(e["kind"] == "rhythm_started" for e in lab.store.events(limit=500, person="p1"))


async def test_out_of_bed_hold_while_away_flag_set(tmp_path):
    """Gets up at 03:00 for 20 min while the phone shows away (charging in the car): the side holds, then resumes."""
    night = "2026-11-03"
    plan = {night: dict(WEEKNIGHT, away=[("02:50", "04:00")], ups=[("03:00", 20)])}
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT}, devices=[dev("phone", "home", "phone")])
    await lab.evenings([night], start="21:30", dt=60)
    rows = lab.tl("p1", at(night, "03:01"), at(night, "03:19"))
    assert rows and all(r[2] == "out_of_bed_hold" and r[4] for r in rows), set(r[2] for r in rows)
    after = lab.tl("p1", at(night, "03:30"), at(night, "04:00"))
    assert all(r[2] == "rhythm" for r in after)


async def test_wifi_naps_while_asleep_never_turn_home_false(tmp_path):
    """The phone drops off Wi-Fi for 2-4 minutes at a time all night (sleep mode): never 'away'."""
    night = "2026-11-03"
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={}, defaults={"p1": WEEKNIGHT}, devices=[dev("phone", "home", "phone")],
                   feeders={"phone": lambda L, t: None if (int(t // 180) % 5 == 0) else 1.0})
    await lab.evenings([night], start="21:00", dt=60)
    assert not any(r[3] is False for r in lab.tl("p1", at(night, "21:30"), at(night, "08:00")))


# fixed (was a known bug)
async def test_dead_phone_battery_is_unknown_not_away(tmp_path):
    night = "2026-11-03"
    plan = {night: dict(WEEKNIGHT, phone_dead_from="20:30")}
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT}, devices=[dev("phone", "home", "phone")])
    await lab.evenings([night], start="19:30", dt=60)
    rows = lab.tl("p1", at(night, "21:35"), at(night, "22:15"))
    assert rows and all(r[3] is not False for r in rows), [r[3] for r in rows][:5]
    assert all(r[2] == "waiting" and r[4] for r in rows)


async def test_gps_jitter_parked_far_away_is_not_heading_home(tmp_path):
    night = "2026-11-03"
    jitter = random.Random(9)
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={}, defaults={"p1": dict(WEEKNIGHT, **{"in": "02:00", "out": "06:30"})},
                   devices=[dev("gps", "location", "location")],
                   feeders={"gps": lambda L, t: round(12.0 + jitter.uniform(-0.3, 0.3), 3)})
    await lab.evenings([night], start="20:00", end="01:30", dt=30)
    rows = lab.tl("p1", at(night, "21:00"), at(night, "01:00"))
    assert all(r[3] is False for r in rows)
    assert all(r[2] == "away" for r in rows if r[0] >= at(night, "21:30"))


# fixed (was a known bug)
async def test_gps_jitter_parked_close_by_is_not_heading_home(tmp_path):
    night = "2026-11-03"
    jitter = random.Random(4)
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={}, defaults={"p1": dict(WEEKNIGHT, **{"in": "02:00", "out": "06:30"})},
                   devices=[dev("gps", "location", "location")],
                   feeders={"gps": lambda L, t: round(3.0 + jitter.uniform(-0.3, 0.3), 3)})
    await lab.evenings([night], start="20:00", end="01:30", dt=30)
    rows = lab.tl("p1", at(night, "21:00"), at(night, "01:00"))
    assert not any(r[7] for r in rows), sum(1 for r in rows if r[7])
    assert all(r[3] is False for r in rows)


async def test_gps_lost_while_driving_home(tmp_path):
    """GPS stops reporting 10 km out on the drive home: never stuck 'away' forever; once it's stale the side goes back
    to 'unknown' and pre-condition runs."""
    night = "2026-11-03"
    plan = {night: dict(WEEKNIGHT, **{"in": "23:30"}, trip=("19:00", "23:15", 30.0, 60.0))}
    cut = at(night, "22:55")
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT},
                   devices=[dev("gps", "location", "location")],
                   feeders={"gps": lambda L, t: None if t >= cut else round(L.distance("p1", t), 3)})
    await lab.evenings([night], start="18:30", dt=60)
    late = lab.tl("p1", at(night, "23:30"), at(night, "23:40"))
    assert late and all(r[3] is not False for r in late), [r[3] for r in late]
    assert all(r[5] for r in lab.tl("p1", at(night, "23:45"), at(night, "05:00")))


# ───────────────────────── 4. two people ─────────────────────────
async def test_one_away_one_home_partner_unaffected(tmp_path):
    night = "2026-11-03"
    plan = {"p1": {night: dict(WEEKNIGHT, away=[("18:00", "23:50")], **{"in": "00:10"})},
            "p2": {night: {"in": "22:30", "out": "06:30"}}}
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans=plan, defaults={"p1": WEEKNIGHT, "p2": {"in": "22:30", "out": "06:30"}},
                   devices=[dev("phone1", "home", "phone", "p1"), dev("phone2", "home", "phone", "p2")])
    await lab.evenings([night], start="19:00", dt=60)
    p1 = lab.tl("p1", at(night, "21:40"), at(night, "23:40"))
    p2 = lab.tl("p2", at(night, "21:40"), at(night, "23:40"))
    assert all(r[2] == "away" and not r[4] for r in p1)
    assert all(r[3] is True for r in p2) and any(r[5] for r in p2)
    assert all(r[4] for r in p2 if r[0] >= at(night, "21:45"))


# ───────────────────────── 5. very late home, home but not in bed ─────────────────────────
async def test_driving_home_after_window_end_still_gets_a_working_bed(tmp_path):
    night = "2026-11-03"
    plan = {night: dict(WEEKNIGHT, trip=("19:00", "02:20", 25.0, 50.0), **{"in": "02:35", "out": "07:00"})}
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT},
                   devices=[dev("gps", "location", "location")])
    await lab.evenings([night], start="20:00", dt=60)
    assert all(not r[4] for r in lab.tl("p1", at(night, "21:40"), at(night, "01:30")))     # no pre-condition for nobody
    started = lab.tl("p1", at(night, "02:45"), at(night, "05:00"))
    assert started and all(r[5] and r[4] for r in started), "a 2:35 AM arrival must still get a working bed"


async def test_home_but_not_in_bed_precool_runs_but_not_forever(tmp_path):
    """Home all evening but on the sofa until 02:10: pre-condition runs, stops after window end (+ hold), starts again
    when they finally get in."""
    night = "2026-11-03"
    plan = {night: dict(WEEKNIGHT, **{"in": "02:10"})}
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT},
                   devices=[dev("phone", "home", "phone")])
    await lab.evenings([night], start="21:00", dt=60)
    assert all(r[4] for r in lab.tl("p1", at(night, "21:35"), at(night, "01:25")))
    off = lab.tl("p1", at(night, "02:01"), at(night, "02:10"))
    assert off and all(r[2] == "missed" and not r[4] for r in off)
    assert all(r[5] for r in lab.tl("p1", at(night, "02:20"), at(night, "05:00")))


# ───────────────────────── 6. calendar edge cases ─────────────────────────
def _ha_cal(state, start, end):
    return {"state": state, "attributes": {"start_time": start, "end_time": end}}


# fixed (was a known bug)
def test_all_day_event_is_not_an_evening_event():
    now = ts("2026-11-03T21:00")
    v = _calendar_value(_ha_cal("on", "2026-11-03 00:00:00", "2026-11-04 00:00:00"), now)
    assert v == 0.0


def test_calendar_value_timed_cases():
    now = ts("2026-11-03T21:00")
    assert _calendar_value(_ha_cal("on", "2026-11-03T19:00:00-05:00", "2026-11-03T23:30:00-05:00"), now) == ts("2026-11-03T23:30")
    assert _calendar_value(_ha_cal("off", "2026-11-03T18:00:00-05:00", "2026-11-03T19:30:00-05:00"), now) == 0.0    # over
    assert _calendar_value(_ha_cal("off", "2026-11-04T09:00:00-05:00", "2026-11-04T10:00:00-05:00"), now) == 0.0    # tomorrow
    assert _calendar_value(_ha_cal("off", "2026-11-03T22:00:00-05:00", "2026-11-03T23:00:00-05:00"), now) == ts("2026-11-03T23:00")
    assert _calendar_value({"state": "off", "attributes": {}}, now) == 0.0


def test_ha_calendar_times_without_a_zone_are_the_households():
    """HA writes start/end without a zone, in the home's time. The server machine (a container in UTC, CI) may be in
    another zone: they must be read in the household's (2026-11-09: CI in UTC missed a 10 PM event)."""
    from zoneinfo import ZoneInfo
    ny, utc = ZoneInfo("America/New_York"), ZoneInfo("UTC")
    now = datetime(2026, 11, 3, 21, 0, tzinfo=ny).timestamp()
    end = datetime(2026, 11, 3, 23, 0, tzinfo=ny).timestamp()
    assert _calendar_value(_ha_cal("off", "2026-11-03 22:00:00", "2026-11-03 23:00:00"), now, ny) == end
    # read as UTC it would be 5 hours off (already over), which is exactly the bug
    assert _calendar_value(_ha_cal("off", "2026-11-03 22:00:00", "2026-11-03 23:00:00"), now, utc) == 0.0


async def test_event_ending_before_bedtime_doesnt_delay_anything(tmp_path):
    night = "2026-11-03"
    plan = {night: dict(WEEKNIGHT, events=[("18:00", "20:00")])}
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT}, devices=[dev("cal", "calendar", "calendar")])
    await lab.evenings([night], start="17:00", end="22:00", dt=60, morning=False)
    rows = lab.tl("p1", at(night, "17:00"), at(night, "22:00"))
    assert any(r[9]["busy_until"] for r in rows if r[0] < at(night, "20:00"))       # busy while it's on
    assert all(r[9]["busy_until"] is None for r in rows if r[0] >= at(night, "20:21"))      # event end + 20 min travel


# fixed (was a known bug)
async def test_overlapping_events_wait_for_the_last_to_end(tmp_path):
    """Two overlapping evening events (dinner to 22:00, then a show to 23:40). HA shows one at a time; the fallback
    must wait until the show plus the drive home (20 min) is over."""
    night = "2026-11-03"
    plan = {night: dict(WEEKNIGHT, **{"in": "00:30"}, events=[("19:00", "22:00"), ("21:30", "23:40")])}
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT}, devices=[dev("cal", "calendar", "calendar")])
    # bed sensors silent: the fallback is the only way the night can start
    lab.bed.z["left"].frozen = lab.bed.z["right"].frozen = True
    lab.in_bed = lambda pid, t: False
    await lab.evenings([night], start="19:30", end="02:00", dt=60)
    starts = [e for e in lab.store.events(limit=500, person="p1") if e["kind"] == "rhythm_started"]
    assert starts and starts[0]["ts"] >= at(night, "00:00"), [datetime.fromtimestamp(e["ts"], TZ) for e in starts]


async def test_event_past_wake_time_never_starts_on_schedule(tmp_path):
    """A night shift until 07:00: with silent sensors the schedule fallback must not start a night for nobody."""
    night = "2026-11-03"
    plan = {night: dict(WEEKNIGHT, events=[("21:00", "07:00")])}
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT}, devices=[dev("cal", "calendar", "calendar")])
    lab.bed.z["left"].frozen = lab.bed.z["right"].frozen = True
    lab.in_bed = lambda pid, t: False
    await lab.evenings([night], start="20:30", end="07:30", dt=120)
    assert not [e for e in lab.store.events(limit=500, person="p1") if e["kind"] == "rhythm_started"]


# ───────────────────────── 7. lights ─────────────────────────
async def _week_with_lights(tmp_path, night_extra):
    week = days("2026-11-01", 6)
    plan = {d: dict(WEEKNIGHT, **night_extra) for d in week}
    lab = ComboLab(tmp_path, "2026-11-01T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT},
                   devices=[dev("light", "light", "light", None)], room_c=21.0)
    await lab.evenings(week, start="21:30", dt=180)
    return lab, ids(tuning_guide(lab.c, lab.p1, lab.clock.now_ts()))


async def test_getting_up_with_the_light_on_is_noted(tmp_path):
    lab, g = await _week_with_lights(tmp_path, {"ups": [("02:00", 9), ("04:00", 9)]})
    assert "guide-lights-ups" in g, list(g)
    assert not any(k.startswith("guide-bedtime") for k in g)


async def test_lights_left_on_all_night_draw_no_conclusions(tmp_path):
    lab, g = await _week_with_lights(tmp_path, {"lights_never_off": True})
    assert "guide-lights-reading" not in g and "guide-lights-ups" not in g, list(g)
    assert not any(k.startswith("guide-bedtime") for k in g)


async def test_lights_off_before_getting_into_bed_is_not_reading(tmp_path):
    lab, g = await _week_with_lights(tmp_path, {"lights_off": "22:10"})
    assert "guide-lights-reading" not in g


# ───────────────────────── 8. Autopilot with combined signals ─────────────────────────
async def test_warm_room_late_gym_restless_gets_reasonable_cooler_changes(tmp_path):
    week = days("2026-11-01", 4)
    plan = {d: dict(WEEKNIGHT, events=[("19:30", "21:00")], away=[("19:15", "21:15")]) for d in week}
    lab = ComboLab(tmp_path, "2026-11-01T16:00", plans={"p1": plan}, defaults={"p1": WEEKNIGHT},
                   devices=full_devices(), sleepers={"left": Sleeper(watch=True, restless=0.15)}, room_c=26.0)
    lab.p1.settings.autopilot.learn_skip = True
    await lab.evenings(week, start="19:00", dt=120)
    acted = [x for x in lab.decisions("p1") if x["status"] in ("suggested", "applied")]
    assert acted and all(x["direction"] == "cooler" for x in acted)
    assert all(x["phase"] in lab.p1.settings.autopilot.phases for x in acted)
    per_night: dict[str, int] = {}
    for x in acted:
        per_night[evening_of(x["ts"])] = per_night.get(evening_of(x["ts"]), 0) + 1
    assert max(per_night.values()) <= lab.p1.settings.autopilot.max_actions
    assert any(r["signal"] == "room" for x in acted for r in x["reasons"])


def _w(**kw):
    now = 50_000.0
    rows = [{"ts": now - 3 * 3600 + i * 15, "status": 0, "hr": 56, "target": 17.0, "measured": 17.2, "occupied": 1, "phase": "deep"}
            for i in range(int(3 * 3600 / 15))]
    skin = [(now - 3 * 3600 + 1500 + i * 300, 34.0) for i in range(25)] + [(now - 500 + i * 60, 34.7) for i in range(8)]
    base = dict(now=now, rows=rows, in_bed=now - 3 * 3600, state="rhythm", phase="deep", flash_active=False,
                events=[{"kind": "feel", "ts": now - 60, "feel": "too_cold"}], skin=skin, persona="tide", wake=now + 5 * 3600)
    base.update(kw)
    return Window(**base)


def test_cold_tap_beats_a_warm_watch_after_caffeine():
    """After an evening coffee the watch reads warm, but the person says too cold: never cooler, warmer it is."""
    d = evaluate(_w(), AutopilotPolicy(), 17.0, [])
    assert d.direction == "warmer" and d.status in ("suggested", "applied")
    assert any("disagree" in r.detail for r in d.reasons if r.signal == "feedback")


def test_cold_tap_in_a_warm_humid_room_does_not_cool():
    d = evaluate(_w(room=[(50_000.0 - 60 * i, 26.5) for i in range(20)], humidity=[(50_000.0 - 60 * i, 70) for i in range(20)]),
                 AutopilotPolicy(), 17.0, [])
    assert d.direction in ("warmer", "none")


async def test_one_caffeine_night_doesnt_make_a_caffeine_rule(tmp_path):
    week = days("2026-11-01", 5)
    lab = ComboLab(tmp_path, "2026-11-01T16:00", plans={}, defaults={"p1": WEEKNIGHT}, room_c=21.0)
    lab.store.samples_many([(at("2026-11-02", "17:00"), "wear:p1:caffeine_mg:Phone", 200.0)])
    await lab.evenings(week, start="21:30", dt=240)
    assert "guide-caffeine" not in ids(tuning_guide(lab.c, lab.p1, lab.clock.now_ts()))


# ───────────────────────── 9. context reads ─────────────────────────
async def test_two_phones_one_says_home_is_home(tmp_path):
    """Two phones for one person (work + personal); the work phone is in the office: home wins."""
    night = "2026-11-03"
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={}, defaults={"p1": WEEKNIGHT},
                   devices=[dev("phoneA", "home", "phone"), dev("phoneB", "home", "phone")],
                   feeders={"phoneA": lambda L, t: 1.0, "phoneB": lambda L, t: 0.0})
    await lab.evenings([night], start="20:00", end="22:00", dt=60, morning=False)
    c = person_context(lab.sources, lab.store, lab.p1, lab.clock.now_ts())
    assert c.home is True and "Wi-Fi" in c.why


async def test_other_persons_devices_dont_leak(tmp_path):
    night = "2026-11-03"
    lab = ComboLab(tmp_path, "2026-11-03T16:00", plans={"p2": {night: {"in": "22:30", "out": "06:30", "away": [("18:00", "23:59")]}}},
                   defaults={"p1": WEEKNIGHT, "p2": {"in": "22:30", "out": "06:30"}},
                   devices=[dev("phone2", "home", "phone", "p2"), dev("cal2", "calendar", "calendar", "p2")])
    await lab.evenings([night], start="18:00", end="22:00", dt=60, morning=False)
    assert lab.c.context["p1"]["home"] is None and lab.c.context["p1"]["busy_until"] is None
    assert lab.c.context["p2"]["home"] is False
