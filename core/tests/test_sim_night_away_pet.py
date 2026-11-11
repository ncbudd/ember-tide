"""A pet on the side of someone who is away (engine/pets.py, engine/away.py): whatever lies there for a few minutes isn't
them. Alex is on the left (p1), Sam on the right (p2); Sam is marked away with no end date."""

from embertide.engine import away as away_mod
from embertide.models import Pet
from simnight import Plan, Sim, ts

DOG = Pet(name="Rufus", kind="dog", weight_kg=29.5, sleeps_on_bed="yes", side="either")
CAT = Pet(name="Biscuit", kind="cat", weight_kg=4.5, sleeps_on_bed="yes", side="either")


def pet_events(r, zone="right"):
    return [e for e in r.events() if e["kind"] in ("pet_suspected", "pet_cleared") and e.get("zone") == zone]


def by_day(a: str, b: str, pet: int | None = None, zone: str = "right") -> Plan:
    """A daytime stretch on `zone` from a to b (full ISO times: Plan's HH:MM are evening-based): the pet with this heart
    rate, or (pet None) a person lying there."""
    plan = Plan()
    if pet is None:
        plan.iv[zone].append((ts(a), ts(b)))
    else:
        plan.actions += [(ts(a), lambda x: x.bed.pets.update({zone: pet})), (ts(b), lambda x: x.bed.pets.pop(zone, None))]
    return plan


def setup(tmp_path, start, pets=(DOG,)):
    r = Sim(tmp_path, start)
    r.hh.pets = list(pets)
    away_mod.start(r.c, r.p2.id, r.clock.now_ts(), by="p2")
    return r


async def test_the_dog_on_an_away_persons_side_by_day_is_the_dog(tmp_path):
    """The real case: the dog lies on the away person's side late morning; heart and breathing like a sleeping adult."""
    r = setup(tmp_path, "2026-11-06T11:20")
    plan = by_day(pet=53, a="2026-11-06T11:27", b="2026-11-06T13:04")
    await r.run("2026-11-06T13:30", script=plan)
    evs = pet_events(r)
    assert [e["kind"] for e in evs] == ["pet_suspected", "pet_cleared"]
    sus = evs[0]
    assert sus["pet"] == "Rufus" and sus["evidence"] == "away" and "away, so this is probably Rufus" in sus["why"]
    assert ts("2026-11-06T11:29") <= sus["ts"] <= ts("2026-11-06T11:36")
    assert r.started_count(r.p2) == 0 and not r.bed.z["right"].on
    assert away_mod.is_away(r.store, r.p2.id, r.clock.now_ts())
    held = r.store.get("away_spans:p2") or []
    assert len(held) == 1 and held[0]["pet"] == DOG.id and held[0]["readings"]
    # taught to the dog's signature once it is safely behind us and they're still away
    r.clock.jump_to(ts("2026-11-07T08:00"))
    away_mod._settle(r.c, "p2", r.clock.now_ts())
    from embertide.engine import petq
    assert DOG.id in petq.pet_sigs(r.store)


