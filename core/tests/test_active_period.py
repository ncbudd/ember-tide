"""An active stretch in bed (~35 min): both pads report moving / turned over / moving briefly, heart 90-140, breathing
20-35, sometimes both people on one side; then they settle and fall asleep (one gets up to the bathroom). It must
read as "active, not sleeping yet": no Autopilot learning or nudges, no pet or echo flags, no out-of-bed hold, the
night starts once they settle, and the story doesn't call it restless sleep or getting up."""

import random

import pytest

from embertide.engine import watch as W
from embertide.engine.active import ActiveDetector
from embertide.engine.presence import SideState
from embertide.engine.summary import summarize
from embertide.models import Pet
from sim_edges import TZ, ActiveBed, Rig, ts

CAT = Pet(id="c1", name="Biscuit", kind="cat", weight_kg=4.5)


def world(a0: str, a1: str, in_l: str, in_r: str, one_side=False, bath=("2026-11-06T00:30", "2026-11-06T00:36"),
          watch=False):
    A0, A1, IL, IR, B0, B1 = (ts(x) for x in (a0, a1, in_l, in_r, *bath))
    rnd = random.Random(3)

    def f(r, now):
        act = A0 <= now < A1
        r.bed.set_presence("left", now >= IL and not (act and one_side) and not (B0 <= now < B1))
        r.bed.set_presence("right", now >= IR)
        r.bed.active = {"left": act and not one_side, "right": act}
        if watch and int(now) % 60 == 0 and now >= IL:
            pad = r.c.presence.sides["left"].hr
            hr = rnd.randint(95, 135) if act else (pad or 60) + rnd.randint(-2, 2)
            W.record(r.store, "p1", [(now, hr)], [(now, 30)] if act else [], now)        # arm movement counted as steps
            r.store.sample(now, "wear:p1:skin_temp:W", round((34.9 if act else 34.0) + rnd.uniform(-0.05, 0.05), 2))
    return f


def household_with(pets=(), mode="act"):
    from sim_edges import household
    hh = household()
    hh.pets = list(pets)
    for p in hh.people:
        p.settings.autopilot.learn_skip = True
        p.settings.autopilot.mode = mode
    return hh


def story(r, p):
    return summarize(r.store, p, r.e(p).night.date, TZ, r.clock.now_ts())


# ───────────── the detector on its own ─────────────
def side(status, hr, occupied=True):
    return SideState(occupied=occupied, raw_occupied=occupied, status=status if occupied else 5, hr=hr if occupied else None)


def feed(det, seq, t0=0.0, dt=15, known=None, fast=False):
    out = []
    for i, (l, r) in enumerate(seq):
        out.append(det.update(t0 + i * dt, {"left": l, "right": r}, known=known, fast_pets=fast))
    return out


def test_two_people_active_then_calm():
    det, rnd = ActiveDetector(), random.Random(1)
    calm = [(side(0, 62), side(0, 66))] * 12                                     # got in, 3 min
    busy = [(side(rnd.choice([4, 6, 7, 0]), rnd.randint(95, 135)), side(rnd.choice([4, 6, 7]), rnd.randint(95, 135)))
            for _ in range(120)]
    after = [(side(0, 60), side(0, 63))] * 40
    ch = feed(det, calm + busy + after)
    assert ch.count("started") == 1 and ch.count("ended") == 1
    assert 12 + 8 <= ch.index("started") <= 12 + 25                              # a few minutes in, not at once
    assert ch.index("ended") >= 12 + 120 + 15                                    # after ~5 calm minutes


def test_both_on_one_side_counts():
    det, rnd = ActiveDetector(), random.Random(2)
    seq = [(side(0, 64), side(0, 70))] * 12 + [(side(5, None, occupied=False), side(rnd.choice([4, 6, 7]), rnd.randint(95, 130)))
                                              for _ in range(40)]
    assert "started" in feed(det, seq)


def test_cat_alone_on_the_bed_is_not_active():
    det, rnd = ActiveDetector(), random.Random(3)
    seq = [(side(5, None, occupied=False), side(rnd.choice([0, 0, 4, 6]), rnd.randint(130, 150))) for _ in range(80)]
    assert "started" not in feed(det, seq, fast=True)


def test_one_restless_sleeper_beside_a_calm_partner_is_not_active():
    # a hot or unsettled sleeper (Autopilot's job) next to a partner lying still: not this
    det, rnd = ActiveDetector(), random.Random(4)
    seq = [(side(0, 60), side(0, 66))] * 12 + [(side(rnd.choice([4, 6, 0]), rnd.randint(88, 100)), side(0, 58)) for _ in range(80)]
    assert "started" not in feed(det, seq)


def test_runs_out_after_two_hours():
    det, rnd = ActiveDetector(), random.Random(5)
    seq = [(side(0, 62), side(0, 66))] * 12 + [(side(rnd.choice([4, 6, 7]), rnd.randint(95, 130)), side(rnd.choice([4, 6, 7]), rnd.randint(95, 130)))
                                              for _ in range(int(2.5 * 3600 / 15))]
    ch = feed(det, seq)
    assert ch.count("started") == 1 and ch.count("ended") == 1                  # and doesn't start again straight away


