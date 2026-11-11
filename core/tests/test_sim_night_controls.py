"""Whole-night simulations of the Tonight buttons and the phone / watch / calendar signals, pressed at awkward moments.

Alex is on the left (p1). Work-night rhythm: bed 22:30, wake 07:00, pad warming from 21:45."""

import pytest

from embertide.sources import SourceConfig
from embertide.units import f_to_c
from simnight import Plan, Sim, T, hm, ts

F = f_to_c


def tgt(r, p, when):
    return r.at(p, when)[4]


def now(r):
    return r.clock.now_ts()


# ───────────────────────── flashes ─────────────────────────
async def test_a_hot_flash_while_the_side_is_still_warming_up(tmp_path):
    """22:00, nobody in bed yet: the flash cools the pad, eases back to the warm-up temperature, and does not start the night."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("22:00", lambda r_: r_.e(r_.p1).flash("hot", now(r_), minutes=10))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.at(p, "2026-11-05T22:05")[4] == pytest.approx(r.hh.people[0].settings.hot_flash.target_c, abs=0.2)
    assert r.at(p, "2026-11-05T22:30")[0] == "waiting" and r.at(p, "2026-11-05T22:30")[4] == pytest.approx(F(68), abs=0.2)
    assert r.started_count(p) == 1 and hm(r.events("rhythm_started", "p1")[0]["ts"]) >= "22:40"


async def test_a_flash_in_the_last_half_hour_and_across_the_end_of_the_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "07:30")
    plan.at("06:45", lambda r_: r_.e(r_.p1).flash("cold", now(r_), minutes=30))
    plan.at("07:10", lambda r_: r_.e(r_.p1).flash("hot", now(r_), minutes=60))
    await r.run("2026-11-06T09:30", script=plan)
    p = r.p1
    assert r.at(p, "2026-11-06T06:55")[4] == pytest.approx(r.hh.people[0].settings.cold_flash.target_c, abs=0.3)
    assert r.at(p, "2026-11-06T07:30")[3] is True                           # the flash outlives the night: a flash is a flash
    assert not r.bed.z["left"].on and r.e(p).s.flash is None                # and it ends cleanly
    assert r.timeline(p)[-1][1] == "done"


async def test_a_flash_while_paused_then_the_pause_ends(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: r_.e(r_.p1).tonight(now(r_), "pause", 60))
    plan.at("01:10", lambda r_: r_.e(r_.p1).flash("hot", now(r_), minutes=10))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.at(p, "2026-11-06T01:05")[3] is False
    assert r.at(p, "2026-11-06T01:15")[3] is True
    assert r.at(p, "2026-11-06T01:45")[3] is False and r.at(p, "2026-11-06T01:45")[0] == "paused"
    assert r.at(p, "2026-11-06T02:05")[0] == "rhythm" and r.at(p, "2026-11-06T02:05")[3] is True


async def test_two_flashes_pressed_a_minute_apart_from_two_devices(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: r_.e(r_.p1).flash("hot", now(r_), source="app"))
    plan.at("01:01", lambda r_: r_.e(r_.p1).flash("hot", now(r_), source="dial"))
    await r.run("2026-11-06T04:00", script=plan)
    p = r.p1
    assert r.e(p).s.flash is None
    assert r.at(p, "2026-11-06T03:30")[4] == pytest.approx(F(65), abs=0.2)  # the plain plan again, no leftover
    assert len(r.events("hot_flash", "p1")) == 2


# ───────────────────────── warmer / cooler, pause, off ─────────────────────────
async def test_cooler_pressed_twice_quickly_from_two_devices_adds_up(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: r_.e(r_.p1).adjust(now(r_), delta_c=-0.56, until="night", source="app"))
    plan.at("01:00", lambda r_: r_.e(r_.p1).adjust(now(r_), delta_c=-0.56, until="night", source="dial"))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert tgt(r, p, "2026-11-06T02:00") == pytest.approx(F(63) - 1.12, abs=0.1)
    assert tgt(r, p, "2026-11-06T05:00") == pytest.approx(F(65) - 1.12, abs=0.1)       # rest of the night, across the 3 AM step
    assert r.at(r.p1, "2026-11-06T06:55")[3] is True


async def test_a_change_for_this_phase_ends_with_the_phase(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: r_.e(r_.p1).adjust(now(r_), delta_c=1.0, until="next_phase"))
    await r.run("2026-11-06T04:00", script=plan)
    p = r.p1
    assert tgt(r, p, "2026-11-06T02:00") == pytest.approx(F(63) + 1.0, abs=0.1)
    assert tgt(r, p, "2026-11-06T03:40") == pytest.approx(F(65), abs=0.1)


async def test_warmer_in_the_last_half_hour_before_wake(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "07:10")
    plan.at("06:40", lambda r_: r_.e(r_.p1).adjust(now(r_), delta_c=1.0, until="night"))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert tgt(r, p, "2026-11-06T07:05") == pytest.approx(F(74) + 1.0, abs=0.1)
    assert not r.bed.z["left"].on


async def test_pause_for_an_hour_pressed_twice(tmp_path):
    """A second 'pause' replaces the first (it does not stack): 60 min at 01:00, 15 min at 01:20, back at 01:35."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: r_.e(r_.p1).tonight(now(r_), "pause", 60))
    plan.at("01:20", lambda r_: r_.e(r_.p1).tonight(now(r_), "pause", 15))
    await r.run("2026-11-06T02:30", script=plan)
    p = r.p1
    assert r.at(p, "2026-11-06T01:30")[0] == "paused" and r.at(p, "2026-11-06T01:30")[3] is False
    assert r.at(p, "2026-11-06T01:40")[0] == "rhythm" and r.at(p, "2026-11-06T01:40")[3] is True
    assert r.started_count(p) == 1


