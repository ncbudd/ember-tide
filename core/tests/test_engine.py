from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from embertide.clock import FixedClock
from embertide.drivers.base import SensorFrame
from embertide.drivers.sim import SimBed
from embertide.engine.controller import Controller
from embertide.engine.night import NightEngine
from embertide.engine.presence import PresenceFilter
from embertide.engine.rhythm import night_date, resolve, target_at
from embertide.seed import default_household
from embertide.store import Store
from embertide.units import f_to_c

TZ = ZoneInfo("America/New_York")


def ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


def person(i=0):
    return default_household().people[i]


def test_night_date_rolls_at_noon():
    assert night_date(datetime(2026, 11, 7, 3, 0, tzinfo=TZ)).isoformat() == "2026-11-06"
    assert night_date(datetime(2026, 11, 6, 23, 0, tzinfo=TZ)).isoformat() == "2026-11-06"


def test_resolve_anchors_and_ramp():
    p = person()
    rh = p.rhythms[0]
    n = resolve(rh, night_date(datetime(2026, 11, 5, 23, tzinfo=TZ)), TZ, in_bed=ts("2026-11-05T22:24"))
    starts = {ph.id: ph.start for ph in n.phases}
    assert starts["wind_down"] == ts("2026-11-05T21:30")
    assert starts["fall_asleep"] == ts("2026-11-05T22:24")
    assert starts["deep"] == ts("2026-11-05T23:04")
    assert starts["late"] == ts("2026-11-06T03:00")
    assert starts["wake"] == ts("2026-11-06T06:00")
    # halfway through the 20-minute ramp into Deep
    v, ph = target_at(n, ts("2026-11-05T23:14"))
    assert ph.id == "deep"
    assert v == pytest.approx((f_to_c(66) + f_to_c(63)) / 2, abs=0.02)
    assert target_at(n, ts("2026-11-06T06:46"))[0] is None   # off after wake + 15


def test_early_in_bed_pulls_wind_down_back():
    rh = person().rhythms[0]
    n = resolve(rh, night_date(datetime(2026, 11, 5, 23, tzinfo=TZ)), TZ, in_bed=ts("2026-11-05T21:05"))
    s = [ph.start for ph in n.phases]
    assert s == sorted(s)
    assert n.phases[0].start <= ts("2026-11-05T21:05")


def frames(l, r, clock):
    return {"left": SensorFrame(status=l[0], hr=l[1], br=l[2], clock=(clock,)),
            "right": SensorFrame(status=r[0], hr=r[1], br=r[2], clock=(clock,))}


def test_presence_confirm_blip_and_echo():
    pf = PresenceFilter()
    t = 1000.0
    # a half-second blip on the right never confirms
    pf.update(t, frames((5, None, None), (0, 70, 14), 1))
    pf.update(t + 0.5, frames((5, None, None), (5, None, None), 2))
    assert pf.present("right") is False
    # left sleeper, echoed exactly on the right
    for i in range(40):
        pf.update(t + 10 + i, frames((0, 60, 14), (0, 60, 14), 10 + i) if i else frames((0, 60, 14), (5, None, None), 10))
    assert pf.present("left") is True
    assert pf.present("right") is False          # echo detected after 20 s
    assert pf.sides["right"].echo or not pf.sides["right"].occupied


def test_presence_stale_clock_is_unknown():
    pf = PresenceFilter()
    for i in range(30):
        pf.update(2000 + i, frames((0, 60, 14), (5, None, None), 1))   # clock never changes
    assert pf.present("left") is None


def test_rhythm_starts_only_after_confirm_inside_window():
    e = NightEngine(person(), TZ)
    t = ts("2026-11-05T20:30")
    assert e.tick(t, True) is None and e.s.state == "day"            # before pre-condition, in bed early, outside window
    t = ts("2026-11-05T21:40")
    e.tick(t, False)
    assert e.s.state == "waiting" and e.s.target_c == pytest.approx(f_to_c(68), abs=0.01)
    e.tick(t + 60, True)
    e.tick(t + 120, True)
    assert not e.s.started                                          # only 1 minute so far
    e.tick(t + 60 + 181, True)
    assert e.s.started and e.s.in_bed == t + 60
    assert e.s.phase_id == "fall_asleep"


def test_out_of_bed_hold_then_off():
    e = NightEngine(person(), TZ)
    t = ts("2026-11-05T22:30")
    e.tick(t, True)
    e.tick(t + 200, True)
    assert e.s.started
    e.tick(t + 3600, False)
    assert e.s.state == "out_of_bed_hold" and e.s.target_c is not None
    e.tick(t + 3600 + 31 * 60, False)
    assert e.s.state == "out_of_bed" and e.s.target_c is None
    e.tick(t + 3600 + 40 * 60, True)
    assert e.s.state == "rhythm"


