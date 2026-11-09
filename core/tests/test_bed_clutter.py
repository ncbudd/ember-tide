"""Real-life bed clutter: extra bedding, pillows, a weighted blanket, laundry, a pet hopping up, someone perching on
the edge, and slow load-cell drift. None of it may look like someone getting into or out of bed, a pet, or a reason
for Autopilot to act; real getting in and out must still be seen. (engine/bedscale.py, engine/pets.py)"""

import random

from embertide.engine.bedscale import ScaleTracker
from test_pets import CAT, PetNight
from test_scenarios import ts

PERSON = 78.0          # the left sleeper's weight (given in Settings or learned)
EMPTY = 1.8            # what the empty side reads after zeroing (topper, sheets, a little offset)


class Bed(PetNight):
    """A left-side scale fed every step from what's on the bed: clutter (kg), the sleeper, and noise."""

    def __init__(self, tmp_path, start, cat=True):
        super().__init__(tmp_path, start, pets=[CAT] if cat else [], scale=True, person_kg={"p1": PERSON})
        self.bed.scripted = False
        self.clutter = 0.0
        self.drift = 0.0
        self.in_bed = False
        self.extra = 0.0           # someone perching, a cat: on top of everything else
        self.rand = random.Random(7)
        self.seen: list[dict] = []

    def get_in(self, v=True):
        self.in_bed = v
        self.present("left", v)

    async def step(self, n=1, dt=30):
        for _ in range(n):
            self.scale_kg = round(EMPTY + self.drift + self.clutter + (PERSON if self.in_bed else 0) + self.extra
                                  + self.rand.uniform(-0.3, 0.3), 2)
            await super().step(1, dt)
            f = self.c.fused.get("left") or {}
            scale = self.mgr.vote_value(self.mgr.configs[0])
            self.seen.append({"ts": self.clock.now_ts(), "present": f.get("present"), "scale": scale, "in": self.in_bed})

    def pet_flags(self):
        return [e for e in self.events("pet_suspected") if e.get("zone") == "left"]

    def assert_clean(self, since: str, until: str):
        """Between two times the answer followed the person exactly (2 min grace after each real change)."""
        a, b = ts(since), ts(until)
        changes = [x["ts"] for i, x in enumerate(self.seen) if i and x["in"] != self.seen[i - 1]["in"]]
        for x in self.seen:
            if not (a <= x["ts"] <= b) or any(0 <= x["ts"] - t <= 120 for t in changes):
                continue
            assert x["scale"] == (1.0 if x["in"] else 0.0), x
            assert bool(x["present"]) == x["in"], x

    async def normal_night(self, day: str, nxt: str, dt: float = 30):
        """In at 22:30, out at 06:30; the night must start and then see you get up."""
        await self.until(f"{day}T22:30", dt=dt)
        assert not self.e(self.p1).s.started, "nothing on the bed may start the night early"
        self.get_in()
        await self.until(f"{day}T22:40")
        assert self.e(self.p1).s.started
        await self.until(f"{nxt}T06:30", dt=dt)
        assert self.e(self.p1).s.absent_since is None
        self.get_in(False)
        await self.until(f"{nxt}T06:35")
        assert self.e(self.p1).s.absent_since is not None, "getting up must still be seen"


async def test_heavier_duvet_3kg(tmp_path):
    n = Bed(tmp_path, "2026-11-05T18:00")
    await n.until("2026-11-05T19:00")
    n.clutter = 3.0                                   # the winter duvet goes on
    await n.normal_night("2026-11-05", "2026-11-06")
    n.assert_clean("2026-11-05T18:00", "2026-11-06T06:35")
    assert not n.pet_flags()


async def test_extra_pillows_2kg_while_asleep_and_after(tmp_path):
    n = Bed(tmp_path, "2026-11-05T21:00")
    n.clutter = 2.0                                   # two extra pillows
    await n.normal_night("2026-11-05", "2026-11-06")
    n.clutter = 0.0                                   # pillows taken off in the morning
    await n.until("2026-11-06T09:00")
    n.assert_clean("2026-11-05T21:00", "2026-11-06T09:00")
    assert not n.pet_flags()


async def test_weighted_blanket_7kg(tmp_path):
    n = Bed(tmp_path, "2026-11-05T19:00")
    await n.until("2026-11-05T20:00")
    n.clutter = 7.0                                   # laid out on the empty bed in the evening, slept under
    await n.normal_night("2026-11-05", "2026-11-06")
    await n.until("2026-11-06T07:00")
    n.clutter = 0.0                                   # folded away
    await n.until("2026-11-06T08:00")
    n.assert_clean("2026-11-05T19:00", "2026-11-06T08:00")
    assert not n.pet_flags()
    assert abs(n.mgr.scale(n.mgr.configs[0]).empty_kg - EMPTY) < 1.0, "the zero follows the blanket back off"