async def test_a_pause_that_would_end_after_the_night_does_nothing_odd(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("06:30", lambda r_: r_.e(r_.p1).tonight(now(r_), "pause", 180))
    await r.run("2026-11-06T10:30", script=plan)
    assert not r.bed.z["left"].on and r.e(r.p1).s.state in ("done", "day")


async def test_off_tonight_before_bed_and_the_next_night_is_normal(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("21:30", lambda r_: r_.e(r_.p1).tonight(now(r_), "skip"))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.states(p, "2026-11-05T22:00", "2026-11-06T07:00") == {"skipped"}
    assert all(not row[p.id][3] for t, row in r.rec if ts("2026-11-05T21:50") <= t < ts("2026-11-06T07:00"))
    r.clock.jump_to(ts("2026-11-06T21:00"))
    plan2 = Plan("2026-11-06").sleep("left", "23:40", "08:50")
    await r.run("2026-11-07T09:30", script=plan2)
    assert r.at(p, "2026-11-07T02:00")[0] == "rhythm" and r.at(p, "2026-11-07T02:00")[3] is True


async def test_off_tonight_pressed_the_morning_after_means_the_coming_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("08:00", lambda r_: r_.e(r_.p1).tonight(now(r_), "skip"))
    await r.run("2026-11-06T09:00", script=plan)
    assert r.at(r.p1, "2026-11-06T03:00")[3] is True                            # last night was not cut short
    r.clock.jump_to(ts("2026-11-06T21:00"))
    plan2 = Plan("2026-11-06").sleep("left", "23:40", "08:50")
    await r.run("2026-11-07T09:30", script=plan2)
    assert r.states(r.p1, "2026-11-06T22:00", "2026-11-07T08:00") == {"skipped"}
    assert all(not row[r.p1.id][3] for t, row in r.rec if ts("2026-11-06T22:00") <= t < ts("2026-11-07T08:00"))


async def test_off_tonight_pressed_in_the_night_and_turned_on_again_by_a_tap(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: r_.e(r_.p1).tonight(now(r_), "skip"))
    plan.at("03:00", lambda r_: r_.e(r_.p1).adjust(now(r_), delta_c=-0.56, until="next_phase"))   # + / - means 'I want it back'
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.at(p, "2026-11-06T02:00")[0] == "skipped" and r.at(p, "2026-11-06T02:00")[3] is False
    assert r.at(p, "2026-11-06T03:30")[0] == "rhythm" and r.at(p, "2026-11-06T03:30")[3] is True
    assert r.started_count(p) == 1


# ───────────────────────── turning it on by hand ─────────────────────────
async def test_two_afternoon_naps_with_a_timer(tmp_path):
    r = Sim(tmp_path, "2026-11-05T12:30")
    plan = Plan().sleep("left", "13:10", "14:40").sleep("left", "16:10", "16:50").sleep("left", "22:40", "+06:50")
    plan.at("13:05", lambda r_: r_.e(r_.p1).adjust(now(r_), temp_c=F(70), minutes=90))
    plan.at("16:05", lambda r_: r_.e(r_.p1).adjust(now(r_), temp_c=F(70), minutes=45))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.at(p, "2026-11-05T13:30")[3] is True and r.at(p, "2026-11-05T13:30")[4] == pytest.approx(F(70), abs=0.1)
    assert r.at(p, "2026-11-05T15:00")[3] is False
    assert r.at(p, "2026-11-05T16:30")[3] is True and r.at(p, "2026-11-05T17:00")[3] is False
    assert r.started_count(p) == 1 and hm(r.events("rhythm_started", "p1")[0]["ts"]) >= "22:40"
    assert len(r.events("adjust_ended", "p1")) == 2
    s = r.summary(p, "2026-11-05")
    assert hm(s["in_bed_at"]) >= "22:40"                                         # the naps are not the night
    assert "left_on" not in r.kinds("p1")


async def test_turned_on_by_hand_and_forgotten_asks_once(tmp_path):
    r = Sim(tmp_path, "2026-11-05T12:30")
    plan = Plan()
    plan.at("13:00", lambda r_: r_.e(r_.p1).adjust(now(r_), temp_c=F(70), minutes=240))
    await r.run("2026-11-05T17:30", script=plan)
    assert r.kinds("p1").count("left_on") == 1
    assert r.at(r.p1, "2026-11-05T16:00")[3] is True and r.at(r.p1, "2026-11-05T17:10")[3] is False


async def test_turned_on_by_hand_at_ten_then_getting_in_hands_over_to_the_rhythm(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("22:00", lambda r_: r_.e(r_.p1).adjust(now(r_), temp_c=F(72), minutes=240))
    await r.run("2026-11-06T01:00", script=plan)
    p = r.p1
    assert r.at(p, "2026-11-05T22:20")[4] == pytest.approx(F(72), abs=0.15)      # by hand, before the rhythm warms
    assert r.at(p, "2026-11-06T00:30")[2] == "deep" and r.e(p).s.override is None
    assert tgt(r, p, "2026-11-06T00:30") == pytest.approx(F(63), abs=0.15)


async def test_not_sleeping_here_tonight_but_they_get_in_anyway(tmp_path):
    """'Not sleeping here' (home set to no) and then lying down on the side: the bed believes the person in it."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.c.home[r.p1.id] = False
    plan = Plan().sleep("left", "22:40", "06:50")
    await r.run("2026-11-06T07:30", script=plan)
    assert r.states(r.p1, "2026-11-05T21:50", "2026-11-05T22:30") == {"away"} and not r.at(r.p1, "2026-11-05T22:20")[3]
    assert r.started_count(r.p1) == 1 and r.at(r.p1, "2026-11-06T01:00")[3] is True


# ───────────────────────── phone, watch, calendar, lights ─────────────────────────
async def test_phone_home_wifi_charging_and_bedtime_mode_through_the_evening(tmp_path):
    """The phone leaves for dinner (away), comes home on Wi-Fi, charges and goes into bedtime mode: the pad is never
    warmed for an away person, is warm when they are home, and the night starts when they lie down."""
    r = Sim(tmp_path, "2026-11-05T20:30")
    plan = Plan().sleep("left", "23:00", "06:50")
    plan.at("20:30", lambda r_: r_.phone(r_.p1, home=False, distance_km=14.0))
    plan.at("22:20", lambda r_: r_.phone(r_.p1, home=True, wifi_home=True, distance_km=0.0))
    plan.at("22:50", lambda r_: r_.phone(r_.p1, charging=True, bedtime_mode=True))

    def f(r_, t):
        plan(r_, t)
        if int(t) % 1200 < 60 and t > T("22:20"):
            r_.phone(r_.p1, home=True, wifi_home=True)
    await r.run("2026-11-06T07:30", script=f)
    p = r.p1
    assert r.at(p, "2026-11-05T22:00")[0] == "away" and r.at(p, "2026-11-05T22:00")[3] is False
    assert r.at(p, "2026-11-05T22:30")[0] == "waiting" and r.at(p, "2026-11-05T22:30")[3] is True
    assert r.started_count(p) == 1 and hm(r.events("rhythm_started", "p1")[0]["ts"]) <= "23:10"
    assert r.kinds("p1").count("away_precool") <= 1


async def test_bedside_dial_beacon_alone_cannot_start_a_night_but_backs_the_pad(tmp_path):
    """The phone lying by the left dial on the charger is a weak hint (never an 'out of bed' vote). With a dead pad it is
    not enough to start the night by itself: the schedule takes over at bedtime instead."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.bed.garbage["left"] = True
    plan = Plan()
    plan.pad_on["left"] = False
    plan.at("22:20", lambda r_: r_.phone(r_.p1, charging=True, dials=[{"minor": 1, "rssi": -55}], home=True))
    await r.run("2026-11-06T01:00", script=plan)
    ev = r.events("rhythm_started", "p1")
    assert len(ev) == 1 and "schedule" in ev[0]["reason"] and hm(ev[0]["ts"]) <= "22:45"


async def test_live_watch_heart_rate_does_not_break_a_normal_night(tmp_path):
    """A watch reporting live heart rate that matches the pad confirms the sleeper; one that doesn't (someone else
    on the side) is not the sleeper's."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")

    def f(r_, t):
        plan(r_, t)
        fr = r_.bed.latest()
        if fr and int(t) % 120 < 60 and r_.bed.z["left"].occupied:
            hr = fr.zones["left"].sensor.hr
            if hr:
                r_.watch(r_.p1, hr + 1)
    await r.run("2026-11-06T07:30", script=f)
    p = r.p1
    assert r.started_count(p) == 1 and r.at(p, "2026-11-06T03:00")[0] == "rhythm"
    ident = (r.c.fused.get("left") or {}).get("watch") or {}
    assert ident.get("state") in (None, "me", "stale")
    assert "problem" not in r.kinds("p1")


async def test_the_calendar_feed_holds_the_schedule_start_until_the_event_is_over(tmp_path):
    """The bed strip is silent (so the night would start on the schedule at 22:30) but the calendar says an evening event
    runs until 23:30 (+20 min to get home): the schedule start waits for it."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.bed.empty_block["left"] = True
    cal = SourceConfig(id="cal", name="cal", driver="ha-entity", role="calendar", kind="calendar", person="p1",
                       params={"entity_id": "calendar.evening"})
    r.sources.configs.append(cal)
    plan = Plan()
    plan.at("21:00", lambda r_: r_.sources._sample(cal, T("23:30"), now(r_)))
    await r.run("2026-11-06T01:00", script=plan)
    ev = r.events("rhythm_started", "p1")
    assert len(ev) == 1 and ts("2026-11-05T23:45") <= ev[0]["ts"] <= ts("2026-11-06T00:10"), ev


async def test_a_home_assistant_light_and_a_motion_sensor_do_not_start_or_end_anything(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    light = r.add("light", None, role="light")
    motion = r.add("motion", "left")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.drive(motion, "left", lambda o: 1.0 if o and int(now(r)) % 3600 < 600 else 0.0)
    plan.at("21:00", lambda r_: light.set(1.0)).at("22:45", lambda r_: light.set(0.0))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    assert "out_of_bed" not in r.states(p, "2026-11-06T00:00", "2026-11-06T06:00")
