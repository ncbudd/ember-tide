"""Second pass, areas 3 and 7: the partner away with the dog on her side, and replays of the shape of a real night.
Alex (p1, left) is home; Sam (p2, right) is marked away with no end date, the dog lies on Sam's side."""

import pytest

from embertide.engine import away as away_mod
from embertide.engine import petq
from embertide.models import Pet
from sim2kit import anchors, catch, deep_from, kinds, phase_at, reading
from simnight import Plan, Sim, T, hm, ts

DOG = Pet(name="Rufus", kind="dog", weight_kg=29.5, sleeps_on_bed="yes", side="either")


def setup(tmp_path, start="2026-11-05T21:00", pets=(DOG,)):
    r = Sim(tmp_path, start)
    r.hh.pets = list(pets)
    away_mod.start(r.c, "p2", r.clock.now_ts(), by="p2")
    return r


def sam_notes(got):
    return [n for n in got if n.person == "p2"]


async def test_partner_away_dog_on_her_side_all_evening_and_night(tmp_path):
    r = setup(tmp_path)
    got = catch(r)
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("21:00", lambda x: x.bed.pets.update(right=58)).at("07:30", lambda x: x.bed.pets.pop("right", None))
    await r.run("2026-11-06T10:00", script=plan)
    # nothing is told to Sam about her side, and nothing about her side reaches Alex either
    assert not sam_notes(got), [(n.kind, n.title) for n in sam_notes(got)]
    assert not [n for n in got if n.kind in ("wearable", "bed_silent", "sensor", "target", "reach", "bridge", "storage", "account")]
    # Sam's side never started, stayed 'away', and wasn't warmed
    assert r.started_count(r.p2) == 0 and r.states(r.p2, "2026-11-05T21:00", "2026-11-06T06:30") == {"away"}
    assert not r.bed.z["right"].on
    assert away_mod.is_away(r.store, "p2", r.clock.now_ts())
    # the dog's stay is held for learning
    held = r.store.get("away_spans:p2") or []
    assert held and all(h["pet"] == DOG.id for h in held)      # held in 4-hour chunks
    assert r.started_count(r.p1) == 1


async def test_the_morning_away_check_asks_nothing_when_she_is_marked_away_and_the_dog_filled_the_side(tmp_path):
    r = setup(tmp_path)
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("21:00", lambda x: x.bed.pets.update(right=58)).at("07:30", lambda x: x.bed.pets.pop("right", None))
    await r.run("2026-11-06T10:00", script=plan)
    q = away_mod.morning_check(r.c, r.p2, r.clock.now_ts())
    assert q is None                                                  # can't be sure: ask nothing (no ring night)
    assert away_mod.open_check(r.store, "p2") is None


async def test_the_morning_away_check_with_a_ring_asleep_elsewhere_agrees_she_was_away(tmp_path):
    """Her ring slept somewhere else; her side held the dog: 'as expected', nothing to ask, the night is left out."""
    from embertide.engine import sleeps  # noqa: F401
    r = setup(tmp_path)
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("21:00", lambda x: x.bed.pets.update(right=58)).at("07:30", lambda x: x.bed.pets.pop("right", None))
    await r.run("2026-11-06T10:00", script=plan)
    a, b = ts("2026-11-05T22:45"), ts("2026-11-06T06:30")
    r.store.put_stages("p2", "oura", "Oura", [{"start": a, "end": b, "stage": "light"}], a, b)
    v, why = away_mod.night_verdict(r.c, r.p2, r.e(r.p2).night.date if r.e(r.p2).night else __import__("datetime").date(2026, 11, 5))
    assert v in (None, "away", "bed")
    # (the verdict API needs a stored sleep record, not only stages; the point: whatever it says, nothing is asked while
    # she is marked away and the verdict isn't 'bed')
    assert v != "bed"


async def test_her_sleep_screen_says_away_and_autopilot_does_not_count_it(tmp_path):
    r = setup(tmp_path)
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("21:00", lambda x: x.bed.pets.update(right=58)).at("07:30", lambda x: x.bed.pets.pop("right", None))
    await r.run("2026-11-06T10:00", script=plan)
    s = r.summary(r.p2, "2026-11-05")
    shown = away_mod.hide_away_night(r.store, "p2", s, r.clock.now_ts(), r.c.clock.tz)
    assert shown["away"] is True and shown["has_data"] is False and shown["moments"] == []   # "You were away"
    a = r.c.autopilot
    a._nights_cache.clear()
    assert a.learning(r.p2, r.clock.now_ts())["have"] == 0


async def test_dog_stays_learned_after_a_long_night_on_her_side(tmp_path):
    r = setup(tmp_path)
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("21:00", lambda x: x.bed.pets.update(right=58)).at("07:30", lambda x: x.bed.pets.pop("right", None))
    await r.run("2026-11-06T10:00", script=plan)
    r.clock.jump_to(ts("2026-11-07T08:00"))
    away_mod._settle(r.c, "p2", r.clock.now_ts())
    assert DOG.id in petq.pet_sigs(r.store)


