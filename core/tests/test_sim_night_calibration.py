"""Autopilot's calibration week: three ordinary nights teach a baseline; a strange night must neither count as one of
the learning nights nor move what was learned. Alex (p1, left); Sam's Autopilot is off to keep the run short."""

import pytest

from embertide.engine import away as away_mod
from embertide.models import Pet
from simnight import Plan, Sim, T, ts

NORMAL = ("22:40", "06:50")
CAT = Pet(name="Biscuit", kind="cat", weight_kg=4.5, sleeps_on_bed="yes")


async def one_night(r, evening: str, plan_fn, until_hour: str = "09:00") -> None:
    from datetime import date, timedelta
    r.clock.jump_to(ts(f"{evening}T20:30"))
    nxt = (date.fromisoformat(evening) + timedelta(days=1)).isoformat()
    plan = Plan(evening)
    plan_fn(plan, r)
    await r.run(f"{nxt}T{until_hour}", script=plan)


def ordinary(plan, r):
    plan.sleep("left", *NORMAL)


def learned(r):
    a = r.c.autopilot
    a._nights_cache.clear()
    a.__dict__.get("_base_cache", {}).clear()
    p, t = r.p1, r.clock.now_ts()
    return a.learning(p, t), a.baseline(p, t)


async def three_ordinary_nights(tmp_path):
    r = Sim(tmp_path, "2026-11-02T20:30")
    r.hh.people[1].settings.autopilot.mode = "off"
    for d in ("2026-11-02", "2026-11-03", "2026-11-04"):
        await one_night(r, d, ordinary)
    lr, base = learned(r)
    assert lr["have"] == 3 and not lr["done"] and base and base["hr"], (lr, base)
    return r, lr, base


# ───────────────────────── nights that must not count ─────────────────────────
def away_with_a_cat(plan, r):
    away_mod.start(r.c, "p1", r.clock.now_ts(), by="p1")
    r.hh.pets = [CAT]
    plan.at("21:00", lambda r_: r_.bed.pets.update(left=165))


def a_cat_alone(plan, r):
    r.hh.pets = [CAT]
    plan.at("21:00", lambda r_: r_.bed.pets.update(left=165))


def mostly_not_in_bed(plan, r):
    plan.sleep("left", "22:40", "01:00")


def a_guest(plan, r):
    r.c.guest_start(["left"], 24.0, 40, None)
    plan.sleep("left", "23:00", "06:50")


def off_tonight(plan, r):
    plan.sleep("left", *NORMAL)
    plan.at("21:30", lambda r_: r_.e(r_.p1).tonight(r_.clock.now_ts(), "skip"))


def a_very_late_night(plan, r):
    plan.sleep("left", "03:05", "10:00")


def a_feverish_restless_night(plan, r):
    plan.sleep("left", *NORMAL)
    plan.at("23:30", lambda r_: r_.bed.active.update(left=True)).at("05:00", lambda r_: r_.bed.active.update(left=False))


@pytest.mark.parametrize("weird", [away_with_a_cat, a_cat_alone, mostly_not_in_bed, a_guest, off_tonight, a_very_late_night,
                                   a_feverish_restless_night], ids=lambda f: f.__name__)
async def test_a_strange_night_does_not_count_or_skew_the_baseline(tmp_path, weird):
    r, lr, base = await three_ordinary_nights(tmp_path)
    await one_night(r, "2026-11-05", weird, until_hour="12:30")
    lr2, base2 = learned(r)
    assert lr2["have"] == 3, f"{weird.__name__} was counted as a learning night: {lr2}"
    assert base2["hr"] == pytest.approx(base["hr"], abs=1.0), (base, base2)
    assert base2["restless_p90"] == pytest.approx(base["restless_p90"], abs=0.05)


async def test_a_night_with_a_long_power_cut_does_not_count(tmp_path):
    r, lr, base = await three_ordinary_nights(tmp_path)

    def cut(plan, r_):
        plan.sleep("left", *NORMAL)
        plan.at("01:00", lambda x: x.bed.power_off()).at("04:00", lambda x: x.bed.power_on())
    await one_night(r, "2026-11-05", cut, until_hour="12:30")
    lr2, base2 = learned(r)
    assert lr2["have"] == 3
    assert {"date": "2026-11-05", "why": "the bed or the server was off for a while"} in lr2["left_out"]
    assert base2["hr"] == pytest.approx(base["hr"], abs=1.0)


