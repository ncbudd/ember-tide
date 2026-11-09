"""Pets on the bed: a cat or dog must not start a night, end an out-of-bed hold, or feed Autopilot."""

import pytest

from embertide.engine.autopilot import signals_at
from embertide.models import Pet
from embertide.sources import SourceConfig, SourceManager
from test_autopilot_suite import H, NOW, make, night_rows, win
from test_scenarios import Night


class PetNight(Night):
    """A night with an optional bed scale on the left that the test sets by hand (kg each step)."""

    def __init__(self, tmp_path, start, pets=(), scale=False, person_kg=None, **kw):
        super().__init__(tmp_path, start, **kw)
        self.hh.pets = [Pet(**p) for p in pets]
        self.mgr = SourceManager(self.store, self.clock, lambda: self.bed)
        self.scale_kg: float | None = None
        if scale:
            self.mgr.configs = [SourceConfig(id="kgl", name="Scale left", driver="ha-entity", role="weight", zone="left",
                                             kind="scale", params={"url": "http://x", "entity_id": "sensor.w", "token": "t"})]
        if person_kg:
            self.mgr.calibration.person_kg.update(person_kg)
        self.c.sources = self.mgr

    async def step(self, n=1, dt=30):
        for _ in range(n):
            if self.mgr.configs and self.scale_kg is not None:
                self.mgr._sample(self.mgr.configs[0], self.scale_kg, self.clock.now_ts())
            await super().step(1, dt)


CAT = {"name": "Biscuit", "kind": "cat", "weight_kg": 4.5, "sleeps_on_bed": "yes"}
DOG = {"name": "Rufus", "kind": "dog", "weight_kg": 25, "sleeps_on_bed": "yes"}


