"""Second pass, area 1: the sleep sign (engine/sleepsign.py, night.py) meeting everything else.
Alex is on the left (p1), Sam on the right (p2). Work-night rhythm: bed 22:30, wake 07:00, Deep = in bed + 40 min."""

import random

import pytest

from embertide.engine.summary import summarize
from embertide.models import Pet
from sim2_sweep import fires, reading_rows
from sim2kit import anchors, catch, deep_from, kinds, phase_at, reading
from simnight import Plan, Sim, hm, ts

CAT = Pet(name="Biscuit", kind="cat", weight_kg=4.5, sleeps_on_bed="yes", side="either")


async def test_early_in_bed_with_a_pet_on_the_partners_side(tmp_path):
    """Alex reads from 21:10 and settles; a cat lies on Sam's (empty) side. Alex's night is Alex's: the cat's side
    neither starts a night nor moves Alex's anchor, and nobody is told anything."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.hh.pets = [CAT]
    got = catch(r)
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "21:40")
    plan.at("21:00", lambda x: x.bed.pets.update(right=165)).at("23:45", lambda x: (x.bed.pets.pop("right", None), setattr(x.bed, "echo", False))).at("23:50", lambda x: setattr(x.bed, "echo", True))
    await r.run("2026-11-06T00:30", script=plan)
    (at, why), = anchors(r)
    assert why == "settled" and "21:50" <= at <= "22:15"
    assert r.started_count(r.p2) == 0 and r.started_count(r.p1) == 1
    assert deep_from(r) < "23:00"
    assert not any(k not in ("pre_condition",) and k in ("wearable", "bed_silent", "sensor", "target", "account", "reach", "bridge", "storage")
                   for k in kinds(got))


async def test_early_in_bed_with_the_partner_away(tmp_path):
    from embertide.engine import away as away_mod
    r = Sim(tmp_path, "2026-11-05T21:00")
    away_mod.start(r.c, "p2", r.clock.now_ts(), by="p2")
    got = catch(r)
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    await r.run("2026-11-06T00:30", script=plan)
    assert anchors(r) == [("22:30", "bedtime")]
    assert r.started_count(r.p2) == 0 and r.states(r.p2, "2026-11-05T22:00", "2026-11-06T07:00") == {"away"}
    assert not [n for n in got if n.person == "p2"]


async def test_early_in_bed_then_restart_and_a_moved_alarm(tmp_path):
    """Server down 21:50-21:56 while waiting, the phone alarm moved to 05:30 after the restart: the anchor, Deep and the
    earlier warm wake all come out right."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    await r.run("2026-11-05T21:50", script=plan)
    r.crash()
    await r.run("2026-11-05T21:56", script=plan)
    r.boot()
    plan.at("22:05", lambda x: x.phone(x.p1, next_alarm=ts("2026-11-06T05:30")))
    await r.run("2026-11-06T06:00", script=plan)
    assert anchors(r) == [("22:30", "bedtime")] and r.started_count(r.p1) == 1
    assert phase_at(r, "2026-11-06T04:55") != "wake" and phase_at(r, "2026-11-06T05:05") == "wake"
    assert "23:10" <= deep_from(r) <= "23:14"


async def test_phone_bedtime_mode_reported_late_anchors_when_the_server_heard(tmp_path):
    """Bedtime mode came on at 22:00 but the phone only told the server at 22:20 (the phone sends no "since"): the sign is stamped when the server heard, the safe (late) side."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    plan.at("21:05", lambda x: x.phone(x.p1, bedtime_mode=False))
    plan.at("22:20", lambda x: x.phone(x.p1, bedtime_mode=True, bedtime_since=ts("2026-11-05T22:00")))
    await r.run("2026-11-06T00:30", script=plan)
    assert anchors(r) == [("22:20", "bedtime_mode")]
    assert "23:00" <= deep_from(r) <= "23:06"


async def test_phone_bedtime_mode_stale_is_not_a_sign(tmp_path):
    """Bedtime mode was reported on at 21:30 and the phone then went silent for hours (dead battery): once the report is
    stale it is no sign; the usual bedtime stays the backstop (no anchor can appear from a report hours old)."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    plan.at("21:05", lambda x: x.phone(x.p1, bedtime_mode=False))
    # the phone says bedtime mode is on, but "since" is before they got in: never counts
    plan.at("21:05", lambda x: x.phone(x.p1, bedtime_mode=True, bedtime_since=ts("2026-11-05T20:00")))
    await r.run("2026-11-06T00:30", script=plan)
    assert anchors(r) == [("22:30", "bedtime")]


