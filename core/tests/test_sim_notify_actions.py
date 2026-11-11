"""Notification simulation, part 3: the buttons. Each one works once, a second tap is refused politely, it expires, it
only ever acts on its own person's side, it works when the app is signed out (the phone's device key), and the quiet
"Done: ..." follows where the code promises one. Also: a stale button is not acted on wrongly."""

import pytest
from fastapi.testclient import TestClient

from embertide import care as carelib
from embertide.drivers.profiles import ORION
from embertide.engine import away, petq
from embertide.notify import APP_ACTS, Action, Message
from notify_rig import Rig
from test_device_tokens import APP, home, mint  # noqa: F401  (fixture: owner alex/p1, sleeper sam/p2, viewer vic/p2)
from test_sim_notify_triggers import open_question

H = 3600


async def setup(tmp_path, start="2026-11-05T14:00", **kw):
    r = Rig(tmp_path, start, **kw)
    r.n.bed.profile = ORION
    snap = await r.prime()
    r.n.clock.advance(700)
    return r, snap


def press(r, person, act, **kw):
    """Make a button's single-use code and press it (what a tap on ntfy / Home Assistant / the confirm page does)."""
    code = r.nt.new_action(person, Action(kw.pop("label", act), act, **kw))
    return code, r.nt.use_action(code)


# ───────────────────────── every button does its one thing ─────────────────────────
async def test_pause_for_an_hour(tmp_path):
    r, _ = await setup(tmp_path)
    _, used = press(r, "p1", "pause", minutes=60)
    assert r.c.engine_for("p1").s.pause_until == pytest.approx(r.now + 3600, abs=5)
    assert used["done"].startswith("Paused Left's side until ")


async def test_off_tonight_start_now_resume_and_turn_it_off(tmp_path):
    r, _ = await setup(tmp_path)
    e = r.c.engine_for("p1")
    _, u = press(r, "p1", "skip")
    assert (e.s.skip or e.s.skip_next) and u["done"] == "Left's side is off for tonight"
    _, u = press(r, "p1", "resume")
    assert not e.s.skip and not e.s.skip_next and u["done"] == "Left's side is back on"
    _, u = press(r, "p1", "start_now")
    assert e.s.started and u["done"] == "Started Left's night"
    e.adjust(r.now, temp_c=20.0, minutes=90)
    _, u = press(r, "p1", "manual_off")
    assert e.s.override is None and u["done"] == "Left's side is off"


@pytest.mark.parametrize("act,temp,text", [("feel_cold", "cold", "Noted: too cold"), ("feel_right", "right", "Noted: just right"),
                                           ("feel_warm", "warm", "Noted: too warm")])
async def test_check_in_buttons_store_how_it_felt(tmp_path, act, temp, text):
    r, _ = await setup(tmp_path, "2026-11-06T08:00")
    _, u = press(r, "p1", act)
    assert u["done"] == text
    assert r.c.store.get("feedback:p1:2026-11-05")["temp"] == temp
    assert r.c.store.get("feedback:p2:2026-11-05") is None


@pytest.mark.parametrize("act,answer,text", [("pet_yes", "yes", "Thanks: noted as the pet"), ("pet_me", "me", "Thanks: noted as you"),
                                             ("pet_unsure", "unsure", "No problem: nothing changed")])
async def test_pet_buttons_answer_the_question(tmp_path, act, answer, text):
    r, _ = await setup(tmp_path, "2026-11-06T12:00")
    q = open_question(r)
    _, u = press(r, "p1", act, ref=q["id"])
    assert u["done"] == text
    got = next(x for x in petq.questions(r.c.store) if x["id"] == q["id"])
    assert got["status"] == "answered" and got["answer"] == answer


@pytest.mark.parametrize("act,text", [("away_away", "Marked away: your side is off until you're back"), ("away_night", "Noted: that night is left out"),
                                      ("away_home", "Noted: you were home")])
