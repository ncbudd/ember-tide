"""Notification simulation, part 4: wording. Every kind of notification the triggers really produce, in each of the three
tones, read as the phone would show it: no missing or broken template, no unfilled {placeholders}, a sensible length,
names and numbers kept, plain words, and alerts that stay plain in every tone."""

import re

import pytest

from embertide import voice
from embertide.notify import Action, Message
from notify_rig import Rig
from test_sim_notify_triggers import left_on_snap, open_question

H = 3600
TONES = ("calm", "friendly", "cheeky")
BANNED = ("tow" "er", "pre-cool", "precool", "{", "}", "None", "nan ", "undefined", "TODO", "  ")


async def start(tmp_path, tone, at="2026-11-05T12:00"):
    r = Rig(tmp_path, at)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    for p in r.c.household.people:
        p.settings.tone = tone
    snap = await r.prime()
    r.n.clock.advance(700)
    return r, snap


def good_night(monkeypatch, in_bed=470):
    monkeypatch.setattr("embertide.engine.summary.summarize",
                        lambda *a, **k: {"has_data": True, "in_bed_min": in_bed, "time_to_settle_min": 12, "hot_flashes": 1, "cold_flashes": 0, "times_up": 2})
    monkeypatch.setattr("embertide.engine.away.night_verdict", lambda *a, **k: ("home", ""))


# Each situation: (kind that must come out, who gets it, an async function that creates it)
async def s_away_precool(r, snap):
    r.put(snap, "p1", state="away", away=None)
    await r.observe(snap, at=r.sch(snap, "p1")["precool"] + 60)


async def s_empty_side(r, snap):
    r.put(snap, "p1", state="missed")
    await r.observe(snap, at=r.sch(snap, "p1")["bedtime"] - 300)


async def s_left_on(r, snap):
    left_on_snap(r, snap, r.now)
    await r.observe(snap, at=r.now)
    await r.observe(snap, at=r.now + 21 * 60)


async def s_not_started(r, snap):
    r.put(snap, "p1", state="waiting", settle={"offer_start": True}, away=None)
    await r.observe(snap, at=r.sch(snap, "p1")["bedtime"] + 2 * H)


async def s_morning(r, snap):
    r.c.autopilot.learning = lambda p, now: {"done": False}
    r.nt.send(Message("problem", "Can't reach the bed", "No word.", None, urgent=False))
    r.put(snap, "p1", state="done")
    await r.observe(snap, at=r.sch(snap, "p1")["end"] + 600)


async def s_pet_question(r, snap):
    open_question(r)
    await r.observe(snap, at=r.now)


async def s_away_check(r, snap, monkeypatch=None):
    from embertide.engine import away
    away.morning_check = lambda c, p, now: {"kind": "away", "status": "asked", "why": "no one was in your side", "night": "2026-11-04"} if p.id == "p1" else None
    r.n.clock.jump_to(r.now + 86400)
    await r.observe(snap, at=r.now)


async def s_back_check(r, snap):
    from embertide.engine import away
    away.morning_check = lambda c, p, now: {"kind": "back", "status": "asked", "why": "your ring shows you asleep", "night": "2026-11-04"} if p.id == "p1" else None
    r.put(snap, "p1", away={"since": 1})
    r.n.clock.jump_to(r.now + 86400)
    await r.observe(snap, at=r.now)


async def s_offline(r, snap):
    snap["system"].update(online=False, detail="no answer")
    await r.observe(snap, at=r.now)
    await r.observe(snap, at=r.now + 301)


async def s_stale(r, snap):
    for p in r.c.household.people:
        p.settings.notify.morning = False                 # no morning summary to carry it: the "While you slept" note
    for pid in ("p1", "p2"):
        r.put(snap, pid, state="rhythm")
    r.pv(snap, "p1")["presence"]["stale"] = True
    t = r.sch(snap, "p1")["bedtime"] + 2 * H
    await r.observe(snap, at=t)
    await r.observe(snap, at=t + 700)                    # held: both are asleep
    for pid in ("p1", "p2"):
        r.put(snap, pid, state="done")
    r.pv(snap, "p1")["presence"]["stale"] = False
    await r.observe(snap, at=r.sch(snap, "p1")["wake"] + 1800)


async def s_hub(r, snap):
    snap["system"]["hub"] = {"faults": [{"code": "FAN", "name": "Fan problem", "zone": "left"}], "water_ok": False, "safety_ok": True}
    await r.observe(snap, at=r.now)


async def s_care(r, snap):
    from embertide import care as carelib
    from embertide.drivers.profiles import ORION
    r.n.bed.profile = ORION
    carelib.of(r.c).edit("tablets", {"last_done": "2026-01-01"})
    r.nt.care_persons = lambda: {"p1"}
    r.n.clock.jump_to(r.now + 86400 * 5 + 3 * H)
    await r.observe(snap, at=r.now)


async def s_done(r, snap):
    code = r.nt.new_action("p1", Action("Off tonight", "skip"))
    r.nt.use_action(code)
    await r.drain()


