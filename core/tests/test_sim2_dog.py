"""Second pass, area 2: dog identity x sleep sign x settle gate. Sam (p2) sleeps on the right, which has a bed radar; a large
dog may lie on either side; Alex (p1) on the left. Work-night rhythm: bed 22:30."""

import pytest

from sim2kit import anchors, catch, hm, kinds
from simnight import Plan, Sim, ts
from test_sim_night_dog_identity import DOG, phone_away_from_bed, pet_sus, rig, world


def own_hr(x):
    s = x.c.presence.sides.get("right")
    return s.hr if s is not None and s.hr else None


async def test_dog_at_2130_phone_away_watch_connected_but_silent_does_not_start_the_night(tmp_path):
    """The watch is connected but asleep (sends nothing), the phone is in another room, the radar sees the dog: before the
    usual bedtime nothing starts. The dog is gone at 22:15."""
    r = rig(tmp_path, "2026-11-05T20:30")
    got = catch(r)
    step = world(Plan(), dog=("2026-11-05T21:30", "2026-11-05T22:15"), watch=None, phone=phone_away_from_bed)
    await r.run("2026-11-05T22:25", script=step)
    assert pet_sus(r) and r.started_count(r.p2) == 0
    assert not [n for n in got if n.person == "p2"]


async def test_dog_all_evening_past_bedtime_with_a_silent_watch(tmp_path):
    """The dog stays on the side past the usual bedtime, watch silent, phone away: after bedtime the phone alone isn't enough,
    so the night may start (documented). What matters: it is one start, no sleep anchor from the dog's stillness, and
    nobody is told anything about it."""
    r = rig(tmp_path, "2026-11-05T20:30")
    got = catch(r)
    step = world(Plan(), dog=("2026-11-05T21:30", "2026-11-06T00:30"), watch=None, phone=phone_away_from_bed)
    await r.run("2026-11-06T01:00", script=step)
    assert r.started_count(r.p2) <= 1
    assert not anchors(r, "p2") or anchors(r, "p2")[0][1] in ("bedtime",)
    assert not [n for n in got if n.person == "p2"]


@pytest.mark.xfail(reason="JUDGEMENT: the dog stays on Sam's side past the usual bedtime with the radar seeing it, the phone clearly "
                          "away from the bed and not charging and the watch silent: the night starts at 22:33 and heats an empty "
                          "side until the out-of-bed hold at 00:32. Documented (phone alone never counts after bedtime): is that "
                          "the right trade against an owner whose phone is in the kitchen?", strict=True)
async def test_dog_all_evening_past_bedtime_does_not_run_the_night(tmp_path):
    r = rig(tmp_path, "2026-11-05T20:30")
    step = world(Plan(), dog=("2026-11-05T21:30", "2026-11-06T00:30"), watch=None, phone=phone_away_from_bed)
    await r.run("2026-11-06T01:00", script=step)
    assert r.started_count(r.p2) == 0


async def test_owner_gets_in_at_2210_phone_in_hand_watch_answers_after_12_minutes(tmp_path):
    """Early in bed, the phone says 'not by the bed' (in hand), the watch is silent for 12 minutes, then reads their own
    heart rate. The start isn't pushed back beyond a sensible time and no pet is blamed once the watch says it's them."""
    r = rig(tmp_path, "2026-11-05T21:00")
    got = catch(r)
    late_watch = lambda x: own_hr(x) if x.clock.now_ts() >= ts("2026-11-05T22:22") else None  # noqa: E731
    step = world(Plan().sleep("right", "22:10", "06:50"), watch=late_watch, phone=phone_away_from_bed)
    await r.run("2026-11-05T23:30", script=step)
    starts = r.events("rhythm_started", "p2")
    assert len(starts) == 1
    assert ts("2026-11-05T22:10") <= starts[0]["ts"] <= ts("2026-11-05T22:35"), hm(starts[0]["ts"])
    assert not [n for n in got if n.person == "p2"]
    assert not [e for e in r.events("pet_suspected") if e.get("zone") == "right" and e["ts"] > ts("2026-11-05T22:30")]


