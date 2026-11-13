"""Notification simulation, part 6: a quiet watch or ring, a bed whose sensors said nothing, a presence sensor that flips or is
stuck, and a side that doesn't reach its temperature. Each has its own switch (on by default), reads well in the tones it has,
clears itself when fixed, and stays quiet after a few reminders until then. The whole-night versions are in
test_sim_night_gaps.py and test_sim_night_time.py."""

import pathlib

import pytest

from embertide import notify, voice
from notify_rig import Rig
from test_sim_notify_tones import check_hygiene

H = 3600
DAY = 86400
KINDS = ("wearable", "bed_silent", "sensor", "target")
ROOT = pathlib.Path(__file__).resolve().parents[2]


async def rig(tmp_path, start="2026-11-05T12:00", **kw):
    r = Rig(tmp_path, start, **kw)
    r.phone("p1", "fcm")
    r.phone("p2", "up")
    snap = await r.prime()
    r.n.clock.advance(700)
    r.nt.owner_persons = lambda: {"p1"}
    r.nt.__dict__.pop("_wear_checked", None)                 # the priming tick already looked once
    return r, snap


def test_every_kind_has_a_switch_on_by_default_a_cap_a_settings_row_and_a_docs_row():
    prefs = (ROOT / "web" / "src" / "components" / "NotifyPrefs.tsx").read_text(encoding="utf8")
    types = (ROOT / "web" / "src" / "types.ts").read_text(encoding="utf8")
    docs = (ROOT / "docs" / "notifications.md").read_text(encoding="utf8")
    from embertide.models import NotifyPrefs
    for k in KINDS:
        assert NotifyPrefs().model_dump()[k] is True
        assert NotifyPrefs.model_validate({"morning": False})  .model_dump()[k] is True      # old stored settings load
        assert k in notify.CAPS
        assert f'["{k}",' in prefs and f"{k}?: boolean" in types
        assert f"`{k}`" in docs
    assert {"wearable", "sensor"} <= notify.OWN_TIMING_KINDS and "sensor" in notify.GAP_KINDS_OWNER


# ───────────────────────── a watch or ring silent for two nights ─────────────────────────
@pytest.mark.parametrize("tone", ["calm", "friendly", "cheeky"])
async def test_wearable_quiet_reads_well_in_every_tone_and_names_the_device(tmp_path, tone):
    r, snap = await rig(tmp_path / tone)
    r.p1.settings.tone = tone
    r.nt._wearable_silent = lambda p, now: "Pixel Watch" if p.id == "p1" else None
    await r.observe(snap, at=r.now)
    g = r.sent("wearable")[0]
    check_hygiene(g)
    assert "Pixel Watch" in g.title and "two nights" in g.title
    assert "charged and worn" in g.body and "open the app" in g.body
    assert g.raw["link"] == "/settings#watch"


async def test_wearable_quiet_once_a_day_four_reminders_then_quiet_and_starts_over_when_fixed(tmp_path):
    r, snap = await rig(tmp_path)
    state = {"dev": "your watch or ring"}
    r.nt._wearable_silent = lambda p, now: state["dev"] if p.id == "p1" else None
    for d in range(8):
        await r.observe(snap, at=r.at(f"2026-11-{5 + d:02d}T12:10"))
        await r.observe(snap, at=r.at(f"2026-11-{5 + d:02d}T15:00"))             # same day: not again
    assert len(r.sent("wearable", via="fcm")) == notify.GAP_REMINDERS
    state["dev"] = None
    await r.observe(snap, at=r.at("2026-11-13T12:10"))
    state["dev"] = "Oura"
    await r.observe(snap, at=r.at("2026-11-14T12:10"))
    assert len(r.sent("wearable", via="fcm")) == notify.GAP_REMINDERS + 1


async def test_wearable_quiet_is_for_that_person_only_in_the_day_and_respects_the_switch_and_away(tmp_path):
    r, snap = await rig(tmp_path)
    r.nt._wearable_silent = lambda p, now: "Oura"
    r.p1.settings.notify.wearable = False
    await r.observe(snap, at=r.now)
    assert [g.person for g in r.sent("wearable")] == ["p2"]                       # each person has their own switch and their own device
    r2, snap2 = await rig(tmp_path / "night", "2026-11-05T02:00")
    r2.nt._wearable_silent = lambda p, now: "Oura"
    await r2.observe(snap2, at=r2.now)
    await r2.observe(snap2, at=r2.at("2026-11-05T08:30"))
    assert not r2.sent("wearable") and not r2.c.store.get("notify_held")          # never at night, never held