SITUATIONS = {
    "away_precool": (s_away_precool, "away_precool"), "empty_side": (s_empty_side, "empty_side"), "left_on": (s_left_on, "left_on"),
    "not_started": (s_not_started, "not_started"), "morning": (s_morning, "morning"), "pet_question": (s_pet_question, "pet_question"),
    "away_check": (s_away_check, "away_check"), "back_check": (s_back_check, "away_check"), "offline": (s_offline, "problem"),
    "stale": (s_stale, "digest"), "hub": (s_hub, "hub"), "care": (s_care, "maintenance"),
}
ALERT_KINDS = {"problem", "hub", "maintenance", "digest"}


async def produce(tmp_path, name, tone, monkeypatch):
    good_night(monkeypatch)
    r, snap = await start(tmp_path, tone, "2026-11-05T12:00" if name != "care" else "2026-11-10T12:00")
    fn, kind = SITUATIONS[name]
    from embertide.engine import away
    keep = away.morning_check
    try:
        await fn(r, snap)
    finally:
        away.morning_check = keep
    return r, r.sent(kind)


def check_hygiene(g):
    text = g.title + " " + g.body
    for bad in BANNED:
        assert bad not in text, (g.kind, bad, text)
    assert g.title.strip() and g.body.strip() and g.title[0].isupper(), g
    assert len(g.title) <= 90, (g.kind, len(g.title), g.title)             # a phone shows about this much of a title
    assert len(g.body) <= 320, (g.kind, len(g.body), g.body)               # and about this much without expanding
    assert not g.title.endswith((",", " ")) and "  " not in text
    assert len(g.actions) <= 3 and all(1 <= len(a) <= 24 for a in g.actions), g.actions


@pytest.mark.parametrize("tone", TONES)
@pytest.mark.parametrize("name", sorted(SITUATIONS))
async def test_every_kind_reads_well_in_every_tone(tmp_path, monkeypatch, name, tone):
    r, got = await produce(tmp_path, name, tone, monkeypatch)
    assert got, f"{name} sent nothing in the {tone} tone"
    for g in got:
        check_hygiene(g)
    g = got[0]
    if name == "left_on":
        assert "Left" in g.title and re.search(r"2\d", g.body)                      # the name and the minutes survive every tone
    if name == "pet_question":
        assert "Biscuit" in g.title
    if name == "morning":
        assert "7 h 50 min in bed" in g.body and "settled in 12 min" in g.body and "flash" in g.body and "up 2×" in g.body
    if name == "away_check":
        assert "Wednesday" in g.title + g.body
    if name in ("away_precool", "empty_side"):
        assert "pre-condition" in (g.title + g.body).lower() or "cool" in (g.title + g.body).lower() or "ready" in (g.title + g.body).lower()


@pytest.mark.parametrize("name", ["offline", "hub", "care"])
async def test_alerts_read_the_same_in_every_tone(tmp_path, monkeypatch, name):
    seen = {}
    for tone in TONES:
        _, got = await produce(tmp_path / tone, name, tone, monkeypatch)
        seen[tone] = [(g.title, g.body) for g in got]
    assert seen["calm"] == seen["friendly"] == seen["cheeky"]


@pytest.mark.parametrize("tone", TONES)
async def test_the_button_confirmations_are_short_plain_and_tone_free(tmp_path, tone):
    r, snap = await start(tmp_path, tone)
    for act in ("skip", "start_now", "resume", "feel_warm", "away_night"):
        a = {"person": "p1", "act": act, "minutes": None, "label": "x", "ref": None}
        text = r.nt.done_text(a)
        assert 8 <= len(text) <= 70 and "{" not in text, text


@pytest.mark.parametrize("kind", sorted(k for k in voice.PHRASES if k not in {"story_pet", "story_pet_with", "story_fitted", "checkin_app", "flash_end",
                                                                          "manual_cooler", "manual_warmer", "pattern_cooler", "pattern_warmer",
                                                                          "assistant_proposed"}))
def test_every_notification_phrase_fits_a_phone_in_every_tone(kind):
    """All variants of every notification wording, filled with the longest sensible values, still fit the title and body."""
    slots = {"name": "Alexandria", "minutes": 120, "summary": "7 h 50 min in bed · settled in 12 min · 3 flashes · up 2×", "who": "Biscuit",
             "when": "10:40 PM–11:10 PM", "side": "Right", "detail": "no answer from the broker", "lines": "2:06 AM: Can't reach the bed. 3:10 AM: Right bed sensor stopped updating.",
             "what": "Purification tablets are due", "advice": "Check the hub's vents aren't blocked and follow the maker's steps.",
             "why": "Keeps the water in the hub clean.", "more": " 2 other jobs are due too.", "day": "Wednesday",
             "service": "Withings", "hours": 12, "free": "600 MB", "n": 12,
             "device": "your watch or ring", "now": "74 °C", "want": "68 °C"}
    want = voice.slots_of(voice.PHRASES[kind]["calm"][0])
    for tone in TONES:
        for i, v in enumerate(voice.PHRASES[kind][tone]):
            out = voice.say(kind, {k: slots[k] for k in want}, tone=tone, day=__import__("datetime").date(2026, 11, 1 + i))
            title, body = out if isinstance(out, tuple) else (out, "")
            assert len(title) <= 100 and len(body) <= 330, (kind, tone, title, body)
            assert not re.search(r"\{|\}", title + body)
