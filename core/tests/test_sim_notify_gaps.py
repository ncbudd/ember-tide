"""Notification simulation, part 5: situations a person had to act on that used to raise nothing. Each new kind has its own
switch (on by default), reads well in all three tones, is daytime only (never held, never at night), goes out at most once
a day, stops after a few reminders until fixed, clears itself when the problem goes away, and names where to fix it."""

import pathlib

import pytest

from embertide import notify
from embertide.models import NotifyPrefs
from embertide.notify import Message
from notify_rig import Rig
from test_sim_notify_tones import check_hygiene

H = 3600
DAY = 86400
NEW_KINDS = ("account", "reach", "bridge", "storage")


async def rig(tmp_path, start="2026-11-05T12:00", **kw):
    r = Rig(tmp_path, start, **kw)
    r.phone("p1", "fcm")
    r.phone("p2", "up")
    snap = await r.prime()
    r.n.clock.advance(700)
    r.nt.owner_persons = lambda: {"p1"}
    return r, snap


def test_every_new_kind_has_a_switch_on_by_default_a_cap_and_a_settings_row():
    web = pathlib.Path(__file__).resolve().parents[2] / "web" / "src"
    prefs = (web / "components" / "NotifyPrefs.tsx").read_text(encoding="utf8")
    types = (web / "types.ts").read_text(encoding="utf8")
    docs = (pathlib.Path(__file__).resolve().parents[2] / "docs" / "notifications.md").read_text(encoding="utf8")
    for k in NEW_KINDS:
        assert NotifyPrefs().model_dump()[k] is True
        assert k in notify.CAPS and k in notify.OWN_TIMING_KINDS
        assert f'["{k}",' in prefs and f"{k}?: boolean" in types
        assert f"`{k}`" in docs


