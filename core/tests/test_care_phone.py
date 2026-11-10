"""Care reminders on the phone: the notification buttons (a phone device key may mark a job done or put it off, and
nothing else under /api/care), the wording, and who receives the reminder (owners and sleepers with their own switch on,
nobody who is view-only or marked away). Everything here is synthetic."""

from fastapi.testclient import TestClient

from embertide import care as carelib
from embertide.engine import away
from test_care import DAY, care_msgs, due_tablets, orion_rig
from test_device_tokens import APP, home, mint  # noqa: F401  (fixture: owner alex/p1, sleeper sam/p2, viewer vic/p2)


def test_a_phone_key_can_mark_done_and_snooze_for_owner_and_sleeper_not_viewer_and_nothing_else(home):
    c, sess = home
    for who in ("alex", "sam"):
        key, _ = mint(c, sess[who])
        h = {**key, **APP}
        kc = TestClient(c.app)
        d = kc.post("/api/care/hub_check/done", json={}, headers=h)
        assert d.status_code == 200 and d.json()["after_done"].startswith("Marked done. Next reminder in about ")
        s = kc.post("/api/care/hub_check/snooze", headers=h)
        assert s.status_code == 200 and s.json()["after_snooze"] == "I'll remind you next week."
        assert kc.put("/api/care/hub_check", json={"interval_days": 5}, headers=h).status_code == 403
        assert kc.post("/api/care", json={"title": "x", "interval_days": 5}, headers=h).status_code == 403
        assert kc.delete("/api/care/hub_check", headers=h).status_code == 403
        assert kc.get("/api/care", headers=h).status_code == 403             # reading the list is not in the allowlist
    key, _ = mint(c, sess["vic"])
    h = {**key, **APP}
    kc = TestClient(c.app)
    assert kc.post("/api/care/hub_check/done", json={}, headers=h).status_code == 403
    assert kc.post("/api/care/hub_check/snooze", headers=h).status_code == 403


def test_a_watch_key_cannot_touch_care(home):
    c, sess = home
    key, _ = mint(c, sess["alex"], scope="watch")
    assert TestClient(c.app).post("/api/care/hub_check/done", json={}, headers={**key, **APP}).status_code == 403


def test_the_app_channel_payload_carries_both_buttons_with_the_job(tmp_path):
    r = orion_rig(tmp_path)
    due_tablets(r)
    m = care_msgs(r)[0]
    from embertide.notify import APP_ACTS
    assert [a.act for a in m.actions] == ["care_done", "care_later"] and all(a.act in APP_ACTS for a in m.actions)
    assert [a.label for a in m.actions] == ["It's done", "Remind me next week"]
    assert all(a.ref == "tablets" for a in m.actions)


def test_confirmation_strings_follow_the_interval():
    assert carelib.after_text(True, 183) == "Marked done. Next reminder in about 6 months."
    assert carelib.after_text(True, 90) == "Marked done. Next reminder in about 3 months."
    assert carelib.after_text(True, 14) == "Marked done. Next reminder in about 2 weeks."
    assert carelib.after_text(True, 365) == "Marked done. Next reminder in about a year."
    assert carelib.after_text(False, 183) == "I'll remind you next week."


def test_recipients_follow_each_persons_own_switch(tmp_path):
    r = orion_rig(tmp_path)
    carelib.of(r.c).edit("tablets", {"last_done": "2026-01-01"})
    r.c.notifier.care_persons = lambda: {"p1", "p2"}
    r.c.person("p2").settings.notify.maintenance = False
    assert [m.person for m in care_msgs(r)] == ["p1"]                  # owner on, sleeper off
    r2 = orion_rig(tmp_path / "b")
    carelib.of(r2.c).edit("tablets", {"last_done": "2026-01-01"})
    r2.c.notifier.care_persons = lambda: {"p1", "p2"}
    assert sorted(m.person for m in care_msgs(r2)) == ["p1", "p2"]     # both on: one message each
    assert care_msgs(r2) == []                                          # and no second one the same day


def test_view_only_and_away_people_do_not_get_it(tmp_path):
    r = orion_rig(tmp_path)
    carelib.of(r.c).edit("tablets", {"last_done": "2026-01-01"})
    r.c.notifier.care_persons = lambda: {"p1"}                          # p2 has no owner or sleeper account
    assert [m.person for m in care_msgs(r)] == ["p1"]
    r2 = orion_rig(tmp_path / "b")
    carelib.of(r2.c).edit("tablets", {"last_done": "2026-01-01"})
    r2.c.notifier.care_persons = lambda: {"p1", "p2"}
    away.start(r2.c, "p2", r2.clock.now_ts())
    assert [m.person for m in care_msgs(r2)] == ["p1"]


def test_one_person_acting_ends_the_follow_up_for_both(tmp_path):
    for act in (0, 1):
        r = orion_rig(tmp_path / str(act))
        carelib.of(r.c).edit("tablets", {"last_done": "2026-01-01"})
        n = r.c.notifier
        n.care_persons = lambda: {"p1", "p2"}
        first = care_msgs(r)
        assert len(first) == 2
        by_p = {m.person: m for m in first}
        n.use_action(n.new_action("p2", by_p["p2"].actions[act]))
        for _ in range(10 if act == 0 else 6):                          # done: silent for good; later: silent for the week asked for
            r.clock.advance(DAY)
            assert care_msgs(r) == []


def test_a_sleeper_can_change_their_own_maintenance_switch(home):
    c, sess = home
    kc = TestClient(c.app)
    body = {"notify": {"maintenance": False}}
    p = kc.patch("/api/people/p2/settings", json=body, headers=sess["sam"])
    assert p.status_code == 200, p.text
    assert kc.patch("/api/people/p1/settings", json=body, headers=sess["sam"]).status_code == 403    # not the owner's
    assert kc.patch("/api/people/p2/settings", json=body, headers=sess["vic"]).status_code == 403    # view-only can't
    snap = kc.get("/api/snapshot", headers=sess["sam"]).json()
    assert next(x for x in snap["people"] if x["id"] == "p2")["settings"]["notify"]["maintenance"] is False