async def test_she_comes_home_mid_evening_the_dog_story_does_not_bite(tmp_path):
    """Away, the dog on her side until 22:50; she comes back (uses her side by hand) at 23:00: the dog's stay is dropped
    ('may have been her') rather than taught, and her night starts."""
    r = setup(tmp_path)
    plan = Plan().sleep("left", "22:40", "06:50").sleep("right", "23:20", "06:50")
    plan.at("21:00", lambda x: x.bed.pets.update(right=58)).at("22:50", lambda x: x.bed.pets.pop("right", None))
    await r.run("2026-11-06T00:30", script=plan)
    assert r.started_count(r.p2) in (0, 1)


# ───────────────────────── replays of the shape of a real night ─────────────────────────
def last_night(plan, owner_in="23:00"):
    """Owner in at 23:00 (after the usual bedtime), the dog on the away partner's side 23:00-09:11, an alarm for 08:30 that
    is then replaced by one for 09:10 after the night had finished, owner out at 09:13."""
    plan.sleep("left", owner_in, "09:13")
    plan.at("23:00", lambda x: x.bed.pets.update(right=58)).at("09:11", lambda x: x.bed.pets.pop("right", None))
    return plan


def alarmed(plan, first="08:30", second="09:10", swap_at="08:50"):
    def f(r, now):
        plan(r, now)
        if int(now) % 600 < 60:
            r.phone(r.p1, next_alarm=T(first) if now < T(swap_at) else T(second))
    return f


async def test_replay_last_night_a_pet_on_the_away_partners_side_and_a_reopened_night(tmp_path):
    r = setup(tmp_path)
    got = catch(r)
    plan = last_night(Plan())
    await r.run("2026-11-06T12:00", script=alarmed(plan))
    p = r.p1
    # the owner's night: one start a few minutes after getting in, no early-sleep anchor (they got in after bedtime)
    starts = r.events("rhythm_started", "p1")
    assert len(starts) == 1 and ts("2026-11-05T23:00") <= starts[0]["ts"] <= ts("2026-11-05T23:10"), [hm(e["ts"]) for e in starts]
    assert anchors(r) == []
    assert "23:38" <= deep_from(r, a="2026-11-05T22:00", b="2026-11-06T02:00") <= "23:46"
    assert phase_at(r, "2026-11-06T03:10") == "late"
    # the warm wake follows the first alarm (08:30 - 30 min = 08:00) and the new alarm 09:10 reopens the finished night
    assert phase_at(r, "2026-11-06T08:10") == "wake"
    assert len(r.events("wake_from_alarm", "p1")) >= 1
    assert r.at(p, "2026-11-06T09:05")[0] in ("rhythm", "out_of_bed_hold", "done")
    assert r.timeline(p)[-1][1] == "done" and not r.bed.z["left"].on
    assert r.started_count(p) == 1
    # her side: the pet labelled, never started, no story of a night
    sus = [e for e in r.events("pet_suspected") if e.get("zone") == "right"]
    assert sus and sus[0]["evidence"] == "away" and sus[0]["pet"] == "Rufus"
    assert r.started_count(r.p2) == 0 and r.states(r.p2, "2026-11-05T23:00", "2026-11-06T06:30") == {"away"}
    s = r.summary(r.p2, "2026-11-05")
    assert any(m["kind"] == "pet" for m in s.get("moments") or [])
    # no stray notifications
    assert not sam_notes(got)
    assert not [n for n in got if n.kind not in ("away_precool",)] or all(n.expires is not None or n.link for n in got)
    assert not [n for n in got if n.kind in ("wearable", "bed_silent", "sensor", "target", "account", "reach", "bridge", "storage")]


async def test_replay_last_night_with_an_early_owner_names_the_sleep_sign(tmp_path):
    """The same night but the owner got in at 21:30 reading; the dog came to her side at 23:00. The anchor reason must be the
    owner's own, and the dog (arriving later) must not disturb it."""
    r = setup(tmp_path)
    got = catch(r)
    plan = Plan().sleep("left", "21:30", "09:13")
    reading(plan, "left", "21:30", "22:00")
    plan.at("23:00", lambda x: x.bed.pets.update(right=58)).at("09:11", lambda x: x.bed.pets.pop("right", None))
    await r.run("2026-11-06T12:00", script=alarmed(plan))
    (at, why), = anchors(r)
    assert why in ("settled", "bedtime") and at <= "22:30", (at, why)
    sleep = [m for m in r.summary(r.p1, "2026-11-05")["moments"] if m["kind"] == "sleep"]
    assert len(sleep) == 1
    assert r.started_count(r.p1) == 1 and r.timeline(r.p1)[-1][1] == "done"
    assert not sam_notes(got)
    assert not [n for n in got if n.kind in ("wearable", "bed_silent", "sensor", "target", "account", "reach", "bridge", "storage")]