async def test_away_check_buttons(tmp_path, act, text):
    r, _ = await setup(tmp_path, "2026-11-06T09:00")
    away._save_checks(r.c.store, "p1", {"2026-11-05": {"kind": "away", "status": "asked", "why": "x", "at": r.now}})
    _, u = press(r, "p1", act, ref="2026-11-05")
    assert u["done"] == text
    assert away.checks(r.c.store, "p1")["2026-11-05"]["status"] != "asked"
    assert away.is_away(r.c.store, "p1", r.now) == (act == "away_away")
    assert not away.is_away(r.c.store, "p2", r.now)


async def test_welcome_back_buttons(tmp_path):
    r, _ = await setup(tmp_path, "2026-11-06T09:00")
    away.start(r.c, "p1", r.now - 86400, by="p1")
    away._save_checks(r.c.store, "p1", {"2026-11-05": {"kind": "back", "status": "asked", "why": "x", "at": r.now}})
    _, u = press(r, "p1", "away_back", ref="2026-11-05")
    assert u["done"] == "Welcome back: your side runs again tonight" and not away.is_away(r.c.store, "p1", r.now)


async def test_care_buttons_mark_done_or_put_off_a_week(tmp_path):
    r, _ = await setup(tmp_path, "2026-11-10T12:00")
    carelib.of(r.c).edit("tablets", {"last_done": "2026-01-01"})
    _, u = press(r, "p1", "care_later", ref="tablets")
    assert u["done"] == "I'll remind you next week."
    _, u = press(r, "p1", "care_done", ref="tablets")
    assert u["done"].startswith("Marked done.") and carelib.of(r.c).get("tablets")["last_done"] == "2026-11-10"
    _, u = press(r, "p1", "care_done", ref="gone_job")               # a job that no longer exists: polite, no crash
    assert u["done"]


async def test_every_app_button_has_a_specific_done_message(tmp_path):
    """No button ends in the generic "Done: <label>" except one we don't know."""
    r, _ = await setup(tmp_path)
    for act in sorted(APP_ACTS - {"pause"}):            # (pause names the time it ends: tested above)
        a = {"person": "p1", "act": act, "minutes": 60, "label": "Some label", "ref": "x", "expires": 0}
        text = r.nt.done_text(a)
        assert text and not text.startswith("Done:"), act


# ───────────────────────── once, politely, and it expires ─────────────────────────
async def test_a_button_works_once_and_the_second_tap_is_refused(tmp_path):
    r, _ = await setup(tmp_path)
    code, first = press(r, "p1", "pause", minutes=60)
    assert first
    assert r.nt.use_action(code) is None and r.nt.peek_action(code) is None


async def test_buttons_expire_after_twelve_hours(tmp_path, monkeypatch):
    import time
    r, _ = await setup(tmp_path)
    code = r.nt.new_action("p1", Action("Off tonight", "skip"))
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 11.5 * H)
    assert r.nt.peek_action(code) is not None
    monkeypatch.setattr(time, "time", lambda: real() + 12.5 * H)
    assert r.nt.peek_action(code) is None and r.nt.use_action(code) is None
    assert not r.c.engine_for("p1").s.skip and not r.c.engine_for("p1").s.skip_next


async def test_codes_are_unguessable_and_stored_only_as_hashes(tmp_path):
    r, _ = await setup(tmp_path)
    code = r.nt.new_action("p1", Action("Off tonight", "skip"))
    assert len(code) >= 20 and code not in str(r.c.store.get("notify_actions"))
    assert r.nt.use_action("made-up") is None and r.nt.use_action("") is None


def test_the_web_endpoint_says_it_politely_and_works_without_signing_in(tmp_path):
    from test_notify import make
    with make(tmp_path, auth=True) as c:
        c.post("/api/auth/setup", json={"username": "alex", "password": "correct horse battery", "person": "p1"})
        nt = c.app.state.notifier
        code = nt.new_action("p1", Action("Wait an hour", "pause", 60))
        stranger = TestClient(c.app)
        assert stranger.get(f"/api/notify/act/{code}").json() == {"label": "Wait an hour", "act": "pause", "person_name": "Left", "minutes": 60}
        r = stranger.post(f"/api/notify/act/{code}")
        assert r.status_code == 200 and r.json()["done"].startswith("Paused Left's side until ")
        r2 = stranger.post(f"/api/notify/act/{code}")
        assert r2.status_code == 404 and r2.json()["detail"] == "This button has expired or was already used."
        assert stranger.get(f"/api/notify/act/{code}").status_code == 404


