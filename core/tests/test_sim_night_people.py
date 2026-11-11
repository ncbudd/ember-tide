"""Whole-night simulations with people: two in bed, one late, wrong side, away, guest, pets, reading, getting up.

Alex is on the left (p1), Sam on the right (p2). Work-night rhythm: bed 22:30, wake 07:00."""

import pytest

from embertide.engine import away as away_mod
from embertide.models import Pet
from embertide.units import f_to_c
from simnight import Plan, Sim, T, hm, ts

CAT = Pet(name="Biscuit", kind="cat", weight_kg=4.5, sleeps_on_bed="yes")
DOG = Pet(name="Rufus", kind="dog", weight_kg=25, sleeps_on_bed="yes")


def start_time(r, p):
    ev = r.events("rhythm_started", p.id)
    return hm(ev[0]["ts"]) if ev else None


# ───────────────────────── two people ─────────────────────────
async def test_both_in_bed_together_each_side_has_its_own_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50").sleep("right", "22:40", "06:55")
    await r.run("2026-11-06T07:30", script=plan)
    for p in (r.p1, r.p2):
        assert r.started_count(p) == 1 and start_time(r, p) <= "22:50"
    assert r.at(r.p1, "2026-11-06T00:30")[4] == pytest.approx(f_to_c(63), abs=0.15)      # Alex runs cool
    assert r.at(r.p2, "2026-11-06T00:30")[4] == pytest.approx(f_to_c(72), abs=0.15)      # Sam runs warm
    assert not {"problem", "empty_side", "not_started"} & set(r.kinds())


async def test_getting_in_fifteen_seconds_apart_is_two_people_not_an_echo(tmp_path):
    r = Sim(tmp_path, "2026-11-05T22:00")
    plan = Plan().sleep("left", "22:40:00", "06:50").sleep("right", "22:40:15", "06:55")
    await r.run("2026-11-05T22:38", dt=60, script=plan)
    await r.run("2026-11-05T22:50", dt=15, script=plan)
    await r.run("2026-11-06T07:30", dt=60, script=plan)
    for p in (r.p1, r.p2):
        assert r.started_count(p) == 1, (p.id, r.timeline(p))
    assert "out_of_bed" not in r.states(r.p2, "2026-11-06T00:00", "2026-11-06T06:00")


async def test_one_gets_in_much_later(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50").sleep("right", "01:30", "06:55")
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p1) == 1 and start_time(r, r.p1) <= "22:50"
    assert r.started_count(r.p2) == 1 and "01:33" <= start_time(r, r.p2) <= "01:45"
    assert r.at(r.p2, "2026-11-06T02:30")[0] == "rhythm"
    assert "empty_side" not in r.kinds("p2")                      # they came, just late
    assert r.at(r.p1, "2026-11-06T01:35")[0] == "rhythm"            # Alex's night untouched


async def test_sleeper_on_the_partners_side_while_the_partner_is_away(tmp_path):
    """Alex is on Sam's side (the right) all night; Sam is away. Sam's side is Sam's: Sam stays 'away', Alex's own
    empty side is not warmed, and Alex is told the left side is empty with a way to turn it off."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.c.home[r.p2.id] = False
    plan = Plan().sleep("right", "22:40", "06:50")
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p1) == 0
    assert "missed" in r.states(r.p1, "2026-11-06T02:10", "2026-11-06T03:00")
    assert not r.bed.z["left"].on
    assert "empty_side" in r.kinds("p1")


async def test_both_on_one_side(tmp_path):
    """Both people on the left. The strip shows one stretch on the left and an echo on the right: Alex's night runs, Sam
    (whose side is empty) does not start, and nothing is raised as a problem."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p1) == 1 and r.started_count(r.p2) == 0
    assert "problem" not in r.kinds()


