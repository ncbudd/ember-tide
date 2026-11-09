"""Work days per person: the work-night / day-off rhythms follow them, everywhere a night's rhythm is picked."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.engine.rhythm import pick_rhythm
from embertide.models import Household, Person, PersonSettings, Rhythm, off_nights, sync_rhythm_days, work_nights
from embertide.seed import default_household, default_rhythms

H = {"X-Embertide": "1"}


def client(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False
    return TestClient(create_app(cfg))


def test_default_is_monday_to_friday():
    s = PersonSettings()
    assert s.work_days == [0, 1, 2, 3, 4]
    # nights before Mon–Fri are Sun–Thu evenings; Fri and Sat evenings come before a day off
    assert work_nights(s.work_days) == [0, 1, 2, 3, 6]
    assert off_nights(s.work_days) == [4, 5]


def test_work_days_validated_and_sorted():
    assert PersonSettings(work_days=[5, 3, 3, 0]).work_days == [0, 3, 5]
    with pytest.raises(ValueError):
        PersonSettings(work_days=[7])
    assert PersonSettings(work_days=[]).work_days == []          # not working at all is allowed


def test_weekend_worker_gets_their_own_nights():
    p = Person(id="p", name="R", zone="right", rhythms=default_rhythms("ember"))
    p.settings.work_days = [4, 5, 6, 0]                           # Fri–Mon
    assert sync_rhythm_days(p)
    work = next(r for r in p.rhythms if r.nights == "work")
    off = next(r for r in p.rhythms if r.nights == "off")
    assert work.days == [3, 4, 5, 6]                              # Thu–Sun evenings
    assert off.days == [0, 1, 2]
    assert work.name == "Work nights" and off.name == "Days off"  # "Weeknights" would be wrong now
    # Saturday 2026-11-07 evening is before Sunday, a work day for them
    assert pick_rhythm(p.rhythms, date(2026, 11, 7)).nights == "work"
    assert pick_rhythm(p.rhythms, date(2026, 11, 10)).nights == "off"      # Tuesday evening, Wednesday off
    assert not sync_rhythm_days(p)                                # nothing left to change


def test_old_saved_rhythms_follow_work_days_but_custom_ones_dont():
    old = default_household().model_dump()
    for p in old["people"]:
        for r in p["rhythms"]:
            r.pop("nights")
        p["settings"].pop("work_days")
    p1 = old["people"][0]
    p1["rhythms"][1]["days"] = [5]                                 # someone already moved their weekend rhythm by hand
    h = Household.model_validate(old)
    assert [r.nights for r in h.people[0].rhythms] == ["work", ""]
    assert [r.nights for r in h.people[1].rhythms] == ["work", "off"]
    # once saved, the explicit value sticks
    again = Household.model_validate(h.model_dump())
    assert [r.nights for r in again.people[0].rhythms] == ["work", ""]
    r = Rhythm.model_validate({**old["people"][1]["rhythms"][0], "nights": ""})
    assert r.nights == ""


def test_settings_patch_moves_the_rhythms_and_summary_says_which_night(tmp_path):
    with client(tmp_path) as c:
        r = c.patch("/api/people/p2/settings", json={"work_days": [5, 6]}, headers=H)       # weekends only
        assert r.status_code == 200, r.text
        rh = {x["id"]: x for x in c.get("/api/people/p2/rhythms").json()}
        assert rh["weeknights"]["days"] == [4, 5] and rh["weeknights"]["name"] == "Work nights"
        assert rh["weekends"]["days"] == [0, 1, 2, 3, 6]
        # the other person is untouched
        rh1 = {x["id"]: x for x in c.get("/api/people/p1/rhythms").json()}
        assert rh1["weeknights"]["days"] == [0, 1, 2, 3, 6] and rh1["weeknights"]["name"] == "Weeknights"
        assert c.patch("/api/people/p2/settings", json={"work_days": [9]}, headers=H).status_code == 422
        # picking days by hand in the rhythm editor stops that rhythm following work days
        body = {**rh["weekends"], "days": [0, 1]}
        assert c.put("/api/people/p2/rhythms/weekends", json=body, headers=H).status_code == 200
        rh = {x["id"]: x for x in c.get("/api/people/p2/rhythms").json()}
        assert rh["weekends"]["nights"] == "" and rh["weekends"]["days"] == [0, 1]
        c.patch("/api/people/p2/settings", json={"work_days": [0, 1, 2, 3, 4]}, headers=H)
        rh = {x["id"]: x for x in c.get("/api/people/p2/rhythms").json()}
        assert rh["weekends"]["days"] == [0, 1] and rh["weeknights"]["days"] == [0, 1, 2, 3, 6]
        # saving a rhythm unchanged keeps it following
        assert c.put("/api/people/p2/rhythms/weeknights", json=rh["weeknights"], headers=H).status_code == 200
        assert {x["id"]: x for x in c.get("/api/people/p2/rhythms").json()}["weeknights"]["nights"] == "work"
        nights = c.get("/api/people/p1/nights?count=3").json()
        for n in nights:
            d = date.fromisoformat(n["date"])
            assert n["before"] == ("work" if d.weekday() in (6, 0, 1, 2, 3) else "off")


def test_persona_choice_can_reset_the_starting_temperatures(tmp_path):
    with client(tmp_path) as c:
        deep = lambda: next(ph for ph in c.get("/api/people/p1/rhythms").json()[0]["phases"] if ph["id"] == "deep")["temp_c"]  # noqa: E731
        before = deep()
        c.put("/api/people/p1/profile", json={"persona": "ember"}, headers=H)
        assert deep() == before                                       # just the label
        c.put("/api/people/p1/profile", json={"persona": "ember", "persona_temps": True}, headers=H)
        assert deep() > before + 4                                    # runs cold: a warmer start (72 °F vs 63 °F)
        assert c.get("/api/household").json()["people"][0]["persona"] == "ember"


def test_setup_answers_are_saved_and_checked(tmp_path):
    """First-time setup saves through the ordinary settings PATCH: the wearable pick and the steps already done."""
    with client(tmp_path) as c:
        r = c.patch("/api/people/p2/settings", json={"wearable": "oura", "setup_steps": ["you", "workdays"], "welcomed": True}, headers=H)
        assert r.status_code == 200, r.text
        s = c.get("/api/household").json()["people"][1]["settings"]
        assert s["wearable"] == "oura" and s["setup_steps"] == ["you", "workdays"] and s["welcomed"]
        assert c.patch("/api/people/p2/settings", json={"wearable": "<script>"}, headers=H).status_code == 422
        assert c.patch("/api/people/p2/settings", json={"setup_steps": ["x"] * 40}, headers=H).status_code == 422
        # "run setup again" clears both
        c.patch("/api/people/p2/settings", json={"welcomed": False, "setup_steps": []}, headers=H)
        s = c.get("/api/household").json()["people"][1]["settings"]
        assert not s["welcomed"] and s["setup_steps"] == [] and s["wearable"] == "oura"
