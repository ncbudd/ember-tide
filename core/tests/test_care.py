"""Care and maintenance reminders: due-date maths (done, snooze, interval change, your own tasks, restarts), who may do
what, the notification's timing and caps and its one follow-up, the low-water tie-in, input checks, the profiles and
the Home Assistant entities. Everything here is synthetic."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from embertide import care as carelib
from embertide.app import create_app
from embertide.config import Config
from embertide.drivers.profiles import GENERIC, ORION
from sim_edges import Rig, ts

H = {"X-Embertide": "1"}
PW = "correct horse battery"
DAY = 24 * 3600


def orion_rig(tmp_path, start="2026-11-10T12:00") -> Rig:
    r = Rig(tmp_path, start)
    r.bed.profile = ORION                       # the simulator wearing the Orion profile
    return r


def by_id(r, tid: str) -> dict:
    return next(t for t in carelib.of(r.c).tasks() if t["id"] == tid)


# ---------- the profiles ----------
def test_profiles_are_well_formed_and_tablets_are_a_maker_task():
    for prof in (GENERIC, ORION):
        ids = [t["id"] for t in prof["tasks"]]
        assert len(ids) == len(set(ids)) and "water" in ids
        for t in prof["tasks"]:
            assert t["title"] and t["why"] and t["steps"] and t["source"] in ("maker", "default")
            assert (t.get("trigger") == "hub_water") == (t["interval_days"] is None)
    tablets = next(t for t in ORION["tasks"] if t["id"] == "tablets")
    assert tablets["source"] == "maker" and tablets["url"].startswith("https://help.orionsleep.com/") and tablets["interval_days"] == 180
    assert "2" in tablets["supplies"]
    assert ORION["light_guide"]["items"][0][0] == "Blue" and "not a status" in ORION["light_guide"]["note"]
    assert GENERIC["light_guide"] is None


def test_nothing_personal_or_the_banned_word_in_the_profiles():
    blob = repr(ORION) + repr(GENERIC)
    assert ("tow" + "er") not in blob.lower()


# ---------- due-date maths ----------
def test_a_new_install_counts_from_the_first_day_and_asks_nothing_yet(tmp_path):
    r = orion_rig(tmp_path)
    t = by_id(r, "tablets")
    assert t["last_done"] is None and t["counting_from"] == "2026-11-10" and t["days_until"] == 180
    assert t["status_text"] == "Due in 180 days" and not t["due"] and t["source_label"] == "From the maker's instructions"
    assert by_id(r, "hub_check")["source_label"] == "Suggested default"
    assert carelib.of(r.c).summary()["due_now"] == []


def test_last_done_sets_the_due_date_and_the_wording(tmp_path):
    r = orion_rig(tmp_path)
    care = carelib.of(r.c)
    care.edit("tablets", {"last_done": "2026-09-30"})           # "I last did this on..."
    t = by_id(r, "tablets")
    assert t["due_date"] == "2027-03-29" and t["days_until"] == 139 and t["status"] == "ok"
    care.edit("tablets", {"interval_days": 40})
    t = by_id(r, "tablets")
    assert t["due_date"] == "2026-11-09" and t["days_until"] == -1 and t["status_text"] == "1 day overdue" and t["due_now"]
    care.edit("tablets", {"interval_days": 41})
    assert by_id(r, "tablets")["status_text"] == "Due today"
    care.edit("tablets", {"interval_days": 42})
    assert by_id(r, "tablets")["status_text"] == "Due tomorrow"
    care.edit("tablets", {"interval_days": 49})
    t = by_id(r, "tablets")
    assert t["status_text"] == "Due in 8 days" and t["status"] == "ok"
    care.edit("tablets", {"interval_days": 48})
    assert by_id(r, "tablets")["status"] == "soon"
    care.edit("tablets", {"interval_days": 30})
    assert by_id(r, "tablets")["status_text"] == "11 days overdue"


def test_done_defaults_to_today_and_takes_a_date_and_clears_a_snooze(tmp_path):
    r = orion_rig(tmp_path)
    care = carelib.of(r.c)
    care.edit("tablets", {"last_done": "2026-01-01"})
    assert by_id(r, "tablets")["due_now"]
    care.snooze("tablets")
    assert by_id(r, "tablets")["due"] and not by_id(r, "tablets")["due_now"] and by_id(r, "tablets")["snoozed_until"] == "2026-11-17"
    care.done("tablets")
    t = by_id(r, "tablets")
    assert t["last_done"] == "2026-11-10" and t["snoozed_until"] is None and t["days_until"] == 180
    care.done("tablets", "2026-11-01")
    assert by_id(r, "tablets")["last_done"] == "2026-11-01" and by_id(r, "tablets")["days_until"] == 171


def test_snooze_ends_after_a_week_and_can_be_set_to_other_lengths(tmp_path):
    r = orion_rig(tmp_path)
    care = carelib.of(r.c)
    care.edit("tablets", {"last_done": "2026-01-01"})
    care.snooze("tablets")
    r.clock.advance(6 * DAY)
    assert not by_id(r, "tablets")["due_now"]
    r.clock.advance(1 * DAY)
    assert by_id(r, "tablets")["due_now"] and by_id(r, "tablets")["snoozed_until"] is None
    care.snooze("tablets", 3)
    assert by_id(r, "tablets")["snoozed_until"] == "2026-11-20"


def test_turning_a_task_off_hides_it_from_due(tmp_path):
    r = orion_rig(tmp_path)
    care = carelib.of(r.c)
    care.edit("tablets", {"last_done": "2026-01-01"})
    care.edit("tablets", {"enabled": False})
    t = by_id(r, "tablets")
    assert not t["due"] and t["status"] == "off" and t["status_text"] == "Turned off"


def test_your_own_tasks_can_be_added_changed_and_deleted(tmp_path):
    r = orion_rig(tmp_path)
    care = carelib.of(r.c)
    t = care.add("Wash the cover", 30, why="Keeps it fresh.", steps=["Take it off.", "Wash it."], supplies="Mild soap", last_done="2026-10-20")
    assert t["custom"] and t["source_label"] == "Added by you" and t["days_until"] == 9 and t["id"].startswith("c_")
    t = care.edit(t["id"], {"title": "Wash the topper cover", "steps": ["Take it off."], "interval_days": 14})
    assert t["title"] == "Wash the topper cover" and t["steps"] == ["Take it off."] and t["days_until"] == -7
    with pytest.raises(carelib.CareError):
        care.add("wash the TOPPER cover", 10)                     # same name
    care.delete(t["id"])
    with pytest.raises(KeyError):
        care.get(t["id"])
    with pytest.raises(carelib.CareError):
        care.delete("tablets")                                     # built-in: turn off, not delete
    with pytest.raises(carelib.CareError):
        care.edit("tablets", {"title": "Other"})                   # built-in words stay with the profile
    for i in range(carelib.MAX_CUSTOM):
        care.add(f"Job {i}", 10)
    with pytest.raises(carelib.CareError):
        care.add("One too many", 10)


def test_it_survives_a_restart_and_a_new_install_date_is_kept(tmp_path):
    r = orion_rig(tmp_path)
    care = carelib.of(r.c)
    care.edit("tablets", {"last_done": "2026-09-30", "interval_days": 200})
    care.edit("hub_check", {"enabled": False})
    c = care.add("Wash the cover", 30)
    care.snooze("tablets")
    r.clock.advance(3 * DAY)
    r.boot()
    r.bed.profile = ORION
    t = by_id(r, "tablets")
    assert t["last_done"] == "2026-09-30" and t["interval_days"] == 200 and t["snoozed_until"] == "2026-11-17"
    assert not by_id(r, "hub_check")["enabled"] and by_id(r, "hub_check")["status"] == "off"
    assert by_id(r, c["id"])["counting_from"] == "2026-11-10"          # counted from when it was added, not from the restart
    assert carelib.of(r.c).listing()["since"] == "2026-11-10"


def test_the_wording_comes_from_the_profile_not_the_store(tmp_path):
    r = orion_rig(tmp_path)
    carelib.of(r.c).edit("tablets", {"interval_days": 100})
    stored = r.store.get("care")
    assert "steps" not in stored["tasks"]["tablets"] and "title" not in stored["tasks"]["tablets"]


# ---------- the water tie-in ----------
async def test_add_water_is_due_now_when_the_hub_says_the_water_is_low(tmp_path):
    r = orion_rig(tmp_path)
    await r.step()
    w = by_id(r, "water")
    assert not w["due"] and w["status_text"] == "Fine for now" and w["interval_days"] is None and len(w["steps"]) >= 5
    assert "distilled" in w["steps"][0].lower()
    r.bed.low_water = True
    await r.step()
    w = by_id(r, "water")
    assert w["due"] and w["due_now"] and w["status"] == "water" and w["status_text"] == "Needs water now"
    assert "water" in carelib.of(r.c).summary()["due_now"]
    # marking it done notes the date but doesn't hide a still-low hub
    carelib.of(r.c).done("water")
    assert by_id(r, "water")["last_done"] == "2026-11-10"
    r.bed.low_water = False
    await r.step()
    assert not by_id(r, "water")["due"]
    with pytest.raises(carelib.CareError):
        carelib.of(r.c).edit("water", {"interval_days": 30})


async def test_low_water_makes_no_second_maintenance_message(tmp_path):
    r = orion_rig(tmp_path)
    r.bed.low_water = True
    await r.step()
    n = r.c.notifier
    n.care_persons = lambda: {"p1"}
    assert care_msgs(r) == []                                      # the hub message already says it


# ---------- notifications ----------
def snap_of(state="day", present=None) -> dict:
    return {"system": {"online": True}, "people": [{"id": "p1", "zone": "left", "night": {"state": state}},
                                                   {"id": "p2", "zone": "right", "night": {"state": state}}]}


def care_msgs(r, snap=None) -> list:
    n = r.c.notifier
    n.__dict__["_care_at"] = 0
    snap = snap or snap_of()
    n._snap = snap
    out: list = []
    n._care(snap, r.clock.now_ts(), out)
    return out


def due_tablets(r):
    r.c.notifier.care_persons = lambda: {"p1"}
    carelib.of(r.c).edit("tablets", {"last_done": "2026-01-01"})


def test_the_message_is_calm_goes_to_the_owner_and_has_two_buttons(tmp_path):
    r = orion_rig(tmp_path)
    due_tablets(r)
    out = care_msgs(r)
    assert len(out) == 1
    m = out[0]
    assert (m.kind, m.person) == ("maintenance", "p1") and m.title == "Time to add purification tablets"
    assert "Keeps the water in the hub clean. The maker calls them cleaning tablets." in m.body
    assert [(a.label, a.act, a.ref) for a in m.actions] == [("It's done", "care_done", "tablets"), ("Remind me next week", "care_later", "tablets")]
    assert care_msgs(r) == []                                      # told: not again the same day


def test_with_no_owner_linked_everyone_who_wants_it_gets_it_and_the_switch_works(tmp_path):
    r = orion_rig(tmp_path)
    carelib.of(r.c).edit("tablets", {"last_done": "2026-01-01"})
    assert {m.person for m in care_msgs(r)} == {"p1", "p2"}
    r2 = orion_rig(tmp_path / "b")
    carelib.of(r2.c).edit("tablets", {"last_done": "2026-01-01"})
    r2.c.notifier.care_persons = lambda: {"p1"}
    r2.c.person("p1").settings.notify.maintenance = False
    assert care_msgs(r2) == []
    assert r2.c.notifier is not None and "maintenance" in __import__("embertide.models", fromlist=["NotifyPrefs"]).NotifyPrefs().model_dump()


@pytest.mark.parametrize("when,sent", [("2026-11-10T08:59", False), ("2026-11-10T09:00", True), ("2026-11-10T18:59", True),
                                       ("2026-11-10T19:00", False), ("2026-11-10T23:30", False), ("2026-11-10T03:00", False)])
def test_only_between_9_am_and_7_pm(tmp_path, when, sent):
    r = orion_rig(tmp_path, when)
    due_tablets(r)
    assert bool(care_msgs(r)) is sent


def test_never_while_a_night_is_running_or_in_quiet_hours(tmp_path):
    r = orion_rig(tmp_path, "2026-11-10T14:00")
    due_tablets(r)
    assert care_msgs(r, snap_of(state="rhythm")) == []             # someone's night is running (a nap on the bed counts)
    assert care_msgs(r, snap_of(state="out_of_bed_hold")) == []
    snap = snap_of()
    snap["people"][1]["schedule"] = {"bedtime": ts("2026-11-10T13:00"), "wake": ts("2026-11-10T20:00"), "end": ts("2026-11-10T20:30")}
    assert care_msgs(r, snap) == []                                # the other person's quiet hours also hold it
    assert care_msgs(r, snap_of())                                 # nothing held it back for good: it goes once all is calm


def test_at_most_one_a_day_and_the_rest_wait_for_tomorrow(tmp_path):
    r = orion_rig(tmp_path)
    r.c.notifier.care_persons = lambda: {"p1"}
    care = carelib.of(r.c)
    care.edit("tablets", {"last_done": "2026-01-01"})
    care.edit("hub_check", {"last_done": "2026-01-01"})
    out = care_msgs(r)
    assert len(out) == 1 and out[0].actions[0].ref == "hub_check" and "1 other job is due too." in out[0].body   # the most overdue first
    r.c.notifier.send(out[0])
    assert care_msgs(r) == []
    r.clock.advance(DAY)
    out = care_msgs(r)
    assert len(out) == 1 and out[0].actions[0].ref == "tablets"


def test_one_follow_up_after_a_week_then_silence_until_done(tmp_path):
    r = orion_rig(tmp_path)
    due_tablets(r)
    n = r.c.notifier
    first = care_msgs(r)
    assert first and n.send(first[0])
    for _ in range(6):
        r.clock.advance(DAY)
        assert care_msgs(r) == []
    r.clock.advance(DAY)                                            # day 7
    again = care_msgs(r)
    assert len(again) == 1 and again[0].title.endswith("time to add purification tablets")
    assert n.send(again[0])
    for _ in range(40):
        r.clock.advance(DAY)
        assert care_msgs(r) == []                                   # then nothing more
    carelib.of(r.c).done("tablets")
    carelib.of(r.c).edit("tablets", {"interval_days": 1})
    r.clock.advance(2 * DAY)
    assert len(care_msgs(r)) == 1                                   # marked done earlier: a new round starts when due again


def test_remind_me_next_week_pushes_the_follow_up_out_and_turning_back_on_resets(tmp_path):
    r = orion_rig(tmp_path)
    due_tablets(r)
    n = r.c.notifier
    first = care_msgs(r)
    assert n.send(first[0])
    code = n.new_action("p1", first[0].actions[1])
    a = n.use_action(code)
    assert a and a["done"] == "I'll remind you next week."
    assert by_id(r, "tablets")["snoozed_until"] == "2026-11-17"
    r.clock.advance(7 * DAY)
    again = care_msgs(r)
    assert len(again) == 1                                          # snooze over, a week since the first: the one follow-up
    n.send(again[0])
    carelib.of(r.c).edit("tablets", {"enabled": False})
    carelib.of(r.c).edit("tablets", {"enabled": True})
    r.clock.advance(DAY)
    assert len(care_msgs(r)) == 1                                   # re-enabled: it may speak up once more


def test_the_done_button_marks_it_done_once(tmp_path):
    r = orion_rig(tmp_path)
    due_tablets(r)
    n = r.c.notifier
    m = care_msgs(r)[0]
    code = n.new_action("p1", m.actions[0])
    a = n.use_action(code)
    assert a["done"] == "Marked done. Next reminder in about 6 months." and by_id(r, "tablets")["last_done"] == "2026-11-10" and not by_id(r, "tablets")["due"]
    assert n.use_action(code) is None                               # single use
    assert any(e.get("act") == "care_done" for e in r.events("notification_action"))


def test_a_button_for_a_deleted_task_does_nothing_and_does_not_break(tmp_path):
    r = orion_rig(tmp_path)
    n = r.c.notifier
    from embertide.notify import Action
    code = n.new_action("p1", Action("Done", "care_done", ref="c_gone"))
    assert n.use_action(code)["done"] == "Marked done."


def test_the_cap_for_the_kind_is_one_a_day(tmp_path):
    from embertide.notify import CAPS, OWN_TIMING_KINDS
    assert CAPS["maintenance"] == 1 and "maintenance" in OWN_TIMING_KINDS


async def test_the_real_observe_loop_sends_it_once(tmp_path):
    r = orion_rig(tmp_path, "2026-11-10T12:00")
    due_tablets(r)
    for _ in range(3):
        await r.step(30)
        r.c.notifier.__dict__["_care_at"] = 0
    assert r.notes().count("Time to add purification tablets") == 1


# ---------- who can do what, and input checks ----------
def make(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, True
    return TestClient(create_app(cfg))


def sign_in(owner):
    assert owner.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": "p1"}).status_code == 200
    code = owner.post("/api/auth/invites", json={"person": "p2", "role": "sleeper"}, headers=H).json()["code"]
    g = TestClient(owner.app)
    assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
    code = owner.post("/api/auth/invites", json={"person": None, "role": "viewer"}, headers=H).json()["code"]
    v = TestClient(owner.app)
    assert v.post("/api/auth/accept", json={"code": code, "username": "guest", "password": PW}).status_code == 200
    return g, v


def test_everyone_signed_in_can_read_a_sleeper_can_mark_done_only_the_owner_edits(tmp_path):
    with make(tmp_path) as owner:
        sleeper, viewer = sign_in(owner)
        for who in (owner, sleeper, viewer):
            d = who.get("/api/care")
            assert d.status_code == 200 and {t["id"] for t in d.json()["tasks"]} >= {"water", "hub_check"}
        # the simulator wears the generic profile: no light guide, and an owner can set the "last done" date
        assert owner.get("/api/care").json()["hub_guide"] is None
        assert owner.put("/api/care/hub_check", json={"last_done": "2026-08-01"}, headers=H).status_code == 200
        t = owner.get("/api/care").json()["tasks"]
        assert next(x for x in t if x["id"] == "hub_check")["last_done"] == "2026-08-01"
        # sleeper: done and snooze yes; edit, add, delete no
        assert sleeper.post("/api/care/hub_check/done", json={}, headers=H).status_code == 200
        assert sleeper.post("/api/care/hub_check/snooze", json={}, headers=H).status_code == 200
        assert sleeper.put("/api/care/hub_check", json={"interval_days": 5}, headers=H).status_code == 403
        assert sleeper.post("/api/care", json={"title": "x", "interval_days": 5}, headers=H).status_code == 403
        assert owner.post("/api/care", json={"title": "Wash the cover", "interval_days": 30}, headers=H).status_code == 200
        cid = next(x["id"] for x in owner.get("/api/care").json()["tasks"] if x["custom"])
        assert sleeper.delete(f"/api/care/{cid}", headers=H).status_code == 403
        # a view-only account can only look
        for call in (lambda: viewer.post("/api/care/hub_check/done", json={}, headers=H),
                     lambda: viewer.post("/api/care/hub_check/snooze", json={}, headers=H),
                     lambda: viewer.put("/api/care/hub_check", json={"enabled": False}, headers=H),
                     lambda: viewer.delete(f"/api/care/{cid}", headers=H)):
            assert call().status_code == 403
        # the browser-page guard: a change without the app's header is refused
        assert owner.post("/api/care/hub_check/done", json={}).status_code == 403
        assert owner.delete(f"/api/care/{cid}", headers=H).status_code == 200


def test_the_light_guide_reaches_everyone_with_the_orion_profile(tmp_path):
    with make(tmp_path) as owner:
        sleeper, viewer = sign_in(owner)
        owner.app.state.notifier.c.driver.profile = ORION
        for who in (owner, sleeper, viewer):
            g = who.get("/api/care").json()["hub_guide"]
            assert g["source"] == "From the maker's own guide" and ["Red", "Updating. Leave it alone and don't unplug it."] in g["items"]


def test_input_is_checked(tmp_path):
    with make(tmp_path) as o:
        o.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": "p1"})
        put = lambda tid, body: o.put(f"/api/care/{tid}", json=body, headers=H)                       # noqa: E731
        for bad in ({"interval_days": 0}, {"interval_days": 4000}, {"interval_days": "ten"}, {"enabled": "yes"},
                    {"last_done": "yesterday"}, {"last_done": "2999-01-01"}, {"last_done": "1990-01-01"}, {"nope": 1}):
            assert put("hub_check", bad).status_code == 422, bad
        assert put("hub_check", {"interval_days": 45, "enabled": True, "last_done": "2026-10-01"}).status_code == 200
        assert put("hub_check", {"last_done": ""}).json()["last_done"] is None                       # clears it
        assert put("nothing_here", {"interval_days": 5}).status_code == 404
        assert put("water", {"interval_days": 5}).status_code == 422                                  # not on a schedule
        assert put("hub_check", {"title": "Mine"}).status_code == 422                                 # built-in wording is fixed
        add = lambda body: o.post("/api/care", json=body, headers=H)                                 # noqa: E731
        for bad in ({"title": "", "interval_days": 5}, {"title": "x" * 61, "interval_days": 5}, {"title": "A", "interval_days": 0},
                    {"title": "A", "interval_days": 5, "steps": ["s"] * 11}, {"title": "A", "interval_days": 5, "steps": ["s" * 201]},
                    {"title": "A", "interval_days": 5, "why": "w" * 201}, {"title": "A", "interval_days": 5, "last_done": "2999-01-01"},
                    {"title": "A", "interval_days": 5, "colour": "red"}, {"title": "   ", "interval_days": 5}):
            assert add(bad).status_code == 422, bad
        assert add({"title": "Water the plant by the bed", "interval_days": 7, "steps": ["Pour."], "supplies": "A jug"}).status_code == 200
        assert add({"title": "water the plant by the bed", "interval_days": 7}).status_code == 422     # duplicate name
        for tid, body in (("hub_check", {"on": "2026-13-45"}), ("hub_check", {"on": "2999-01-01"}), ("hub_check", {"on": "no"}),
                          ("hub_check", {"extra": 1})):
            assert o.post(f"/api/care/{tid}/done", json=body, headers=H).status_code == 422, body
        assert o.post("/api/care/hub_check/snooze", json={"days": 0}, headers=H).status_code == 422
        assert o.post("/api/care/hub_check/snooze", json={"days": 61}, headers=H).status_code == 422
        assert o.post("/api/care/hub_check/snooze", json={"days": 14}, headers=H).status_code == 200
        assert o.post("/api/care/missing/done", json={}, headers=H).status_code == 404
        assert o.delete("/api/care/hub_check", headers=H).status_code == 422                          # built-in: off, not deleted
        assert o.delete("/api/care/c_nope", headers=H).status_code == 404


def test_parse_date_edges():
    today = date(2026, 11, 10)
    assert carelib.parse_date("2026-11-10", today=today) == today
    with pytest.raises(carelib.CareError):
        carelib.parse_date("2026-11-11", today=today)


# ---------- Home Assistant ----------
async def test_home_assistant_gets_a_days_sensor_and_a_done_button_per_task(tmp_path):
    from embertide.ha_bridge import HaBridge
    r = orion_rig(tmp_path)
    r.c.person("p1")
    b = HaBridge(r.c)
    carelib.of(r.c).edit("tablets", {"last_done": "2026-09-30"})
    topics = {t: c for t, c in b.discovery()}
    assert "homeassistant/sensor/embertide_care_tablets_days/config" in topics
    btn = topics["homeassistant/button/embertide_care_tablets_done/config"]
    assert btn["command_topic"] == "embertide/care/set/tablets" and btn["payload_press"] == "done"
    st = b.states({"people": [], "now": r.clock.now_ts()})
    assert st["embertide/care/tablets/days"] == "139" and st["embertide/care/water/days"] == "None"
    assert await b.handle("embertide/care/set/tablets", "done") == "care tablets done"
    assert by_id(r, "tablets")["last_done"] == "2026-11-10"
    assert await b.handle("embertide/care/set/tablets", "explode") is None
    assert await b.handle("embertide/care/set/nope", "done") is None