async def test_owner_early_with_the_phone_in_hand_and_no_watch_at_all(tmp_path):
    """No watch ever: the phone alone calls it the pet for 5 min at most? Measure how late the real owner's night starts."""
    r = rig(tmp_path, "2026-11-05T21:00")
    step = world(Plan().sleep("right", "22:10", "06:50"), watch=None, phone=phone_away_from_bed)
    await r.run("2026-11-05T23:30", script=step)
    starts = r.events("rhythm_started", "p2")
    assert len(starts) == 1
    assert starts[0]["ts"] <= ts("2026-11-05T22:35"), f"started only at {hm(starts[0]['ts'])}"


async def test_dog_does_not_set_the_sleep_anchor(tmp_path):
    """Sam is in bed reading from 21:10 (started, waiting for a sign), gets up at 21:35 and the dog takes the side. The dog's
    stillness must never be taken for Sam settling; the usual bedtime stays the only way the wait ends."""
    r = rig(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("right", "21:10", "06:50", ups=[("21:35", "23:50")])
    plan.at("21:10", lambda x: x.bed.reading.update(right=True)).at("21:35", lambda x: x.bed.reading.pop("right", None))
    step = world(plan, dog=("2026-11-05T21:40", "2026-11-05T23:45"), phone=phone_away_from_bed)
    await r.run("2026-11-06T00:30", script=step)
    a = anchors(r, "p2")
    assert not a or a[0][1] == "bedtime", a


async def test_dog_alongside_a_settling_owner_does_not_break_the_sign(tmp_path):
    """Sam reads then settles; the dog lies on the empty LEFT side of Alex's empty pad the whole time. Sam's anchor is the
    settle check; nothing started on the dog's side."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.hh.pets = [DOG]
    plan = Plan().sleep("right", "21:10", "06:50")
    plan.at("21:00", lambda x: x.bed.pets.update(left=115))
    await r.run("2026-11-06T00:30", script=plan)
    (at, why), = anchors(r, "p2")
    assert why == "settled" and at < "22:20"


@pytest.mark.xfail(reason="JUDGEMENT: stale phone counts as unknown, so a dog on the side from 21:30 starts Sam's night at 21:38 "
                          "and a fresh 'phone away from the bed' report 17 min later can't undo it (it ends in an out-of-bed hold at "
                          "22:22). Should a fresh away report cancel a start that is minutes old and was made with no watch?", strict=True)
async def test_phone_stale_two_hours_then_fresh_with_a_dog_on_the_side(tmp_path):
    """The phone last reported at 19:30 (stale by 21:30). The dog lies on the side 21:30-22:20; the phone reports again at
    21:55 (away from the bed). Stale counts as unknown, so the dog starts the night at ~21:36; a fresh 'away' report
    can't end a running night. Recorded behaviour: ask the owner whether a fresh report should be able to cancel a start
    that is only minutes old."""
    r = rig(tmp_path, "2026-11-05T19:30")
    phone_away_from_bed(r)
    r.clock.jump_to(ts("2026-11-05T21:00"))
    last = {"p": False}

    def phone(x):
        if x.clock.now_ts() >= ts("2026-11-05T21:55") and not last["p"]:
            last["p"] = True
            phone_away_from_bed(x)
    step = world(Plan(), dog=("2026-11-05T21:30", "2026-11-05T22:20"), phone=None)

    def both(x, now):
        step(x, now)
        phone(x)
    await r.run("2026-11-05T22:25", script=both)
    assert r.started_count(r.p2) == 0, "the dog started the night while the phone was stale"


async def test_watch_says_me_for_a_few_minutes_then_disconnects(tmp_path):
    """Sam in bed from 21:10 reading, the watch agrees for 10 minutes then disconnects (nothing more all night); the phone is
    away from the dial. Once the watch said 'me' no pet is suspected and the night is Sam's all the way."""
    r = rig(tmp_path, "2026-11-05T21:00")
    got = catch(r)
    gone = lambda x: own_hr(x) if x.clock.now_ts() < ts("2026-11-05T21:25") else None  # noqa: E731
    plan = Plan().sleep("right", "21:10", "06:50")
    plan.at("21:10", lambda x: x.bed.reading.update(right=True)).at("21:50", lambda x: x.bed.reading.pop("right", None))
    step = world(plan, watch=gone, phone=phone_away_from_bed)
    await r.run("2026-11-06T07:00", script=step)
    assert not pet_sus(r)
    assert r.started_count(r.p2) == 1
    assert "out_of_bed" not in r.states(r.p2, "2026-11-05T22:00", "2026-11-06T06:30")
    assert not [n for n in got if n.person == "p2"]
