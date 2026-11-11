"""Notification simulation, part 2: delivery. Every provider, people with no phone or two phones, phones that went away,
providers that are down or hang, quiet hours, daily caps, de-duplication, partner privacy, and server restarts.
Nothing leaves the machine: the network is a fake that records what would have been sent."""

import asyncio
import time

import httpx
import pytest

from embertide.notify import Action, Message
from notify_rig import FCM_HOST, PUSH_SERVER, WEB_HOST, Rig

H = 3600


def msg(person="p1", kind="left_on", title="Left's side is on, but nobody's in it", body="Empty for 20 min.", actions=True, **kw):
    return Message(kind, title, body, person, [Action("Turn it off", "manual_off")] if actions else [], **kw)


async def send(r, m):
    ok = r.nt.send(m)
    await r.drain()
    return ok


# ───────────────────────── every channel ─────────────────────────
@pytest.mark.parametrize("kind", ["fcm", "up", "web"])
async def test_each_app_push_channel_delivers_to_the_right_person_with_the_button(tmp_path, kind):
    r = Rig(tmp_path)
    r.phone("p1", kind)
    r.phone("p2", "fcm")
    assert await send(r, msg())
    got = r.sent()
    assert [(g.via, g.person) for g in got] == [(kind, "p1")]
    assert got[0].title.startswith("Left's side is on") and got[0].actions == ["Turn it off"]
    if kind == "web":
        assert got[0].codes and got[0].raw["url"] == "/"                       # the confirm page; iPhone shows no buttons and opens the app
    else:
        assert got[0].acts == ["manual_off"]


async def test_ntfy_target_delivers_with_link_buttons_that_carry_no_secret(tmp_path):
    r = Rig(tmp_path, ntfy=True)
    assert await send(r, msg())
    got = r.sent()
    assert [(g.via, g.to, g.person) for g in got] == [("ntfy", "left-bed", "p1")]
    assert got[0].raw["actions"][0]["url"].startswith("https://bed.example/api/notify/act/")
    assert "tk_" not in str(got[0].raw["actions"]) and got[0].raw["click"] == "https://bed.example/"


async def test_home_assistant_target_delivers_with_confirm_page_buttons_without_the_listener(tmp_path):
    r = Rig(tmp_path, ha=True)
    assert await send(r, msg())
    got = r.sent()
    assert [(g.via, g.to, g.person) for g in got] == [("ha", "mobile_app_left", "p1")]
    assert got[0].raw["data"]["actions"][0]["action"] == "URI" and got[0].raw["data"]["actions"][0]["uri"].startswith("https://bed.example/act#")


async def test_home_assistant_one_tap_buttons_when_the_listener_is_connected(tmp_path):
    from embertide.notify import HaActionListener
    r = Rig(tmp_path, ha=True)
    lis = HaActionListener("http://192.168.1.10:8123", "ha_tok", r.nt._on_ha_action)
    lis.connected = True
    r.nt.ha_listeners["http://192.168.1.10:8123"] = lis
    assert await send(r, msg())
    act = r.sent()[0].raw["data"]["actions"][0]
    assert act["action"].startswith("ET_") and act["title"] == "Turn it off"      # one tap: the phone never calls this server
    snap = await r.prime()
    r.c.engine_for("p1").adjust(r.now, temp_c=20.0, minutes=90)                    # something to turn off
    assert r.c.engine_for("p1").s.override is not None
    await r.nt._on_ha_action(act["action"])
    await r.drain()
    assert r.c.engine_for("p1").s.override is None
    done = [g for g in r.sent("action_done")]
    assert len(done) == 1 and done[0].to == "mobile_app_left" and done[0].title == "Left's side is off"   # the quiet "it worked"
    await r.nt._on_ha_action(act["action"])                                       # a second tap: nothing
    await r.drain()
    assert len(r.sent("action_done")) == 1
    await r.nt._on_ha_action("something_else")                                    # not ours: ignored
    r.nt.ha_listeners.clear()


async def test_the_app_wins_over_ntfy_and_home_assistant_one_channel_per_message(tmp_path):
    r = Rig(tmp_path, ntfy=True, ha=True)
    r.phone("p1", "fcm")
    await send(r, msg())
    assert [(g.via, g.person) for g in r.sent()] == [("fcm", "p1")]