async def test_laundry_pile_10kg_in_the_daytime(tmp_path):
    n = Bed(tmp_path, "2026-11-05T12:00")
    await n.until("2026-11-05T13:00")
    n.clutter = 10.0                                  # clean laundry dumped on the bed for the afternoon
    await n.until("2026-11-05T18:00")
    n.clutter = 0.0
    await n.normal_night("2026-11-05", "2026-11-06")
    n.assert_clean("2026-11-05T12:00", "2026-11-06T06:35")
    assert not n.pet_flags()


async def test_laundry_left_on_the_bed_at_bedtime(tmp_path):
    n = Bed(tmp_path, "2026-11-05T17:00")
    n.clutter = 10.0                                  # never put away: you sleep next to it / push it aside
    await n.normal_night("2026-11-05", "2026-11-06")
    n.assert_clean("2026-11-05T17:00", "2026-11-06T06:35")
    assert not n.pet_flags()


async def test_cat_hopping_on_and_off(tmp_path):
    n = Bed(tmp_path, "2026-11-05T20:00")
    for _ in range(6):                                # evening: up for half a minute, off for two
        n.extra = 4.5
        n.bed.pets["left"] = 160
        await n.step(3, dt=10)
        n.extra = 0.0
        del n.bed.pets["left"]
        await n.step(12, dt=10)
    assert not n.e(n.p1).s.started and not n.pet_flags()
    assert all(x["present"] is not True for x in n.seen)
    await n.until("2026-11-05T22:30")
    n.get_in()
    await n.until("2026-11-06T01:00")
    for _ in range(4):                                # in the night: hops onto you and straight off again
        n.extra = 4.5
        await n.step(3, dt=10)
        n.extra = 0.0
        await n.step(12, dt=10)
    assert not n.pet_flags(), "a quick hop is not a pet event"
    assert n.e(n.p1).s.absent_since is None
    n.assert_clean("2026-11-05T22:30", "2026-11-06T02:00")


async def test_someone_perching_on_the_edge_for_2_minutes(tmp_path):
    n = Bed(tmp_path, "2026-11-05T21:00")
    await n.until("2026-11-05T21:30")
    n.extra = 0.45 * PERSON                           # sits on the edge to put socks on: part of a body on this side
    n.present("left", True)                           # worst case: the strip feels it too
    await n.step(4)
    n.extra = 0.0
    n.present("left", False)
    await n.until("2026-11-05T22:00")
    assert not n.e(n.p1).s.started, "perching is not going to bed"
    assert all(x["scale"] != 1.0 for x in n.seen)
    assert all(x["present"] is not True for x in n.seen)
    assert not n.pet_flags()
    await n.normal_night("2026-11-05", "2026-11-06")
    n.assert_clean("2026-11-05T22:00", "2026-11-06T06:35")


async def test_drift_over_a_week(tmp_path):
    n = Bed(tmp_path, "2026-11-02T18:00")
    n.mgr.calibration.person_kg.clear()               # nobody gave a weight: it has to learn it from the nights
    days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08", "2026-11-09"]
    for i, day in enumerate(days[:-1]):
        n.drift = 1.0 * i                             # load cells creep up a kilo a day: +6 kg by the end
        n.clutter = 3.0 if i >= 3 else 0.0            # the duvet changes halfway through
        n.clutter += 7.0 if i >= 5 else 0.0           # and a weighted blanket arrives: 18 kg over the first zero
        await n.normal_night(day, days[i + 1], dt=60)
        await n.until(f"{days[i + 1]}T18:00", dt=60)
    n.assert_clean("2026-11-02T18:00", "2026-11-09T06:35")
    assert not n.pet_flags()
    tr = n.mgr.scale(n.mgr.configs[0])
    assert abs(tr.empty_kg - (EMPTY + 6.0 + 10.0)) < 1.0, tr.empty_kg
    assert abs(tr.person_kg - PERSON) < 3.0, "it learned the sleeper's weight from the nights"


def test_scale_threshold_is_relative_to_the_person():
    tr = ScaleTracker()
    assert tr.threshold(20, []) == 20                 # nothing known: the device setting
    assert tr.threshold(20, [78]) == 0.6 * 78         # a person-sized load
    assert tr.threshold(20, [30]) == 20               # a light sleeper: never above the setting's floor...
    assert tr.threshold(40, [42]) == 0.85 * 42        # ...nor above most of the person
    tr.person_kg = 90
    assert tr.threshold(20, [60]) == 54               # learned weight wins over the given one


def test_short_spikes_dont_move_the_zero():
    tr = ScaleTracker(empty_kg=2.0)
    t = 0.0
    for i in range(200):                              # 100 min of an empty bed with a 30 s 12 kg bag dropped every 10 min
        v = 2.0 + (12.0 if i % 20 == 0 else 0.0)
        tr.update(t, v, empty=True, in_bed=False, threshold=46.8)
        t += 30
    assert abs(tr.empty_kg - 2.0) < 0.3
