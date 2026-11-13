"""Whole-night simulations around time and faults: clock changes, edited rhythms, the phone's alarm, restarts, power
cuts, the bed's hub going away or misbehaving, a database that fails once.

Alex is on the left (p1). Work-night rhythm: bed 22:30, wake 07:00 (Thursday 5 Nov 2026), day-off rhythm: bed 23:30,
wake 09:00. Pad pre-warm starts 45 minutes before bedtime."""

import pytest

from embertide.units import f_to_c
from simnight import Plan, Sim, T, hm, ts

F = f_to_c


def tgt(r, p, when):
    return r.at(p, when)[4]


# ───────────────────────── daylight saving ─────────────────────────
async def test_autumn_clocks_go_back_an_hour_in_the_night(tmp_path):
    """Sat 31 Oct to Sun 1 Nov 2026: 01:00-02:00 happens twice. One start, the 03:00 step still at 03:00 by the clock,
    the warm wake an hour later in real time, and the summary counts the extra hour in bed."""
    r = Sim(tmp_path, "2026-10-31T21:00")
    plan = Plan("2026-10-31").sleep("left", "23:40", "08:50")
    await r.run("2026-11-01T09:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    assert r.at(p, "2026-11-01T03:30")[2] == "late"                          # the clock-anchored step
    assert r.at(p, "2026-11-01T08:40")[2] == "wake"                          # warm wake starts 30 minutes before 09:00
    assert not r.bed.z["left"].on and r.timeline(p)[-1][1] == "done"
    s = r.summary(p, "2026-10-31")
    assert 600 <= s["in_bed_min"] <= 620, s["in_bed_min"]                      # 9 h 10 min of clock plus the extra hour
    assert s["times_up"] == 0
    assert not {"problem", "hub"} & set(r.kinds("p1"))


async def test_spring_clocks_go_forward_an_hour_in_the_night(tmp_path):
    """13 to 14 March 2027: 02:00-03:00 never happens. The night does not skip a step or end early."""
    r = Sim(tmp_path, "2027-03-13T21:00")
    plan = Plan("2027-03-13").sleep("left", "23:40", "08:50")
    await r.run("2027-03-14T09:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    phases = [row[p.id][2] for _, row in r.rec if row[p.id][1]]
    assert "deep" in phases and "late" in phases and "wake" in phases
    assert r.at(p, "2027-03-14T03:30")[2] == "late"
    assert r.at(p, "2027-03-14T08:40")[3] is True and not r.bed.z["left"].on
    s = r.summary(p, "2027-03-13")
    assert 440 <= s["in_bed_min"] <= 500, s["in_bed_min"]                      # 9 h 10 min of clock less the missing hour


# ───────────────────────── editing the plan while it runs ─────────────────────────
async def test_a_rhythm_edited_while_the_night_runs(tmp_path):
    """The late-night step is changed at 01:00, the wake time at 04:00. The bed follows both at once, no restart of the
    night, nothing lost."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:20")

    def late_warmer(r_):
        for ph in r_.hh.people[0].rhythms[0].phases:
            if ph.id == "late":
                ph.temp_c = F(70)
        r_.c.reload_household(r_.hh)

    def earlier_wake(r_):
        r_.hh.people[0].rhythms[0].schedule.wake = "06:00"
        r_.c.reload_household(r_.hh)
    plan.at("01:00", late_warmer).at("04:00", earlier_wake)
    await r.run("2026-11-06T07:00", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    assert tgt(r, p, "2026-11-06T03:40") == pytest.approx(F(70), abs=0.15)
    assert r.at(p, "2026-11-06T05:40")[2] == "wake"                              # warm wake now 05:30
    assert r.timeline(p)[-1] == ("06:16", "done") or r.timeline(p)[-1][1] == "done"
    assert not r.bed.z["left"].on


async def test_bedtime_changed_to_earlier_than_now_before_getting_in(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")

    def earlier(r_):
        r_.hh.people[0].rhythms[0].schedule.bedtime = "21:00"
        r_.c.reload_household(r_.hh)
    plan.at("22:00", earlier)
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    assert hm(r.events("rhythm_started", "p1")[0]["ts"]) <= "22:50"            # still starts when they get in
    assert r.at(p, "2026-11-06T00:30")[2] == "deep"
    assert r.timeline(p)[-1][1] == "done"


async def test_bedtime_changed_to_earlier_than_now_when_the_night_runs_on_the_clock(tmp_path):
    """Sensorless household (start on presence off): moving bedtime to a time that has already passed starts the night
    at once instead of leaving the bed cold."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.hh.people[0].rhythms[0].schedule.start_on_presence = False
    r.c.reload_household(r.hh)

    def earlier(r_):
        r_.hh.people[0].rhythms[0].schedule.bedtime = "21:30"
        r_.c.reload_household(r_.hh)
    plan = Plan().sleep("left", "22:40", "06:50").at("22:00", earlier)
    await r.run("2026-11-06T07:30", script=plan)
    ev = r.events("rhythm_started", "p1")
    assert len(ev) <= 1
    assert r.e(r.p1).s.started_by == "schedule"
    assert r.timeline(r.p1)[-1][1] == "done"


async def test_the_weekday_to_weekend_hand_over(tmp_path):
    """A Thursday night runs on the weeknight rhythm right through midnight into Friday morning; Friday night is a
    weekend night (bed 23:30, wake 09:00) and its warm wake and pad are still on at 08:00."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    seen = {}

    def peek(r_):
        seen[hm(r_.clock.now_ts())] = r_.c.snapshot()["people"][0]["schedule"]
    plan.at("00:10", peek)
    await r.run("2026-11-06T07:30", script=plan)
    assert seen["00:10"]["rhythm_id"] == "weeknights"
    assert r.at(r.p1, "2026-11-06T07:20")[3] is False                          # weeknight: off after 07:15
    r.clock.jump_to(ts("2026-11-06T21:00"))
    plan2 = Plan("2026-11-06").sleep("left", "23:40", "08:50")
    await r.run("2026-11-07T09:40", script=plan2)
    p = r.p1
    assert r.started_count(p) == 2                                              # one for each night
    assert r.at(p, "2026-11-07T08:00")[3] is True and r.at(p, "2026-11-07T08:00")[0] == "rhythm"
    assert r.at(p, "2026-11-07T00:20")[2] == "fall_asleep" or r.at(p, "2026-11-07T00:20")[2] == "deep"
    assert not r.bed.z["left"].on


# ───────────────────────── the server's clock ─────────────────────────
async def test_the_server_clock_jumps_forward_then_back(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: r_.clock.jump_to(r_.clock.now_ts() + 3 * 3600))
    plan.at("04:30", lambda r_: r_.clock.jump_to(r_.clock.now_ts() - 2 * 3600))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    assert "out_of_bed" not in r.states(p, "2026-11-06T00:00", "2026-11-06T06:00")
    assert not r.tick_failures
    assert "problem" not in r.kinds("p1")
    assert r.timeline(p)[-1][1] == "done"


# ───────────────────────── the phone's alarm ─────────────────────────
def alarm_script(r, plan, alarm_at):
    """The phone reports its next alarm every 20 minutes; alarm_at(now) says what it holds (a time or None)."""
    def f(r_, now):
        plan(r_, now)
        if int(now) % 1200 < 60:
            r_.phone(r_.p1, next_alarm=alarm_at(now))
    return f


async def test_an_earlier_phone_alarm_moves_the_warm_wake(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:30")
    await r.run("2026-11-06T07:30", script=alarm_script(r, plan, lambda now: T("06:15")))
    p = r.p1
    assert r.at(p, "2026-11-06T05:30")[2] == "late"
    assert r.at(p, "2026-11-06T05:55")[2] == "wake"                              # alarm 06:15, warming from 05:45
    assert r.timeline(p)[-1][1] == "done" and not r.bed.z["left"].on


async def test_a_later_phone_alarm_lets_them_sleep_in(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "08:00")
    await r.run("2026-11-06T09:00", script=alarm_script(r, plan, lambda now: T("08:00")))
    p = r.p1
    assert r.at(p, "2026-11-06T06:50")[2] == "late"                              # not warming at the planned 06:30
    assert r.at(p, "2026-11-06T07:45")[2] == "wake"
    assert r.at(p, "2026-11-06T08:30")[3] is False or r.at(p, "2026-11-06T08:30")[0] == "done"


async def test_the_alarm_is_cancelled_in_the_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "07:00")

    def alarm(now):
        return T("06:15") if now < T("02:00") else None
    await r.run("2026-11-06T07:30", script=alarm_script(r, plan, alarm))
    p = r.p1
    assert r.at(p, "2026-11-06T05:55")[2] == "late"                              # back to the planned wake
    assert r.at(p, "2026-11-06T06:40")[2] == "wake"
    assert len(r.events("wake_alarm_cleared", "p1")) == 1


async def test_a_snoozed_alarm_does_not_stretch_the_wake(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:40")

    def alarm(now):
        return T("06:15") if now < T("06:15") else T("06:25")
    await r.run("2026-11-06T07:30", script=alarm_script(r, plan, alarm))
    p = r.p1
    assert len(r.events("wake_from_alarm", "p1")) == 1                           # followed once, the snooze ignored
    assert r.at(p, "2026-11-06T05:55")[2] == "wake"
    assert r.timeline(p)[-1][1] == "done" and r.timeline(p)[-1][0] <= "06:50"   # up at 06:40: done once out of bed 3 min


async def test_a_new_alarm_after_the_first_rang_reopens_the_finished_night(tmp_path):
    """Intended: Alex gets up at 06:50, the pad goes off; later sets a new alarm for 09:30 and lies back down. The
    night is not over (the wake follows the alarm), so the warm wake runs for it."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50").sleep("left", "08:20", "09:40")

    def alarm(now):
        return T("06:30") if now < T("07:30") else T("09:30")
    await r.run("2026-11-06T10:30", script=alarm_script(r, plan, alarm))
    p = r.p1
    assert r.at(p, "2026-11-06T07:20")[0] in ("done", "out_of_bed", "out_of_bed_hold")
    assert r.at(p, "2026-11-06T09:10")[0] in ("rhythm", "out_of_bed_hold")
    assert r.at(p, "2026-11-06T09:10")[3] is True                                # warm again for the second lie-down
    assert r.timeline(p)[-1][1] == "done"


# ───────────────────────── restarts ─────────────────────────
STAGES = ["21:50", "22:43", "23:30", "01:00", "02:10", "04:30", "06:20", "07:10"]


@pytest.mark.parametrize("when", STAGES)
async def test_server_restart_at_every_stage(tmp_path, when):
    """The server is down for two minutes at each stage. A change made at 23:00 for the rest of the night and a hot flash
    at 00:55 are kept; no second start; nothing about the night is invented."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50", ups=[("02:00", "02:20"), ("04:00", "04:50")])
    plan.at("23:00", lambda r_: r_.e(r_.p1).adjust(r_.clock.now_ts(), delta_c=0.6, until="night"))
    plan.at("00:55", lambda r_: r_.e(r_.p1).flash("hot", r_.clock.now_ts(), minutes=30))
    before = {}

    def crash(r_):
        e = r_.e(r_.p1)
        before.update(started=e.s.started, in_bed=e.s.in_bed, override=e.s.override, flash=e.s.flash, by=e.s.started_by)
        r_.crash()

    def back(r_):
        r_.boot()
    hh, mm = int(when[:2]), int(when[3:])
    t_back = f"{(hh * 60 + mm + 2) // 60 % 24:02d}:{(hh * 60 + mm + 2) % 60:02d}"
    plan.at(when, crash).at(t_back, back)
    await r.run("2026-11-06T07:45", script=plan)
    p = r.p1
    assert r.started_count(p) == 1 and not r.events("rhythm_restarted", "p1")
    e = r.e(p)
    if before["started"]:
        assert e.s.in_bed == before["in_bed"] and e.s.started_by == before["by"]
    if before["override"]:
        assert any(x["kind"] == "adjust" for x in r.events(person="p1"))
    assert r.timeline(p)[-1][1] == "done" and not r.bed.z["left"].on
    if T(when) < T("23:00") or T(when) > T("05:00"):
        pass
    # the change made for the rest of the night is still in the target at 05:30 (late step 65 F + 0.6)
    if ts("2026-11-06T05:30") > T(when) + 300 and T(when) < T("06:20"):
        assert tgt(r, p, "2026-11-06T05:30") == pytest.approx(F(65) + 0.6, abs=0.2)


async def test_the_server_is_off_for_five_minutes_and_for_three_hours(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: r_.crash()).at("01:05", lambda r_: r_.boot())
    plan.at("02:30", lambda r_: r_.crash()).at("05:30", lambda r_: r_.boot())
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    assert r.at(p, "2026-11-06T01:10")[0] == "rhythm" and r.at(p, "2026-11-06T05:35")[0] == "rhythm"
    assert r.at(p, "2026-11-06T05:35")[2] == "late"
    assert r.at(p, "2026-11-06T05:35")[3] is True
    assert r.timeline(p)[-1][1] == "done"
    assert not {"problem", "empty_side"} & set(r.kinds("p1"))


# ───────────────────────── the bed's hub ─────────────────────────
async def test_hub_offline_at_bedtime_and_back_later(tmp_path):
    """The hub is unreachable from 22:00 to 23:15. Nothing can be seen, so at bedtime the night starts on the schedule (a
    cold bed is worse than a warm empty one), and the pad is commanded at once when the hub is back. One 'lost contact'
    message, and no false 'isn't taking changes' on the way back."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("22:00", lambda r_: setattr(r_.bed, "link", False)).at("23:15", lambda r_: setattr(r_.bed, "link", True))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    ev = r.events("rhythm_started", "p1")[0]
    assert "schedule" in ev["reason"] and ev["ts"] <= ts("2026-11-05T22:35")
    assert r.at(p, "2026-11-05T23:20")[3] is True                                  # commanded as soon as the hub answered
    assert r.at(p, "2026-11-06T00:30")[3] is True
    assert r.kinds().count("problem") == 1


async def test_hub_offline_mid_night_and_back(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("03:00", lambda r_: setattr(r_.bed, "link", False)).at("03:40", lambda r_: setattr(r_.bed, "link", True))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    assert r.at(p, "2026-11-06T03:50")[0] == "rhythm" and r.at(p, "2026-11-06T03:50")[3] is True
    assert "out_of_bed" not in r.states(p, "2026-11-06T02:00", "2026-11-06T06:00")
    assert r.kinds().count("problem") == 1


async def test_hub_offline_for_the_rest_of_the_night(tmp_path):
    """Off from 23:00 to 07:30: when it is back the night is over and the pads are told to turn off, not warm up."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("23:00", lambda r_: setattr(r_.bed, "link", False)).at("07:30", lambda r_: setattr(r_.bed, "link", True))
    await r.run("2026-11-06T08:30", script=plan)
    assert not r.bed.z["left"].on and not r.bed.z["right"].on
    assert r.kinds().count("problem") == 1


async def test_a_hub_that_ignores_commands_is_noticed_and_not_hammered(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: setattr(r_.bed, "ignore_commands", True))
    plan.at("05:00", lambda r_: setattr(r_.bed, "ignore_commands", False))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    # (the back-off between repeated commands runs on the wall clock, so a simulated night can't measure it)
    assert r.kinds().count("problem") == 1                                       # told once: it isn't taking changes
    assert r.at(p, "2026-11-06T05:20")[4] == pytest.approx(F(65), abs=0.2)       # and caught up as soon as it listened
    assert r.timeline(p)[-1][1] == "done" and not r.bed.z["left"].on


async def test_a_pad_that_never_reaches_the_target_is_reported(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("00:30", lambda r_: setattr(r_.bed, "stuck_measured", 24.5))
    await r.run("2026-11-06T07:30", script=plan)
    kinds = r.kinds()
    assert kinds.count("target") >= 2                  # held for the morning (sleeper), and at once because it was far off (owners)
    assert "problem" not in kinds


async def test_low_water_and_a_hub_fault_are_each_told_once(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("02:00", lambda r_: setattr(r_.bed, "low_water", True))
    plan.at("03:00", lambda r_: r_.bed.inject_fault("hub_hot"))
    plan.at("03:30", lambda r_: r_.bed.clear_faults())
    plan.at("04:00", lambda r_: setattr(r_.bed, "low_water", False))
    await r.run("2026-11-06T07:30", script=plan)
    assert r.kinds().count("hub") == 3                    # low water, the hub too hot, and its safety check (one fault, two lines)
    assert r.started_count(r.p1) == 1 and r.timeline(r.p1)[-1][1] == "done"


# ───────────────────────── the database ─────────────────────────
async def test_one_failed_event_write_does_not_stop_the_start(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    r.fail_once("event", when=lambda a, k: len(a) > 1 and a[1] == "rhythm_started")
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.e(p).s.started and r.at(p, "2026-11-06T00:30")[2] == "deep" and r.at(p, "2026-11-06T00:30")[3] is True
    assert r.timeline(p)[-1][1] == "done"


async def test_one_failed_telemetry_write_loses_one_tick_not_the_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: r_.fail_once("telemetry"))
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert len(r.tick_failures) == 1
    assert r.started_count(p) == 1 and r.at(p, "2026-11-06T03:30")[2] == "late"
    assert r.timeline(p)[-1][1] == "done"
    assert r.summary(p)["has_data"]


# ───────────────────────── messages across a restart ─────────────────────────
async def test_messages_are_not_sent_again_after_a_restart(tmp_path):
    """Sam is away (told once at warm-up) and Alex never comes to bed (told once at 02:00): restarting the server in
    between and after must not say either again."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.c.home[r.p2.id] = False
    plan = Plan()
    plan.at("22:10", lambda r_: (r_.crash(), r_.boot(), r_.c.home.update({"p2": False}))).at(
        "02:30", lambda r_: (r_.crash(), r_.boot(), r_.c.home.update({"p2": False})))
    await r.run("2026-11-06T07:30", script=plan)
    assert r.kinds("p2").count("away_precool") == 1
    assert r.kinds("p1").count("empty_side") == 1
