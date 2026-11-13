"""The tuning guide over simulated weeks: bedtime drift, wake drift, lights, late events, nights away, device tips,
and applying a schedule proposal."""

import pytest

from embertide.engine.guide import apply_schedule, tuning_guide
from embertide.engine.insights import apply_proposal
from test_context import CtxNight, cal, light, phone

WEEK = ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]       # Sun-Thu: weeknights, bedtime 22:15


def nxt(d):
    from datetime import date, timedelta
    return (date.fromisoformat(d) + timedelta(days=1)).isoformat()


async def live(n, nights, in_at="23:00", out_at="06:30"):
    for d in nights:
        await n.until(f"{d}T{in_at}", dt=180)
        n.present("left", True)
        await n.until(f"{nxt(d)}T{out_at}", dt=180)
        n.present("left", False)
    await n.until(f"{nxt(nights[-1])}T13:00", dt=300)


def ids(items):
    return {i["id"]: i for i in items}


async def test_late_bedtimes_suggest_moving_bedtime_and_lights_note(tmp_path):
    n = CtxNight(tmp_path, "2026-10-31T12:00", [light(off="23:25", on="06:40")])
    await live(n, WEEK)
    g = ids(tuning_guide(n.c, n.p1, n.clock.now_ts()))
    b = g["guide-bedtime-weeknights"]
    assert b["proposal"] == {"rhythm_id": "weeknights", "schedule": "bedtime", "from": "22:15", "to": "23:00"}
    assert "min after your set bedtime" in b["title"] and b["title"].startswith("Weeknights")
    assert "guide-lights-reading" in g                                   # lights off ~25 min after getting in
    assert "guide-wake-weeknights" not in g                              # gets up on time
    assert "guide-add-room" in g                                         # no room sensor configured
    what = apply_schedule(n.p1, b["proposal"])
    assert "bedtime 22:15 → 23:00" in what and n.p1.rhythms[0].schedule.bedtime == "23:00"


async def test_on_time_sleeper_gets_no_schedule_change(tmp_path):
    n = CtxNight(tmp_path, "2026-10-31T12:00")
    await live(n, WEEK, in_at="22:20")
    g = ids(tuning_guide(n.c, n.p1, n.clock.now_ts()))
    assert not any(k.startswith("guide-bedtime") or k.startswith("guide-wake") for k in g)


async def test_early_riser_suggests_earlier_wake(tmp_path):
    n = CtxNight(tmp_path, "2026-10-31T12:00")
    await live(n, WEEK, in_at="22:20", out_at="05:45")
    w = ids(tuning_guide(n.c, n.p1, n.clock.now_ts()))["guide-wake-weeknights"]
    assert w["proposal"]["to"] == "05:45"


async def test_late_event_nights_are_left_out_and_noted(tmp_path):
    # in bed on time, except one night out at a concert until 23:45
    n = CtxNight(tmp_path, "2026-10-31T12:00", [cal(("19:00", "23:45", "Concert"))])
    for d in WEEK:
        if d != "2026-11-03":
            n.mgr.configs[0].params["events"] = []
        else:
            n.mgr.configs[0].params["events"] = [["19:00", "23:45", "Concert"]]
        await live(n, [d], in_at="00:10" if d == "2026-11-03" else "22:20")
    g = ids(tuning_guide(n.c, n.p1, n.clock.now_ts()))
    assert "guide-late-events" in g and "2026-11-03" in g["guide-late-events"]["evidence"][0]
    assert not any(k.startswith("guide-bedtime") for k in g)            # the late night doesn't skew bedtime


async def test_away_evenings_noted(tmp_path):
    n = CtxNight(tmp_path, "2026-10-31T12:00", [phone(away=[["18:00", "22:50"]], naps=False)])
    await live(n, WEEK, in_at="23:00")
    g = ids(tuning_guide(n.c, n.p1, n.clock.now_ts()))
    assert "guide-away" in g and len(g["guide-away"]["evidence"]) == 5
    assert not any(k.startswith("guide-bedtime") for k in g)            # away nights aren't "ordinary"


async def test_learning_note_while_calibrating(tmp_path):
    n = CtxNight(tmp_path, "2026-10-31T12:00")
    g = ids(tuning_guide(n.c, n.p1, n.clock.now_ts()))
    assert g["guide-learning"]["title"].endswith("0 of 7")


def test_apply_rejects_bad_proposals():
    from embertide.seed import default_household
    p = default_household().people[0]
    with pytest.raises(KeyError):
        apply_proposal(p, {"rhythm_id": "weeknights", "phase_id": "deep", "field": "id", "to": "x"})
    with pytest.raises(KeyError):
        apply_proposal(p, {"rhythm_id": "weeknights", "phase_id": "deep", "field": "temp_c", "to": 99})
    with pytest.raises(KeyError):
        apply_schedule(p, {"rhythm_id": "weeknights", "schedule": "bedtime", "to": "25:99"})
    assert "17.5" in apply_proposal(p, {"rhythm_id": "weeknights", "phase_id": "deep", "field": "temp_c", "to": "17.5"})


async def test_late_workouts_and_caffeine_slow_settling(tmp_path, monkeypatch):
    """Day data from Health Connect: workouts within 3 h of bed on 3 nights, settling 20 min slower on those."""
    import embertide.engine.guide as G
    from test_scenarios import ts
    n = CtxNight(tmp_path, "2026-11-07T12:00")
    days = ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    fake = []
    for i, d in enumerate(days):
        inb = ts(f"{d}T22:20")
        late = i in (0, 2, 4)
        fake.append({"date": d, "has_data": True, "in_bed_at": inb, "out_at": ts(f"{nxt(d)}T06:30"),
                     "time_to_settle_min": 35 if late else 14})
        if late:
            n.store.samples_many([(inb - 2 * 3600, "wear:p1:exercise_min:Watch", 45.0)])
        n.store.samples_many([(inb - 3 * 3600, "wear:p1:caffeine_mg:Phone", 30.0)])      # a little, every day: no effect
    monkeypatch.setattr(G, "recent", lambda *a, **k: fake)
    g = ids(tuning_guide(n.c, n.p1, n.clock.now_ts()))
    assert "guide-exercise" in g and "35 min" in g["guide-exercise"]["detail"]
    assert "guide-caffeine" not in g


def test_schedule_times_in_text_follow_the_clock_style():
    from embertide.engine.guide import _say_hm
    assert _say_hm("00:00", "12") == "12:00 AM" and _say_hm("23:15", "12") == "11:15 PM"
    assert _say_hm("00:00", "24") == "00:00" and _say_hm("07:30", "24") == "07:30"
