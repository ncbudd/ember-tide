"""Cloud accounts (Withings, WHOOP, Polar on the shared framework), the providers list, webhooks and JSON polling,
all against fake services."""

import asyncio
import json
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.cloud.generic import Mapping, extract, get_path
from embertide.config import Config

H = {"X-Embertide": "1"}
TZ = ZoneInfo("America/New_York")
PW = "correct horse battery"


def client(tmp_path, auth=False):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, auth
    cfg.public_url = "https://bed.example.org"
    return TestClient(create_app(cfg))


def ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


def iso(s: str) -> str:
    return datetime.fromisoformat(s).replace(tzinfo=TZ).isoformat()


T0 = ts("2026-11-04T23:00")


class Fake:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def client(self):
        def h(req: httpx.Request):
            self.calls.append(req)
            u = urlsplit(str(req.url))
            for (host, path), fn in self.routes.items():
                if u.netloc == host and u.path == path:
                    return fn(req)
            return httpx.Response(404)
        return httpx.AsyncClient(transport=httpx.MockTransport(h))


def connect(c, prov: str, fake: Fake, pid="p2"):
    p = c.app.state.cloud.get(prov)
    p.client = fake.client()
    assert c.put(f"/api/integrations/cloud/{prov}", json={"client_id": "cid_test", "client_secret": "sec_test"}, headers=H).status_code == 200
    r = c.post(f"/api/cloud/me/{prov}/connect", json={"person": pid}, headers=H)
    assert r.status_code == 200, r.text
    q = parse_qs(urlsplit(r.json()["url"]).query)
    assert q["redirect_uri"] == [f"https://bed.example.org/api/cloud/{prov}/callback"]
    ok = c.get(f"/api/cloud/{prov}/callback?code=good&state={q['state'][0]}")
    assert ok.status_code == 200 and "connected" in ok.text, ok.text
    return p, q


def test_providers_list_and_owner_only_credentials(tmp_path):
    with client(tmp_path, auth=True) as c:
        c.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": "p1"})
        lst = {p["id"]: p for p in c.get("/api/cloud/providers").json()["providers"]}
        assert {"oura", "withings", "whoop", "polar"} <= {k for k, v in lst.items() if v["available"]}
        assert not lst["garmin"]["available"] and "partner" in lst["garmin"]["why"]
        assert lst["oura"]["redirect_uri"] == "https://bed.example.org/api/oura/callback"
        assert lst["withings"]["redirect_uri"] == "https://bed.example.org/api/cloud/withings/callback"
        c.put("/api/integrations/cloud/whoop", json={"client_id": "cid_test", "client_secret": "sec_test"}, headers=H)
        g = c.get("/api/integrations/cloud/whoop")
        assert g.json()["configured"] and "sec_test" not in g.text
        assert c.get("/api/cloud/providers").json()["providers"][2]["configured"]
        code = c.post("/api/auth/invites", json={"person": "p2", "role": "sleeper"}, headers=H).json()["code"]
        sam = TestClient(c.app)
        sam.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW})
        assert sam.put("/api/integrations/cloud/whoop", json={"client_id": "x1234", "client_secret": "y"}, headers=H).status_code == 403
        assert sam.get("/api/cloud/providers").status_code == 200                      # everyone can see what's there
        assert sam.post("/api/cloud/me/whoop/connect", json={}, headers=H).status_code == 200
        assert c.get("/api/cloud/nope/callback").status_code == 404