async def test_one_crosses_to_the_other_side_in_the_night(tmp_path):
    """Sam goes to the sofa at 01:00 and Alex rolls into the middle then onto Sam's side. Alex is on the right from
    01:20: Sam's engine follows the right side (it has no idea who it is). Nothing breaks: both sides keep a plan,
    the left does not run for an empty side for hours."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "01:20").sleep("right", "22:40", "01:00").sleep("right", "01:20", "06:50")
    await r.run("2026-11-06T07:30", script=plan)
    assert not r.bed.z["left"].on or r.at(r.p1, "2026-11-06T05:00")[0] in ("out_of_bed",)
    assert r.at(r.p2, "2026-11-06T03:00")[0] == "rhythm"
    assert r.at(r.p1, "2026-11-06T04:00")[0] == "out_of_bed"


async def test_one_person_away_the_other_sleeps_normally(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.c.home[r.p2.id] = False
    plan = Plan().sleep("left", "22:40", "06:50")
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p1) == 1
    assert r.states(r.p2, "2026-11-05T21:50", "2026-11-06T07:00") == {"away"}
    assert not r.bed.z["right"].on
    assert r.kinds("p2").count("away_precool") == 1                 # told once at warm-up time, with buttons
    assert "away_precool" not in r.kinds("p1")


async def test_away_with_an_end_date_comes_back_by_itself(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    away_mod.start(r.c, r.p2.id, r.clock.now_ts(), by="p2", until=ts("2026-11-06T12:00"))
    plan = Plan().sleep("left", "22:40", "06:50")
    await r.run("2026-11-06T07:30", script=plan)
    assert r.states(r.p2, "2026-11-05T22:00", "2026-11-06T07:00") == {"away"}
    await r.run("2026-11-06T12:30", script=plan)
    assert not away_mod.is_away(r.store, r.p2.id, r.clock.now_ts())
    assert r.e(r.p2).s.state in ("day", "done", "waiting")


async def test_away_without_an_end_date_ends_when_they_use_their_side(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    away_mod.start(r.c, r.p2.id, r.clock.now_ts(), by="p2")
    plan = Plan().sleep("left", "22:40", "06:50")
    await r.run("2026-11-05T23:00", script=plan)
    assert r.e(r.p2).s.state == "away"
    r.e(r.p2).tonight(r.clock.now_ts(), "start_now", source="app")       # Sam is back and starts the side by hand
    await r.run("2026-11-05T23:30", script=plan)
    assert not away_mod.is_away(r.store, r.p2.id, r.clock.now_ts())
    assert r.e(r.p2).s.state in ("rhythm", "out_of_bed_hold") and r.bed.z["right"].on      # (nobody is in the bed yet: the hold)


async def test_a_guest_on_the_right_side_all_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.c.guest_start(["right"], 24.0, 40, None)
    plan = Plan().sleep("left", "22:40", "06:50").sleep("right", "23:15", "07:30")
    await r.run("2026-11-06T09:00", script=plan)
    assert r.started_count(r.p2) == 0 and r.e(r.p2).s.state == "guest"
    assert r.bed.z["right"].on is False                               # 40 minutes after the guest got up it switches off
    assert r.at(r.p2, "2026-11-06T03:00")[3:] == (True, pytest.approx(24.0, abs=0.1))
    assert not [e for e in r.events() if e.get("person") == "p2" and e["kind"] in ("rhythm_started",)]
    assert not r.kinds("p2")                                          # nothing about the guest goes to Sam
    assert r.summary(r.p2, "2026-11-05").get("has_data") is False                    # and nothing is recorded


async def test_a_guest_on_the_away_persons_side(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    away_mod.start(r.c, r.p2.id, r.clock.now_ts(), by="p2")
    r.c.guest_start(["right"], 23.0, 40, None)
    plan = Plan().sleep("left", "22:40", "06:50").sleep("right", "23:00", "07:00")
    await r.run("2026-11-06T07:30", script=plan)
    assert r.e(r.p2).s.state == "guest"
    assert r.at(r.p2, "2026-11-06T01:00")[3] is True
    assert "away_precool" not in r.kinds("p2") and "away_check" not in r.kinds("p2")


# ───────────────────────── pets and children ─────────────────────────
async def test_a_cat_alone_on_an_empty_side_before_bedtime(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.hh.pets = [CAT]
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("21:00", lambda r_: r_.bed.pets.update(right=165)).at("22:20", lambda r_: r_.bed.pets.clear())
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p2) == 0
    assert r.started_count(r.p1) == 1
    assert "empty_side" in r.kinds("p2")


async def test_a_cat_on_the_empty_side_all_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.hh.pets = [CAT]
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("21:00", lambda r_: r_.bed.pets.update(right=165))
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p2) == 0 and r.started_count(r.p1) == 1
    assert not r.bed.z["right"].on
    assert r.summary(r.p2).get("has_data") is not None


async def test_a_cat_stays_on_after_the_person_leaves_in_the_morning(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.hh.pets = [CAT]
    plan = Plan().sleep("left", "22:40", "06:50").sleep("right", "22:45", "06:55")
    plan.at("06:56", lambda r_: r_.bed.pets.update(right=165))
    await r.run("2026-11-06T10:00", script=plan)
    assert r.started_count(r.p2) == 1
    assert r.timeline(r.p2)[-1][1] == "done" and not r.bed.z["right"].on
    assert r.started_count(r.p2) == 1


async def test_a_pet_with_an_adult_heart_rate_on_the_empty_side(tmp_path):
    """A calm dog with a heart rate in a person's range, alone on Sam's empty side, can't be told from Sam by the strip
    alone. With a radar on the side that sees nobody, the radar's no beats the strip."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.hh.pets = [DOG]
    radar = r.add("radar", "right")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.drive(radar, "right", lambda o: 0.0)
    plan.at("22:00", lambda r_: r_.bed.pets.update(right=62))
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p2) == 0


@pytest.mark.xfail(strict=False, reason="JUDGEMENT: a dog with a person-like heart rate alone on an empty side, with no radar or "
                   "scale, looks exactly like Sam. Should the strip ever be enough on its own to start a night?")
async def test_a_pet_with_an_adult_heart_rate_and_nothing_else(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.hh.pets = [DOG]
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("22:00", lambda r_: r_.bed.pets.update(right=62))
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p2) == 0


