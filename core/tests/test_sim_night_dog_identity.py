"""Is it the side's sleeper, or the dog? The owner's watch and phone tell them apart (engine/pets.py, engine/watch.py,
engine/phone_context.py). Sam (p2) sleeps on the right, Alex (p1) on the left. A large dog may lie on either side; the
right side has a bed radar (it sees the dog too). Work-night rhythm: bed 22:30, earliest start 21:00."""

from embertide.models import Pet
from simnight import Plan, Sim, ts

DOG = Pet(name="Rufus", kind="dog", weight_kg=29.5, sleeps_on_bed="yes", side="either")


def pet_sus(r, zone="right"):
    return [e for e in r.events("pet_suspected") if e.get("zone") == zone]


def rig(tmp_path, start, pets=(DOG,), radar=True):
    r = Sim(tmp_path, start)
    r.hh.pets = list(pets)
    r.radar = r.add("radar", "right") if radar else None
    return r


def phone_away_from_bed(r):
    """On the home Wi-Fi, not charging, its own (right) dial heard faintly: not at the bedside."""
    r.phone(r.p2, wifi_home=True, charging=False, watch_connected=True,
            dials=[{"minor": 2, "rssi": -83, "seen_at": r.clock.now_ts()}])


def phone_on_bedside_charger(r):
    r.phone(r.p2, wifi_home=True, charging=True, dials=[{"minor": 2, "rssi": -60, "seen_at": r.clock.now_ts()}])


def world(plan, *, dog=None, watch=None, phone=None, every=600):
    """Each step: the plan; the dog on the right while `dog` = (from, to) says; the watch's live heart rate (bpm, or a
    function of the rig); the phone's report every `every` seconds."""
    last = {"phone": -1e18}

    def step(r, now):
        plan(r, now)
        if dog:
            on = ts(dog[0]) <= now < ts(dog[1])
            if on:
                r.bed.pets["right"] = 115
            else:
                r.bed.pets.pop("right", None)
        if r.radar is not None:
            r.radar.set(1.0 if (r.bed.z["right"].occupied or "right" in r.bed.pets) else 0.0)
        if watch is not None:
            bpm = watch(r) if callable(watch) else watch
            if bpm is not None:
                r.watch(r.p2, bpm)
        if phone is not None and now - last["phone"] >= every:
            last["phone"] = now
            phone(r)
    return step


async def test_todays_case_the_dog_by_day_with_the_radar_on(tmp_path):
    """11:35-13:05: the dog on the owner's side, the radar sees it, the phone is away from the dial and not charging,
    the watch is connected and reads the owner's own heart rate (not the dog's)."""
    r = rig(tmp_path, "2026-11-06T11:20")
    step = world(Plan("2026-11-06"), dog=("2026-11-06T11:35", "2026-11-06T13:05"), watch=72, phone=phone_away_from_bed)
    await r.run("2026-11-06T12:00", script=step)
    pet = r.c.fused["right"]["pet"]
    assert pet["suspected"] and pet["evidence"] == "watch" and "watch and phone" in pet["why"], pet
    await r.run("2026-11-06T13:30", script=step)
    sus = pet_sus(r)
    assert sus and sus[0]["pet"] == "Rufus" and sus[0]["evidence"] in ("watch", "phone"), sus   # whichever held first
    assert "Rufus" in sus[0]["why"]
    assert ts("2026-11-06T11:35") <= sus[0]["ts"] <= ts("2026-11-06T11:55")
    assert r.started_count(r.p2) == 0
    assert any(e.get("zone") == "right" for e in r.events("pet_cleared"))


async def test_the_dog_in_the_evening_before_bedtime_does_not_start_the_night_early(tmp_path):
    r = rig(tmp_path, "2026-11-05T20:30")
    step = world(Plan(), dog=("2026-11-05T21:05", "2026-11-05T22:15"), watch=74, phone=phone_away_from_bed)
    await r.run("2026-11-05T22:20", script=step)
    assert pet_sus(r) and r.started_count(r.p2) == 0


async def test_phone_alone_holds_off_an_early_start_by_the_dog(tmp_path):
    """No watch: the phone (home, not charging, not by the bed) with the dog allowed on that side and no learned range
    is enough to hold off an EARLY start (before the usual bedtime)."""
    r = rig(tmp_path, "2026-11-05T20:30")
    step = world(Plan(), dog=("2026-11-05T21:05", "2026-11-05T22:15"), phone=phone_away_from_bed)
    await r.run("2026-11-05T22:20", script=step)
    sus = pet_sus(r)
    assert sus and sus[0]["evidence"] == "phone" and "phone" in sus[0]["why"]
    assert r.started_count(r.p2) == 0


