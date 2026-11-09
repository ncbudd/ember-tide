"""Me or not me: the sleeper's own watch heart rate against the bed pad, the learned normal range as a fallback, and
the pet questions. Simulated nights on the right side (p2), whose sleeper wears the watch; a big dog "Biscuit"
(29.5 kg, either side) may lie there instead."""

import random

import pytest

from embertide.engine import petq
from embertide.engine import watch as W
from embertide.models import Pet
from test_pets import PetNight

DOG = {"name": "Biscuit", "kind": "dog", "weight_kg": 29.5, "sleeps_on_bed": "yes", "side": "either"}
BAND = {"hr": [52.0, 64.0], "br": [12.0, 16.0], "restless": 0.2}


class WatchNight(PetNight):
    """PetNight plus the right-side sleeper's watch: `watch_fn(night) -> bpm | None` each 30 s step, handed over in
    5-minute batches like the phone relay does."""

    def __init__(self, tmp_path, start, watch_fn=None, band=None, echo=False, **kw):
        # the simulator's echo copies an occupied pad onto the other one; the pad processed first (left) wins, so it's
        # only on for the echo test, where the left sleeper is the real one
        super().__init__(tmp_path, start, echo=echo, **kw)
        self.watch_fn = watch_fn
        self.pending: list[tuple[float, float]] = []
        self.rand = random.Random(3)
        self.steps_n = 0
        if band is not None:
            ap = self.c.autopilot
            ap.baseline = lambda p, now: {"band": band}
            ap.learning = lambda p, now: {"need": 7, "have": 7, "done": True, "skipped": False}

    async def step(self, n=1, dt=30):
        for _ in range(n):
            await super().step(1, dt)
            now = self.clock.now_ts()
            if self.watch_fn is not None:
                v = self.watch_fn(self)
                if v is not None:
                    self.pending.append((now, float(v)))
            self.steps_n += 1
            if self.steps_n % 10 == 0 and self.pending:
                W.record(self.store, "p2", self.pending, [], now)
                self.pending = []

    def pad_hr(self, zone="right"):
        return self.c.presence.sides[zone].hr

    def fused(self, zone="right"):
        return self.c.fused.get(zone) or {}


def matching(n):          # the watch reads what the pad reads, give or take a beat
    hr = n.pad_hr()
    return None if hr is None else hr + n.rand.uniform(-2, 2)


def elsewhere(n):         # awake on the couch: about 72
    return 72 + n.rand.uniform(-2, 2)


# ---------- identity() itself ----------
def test_identity_states():
    now = 10_000.0
    pad = [(now - 600 + i * 10, 60 + (i % 3)) for i in range(61)]
    me = [(now - 540 + i * 60, 61) for i in range(9)]
    assert W.identity(me, pad, now)["state"] == "me"
    far = [(now - 540 + i * 60, 78) for i in range(9)]
    assert W.identity(far, pad, now)["state"] == "not_me"
    mid = [(now - 540 + i * 60, 68) for i in range(9)]
    assert W.identity(mid, pad, now)["state"] == "unsure"
    assert W.identity(me[:2], pad, now)["state"] == "unsure"                  # too few pairs
    assert W.identity([(now - 1800, 61)], pad, now)["state"] == "stale"      # nothing fresh: the watch is off
    walk = W.identity(mid, pad, now, steps=[(now - 200, 30), (now - 100, 25)])
    assert walk["state"] == "not_me" and "steps" in walk["why"]


def test_not_me_needs_a_steady_pad():
    now = 10_000.0
    jumpy = [(now - 600 + i * 10, 50 if i % 2 else 75) for i in range(61)]     # a restless pad: not a fair comparison
    far = [(now - 540 + i * 60, 90) for i in range(9)]
    assert W.identity(far, jumpy, now)["state"] != "not_me"


