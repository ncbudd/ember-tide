"""Tonight's morning cards: last night with the check-in, the review (ready, or still being written), tonight's
suggestions; put away on every device; nothing for an away night."""

from embertide.engine import morning as mo
from embertide.notify import save_feel
from test_notify_review import SUGG, morning
from test_autopilot_suite import make

H = {"X-Embertide": "1"}


def kinds(f):
    return [c["kind"] for c in f["cards"]]


async def test_night_card_with_check_in(tmp_path, monkeypatch):
    n = await morning(tmp_path)
    monkeypatch.setattr(mo, "waiting_suggestions", lambda *a, **k: [])
    n.p1.settings.autopilot.ai_review = False
    f = mo.feed(n.c, n.p1, n.clock.now_ts())
    assert f["night"] == "2026-11-05" and kinds(f) == ["night"]
    card = f["cards"][0]
    assert card["id"] == "night-2026-11-05" and card["in_bed_min"] > 0 and "in bed" in card["line"] and card["feel"] is None
    save_feel(n.c, n.p1.id, "warm", n.clock.now_ts(), night="2026-11-05")
    assert mo.feed(n.c, n.p1, n.clock.now_ts())["cards"][0]["feel"] == "warm"


async def test_review_shows_working_then_ready_and_gives_up(tmp_path, monkeypatch):
    n = await morning(tmp_path)
    monkeypatch.setattr(mo, "waiting_suggestions", lambda *a, **k: [])
    now = n.clock.now_ts()
    n.store.put(f"ai_review_started:{n.p1.id}:2026-11-05", now - 60)
    assert [c.get("state") for c in mo.feed(n.c, n.p1, now)["cards"] if c["kind"] == "review"] == ["working"]
    assert "review" not in kinds(mo.feed(n.c, n.p1, now + mo.REVIEW_WAIT_S))          # never came: no endless spinner
    n.store.put(f"ai_review:{n.p1.id}:2026-11-05", {"answer": "A steady night.", "model": "m", "proposal": None})
    rv = [c for c in mo.feed(n.c, n.p1, now)["cards"] if c["kind"] == "review"][0]
    assert rv["state"] == "ready" and rv["answer"] == "A steady night."
    n.p1.settings.autopilot.ai_review = False
    assert "review" not in kinds(mo.feed(n.c, n.p1, now))


async def test_suggestions_card_and_put_away(tmp_path, monkeypatch):
    n = await morning(tmp_path)
    monkeypatch.setattr(mo, "waiting_suggestions", lambda *a, **k: list(SUGG))
    now = n.clock.now_ts()
    f = mo.feed(n.c, n.p1, now)
    assert kinds(f)[-1] == "sugg" and f["cards"][-1]["items"][0]["title"] == "Warmer late night?"
    for cid in ("night-2026-11-05", "sugg-2026-11-05"):
        mo.dismiss(n.c, n.p1.id, cid, now)
    assert kinds(mo.feed(n.c, n.p1, now)) in ([], ["review"])


async def test_nothing_for_an_away_night(tmp_path, monkeypatch):
    n = await morning(tmp_path)
    monkeypatch.setattr("embertide.engine.away.night_verdict", lambda *a, **k: ("away", ""))
    assert mo.feed(n.c, n.p1, n.clock.now_ts())["cards"] == []


def test_morning_endpoint(tmp_path):
    with make(tmp_path) as tc:
        r = tc.get("/api/people/p1/morning", headers=H)
        assert r.status_code == 200 and set(r.json()) == {"night", "cards"}


# ---------- Sleep → Review & History ----------

def test_apply_then_undo_puts_the_old_value_back(tmp_path):
    with make(tmp_path) as c:
        rh = c.get("/api/people/p1/rhythms", headers=H).json()[0]
        deep = next(p for p in rh["phases"] if p["id"] == "deep")
        prop = {"rhythm_id": rh["id"], "phase_id": "deep", "field": "temp_c", "from": deep["temp_c"], "to": deep["temp_c"] + 0.5}
        assert c.post("/api/people/p1/insights/apply", json={"proposal": prop, "insight_id": "ap-deep-warmer-x", "source": "suggestion"}, headers=H).status_code == 200
        assert next(p for p in c.get("/api/people/p1/rhythms", headers=H).json()[0]["phases"] if p["id"] == "deep")["temp_c"] == deep["temp_c"] + 0.5
        r = c.post("/api/people/p1/insights/undo", json={"insight_id": "ap-deep-warmer-x"}, headers=H)
        assert r.status_code == 200 and "→" in r.json()["undone"]
        assert next(p for p in c.get("/api/people/p1/rhythms", headers=H).json()[0]["phases"] if p["id"] == "deep")["temp_c"] == deep["temp_c"]
        assert c.post("/api/people/p1/insights/undo", json={"insight_id": "ap-deep-warmer-x"}, headers=H).status_code == 409   # once