def test_withings_wrapped_tokens_stages_hr_weight(tmp_path):
    def token(req):
        f = parse_qs(req.content.decode())
        assert f["action"] == ["requesttoken"] and f["client_secret"] == ["sec_test"]
        return httpx.Response(200, json={"status": 0, "body": {"access_token": "wa1", "refresh_token": "wr1", "expires_in": 10800, "userid": 42}})
    calls = {"sleep": 0}

    def sleep(req):
        calls["sleep"] += 1
        assert req.headers["authorization"] == "Bearer wa1"
        series = [{"startdate": T0 + i * 1800, "enddate": T0 + (i + 1) * 1800, "state": [0, 1, 2, 3][i % 4],
                   "hr": {str(int(T0 + i * 1800 + 60)): 58 + i % 3}, "rr": {str(int(T0 + i * 1800 + 60)): 14}} for i in range(14)]
        return httpx.Response(200, json={"status": 0, "body": {"series": series}})

    def meas(req):
        return httpx.Response(200, json={"status": 0, "body": {"measuregrps": [{"date": T0 - 3600, "measures": [{"type": 1, "value": 72500, "unit": -3}]}]}})
    fake = Fake({("wbsapi.withings.net", "/v2/oauth2"): token, ("wbsapi.withings.net", "/v2/sleep"): sleep,
                 ("wbsapi.withings.net", "/measure"): meas})
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        p, q = connect(c, "withings", fake)
        assert q["scope"] == ["user.metrics,user.activity,user.sleepevents"]       # comma-separated, Withings style
        assert p.token("p2")["user"] == "42"
        r = c.post("/api/cloud/me/withings/sync", json={"person": "p2"}, headers=H)
        assert r.status_code == 200, r.text
        # connecting already ran a first sync in the background; this one finds everything there already
        assert r.json()["stored"] == {"hr": 0, "resp_rate": 0, "weight_kg": 0} and r.json()["nights"] == 1
        st = c.app.state.cloud.c.store
        assert len(st.samples("wear:p2:hr:Withings", 0, 2e9)) == 14 and len(st.samples("wear:p2:resp_rate:Withings", 0, 2e9)) == 14
        assert st.samples("wear:p2:weight_kg:Withings", 0, 2e9)[0][1] == 72.5
        segs = st.get_stages("p2", T0 - 1, T0 + 9 * 3600)["withings"]["segments"]
        assert [s["stage"] for s in segs[:4]] == ["awake", "light", "deep", "rem"]


def test_withings_status_200_with_error_code_means_reconnect(tmp_path):
    def token(req):
        f = parse_qs(req.content.decode())
        if f["grant_type"] == ["refresh_token"]:
            return httpx.Response(200, json={"status": 503, "error": "invalid refresh"})
        return httpx.Response(200, json={"status": 0, "body": {"access_token": "wa1", "refresh_token": "wr1", "expires_in": 10800}})
    fake = Fake({("wbsapi.withings.net", "/v2/oauth2"): token,
                 ("wbsapi.withings.net", "/v2/sleep"): lambda req: httpx.Response(200, json={"status": 401, "error": "invalid token"})})
    with client(tmp_path) as c:
        connect(c, "withings", fake)
        r = c.post("/api/cloud/me/withings/sync", json={"person": "p2"}, headers=H)
        assert r.status_code == 502
        s = c.get("/api/cloud/me/withings?person=p2").json()
        assert s["needs_reconnect"] and "Connect again" in s["last_error"]


def test_whoop_nightly_scores(tmp_path):
    def token(req):
        return httpx.Response(200, json={"access_token": "wh1", "refresh_token": "whr1", "expires_in": 3600, "scope": "offline read:sleep read:recovery"})
    pages = {"n": 0}

    def sleep(req):
        q = parse_qs(urlsplit(str(req.url)).query)
        if "nextToken" not in q:
            return httpx.Response(200, json={"records": [{"id": "s1", "start": iso("2026-11-04T23:00"), "end": iso("2026-11-05T06:30"), "nap": False,
                                                         "score": {"respiratory_rate": 15.2}}], "next_token": "p2"})
        pages["n"] += 1
        return httpx.Response(200, json={"records": [{"id": "nap1", "start": iso("2026-11-05T13:00"), "end": iso("2026-11-05T13:30"), "nap": True, "score": {}}], "next_token": None})

    def rec(req):
        return httpx.Response(200, json={"records": [{"sleep_id": "s1", "created_at": iso("2026-11-05T06:40"),
                                                      "score": {"recovery_score": 66, "resting_heart_rate": 54, "hrv_rmssd_milli": 48.5,
                                                                "skin_temp_celsius": 33.9, "spo2_percentage": 96.5}}]})
    fake = Fake({("api.prod.whoop.com", "/oauth/oauth2/token"): token, ("api.prod.whoop.com", "/developer/v2/activity/sleep"): sleep,
                 ("api.prod.whoop.com", "/developer/v2/recovery"): rec})
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        p, q = connect(c, "whoop", fake)
        assert "offline" in q["scope"][0]
        c.post("/api/cloud/me/whoop/sync", json={"person": "p2"}, headers=H).json()
        assert pages["n"] >= 1                                            # followed next_token
        st = c.app.state.cloud.c.store
        for m in ("resp_rate", "resting_hr", "hrv", "skin_temp", "spo2", "readiness"):
            assert len(st.samples(f"wear:p2:{m}:WHOOP", 0, 2e9)) == 1, m          # the nap was left out, nothing doubled
        assert st.samples("wear:p2:hrv:WHOOP", 0, 2e9) == [(ts("2026-11-05T06:30"), 48.5)]


