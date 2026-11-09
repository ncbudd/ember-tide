"""End-to-end scenarios: a simulated bed, the real controller and engine, time moving in steps.
Each test is one situation a real household runs into."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import embertide.engine.controller as ctlmod
from embertide.clock import FixedClock
from embertide.drivers.sim import SimBed
from embertide.engine.controller import Controller
from embertide.seed import default_household
from embertide.store import Store
from embertide.units import f_to_c

TZ = ZoneInfo("America/New_York")


def ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


class Night:
    """A controllable night. Presence is manual unless `scripted=True`."""

    def __init__(self, tmp_path, start: str, scripted: bool = False, echo: bool = True, blips: bool = False, store=None, household=None):
        ctlmod.MIN_CMD_GAP_S = 0
        self.clock = FixedClock(ts(start), "America/New_York")
        self.bed = SimBed(self.clock)
        self.bed.scripted, self.bed.echo, self.bed.blips = scripted, echo, blips
        self.store = store or Store(tmp_path / "s.db")
        self.hh = household or default_household()
        self.c = Controller(self.hh, self.bed, self.store, self.clock)
        self.p1, self.p2 = self.hh.people

    def present(self, zone: str, v: bool) -> None:
        self.bed.scripted = False
        self.bed.set_presence(zone, v)

    async def step(self, n: int = 1, dt: float = 30) -> None:
        for _ in range(n):
            self.bed._tick()
            await self.c.tick()
            for t in list(self.c._inflight.values()):
                await t
            self.clock.advance(dt)

    async def until(self, when: str, dt: float = 30) -> None:
        while self.clock.now_ts() < ts(when):
            await self.step(1, dt)

    def e(self, p):
        return self.c.engine_for(p.id)

    def side(self, zone: str):
        return self.bed.z[zone]

    def events(self, kind: str) -> list[dict]:
        return [x for x in self.store.events(limit=5000) if x["kind"] == kind]


# 1 ─ the normal night
async def test_normal_weeknight_scripted(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00", scripted=True)
    await n.until("2026-11-05T21:35")
    assert n.side("left").on and n.side("left").setpoint == pytest.approx(f_to_c(68), abs=0.1)   # pre-cool
    await n.until("2026-11-06T00:30")
    assert n.e(n.p1).s.started and n.e(n.p1).s.phase_id == "deep"
    assert n.side("left").setpoint == pytest.approx(f_to_c(63), abs=0.1)
    await n.until("2026-11-06T06:20")
    assert n.e(n.p1).s.phase_id == "wake"
    await n.until("2026-11-06T07:00")
    assert not n.side("left").on and not n.side("right").on                                    # off after wake


# 2 ─ away all evening, home late
async def test_away_then_home_late(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    n.c.home[n.p1.id] = False
    await n.until("2026-11-05T22:00")
    assert n.e(n.p1).s.state == "away" and not n.side("left").on                               # no pre-cool while away
    assert n.side("right").on                                                                  # partner unaffected
    n.c.home[n.p1.id] = True
    await n.step(2)
    assert n.e(n.p1).s.state == "waiting" and n.side("left").on
    n.present("left", True)
    await n.step(10)
    assert n.e(n.p1).s.started and n.e(n.p1).s.phase_id == "fall_asleep"


# 3 ─ arriving after "keep pre-cooling until"
async def test_late_arrival_still_starts(tmp_path):
    n = Night(tmp_path, "2026-11-06T01:00")
    await n.until("2026-11-06T02:10")
    assert n.e(n.p1).s.state == "missed" and not n.side("left").on                              # stopped pre-cooling an empty side
    n.present("left", True)
    await n.until("2026-11-06T02:20")
    assert n.e(n.p1).s.started, "a 2 AM arrival must still get a working bed"
    assert n.side("left").on


# 4 ─ arriving an hour before wake does not start a full night
async def test_too_close_to_wake_does_not_start(tmp_path):
    n = Night(tmp_path, "2026-11-06T05:40")
    n.present("left", True)
    await n.until("2026-11-06T05:55")
    assert not n.e(n.p1).s.started


# 5 ─ evening nap before the earliest start
async def test_evening_nap_does_not_start_then_counts_at_earliest(tmp_path):
    n = Night(tmp_path, "2026-11-05T19:30")
    n.present("left", True)
    await n.until("2026-11-05T20:30")
    assert not n.e(n.p1).s.started and not n.side("left").on
    await n.until("2026-11-05T21:05")
    assert n.e(n.p1).s.started and n.e(n.p1).s.in_bed == ts("2026-11-05T21:00")


# 6 ─ short bathroom trip vs. long absence
async def test_out_of_bed_short_and_long(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T01:00")
    sp = n.side("left").setpoint
    n.present("left", False)
    await n.until("2026-11-06T01:10")
    assert n.e(n.p1).s.state == "out_of_bed_hold" and n.side("left").on and n.side("left").setpoint == pytest.approx(sp, abs=0.1)
    n.present("left", True)
    await n.step(4)
    assert n.e(n.p1).s.state == "rhythm"
    n.present("left", False)
    await n.until("2026-11-06T01:50")
    assert n.e(n.p1).s.state == "out_of_bed" and not n.side("left").on
    n.present("left", True)
    await n.step(4)
    assert n.e(n.p1).s.state == "rhythm" and n.side("left").on


# 7 ─ hot flash, extend to the cap, stop during the burst
async def test_hot_flash_lifecycle(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T01:00")
    base = n.side("left").setpoint
    e = n.e(n.p1)
    e.flash("hot", n.clock.now_ts())
    await n.step(2)
    assert n.side("left").setpoint == pytest.approx(12.8, abs=0.1)
    for _ in range(30):
        e.extend_flash(n.clock.now_ts())
    assert e.s.flash.end - e.s.flash.start <= 60 * 60 + 1
    e.stop_flash(n.clock.now_ts())
    await n.step(4)                                  # 2 min into a 5 min ease-back
    mid = n.side("left").setpoint
    assert 12.8 < mid < base
    await n.step(8)
    assert e.s.flash is None and n.side("left").setpoint == pytest.approx(base, abs=0.15)
    assert len(n.events("hot_flash")) == 1 and n.events("flash_stopped")


# 8 ─ flash while the side is off for the night
async def test_flash_while_side_off(tmp_path):
    n = Night(tmp_path, "2026-11-05T23:00")
    n.present("left", True)
    await n.step(10)
    e = n.e(n.p1)
    e.tonight(n.clock.now_ts(), "skip")
    await n.step(2)
    assert not n.side("left").on
    e.flash("hot", n.clock.now_ts(), minutes=5)
    await n.step(2)
    assert n.side("left").on and n.side("left").setpoint == pytest.approx(12.8, abs=0.1)
    await n.step(12)                                 # 6 min later: burst over, back to off (no ease to nowhere)
    assert not n.side("left").on


# 9 ─ partner's actions never touch my side
async def test_partner_independence(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:30")
    n.present("left", True)
    n.present("right", True)
    await n.until("2026-11-06T00:30")
    mine = n.side("left").setpoint
    n.e(n.p2).flash("hot", n.clock.now_ts())
    n.e(n.p2).adjust(n.clock.now_ts(), delta_c=-2)
    n.e(n.p2).tonight(n.clock.now_ts(), "pause", 30)
    await n.step(4)
    assert n.side("left").setpoint == pytest.approx(mine, abs=0.05) and n.side("left").on


# 10 ─ pause auto-resumes
async def test_pause_auto_resumes(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-05T23:30")
    n.e(n.p1).tonight(n.clock.now_ts(), "pause", 60)
    await n.step(2)
    assert n.e(n.p1).s.state == "paused" and not n.side("left").on
    await n.until("2026-11-06T00:32")
    assert n.e(n.p1).s.state == "rhythm" and n.side("left").on


# 11 ─ start now while away (e.g. came home and the phone's location lagged)
async def test_start_now_overrides_away(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:00")
    n.c.home[n.p1.id] = False
    await n.step(2)
    assert n.e(n.p1).s.state == "away"
    n.e(n.p1).tonight(n.clock.now_ts(), "start_now")
    await n.step(2)
    assert n.e(n.p1).s.started and n.side("left").on


# 12 ─ overrides: until next phase vs rest of night
async def test_override_scopes(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-05T22:40")
    e = n.e(n.p1)
    e.adjust(n.clock.now_ts(), delta_c=1, until="next_phase")
    await n.until("2026-11-05T23:40")                # Deep has begun
    assert e.s.override is None
    e.adjust(n.clock.now_ts(), delta_c=-1, until="night")
    await n.until("2026-11-06T03:30")                # Late night
    assert e.s.override is not None
    assert n.side("left").setpoint == pytest.approx(f_to_c(65) - 1, abs=0.15)


# 13 ─ clamps protect against silly values
async def test_clamps(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-05T22:40")
    e = n.e(n.p1)
    e.adjust(n.clock.now_ts(), temp_c=5)                        # below everything
    await n.step(2)
    assert n.side("left").setpoint >= n.hh.user_min_c - 0.01
    e.adjust(n.clock.now_ts(), temp_c=60)
    await n.step(2)
    assert n.side("left").setpoint <= n.hh.user_max_c + 0.01


# 14 ─ a frozen sensor never starts a night by itself
async def test_frozen_sensor_is_unknown(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20", echo=False)
    n.bed.z["left"].frozen = True
    n.present("left", True)
    await n.step(20)
    assert n.c.presence.present("left") is None
    assert not n.e(n.p1).s.started


# 15 ─ restart in the middle of a flash
async def test_restart_mid_flash_keeps_the_end_time(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T01:00")
    n.e(n.p1).flash("hot", n.clock.now_ts(), minutes=10)
    await n.step(2)
    n.c._persist()
    end = n.e(n.p1).s.flash.end
    m = Night(tmp_path, "2026-11-06T01:02", store=n.store, household=n.hh)    # "restart": new controller, same database
    m.bed.z = n.bed.z
    assert m.e(m.p1).s.flash and m.e(m.p1).s.flash.end == end
    m.clock.jump_to(n.clock.now_ts())
    await m.step(2)
    assert m.side("left").setpoint == pytest.approx(12.8, abs=0.1)


# 16 ─ weekend rhythm on Friday night, weeknight rhythm on Sunday night
async def test_weekend_and_weeknight_selection(tmp_path):
    n = Night(tmp_path, "2026-11-06T22:00")           # Friday
    await n.step(2)
    assert n.e(n.p1).night.rhythm_id == "weekends"
    n.clock.jump_to(ts("2026-11-08T22:00"))           # Sunday
    await n.step(2)
    assert n.e(n.p1).night.rhythm_id == "weeknights"


# 17 ─ daylight saving ends during the night (US: Nov 1, 2026 at 2 AM)
async def test_dst_fall_back_night(tmp_path):
    n = Night(tmp_path, "2026-10-31T22:00")           # Saturday night, weekend rhythm, wake 08:00
    n.present("left", True)
    await n.step(10)
    night = n.e(n.p1).night
    wake_local = datetime.fromtimestamp(night.wake, TZ)
    assert (wake_local.hour, wake_local.minute) == (8, 0)
    late = next(p for p in night.phases if p.id == "late")
    assert datetime.fromtimestamp(late.start, TZ).hour == 3
    assert night.wake - night.bedtime == 10 * 3600    # 23:00 → 08:00 is 10 real hours on this night


# 18 ─ swapping sides mid-night moves the rhythm and keeps history with the person
async def test_swap_sides_mid_night(tmp_path):
    from embertide.engine.summary import summarize
    from datetime import date
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    n.present("right", True)
    await n.until("2026-11-06T00:30")
    left_before, right_before = n.side("left").setpoint, n.side("right").setpoint
    n.p1.zone, n.p2.zone = "right", "left"            # what POST /api/household/swap-sides does
    n.c.reload_household(n.hh)
    await n.step(3)
    assert n.side("right").setpoint == pytest.approx(left_before, abs=0.15)
    assert n.side("left").setpoint == pytest.approx(right_before, abs=0.15)
    rows = n.store.history(n.p1.zone, ts("2026-11-05T22:00"), n.clock.now_ts(), person=n.p1.id)
    assert {round(r["target"] or 0, 1) for r in rows[-3:]} == {round(left_before, 1)}
    s = summarize(n.store, n.p1, date(2026, 11, 5), TZ, n.clock.now_ts())
    assert s["has_data"]


# 19 ─ new install, nothing recorded: every read works
async def test_empty_install_reads(tmp_path):
    from datetime import date
    from embertide.engine.insights import insights
    from embertide.engine.stages import collect
    from embertide.engine.summary import recent, summarize
    n = Night(tmp_path, "2026-11-05T12:00")
    await n.step(1)
    assert summarize(n.store, n.p1, date(2026, 11, 5), TZ, n.clock.now_ts())["has_data"] is False
    assert recent(n.store, n.p1, TZ, n.clock.now_ts(), 7)
    assert collect(n.store, n.p1, 0, n.clock.now_ts()) == []
    assert insights(n.p1, [], TZ, n.clock.now_ts())[0]["id"] == "need_data"


# 20 ─ both sides echo: one sleeper, empty partner side never starts
async def test_one_sleeper_echo_does_not_start_partner(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20", echo=True, blips=True)
    n.present("left", True)
    await n.until("2026-11-06T00:00")
    assert n.e(n.p1).s.started
    assert not n.e(n.p2).s.started, "the echo on the empty side must not start the partner's night"


# 21 ─ + while paused resumes the rhythm and applies the step; pausing keeps an earlier change
async def test_adjust_while_paused_resumes_and_pause_keeps_override(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-05T22:40")
    e = n.e(n.p1)
    e.adjust(n.clock.now_ts(), delta_c=-0.5)
    e.tonight(n.clock.now_ts(), "pause", 60)
    await n.step(2)
    assert e.s.state == "paused" and e.s.override is not None, "pausing must not throw away the change"
    e.adjust(n.clock.now_ts(), delta_c=-0.5)
    await n.step(2)
    assert e.s.state == "rhythm" and e.s.override.delta_c == pytest.approx(-1.0)
    assert any(x["kind"] == "tonight_resume" for x in n.store.events(limit=50))


# 22 ─ + while off for the night turns the side back on
async def test_adjust_while_skipped_turns_back_on(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-05T22:40")
    e = n.e(n.p1)
    e.tonight(n.clock.now_ts(), "skip")
    await n.step(2)
    assert not n.side("left").on
    e.adjust(n.clock.now_ts(), delta_c=0.5)
    await n.step(2)
    assert n.side("left").on and not e.s.skip


# 23 ─ the chart and the plan list show tonight's change, not just the rhythm (found on the phone)
async def test_chart_shows_the_change(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T00:10")                 # Deep
    e = n.e(n.p1)
    first = n.clock.now_ts()
    for _ in range(3):                                 # three presses, a minute apart
        e.adjust(n.clock.now_ts(), delta_c=1, until="next_phase")
        await n.step(2)
    assert e.s.override.set_ts == first                # the change is drawn from the first press
    await n.step(1)
    snap = n.c.snapshot()["people"][0]
    deep = next(p for p in snap["phases"] if p["id"] == "deep")
    late = next(p for p in snap["phases"] if p["id"] == "late")
    assert deep["adjusted_c"] == pytest.approx(deep["temp_c"] + 3, abs=0.01)
    assert late["adjusted_c"] is None                  # "until next phase" stops at Late night
    curve = dict((t, v) for t, v in snap["curve"])
    now = n.clock.now_ts()
    at_now = min(curve, key=lambda t: abs(t - now) if t >= now else 1e12)
    assert curve[at_now] == pytest.approx(snap["night"]["target_c"], abs=0.3)
    after = min(t for t in curve if t >= late["start"] + 3600)
    assert curve[after] == pytest.approx(late["temp_c"], abs=0.05)
    assert snap["rhythm_curve"]                        # the unchanged rhythm is there for comparison


# 24 ─ a flash shows on the chart while it runs
async def test_chart_shows_a_flash(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T00:10")
    e = n.e(n.p1)
    e.flash("hot", n.clock.now_ts())
    await n.step(1)
    snap = n.c.snapshot()["people"][0]
    f = snap["night"]["flash"]
    mid = [v for t, v in snap["curve"] if f["start"] <= t < f["end"]]
    assert mid and all(v == pytest.approx(f["target_c"]) for v in mid)


# 25 ─ a change made while settling in carries into the night (found on the phone)
async def test_change_while_settling_in_carries_over(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    e = n.e(n.p1)
    await n.until("2026-11-05T22:30")
    n.present("left", True)
    await n.step(2)                                    # in bed, not yet confirmed
    assert not e.s.started
    e.adjust(n.clock.now_ts(), delta_c=1, until="next_phase")
    await n.until("2026-11-05T22:40")                  # confirmed: the night started
    assert e.s.started and e.s.override is not None
    assert e.s.override.phase_id == e.s.phase_id
    assert e.s.target_c == pytest.approx(e.s.base_c + 1, abs=0.05)


# 26 ─ "off tonight" pressed in the morning means tonight, not the night that just ended (found while testing buttons)
async def test_off_tonight_pressed_in_the_morning(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20", scripted=True)
    await n.until("2026-11-06T10:00")                  # last night is over
    e = n.e(n.p1)
    e.tonight(n.clock.now_ts(), "skip")
    await n.until("2026-11-06T22:30")                  # noon rolled over to the new night
    assert e.s.skip and e.s.state == "skipped"
    assert not n.side("left").on
    await n.until("2026-11-07T22:30")                  # and only that one night
    assert not e.s.skip


# 27 ─ "off" wins over a temperature someone set (found from Home Assistant: set 65°, then turn off → stayed on)
async def test_off_wins_over_a_set_temperature(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-05T23:00")
    e = n.e(n.p1)
    e.adjust(n.clock.now_ts(), temp_c=f_to_c(65), until="night")
    await n.step(2)
    e.tonight(n.clock.now_ts(), "skip")
    await n.step(2)
    assert e.s.target_c is None and not n.side("left").on
    e.tonight(n.clock.now_ts(), "pause", 30)
    e.tonight(n.clock.now_ts(), "resume")
    await n.step(2)
    assert n.side("left").on                           # back on, still at the temperature that was set
    assert e.s.target_c == pytest.approx(f_to_c(65), abs=0.05)


# 28 ─ a side whose sensor never reports still gets its night, on the schedule (found on the real bed)
async def test_silent_sensor_falls_back_to_schedule(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    e = n.e(n.p1)
    n.c.fuse = lambda zone: None if zone == "left" else False       # left side: no presence data at all
    await n.until("2026-11-05T22:20")                  # usual bedtime 22:15, plus 10 minutes of not knowing
    assert e.s.started and e.s.state == "rhythm"
    assert any(x.get("reason") for x in n.events("rhythm_started"))
    assert not n.e(n.p2).s.started                     # the partner's sensor works and says empty: no change there


async def test_each_side_waits_for_its_own_sleeper(tmp_path):
    """Pre-condition both sides, start each rhythm only when that side's own sleeper gets in. The partner's echo on the
    empty pad doesn't start it, and a late arrival starts from when they got in, not from bedtime."""
    n = Night(tmp_path, "2026-11-05T21:30", echo=True)
    n.bed.scripted = False
    await n.until("2026-11-05T21:55")
    assert n.e(n.p1).s.state == "waiting" and n.e(n.p2).s.state == "waiting"
    assert n.e(n.p1).s.target_c is not None and n.e(n.p2).s.target_c is not None       # both pre-conditioning
    n.present("left", True)
    await n.until("2026-11-06T00:20")
    assert n.e(n.p1).s.started
    assert not n.e(n.p2).s.started and n.e(n.p2).s.state == "waiting" and n.e(n.p2).s.target_c is not None
    n.present("right", True)
    await n.until("2026-11-06T00:45")
    e2 = n.e(n.p2).s
    assert e2.started and e2.in_bed >= ts("2026-11-06T00:15")


