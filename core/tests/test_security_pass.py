"""Hardening from the 2026-11 security review: private morning/review, history after a side swap, login and password
throttles, bounded export and imports, Home Assistant test inputs, push errors and saved tokens."""

import time

from fastapi.testclient import TestClient

from test_auth import H, PW, make, setup_owner


def partner(c, role="sleeper"):
    code = c.post("/api/auth/invites", json={"person": "p2", "role": role}, headers=H).json()["code"]
    g = TestClient(c.app)
    assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
    return g


def test_morning_cards_and_night_review_are_private(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        g = partner(c)
        assert g.get("/api/people/p1/morning").status_code == 403
        assert g.get("/api/people/p1/review?date=2026-11-25").status_code == 403
        assert g.get("/api/people/p2/morning").status_code == 200              # their own
        assert c.get("/api/people/p2/morning").status_code == 200              # the owner sees everyone


def test_history_after_a_swap_is_only_your_own_rows(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        g = partner(c)
        k = c.app.state.oura.c
        p1 = next(p for p in k.household.people if p.id == "p1")
        p2 = next(p for p in k.household.people if p.id == "p2")
        now = k.clock.now_ts()
        # the owner slept on what is now the partner's side, earlier
        k.store.telemetry([(now - 3600, p2.zone, 1, 20.0, 21.0, 1, 61, 14, 0, 20.0, "deep", 1, "p1"),
                               (now - 1800, p2.zone, 1, 20.0, 21.0, 1, 58, 13, 0, 20.0, "deep", 1, "p2")])
        rows = g.get(f"/api/history?zone={p2.zone}&hours=2").json()
        hrs = [r["hr"] for r in rows if r.get("hr")]
        assert 58 in hrs and 61 not in hrs                                     # theirs, not the owner's 61
        assert 61 in [r.get("hr") for r in c.get(f"/api/history?zone={p2.zone}&hours=2").json()]   # the owner sees all
        assert g.get(f"/api/history?zone={p2.zone}&hours=inf").status_code == 200
        assert p1.zone != p2.zone


def test_spaces_around_a_name_dont_dodge_the_login_throttle(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        c.post("/api/auth/logout", headers=H)
        for pad in ("alex", " alex", "alex ", "  alex", "alex\t"):
            c.post("/api/auth/login", json={"username": pad, "password": "wrong-wrong"})
        assert c.post("/api/auth/login", json={"username": "   alex  ", "password": PW}).status_code == 429


def test_current_password_cant_be_guessed_endlessly(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        for _ in range(5):
            assert c.post("/api/auth/me/password", json={"current": "wrong guess", "new": "another long one"}, headers=H).status_code == 422
        assert c.post("/api/auth/me/password", json={"current": PW, "new": "another long one"}, headers=H).status_code == 429


def test_export_window_is_bounded(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        for hours in ("1e9", "inf", "nan", "-5"):
            assert c.get(f"/api/export?hours={hours}").status_code == 200, hours


def test_imported_stages_older_than_400_days_are_refused(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        old = {"source": "x", "segments": [{"start": "2020-01-01T23:00", "end": "2020-01-02T01:00", "stage": "light"}]}
        r = c.post("/api/people/p1/stages", json=old, headers=H)
        assert r.status_code == 400


def test_home_assistant_test_checks_its_inputs(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        bad_entity = c.post("/api/sources/test-ha", json={"url": "http://homeassistant.local:8123", "token": "t",
                                                            "entity_id": "../../latest/meta-data/"}, headers=H).json()
        assert bad_entity["ok"] is False and "entity ID" in bad_entity["error"]
        meta = c.post("/api/sources/test-ha", json={"url": "http://169.254.169.254", "token": "t",
                                                     "entity_id": "sensor.x"}, headers=H).json()
        assert meta["ok"] is False and "address" in meta["error"]
        ftp = c.post("/api/sources/test-ha", json={"url": "ftp://ha.local", "token": "t", "entity_id": "sensor.x"}, headers=H).json()
        assert ftp["ok"] is False


def test_saved_push_token_stays_with_its_server(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        store = c.app.state.oura.c.store
        assert c.put("/api/notify/settings", json={"push_server": "https://ntfy.example.com", "push_token": "tk-synthetic-1"}, headers=H).status_code == 200
        assert store.get("notify_settings")["push_token"] == "tk-synthetic-1"
        c.put("/api/notify/settings", json={"push_server": "https://ntfy.example.com", "keep_push_token": True}, headers=H)
        assert store.get("notify_settings")["push_token"] == "tk-synthetic-1"            # same server: kept
        c.put("/api/notify/settings", json={"push_server": "http://other.example.com", "keep_push_token": True}, headers=H)
        assert store.get("notify_settings")["push_token"] is None                          # new address: dropped


def test_unknown_names_cost_one_hash_like_known_ones():
    from embertide import auth as A
    calls = []
    real = A.hash_password

    def counting(pw):
        calls.append(pw)
        return real(pw)
    A.hash_password = counting
    try:
        A._dummy_hash()
        A._dummy_hash()
    finally:
        A.hash_password = real
    assert len(calls) <= 1                                   # made once, then reused
    t = time.time()
    assert A.check_password("x", A._dummy_hash()) is False
    assert time.time() - t < 5