def test_polar_basic_auth_registration_and_hypnogram(tmp_path):
    seen = {}

    def token(req):
        seen["auth"] = req.headers.get("authorization", "")
        assert "client_secret" not in req.content.decode()
        return httpx.Response(200, json={"access_token": "pa1", "token_type": "bearer", "x_user_id": 777})

    def users(req):
        seen["member"] = json.loads(req.content)["member-id"]
        return httpx.Response(409)                                     # already registered is fine

    def sleep(req):
        return httpx.Response(200, json={"nights": [{"date": "2026-11-05", "sleep_start_time": iso("2026-11-04T23:00"), "sleep_end_time": iso("2026-11-05T06:30"),
                                                     "hypnogram": {"23:00": 0, "23:10": 2, "23:40": 4, "01:00": 1, "02:00": 3},
                                                     "heart_rate_samples": {"23:05": 61, "00:30": 55, "05:00": 57}}]})

    def recharge(req):
        return httpx.Response(200, json={"recharges": [{"date": "2026-11-05", "heart_rate_avg": 56, "heart_rate_variability_avg": 41, "breathing_rate_avg": 14.5}]})
    fake = Fake({("polarremote.com", "/v2/oauth2/token"): token, ("www.polaraccesslink.com", "/v3/users"): users,
                 ("www.polaraccesslink.com", "/v3/users/sleep"): sleep, ("www.polaraccesslink.com", "/v3/users/nightly-recharge"): recharge})
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        p, _ = connect(c, "polar", fake)
        assert seen["auth"].startswith("Basic ") and seen["member"] == "et-p2"
        assert p.token("p2")["refresh_token"] is None and p.token("p2")["user"] == "777"
        r = c.post("/api/cloud/me/polar/sync", json={"person": "p2"}, headers=H).json()
        assert r["nights"] == 1
        st = c.app.state.cloud.c.store
        assert len(st.samples("wear:p2:hrv:Polar", 0, 2e9)) == 1
        segs = st.get_stages("p2", T0 - 1, T0 + 9 * 3600)["polar"]["segments"]
        assert [s["stage"] for s in segs] == ["awake", "light", "deep", "rem", "light"]
        assert segs[3]["start"] == ts("2026-11-05T01:00") and segs[-1]["end"] == ts("2026-11-05T06:30")
        hr = st.samples("wear:p2:hr:Polar", 0, 2e9)
        assert hr[1] == (ts("2026-11-05T00:30"), 55.0)                 # after midnight lands on the next day


def test_json_paths_and_mapping():
    body = {"data": {"items": [{"t": "2026-11-05T03:00:00-05:00", "v": {"bpm": 55}}, {"t": 1793944800000, "v": {"bpm": "57"}}, {"v": {}}]}}
    assert get_path(body, "$.data.items[1].v.bpm") == "57" and get_path(body, "data.items.0.t").startswith("2026")
    assert get_path(body, "data.nope.x") is None and get_path([1, 2], "5") is None
    pts = extract(body, Mapping(metric="hr", items_path="data.items", value_path="v.bpm", time_path="t"), 1.0)
    assert pts == [["2026-11-05T03:00:00-05:00", 55.0], [1793944800000, 57.0]]
    one = extract({"temp": 93.2}, Mapping(metric="skin_temp", value_path="temp", unit="F"), 5.0)
    assert one[0][0] == 5.0 and round(one[0][1], 1) == 34.0