def test_hot_flash_then_ease_back():
    e = NightEngine(person(), TZ)
    t = ts("2026-11-06T01:30")
    e.s.started, e.s.in_bed, e.s.key = True, ts("2026-11-05T22:24"), "2026-11-05"
    base = e.tick(t, True)
    e.flash("hot", t)
    assert e.tick(t + 60, True) == pytest.approx(12.8)
    mid = e.tick(t + 15 * 60 + 150, True)                            # halfway through the 5-minute ease
    assert mid == pytest.approx((12.8 + base) / 2, abs=0.05)
    assert e.tick(t + 21 * 60, True) == pytest.approx(base, abs=0.01)
    assert e.s.flash is None


def test_off_tonight_ends_a_running_flash():
    for action in ("skip", "pause"):
        e = NightEngine(person(), TZ)
        t = ts("2026-11-06T01:30")
        e.s.started, e.s.in_bed, e.s.key = True, ts("2026-11-05T22:24"), "2026-11-05"
        e.tick(t, True)
        e.flash("hot", t)
        assert e.tick(t + 60, True) == pytest.approx(12.8)
        e.tonight(t + 120, action)
        assert e.tick(t + 180, True) is None, action
        assert e.s.flash is None


def test_flash_still_runs_while_the_rhythm_is_off():
    e = NightEngine(person(), TZ)
    t = ts("2026-11-06T19:00")                                       # evening, before the night starts
    e.tick(t, False)
    e.flash("hot", t)
    assert e.tick(t + 60, False) == pytest.approx(12.8)


def test_nap_turned_on_by_hand_ends_on_its_timer():
    e = NightEngine(person(), TZ)
    t = ts("2026-11-06T14:00")                                       # afternoon: the rhythm has the side off
    assert e.tick(t, False) is None
    e.adjust(t, temp_c=20.0, minutes=45)
    assert e.tick(t + 60, True) == pytest.approx(20.0)
    assert e.s.override.manual
    assert e.tick(t + 44 * 60, True) == pytest.approx(20.0)
    assert e.tick(t + 46 * 60, True) is None and e.s.override is None


def test_by_hand_session_without_timer_hands_over_to_the_rhythm():
    e = NightEngine(person(), TZ)
    t = ts("2026-11-06T18:00")
    e.tick(t, False)
    e.adjust(t, temp_c=24.0)
    assert e.tick(t + 60, False) == pytest.approx(24.0)
    later = ts("2026-11-06T23:30")                                   # the night has started on schedule/presence
    e.s.started, e.s.in_bed = True, ts("2026-11-06T22:30")
    v = e.tick(later, True)
    assert e.s.override is None and v is not None and v != pytest.approx(24.0)


def test_warmer_while_off_tonight_resumes_rather_than_a_by_hand_session():
    e = NightEngine(person(), TZ)
    t = ts("2026-11-06T01:30")
    e.s.started, e.s.in_bed, e.s.key = True, ts("2026-11-05T22:24"), "2026-11-05"
    e.tick(t, True)
    e.tonight(t, "skip")
    assert e.tick(t + 60, True) is None
    e.adjust(t + 120, delta_c=0.5)
    assert not e.s.override.manual
    assert e.tick(t + 180, True) is not None


def test_flash_extend_respects_max():
    e = NightEngine(person(), TZ)
    t = ts("2026-11-06T02:00")
    e.flash("cold", t)
    for _ in range(20):
        e.extend_flash(t)
    assert e.s.flash.end - t <= 60 * 60


def test_override_until_next_phase_expires():
    e = NightEngine(person(), TZ)
    t = ts("2026-11-05T22:30")
    e.tick(t, True)
    e.tick(t + 200, True)
    base = e.tick(t + 600, True)
    e.adjust(t + 600, delta_c=-1)
    assert e.tick(t + 660, True) == pytest.approx(base - 1, abs=0.3)
    e.tick(ts("2026-11-05T23:30"), True)                             # now in Deep
    assert e.s.override is None


async def test_controller_drives_sim_through_a_night(tmp_path):
    clock = FixedClock(ts("2026-11-05T21:00"), "America/New_York")
    bed = SimBed(clock)
    bed.echo = True
    bed.blips = False
    store = Store(tmp_path / "t.db")
    c = Controller(default_household(), bed, store, clock)
    import embertide.engine.controller as mod
    mod.MIN_CMD_GAP_S = 0
    for _ in range(int(10.5 * 60)):                                  # 21:00 → 07:30 in 1-minute steps
        bed._tick()
        await c.tick()
        for t in list(c._inflight.values()):
            await t
        clock.advance(60)
    evs = store.events(limit=500)
    started = [e for e in evs if e["kind"] == "rhythm_started"]
    assert {e["person"] for e in started} == {"p1", "p2"}
    left = store.history("left", ts("2026-11-05T21:00"))
    deep = [h for h in left if ts("2026-11-06T00:30") <= h["ts"] <= ts("2026-11-06T02:00")]
    assert deep and all(abs(h["setpoint"] - f_to_c(63)) < 0.15 for h in deep)
    assert left[-1]["on"] == 0                                        # off after wake


