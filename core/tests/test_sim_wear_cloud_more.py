"""The other cloud accounts (Withings, WHOOP, Polar) when the service has a bad day, and Oura's odd sleeps."""

import httpx
from test_cloud import H, Fake, client, connect, iso, ts
from test_oura import NAP, SLEEP, FakeOura, _connected
from test_oura import client as oura_client


def sync(c, prov, expect=502):
    r = c.post(f"/api/cloud/me/{prov}/sync", json={"person": "p2"}, headers=H)
    assert r.status_code == expect, (r.status_code, r.text)
    return r


def stat(c, prov):
    return c.get(f"/api/cloud/me/{prov}?person=p2").json()


def whoop_fake(sleep, rec=None):
    token = lambda req: httpx.Response(200, json={"access_token": "wh1", "refresh_token": "whr1", "expires_in": 3600})   # noqa: E731
    return Fake({("api.prod.whoop.com", "/oauth/oauth2/token"): token, ("api.prod.whoop.com", "/developer/v2/activity/sleep"): sleep,
                 ("api.prod.whoop.com", "/developer/v2/recovery"): rec or (lambda req: httpx.Response(200, json={"records": []}))})


def test_whoop_500_then_recovery_keeps_what_it_had(tmp_path):
    state = {"down": False}

    def sleep(req):
        if state["down"]:
            return httpx.Response(500)
        return httpx.Response(200, json={"records": []})
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        connect(c, "whoop", whoop_fake(sleep))
        sync(c, "whoop", 200)
        state["down"] = True
        sync(c, "whoop")
        s = stat(c, "whoop")
        assert "500" in s["last_error"] and not s["needs_reconnect"]
        state["down"] = False
        sync(c, "whoop", 200)
        assert stat(c, "whoop")["last_error"] is None


