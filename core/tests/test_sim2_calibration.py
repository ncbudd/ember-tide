"""Second pass, area 4: which nights count as calibration nights when the night had a hole in it. A restart is not an
outage; a long outage while in bed is; an outage while out of bed isn't about their sleep; half awake is a clean boundary.
Alex (p1, left); Sam's Autopilot is off."""

import pytest

from sim2kit import catch, kinds
from simnight import Plan, Sim, ts
from test_sim_night_calibration import NORMAL, learned, one_night, three_ordinary_nights


async def _night(tmp_path, scenario, until_hour="12:30"):
    r, lr, base = await three_ordinary_nights(tmp_path)
    got = catch(r)
    await one_night(r, "2026-11-05", scenario, until_hour=until_hour)
    lr2, base2 = learned(r)
    return r, lr2, got


def _kinds_that_must_not_fire(got):
    return [n for n in got if n.kind in ("wearable", "bed_silent", "sensor", "target", "account", "reach", "bridge", "storage")]


async def test_a_two_minute_server_restart_at_2am_does_not_exclude_the_night(tmp_path):
    r, lr, base = await three_ordinary_nights(tmp_path)
    got = catch(r)
    r.clock.jump_to(ts("2026-11-05T20:30"))
    plan = Plan("2026-11-05").sleep("left", *NORMAL)
    await r.run("2026-11-06T02:00", script=plan)
    r.crash()
    await r.run("2026-11-06T02:02", script=plan)
    r.boot()
    got2 = catch(r)
    await r.run("2026-11-06T12:30", script=plan)
    lr2, _ = learned(r)
    assert lr2["have"] == 4 and not lr2.get("left_out"), lr2
    assert not _kinds_that_must_not_fire(got + got2)


async def test_a_ten_minute_restart_still_counts(tmp_path):
    r, lr, base = await three_ordinary_nights(tmp_path)
    r.clock.jump_to(ts("2026-11-05T20:30"))
    plan = Plan("2026-11-05").sleep("left", *NORMAL)
    await r.run("2026-11-06T02:00", script=plan)
    r.crash()
    await r.run("2026-11-06T02:10", script=plan)
    r.boot()
    await r.run("2026-11-06T12:30", script=plan)
    lr2, _ = learned(r)
    assert lr2["have"] == 4


async def test_a_70_minute_broker_outage_while_in_bed_excludes_the_night(tmp_path):
    def broker(plan, r):
        plan.sleep("left", *NORMAL)
        plan.at("01:00", lambda x: setattr(x.bed, "link", False)).at("02:10", lambda x: setattr(x.bed, "link", True))
    r, lr, got = await _night(tmp_path, broker)
    assert lr["have"] == 3
    assert {"date": "2026-11-05", "why": "the bed or the server was off for a while"} in lr["left_out"]


async def test_a_50_minute_broker_outage_still_counts(tmp_path):
    def broker(plan, r):
        plan.sleep("left", *NORMAL)
        plan.at("01:00", lambda x: setattr(x.bed, "link", False)).at("01:50", lambda x: setattr(x.bed, "link", True))
    r, lr, got = await _night(tmp_path, broker)
    assert lr["have"] == 4 and not lr.get("left_out")


@pytest.mark.xfail(reason="JUDGEMENT: summary.outage_min counts any hole of 10+ min between the first and last time in bed, "
                          "including one that starts and ends with the side empty (up at 01:00, cut 01:10-03:00, still up at "
                          "03:00): the night is left out as 'the bed or the server was off', though nothing of their sleep was lost. "
                          "Should a hole flanked by out-of-bed readings on both sides be ignored? (They could have slept in the gap.)",
                   strict=True)
async def test_a_power_cut_while_out_of_bed_is_not_about_their_sleep(tmp_path):
    """Up from 01:00 to 03:30 (an unwell night); the bed is off 01:10-03:00 while nobody is in it."""
    def cut(plan, r):
        plan.sleep("left", *NORMAL, ups=[("01:00", "03:30")])
        plan.at("01:10", lambda x: x.bed.power_off()).at("03:00", lambda x: x.bed.power_on())
    r, lr, got = await _night(tmp_path, cut)
    why = [x["why"] for x in lr.get("left_out") or []]
    assert "the bed or the server was off for a while" not in why, lr
    assert lr["have"] == 4 or why, lr


def _awake_night(minutes_end: str):
    def f(plan, r):
        plan.sleep("left", *NORMAL)
        plan.at("23:00", lambda x: x.bed.active.update(left=True)).at(minutes_end, lambda x: x.bed.active.update(left=False))
    return f


@pytest.mark.parametrize("end,counts", [("02:50", True), ("03:10", False)])
async def test_a_night_half_awake_at_the_boundary(tmp_path, end, counts):
    r, lr, got = await _night(tmp_path, _awake_night(end))
    share = r.summary(r.p1, "2026-11-05")["awake_moving_share"]
    assert (share < 0.5) == counts, share
    assert (lr["have"] == 4) == counts, (share, lr)
    if not counts:
        assert {"date": "2026-11-05", "why": "you were awake and moving for most of it"} in lr["left_out"]


async def test_the_left_out_list_is_plain_words_and_newest_first(tmp_path):
    r, lr, base = await three_ordinary_nights(tmp_path)
    await one_night(r, "2026-11-05", _awake_night("04:00"), until_hour="12:30")
    await one_night(r, "2026-11-06", lambda plan, r_: (plan.sleep("left", *NORMAL),
                                                       plan.at("01:00", lambda x: x.bed.power_off()).at("04:00", lambda x: x.bed.power_on())),
                    until_hour="12:30")
    lr2, _ = learned(r)
    dates = [x["date"] for x in lr2["left_out"]]
    assert dates == sorted(dates, reverse=True) and len(dates) == 2
    for x in lr2["left_out"]:
        assert x["why"][0].islower() and "_" not in x["why"] and "min" not in x["why"].split()
        assert len(x["why"]) < 60
    assert lr2["have"] == 3