async def test_a_child_joins_the_parent_at_two(tmp_path):
    """A child climbs in beside Alex at 02:00 and stays (the strip sees one side occupied, stronger readings): the night
    carries on, the bed is not switched off and nobody is told anything."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50").sleep("right", "02:00", "06:00")
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p1) == 1
    assert r.at(r.p1, "2026-11-06T04:00")[0] == "rhythm"
    assert "problem" not in r.kinds()


# ───────────────────────── how someone gets into bed ─────────────────────────
async def test_sitting_on_the_edge_for_five_minutes_then_the_real_bedtime(tmp_path):
    """Five minutes on the edge at 21:30 (a phone call), then up for an hour, then bed at 22:45: one clean night
    anchored at the real bedtime, the cool 'Fall asleep' step not skipped."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "21:30", "21:35").sleep("left", "22:45", "06:50")
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.e(p).s.state == "done"
    final = [e for e in r.events("rhythm_started", "p1")][-1]
    assert final["ts"] >= ts("2026-11-05T22:45")
    assert r.at(p, "2026-11-05T23:00")[2] == "fall_asleep"
    assert r.at(p, "2026-11-06T00:30")[2] == "deep"
    assert "not_started" not in r.kinds("p1")


async def test_reading_in_bed_for_two_hours_before_sleep(tmp_path):
    """Decided: lying down at 21:10 to read until 23:00 starts the night, but the bed holds "Fall asleep" until a sign of
    sleep or the usual bedtime (22:30); "Deep" then follows 40 minutes later, not at 21:53. (test_sim_night_sleepsign.py
    has each sign.)"""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "21:10", "06:50")
    plan.at("21:10", lambda r_: r_.bed.reading.update(left=True)).at("23:00", lambda r_: r_.bed.reading.pop("left", None))
    await r.run("2026-11-06T00:30", script=plan)
    assert r.at(r.p1, "2026-11-05T21:20")[0] == "rhythm"
    assert r.at(r.p1, "2026-11-05T22:30")[2] == "fall_asleep"
    assert r.at(r.p1, "2026-11-05T23:15")[2] == "deep"


@pytest.mark.parametrize("minutes,expect_off", [(20, False), (50, True), (90, True)])
async def test_getting_up_for_a_while_and_coming_back(tmp_path, minutes, expect_off):
    r = Sim(tmp_path, "2026-11-05T21:00")
    up_at = ts("2026-11-06T02:00")
    back = f"{(2 * 60 + minutes) // 60:02d}:{(2 * 60 + minutes) % 60:02d}"
    plan = Plan().sleep("left", "22:40", "06:50", ups=[("02:00", back)])
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    states = r.states(p, "2026-11-06T02:00", "2026-11-06T03:50")
    assert ("out_of_bed" in states) is expect_off and "out_of_bed_hold" in states
    assert r.at(p, f"2026-11-06T{(2 * 60 + minutes + 6) // 60:02d}:{(2 * 60 + minutes + 6) % 60:02d}")[0] == "rhythm"
    assert r.at(p, "2026-11-06T05:00")[3] is True
    s = r.summary(p)
    assert s["times_up"] == 1 and abs(s["up_minutes"] - minutes) <= 4


async def test_getting_up_for_good_an_hour_before_wake(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:00")
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.at(p, "2026-11-06T06:10")[0] == "out_of_bed_hold"
    assert r.at(p, "2026-11-06T06:40")[0] == "out_of_bed" and r.at(p, "2026-11-06T06:40")[3] is False
    assert r.timeline(p)[-1][1] == "done"
    assert "morning" in r.kinds("p1")


async def test_never_going_to_bed(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    await r.run("2026-11-06T09:00", script=Plan())
    p = r.p1
    assert r.started_count(p) == 0
    tl = r.timeline(p)
    assert [s for _, s in tl][:4] == ["day", "waiting", "missed", "done"] or "missed" in [s for _, s in tl], tl
    assert not r.bed.z["left"].on
    assert r.kinds("p1").count("empty_side") == 1
    assert "morning" not in r.kinds("p1")


async def test_going_to_bed_at_six_in_the_morning(tmp_path):
    """Lying down at 06:00 (an hour before wake) is not a night: no start, no warm-up, no message, the bed stays off."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "06:00", "12:00")
    await r.run("2026-11-06T13:00", script=plan)
    assert r.started_count(r.p1) == 0
    assert not r.bed.z["left"].on
    assert r.at(r.p1, "2026-11-06T08:00")[0] in ("missed", "done") and not r.at(r.p1, "2026-11-06T08:00")[3]
    assert r.kinds("p1").count("empty_side") == 1            # told once about the empty evening, not again for the morning
    assert not {"problem", "not_started"} & set(r.kinds("p1"))


async def test_a_night_that_runs_past_noon(tmp_path):
    """Sleeping in until 13:30: the side turned off after the warm wake and stays off; the new day at noon does not
    start a second night or restart the bed."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "+13:30")
    await r.run("2026-11-06T14:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    assert r.at(p, "2026-11-06T09:00")[3] is False and r.at(p, "2026-11-06T12:30")[3] is False
    assert r.e(p).s.state in ("day", "done")
    s = r.summary(p, "2026-11-05")
    assert s["has_data"] and s["in_bed_min"] > 8 * 60