async def test_getting_up_for_the_bathroom_before_the_anchor(tmp_path):
    """In at 21:10 to read, up 21:40-21:46 (short), back to reading until 22:15, then asleep. One night, no restart; the
    sign needs a full still half hour after coming back, so it can't fire before ~22:20 and the backstop is 22:30."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50", ups=[("21:40", "21:46")]), "left", "21:10", "22:15")
    await r.run("2026-11-06T00:30", script=plan)
    assert r.started_count(r.p1) == 1 and not r.events("rhythm_restarted", "p1")
    (at, why), = anchors(r)
    assert "22:15" <= at <= "22:30", (at, why)
    assert phase_at(r, "2026-11-05T21:30") == "fall_asleep"


async def test_getting_up_for_the_bathroom_then_straight_to_sleep(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "21:10", "06:50", ups=[("21:30", "21:36")])
    await r.run("2026-11-06T00:30", script=plan)
    assert r.started_count(r.p1) == 1
    (at, why), = anchors(r)
    assert why == "settled" and at <= "22:30"
    assert "not_in_bed" not in r.states(r.p1, "2026-11-05T22:30", "2026-11-06T06:00")


async def test_early_in_bed_across_the_weekend_rhythm_switch_at_midnight(tmp_path):
    """Friday night (weekend rhythm, bed 23:30): in at 22:00, reading until 00:30, then asleep. The night belongs to Friday
    all the way through midnight; the backstop is the weekend bedtime and Deep follows it."""
    r = Sim(tmp_path, "2026-11-06T21:00")
    plan = reading(Plan("2026-11-06").sleep("left", "22:00", "08:50"), "left", "22:00", "00:30")
    await r.run("2026-11-07T02:00", script=plan)
    assert r.started_count(r.p1) == 1 and not r.events("rhythm_restarted", "p1")
    (at, why), = anchors(r)
    assert why in ("bedtime", "settled") and at <= "23:30"
    assert r.e(r.p1).night.rhythm_id == "weekends"
    assert deep_from(r, a="2026-11-06T21:00", b="2026-11-07T03:00") is not None


async def test_thursday_night_running_into_friday_keeps_the_work_rhythm(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "08:50"), "left", "21:10", "01:00")
    await r.run("2026-11-06T02:00", script=plan)
    assert r.e(r.p1).night.rhythm_id == "weeknights"
    assert anchors(r) == [("22:30", "bedtime")]


# ───────────────────────── the settle check against realistic readers ─────────────────────────
def _rate(**kw):
    rng = random.Random(7)
    n = hit = 0
    for h0 in (62, 70, 80):
        for sd in (1.5, 3):
            for move in (0.02, 0.06, 0.10):
                for _ in range(4):
                    n += 1
                    hit += fires(reading_rows(h0, kw.get("amp", 0), kw.get("tau", 15), kw.get("slope", 0), sd, move, rng)) is not None
    return hit / n


def test_a_steady_reader_is_never_taken_for_asleep():
    assert _rate(amp=0, slope=0) == 0.0


def test_a_reader_relaxing_a_little_is_rarely_taken_for_asleep():
    """3 bpm of settling-in over the first half hour, nothing after: the rate stays low."""
    assert _rate(amp=3, slope=0) <= 0.1


def test_a_reader_relaxing_six_bpm_is_not_taken_for_asleep():
    assert _rate(amp=6, slope=0) <= 0.25


def test_a_true_sleeper_is_still_found_fast():
    """Falls asleep at 20 min: heart rate drops 10 bpm and stays down, no movement: found within the hour."""
    rng = random.Random(3)
    rows = []
    for i in range(int(3 * 3600 / 15)):
        t = i * 15.0
        hr = 70 - (10 if t > 1200 else 0) + rng.gauss(0, 1.5)
        rows.append({"ts": t, "occupied": 1, "status": 0, "hr": round(hr)})
    f = fires(rows)
    assert f is not None and f <= 55


# ───────────────────────── "Fix this night" after an anchored night ─────────────────────────
async def _anchored_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    got = catch(r)
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "21:40")
    await r.run("2026-11-06T09:30", script=plan)
    return r, got


def _sleep_line(s):
    return [m for m in s["moments"] if m["kind"] == "sleep"]


async def test_fixing_the_in_bed_time_keeps_the_story_and_stats_consistent(tmp_path):
    from embertide.engine import compare as C
    r, _ = await _anchored_night(tmp_path)
    before = r.summary(r.p1, "2026-11-05")
    (m,) = _sleep_line(before)
    assert m["why"] == "settled"
    # "I was actually in bed from 21:30, and got up at 07:05"
    fix = C.clean_fix({"in_bed_at": ts("2026-11-05T21:30"), "out_at": ts("2026-11-06T07:05")},
                      ts("2026-11-05T20:00"), ts("2026-11-06T11:00"), [])
    r.store.put("night_fix:p1:2026-11-05", fix)
    s = summarize(r.store, r.p1, r.e(r.p1).night.date, r.c.clock.tz, r.clock.now_ts())
    assert s["in_bed_at"] == ts("2026-11-05T21:30") and s["raw"]["in_bed_at"] < ts("2026-11-05T21:30")
    (m2,) = _sleep_line(s)
    assert m2["ts"] == m["ts"] and m2["text"] == m["text"]            # the story line is the same sign, untouched
    assert s["fell_asleep_est"] is None or s["fell_asleep_est"] >= s["in_bed_at"]
    assert m2["ts"] >= s["in_bed_at"], "the story says they settled before the corrected time they got in"
    assert s["in_bed_min"] > 0


async def test_fixing_the_in_bed_time_to_after_the_anchor_is_told_sensibly(tmp_path):
    """The owner says they only got in at 22:20 (the bed saw the dog, say); the 'You settled around 9:5x' story line would
    sit before getting in."""
    from embertide.engine import compare as C
    r, _ = await _anchored_night(tmp_path)
    fix = C.clean_fix({"in_bed_at": ts("2026-11-05T22:20")}, ts("2026-11-05T20:00"), ts("2026-11-06T11:00"), [])
    r.store.put("night_fix:p1:2026-11-05", fix)
    s = summarize(r.store, r.p1, r.e(r.p1).night.date, r.c.clock.tz, r.clock.now_ts())
    sl = _sleep_line(s)
    assert not sl or sl[0]["ts"] >= s["in_bed_at"], f"{hm(sl[0]['ts'])} settled but in bed only from {hm(s['in_bed_at'])}"


# ───────────────────────── the same reader through the real controller ─────────────────────────
class RelaxingReader:
    """Reading, heart rate easing down `amp` bpm over about `tau` minutes after getting in, then steady, with a page turn now
    and then."""

    def __init__(self, bed, zone: str, amp: float, tau_min: float, since: float) -> None:
        import math
        from embertide.drivers.base import SensorFrame
        self.rng = random.Random(5)
        orig = bed._sensor

        def sensor(name, t, done):
            f = orig(name, t, done)
            if name == zone and bed.z[name].occupied and bed.reading.get(name):
                hr = 72 - amp * (1 - math.exp(-max(0.0, t - since) / 60 / tau_min)) + self.rng.uniform(-1.5, 1.5)
                return SensorFrame(status=self.rng.choice([0] * 19 + [4]), hr=round(hr), br=15, clock=f.clock)
            return f
        bed._sensor = sensor


@pytest.mark.parametrize("amp", [0, 3])
async def test_a_reader_easing_down_a_little_waits_for_bedtime(tmp_path, amp):
    r = Sim(tmp_path, "2026-11-05T21:00")
    RelaxingReader(r.bed, "left", amp, 15, ts("2026-11-05T21:10"))
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    await r.run("2026-11-05T23:00", dt=15, script=plan)             # the real strip writes a row every 15 s
    assert anchors(r) == [("22:30", "bedtime")]


async def test_a_reader_easing_down_ten_bpm_waits_for_bedtime(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    RelaxingReader(r.bed, "left", 10, 10, ts("2026-11-05T21:10"))
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    await r.run("2026-11-05T23:00", dt=15, script=plan)             # the real strip writes a row every 15 s
    assert anchors(r) == [("22:30", "bedtime")]