async def test_phone_alone_never_calls_it_the_dog_after_bedtime(tmp_path):
    """After the usual bedtime the phone alone is not enough (it may simply be in the kitchen)."""
    r = rig(tmp_path, "2026-11-05T22:30")
    step = world(Plan().sleep("right", "22:40", "06:50"), phone=phone_away_from_bed)
    await r.run("2026-11-06T00:30", script=step)
    assert not pet_sus(r) and r.started_count(r.p2) == 1


async def test_the_owner_reading_in_bed_with_the_phone_in_hand_and_the_watch_agreeing(tmp_path):
    """In bed at 21:10, phone in hand (not charging, not by the dial), but the watch matches the bed: it's them."""
    r = rig(tmp_path, "2026-11-05T21:00")

    def own_hr(x):
        s = x.c.presence.sides.get("right")
        return s.hr if s is not None and s.hr else None
    step = world(Plan().sleep("right", "21:10", "06:50"), watch=own_hr, phone=phone_away_from_bed)
    await r.run("2026-11-05T22:00", script=step)
    assert not pet_sus(r)
    assert r.started_count(r.p2) == 1


async def test_the_owner_in_bed_with_the_watch_off_and_the_phone_on_the_bedside_charger(tmp_path):
    r = rig(tmp_path, "2026-11-05T21:00")
    step = world(Plan().sleep("right", "21:10", "06:50"), phone=phone_on_bedside_charger)
    await r.run("2026-11-05T22:00", script=step)
    assert not pet_sus(r) and r.started_count(r.p2) == 1


async def test_the_phone_left_in_another_room_but_the_watch_says_it_is_them(tmp_path):
    r = rig(tmp_path, "2026-11-05T21:00")

    def own_hr(x):
        s = x.c.presence.sides.get("right")
        return s.hr if s is not None and s.hr else None
    step = world(Plan().sleep("right", "22:00", "06:50"), watch=own_hr, phone=phone_away_from_bed)
    await r.run("2026-11-05T23:00", script=step)
    assert not pet_sus(r) and r.started_count(r.p2) == 1


async def test_a_stale_phone_counts_for_nothing(tmp_path):
    """The phone last reported hours ago: unknown, so a person in bed early starts the night as always."""
    r = rig(tmp_path, "2026-11-05T17:00")
    phone_away_from_bed(r)
    r.clock.jump_to(ts("2026-11-05T21:00"))
    step = world(Plan().sleep("right", "21:10", "06:50"))
    await r.run("2026-11-05T22:00", script=step)
    assert not pet_sus(r) and r.started_count(r.p2) == 1


async def test_the_dog_joining_the_owner_mid_night_never_ends_the_night(tmp_path):
    r = rig(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("right", "22:40", "06:50")
    plan.at("01:00", lambda x: x.bed.pets.update(right=115)).at("03:00", lambda x: x.bed.pets.pop("right", None))
    step = world(plan, phone=phone_away_from_bed)
    await r.run("2026-11-06T07:00", script=step)
    assert r.started_count(r.p2) == 1
    assert "out_of_bed" not in r.states(r.p2, "2026-11-06T00:00", "2026-11-06T06:00")


async def test_no_pets_configured_is_unchanged(tmp_path):
    r = rig(tmp_path, "2026-11-05T20:30", pets=())
    step = world(Plan().sleep("right", "21:10", "06:50"), watch=90, phone=phone_away_from_bed)
    await r.run("2026-11-05T22:00", script=step)
    assert not r.events("pet_suspected") and r.started_count(r.p2) == 1


async def test_the_watch_is_asked_for_heart_rate_when_the_side_gets_occupied_by_day(tmp_path):
    """Outside the night the watch normally rests; a side getting occupied asks it for up to 20 minutes (long enough for
    one of the phone's check-ins, every 15 minutes at most, to fall inside)."""
    from embertide.engine import watch as W
    r = rig(tmp_path, "2026-11-06T11:20")
    phone_away_from_bed(r)
    assert not W.wanted(r.c, r.p2, r.clock.now_ts())
    step = world(Plan("2026-11-06"), dog=("2026-11-06T11:35", "2026-11-06T12:30"))
    await r.run("2026-11-06T11:38", script=step)
    assert W.wanted(r.c, r.p2, r.clock.now_ts())
    await r.run("2026-11-06T11:58", script=step)
    assert not W.wanted(r.c, r.p2, r.clock.now_ts())                 # 20 minutes per stretch, then it rests