def test_the_test_button_changes_nothing_and_is_used_up(tmp_path):
    from test_notify import make
    with make(tmp_path) as c:
        nt = c.app.state.notifier
        code = nt.new_action("p1", Action("Looks good", "noop"))
        assert c.post(f"/api/notify/act/{code}").json()["done"] == "Nothing to change. Notifications work."
        assert c.post(f"/api/notify/act/{code}").status_code == 404


# ───────────────────────── only their own side ─────────────────────────
async def test_a_button_for_one_person_never_touches_the_other_side(tmp_path):
    r, _ = await setup(tmp_path)
    e1, e2 = r.c.engine_for("p1"), r.c.engine_for("p2")
    for act in ("pause", "skip", "start_now"):
        press(r, "p1", act, minutes=60)
    assert e2.s.pause_until is None and not e2.s.skip and not e2.s.skip_next and not e2.s.started
    e2.adjust(r.now, temp_c=21.0, minutes=60)
    press(r, "p1", "manual_off")
    assert e2.s.override is not None                                           # Right's nap is still on


async def test_another_persons_question_cannot_be_answered_with_my_button(tmp_path):
    r, _ = await setup(tmp_path, "2026-11-06T12:00")
    q = open_question(r, "p2")
    _, u = press(r, "p1", "pet_yes", ref=q["id"])                              # Left's button, Right's question
    assert u and next(x for x in petq.questions(r.c.store) if x["id"] == q["id"])["status"] == "open"


async def test_a_care_or_away_button_cannot_answer_for_the_partner(tmp_path):
    r, _ = await setup(tmp_path, "2026-11-06T09:00")
    away._save_checks(r.c.store, "p2", {"2026-11-05": {"kind": "away", "status": "asked", "why": "x", "at": r.now}})
    press(r, "p1", "away_away", ref="2026-11-05")                              # Left has no such question
    assert away.checks(r.c.store, "p2")["2026-11-05"]["status"] == "asked" and not away.is_away(r.c.store, "p2", r.now)


# ───────────────────────── the quiet "Done" follows ─────────────────────────
async def test_done_follows_a_link_button_on_ntfy_and_home_assistant_with_no_second_ping_in_the_app(tmp_path):
    r, _ = await setup(tmp_path, ntfy=True, ha=True)
    r.nt.send(Message("empty_side", "Your side is pre-conditioned but empty", "b", "p1", [Action("Off tonight", "skip")]))
    await r.drain()
    nt_msg = r.sent("empty_side", via="ntfy")[0]
    r.got.clear()
    used = r.nt.use_action(nt_msg.codes[0])
    await r.drain()
    done = r.sent("action_done")
    assert used and sorted((g.via, g.person) for g in done) == [("ha", "p1"), ("ntfy", "p1")]
    assert all(g.title == "Left's side is off for tonight" for g in done)
    assert not r.sent(person="p2")


async def test_a_button_pressed_in_the_app_or_web_page_logs_no_false_undelivered(tmp_path):
    r, _ = await setup(tmp_path)
    r.phone("p1", "fcm")
    press(r, "p1", "pause", minutes=60)
    await r.drain()
    assert not r.got and not r.events("notification_undelivered")             # the app showed its own "done"