async def test_the_warm_wake_follows_the_phones_alarm(tmp_path):
    """2026-11-09: alarm at 8:30 but the rhythm woke at 9:30, then a new alarm for 10:30 to sleep in. The wake follows
    the alarm both ways (within 4 h earlier / 3 h later); a wake set by hand wins; a far-off alarm is ignored."""
    n = Night(tmp_path, "2026-11-05T22:00")
    n.present("left", True)
    await n.until("2026-11-06T03:00")
    e = n.e(n.p1)
    planned = e.night.wake
    early = planned - 3600
    assert e.follow_alarm(n.clock.now_ts(), early)
    await n.step(2)
    assert abs(e.night.wake - early) < 1
    assert not e.follow_alarm(n.clock.now_ts(), early)                 # same alarm: nothing to do
    later = planned + 3600                                              # snoozed to a later alarm
    assert e.follow_alarm(n.clock.now_ts(), later)
    await n.step(2)
    assert abs(e.night.wake - later) < 1
    assert e.follow_alarm(n.clock.now_ts(), early)
    assert not e.follow_alarm(n.clock.now_ts(), early - 5 * 3600)       # a 3 AM reminder alarm: the morning one still stands
    assert abs(e.s.wake_override - early) < 1
    # the alarm is turned off before it rings (the next one is tomorrow's): back to the planned wake
    assert e.follow_alarm(n.clock.now_ts(), planned + 24 * 3600)
    await n.step(2)
    assert abs(e.night.wake - planned) < 1 and e.s.wake_override is None
    assert not e.follow_alarm(n.clock.now_ts(), planned + 8 * 3600)    # an afternoon alarm isn't tonight's wake
    e.s.wake_by_hand = True
    assert not e.follow_alarm(n.clock.now_ts(), early + 600)           # set by hand in the app: the alarm doesn't move it
    n.p1.settings.wake_with_alarm = False
    e.s.wake_by_hand = False
    assert not e.follow_alarm(n.clock.now_ts(), early + 900)           # switched off