async def test_a_short_power_blip_still_counts(tmp_path):
    """The control: a ten-minute cut is an ordinary night."""
    r, lr, base = await three_ordinary_nights(tmp_path)

    def blip(plan, r_):
        plan.sleep("left", *NORMAL)
        plan.at("01:00", lambda x: x.bed.power_off()).at("01:10", lambda x: x.bed.power_on())
    await one_night(r, "2026-11-05", blip, until_hour="12:30")
    lr2, _ = learned(r)
    assert lr2["have"] == 4 and not lr2.get("left_out")


async def test_a_server_outage_of_two_hours_does_not_count(tmp_path):
    """The server down (nothing recorded at all) for two hours in the night: the same as a power cut."""
    r, lr, base = await three_ordinary_nights(tmp_path)
    r.clock.jump_to(ts("2026-11-05T20:30"))
    plan = Plan("2026-11-05").sleep("left", *NORMAL)
    await r.run("2026-11-06T01:00", script=plan)
    r.clock.jump_to(ts("2026-11-06T03:00"))           # nothing ran (and nothing was recorded) in between
    await r.run("2026-11-06T12:30", script=plan)
    lr2, _ = learned(r)
    assert lr2["have"] == 3 and lr2["left_out"][0]["date"] == "2026-11-05"


async def test_a_fourth_ordinary_night_does_count(tmp_path):
    """The control for the above: the counting itself works."""
    r, lr, base = await three_ordinary_nights(tmp_path)
    await one_night(r, "2026-11-05", ordinary, until_hour="12:30")
    lr2, _ = learned(r)
    assert lr2["have"] == 4


async def test_a_mostly_awake_night_does_not_count(tmp_path):
    r, lr, base = await three_ordinary_nights(tmp_path)
    await one_night(r, "2026-11-05", a_feverish_restless_night, until_hour="12:30")
    lr2, _ = learned(r)
    assert lr2["have"] == 3
    assert {"date": "2026-11-05", "why": "you were awake and moving for most of it"} in lr2["left_out"]


async def test_one_bad_night_does_not_double_the_restless_baseline(tmp_path):
    r, lr, base = await three_ordinary_nights(tmp_path)
    await one_night(r, "2026-11-05", a_feverish_restless_night, until_hour="12:30")
    _, base2 = learned(r)
    assert base2["restless_p90"] == pytest.approx(base["restless_p90"], abs=0.05)


def tossing_and_turning(plan, r):
    """Asleep (a normal heart rate) but moving and turning for four hours: restless, not awake, so the night counts.
    Pooled with three calm nights that is over a tenth of all the windows, enough to move a 90th percentile a lot."""
    plan.sleep("left", *NORMAL)
    plan.at("23:30", lambda r_: r_.bed.junk.update(left=4)).at("03:30", lambda r_: r_.bed.junk.pop("left", None))


async def test_one_restless_night_that_counts_does_not_swing_the_learned_restlessness(tmp_path):
    r, lr, base = await three_ordinary_nights(tmp_path)
    await one_night(r, "2026-11-05", tossing_and_turning, until_hour="12:30")
    lr2, base2 = learned(r)
    assert lr2["have"] == 4                                 # it is one of the learning nights
    assert base2["restless_p90"] == pytest.approx(base["restless_p90"], abs=0.02), (base, base2)
    assert base2["band"]["restless"] == base2["restless_p90"]


def test_restless_outlier_rule_keeps_ordinary_nights_and_needs_three():
    from embertide.engine.autopilot import Autopilot
    a = Autopilot.__new__(Autopilot)
    calm = [[0.0] * 30 + [0.05] * 10 for _ in range(3)]
    wild = [[0.0] * 10 + [1.0] * 30]
    assert a._usual_restless(calm) == [w for ws in calm for w in ws]          # nothing far out: all pooled as before
    assert a._usual_restless(calm + wild) == [w for ws in calm for w in ws]   # the far-out night is set aside
    assert a._usual_restless(calm[:1] + wild) == calm[0] + wild[0]            # too few nights to call one an outlier
    two_alike = [[0.1] * 40, [0.12] * 40, [0.0] * 40, [0.0] * 40]
    assert len(a._usual_restless(two_alike)) == 160                           # ordinary spread is not an outlier
