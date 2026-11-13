"""Notification simulation, part 1: every situation in which someone needs to know or act, one scenario each, end to end
(the real Notifier on the real controller, a fixed clock, fake providers that record what would have been sent).
Each checks the kind, who gets it (and who must not), the buttons, how often it may repeat, and what quiet hours do.
The table these come from is in docs/notifications.md."""

import pytest

from notify_rig import Rig

H = 3600


def tonight(snap, r, pid="p1"):
    return r.sch(snap, pid)


async def rig(tmp_path, start="2026-11-05T12:00", **kw):
    r = Rig(tmp_path, start, **kw)
    r.phone("p1", "fcm")
    r.phone("p2", "up")
    snap = await r.prime()
    r.n.clock.advance(700)                       # past the once-a-few-minutes look-ups the live tick just made
    return r, snap


# ───────────────────────── away at pre-condition time ─────────────────────────
async def test_away_at_precondition_time_goes_to_that_person_once_with_three_buttons(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="away", away=None)
    assert await r.observe(snap, at=sch["precool"] - 60) == []                    # not due yet
    out = await r.observe(snap, at=sch["precool"] + 60)
    assert [m.kind for m in out] == ["away_precool"]
    for i in range(1, 40):                                                         # the next 20 minutes of ticks: silence
        assert await r.observe(snap, at=sch["precool"] + 60 + i * 30) == []
    got = r.sent("away_precool")
    assert len(got) == 1 and got[0].person == "p1" and got[0].via == "fcm"
    assert got[0].actions == ["Wait an hour", "Off tonight", "Start anyway"] and got[0].acts == ["pause", "skip", "start_now"]
    assert not r.sent(person="p2")                                                 # the partner hears nothing
    assert "Right" not in got[0].title + got[0].body


async def test_back_home_before_precondition_time_means_no_message(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="away", away=None)
    await r.observe(snap, at=sch["precool"] - 600)
    r.put(snap, "p1", state="waiting")                                             # came home
    assert await r.observe(snap, at=sch["precool"] + 60) == []
    assert not r.got


async def test_marked_away_by_hand_is_never_asked_about_their_side(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="away", away={"since": r.now, "by": "p1"})
    await r.observe(snap, at=sch["precool"] + 60)
    assert not r.got


async def test_away_precondition_switch_off(tmp_path):
    r, snap = await rig(tmp_path)
    r.p1.settings.notify.away_precool = False
    r.put(snap, "p1", state="away", away=None)
    await r.observe(snap, at=tonight(snap, r)["precool"] + 60)
    assert not r.got


async def test_away_message_for_a_guest_side_is_never_sent(tmp_path):
    r, snap = await rig(tmp_path)
    r.put(snap, "p1", state="away", away=None, guest={"name": "Visitor"})
    await r.observe(snap, at=tonight(snap, r)["precool"] + 60)
    assert not r.got


# ───────────────────────── an empty side still running ─────────────────────────
async def test_empty_side_in_the_evening_is_sent_once_with_off_tonight(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="missed")
    await r.observe(snap, at=sch["bedtime"] - 600)
    await r.observe(snap, at=sch["bedtime"] - 300)
    got = r.sent("empty_side")
    assert len(got) == 1 and got[0].person == "p1" and got[0].actions == ["Off tonight"] and got[0].acts == ["skip"]
    assert not r.sent(person="p2")