async def test_the_dog_on_an_away_persons_side_at_night(tmp_path):
    r = setup(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("23:00", lambda x: x.bed.pets.update(right=58)).at("03:00", lambda x: x.bed.pets.pop("right", None))
    await r.run("2026-11-06T07:30", script=plan)
    evs = pet_events(r)
    assert evs and evs[0]["kind"] == "pet_suspected" and evs[0]["evidence"] == "away"
    assert r.started_count(r.p2) == 0 and r.states(r.p2, "2026-11-05T22:00", "2026-11-06T07:00") == {"away"}
    assert r.started_count(r.p1) == 1                                # Alex's night is untouched
    sleep = r.summary(r.p2, "2026-11-05")
    assert any(m["kind"] == "pet" for m in sleep.get("moments") or [])


async def test_a_radar_that_sees_someone_on_the_away_side_may_be_a_person(tmp_path):
    """A house guest or the partner back early: the radar sees a person, so it isn't called the pet."""
    r = setup(tmp_path, "2026-11-06T11:20")
    radar = r.add("radar", "right")
    plan = by_day(a="2026-11-06T11:30", b="2026-11-06T13:00")
    plan.drive(radar, "right")
    await r.run("2026-11-06T13:30", script=plan)
    assert not [e for e in pet_events(r) if e["kind"] == "pet_suspected"]
    assert r.started_count(r.p2) == 0                                # still away: nothing starts
    # (the stay is still held for later as before, test_away.py: taught only if they're still away 18 h on, and
    # dropped if the morning check or "I'm back" says it may have been them)


async def test_not_away_is_unchanged(tmp_path):
    """Sam at home: a dog with a person-like heart rate alone on the side is left to the existing checks."""
    r = Sim(tmp_path, "2026-11-06T11:20")
    r.hh.pets = [DOG]
    plan = by_day(pet=53, a="2026-11-06T11:27", b="2026-11-06T13:04")
    await r.run("2026-11-06T13:30", script=plan)
    assert not [e for e in pet_events(r) if e.get("evidence") == "away"]


async def test_the_range_check_knows_an_empty_side_from_the_strip_alone(tmp_path):
    """No live watch at all: a stretch that began on a side with no night running counts as "otherwise empty", so the
    learned range / pet signature check applies to sleepers without a watch too; the sleeper back from a short trip
    within 20 min doesn't."""
    r = Sim(tmp_path, "2026-11-06T11:20")
    r.hh.pets = [DOG]
    plan = by_day(pet=53, a="2026-11-06T11:27", b="2026-11-06T12:30")
    await r.run("2026-11-06T11:40", script=plan)
    ep = r.c.__dict__["_eps"]["right"]
    assert r.c._pet_hints("right", [], ep)["empty_side"] is True
    r2 = Sim(tmp_path / "b", "2026-11-05T21:00")
    await r2.run("2026-11-06T01:00", script=Plan().sleep("right", "22:40", "06:50", ups=[("00:40", "00:50")]))
    ep2 = r2.c.__dict__["_eps"]["right"]
    assert r2.c._pet_hints("right", [], ep2)["empty_side"] is False


async def test_a_guest_on_the_away_persons_side_stays_a_guest(tmp_path):
    r = setup(tmp_path, "2026-11-05T21:00")
    r.c.guest_start(["right"], 23.0, 40, None)
    plan = Plan().sleep("left", "22:40", "06:50").sleep("right", "23:00", "07:00")
    await r.run("2026-11-06T02:00", script=plan)
    assert not [e for e in pet_events(r) if e["kind"] == "pet_suspected"]
    assert r.bed.z["right"].on                                       # the guest's side runs


async def test_two_pets_and_no_way_to_tell_says_a_pet(tmp_path):
    big = Pet(name="Rufus", kind="dog", weight_kg=29.5, sleeps_on_bed="yes")
    big2 = Pet(name="Max", kind="dog", weight_kg=31, sleeps_on_bed="yes")
    r = setup(tmp_path, "2026-11-06T11:20", pets=(big, big2))
    plan = by_day(pet=55, a="2026-11-06T11:27", b="2026-11-06T12:30")
    await r.run("2026-11-06T13:00", script=plan)
    sus = [e for e in pet_events(r) if e["kind"] == "pet_suspected"]
    assert sus and sus[0]["pet"] is None and "probably a pet" in sus[0]["why"]


async def test_two_pets_a_cat_and_a_big_dog_picks_the_dog_by_its_heart_rate(tmp_path):
    r = setup(tmp_path, "2026-11-06T11:20", pets=(CAT, DOG))
    plan = by_day(pet=55, a="2026-11-06T11:27", b="2026-11-06T12:30")
    await r.run("2026-11-06T13:00", script=plan)
    sus = [e for e in pet_events(r) if e["kind"] == "pet_suspected"]
    assert sus and sus[0]["pet"] == "Rufus"


async def test_no_pets_configured_is_unchanged(tmp_path):
    r = setup(tmp_path, "2026-11-06T11:20", pets=())
    plan = by_day(a="2026-11-06T11:30", b="2026-11-06T13:00")
    await r.run("2026-11-06T13:30", script=plan)
    assert not pet_events(r) and r.started_count(r.p2) == 0