def test_a_short_sit_down_hours_before_isnt_getting_into_bed():
    """2026-11-09: 30 s on her side at 11:21 PM (sitting on the edge / the dog), then in bed from 12:55."""
    from datetime import date
    from zoneinfo import ZoneInfo
    from embertide.engine.summary import summarize
    from embertide.seed import default_household
    from embertide.store import Store
    import tempfile, pathlib
    st = Store(pathlib.Path(tempfile.mkdtemp()) / "s.db")
    p = default_household().people[0]
    a = ts("2026-11-05T23:21")
    rows = [(a, 1), (a + 15, 1), (a + 30, 0)]
    b = ts("2026-11-06T00:55")
    rows += [(b + i * 15, 1) for i in range(4 * 60 * 6)]
    st.telemetry([(t, p.zone, 1, 20.0, 20.0, o, 62 if o else None, 14 if o else None, 0 if o else 5, 20.0, "deep", o, p.id)
                  for t, o in rows])
    s = summarize(st, p, date(2026, 11, 5), ZoneInfo("America/New_York"), ts("2026-11-06T12:00"), raw=True)
    assert abs(s["in_bed_at"] - b) < 30


async def test_a_temperature_set_in_the_night_doesnt_keep_an_empty_side_running(tmp_path):
    """Review 2026-11-09: a dial / HA / app temperature (temp_c, not by hand) set at midnight kept the side at that
    temperature through out of bed and the morning, with nobody in it, until noon."""
    n = Night(tmp_path, "2026-11-05T21:00")
    n.present("left", True)
    await n.until("2026-11-06T00:00")
    e = n.e(n.p1)
    assert e.s.started
    e.adjust(n.clock.now_ts(), temp_c=30.0, until="night", source="ha")
    await n.step(2)
    assert e.s.target_c == 30.0                                   # applies while the night runs
    n.present("left", False)
    await n.until("2026-11-06T02:30")
    assert e.s.state in ("out_of_bed", "done") and e.s.target_c is None
    await n.until("2026-11-06T09:00")
    assert e.s.target_c is None