async def test_empty_side_after_midnight_is_held_not_sent_and_dropped_when_you_come_to_bed(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    from datetime import datetime
    mid = datetime.fromtimestamp(sch["wake"], r.c.clock.tz).replace(hour=0, minute=0, second=0).timestamp()
    r.put(snap, "p1", state="missed")
    await r.observe(snap, at=mid + 1800)
    assert not r.sent("empty_side")
    assert [h["kind"] for h in r.c.store.get("notify_held")] == ["empty_side"]


# ───────────────────────── left on by hand ─────────────────────────
def left_on_snap(r, snap, since):
    r.put(snap, "p1", state="day", override={"manual": True, "set_ts": since, "until": None, "temp_c": 20.0, "delta_c": None,
                                              "phase_id": None, "end_ts": None}, target_c=20.0, flash=None)
    r.pv(snap, "p1")["presence"]["present"] = False


async def test_left_on_by_hand_waits_20_minutes_then_asks_once_with_turn_it_off(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T14:00")
    left_on_snap(r, snap, r.now)
    assert await r.observe(snap, at=r.now) == []
    assert await r.observe(snap, at=r.now + 19 * 60) == []
    out = await r.observe(snap, at=r.now + 2 * 60)
    assert [m.kind for m in out] == ["left_on"]
    assert await r.observe(snap, at=r.now + 10 * 60) == []
    got = r.sent("left_on")
    assert len(got) == 1 and got[0].person == "p1" and got[0].actions == ["Turn it off"] and got[0].acts == ["manual_off"]
    assert not r.sent(person="p2")


async def test_left_on_clears_when_someone_gets_in(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T14:00")
    left_on_snap(r, snap, r.now)
    await r.observe(snap, at=r.now + 10 * 60)
    r.pv(snap, "p1")["presence"]["present"] = True
    await r.observe(snap, at=r.now + 15 * 60)                          # in bed at 25 min: not asked
    r.pv(snap, "p1")["presence"]["present"] = False
    assert await r.observe(snap, at=r.now + 20 * 60) == []           # the clock starts over, not carried
    assert not r.got


async def test_left_on_again_the_same_afternoon_is_not_asked_twice_in_the_same_words(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T14:00")
    for i, hour in enumerate((14, 15, 16)):
        left_on_snap(r, snap, r.now + i)                              # a new by-hand session each time
        r.nt._empty_since.clear()
        await r.observe(snap, at=r.now)
        await r.observe(snap, at=r.now + 21 * 60)
        r.pv(snap, "p1")["presence"]["present"] = True
        await r.observe(snap, at=r.now + 60)
        r.at(f"2026-11-05T{hour + 1}:00")
    assert len(r.sent("left_on")) == 1                                       # the same wording within 6 hours is one message
    assert {e["why"] for e in r.events("notification_skipped")} == {"duplicate"}


# ───────────────────────── night not started ─────────────────────────
async def test_night_not_started_offers_start_now_once_and_ignores_quiet_hours(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="waiting", settle={"offer_start": True}, away=None)
    t = sch["bedtime"] + 2 * H
    out = await r.observe(snap, at=t)
    assert [m.kind for m in out] == ["not_started"]
    assert await r.observe(snap, at=t + 600) == []
    got = r.sent("not_started")
    assert len(got) == 1 and got[0].person == "p1" and got[0].actions == ["Start now"] and got[0].acts == ["start_now"]


async def test_night_not_started_not_in_the_last_hour_before_wake_or_when_away_or_started(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="waiting", settle={"offer_start": True}, away=None)
    assert await r.observe(snap, at=sch["wake"] - 1800) == []                    # too close to waking
    r.put(snap, "p1", state="waiting", settle={"offer_start": True}, away={"since": 1})
    assert await r.observe(snap, at=sch["bedtime"] + H) == []                    # marked away
    r.put(snap, "p1", state="rhythm", settle={"offer_start": True}, away=None)
    assert await r.observe(snap, at=sch["bedtime"] + H) == []                    # it started by itself: nothing to offer


# ───────────────────────── morning summary and the calibration check-in ─────────────────────────
@pytest.fixture
def good_night(monkeypatch):
    s = {"has_data": True, "in_bed_min": 470, "time_to_settle_min": 12, "hot_flashes": 0, "cold_flashes": 0, "times_up": 0}
    monkeypatch.setattr("embertide.engine.summary.summarize", lambda *a, **k: dict(s))
    monkeypatch.setattr("embertide.engine.away.night_verdict", lambda *a, **k: ("home", ""))


async def test_morning_summary_goes_to_the_sleeper_only_with_the_check_in_buttons_in_the_first_week(tmp_path, good_night):
    r, snap = await rig(tmp_path)
    r.c.autopilot.learning = lambda p, now: {"done": False, "need": 7, "have": 3}  # still in the calibration week
    sch = tonight(snap, r)
    r.put(snap, "p1", state="done")
    out = await r.observe(snap, at=sch["end"] + 600)
    assert [m.kind for m in out] == ["morning"]
    assert await r.observe(snap, at=sch["end"] + 1200) == []
    got = r.sent("morning")
    assert len(got) == 1 and got[0].person == "p1"
    assert got[0].actions == ["Too cold", "Just right", "Too warm"] and "7 h 50 min in bed" in got[0].body
    assert not r.sent(person="p2")                                                # nothing about Left reaches Right


async def test_morning_summary_after_calibration_has_no_buttons_and_is_late_proof(tmp_path, good_night):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="done")
    assert await r.observe(snap, at=sch["end"] + 5 * H) == []                      # more than 4 h late: not sent at all
    assert not r.got
    await r.observe(snap, at=sch["end"] + 3 * H)
    assert r.sent("morning")[0].actions == []


async def test_no_morning_summary_without_a_night_in_bed_or_when_marked_away(tmp_path, monkeypatch):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    monkeypatch.setattr("embertide.engine.summary.summarize", lambda *a, **k: {"has_data": True, "in_bed_min": 0})
    monkeypatch.setattr("embertide.engine.away.night_verdict", lambda *a, **k: ("home", ""))
    r.put(snap, "p1", state="done")
    await r.observe(snap, at=sch["end"] + 600)
    assert not r.got
    monkeypatch.setattr("embertide.engine.summary.summarize", lambda *a, **k: {"has_data": True, "in_bed_min": 400})
    from embertide.engine import away
    away.start(r.c, "p2", r.now, by="p2")
    r.put(snap, "p2", state="done")
    r.pv(snap, "p2")["schedule"] = dict(sch)
    await r.observe(snap, at=sch["end"] + 700)
    assert not r.sent("morning", person="p2")


async def test_morning_switch_off_and_guest_side(tmp_path, good_night):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.p1.settings.notify.morning = False
    r.put(snap, "p1", state="done")
    await r.observe(snap, at=sch["end"] + 600)
    r.p1.settings.notify.morning = True
    r.put(snap, "p1", state="done", guest={"name": "Visitor"})
    await r.observe(snap, at=sch["end"] + 900)
    assert not r.got


# ───────────────────────── the morning away check ─────────────────────────
async def test_morning_away_check_has_three_buttons_goes_only_to_that_person_and_is_once(tmp_path, monkeypatch):
    r, snap = await rig(tmp_path, "2026-11-06T09:00")
    calls = []

    def fake(c, p, now):
        calls.append(p.id)
        return {"kind": "away", "status": "asked", "why": "no one was in your side", "night": "2026-11-05"} if p.id == "p1" and len(calls) < 3 else None
    monkeypatch.setattr("embertide.engine.away.morning_check", fake)
    await r.observe(snap, at=r.now)
    got = r.sent("away_check")
    assert len(got) == 1 and got[0].person == "p1"
    assert got[0].actions == ["Away a few days", "Just that night", "I was home"]
    assert "Thursday" in got[0].title + got[0].body
    assert not r.sent(person="p2")
    await r.observe(snap, at=r.now + 700)
    assert len(r.sent("away_check")) == 1


async def test_back_home_question_when_marked_away_but_slept_in_their_side(tmp_path, monkeypatch):
    r, snap = await rig(tmp_path, "2026-11-06T09:00")
    monkeypatch.setattr("embertide.engine.away.morning_check",
                        lambda c, p, now: {"kind": "back", "status": "asked", "why": "your ring shows you asleep", "night": "2026-11-05"} if p.id == "p1" else None)
    r.put(snap, "p1", away={"since": 1})
    await r.observe(snap, at=r.now)
    got = r.sent("away_check")
    assert len(got) == 1 and "Thursday" in got[0].body and got[0].actions == ["Yes, I'm back", "Still away"]


# ───────────────────────── pet questions ─────────────────────────
def open_question(r, pid="p1", created=None):
    from embertide.engine import petq
    from embertide.models import Pet
    if not r.c.household.pets:
        r.c.household.pets.append(Pet(id="pet1", name="Biscuit", kind="cat", weight_kg=4, side="either"))
    t = created or r.now - 3600
    q = {"id": "q" + pid, "person": pid, "zone": "left", "group": "g", "kind": "pet", "created": t, "pets": ["pet1"],
         "spans": [{"start": int(t - 1800), "end": int(t - 600), "readings": []}], "status": "open", "answer": None, "answered_at": None,
         "pushed": False}
    petq._save(r.c.store, [q])
    return q


async def test_pet_question_in_the_day_has_yes_no_unsure_buttons_once(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T12:00")
    open_question(r)
    await r.observe(snap, at=r.now)
    got = r.sent("pet_question")
    assert len(got) == 1 and got[0].person == "p1"
    assert got[0].actions == ["Yes, it was Biscuit", "No, it was me", "Not sure"] and got[0].acts == ["pet_yes", "pet_me", "pet_unsure"]
    assert "Biscuit" in got[0].title
    await r.observe(snap, at=r.now + 600)
    assert len(r.sent("pet_question")) == 1
    assert not r.sent(person="p2")


@pytest.mark.parametrize("hour,expect", [(7, 0), (8, 1), (12, 1), (21, 0)])
async def test_pet_question_only_between_8_am_and_9_pm(tmp_path, hour, expect):
    r, snap = await rig(tmp_path, f"2026-11-05T{hour:02d}:30")
    open_question(r)
    await r.observe(snap, at=r.now)
    assert len(r.sent("pet_question")) == expect


async def test_pet_question_waits_for_the_morning_summary_and_rides_in_it(tmp_path, good_night):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    from datetime import datetime
    day = datetime.fromtimestamp(sch["end"], r.c.clock.tz)
    open_question(r, created=sch["end"] - 3 * H)
    r.put(snap, "p1", state="done")
    await r.observe(snap, at=sch["end"] + 600)
    assert not r.sent("pet_question")                                              # no separate ping
    morning = r.sent("morning")
    assert len(morning) == 1 and "Biscuit" in morning[0].body


async def test_pet_question_off_by_either_switch_and_never_for_a_guest(tmp_path):
    r, snap = await rig(tmp_path)
    open_question(r)
    r.p1.settings.notify.pet_question = False
    await r.observe(snap, at=r.now)
    r.p1.settings.notify.pet_question = True
    r.p1.settings.pet_questions = False
    await r.observe(snap, at=r.now + 600)
    r.p1.settings.pet_questions = True
    r.put(snap, "p1", guest={"name": "Visitor"})
    await r.observe(snap, at=r.now + 1200)
    assert not r.got


async def test_pet_question_is_not_asked_of_someone_marked_away(tmp_path):
    """Whatever lay on an away person's side wasn't about them: no question about their side (only the away check)."""
    r, snap = await rig(tmp_path)
    open_question(r)
    from embertide.engine import away
    away.start(r.c, "p1", r.now, by="p1")
    r.put(snap, "p1", away={"since": r.now})
    await r.observe(snap, at=r.now)
    assert not r.sent("pet_question")


async def test_pet_question_skips_cleanly_when_pet_was_deleted(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T12:00")
    open_question(r)
    r.c.household.pets = []
    await r.observe(snap, at=r.now)
    assert not r.sent("pet_question")


# ───────────────────────── bed trouble ─────────────────────────
async def test_bed_offline_five_minutes_in_the_day_goes_to_phones_that_want_it_once_per_outage(tmp_path):
    r, snap = await rig(tmp_path)
    snap["system"].update(online=False, detail="no broker")
    await r.observe(snap, at=r.now)
    assert await r.observe(snap, at=r.now + 200) == []                              # not yet
    out = await r.observe(snap, at=r.now + 150)
    assert [m.kind for m in out] == ["problem"]
    for i in range(20):
        assert await r.observe(snap, at=r.now + 300 * (i + 1)) == []
    got = r.sent("problem")
    assert sorted(g.person for g in got) == ["p1", "p2"] and all("Can't reach the bed" in g.title or "isn't answering" in g.title or "Lost contact" in g.title for g in got)
    assert got[0].actions == []


async def test_bed_offline_respects_the_problems_switch_per_person(tmp_path):
    r, snap = await rig(tmp_path)
    r.p2.settings.notify.problems = False
    snap["system"].update(online=False, detail="no broker")
    await r.observe(snap, at=r.now)
    await r.observe(snap, at=r.now + 301)
    assert [g.person for g in r.sent("problem")] == ["p1"]


async def test_bed_offline_while_someone_sleeps_is_held_for_the_morning_not_sent(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="rhythm")
    snap["system"].update(online=False, detail="no broker")
    await r.observe(snap, at=sch["bedtime"] + 2 * H)
    await r.observe(snap, at=sch["bedtime"] + 2 * H + 301)
    assert not r.sent("problem")
    assert [h["kind"] for h in r.c.store.get("notify_held")] == ["problem"]


async def test_frozen_sensor_during_a_night_is_reported_once_per_side_per_night(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="rhythm")
    r.pv(snap, "p1")["presence"]["stale"] = True
    t = sch["bedtime"] + 2 * H
    await r.observe(snap, at=t)
    assert await r.observe(snap, at=t + 599) == []
    out = await r.observe(snap, at=t + 601)
    assert [m.kind for m in out] == ["problem"] and "sensor" in out[0].title
    assert await r.observe(snap, at=t + 3600) == []
    # held (someone is in bed): it goes with the morning, not now
    assert not r.sent("problem") and len(r.c.store.get("notify_held")) == 1


async def test_frozen_sensor_is_not_reported_while_the_whole_bed_is_offline(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="rhythm")
    r.pv(snap, "p1")["presence"]["stale"] = True
    snap["system"]["online"] = False
    t = sch["bedtime"] + 2 * H
    await r.observe(snap, at=t)
    out = await r.observe(snap, at=t + 700)
    assert not [m for m in out if "sensor" in m.title]


async def test_bed_ignoring_commands_is_one_alert_after_20_minutes(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="waiting")
    t = sch["bedtime"] - 600
    r.c.unconfirmed_since = {r.p1.zone: t - 19 * 60}
    assert [m for m in await r.observe(snap, at=t) if m.kind == "problem"] == []
    r.c.unconfirmed_since = {r.p1.zone: t - 21 * 60}
    out = [m for m in await r.observe(snap, at=t + 60) if m.kind == "problem"]
    assert len(out) == 1 and "isn't taking changes on the left side" in out[0].title
    assert not [m for m in await r.observe(snap, at=t + 700) if m.kind == "problem"]


async def test_a_side_whose_update_keeps_failing_is_reported_once_a_night(tmp_path):
    r, snap = await rig(tmp_path)
    snap["system"]["tick_errors"] = {"p1": 119}
    assert [m for m in await r.observe(snap, at=r.now) if m.kind == "problem"] == []
    snap["system"]["tick_errors"] = {"p1": 120}
    out = [m for m in await r.observe(snap, at=r.now + 60) if m.kind == "problem"]
    assert len(out) == 1 and "isn't updating" in out[0].title
    assert not [m for m in await r.observe(snap, at=r.now + 600) if m.kind == "problem"]


# ───────────────────────── the bed's hub ─────────────────────────
def hub(snap, **kw):
    snap["system"]["hub"] = {"faults": kw.get("faults", []), "water_ok": kw.get("water_ok", True), "safety_ok": kw.get("safety_ok", True)}


async def test_hub_fault_goes_to_owner_phones_once_a_day_in_the_day(tmp_path):
    r, snap = await rig(tmp_path)
    r.nt.owner_persons = lambda: {"p1"}                                        # Left owns the account, Right doesn't
    hub(snap, faults=[{"code": "FAN", "name": "Fan problem", "zone": "left"}])
    await r.observe(snap, at=r.now)
    await r.observe(snap, at=r.now + 600)
    got = r.sent("hub")
    assert len(got) == 1 and got[0].person == "p1" and "Fan problem" in got[0].title + got[0].body
    assert not r.sent(person="p2")                                             # a non-owner is not told
    await r.observe(snap, at=r.now + 86400)                                    # next day, still faulty: told again
    assert len(r.sent("hub")) == 2


async def test_hub_low_water_is_one_message_not_two(tmp_path):
    r, snap = await rig(tmp_path)
    hub(snap, water_ok=False)
    await r.observe(snap, at=r.now)
    assert sorted(g.person for g in r.sent("hub")) == ["p1", "p2"]                 # no owner account is linked: both phones, one each
    assert "water" in r.sent("hub")[0].title.lower() + r.sent("hub")[0].body.lower()


async def test_hub_fault_while_asleep_is_held_but_a_failing_safety_check_is_not(tmp_path):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    r.put(snap, "p1", state="rhythm")
    hub(snap, faults=[{"code": "FAN", "name": "Fan problem", "zone": None}])
    await r.observe(snap, at=sch["bedtime"] + 2 * H)
    assert not r.sent("hub") and len(r.c.store.get("notify_held")) == 1
    hub(snap, safety_ok=False)
    await r.observe(snap, at=sch["bedtime"] + 2 * H + 60)
    assert len(r.sent("hub")) >= 1 and "safety" in r.sent("hub")[0].title + r.sent("hub")[0].body


async def test_hub_switch_off_silences_that_person(tmp_path):
    r, snap = await rig(tmp_path)
    r.p1.settings.notify.hub = False
    hub(snap, water_ok=False)
    await r.observe(snap, at=r.now)
    assert [g.person for g in r.sent("hub")] == ["p2"]


# ───────────────────────── Autopilot suggestions and guest mode never push ─────────────────────────
async def test_autopilot_suggestions_are_never_pushed(tmp_path):
    r, snap = await rig(tmp_path)
    from embertide.notify import CAPS
    assert "suggestion" not in CAPS
    # a suggestion waiting changes nothing about what is sent: there is no push path for it at all
    r.c.store.put("autopilot_suggestions", [{"id": "s1", "person": "p1", "title": "Make Deep 1 °F cooler?"}])
    await r.observe(snap, at=r.now)
    assert not r.got


async def test_nothing_is_sent_about_a_guest_or_their_side(tmp_path, good_night):
    r, snap = await rig(tmp_path)
    sch = tonight(snap, r)
    g = {"name": "Visitor"}
    r.put(snap, "p1", state="missed", guest=g, settle={"offer_start": True})
    r.pv(snap, "p1")["presence"]["stale"] = True
    r.c.unconfirmed_since = {}
    for t in (sch["precool"] + 60, sch["bedtime"] - 300, sch["bedtime"] + 2 * H, sch["end"] + 600):
        await r.observe(snap, at=t)
    assert not [x for x in r.got if x.person == "p1"]