def test_whoop_403_means_a_permission_or_membership_problem_in_plain_words(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        connect(c, "whoop", whoop_fake(lambda req: httpx.Response(403)))
        sync(c, "whoop")
        err = stat(c, "whoop")["last_error"]
        assert "permission" in err and "membership" in err and not stat(c, "whoop")["needs_reconnect"]


def test_whoop_garbage_scores_are_skipped_not_stored(tmp_path):
    def rec(req):
        return httpx.Response(200, json={"records": [{"sleep_id": "s1", "created_at": iso("2026-11-05T06:40"),
                                                      "score": {"recovery_score": 600, "resting_heart_rate": 0, "hrv_rmssd_milli": "x",
                                                                "skin_temp_celsius": 99, "spo2_percentage": None}}]})
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        connect(c, "whoop", whoop_fake(lambda req: httpx.Response(200, json={"records": []}), rec))
        sync(c, "whoop", 200)
        st = c.app.state.cloud.c.store
        for m in ("readiness", "resting_hr", "hrv", "skin_temp", "spo2"):
            assert st.samples(f"wear:p2:{m}:WHOOP", 0, 2e9) == [], m


def polar_fake(sleep_status=200, body=None):
    token = lambda req: httpx.Response(200, json={"access_token": "pa1", "x_user_id": 7})   # noqa: E731
    return Fake({("polarremote.com", "/v2/oauth2/token"): token, ("www.polaraccesslink.com", "/v3/users"): lambda req: httpx.Response(409),
                 ("www.polaraccesslink.com", "/v3/users/sleep"): lambda req: httpx.Response(sleep_status, json=body or {"nights": []}),
                 ("www.polaraccesslink.com", "/v3/users/nightly-recharge"): lambda req: httpx.Response(200, json={"recharges": []})})


def test_polar_has_no_refresh_so_a_401_asks_to_connect_again(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        connect(c, "polar", polar_fake(sleep_status=401))
        sync(c, "polar")
        s = stat(c, "polar")
        assert s["needs_reconnect"] and "Connect again" in s["last_error"]


def test_polar_odd_nights_do_not_break_the_sync(tmp_path):
    body = {"nights": [{"sleep_start_time": "garbage", "sleep_end_time": None}, {"sleep_start_time": iso("2026-11-04T23:00"),
                                                                                 "sleep_end_time": iso("2026-11-04T22:00")},
                       {"sleep_start_time": iso("2026-11-04T23:00"), "sleep_end_time": iso("2026-11-05T06:30"),
                        "hypnogram": {"zz": 1, "23:00": 9, "23:10": 2}, "heart_rate_samples": {"x": 60, "23:20": 61}}]}
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        connect(c, "polar", polar_fake(body=body))
        sync(c, "polar", 200)
        st = c.app.state.cloud.c.store
        assert len(st.samples("wear:p2:hr:Polar", 0, 2e9)) == 1


def test_withings_busy_status_backs_off_with_a_calm_message(tmp_path):
    token = lambda req: httpx.Response(200, json={"status": 0, "body": {"access_token": "wa1", "refresh_token": "wr1", "expires_in": 10800}})   # noqa: E731
    fake = Fake({("wbsapi.withings.net", "/v2/oauth2"): token,
                 ("wbsapi.withings.net", "/v2/sleep"): lambda req: httpx.Response(200, json={"status": 601})})
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        p, _ = connect(c, "withings", fake)
        sync(c, "withings", 429)
        s = stat(c, "withings")
        assert s["last_error"] == "Withings is busy; trying again later."
        assert not p.due("p2", p.c.clock.now_ts() + 300) and p.due("p2", p.c.clock.now_ts() + 3 * 3600)


def test_withings_unexpected_status_is_an_error_not_a_sign_out(tmp_path):
    token = lambda req: httpx.Response(200, json={"status": 0, "body": {"access_token": "wa1", "refresh_token": "wr1", "expires_in": 10800}})   # noqa: E731
    fake = Fake({("wbsapi.withings.net", "/v2/oauth2"): token,
                 ("wbsapi.withings.net", "/v2/sleep"): lambda req: httpx.Response(200, json={"status": 2554})})
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        connect(c, "withings", fake)
        sync(c, "withings")
        s = stat(c, "withings")
        assert "2554" in s["last_error"] and not s["needs_reconnect"]


# ---------- Oura sleeps ----------
def test_oura_two_sleep_sessions_the_same_night_and_a_late_nap_are_kept_apart(tmp_path):
    second = {**SLEEP, "id": "s2", "type": "sleep", "bedtime_start": SLEEP["bedtime_end"], "bedtime_end": SLEEP["bedtime_end"].replace("06:30", "07:20"),
              "sleep_phase_5_min": "222", "heart_rate": {"interval": 300, "items": [], "timestamp": SLEEP["bedtime_end"]}}

    class Fake2(FakeOura):
        def handler(self, req):
            r = super().handler(req)
            if req.url.path.endswith("/sleep") and r.status_code == 200:
                import json
                j = json.loads(r.content)
                j["data"] = j["data"] + [second]
                return httpx.Response(200, json=j)
            return r
    fake = Fake2()
    with oura_client(tmp_path) as c:
        o = _connected(c, fake)
        c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H)
        sleeps = c.get("/api/people/p2/sleeps?days=30").json()["sleeps"]
        assert len(sleeps) == 2
        night = c.get("/api/people/p2/night?date=2026-11-04").json()
        assert night["stage_sources"]


def test_oura_a_sleep_with_end_before_start_or_missing_times_is_skipped(tmp_path):
    broken = [{**SLEEP, "id": "b1", "bedtime_end": SLEEP["bedtime_start"]}, {**SLEEP, "id": "b2", "bedtime_start": None},
              {**SLEEP, "id": "b3", "sleep_phase_5_min": "9x?", "heart_rate": {"interval": 0, "items": [1, 2], "timestamp": "bad"}}]

    class Fake3(FakeOura):
        def handler(self, req):
            if req.url.path.endswith("/sleep"):
                return httpx.Response(200, json={"data": broken, "next_token": None})
            return super().handler(req)
    fake = Fake3()
    with oura_client(tmp_path) as c:
        _connected(c, fake)
        r = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H)
        assert r.status_code == 200, r.text
        assert c.get("/api/oura/me?person=p2").json()["last_error"] is None