# ───────────────────────── a connected account needs signing in again ─────────────────────────
async def test_account_signed_out_tells_that_person_once_a_day_with_where_to_fix_it(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T12:00")
    r.nt.account_states = lambda: [("p1", "Oura", True), ("p2", "Withings", False)]
    r.phone("p1", "web", "iphone")
    await r.observe(snap, at=r.now)
    await r.observe(snap, at=r.now + 3 * H)
    got = r.sent("account")
    assert {(g.via, g.person) for g in got} == {("fcm", "p1"), ("web", "p1")}                 # both of Left's phones, nobody else
    assert all("Oura" in g.title + g.body and "Connect Oura again" in g.body for g in got)
    web = next(g for g in got if g.via == "web")
    assert web.raw["url"] == "/settings"                                                      # a tap opens the screen that fixes it
    for g in got:
        check_hygiene(g)
    await r.observe(snap, at=r.now + DAY)
    assert len(r.sent("account", via="fcm")) == 2                                             # a day later: a gentle second reminder


async def test_account_reminders_stop_after_a_few_days_and_start_over_after_it_is_fixed(tmp_path):
    r, snap = await rig(tmp_path)
    state = {"needs": True}
    r.nt.account_states = lambda: [("p1", "Oura", state["needs"])]
    for d in range(8):
        await r.observe(snap, at=r.now + DAY * (1 if d else 0))
    assert len(r.sent("account")) == notify.GAP_REMINDERS
    state["needs"] = False
    await r.observe(snap, at=r.now + DAY)
    state["needs"] = True                                                                     # signed out again, a new problem
    await r.observe(snap, at=r.now + DAY)
    assert len(r.sent("account")) == notify.GAP_REMINDERS + 1


async def test_account_cleared_the_same_day_is_not_sent_late(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T07:00")
    state = {"needs": True}
    r.nt.account_states = lambda: [("p1", "Oura", state["needs"])]
    await r.observe(snap, at=r.now)                                                           # 7 AM: too early, nothing yet
    state["needs"] = False                                                                    # fixed before the day window
    await r.observe(snap, at=r.at("2026-11-05T10:00"))
    assert not r.sent("account")


async def test_account_switch_off_and_not_when_the_partner_asks(tmp_path):
    r, snap = await rig(tmp_path)
    r.nt.account_states = lambda: [("p1", "Oura", True)]
    r.p1.settings.notify.account = False
    await r.observe(snap, at=r.now)
    assert not r.got
    r.p1.settings.notify.account = True
    r.p2.settings.notify.account = False                                                      # the partner's switch is theirs
    await r.observe(snap, at=r.now + 60)
    assert [g.person for g in r.sent("account")] == ["p1"]


async def test_account_wording_in_all_three_tones(tmp_path):
    seen = {}
    for tone in ("calm", "friendly", "cheeky"):
        r, snap = await rig(tmp_path / tone)
        r.p1.settings.tone = tone
        r.nt.account_states = lambda: [("p1", "WHOOP", True)]
        await r.observe(snap, at=r.now)
        g = r.sent("account")[0]
        check_hygiene(g)
        assert "WHOOP" in g.title and "Connect WHOOP again" in g.body
        seen[tone] = (g.title, g.body)
    assert len(set(seen.values())) == 3


# ───────────────────────── a phone that stopped getting notifications ─────────────────────────
async def fail_three(r, pid="p1", n=3):
    for i in range(n):
        r.nt.send(Message(("left_on", "empty_side", "away_precool", "pet_question")[i % 4], f"Left note {i}", "b", pid))
        await r.drain()


async def test_three_failed_messages_in_a_row_tell_the_owner_not_the_person(tmp_path):
    r, snap = await rig(tmp_path)
    r.nt.owner_persons = lambda: {"p2"}
    r.behave["fcm.googleapis.com"] = 503
    await fail_three(r)
    assert r.nt.store.get("notify_reach") == {"p1": 3}
    await r.observe(snap, at=r.now)
    got = r.sent("reach")
    assert [g.person for g in got] == ["p2"]                                                  # the owner, never the unreachable person
    assert "Left" in got[0].title and "3 messages" in got[0].body
    check_hygiene(got[0])
    await r.observe(snap, at=r.now + 3 * H)
    assert len(r.sent("reach")) == 1                                                          # once a day


async def test_two_failures_are_not_enough_and_a_success_starts_the_count_over(tmp_path):
    r, snap = await rig(tmp_path)
    r.behave["fcm.googleapis.com"] = 503
    await fail_three(r, n=2)
    await r.observe(snap, at=r.now)
    assert not r.sent("reach")
    r.behave["fcm.googleapis.com"] = 200
    r.nt.send(Message("left_on", "Left's side is on again", "b", "p1"))
    await r.drain()
    assert not r.nt.store.get("notify_reach")
    r.behave["fcm.googleapis.com"] = 503
    await fail_three(r, n=2)
    await r.observe(snap, at=r.now + 10)
    assert not r.sent("reach")


async def test_tests_and_confirmations_do_not_count_as_failures(tmp_path):
    r, snap = await rig(tmp_path)
    r.behave["fcm.googleapis.com"] = 503
    for i in range(5):
        r.nt.send(Message("test", f"Test {i}", "b", "p1"))
        r.nt.send(Message("action_done", f"Done {i}", "b", "p1"))
    await r.drain()
    assert not r.nt.store.get("notify_reach")


async def test_registering_a_new_phone_clears_the_count(tmp_path):
    r, snap = await rig(tmp_path)
    r.behave["fcm.googleapis.com"] = 503
    await fail_three(r)
    r.phone("p1", "up", "new-phone")
    assert not r.nt.store.get("notify_reach")
    await r.observe(snap, at=r.now)
    assert not r.sent("reach")


async def test_an_ntfy_only_person_whose_server_rejects_everything_counts_too(tmp_path):
    r = Rig(tmp_path, "2026-11-05T12:00", ntfy=True)
    snap = await r.prime()
    r.n.clock.advance(700)
    r.behave["ntfy.example"] = 401
    await fail_three(r)
    assert r.nt.store.get("notify_reach") == {"p1": 3}
    await r.observe(snap, at=r.now)
    assert [g.to for g in r.sent("reach")] == []                                              # the household target is the same server: nothing can go out
    assert any(e["about"] == "reach" for e in r.events("notification"))


async def test_reach_falls_back_to_the_household_target_when_the_owner_is_the_one_unreachable(tmp_path):
    r = Rig(tmp_path, "2026-11-05T12:00", ntfy=True)
    r.phone("p1", "fcm")
    snap = await r.prime()
    r.n.clock.advance(700)
    r.nt.owner_persons = lambda: {"p1"}
    r.behave["fcm.googleapis.com"] = 503
    r.behave["ntfy.example"] = lambda req: 401 if b"left-bed" in req.content else 200
    await fail_three(r)
    await r.observe(snap, at=r.now)
    assert [(g.via, g.to) for g in r.sent("reach")] == [("ntfy", "house")]


async def test_a_person_with_no_phone_at_all_is_not_a_failure(tmp_path):
    r = Rig(tmp_path)
    snap = await r.prime()
    r.n.clock.advance(700)
    await fail_three(r)
    assert not r.nt.store.get("notify_reach")


# ───────────────────────── the Home Assistant link is down ─────────────────────────
async def test_home_assistant_link_down_for_hours_tells_the_owner_once_a_day(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T09:00")
    r.nt.bridge_status = lambda: (True, "starting")
    await r.observe(snap, at=r.now)
    await r.observe(snap, at=r.now + 2.9 * H)
    assert not r.sent("bridge")                                                               # not yet: a short outage is not news
    await r.observe(snap, at=r.now + 0.2 * H)
    got = r.sent("bridge")
    assert [g.person for g in got] == ["p1"] and "3 hours" in got[0].body                      # owner only
    check_hygiene(got[0])
    await r.observe(snap, at=r.now + 1 * H)
    assert len(r.sent("bridge")) == 1


async def test_home_assistant_link_back_before_the_morning_is_never_told(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T22:00")
    st = {"s": "starting"}
    r.nt.bridge_status = lambda: (True, st["s"])
    await r.observe(snap, at=r.now)
    await r.observe(snap, at=r.at("2026-11-06T02:00"))
    st["s"] = "connected"
    await r.observe(snap, at=r.at("2026-11-06T10:00"))
    assert not r.sent("bridge")


async def test_home_assistant_link_not_set_up_never_raises_anything(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T09:00")
    r.nt.bridge_status = lambda: (False, "off")
    for h in range(0, 30, 2):
        await r.observe(snap, at=r.now + h * H)
    assert not r.sent("bridge")


async def test_home_assistant_bad_password_says_so(tmp_path):
    r, snap = await rig(tmp_path, "2026-11-05T09:00")
    r.nt.bridge_status = lambda: (True, "not authorised: check the MQTT username and password")
    await r.observe(snap, at=r.now)
    await r.observe(snap, at=r.now + 3.1 * H)
    assert "check the MQTT username and password" in r.sent("bridge")[0].body


# ───────────────────────── the disk is nearly full ─────────────────────────
GB = 1 << 30


async def test_nearly_full_disk_tells_the_owner_by_day_and_clears_when_there_is_room(tmp_path):
    r, snap = await rig(tmp_path)
    space = {"free": 600 << 20}
    r.nt.disk_usage = lambda p: (100 * GB, 100 * GB - space["free"], space["free"])
    await r.observe(snap, at=r.now)
    got = r.sent("storage")
    assert [g.person for g in got] == ["p1"] and "600 MB" in got[0].body
    check_hygiene(got[0])
    await r.observe(snap, at=r.now + 2 * H)
    assert len(r.sent("storage")) == 1
    space["free"] = 50 * GB
    await r.observe(snap, at=r.now + DAY)
    assert len(r.sent("storage")) == 1
    space["free"] = 3 * (1 << 20)
    await r.observe(snap, at=r.now + DAY)
    assert len(r.sent("storage")) == 2 and "3 MB" in r.sent("storage")[-1].body


async def test_a_small_disk_with_plenty_of_percent_free_and_a_big_disk_nearly_full(tmp_path):
    r, snap = await rig(tmp_path)
    r.nt.disk_usage = lambda p: (8 * GB, 4 * GB, 4 * GB)
    await r.observe(snap, at=r.now)
    assert not r.sent("storage")
    r.nt.disk_usage = lambda p: (4000 * GB, 3800 * GB, 190 * GB)                              # under 5%
    await r.observe(snap, at=r.now + DAY)
    assert len(r.sent("storage")) == 1


# ───────────────────────── never at night, never held ─────────────────────────
@pytest.mark.parametrize("hour", range(0, 24))
async def test_these_notes_only_go_out_between_9_am_and_7_pm(tmp_path, hour):
    r = Rig(tmp_path, "2026-11-05T00:30")
    r.phone("p1", "fcm")
    snap = await r.prime()
    r.nt.owner_persons = lambda: {"p1"}
    r.nt.account_states = lambda: [("p1", "Oura", True)]
    r.nt.bridge_status = lambda: (True, "starting")
    r.nt.disk_usage = lambda p: (100 * GB, 99 * GB, GB // 4)
    r.nt._bridge_down_since = r.now - 5 * H
    await r.observe(snap, at=r.at(f"2026-11-05T{hour:02d}:30"))
    sent = {g.kind for g in r.got}
    assert (sent == {"account", "bridge", "storage"}) == (9 <= hour < 19), (hour, sent)
    assert not r.c.store.get("notify_held")                                                   # never held for the morning


async def test_none_of_it_while_someone_sleeps_even_in_the_day(tmp_path):
    r, snap = await rig(tmp_path)
    r.put(snap, "p1", state="rhythm")                                                         # a nap, say
    r.nt.account_states = lambda: [("p1", "Oura", True)]
    await r.observe(snap, at=r.now)
    assert not r.got and not r.c.store.get("notify_held")


async def test_a_restart_does_not_resend_today_but_remembers_the_reminder_count(tmp_path):
    r, snap = await rig(tmp_path)
    r.nt.account_states = lambda: [("p1", "Oura", True)]
    await r.observe(snap, at=r.now)
    r.restart()
    r.nt.account_states = lambda: [("p1", "Oura", True)]
    await r.observe(snap, at=r.now + 2 * H)
    assert len(r.sent("account")) == 1
    await r.observe(snap, at=r.now + DAY)
    assert len(r.sent("account")) == 2