# ───────────────────────── the stale button ─────────────────────────
async def test_a_button_from_last_evening_does_not_start_or_skip_the_next_night(tmp_path):
    """Tapped the next morning (the code is still within 12 hours), "Start anyway" must not heat the bed at 7 AM, and
    "Off tonight" must not quietly switch off a night that this message was never about."""
    r, snap = await setup(tmp_path, "2026-11-05T20:50")
    sch = r.sch(snap, "p1")
    r.put(snap, "p1", state="away", away=None)
    await r.observe(snap, at=sch["precool"] + 60)
    codes = r.sent("away_precool", person="p1")
    r.nt._snap = snap
    ntfy_less = r.nt.new_action("p1", Action("Start anyway", "start_now"))     # codes made while the evening is current
    skip = r.nt.new_action("p1", Action("Off tonight", "skip"))
    pause = r.nt.new_action("p1", Action("Wait an hour", "pause", 60))
    r.n.clock.jump_to(sch["end"] + 1800)                                       # next morning, 30 min after the night ended
    e = r.c.engine_for("p1")
    for code in (ntfy_less, skip, pause):
        assert r.nt.peek_action(code) is None and r.nt.use_action(code) is None
    assert not e.s.started and not e.s.skip and not e.s.skip_next and e.s.pause_until is None


async def test_a_night_button_still_works_during_the_night_it_was_about(tmp_path):
    r, snap = await setup(tmp_path, "2026-11-05T20:50")
    sch = r.sch(snap, "p1")
    r.nt._snap = snap
    code = r.nt.new_action("p1", Action("Start anyway", "start_now"))
    r.n.clock.jump_to(sch["bedtime"] + 2 * H)
    assert r.nt.use_action(code)
    assert r.c.engine_for("p1").s.started


# ───────────────────────── signed out: the phone's own key ─────────────────────────
APP_BUTTON_CALLS = [
    ("pause", "/api/people/{p}/tonight", {"action": "pause", "minutes": 60, "source": "notification"}),
    ("skip", "/api/people/{p}/tonight", {"action": "skip", "source": "notification"}),
    ("start_now", "/api/people/{p}/tonight", {"action": "start_now", "source": "notification"}),
    ("resume", "/api/people/{p}/tonight", {"action": "resume", "source": "notification"}),
    ("manual_off", "/api/people/{p}/adjust/clear?source=notification", None),
    ("feel_warm", "/api/people/{p}/feedback", {"temp": "warm"}),
    ("pet_yes", "/api/people/{p}/pet-question/abc123", {"answer": "yes"}),
    ("away_night", "/api/people/{p}/away-check", {"night": "2026-01-01", "answer": "night"}),
    ("care_done", "/api/care/hub_check/done", {}),
    ("care_later", "/api/care/hub_check/snooze", {}),
]


def test_every_app_button_has_a_call_the_signed_out_phone_key_may_make(home):
    c, s = home
    for who, pid, other in (("sam", "p2", "p1"), ("alex", "p1", "p2")):
        key, _ = mint(c, s[who], "phone")
        bare = TestClient(c.app)                       # no cookie, no session header: only the phone's own key
        for act, path, body in APP_BUTTON_CALLS:
            mine = bare.post(path.format(p=pid), json=body, headers={**key, **APP}) if body is not None else bare.post(path.format(p=pid), headers={**key, **APP})
            assert mine.status_code not in (401, 403), (who, act, mine.status_code, mine.text)
            if "{p}" in path:
                theirs = bare.post(path.format(p=other), json=body, headers={**key, **APP}) if body is not None else bare.post(path.format(p=other), headers={**key, **APP})
                assert theirs.status_code == 403, (who, act, "reached the partner", theirs.status_code)


def test_the_app_buttons_are_idempotent_not_double_acting(home):
    """A second tap on the app's own (key-based) buttons does no harm: pausing twice, answering twice."""
    c, s = home
    key, _ = mint(c, s["alex"], "phone")
    bare = TestClient(c.app)
    h = {**key, **APP}
    assert bare.post("/api/people/p1/tonight", json={"action": "skip", "source": "notification"}, headers=h).status_code == 200
    assert bare.post("/api/people/p1/tonight", json={"action": "skip", "source": "notification"}, headers=h).status_code == 200
    first = bare.post("/api/people/p1/feedback", json={"temp": "warm"}, headers=h)
    second = bare.post("/api/people/p1/feedback", json={"temp": "warm"}, headers=h)
    assert first.status_code == second.status_code == 200
