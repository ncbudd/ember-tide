"""The echo filter and a pet on the other side (2026-11-12 night): the left sleeper (Alex) is away and the dog lies on the
left; Sam (right) gets back into bed 15 s after the dog. Later both pads read the same heart rate and Sam's pad was
flagged as the dog's echo, so his night went out of bed although his side's radar saw him.

Alex is on the left (p1), Sam on the right (p2)."""

from embertide.drivers.base import SensorFrame
from embertide.engine import away as away_mod
from embertide.models import Pet
from simnight import GlitchBed, Plan, Sim, ts

DOG = Pet(name="Rex", kind="dog", weight_kg=30, sleeps_on_bed="yes", side="either")


class MirrorBed(GlitchBed):
    """While `mirror` is set and the right pad has a person on it, the left pad (a pet lies there, or it is empty)
    reports exactly what the right pad reports: both ~58 bpm, status 0."""

    mirror = False

    def _sensor(self, name, t, done):
        f = super()._sensor(name, t, done)
        if self.mirror and self.z["right"].occupied and (name == "right" or not self.z["left"].occupied):
            hr = 58 + int(t // 90) % 3 - 1
            return SensorFrame(status=0, hr=hr, br=15, clock=f.clock)
        return f


def night(tmp_path, *, away=True, pets=(DOG,)):
    r = Sim(tmp_path, "2026-11-05T21:00", bed_cls=MirrorBed)
    r.hh.pets = list(pets)
    if away:
        away_mod.start(r.c, r.p1.id, r.clock.now_ts(), by="p1")
    return r


def tonight_plan(dog_on=True):
    plan = Plan()
    plan.iv["right"] += [(ts("2026-11-05T22:40"), ts("2026-11-06T00:50")), (ts("2026-11-06T01:27:31"), ts("2026-11-06T06:50"))]
    if dog_on:
        plan.at("01:27:16", lambda x: x.bed.pets.update(left=61)).at("03:30", lambda x: x.bed.pets.pop("left", None))
    plan.at("01:25", lambda x: setattr(x.bed, "mirror", True)).at("01:50", lambda x: setattr(x.bed, "mirror", False))
    return plan


async def test_owner_in_after_the_dog_stays_present_while_both_pads_read_alike(tmp_path):
    r = night(tmp_path)
    radar = r.add("radar", "right")
    plan = tonight_plan().drive(radar, "right")
    await r.run("2026-11-06T01:20", dt=60, script=plan)
    echo_seen, present = [], []
    for minute in range(21, 56):
        await r.run(f"2026-11-06T01:{minute:02d}", dt=15, script=plan)
        if ts("2026-11-06T01:32") <= r.clock.now_ts() <= ts("2026-11-06T01:50"):
            echo_seen.append(r.c.presence.sides["right"].echo)
            present.append(r.c.fused["right"]["present"])
    assert not any(echo_seen), "Sam's pad was flagged as an echo"
    assert all(present), "Sam's side stopped being present"
    assert r.at(r.p2, "2026-11-06T01:45")[0] == "rhythm"
    assert r.started_count(r.p2) == 1


async def test_owner_alone_on_the_right_still_filters_the_left_echo(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("right", "22:40", "06:50")
    await r.run("2026-11-05T23:30", dt=30, script=plan)
    assert r.c.presence.sides["left"].echo is True
    assert r.c.presence.present("left") is False and r.c.presence.present("right") is True
    assert r.started_count(r.p1) == 0 and r.started_count(r.p2) == 1


async def test_owner_first_then_the_dog_is_unchanged(tmp_path):
    r = night(tmp_path)
    plan = Plan().sleep("right", "22:40", "06:50").at("01:27:16", lambda x: x.bed.pets.update(left=61))
    await r.run("2026-11-06T02:30", dt=30, script=plan)
    assert r.c.presence.present("right") is True
    assert r.at(r.p2, "2026-11-06T02:00")[0] == "rhythm"


async def test_two_people_unchanged(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50").sleep("right", "22:40:15", "06:55")
    await r.run("2026-11-05T23:30", dt=30, script=plan)
    assert r.c.presence.present("left") is True and r.c.presence.present("right") is True


async def test_away_side_with_no_pet_configured_has_no_pet_veto(tmp_path):
    r = night(tmp_path, pets=())
    await r.run("2026-11-06T01:20", dt=60, script=tonight_plan(dog_on=False))
    assert r.c.presence.pet_sides == set()


# ---------------------------------------------------------------------------------------------------------------
# 2026-11-12 night, replayed from the real trace (tests/fixtures/crosstalk_night.json: pad status / heart / breathing
# per side and what each side's radar saw, every 15 s, 21:00 to 09:30). the partner (left, p1 here) away; the sleeper (right, p2).
# His heartbeat showed on the empty left pad all night (left radar saw nobody), the app called it "probably the dog"
# and from 03:20 marked his own pad as the echo of the left, so his night went out of bed at 06:02 and 08:30.

import json
from pathlib import Path

REPLAY = json.loads((Path(__file__).parent / "fixtures" / "crosstalk_night.json").read_text())["rows"]
# columns: ts, Lst, Lhr, Lbr, Rst, Rhr, Rbr, radarL, radarR, p1_state (the trace's), right_echo (the trace's)


class ReplayBed(GlitchBed):
    def _sensor(self, name, t, done):
        f = super()._sensor(name, t, done)
        row = ReplayBed.row_at(t)
        i = 1 if name == "left" else 4
        st, hr, br = row[i:i + 3]
        return SensorFrame(status=st, hr=hr, br=br, clock=f.clock)

    @staticmethod
    def row_at(t):
        lo, hi = 0, len(REPLAY) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if REPLAY[mid][0] <= t:
                lo = mid
            else:
                hi = mid - 1
        return REPLAY[lo]


async def replay_night(tmp_path, pets=(DOG,)):
    r = Sim(tmp_path, "2026-11-11T21:00", bed_cls=ReplayBed)
    r.hh.pets = list(pets)
    away_mod.start(r.c, r.p1.id, r.clock.now_ts(), by="p1")
    rl, rr = r.add("radar", "left"), r.add("radar", "right")
    r.dt = 15.0
    seen = {"pet_left": [], "echo_right": [], "p2": []}

    def script(rig, now):
        row = ReplayBed.row_at(now)
        rl.set(None if row[7] is None else float(row[7]))
        rr.set(None if row[8] is None else float(row[8]))

    def watch(rig, now):
        f = rig.c.fused.get("left") or {}
        seen["pet_left"].append((now, bool((f.get("pet") or {}).get("suspected"))))
        seen["echo_right"].append((now, rig.c.presence.sides["right"].echo))
        seen["p2"].append((now, rig.e(rig.p2).s.state))

    await r.run("2026-11-12T09:30", dt=15, script=script, watch=watch)
    return r, seen


def _states(seen, a, b):
    return {s for t, s in seen["p2"] if ts(a) <= t < ts(b)}


async def test_replay_of_the_crosstalk_night(tmp_path):
    r, seen = await replay_night(tmp_path)
    print("TIMELINE", r.timeline(r.p2))
    assert "out_of_bed_hold" not in _states(seen, "2026-11-12T06:00", "2026-11-12T06:20"), "06:02 false hold"
    assert "out_of_bed_hold" not in _states(seen, "2026-11-12T08:25", "2026-11-12T08:50"), "08:30 false hold"
    assert "out_of_bed_hold" in _states(seen, "2026-11-12T01:16", "2026-11-12T01:26"), "the real getting up at 01:15"
    assert "out_of_bed_hold" not in _states(seen, "2026-11-12T01:33", "2026-11-12T01:38")
    # the left pad was the sleeper's own heartbeat: never labelled as the dog overnight
    overnight = [x for t, x in seen["pet_left"] if ts("2026-11-12T00:00") <= t < ts("2026-11-12T08:25")]
    assert not any(overnight), "the empty left side was labelled as the dog"
    # and no "probably <pet>" notification
    assert "pet_question" not in r.told(r.p2.id)


async def test_morning_up_count_leaves_out_holds_the_radar_contradicted(tmp_path):
    r, seen = await replay_night(tmp_path)
    s = r.summary(r.p2)
    # the real getting up at 01:15 is counted; no hold in the trace was a misread, so none was noted
    gaps = [m for m in s["moments"] if m["kind"] == "out_of_bed"]
    assert any(ts("2026-11-12T01:10") <= m["ts"] <= ts("2026-11-12T01:20") for m in gaps)
    # a hold the controller noted as contradicted by the radar (up_misread) no longer counts as getting up
    n = s["times_up"]
    g = next(m for m in gaps if ts("2026-11-12T01:10") <= m["ts"] <= ts("2026-11-12T01:20"))
    r.store.event(r.clock.now_ts(), "up_misread", r.p2.id, "radar", since=g["ts"], seen=30, ticks=40)
    s2 = r.summary(r.p2)
    assert s2["times_up"] == n - 1
    assert not any(m["kind"] == "out_of_bed" and abs(m["ts"] - g["ts"]) < 60 for m in s2["moments"])


async def test_pet_label_still_given_when_the_away_pad_does_not_follow_the_other(tmp_path):
    """A pad on the away side whose heart rate is clearly not the sleeper's on the other side is still the dog."""
    r = night(tmp_path)
    r.bed.mirror = False
    radar = r.add("radar", "right")
    plan = tonight_plan().drive(radar, "right")
    plan.at("01:27:16", lambda x: x.bed.pets.update(left=61))
    await r.run("2026-11-06T02:30", dt=15, script=plan)
    assert any(e["kind"] == "pet_suspected" and e.get("evidence") == "away" for e in r.events())