async def test_no_phone_and_no_channel_is_recorded_as_not_delivered_not_as_sent(tmp_path):
    r = Rig(tmp_path)
    assert await send(r, msg()) is True
    assert not r.got
    assert [e["delivered"] for e in r.events("notification")] == [False]
    u = r.events("notification_undelivered")
    assert len(u) == 1 and "no phone" in u[0]["why"]


async def test_a_person_with_two_phones_gets_it_on_both_once_each(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm", "pixel")
    r.phone("p1", "web", "iphone")
    await send(r, msg())
    assert sorted((g.via, g.to) for g in r.sent()) == [("fcm", "pixel"), ("web", "iphone")]
    assert not r.sent(person="p2")


async def test_ntfy_and_ha_targets_belong_to_one_person_each(tmp_path):
    r = Rig(tmp_path, ntfy=True, ha=True)
    await send(r, msg("p2", title="Right's side is on, but nobody's in it"))
    assert sorted((g.via, g.to) for g in r.sent()) == [("ha", "mobile_app_right"), ("ntfy", "right-bed")]
    assert not r.sent(person="p1")


# ───────────────────────── phones that went away ─────────────────────────
@pytest.mark.parametrize("kind,rule", [("fcm", 404), ("fcm", (404, '{"error":{"details":[{"errorCode":"UNREGISTERED"}]}}')),
                                      ("fcm", (400, '{"error":{"status":"INVALID_ARGUMENT","message":"The registration token is not a valid FCM registration token"}}')),
                                      ("fcm", (403, '{"error":{"status":"PERMISSION_DENIED","details":[{"errorCode":"SENDER_ID_MISMATCH"}]}}')),
                                      ("up", 410), ("up", 404), ("web", 410), ("web", 404)])
async def test_a_rejected_or_expired_push_token_is_pruned_and_the_next_channel_is_used(tmp_path, kind, rule):
    r = Rig(tmp_path, ntfy=True)
    ph = r.phone("p1", kind)
    r.behave[{"fcm": FCM_HOST, "up": ph.endpoint.split("?")[0], "web": WEB_HOST}[kind]] = rule
    await send(r, msg())
    assert r.nt.devices("p1") == []                                    # pruned, so it isn't tried again
    assert [(g.via, g.to) for g in r.sent()] == [("ntfy", "left-bed")]  # and the message still reached the person
    await send(r, msg(title="Another one"))
    assert len([g for g in r.requests if g.url.host == FCM_HOST or str(g.url).startswith(PUSH_SERVER + "/up") or g.url.host == WEB_HOST]) == 1


async def test_one_gone_phone_and_one_good_phone_no_fallback_and_only_the_gone_one_pruned(tmp_path):
    r = Rig(tmp_path, ntfy=True)
    gone, good = r.phone("p1", "up", "old"), r.phone("p1", "fcm", "new")
    r.behave[gone.endpoint.split("?")[0]] = 410
    await send(r, msg())
    assert [g.to for g in r.sent()] == ["new"]
    assert [d["device"] for d in r.nt.devices("p1")] == ["new"]


@pytest.mark.parametrize("rule", [500, 503, 429, httpx.ConnectTimeout("slow"), httpx.ConnectError("down")])
async def test_a_push_service_that_is_down_keeps_the_phone_registered_and_falls_back(tmp_path, rule):
    r = Rig(tmp_path, ntfy=True)
    ph = r.phone("p1", "fcm")
    r.behave[FCM_HOST] = rule
    await send(r, msg())
    assert len(r.nt.devices("p1")) == 1                                # a bad hour is not a gone phone
    assert [(g.via, g.to) for g in r.sent()] == [("ntfy", "left-bed")]
    fails = [d for d in r.nt.sent_log if not d.get("ok")]
    assert fails and fails[0]["provider"].startswith("app")


async def test_a_provider_that_hangs_does_not_block_the_loop_or_other_peoples_messages(tmp_path):
    r = Rig(tmp_path, ntfy=True)
    r.behave["ntfy.example"] = lambda req: "hang" if b"left-bed" in req.content else 200
    t0 = time.monotonic()
    r.nt.send(msg("p1"))
    r.nt.send(msg("p2", title="Right's side is on, but nobody's in it"))
    assert time.monotonic() - t0 < 0.5                                 # send() never waits for the network
    await asyncio.sleep(0.2)
    assert [(g.to, g.person) for g in r.got] == [("right-bed", "p2")]  # Right's message went while Left's is still stuck
    for t in list(r.nt._tasks):
        t.cancel()


async def test_observe_never_waits_for_a_slow_provider(tmp_path):
    r = Rig(tmp_path, ntfy=True)
    r.behave["ntfy.example"] = "hang"
    snap = await r.prime()
    r.put(snap, "p1", state="missed")
    t0 = time.monotonic()
    out = r.nt.observe(r.at("2026-11-05T21:00"), snap)
    assert time.monotonic() - t0 < 0.5 and [m.kind for m in out] == ["empty_side"]
    for t in list(r.nt._tasks):
        t.cancel()


async def test_a_rejected_ntfy_token_is_logged_in_plain_words_and_nothing_crashes(tmp_path):
    r = Rig(tmp_path, ntfy=True)
    r.behave["ntfy.example"] = 401
    await send(r, msg())
    fails = [d for d in r.nt.sent_log if not d.get("ok")]
    assert fails and "token" in fails[0]["error"]


# ───────────────────────── quiet hours ─────────────────────────
KIND_IN_QUIET = {            # kind → sent or held at 2 AM with someone's night running
    "problem": "held", "hub": "held", "digest": "held", "pet_question": "held", "morning": "held", "away_check": "held",
    "away_precool": "held", "empty_side": "held", "left_on": "held", "not_started": "sent", "maintenance": "sent",
}


@pytest.mark.parametrize("kind,expect", sorted(KIND_IN_QUIET.items()))
async def test_what_quiet_hours_hold_and_what_they_do_not(tmp_path, kind, expect):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    snap = await r.prime()
    sch = r.sch(snap, "p1")
    r.put(snap, "p1", state="rhythm")
    r.nt._snap = snap
    r.n.clock.jump_to(sch["bedtime"] + 3 * H)
    ok = r.nt.send(Message(kind, "Title " + kind, "Body.", "p1" if kind not in ("problem", "hub") else None))
    await r.drain()
    assert (expect == "sent") == bool(r.got), (kind, r.got)
    assert bool(r.c.store.get("notify_held")) == (expect == "held")


async def test_urgent_goes_out_in_quiet_hours_but_is_still_capped_and_deduplicated(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    snap = await r.prime()
    r.put(snap, "p1", state="rhythm")
    r.nt._snap = snap
    r.n.clock.jump_to(r.sch(snap, "p1")["bedtime"] + 3 * H)
    m = Message("hub", "Hub: safety", "Restart it.", None, urgent=True)
    assert r.nt.send(m) and not r.nt.send(m)                               # the same again: dropped
    assert r.events("notification_skipped")[-1]["why"] == "duplicate"


async def test_quiet_hours_switch_off_lets_everything_through(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    r.p1.settings.notify.quiet_hours = False
    snap = await r.prime()
    r.put(snap, "p1", state="rhythm")
    r.nt._snap = snap
    r.n.clock.jump_to(r.sch(snap, "p1")["bedtime"] + 3 * H)
    assert r.nt.send(Message("pet_question", "Was Biscuit on the bed?", "b", "p1"))


async def test_a_partner_asleep_holds_a_household_message_even_when_you_are_up(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    snap = await r.prime()
    r.put(snap, "p2", state="rhythm")
    r.nt._snap = snap
    r.n.clock.jump_to(r.sch(snap, "p2")["bedtime"] + 3 * H)
    assert not r.nt.send(Message("problem", "Can't reach the bed", "b", None))
    assert not r.got and len(r.c.store.get("notify_held")) == 1


# ───────────────────────── held messages come once, and only if still true ─────────────────────────
async def hold_one_night(r, kinds):
    """Hold the given kinds at 2 AM, return the morning (wake + 30 min) when everyone's up."""
    snap = await r.prime()
    sch = r.sch(snap, "p1")
    r.put(snap, "p1", state="rhythm")
    r.put(snap, "p2", state="rhythm")
    r.pv(snap, "p2")["schedule"] = dict(r.sch(snap, "p1"))
    r.nt._snap = snap
    r.n.clock.jump_to(sch["bedtime"] + 3 * H)
    for k in kinds:
        r.nt.send(k)
    assert not r.got
    return snap, sch


async def test_held_problem_arrives_once_after_quiet_hours_as_while_you_slept(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    snap, sch = await hold_one_night(r, [Message("problem", "Can't reach the bed", "b", None)])
    for pid in ("p1", "p2"):
        r.put(snap, pid, state="done")
    await r.observe(snap, at=sch["wake"] + 1800)
    got = r.sent("digest")
    assert sorted(g.person for g in got) == ["p1", "p2"] and all("Can't reach the bed" in g.body for g in got)
    await r.observe(snap, at=sch["wake"] + 2400)
    assert len(r.sent("digest")) == 2                                      # once each


async def test_held_hub_fault_is_told_only_to_owners_who_want_it_not_to_everyone(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    r.nt.owner_persons = lambda: {"p1"}
    snap, sch = await hold_one_night(r, [Message("hub", "Hub: Fan problem", "Check vents.", None)])
    for pid in ("p1", "p2"):
        r.put(snap, pid, state="done")
    await r.observe(snap, at=sch["wake"] + 1800)
    assert [g.person for g in r.sent("digest")] == ["p1"]


async def test_held_bed_trouble_is_not_told_to_someone_who_turned_that_off(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    r.p2.settings.notify.problems = False
    snap, sch = await hold_one_night(r, [Message("problem", "Can't reach the bed", "b", None)])
    for pid in ("p1", "p2"):
        r.put(snap, pid, state="done")
    await r.observe(snap, at=sch["wake"] + 1800)
    assert [g.person for g in r.sent("digest")] == ["p1"]


async def test_a_held_pre_condition_or_empty_side_note_is_not_told_in_the_morning(tmp_path):
    """"Your side is pre-conditioned but empty" from 2 AM is untrue by morning: it is dropped, not delivered late."""
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    snap, sch = await hold_one_night(r, [Message("empty_side", "Your side is pre-conditioned but empty", "b", "p1"),
                                         Message("away_precool", "Pre-conditioning is due, but you're not home", "b", "p1")])
    for pid in ("p1", "p2"):
        r.put(snap, pid, state="done")
    await r.observe(snap, at=sch["wake"] + 1800)
    assert not r.sent("digest") and not r.c.store.get("notify_held")


async def test_a_held_left_on_note_is_told_only_if_it_is_still_on_and_empty(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    snap, sch = await hold_one_night(r, [Message("left_on", "Left's side is on, but nobody's in it", "b", "p1")])
    r.put(snap, "p1", state="done", override=None, target_c=None)          # it was turned off since
    await r.observe(snap, at=sch["wake"] + 1800)
    assert not r.sent("digest")


async def test_nothing_is_delivered_to_someone_still_in_bed_and_held_stays_held(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    snap, sch = await hold_one_night(r, [Message("problem", "Can't reach the bed", "b", None)])
    await r.observe(snap, at=sch["wake"] - 1800)
    assert not r.got and r.c.store.get("notify_held")


async def test_the_morning_summary_carries_what_was_held_and_no_second_ping(tmp_path, monkeypatch):
    monkeypatch.setattr("embertide.engine.summary.summarize", lambda *a, **k: {"has_data": True, "in_bed_min": 470, "times_up": 0})
    monkeypatch.setattr("embertide.engine.away.night_verdict", lambda *a, **k: ("home", ""))
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    snap, sch = await hold_one_night(r, [Message("problem", "Can't reach the bed", "b", None)])
    for pid in ("p1", "p2"):
        r.put(snap, pid, state="done")
        r.pv(snap, pid)["schedule"] = dict(sch)
    await r.observe(snap, at=sch["end"] + 300)
    morning = r.sent("morning")
    assert sorted(g.person for g in morning) == ["p1", "p2"] and all("Can't reach the bed" in g.body for g in morning)
    await r.observe(snap, at=sch["end"] + 4000)
    assert not r.sent("digest")


# ───────────────────────── caps and de-duplication ─────────────────────────
async def test_the_same_message_twice_within_six_hours_is_dropped_and_allowed_after(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.at("2026-11-05T12:00")
    assert await send(r, msg())
    r.n.clock.advance(5 * H)
    assert not await send(r, msg())
    assert r.events("notification_skipped")[-1]["why"] == "duplicate"
    r.n.clock.advance(2 * H)
    assert await send(r, msg())
    assert len(r.got) == 2


async def test_one_person_gets_at_most_four_capped_pings_a_day_and_the_counts_reset_at_midnight(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.at("2026-11-05T10:00")
    kinds = ["away_precool", "empty_side", "left_on", "not_started", "pet_question", "morning"]
    results = [r.nt.send(Message(k, f"T {k}", "b", "p1")) for k in kinds]
    await r.drain()
    assert results == [True] * 4 + [False] * 2
    assert {e["why"] for e in r.events("notification_skipped")} == {"cap"}
    r.at("2026-11-06T10:00")
    assert r.nt.send(Message("morning", "T morning", "b", "p1"))


async def test_each_kind_has_its_own_daily_limit(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    r.at("2026-11-05T10:00")
    assert r.nt.send(Message("empty_side", "A", "b", "p1")) and not r.nt.send(Message("empty_side", "B", "b", "p1"))
    assert r.nt.send(Message("empty_side", "A", "b", "p2"))                          # the partner has their own count
    assert [r.nt.send(Message("left_on", f"L{i}", "b", "p1")) for i in range(3)] == [True, True, False]
    assert [r.nt.send(Message("problem", f"P{i}", "b", None)) for i in range(4)] == [True, True, True, False]


async def test_tests_and_done_confirmations_are_never_capped_or_deduplicated(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.at("2026-11-05T10:00")
    assert all(r.nt.send(Message("test", "Notifications work", "b", "p1")) for _ in range(6))
    assert all(r.nt.send(Message("action_done", "Done", "b", "p1", app=True)) for _ in range(6))


# ───────────────────────── partner privacy ─────────────────────────
async def test_one_persons_private_messages_never_reach_the_partners_phones_or_targets(tmp_path, monkeypatch):
    monkeypatch.setattr("embertide.engine.summary.summarize", lambda *a, **k: {"has_data": True, "in_bed_min": 470, "times_up": 3, "hot_flashes": 2, "cold_flashes": 0})
    monkeypatch.setattr("embertide.engine.away.night_verdict", lambda *a, **k: ("home", ""))
    r = Rig(tmp_path, ntfy=True, ha=True)
    r.phone("p1", "fcm")
    r.phone("p2", "up")
    snap = await r.prime()
    sch = r.sch(snap, "p1")
    r.put(snap, "p1", state="done")
    await r.observe(snap, at=sch["end"] + 300)
    r.put(snap, "p1", state="away", away=None)
    await r.observe(snap, at=sch["precool"] + 86400 + 60)
    mine = [g for g in r.got if g.person == "p1"]
    assert mine and not [g for g in r.got if g.person == "p2"]                        # the partner received nothing at all
    for g in r.got:
        if g.person == "p2":
            assert "Left" not in g.title + g.body


async def test_household_messages_carry_no_private_data(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "up")
    snap = await r.prime()
    snap["system"].update(online=False, detail="no broker")
    await r.observe(snap, at=r.now)
    await r.observe(snap, at=r.now + 301)
    for g in r.sent("problem"):
        assert "hr" not in g.raw and g.body.count("Left") == 0 and g.body.count("Right") == 0


# ───────────────────────── server restarts ─────────────────────────
async def test_a_restart_never_resends_what_was_already_sent(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    snap = await r.prime()
    sch = r.sch(snap, "p1")
    r.put(snap, "p1", state="away", away=None)
    await r.observe(snap, at=sch["precool"] + 60)
    assert len(r.sent("away_precool")) == 1
    r.restart()
    await r.observe(snap, at=sch["precool"] + 120)
    await r.observe(snap, at=sch["precool"] + 1200)
    assert len(r.sent("away_precool")) == 1


async def test_a_restart_during_an_outage_does_not_send_the_alert_again(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    snap = await r.prime()
    snap["system"].update(online=False, detail="no broker")
    await r.observe(snap, at=r.now)
    await r.observe(snap, at=r.now + 301)
    assert len(r.sent("problem")) == 1
    r.restart()
    await r.observe(snap, at=r.now + 60)
    await r.observe(snap, at=r.now + 400)                                   # the new process has its own 5 minutes
    assert len(r.sent("problem")) == 1


async def test_held_messages_survive_a_restart_and_are_delivered_after_quiet_hours(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    snap, sch = await hold_one_night(r, [Message("problem", "Can't reach the bed", "b", None)])
    r.restart()
    for pid in ("p1", "p2"):
        r.put(snap, pid, state="done")
    await r.observe(snap, at=sch["wake"] + 1800)
    assert sorted(g.person for g in r.sent("digest")) == ["p1", "p2"]


async def test_button_codes_and_daily_counts_survive_a_restart(tmp_path):
    r = Rig(tmp_path, ntfy=True)
    r.at("2026-11-05T10:00")
    await send(r, msg())
    code = r.got[0].codes[0]
    r.restart()
    assert r.nt.peek_action(code) is not None                                # the button on the phone still works
    assert r.nt._count("p1", "left_on", r.now)[0] == 1                      # the count was not forgotten
    assert r.nt.send(msg(title="A second one")) and not r.nt.send(msg(title="A third one"))   # so the day's limit of 2 still holds
