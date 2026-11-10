"""The Pets section's "what has been learned" summary (petq.pet_report, GET /api/pets/learned)."""

from fastapi.testclient import TestClient

from embertide.engine import away, petq
from test_auth import H, PW, invite_partner, make, setup_owner
from test_watch_identity import DOG, WatchNight, _ep


def _readings(t, hr, br, n=40):
    return [[t + 30 * i, hr, br] for i in range(n)]


async def test_nothing_learned_yet(tmp_path):
    n = WatchNight(tmp_path, "2026-11-08T13:00", pets=[DOG])
    rep = petq.pet_report(n.c, n.clock.now_ts())
    assert len(rep) == 1 and rep[0]["name"] == "Biscuit"
    assert rep[0]["learned"] is None and rep[0]["distinct"] is None and rep[0]["visits"] == []
    assert rep[0]["waiting"] == {"count": 0, "next_at": None}


async def test_learned_signature_visits_and_distinctness(tmp_path):
    n = WatchNight(tmp_path, "2026-11-08T13:00", pets=[DOG])
    t = n.clock.now_ts()
    q = petq.consider(n.c, "right", _ep(t, t + 4 * 3600, _readings(t, 88.0, 24.0, 60)), t + 4 * 3600 + 60)
    petq.answer(n.c, q["id"], q["person"], "yes", None, t + 4 * 3600 + 120)
    rep = petq.pet_report(n.c, t + 5 * 3600)[0]
    assert rep["learned"]["hr"] == 88 and rep["learned"]["br"] == 24 and rep["learned"]["stays"] == 1
    assert rep["learned"]["updated"] and rep["learned"]["minutes"] == 30
    assert rep["distinct"] is True                                   # 88 bpm is well above a sleeping adult
    assert [(v["zone"], v["seconds"]) for v in rep["visits"]] == [("right", 4 * 3600)]
    # a pet that sleeps like a person is reported as not told apart
    petq.forget_pet(n.store, n.c.household, DOG_ID(n))
    petq.learn_pet(n.store, DOG_ID(n), _readings(t, 60.0, 14.0))
    assert petq.pet_report(n.c, t)[0]["distinct"] is False


def DOG_ID(n):
    return n.c.household.pets[0].id


async def test_held_stays_are_counted_and_forget_clears_them(tmp_path):
    n = WatchNight(tmp_path, "2026-11-08T13:00", pets=[DOG])
    t = n.clock.now_ts()
    away.start(n.c, "p2", t)
    assert petq.consider(n.c, "right", _ep(t, t + 3 * 3600), t + 3 * 3600 + 60) is None
    w = petq.pet_report(n.c, t + 4 * 3600)[0]["waiting"]
    assert w == {"count": 1, "next_at": t + 3 * 3600 + away.LEARN_AFTER_S}
    petq.forget_pet(n.store, n.c.household, DOG_ID(n))
    assert petq.pet_report(n.c, t + 4 * 3600)[0]["waiting"]["count"] == 0


def test_api_owner_sees_all_partner_only_their_own_side(tmp_path):
    with make(tmp_path) as owner:
        setup_owner(owner)
        assert owner.put("/api/pets", headers=H, json={"pets": [{"name": "Rex", "kind": "dog", "weight_kg": 25, "sleeps_on_bed": "yes"}]}).status_code == 200
        ctl = owner.app.state.notifier.c
        pid = ctl.household.pets[0].id
        now = ctl.clock.now_ts()
        for who, zone in (("p1", "left"), ("p2", "right")):
            ctl.store.put(f"away_spans:{who}", [{"start": now - 1000, "end": now - 500, "zone": zone, "pet": pid, "readings": []}])
        rep = owner.get("/api/pets/learned").json()["pets"][0]
        assert rep["name"] == "Rex" and rep["learned"] is None and rep["waiting"]["count"] == 2 and not rep["partial"]
        code = invite_partner(owner)
        g = TestClient(owner.app)
        assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
        rep = g.get("/api/pets/learned").json()["pets"][0]
        assert rep["waiting"]["count"] == 1 and rep["partial"]            # only their own side's held stay
        assert g.delete(f"/api/pets/{pid}/learned", headers=H).status_code == 403
        assert owner.delete(f"/api/pets/{pid}/learned", headers=H).status_code == 200
        assert owner.delete("/api/pets/nope/learned", headers=H).status_code == 404
        assert owner.get("/api/pets/learned").json()["pets"][0]["waiting"]["count"] == 0