# ───────────────────────── the bed's sensors said nothing all night ─────────────────────────
def silent_night(r, snap, run=8 * H, blind=8 * H):
    sch = r.sch(snap, "p1")
    t = r.nt._tracked()
    for pid in ("p1", "p2"):
        t["blind"][pid] = {"night": r.nt._night_key(r.sch(snap, pid)), "run": run, "blind": blind}
    return sch


@pytest.mark.parametrize("tone", ["calm", "friendly", "cheeky"])
async def test_bed_silent_goes_to_each_sleeper_after_wake_in_their_tone(tmp_path, tone):
    r, snap = await rig(tmp_path / tone)
    r.p1.settings.tone = tone
    sch = silent_night(r, snap)
    for p in ("p1", "p2"):
        r.put(snap, p, state="done")
    await r.observe(snap, at=sch["wake"] - 600)
    assert not r.sent("bed_silent")                                               # not before wake
    await r.observe(snap, at=sch["end"] + 60)
    got = r.sent("bed_silent")
    assert sorted(g.person for g in got) == ["p1", "p2"]
    for g in got:
        check_hygiene(g)
        assert "sensors" in g.title and "usual times" in g.body and "power and Wi-Fi" in g.body
    await r.observe(snap, at=sch["end"] + 600)
    assert len(r.sent("bed_silent")) == 2                                         # once a night


async def test_bed_silent_needs_nearly_the_whole_night_and_a_long_enough_night(tmp_path):
    r, snap = await rig(tmp_path)
    sch = silent_night(r, snap, run=8 * H, blind=6 * H)                            # 75%: some readings came
    r.put(snap, "p1", state="done")
    r.put(snap, "p2", state="done")
    await r.observe(snap, at=sch["end"] + 60)
    assert not r.sent("bed_silent")
    r.nt._tracked()["blind"]["p1"].update(run=2 * H, blind=2 * H, judged=False)    # a short night proves nothing
    await r.observe(snap, at=sch["end"] + 120)
    assert not r.sent("bed_silent")


async def test_bed_silent_four_nights_then_quiet_and_a_good_night_clears_it(tmp_path):
    r, snap = await rig(tmp_path)
    sch = r.sch(snap, "p1")
    r.put(snap, "p1", state="done")
    key = f"bed_silent:p1"
    for d in range(6):
        t = r.nt._tracked()
        t["blind"]["p1"] = {"night": f"2026-11-{10 + d}", "run": 8 * H, "blind": 8 * H}
        snap["people"][0]["schedule"]["bedtime"] = r.at(f"2026-11-{10 + d}T22:15")
        snap["people"][0]["schedule"]["wake"] = snap["people"][0]["schedule"]["bedtime"] + 8 * H
        snap["people"][0]["schedule"]["end"] = snap["people"][0]["schedule"]["wake"] + 1800
        await r.observe(snap, at=snap["people"][0]["schedule"]["end"] + 60)
    assert len(r.sent("bed_silent", person="p1")) == notify.GAP_REMINDERS
    s = snap["people"][0]["schedule"]
    t = r.nt._tracked()
    t["blind"]["p1"] = {"night": r.nt._night_key(s), "run": 8 * H, "blind": 0}
    await r.observe(snap, at=s["end"] + 120)
    assert key not in (r.c.store.get("notify_gaps") or {})


async def test_bed_silent_not_for_a_guest_or_when_the_switch_is_off(tmp_path):
    r, snap = await rig(tmp_path)
    sch = silent_night(r, snap)
    r.put(snap, "p1", state="done", guest={"zone": "left"})
    r.put(snap, "p2", state="done")
    r.p2.settings.notify.bed_silent = False
    await r.observe(snap, at=sch["end"] + 60)
    assert not r.sent("bed_silent")


# ───────────────────────── a presence sensor that flips or is stuck ─────────────────────────
def sensor(**kw):
    return {"id": "radar1", "name": "Bedroom radar", "zone": "left", "kind": "radar", "flapping_since": None, "on_since": None, **kw}