# ---------- simulated nights ----------
async def test_dog_on_the_empty_side_with_the_watch_elsewhere_is_not_a_person(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", watch_fn=elsewhere, pets=[DOG])
    n.bed.scripted = False
    await n.step(12)                                           # the watch has been reporting for a while
    n.bed.pets["right"] = 88                                   # a big dog's heart rate overlaps a person's
    await n.until("2026-11-05T22:30")
    assert not n.e(n.p2).s.started
    assert n.fused()["present"] is False
    ev = [e for e in n.events("pet_suspected") if e.get("zone") == "right"]
    assert ev and ev[0]["evidence"] == "watch" and ev[0]["pet"] == "Biscuit"
    assert n.fused()["watch"]["held"] == "not_me"


async def test_dog_with_no_pets_set_up_still_isnt_counted_by_the_watch(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", watch_fn=elsewhere)
    n.bed.scripted = False
    await n.step(12)
    n.bed.pets["right"] = 88
    await n.until("2026-11-05T22:30")
    assert n.fused()["present"] is False and not n.e(n.p2).s.started
    assert any(v["signal"] == "watch:me" and v["value"] == 0.0 for v in n.fused()["votes"])


async def test_dog_without_the_watch_is_caught_by_the_normal_range(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", pets=[DOG], band=BAND)
    n.bed.scripted = False
    n.bed.pets["right"] = 88
    await n.until("2026-11-05T22:30")
    assert not n.e(n.p2).s.started
    ev = [e for e in n.events("pet_suspected") if e.get("zone") == "right"]
    assert ev and ev[0]["evidence"] == "range" and "Biscuit" in ev[0]["why"]


async def test_dog_without_watch_or_learned_range_behaves_as_before(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", pets=[DOG])
    n.bed.scripted = False
    n.bed.pets["right"] = 88
    await n.until("2026-11-05T22:40")
    assert n.e(n.p2).s.started                                 # nothing to tell them apart: when in doubt it's a person


async def test_sleeper_in_bed_with_the_watch_matching(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", watch_fn=matching, pets=[DOG], band=BAND)
    n.bed.scripted = False
    await n.step(12)
    n.present("right", True)
    await n.until("2026-11-05T22:40")
    assert n.e(n.p2).s.started and n.fused()["present"] is True
    assert n.fused()["watch"]["held"] == "me"
    assert not [e for e in n.events("pet_suspected") if e.get("zone") == "right"]
    await n.until("2026-11-06T02:00")
    assert n.fused()["present"] is True


async def test_sleeper_in_bed_with_the_watch_off(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", watch_fn=None, pets=[DOG], band=BAND)
    n.bed.scripted = False
    n.present("right", True)
    await n.until("2026-11-05T22:30")
    assert n.e(n.p2).s.started and n.fused()["present"] is True
    assert "watch" not in n.fused() or n.fused()["watch"]["state"] == "stale"


async def test_the_watch_taken_off_mid_night_changes_nothing(tmp_path):
    on = {"v": True}
    n = WatchNight(tmp_path, "2026-11-05T22:00", watch_fn=lambda n: matching(n) if on["v"] else None, pets=[DOG])
    n.bed.scripted = False
    n.present("right", True)
    await n.until("2026-11-05T23:00")
    on["v"] = False
    await n.until("2026-11-06T01:00")
    assert n.fused()["present"] is True and n.e(n.p2).s.state == "rhythm"


async def test_a_settled_sleeper_is_never_voted_out_by_a_watch_gone_wrong(tmp_path):
    wrong = {"v": False}
    n = WatchNight(tmp_path, "2026-11-05T22:00", pets=[DOG],
                   watch_fn=lambda n: (n.pad_hr() or 60) + 20 if wrong["v"] else None)
    n.bed.scripted = False
    n.present("right", True)
    await n.until("2026-11-05T23:30")                          # in bed an hour and a half, no watch so far
    wrong["v"] = True                                          # then the watch reads 20 bpm high (loose strap)
    await n.until("2026-11-06T00:30")
    assert n.fused()["present"] is True and n.e(n.p2).s.state == "rhythm"


async def test_watch_mismatch_is_outvoted_by_a_radar_that_sees_a_person(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", watch_fn=elsewhere)
    ep = {"since": n.clock.now_ts(), "pad": [], "ident": {"state": "not_me"}, "ident_at": n.clock.now_ts(),
          "me_seen": False, "not_me": True, "nm_since": n.clock.now_ts() - 600, "empty_side": True}
    assert n.c._watch_vote("right", [("bed", 0.5, 1.0)], ep)[2] == 0.0
    assert n.c._watch_vote("right", [("bed", 0.5, 1.0), ("presence:r1", 0.3, 1.0)], ep) is None
    assert n.c._watch_vote("right", [("bed", 0.5, 1.0), ("weight:k1", 0.4, 1.0)], ep) is None


async def test_trusting_the_bed_more_keeps_the_bed_over_the_watch(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", watch_fn=elsewhere)
    n.p2.settings.autopilot.trust_more = "bed"
    n.bed.scripted = False
    await n.step(12)
    n.bed.pets["right"] = 88
    await n.until("2026-11-05T22:40")
    assert n.fused()["present"] is True                        # 0.4 for the watch doesn't beat the strip's 0.5


async def test_the_echo_of_the_left_sleeper_on_the_right(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", watch_fn=elsewhere, pets=[DOG], band=BAND, echo=True)
    n.bed.scripted = False
    await n.step(12)
    n.present("left", True)                                    # the right pad copies the left sleeper
    await n.until("2026-11-05T22:40")
    assert n.e(n.p1).s.started
    assert not n.e(n.p2).s.started and n.fused()["present"] is not True


async def test_afternoon_nap_asks_and_learns_the_person(tmp_path):
    n = WatchNight(tmp_path, "2026-11-06T14:00", pets=[DOG], band=BAND)
    n.bed.scripted = False
    n.present("right", True)
    await n.until("2026-11-06T14:40")
    n.present("right", False)
    await n.until("2026-11-06T14:45")
    q = petq.open_for(n.store, "p2", n.clock.now_ts())
    assert q and q["kind"] == "unexplained"
    v = petq.view(q, n.hh, n.clock.tz)
    assert v["title"].startswith("Was Biscuit on the bed around 2:00 PM")
    assert [c["label"] for c in v["choices"]] == ["Yes", "No, it was me", "Not sure"]
    petq.answer(n.c, q["id"], "p2", "me", None, n.clock.now_ts())
    rng = petq.learned_range(n.store, "p2")
    assert rng["hr"][0] < rng["hr"][1] and petq.open_for(n.store, "p2", n.clock.now_ts()) is None


async def test_nap_with_the_watch_matching_asks_nothing(tmp_path):
    n = WatchNight(tmp_path, "2026-11-06T14:00", watch_fn=matching, pets=[DOG])
    n.bed.scripted = False
    await n.step(12)
    n.present("right", True)
    await n.until("2026-11-06T14:50")
    n.present("right", False)
    await n.until("2026-11-06T14:55")
    assert petq.open_for(n.store, "p2", n.clock.now_ts()) is None


async def test_dog_episode_asks_and_yes_teaches_the_signature_used_next_time(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", pets=[DOG], band=BAND)
    n.bed.scripted = False
    n.bed.pets["right"] = 88
    await n.until("2026-11-05T22:40")
    n.bed.pets.pop("right")
    await n.until("2026-11-05T22:45")
    q = petq.open_for(n.store, "p2", n.clock.now_ts())
    assert q and q["kind"] == "pet" and q["spans"][0]["evidence"] == "range"
    petq.answer(n.c, q["id"], "p2", "yes", None, n.clock.now_ts())
    sig = petq.sig_view(petq.pet_sigs(n.store)[n.hh.pets[0].id])
    assert sig and 80 <= sig["hr"][0] <= 96 and 20 <= sig["br"][0] <= 32
    # a later night with a wider person range, so the dog's readings aren't "far outside" any more: the learned
    # signature recognises it
    (tmp_path / "b").mkdir()
    m = WatchNight(tmp_path / "b", "2026-11-06T22:00", pets=[DOG])
    m.store.put("pet_sigs", petq.pet_sigs(n.store))
    m.hh.pets = [Pet(**{**DOG, "id": n.hh.pets[0].id})]
    m.c.autopilot.baseline = lambda p, now: {"band": {"hr": [52.0, 72.0]}}
    m.c.autopilot.learning = lambda p, now: {"need": 7, "have": 7, "done": True, "skipped": False}
    m.bed.scripted = False
    m.bed.pets["right"] = 82                                    # within the person's reach, but like the dog
    await m.until("2026-11-06T22:30")
    ev = [e for e in m.events("pet_suspected") if e.get("zone") == "right"]
    assert ev and ev[0]["evidence"] == "signature"
    assert m.fused()["present"] is False and m.fused()["pet"]["suspected"]


async def test_one_question_per_night_and_questions_can_be_turned_off(tmp_path):
    n = WatchNight(tmp_path, "2026-11-05T22:00", pets=[DOG], band=BAND)
    n.bed.scripted = False
    for start, stop in (("22:00", "22:30"), ("22:35", "23:00")):
        n.bed.pets["right"] = 88
        await n.until(f"2026-11-05T{stop}")
        n.bed.pets.pop("right")
        await n.until(f"2026-11-05T{stop[:3]}{int(stop[3:]) + 4:02d}")
    qs = [q for q in petq.questions(n.store) if q["status"] == "open"]
    assert len(qs) == 1 and len(qs[0]["spans"]) == 2
    n.p2.settings.pet_questions = False
    assert petq.consider(n.c, "right", {"since": 0, "last_on": 3600, "pet_ticks": 5, "ticks": 10}, n.clock.now_ts()) is None


def test_question_api_and_answers(tmp_path):
    from test_autopilot_suite import H, make
    with make(tmp_path) as c:
        c.put("/api/pets", headers=H, json={"pets": [DOG]})
        assert c.get("/api/people/p2/pet-question").json() is None
        r = c.get("/api/pet-questions").json()
        assert r["pets"][0]["name"] == "Biscuit" and r["pets"][0]["learned"] is False
        bad = c.post("/api/people/p2/pet-question/nope", headers=H, json={"answer": "yes"})
        assert bad.status_code == 404
        assert c.post("/api/people/p2/pet-question/x", headers=H, json={"answer": "maybe"}).status_code == 422


def test_watch_hr_endpoint_validates_and_dedupes(tmp_path):
    from test_autopilot_suite import H, make
    with make(tmp_path) as c:
        now = c.get("/api/snapshot").json()["now"]
        r = c.post("/api/people/p2/watch-hr", headers=H, json={"samples": [[now - 120, 61], [now - 60, 62], [now - 30, 400]]})
        assert r.status_code == 200 and r.json()["stored"] == 2 and r.json()["rejected"] == 1
        assert c.post("/api/people/p2/watch-hr", headers=H, json={"samples": []}).status_code == 429
        g = c.get("/api/people/p2/watch-hr").json()
        assert g["count_hour"] == 2 and g["last"][1] == 62


def test_switching_live_heart_rate_off_on_the_phone_stops_using_it(tmp_path):
    from embertide.engine import phone_context as PC
    from embertide.store import Store
    s = Store(tmp_path / "s.db")
    W.record(s, "p2", [(1000.0, 60.0), (1060.0, 61.0)], [], 1100.0)
    assert W.recent(s, "p2", 1100.0)[0]
    PC.update(s, "p2", {}, 1200.0, sharing=["watch"])
    assert W.recent(s, "p2", 1200.0) == ([], [])


def test_far_outside_and_signature_rules():
    rng = {"hr": [52, 64], "br": [12, 16]}
    assert petq.far_outside(88, 14, rng) and petq.far_outside(60, 26, rng)
    assert not petq.far_outside(72, 15, rng)                    # a person just in bed, a little high: not far
    sigs = {"dog": {"hr": (70.0, 4.0), "br": (25.0, 3.0), "n": 40}}
    assert petq.closer_to_pet(70, 24, rng, sigs) == "dog"
    assert petq.closer_to_pet(72, 15, rng, sigs) is None        # awake person: breathing gives them away
    assert petq.person_range({"hr": [52, 64]}, {"hr": [50, 70], "br": [11, 18]}) == {"hr": [50, 70], "br": [11, 18]}
    assert petq.person_range(None, {"br": [11, 18]}) is None    # no heart-rate range yet: no fallback


def test_signature_learning_is_a_running_mean():
    s = petq._merge(None, [80, 90], "hr")
    s = petq._merge(s, [85, 85, 85], "hr")
    assert s["n_hr"] == 5 and s["hr_mean"] == pytest.approx(85)
    assert s["hr_m2"] == pytest.approx(50)


def test_watch_collects_only_at_night_and_while_someone_is_on_that_side(tmp_path):
    """Battery: heart rate only during the night AND while that side's pad says someone's there (10 min grace)."""
    from embertide.engine import watch as W
    from test_sides import make, ctl
    with make(tmp_path) as c:
        k = ctl(c)
        p = k.household.people[0]
        e = k.engine_for(p.id)
        side = k.presence.sides[p.zone]
        now = k.clock.now_ts()
        e.s.started, e.s.state = True, "rhythm"                     # a night is running
        side.raw_occupied, side.since = True, now - 3600
        assert W.wanted(k, p, now) is True
        side.raw_occupied, side.since = False, now - 120             # just got up: grace
        assert W.wanted(k, p, now) is True
        side.since = now - 900                                       # out of bed 15 min: stop
        assert W.wanted(k, p, now) is False
        side.raw_occupied, side.since = True, now                    # back in bed
        assert W.wanted(k, p, now) is True
        e.s.state = "done"                                           # morning: night over
        e.s.started = False
        if not (lambda n: n and n.precool - 1800 <= now <= n.end)(e.projected(now)):
            assert W.wanted(k, p, now) is False


# ---------- one visit, both sides: one answer settles both ----------
def _ep(a, b, readings=None):
    return {"since": a, "last_on": b, "pet_ticks": 0, "ticks": 10, "in_bed_ticks": 0,
            "readings": readings or [[a + 60 * i, 88.0, 24.0] for i in range(int((b - a) // 60))]}


async def test_a_dog_across_the_bed_is_one_question_whoever_answers(tmp_path):
    """Sunday afternoon: the dog lies across both sides. Two questions (one per side) are linked; Alex answers his,
    Sam's goes away too and her side learns the dog from it (it covers most of her stretch)."""
    n = WatchNight(tmp_path, "2026-11-08T13:00", pets=[DOG])
    t = n.clock.now_ts()
    qa = petq.consider(n.c, "left", _ep(t + 600, t + 9000), t + 9100)        # left: 13:10-15:30
    qb = petq.consider(n.c, "right", _ep(t + 1800, t + 9000), t + 9100)      # right: 13:30-15:30
    assert qa and qb and qa["person"] != qb["person"]
    assert qb["id"] in next(q for q in petq.questions(n.store) if q["id"] == qa["id"]).get("linked", [])
    petq.answer(n.c, qb["id"], qb["person"], "yes", None, t + 9200)
    assert petq.open_for(n.store, qa["person"], t + 9300) is None            # gone for the other person
    a = next(q for q in petq.questions(n.store) if q["id"] == qa["id"])
    assert a["answer"] == "yes" and a["answered_by"] == qb["person"] and a["settled"] == "partner" and a.get("learned") == "pet"


async def test_answered_first_then_the_other_side_appears_settled(tmp_path):
    n = WatchNight(tmp_path, "2026-11-08T13:00", pets=[DOG])
    t = n.clock.now_ts()
    qb = petq.consider(n.c, "right", _ep(t, t + 3000), t + 3100)
    petq.answer(n.c, qb["id"], qb["person"], "yes", None, t + 3200)
    qa = petq.consider(n.c, "left", _ep(t + 600, t + 3300), t + 3400)        # ends a little later: same visit
    assert qa["status"] == "answered" and petq.open_for(n.store, qa["person"], t + 3500) is None


async def test_it_was_me_speaks_only_for_my_side_and_separate_visits_stay_separate(tmp_path):
    n = WatchNight(tmp_path, "2026-11-08T13:00", pets=[DOG])
    t = n.clock.now_ts()
    qa = petq.consider(n.c, "left", _ep(t, t + 3000), t + 3100)
    qb = petq.consider(n.c, "right", _ep(t, t + 3000), t + 3100)
    petq.answer(n.c, qb["id"], qb["person"], "me", None, t + 3200)           # Alex napped on his side
    assert petq.open_for(n.store, qa["person"], t + 3300)["id"] == qa["id"]    # the dog question on hers stays
    (tmp_path / "b").mkdir()
    m = WatchNight(tmp_path / "b", "2026-11-08T13:00", pets=[DOG])
    t = m.clock.now_ts()
    x = petq.consider(m.c, "left", _ep(t, t + 1800), t + 1900)
    y = petq.consider(m.c, "right", _ep(t + 3 * 3600, t + 4 * 3600), t + 4 * 3600 + 60)   # hours later: another visit
    petq.answer(m.c, y["id"], y["person"], "yes", None, t + 5 * 3600)
    assert petq.open_for(m.store, x["person"], t + 5 * 3600)["id"] == x["id"]


async def test_fix_this_night_answers_the_pet_question_for_both(tmp_path):
    n = WatchNight(tmp_path, "2026-11-08T05:00", pets=[DOG])
    t = n.clock.now_ts()
    qa = petq.consider(n.c, "left", _ep(t + 3600, t + 4 * 3600), t + 4 * 3600 + 60)
    qb = petq.consider(n.c, "right", _ep(t + 3600, t + 4 * 3600), t + 4 * 3600 + 60)
    pet = n.hh.pets[0].id
    assert petq.settle_from_fix(n.c, qa["person"], [{"start": t + 3600, "end": t + 4 * 3600, "who": "pet", "pet": pet}], t + 5 * 3600) == 1
    assert petq.open_for(n.store, qa["person"], t + 5 * 3600) is None and petq.open_for(n.store, qb["person"], t + 5 * 3600) is None


def test_a_pet_that_sleeps_like_a_person_is_never_matched_by_signature():
    """Real data: the dog's learned signature is 61±7 bpm / 14±3 breaths, a sleeping adult. Matching on it would call
    a third of the sleepers' own readings "the dog", so it isn't used; a clearly different signature still is."""
    rng = {"hr": [52.0, 68.0], "br": [12.0, 17.0]}
    like_a_person = {"rex": {"hr": [61.0, 7.0], "br": [14.0, 3.0]}}
    assert petq.closer_to_pet(61, 14, rng, like_a_person) is None
    assert petq.closer_to_pet(60, 13, None, like_a_person) is None            # no learned range: a sleeping adult's
    fast = {"cat": {"hr": [130.0, 10.0], "br": [26.0, 3.0]}}
    assert petq.closer_to_pet(128, 25, rng, fast) == "cat"