def test_in_bed_before_window_counts_from_window_start():
    e = NightEngine(person(), TZ)
    e.tick(ts("2026-11-05T20:40"), True)
    e.tick(ts("2026-11-05T21:04"), True)
    assert e.s.started and e.s.in_bed == ts("2026-11-05T21:00")


async def test_night_summary_after_simulated_night(tmp_path):
    from datetime import date
    from embertide.engine.summary import recent, summarize
    clock = FixedClock(ts("2026-11-05T21:00"), "America/New_York")
    bed = SimBed(clock)
    bed.blips = False
    store = Store(tmp_path / "s.db")
    hh = default_household()
    c = Controller(hh, bed, store, clock)
    import embertide.engine.controller as mod
    mod.MIN_CMD_GAP_S = 0
    for i in range(int(10.5 * 60 * 4)):           # 15-second steps
        bed._tick()
        await c.tick()
        for t in list(c._inflight.values()):
            await t
        if i == 4 * 60 * 5:                        # ~2 AM: a hot flash on the right
            c.engine_for("p2").flash("hot", clock.now_ts())
        clock.advance(15)
    s = summarize(store, hh.people[1], date(2026, 11, 5), TZ, clock.now_ts(), series=True)
    assert s["has_data"] and s["hot_flashes"] == 1
    assert s["times_up"] == 1 and 4 <= s["up_minutes"] <= 8       # the scripted 2:12 bathroom trip
    assert s["in_bed_at"] is not None and s["fell_asleep_est"] is not None
    assert s["series"] and recent(store, hh.people[1], TZ, clock.now_ts(), 3)


async def test_stage_estimate_tracks_simulated_truth(tmp_path):
    from embertide.engine.stages import agreement, estimate
    clock = FixedClock(ts("2026-11-05T22:00"), "America/New_York")
    bed = SimBed(clock)
    bed.blips = False
    bed.echo = False
    store = Store(tmp_path / "st.db")
    c = Controller(default_household(), bed, store, clock)
    import embertide.engine.controller as mod
    mod.MIN_CMD_GAP_S = 0
    truth = []
    for _ in range(int(8 * 60 * 4)):
        bed._tick()
        await c.tick()
        st = bed.true_stage("left", clock.now_ts())
        if st:
            if truth and truth[-1]["stage"] == st:
                truth[-1]["end"] = clock.now_ts() + 15
            else:
                truth.append({"start": clock.now_ts(), "end": clock.now_ts() + 15, "stage": st})
        clock.advance(15)
    est = estimate(store.history("left", ts("2026-11-05T21:00"), clock.now_ts()))
    a = agreement(truth, est)
    assert est and a is not None and a > 0.55, a


def test_projection_while_waiting_starts_from_now():
    from embertide.engine.rhythm import target_at
    e = NightEngine(person(), TZ)
    t = ts("2026-11-06T01:30")                 # long after the usual 10:15 bedtime, not in bed yet
    e.tick(t, False)
    assert e.s.state == "waiting"
    n = e.projected(t)
    phases = {p.id: p.start for p in n.phases}
    # 1:30 with a 6:30 wake is a short night: "Deep" comes sooner than the usual 40 min (squeezed to fit, see
    # test_rhythm_edges), but still after "Fall asleep"
    assert phases["fall_asleep"] == t and t + 15 * 60 < phases["deep"] <= t + 40 * 60
    v, ph = target_at(n, t + 60)
    assert ph.id == "fall_asleep"              # not "deep" as if bed had started at 10:15
    assert e.next_info(t)["phase"] == "deep"


def test_settle_detected_on_sparse_rows(tmp_path):
    from datetime import date
    from embertide.engine.summary import summarize
    store = Store(tmp_path / "sp.db")
    p = person()
    t0 = ts("2026-11-05T22:30")
    rows = []
    for k in range(0, 8 * 60, 2):                     # one row every 2 minutes (sparse)
        t = t0 + k * 60
        hr = 70 if k < 20 else 56
        rows.append((t, p.zone, 1, 19.0, 19.2, 1, hr, 14, 0 if k >= 20 else 4, 19.0, "deep", 1, p.id))
    store.telemetry(rows)
    s = summarize(store, p, date(2026, 11, 5), TZ, t0 + 9 * 3600)
    assert s["fell_asleep_est"] is not None and 15 <= s["time_to_settle_min"] <= 30