async def test_cat_on_empty_side_in_the_evening_doesnt_start_the_night(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10", pets=[CAT])
    n.bed.scripted = False
    n.bed.pets["left"] = 160
    await n.until("2026-11-05T22:40")
    assert not n.e(n.p1).s.started
    pet = n.c.fused["left"].get("pet") or {}
    assert pet.get("suspected") and "Biscuit" in pet.get("why", "")
    ev = [e for e in n.events("pet_suspected") if e.get("zone") == "left"]
    assert ev and ev[0]["person"] is None                     # non-personal


async def test_without_pets_the_same_readings_start_the_night(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10")
    n.bed.scripted = False
    n.bed.pets["left"] = 160                                  # something with a fast heart, but no pets configured
    await n.until("2026-11-05T22:40")
    assert n.e(n.p1).s.started                                # behaviour unchanged when no pets are set


async def test_dog_on_the_scale_side_is_not_a_person(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10", pets=[DOG], scale=True, person_kg={"p1": 60})
    n.bed.scripted = False
    n.bed.pets["left"] = 85                                   # a big dog's heart rate overlaps a person's
    n.scale_kg = 25.8
    await n.until("2026-11-05T22:40")
    assert not n.e(n.p1).s.started
    assert "kg on the scale" in n.c.fused["left"]["pet"]["why"]


async def test_person_with_the_cat_is_in_bed(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10", pets=[CAT], scale=True, person_kg={"p1": 60})
    n.bed.scripted = False
    n.present("left", True)
    n.scale_kg = 61.0
    await n.until("2026-11-05T22:25")
    assert n.e(n.p1).s.started
    n.scale_kg = 65.6                                         # the cat jumps up
    await n.step(4)
    pet = n.c.fused["left"]["pet"]
    assert n.c.fused["left"]["present"] and pet["with_person"] and "Biscuit" in pet["why"]


async def test_cat_jumping_up_while_you_are_out_of_bed_keeps_the_hold(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10", pets=[CAT])
    n.bed.scripted = False
    n.present("left", True)
    await n.until("2026-11-06T03:00")
    assert n.e(n.p1).s.started
    n.present("left", False)
    await n.until("2026-11-06T03:05")
    away = n.e(n.p1).s.absent_since
    assert away is not None
    n.bed.pets["left"] = 170                                  # the cat takes the warm spot
    for _ in range(40):                                       # 20 minutes
        await n.step()
        assert n.e(n.p1).s.absent_since == away, "a cat must not count as you coming back"
    await n.until("2026-11-06T03:40")
    assert n.e(n.p1).s.state == "out_of_bed"                  # the hold ran out as if the cat weren't there


async def test_a_cat_brief_settle_doesnt_flip_presence_to_true(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10", pets=[CAT])
    n.bed.scripted = False
    n.bed.pets["left"] = 150
    for _ in range(6):
        await n.step(1, dt=5)
        assert n.c.fused["left"]["present"] is not True


def test_autopilot_ignores_bed_readings_while_a_pet_was_there():
    rows = night_rows(restless_last10=True, hr_base=55, hr_now=150)
    plain = signals_at(win(rows=rows))
    masked = signals_at(win(rows=rows, pet_windows=[(NOW - 900, NOW)]))
    assert plain["restless"] and plain["hr_rise"]
    assert masked["restless"] is None and masked["hr_rise"] is None


async def test_pet_windows_reach_autopilot_from_events(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10", pets=[CAT])
    n.bed.scripted = False
    n.bed.pets["left"] = 160
    await n.until("2026-11-05T22:30")
    w = n.c.autopilot.pet_windows(n.p1, n.clock.now_ts() - 3600, n.clock.now_ts())
    assert w and w[-1][1] == pytest.approx(n.clock.now_ts())
    assert n.c.autopilot.pet_windows(n.p2, n.clock.now_ts() - 3600, n.clock.now_ts()) == []   # other side


async def test_turning_pet_handling_off_restores_plain_behaviour(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10", pets=[CAT])
    n.hh.pets_ignore = False
    n.bed.scripted = False
    n.bed.pets["left"] = 160
    await n.until("2026-11-05T22:40")
    assert n.e(n.p1).s.started


def test_pet_api_validation_and_sizes(tmp_path):
    with make(tmp_path) as c:
        assert c.get("/api/pets").json() == {"pets": [], "ignore": True}
        r = c.put("/api/pets", headers=H, json={"pets": [CAT, dict(DOG, weight_kg=18), {"name": "Mo", "kind": "other", "weight_kg": 40}], "ignore": True})
        assert r.status_code == 200
        pets = r.json()["pets"]
        assert [p["size"] for p in pets] == ["small", "medium", "large"] and len({p["id"] for p in pets}) == 3
        assert c.put("/api/pets", headers=H, json={"pets": [{"name": "Huge", "kind": "dog", "weight_kg": 300}]}).status_code == 422
        assert c.put("/api/pets", headers=H, json={"pets": [{"name": "", "kind": "cat", "weight_kg": 4}]}).status_code == 422
        assert c.put("/api/pets", headers=H, json={"pets": [{"name": "X", "kind": "lizard", "weight_kg": 1}]}).status_code == 422
        assert c.put("/api/pets", headers=H, json={"pets": [], "ignore": False}).json() == {"pets": [], "ignore": False}
        hh = c.get("/api/household").json()
        assert hh["pets"] == [] and hh["pets_ignore"] is False


def test_pet_api_is_owner_only_to_change(tmp_path):
    from embertide.app import create_app
    from embertide.config import Config
    from fastapi.testclient import TestClient
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", True
    with TestClient(create_app(cfg)) as c:
        assert c.put("/api/pets", headers=H, json={"pets": [CAT]}).status_code in (401, 403)


# ---- pets in the night story ----

def _night_story(n, person):
    from embertide.engine.summary import summarize
    from embertide.engine.rhythm import night_date
    return summarize(n.store, person, night_date(n.clock.now()), n.clock.tz, n.clock.now_ts())


async def test_pet_on_the_bed_shows_in_that_sides_story(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10", pets=[CAT, DOG])
    n.c.reload_household(n.hh)                                # pets saved like the Settings page does
    n.bed.scripted = False
    n.bed.pets["left"] = 160
    await n.until("2026-11-05T22:40")
    del n.bed.pets["left"]                                    # it jumps off
    await n.until("2026-11-05T22:50")
    left = [m for m in _night_story(n, n.p1)["moments"] if m["kind"] == "pet"]
    assert len(left) == 1
    m = left[0]
    # the only cat on the bed, seen by its heart rate: named, and it lasted until it left
    assert m["pet"] == "Biscuit" and m["text"] == "Looked like Biscuit on your side, ignored"
    assert m["ignored"] and not m["with_person"] and 25 <= m["minutes"] <= 35
    assert not [m for m in _night_story(n, n.p2)["moments"] if m["kind"] == "pet"]   # other side's story untouched


async def test_two_pets_that_could_be_it_are_just_a_pet(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10", pets=[CAT, {**CAT, "name": "Pepper"}])
    n.c.reload_household(n.hh)
    n.bed.scripted = False
    n.bed.pets["left"] = 160
    await n.until("2026-11-05T22:40")
    m = next(m for m in _night_story(n, n.p1)["moments"] if m["kind"] == "pet")
    assert m["pet"] is None and m["text"] == "Looked like a pet on your side, ignored"


async def test_pet_joining_you_reads_plainly(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T22:10", pets=[CAT], scale=True, person_kg={"p1": 60})
    n.c.reload_household(n.hh)
    n.bed.scripted = False
    n.present("left", True)
    n.scale_kg = 61.0
    await n.until("2026-11-05T22:25")
    n.scale_kg = 65.6
    await n.step(4)
    m = next(m for m in _night_story(n, n.p1)["moments"] if m["kind"] == "pet")
    assert m["with_person"] and m["text"] == "Looked like Biscuit joined you"


def test_pet_label_rules():
    from embertide.engine.summary import _pet_label
    cat, dog = {"name": "Biscuit", "kind": "cat", "weight_kg": 4.5, "side": "left"}, {"name": "Rufus", "kind": "dog", "weight_kg": 25}
    scale = {"pet": "Rufus", "evidence": "scale"}
    assert _pet_label(scale, "left", [cat, dog]) == "Rufus"                         # the only dog
    assert _pet_label(scale, "left", [cat, dog, {**dog, "name": "Max"}]) is None     # two dogs
    assert _pet_label({"pet": "Biscuit", "evidence": "heart"}, "left", [cat, {**dog, "weight_kg": 8}]) is None   # a small dog's heart is fast too
    assert _pet_label({"pet": "Biscuit", "evidence": "heart"}, "right", [cat, dog]) is None   # Biscuit sleeps on the left
    assert _pet_label({"pet": None}, "left", [cat]) is None


def test_pet_moments_reach_the_nights_api(tmp_path):
    with make(tmp_path) as c:
        assert c.put("/api/pets", headers=H, json={"pets": [CAT], "ignore": True}).status_code == 200
        assert c.post("/api/sim", headers=H, json={"jump_to": "2026-11-06T03:00"}).status_code == 200
        ctl = c.app.state.notifier.c
        now = ctl.clock.now_ts()
        ctl.store.event(now - 2400, "pet_suspected", None, "pets", zone="left", pet="Biscuit", why="heart rate 150 bpm", with_person=False, evidence="heart")
        ctl.store.event(now - 1800, "pet_cleared", None, "pets", zone="left")
        night = c.get("/api/people/p1/night").json()
        pet = [m for m in night["moments"] if m["kind"] == "pet" and m["ts"] == now - 2400]   # (the sim may add its own cat)
        assert len(pet) == 1 and pet[0]["minutes"] == 10
        # the Sleep screen words it in the person's tone (voice.py): same pet, same side, whichever variant
        from embertide.voice import PHRASES
        assert pet[0]["text"] in [v.format(who="Biscuit") for v in PHRASES["story_pet"]["calm"]]
        assert not [m for m in c.get("/api/people/p2/night").json()["moments"] if m["kind"] == "pet" and m["ts"] == now - 2400]
        ex = c.get("/api/explore", params={"person_id": "p1"}).json()
        assert [m["text"] for m in ex["moments"] if m["kind"] == "pet" and m["ts"] == now - 2400] == ["Looked like Biscuit on your side, ignored"]


async def test_bedding_weight_on_an_empty_scale_is_not_the_cat(tmp_path):
    n = PetNight(tmp_path, "2026-11-05T19:30", pets=[CAT], scale=True, person_kg={"p1": 60})
    n.bed.scripted = False
    n.scale_kg = 2.0                                          # a duvet / drift, far lighter than the 4.5 kg cat
    await n.until("2026-11-05T19:45")
    assert not [e for e in n.events("pet_suspected") if e.get("zone") == "left"]
    n.scale_kg = 4.8                                          # now the cat: the bed strip feels it too (it's alive)
    n.bed.pets["left"] = 160
    await n.until("2026-11-05T19:50")
    ev = [e for e in n.events("pet_suspected") if e.get("zone") == "left"]
    assert ev and ev[0]["evidence"] == "scale"