def test_inbound_webhook_secret_url(tmp_path):
    with client(tmp_path, auth=True) as c:
        c.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": "p1"})
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        h = c.post("/api/integrations/hooks", json={"name": "Ring bridge", "person": "p2", "metric": "hr", "items_path": "readings",
                                                    "value_path": "bpm", "time_path": "at"}, headers=H).json()
        url = urlsplit(h["url"]).path
        assert "secret" not in h and url.startswith(f"/api/hooks/{h['id']}/")
        anon = TestClient(c.app)                                         # no sign-in, no header: the URL is the key
        body = {"readings": [{"at": T0 + i * 60, "bpm": 60 + i % 4} for i in range(30)]}
        r = anon.post(url, json=body)
        assert r.status_code == 200 and r.json()["stored"] == 30, r.text
        assert anon.post(url, json=body).json()["stored"] == 0           # same readings again: duplicates skipped
        assert anon.post(f"/api/hooks/{h['id']}/wrong-secret-0123456789", json=body).status_code in (401, 404)
        assert anon.post(url, content=b"not json", headers={"content-type": "application/json"}).status_code == 400
        assert anon.post(url, content=b"x" * 300000).status_code == 413
        assert anon.get("/api/integrations/hooks").status_code == 401
        st = c.app.state.cloud.c.store
        assert len(st.samples("wear:p2:hr:Ring bridge", 0, 2e9)) == 30
        listed = c.get("/api/integrations/hooks").json()["hooks"][0]
        assert listed["last_stored"] == 0 and listed["last_ok"]
        c.delete(f"/api/integrations/hooks/{h['id']}", headers=H)
        assert anon.post(url, json=body).status_code == 404


def test_json_poller_with_header_auth(tmp_path):
    seen = []

    def api(req):
        seen.append(req.headers.get("x-api-key"))
        if req.headers.get("x-api-key") != "k-0123456789":
            return httpx.Response(401)
        return httpx.Response(200, json={"result": {"weight": 81250, "time": T0}})
    fake = Fake({("scale.example.net", "/latest"): api})
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        g = c.app.state.cloud.generic
        g.client = fake.client()
        bad = c.post("/api/integrations/polls", json={"name": "Scale", "person": "p1", "metric": "weight_kg", "url": "ftp://x", "value_path": "result.weight"}, headers=H)
        assert bad.status_code == 422
        s = c.post("/api/integrations/polls", json={"name": "Scale", "person": "p1", "metric": "weight_kg", "url": "https://scale.example.net/latest",
                                                    "headers": {"X-Api-Key": "k-0123456789"}, "value_path": "result.weight", "time_path": "result.time",
                                                    "scale": 0.001, "interval_min": 30}, headers=H).json()
        assert s["headers"]["X-Api-Key"].startswith("••••") and "k-0123456789" not in json.dumps(s)
        r = c.post(f"/api/integrations/polls/{s['id']}/run", headers=H).json()
        assert r["ok"] and r["stored"] == 1
        assert c.app.state.cloud.c.store.samples("wear:p1:weight_kg:Scale", 0, 2e9)[0][1] == 81.25
        # editing with the masked value keeps the real key
        c.put(f"/api/integrations/polls/{s['id']}", json={**{k: s[k] for k in ("name", "person", "metric", "url", "value_path", "time_path", "scale", "interval_min")},
                                                         "headers": s["headers"]}, headers=H)
        assert g.polls[0]["headers"]["X-Api-Key"] == "k-0123456789"
        # ...but not when the address moves to another host: the saved key isn't sent somewhere new
        moved = {**{k: s[k] for k in ("name", "person", "metric", "value_path", "time_path", "scale", "interval_min")},
                 "url": "https://other.example.org/latest", "headers": s["headers"]}
        c.put(f"/api/integrations/polls/{s['id']}", json=moved, headers=H)
        assert "X-Api-Key" not in g.polls[0]["headers"]
        c.put(f"/api/integrations/polls/{s['id']}", json={**moved, "url": s["url"], "headers": {"X-Api-Key": "k-0123456789"}}, headers=H)
        # due only every interval; a 401 is reported, not raised
        asyncio.run(g.tick())
        assert len(seen) == 1
        g.store.put("cloud_polls", [{**g.polls[0], "headers": {"X-Api-Key": "wrong"}, "last_ts": 0}])
        asyncio.run(g.tick())
        assert g.polls[0]["last_error"] == "answered 401" and not g.polls[0]["last_ok"]