# ───────────── end to end ─────────────
async def test_after_bedtime_with_a_cat_and_a_watch(tmp_path):
    r = Rig(tmp_path, "2026-11-05T22:00", bed_cls=ActiveBed, hh=household_with([CAT]))
    hold = []
    await r.run("2026-11-06T01:30", dt=15,
                script=world("2026-11-05T23:30", "2026-11-06T00:05", "2026-11-05T22:35", "2026-11-05T22:40", watch=True),
                watch=lambda rig, now: hold.append((rig.e(rig.p1).s.state, rig.e(rig.p2).s.state)))
    started, ended = r.events("active_started"), r.events("active_ended")
    assert len(started) == 1 and len(ended) == 1
    assert ts("2026-11-05T23:30") < started[0]["ts"] <= ts("2026-11-05T23:40") and started[0]["person"] is None
    assert ts("2026-11-06T00:05") < ended[0]["ts"] <= ts("2026-11-06T00:20")
    assert not r.events("pet_suspected") and not r.events("pet_question")      # nobody was the cat
    assert not r.events("adjust") and not [d for d in r.events("autopilot") if d["status"] in ("applied", "suggested")]
    assert all(s == ("rhythm", "rhythm") for s in hold[-200:-100])             # nights kept running through it
    assert r.c.presence._follower is None                                      # no echo decided from it
    for p in (r.p1, r.p2):
        s = story(r, p)
        assert s["active_min"] == pytest.approx(37, abs=6)
        act = [m for m in s["moments"] if m["kind"] == "active"]
        assert len(act) == 1 and act[0]["text"] == "Active, not sleeping yet"
        assert s["time_to_settle_min"] < 35                                    # settling counted from 22:35, as before
        assert s["hr_avg"] < 75                                                # the fast hearts aren't averaged in
    assert story(r, r.p1)["times_up"] == 1                                     # the bathroom trip after it, yes


async def test_before_the_night_starts_it_waits_until_they_settle(tmp_path):
    r = Rig(tmp_path, "2026-11-05T21:30", bed_cls=ActiveBed, hh=household_with())
    deep = {}

    def watch(rig, now):
        for p in (rig.p1, rig.p2):
            if rig.e(p).s.phase_id == "deep" and p.id not in deep:
                deep[p.id] = now
    await r.run("2026-11-06T00:30", dt=15,
                script=world("2026-11-05T22:00", "2026-11-05T22:35", "2026-11-05T21:58", "2026-11-05T21:58"), watch=watch)
    end = r.events("active_ended")[0]["ts"]
    for p in (r.p1, r.p2):
        assert r.e(p).s.in_bed >= end - 60                                     # the night counts from when they settled
        assert deep[p.id] >= end + 35 * 60                                     # "Deep" didn't run during it
        s = story(r, p)
        assert s["time_to_settle_min"] is not None and s["time_to_settle_min"] <= 30


async def test_both_on_one_side_nobody_got_up(tmp_path):
    r = Rig(tmp_path, "2026-11-05T22:00", bed_cls=ActiveBed, hh=household_with([CAT]))
    states = []
    await r.run("2026-11-06T00:25", dt=15,
                script=world("2026-11-05T23:30", "2026-11-06T00:05", "2026-11-05T22:35", "2026-11-05T22:40", one_side=True),
                watch=lambda rig, now: states.append(rig.e(rig.p1).s.state))
    assert "out_of_bed" not in states and r.bed.z["left"].on                   # the left side kept its night
    assert not r.events("pet_suspected")
    s = story(r, r.p1)
    assert s["times_up"] == 0 and not any(m["kind"] == "out_of_bed" for m in s["moments"])


async def test_a_too_hot_tap_during_it_still_counts_gently(tmp_path):
    r = Rig(tmp_path, "2026-11-05T22:00", bed_cls=ActiveBed, hh=household_with())
    tapped = []

    def script(rig, now):
        world("2026-11-05T23:30", "2026-11-06T00:05", "2026-11-05T22:35", "2026-11-05T22:40")(rig, now)
        if not tapped and now >= ts("2026-11-05T23:45"):
            tapped.append(now)
            rig.store.event(now, "feel", "p1", "app", feel="too_hot", phase=rig.e(rig.p1).s.phase_id)
    await r.run("2026-11-05T23:55", dt=15, script=script)
    adj = r.events("adjust", "p1")
    assert len(adj) == 1 and adj[0]["delta_c"] == pytest.approx(-0.56) and adj[0]["source"] == "autopilot"


async def test_a_hot_restless_sleeper_is_still_autopilots_job(tmp_path):
    # the lab's sleeper who runs warm: restless and a faster heart when the side is too warm. Never "active".
    from lab import Lab, Sleeper
    lab = Lab(tmp_path, "2026-11-05T20:00", sleepers={"left": Sleeper(pref_c={"*": -2.5}, restless=0.1)})
    await lab.nights(2)
    assert not lab.events("active_started")
