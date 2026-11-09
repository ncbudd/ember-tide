"""Rhythm edges: a very late bedtime (3 AM with a 7 AM wake), 1 AM, half an hour before wake, an afternoon nap, a
day-off rhythm. Work-night rhythm: bed 22:30, wake 07:00, pre-cool 45 min, wind-down, fall asleep (in bed), deep
(in bed + 40), late night (03:00), warm wake (wake − 30)."""

import pytest

from embertide.engine.rhythm import fitted_late, pick_rhythm, resolve
from embertide.engine.summary import summarize
from sim_edges import TZ, Rig, household, ts


def work_rhythm():
    hh = household()
    p = hh.people[0]
    from datetime import date
    d = date(2026, 11, 5)                  # a Thursday: a work night
    return pick_rhythm(p.rhythms, d), d


def starts(n):
    return {p.id: p.start for p in n.phases}


# ───────────── the plan itself ─────────────
def test_three_am_squeezes_the_night_to_fit_before_the_warm_wake():
    rh, d = work_rhythm()
    n = resolve(rh, d, TZ, in_bed=ts("2026-11-06T03:00"))
    s = starts(n)
    t0 = ts("2026-11-06T03:00")
    assert s["fall_asleep"] == t0
    assert t0 + 10 * 60 <= s["deep"] < t0 + 40 * 60          # still falls asleep first, just for less long
    assert s["deep"] + 60 * 60 <= s["late"] < s["wake"]        # deep gets real time; late night after it
    assert s["wake"] == ts("2026-11-06T06:30")                 # the warm wake stays put before 07:00
    order = [s[k] for k in ("fall_asleep", "deep", "late", "wake")]
    assert order == sorted(order) and len(set(order)) == 4     # nothing skipped
    assert fitted_late(rh, d, TZ, t0)


@pytest.mark.parametrize("hm", ["22:30", "23:00", "00:00", "01:00"])
def test_a_little_late_keeps_the_usual_layout(hm):
    rh, d = work_rhythm()
    day = "2026-11-05" if hm >= "12" else "2026-11-06"
    t0 = ts(f"{day}T{hm}")
    s = starts(resolve(rh, d, TZ, in_bed=t0))
    assert s["fall_asleep"] == t0 and s["deep"] == t0 + 40 * 60
    assert s["late"] == ts("2026-11-06T03:00")
    assert not fitted_late(rh, d, TZ, t0)


# ───────────── end to end ─────────────
def script_late(get_in: str, partner: bool = True):
    t_in = ts(get_in)

    def f(r, now):
        if partner:
            r.bed.set_presence("right", ts("2026-11-05T22:35") <= now < ts("2026-11-06T07:05"))
        r.bed.set_presence("left", t_in <= now < ts("2026-11-06T07:05"))
    return f


async def test_three_am_arrival_end_to_end(tmp_path):
    r = Rig(tmp_path, "2026-11-05T21:00")
    seen = {}

    def watch(rig, now):
        for hm in ("21:50", "22:50", "01:45", "02:30", "03:10", "03:40", "05:30", "06:45"):
            if abs(now - ts(("2026-11-05T" if hm >= "12" else "2026-11-06T") + hm)) < 1:
                seen[hm] = (rig.e(rig.p1).s.state, rig.e(rig.p1).s.phase_id, rig.bed.z["left"].on,
                            rig.e(rig.p2).s.state, rig.e(rig.p2).s.phase_id)
    await r.run("2026-11-06T07:30", script=script_late("2026-11-06T03:00"), watch=watch)
    # empty all evening: pre-cools until the "pre-cool an empty side until" time (01:30 + 30 min hold), then off
    assert seen["21:50"][:3] == ("waiting", "wind_down", True)
    assert seen["01:45"][:3] == ("waiting", "wind_down", True)
    assert seen["02:30"][:3] == ("missed", None, False)
    # the night starts properly and gets every phase, squeezed
    assert seen["03:10"][:3] == ("rhythm", "fall_asleep", True)
    assert seen["03:40"][1] == "deep" and seen["05:30"][1] == "late" and seen["06:45"][1] == "wake"
    started = r.events("rhythm_started", "p1")
    assert len(started) == 1 and started[0]["fitted"] and abs(started[0]["in_bed"] - ts("2026-11-06T03:00")) <= 60
    # the partner who went to bed on time: their own night, untouched
    assert seen["22:50"][3:] == ("rhythm", "fall_asleep") and seen["03:10"][3] == "rhythm"
    assert len(r.events("rhythm_started", "p2")) == 1
    # the summary says it was a late night, and Autopilot doesn't learn the person's normal from it
    s = summarize(r.store, r.p1, r.e(r.p1).night.date, TZ, r.clock.now_ts())
    assert s["late_by_min"] == pytest.approx(270, abs=5)
    m = next(x for x in s["moments"] if x["kind"] == "rhythm_started")
    assert m["fitted"] and "fitted" in m["text"]
    assert s["times_up"] == 0
    s2 = summarize(r.store, r.p2, r.e(r.p2).night.date, TZ, r.clock.now_ts())
    assert s2["late_by_min"] is None
    # one "pre-cooled but empty" note for the late one, nothing for the partner
    # (at 2:30 AM it waits for the morning: quiet hours from midnight, notify.py)
    assert r.told("p1").count("empty_side") + r.told("p2").count("empty_side") == 1