async def test_a_flipping_sensor_is_told_to_owners_in_the_day_once_and_clears(tmp_path):
    r, snap = await rig(tmp_path)
    s = sensor(flapping_since=r.now - 3 * H)
    r.nt.sensor_states = lambda: [s]
    await r.observe(snap, at=r.now)
    got = r.sent("sensor")
    assert [g.person for g in got] == ["p1"]                                       # owners only
    check_hygiene(got[0])
    assert "Bedroom radar" in got[0].title and "Check its position or power" in got[0].body
    assert got[0].raw["link"] == "/studio/devices"
    await r.observe(snap, at=r.now + 2 * H)
    assert len(r.sent("sensor")) == 1
    r.nt.sensor_states = lambda: []
    await r.observe(snap, at=r.now + DAY)
    assert "sensor:flip:radar1" not in (r.c.store.get("notify_gaps") or {})


async def test_a_sensor_set_aside_only_a_short_while_is_not_told(tmp_path):
    r, snap = await rig(tmp_path)
    r.nt.sensor_states = lambda: [sensor(flapping_since=r.now - 1 * H)]
    await r.observe(snap, at=r.now)
    assert not r.sent("sensor")


async def test_a_stuck_sensor_needs_a_day_on_and_hours_of_the_bed_saying_empty_and_is_told_weekly(tmp_path):
    r, snap = await rig(tmp_path)
    s = sensor(on_since=r.now - 30 * H)
    r.nt.sensor_states = lambda: [s]
    await r.observe(snap, at=r.now)
    assert not r.sent("sensor")                                                    # the bed never showed the side empty: could be true
    hour = int(r.now // H)
    r.nt._tracked()["empty"]["left"] = {str(hour - i): 1800.0 for i in range(1, 9)}
    await r.observe(snap, at=r.now + 60)
    got = r.sent("sensor")
    assert len(got) == 1 and "Bedroom radar" in got[0].title and "day" in got[0].title + got[0].body
    check_hygiene(got[0])
    base = r.now
    for day in (3, 8):
        s["on_since"] = base + day * DAY - 60 * H
        r.nt._tracked()["empty"]["left"] = {str(int((base + day * DAY) // H) - i): 1800.0 for i in range(1, 9)}
        await r.observe(snap, at=base + day * DAY)
        assert len(r.sent("sensor")) == (1 if day == 3 else 2)                      # weekly, not daily


async def test_sensor_switch_off_and_sources_list_leaves_out_paused_trusted_and_sleeper_types(tmp_path):
    r, snap = await rig(tmp_path)
    r.p1.settings.notify.sensor = False
    r.nt.sensor_states = lambda: [sensor(flapping_since=r.now - 3 * H)]
    await r.observe(snap, at=r.now)
    assert not r.sent("sensor", person="p1")
    from embertide.sources import SourceConfig, SourceManager
    sm = SourceManager(r.c.store, r.n.clock, lambda: None)
    for sid, kind, enabled, tuning in (("a", "radar", True, {}), ("b", "radar", False, {}), ("c", "radar", True, {"ignore_health": True}),
                                       ("d", "scale", True, {}), ("e", "pad", True, {})):
        c = SourceConfig(id=sid, name=sid, driver="mqtt-topic", role="weight" if kind == "scale" else "presence", kind=kind, zone="left",
                         tuning=tuning, params={"topic": "t", "url": "mqtt://localhost"}, enabled=enabled)
        sm.configs.append(c)
        sm.health[sid] = {"status": "erratic", "why": "x", "since": 1.0, "bad": [], "flips": [], "hist": [], "last_problem": 1.0, "dropped": 0, "flap": True}
        sm._occ[sid] = {"raw": True, "since": 1.0, "last_true": r.now}
        sm.latest[sid] = {"value": 1.0, "ts": r.now, "seen": r.now, "error": None}
    ids = {x["id"] for x in sm.attention(r.now)}
    assert ids == {"a", "d", "e"}                                                  # paused and trusted ones never; flapping ones of any voting type
    for x in sm.attention(r.now):
        assert x["on_since"] is None                                               # erratic: set aside, so not "stuck on"; scale and pad never judged as stuck
    for sid in ("a", "d", "e"):
        sm.health[sid].update(status="ok", flap=False)
    sm._occ["a"] = {"raw": True, "since": 1.0, "last_true": r.now}
    sm._occ["d"] = {"raw": True, "since": 1.0, "last_true": r.now}
    sm.configs[0].tuning = {"hold_s": 1e9}
    kinds = {x["id"] for x in sm.attention(r.now) if x["on_since"] is not None}
    assert kinds == {"a"}                                                          # a radar can be stuck on; a scale or pad holds what lies on it


# ───────────────────────── a side that isn't reaching its temperature ─────────────────────────
async def run_target(r, snap, p="p1", minutes=130, measured=24.0, want=18.0, start="2026-11-06T00:30", move=None, **night):
    pv = r.pv(snap, p)
    r.put(snap, p, state="rhythm", target_c=want, **night)
    pv["bed"].update(on=True, setpoint_c=want)
    t0 = r.at(start)
    for m in range(minutes):
        pv["bed"]["measured_c"] = move(m) if move else measured
        await r.observe(snap, at=t0 + m * 60)


async def test_a_pad_stuck_far_off_is_held_for_the_morning_and_owners_hear_at_once(tmp_path):
    r, snap = await rig(tmp_path / "far")
    await run_target(r, snap, "p2", measured=24.0, want=18.0)                      # Right's side: p1 (the owner) is not the sleeper
    held = [e for e in r.events("notification_held") if e["about"] == "target"]
    assert {e["person"] for e in held} == {"p1", "p2"}                             # the sleeper and the owner, both held for the morning
    sent = r.sent("target")
    assert [g.person for g in sent] == ["p1"]                                      # the far-off alert, at once, to the owner only
    g = sent[0]
    check_hygiene(g)
    assert "right side" in g.title and "75°" in g.body and "64°" in g.body and "water level on the hub" in g.body
    assert g.raw["link"] == "/studio/hub"
    assert not any(e["about"] == "target" for e in r.events("notification") if e["person"] == "p2")


async def test_a_moderate_shortfall_is_only_held_never_sent_at_night(tmp_path):
    r, snap = await rig(tmp_path / "mid")
    await run_target(r, snap, "p1", measured=21.5, want=18.0)                      # 3.5 C = 6.3 F off: over 4 F, under 9 F
    assert not r.sent("target")
    assert len([e for e in r.events("notification_held") if e["about"] == "target"]) == 1       # the sleeper is the owner: one message
    await r.observe(snap, at=r.now)
    assert len([e for e in r.events("notification_held") if e["about"] == "target"]) == 1       # once a night


async def test_units_follow_the_person(tmp_path):
    r, snap = await rig(tmp_path / "c")
    r.p1.settings.units = "C"
    await run_target(r, snap, "p1", measured=21.5, want=18.0)
    h = [x for x in r.c.store.get("notify_held") if x["kind"] == "target"][0]
    assert "21.5°C" in h["body"] and "18°C" in h["body"] and "°F" not in h["body"]


@pytest.mark.parametrize("case", ["near", "ramping", "flash", "commands", "hub", "cold_room", "off", "guest"])
async def test_it_never_fires_falsely(tmp_path, case):
    r, snap = await rig(tmp_path / case)
    kw = {}
    measured = 24.0
    move = None
    if case == "near":
        measured = 19.5                                                            # under 4 F
    elif case == "ramping":
        move = lambda m: max(18.0, 26.0 - 0.1 * m)                                 # a slow ramp that is plainly on its way
    elif case == "flash":
        kw["flash"] = {"kind": "hot"}
    elif case == "commands":
        r.c.unconfirmed_since = {"left": r.now}
    elif case == "hub":
        snap["system"]["hub"] = {"faults": [{"code": "x", "name": "Fault"}], "water_ok": False}
    elif case == "cold_room":
        kw["room_note"] = {"text": "The room is cold"}
    elif case == "off":
        r.p1.settings.notify.target = False
    elif case == "guest":
        kw["guest"] = {"zone": "left"}
    await run_target(r, snap, "p1", measured=measured, move=move, **kw)
    assert not r.sent("target") and not [e for e in r.events("notification_held") if e["about"] == "target"]


async def test_a_target_that_keeps_changing_is_never_steady_and_a_known_slow_room_is_left_alone(tmp_path):
    r, snap = await rig(tmp_path / "chg")
    await run_target(r, snap, "p1", move=lambda m: 24.0, want=18.0, minutes=30)
    pv = r.pv(snap, "p1")
    for m in range(30, 130):                                                       # the target changes every 20 minutes
        r.put(snap, "p1", target_c=18.0 + (m // 20) * 0.5)
        pv["bed"].update(setpoint_c=18.0 + (m // 20) * 0.5)
        await r.observe(snap, at=r.at("2026-11-06T00:30") + m * 60)
    assert not [e for e in r.events("notification_held") if e["about"] == "target"]
    r2, snap2 = await rig(tmp_path / "slow")
    r2.c.room_c = lambda now: 17.0                                                 # cold room, warm target: the pad is known to lag
    await run_target(r2, snap2, "p1", measured=14.0, want=22.0)
    assert not [e for e in r2.events("notification_held") if e["about"] == "target"]


async def test_the_target_note_is_told_in_the_morning_and_a_fixed_night_clears_it(tmp_path):
    r, snap = await rig(tmp_path / "morn")
    await run_target(r, snap, "p1", measured=21.5, want=18.0)
    gaps = r.c.store.get("notify_gaps") or {}
    assert "target:left" in gaps
    for p in ("p1", "p2"):
        r.put(snap, p, state="done")
    sch = r.sch(snap, "p1")
    await r.observe(snap, at=sch["end"] + 60)
    assert any(e["kind"] == "notification" and e["about"] in ("digest", "morning") and "isn't getting to the temperature" in (e.get("body") or "") + e.get("title", "")
               or e["about"] == "digest" for e in r.events("notification"))
    # a later night that ran two hours without the problem clears the count
    r.nt.__dict__["_tgt_ran"]["p1"].update(night="x")
    pv = r.pv(snap, "p1")
    r.put(snap, "p1", state="rhythm", target_c=18.0)
    pv["bed"].update(on=True, setpoint_c=18.0, measured_c=18.1)
    t0 = sch["bedtime"] + DAY
    snap["people"][0]["schedule"]["bedtime"] = t0
    for m in range(0, 150, 5):
        await r.observe(snap, at=t0 + 3600 + m * 60)
    r.put(snap, "p1", state="done")
    await r.observe(snap, at=t0 + 3600 + 160 * 60)
    assert "target:left" not in (r.c.store.get("notify_gaps") or {})


# ───────────────────────── the phone is told when a night-bound button stops being offered ─────────────────────────
async def test_night_bound_pushes_carry_the_end_of_their_night_and_others_do_not(tmp_path):
    r, snap = await rig(tmp_path)
    from embertide.notify import Action, Message
    sch = r.sch(snap, "p1")
    r.nt._snap = snap
    r.n.clock.jump_to(sch["bedtime"] - 3600)
    r.nt.send(Message("away_precool", "Not home", "b", "p1", [Action("Off tonight", "skip")]))
    r.nt.send(Message("left_on", "Left on", "b", "p1", [Action("Turn it off", "manual_off")]))
    await r.drain()
    exp = {g.kind: g.raw.get("expires") for g in r.sent(person="p1", via="fcm")}
    assert exp["away_precool"] and abs(exp["away_precool"] - (__import__("time").time() + sch["end"] - r.now)) < 5
    assert "expires" not in next(g.raw for g in r.sent(person="p1", via="fcm") if g.kind == "left_on")


def test_the_android_expiry_is_in_the_notification_code():
    kt = (ROOT / "web" / "android" / "app" / "src" / "main" / "java" / "app" / "embertide" / "EmberPush.kt").read_text(encoding="utf8")
    assert "setTimeoutAfter(leftMs)" in kt and 'optLong("expires"' in kt and '"pause", "skip", "start_now"' in kt


def test_new_phrases_are_alerts_or_have_all_three_tones():
    for k in ("sensor_flip", "sensor_stuck", "target_short", "target_far"):
        assert k in voice.ALERTS
    for k in ("wearable_quiet", "bed_silent"):
        assert k not in voice.ALERTS and all(len(voice.PHRASES[k][t]) >= 3 for t in voice.TONES)