def test_undo_of_a_bedtime_change(tmp_path):
    with make(tmp_path) as c:
        rh = c.get("/api/people/p1/rhythms", headers=H).json()[0]
        old = rh["schedule"]["bedtime"]
        prop = {"rhythm_id": rh["id"], "schedule": "bedtime", "from": old, "to": "23:45"}
        assert c.post("/api/people/p1/insights/apply", json={"proposal": prop, "insight_id": "guide-bedtime-x", "source": "guide"}, headers=H).status_code == 200
        assert c.post("/api/people/p1/insights/undo", json={"insight_id": "guide-bedtime-x"}, headers=H).status_code == 200
        assert c.get("/api/people/p1/rhythms", headers=H).json()[0]["schedule"]["bedtime"] == old


def test_review_page_and_history_fields(tmp_path):
    with make(tmp_path) as c:
        r = c.get("/api/people/p1/review", headers=H)
        assert r.status_code == 200
        j = r.json()
        assert {"date", "last", "feel", "review", "reviewing", "review_on", "basis", "suggestions", "applied"} <= set(j) and j["last"] is True
        old = c.get("/api/people/p1/review?date=2026-01-01", headers=H).json()
        assert old["last"] is False and old["suggestions"] == []
        assert c.get("/api/people/p1/review?date=nope", headers=H).status_code == 400
        for n in c.get("/api/people/p1/nights?count=3", headers=H).json():
            assert n["reviewed"] in (True, False) and isinstance(n["applied"], int)


def test_basis_names_only_what_was_recorded():
    assert mo.basis({"has_data": True}, {}) == ["The bed: when you were on your side and its temperature, through the night"]
    b = mo.basis({"adjustments": 2, "hot_flashes": 1, "hr_avg": 55, "settled_min": 300}, {"temp": "cold"})
    assert "Your own changes: 2 on the dial or in the app" in b and "1 hot or cold flash" in b
    assert "Your watch or ring: heart rate and sleep stages" in b and "Your check-in: too cold" in b


def test_an_applied_change_belongs_to_the_night_that_had_just_ended(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    with make(tmp_path) as c:
        ctl = c.app.state.oura.c
        ts = datetime(2026, 11, 20, 9, 9, tzinfo=ZoneInfo("America/New_York")).timestamp()
        assert mo.applied_night(ctl, {"ts": ts, "night": "2026-11-15"}) == "2026-11-19"
        assert mo.applied_night(ctl, {"night": "2026-11-15"}) == "2026-11-15"


def test_applied_changes_read_in_the_persons_units_and_clock(tmp_path):
    with make(tmp_path) as c:
        p = c.app.state.oura.c.person("p1")
        rh = p.rhythms[0]
        ph = rh.phases[0]
        p.settings.units = "F"
        t = mo.change_text(p, {"proposal": {"rhythm_id": rh.id, "phase_id": ph.id, "field": "temp_c", "to": 29.44}, "before": 27.22})
        assert t == f"{rh.name} · {ph.name}: 81°F → 85°F"
        p.settings.clock = "12"
        assert mo.change_text(p, {"proposal": {"rhythm_id": rh.id, "schedule": "bedtime", "to": "00:00"}, "before": "23:00"}) == f"{rh.name}: bedtime 11:00 PM → 12:00 AM"


async def test_a_check_in_from_the_notification_puts_the_night_card_away(tmp_path, monkeypatch):
    from embertide.notify import Action
    n = await morning(tmp_path)
    monkeypatch.setattr(mo, "waiting_suggestions", lambda *a, **k: [])
    n.p1.settings.autopilot.ai_review = False
    nt = n.c.notifier
    nt.use_action(nt.new_action(n.p1.id, Action("Too warm", "feel_warm")))
    assert n.store.get(f"feedback:{n.p1.id}:2026-11-05")["temp"] == "warm"
    assert mo.feed(n.c, n.p1, n.clock.now_ts())["cards"] == []                # answered: nothing left to ask on Tonight