async def test_one_am_arrival_keeps_late_night_at_three(tmp_path):
    r = Rig(tmp_path, "2026-11-05T21:00", notifier=False)
    await r.run("2026-11-06T03:20", script=script_late("2026-11-06T01:00"))
    n = r.e(r.p1).night
    s = starts(n)
    assert abs(s["fall_asleep"] - ts("2026-11-06T01:00")) <= 60 and s["late"] == ts("2026-11-06T03:00")
    assert r.e(r.p1).s.phase_id == "late"
    assert not r.events("rhythm_started", "p1")[0].get("fitted")


async def test_half_an_hour_before_wake_does_not_start_a_night(tmp_path):
    r = Rig(tmp_path, "2026-11-06T05:00", notifier=False)
    await r.run("2026-11-06T06:58", script=script_late("2026-11-06T06:30"))
    assert not r.e(r.p1).s.started and not r.bed.z["left"].on       # a lie-down before getting up: the side stays off
    assert r.e(r.p2).s.phase_id == "wake"                              # the partner's warm wake as usual


async def test_partner_alone_on_the_right_is_never_taken_for_an_echo(tmp_path):
    # one sleeper on the right, the left pad picking them up a moment later: the right starts, the left doesn't
    r = Rig(tmp_path, "2026-11-05T22:00", notifier=False)
    await r.run("2026-11-06T02:00", script=lambda rig, now: rig.bed.set_presence("right", now >= ts("2026-11-05T22:35")))
    assert r.e(r.p2).s.started and r.e(r.p2).s.state == "rhythm"
    assert not r.e(r.p1).s.started


async def test_weekend_afternoon_nap_changes_nothing(tmp_path):
    r = Rig(tmp_path, "2026-11-07T14:30", notifier=False)            # a Saturday
    nap = lambda rig, now: rig.bed.set_presence("left", ts("2026-11-07T15:00") <= now < ts("2026-11-07T16:00"))  # noqa: E731
    await r.run("2026-11-07T16:30", script=nap)
    assert r.e(r.p1).s.state == "day" and not r.e(r.p1).s.started
    assert not r.bed.z["left"].on and not r.bed.z["right"].on
    assert not r.events("rhythm_started")
    from datetime import date
    for d in (date(2026, 11, 6), date(2026, 11, 7)):                  # neither night's summary takes the nap in
        s = summarize(r.store, r.p1, d, TZ, r.clock.now_ts())
        assert not s or not s.get("in_bed_min")


async def test_day_off_rhythm_three_am_fits_before_its_later_wake(tmp_path):
    r = Rig(tmp_path, "2026-11-07T21:00", notifier=False)            # Saturday night: day-off rhythm, wake 09:00

    def f(rig, now):
        rig.bed.set_presence("right", ts("2026-11-07T23:35") <= now)
        rig.bed.set_presence("left", ts("2026-11-08T03:00") <= now)
    await r.run("2026-11-08T08:40", script=f)
    n = r.e(r.p1).night
    assert n.rhythm_id == "weekends"
    s = starts(n)
    assert s["wake"] == ts("2026-11-08T08:30")
    assert s["fall_asleep"] < s["deep"] < s["late"] < s["wake"] and s["late"] > ts("2026-11-08T04:30")
    assert r.e(r.p1).s.phase_id == "wake"
    assert r.events("rhythm_started", "p1")[0]["fitted"]


def test_late_nights_are_not_learned_from(tmp_path, monkeypatch):
    from embertide.engine import summary as S
    r_hh = household()
    from embertide.clock import FixedClock
    from embertide.drivers.sim import SimBed
    from embertide.engine.controller import Controller
    from embertide.store import Store
    clock = FixedClock(ts("2026-11-10T12:00"), "America/New_York")
    c = Controller(r_hh, SimBed(clock), Store(tmp_path / "x.db"), clock)
    base = {"has_data": True, "in_progress": False, "in_bed_min": 400}
    usual = [dict(base, date=f"d{i}", in_bed_at=ts(f"2026-11-0{i}T22:40")) for i in range(1, 6)]
    late = dict(base, date="late", in_bed_at=ts("2026-11-07T03:00"), late_by_min=270)
    monkeypatch.setattr(S, "recent", lambda *a, **k: usual + [late])
    assert [n["date"] for n in c.autopilot._nights(r_hh.people[0], clock.now_ts())] == [f"d{i}" for i in range(1, 6)]
    # someone who really goes to bed around 1 AM (whatever the rhythm says) keeps all their nights
    c.autopilot.__dict__.pop("_nights_cache", None)
    owl = [dict(base, date=f"o{i}", in_bed_at=ts(f"2026-11-0{i + 1}T01:{10 + i}"), late_by_min=170) for i in range(1, 6)]
    monkeypatch.setattr(S, "recent", lambda *a, **k: owl)
    assert len(c.autopilot._nights(r_hh.people[0], clock.now_ts())) == 5
