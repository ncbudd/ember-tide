"""Oura cloud connection against a fake Oura API: OAuth, token refresh, 401, rate limits, storage and duplicate merge."""

import time
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config

H = {"X-Embertide": "1"}
TZ = ZoneInfo("America/New_York")
PW = "correct horse battery"


def client(tmp_path, auth=False):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, auth
    cfg.public_url = "https://bed.example.org"
    return TestClient(create_app(cfg))


def iso(s: str) -> str:
    return datetime.fromisoformat(s).replace(tzinfo=TZ).isoformat()


def ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


class FakeOura:
    """Just enough of api.ouraring.com: /oauth/token and three v2 collections."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.valid = {"at1"}
        self.refresh_ok = {"rt1"}
        self.n = 1
        self.rate_limited = 0
        self.fail_data_401 = 0
        self.pages = True
        self.naps = False

    def handler(self, req: httpx.Request):
        self.calls.append(req)
        assert req.headers.get("user-agent")
        u = urlsplit(str(req.url))
        if u.path == "/oauth/token":
            f = parse_qs(req.content.decode())
            assert f["client_id"] == ["cid_test"] and f["client_secret"] == ["sec_test"]
            if f["grant_type"] == ["authorization_code"]:
                if f["code"] != ["good"] or f["redirect_uri"] != ["https://bed.example.org/api/oura/callback"]:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": "at1", "refresh_token": "rt1", "expires_in": 86400, "token_type": "bearer"})
            rt = f["refresh_token"][0]
            if rt not in self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.refresh_ok.discard(rt)                  # single-use
            self.n += 1
            self.valid = {f"at{self.n}"}
            self.refresh_ok.add(f"rt{self.n}")
            return httpx.Response(200, json={"access_token": f"at{self.n}", "refresh_token": f"rt{self.n}", "expires_in": 86400})
        if u.path == "/oauth/revoke":
            return httpx.Response(200)
        if self.rate_limited:
            self.rate_limited -= 1
            return httpx.Response(429, headers={"retry-after": "600"})
        tok = req.headers.get("authorization", "").removeprefix("Bearer ")
        if self.fail_data_401:
            self.fail_data_401 -= 1
            return httpx.Response(401)
        if tok not in self.valid:
            return httpx.Response(401)
        q = parse_qs(u.query)
        if u.path.endswith("/sleep"):
            return httpx.Response(200, json={"data": [SLEEP] + ([NAP] if self.naps else []), "next_token": None})
        if u.path.endswith("/daily_readiness"):
            return httpx.Response(200, json={"data": [{"day": "2026-11-05", "score": 81, "temperature_deviation": -0.3,
                                                       "temperature_trend_deviation": 0.1}], "next_token": None})
        if u.path.endswith("/daily_sleep"):
            return httpx.Response(200, json={"data": [{"day": "2026-11-05", "score": 77}], "next_token": None})
        if u.path.endswith("/heartrate"):
            if self.pages and "next_token" not in q:
                return httpx.Response(200, json={"data": HR[:30], "next_token": "p2"})
            return httpx.Response(200, json={"data": HR[30:], "next_token": None})
        return httpx.Response(404)

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


T0 = ts("2026-11-04T23:00")
SLEEP = {"id": "s1", "day": "2026-11-05", "type": "long_sleep", "bedtime_start": iso("2026-11-04T23:00"), "bedtime_end": iso("2026-11-05T06:30"),
         "heart_rate": {"interval": 300, "items": [58, 56, None, 55], "timestamp": iso("2026-11-04T23:00")},
         "hrv": {"interval": 300, "items": [40, 44, 47, None, 50], "timestamp": iso("2026-11-04T23:00")},
         "readiness": {"temperature_deviation": -0.3, "score": 81}, "lowest_heart_rate": 52, "average_breath": 14.4,
         "sleep_phase_5_min": "4422211133" + "2" * 80}
NAP = {"id": "n1", "day": "2026-11-05", "type": "sleep", "bedtime_start": iso("2026-11-05T14:00"), "bedtime_end": iso("2026-11-05T14:45"),
       "total_sleep_duration": 2100, "light_sleep_duration": 1500, "deep_sleep_duration": 300, "rem_sleep_duration": 300, "awake_time": 600,
       "average_heart_rate": 61.5, "lowest_heart_rate": 56, "sleep_phase_5_min": "442222111"}
HR = [{"bpm": 60 - (i % 5), "source": "sleep", "timestamp": datetime.fromtimestamp(T0 + i * 300, TZ).isoformat()} for i in range(80)]


def connect(c, fake, pid="p2", owner_sets_app=True):
    c.app.state.oura.client = fake.client()
    if owner_sets_app:
        assert c.put("/api/integrations/oura", json={"client_id": "cid_test", "client_secret": "sec_test"}, headers=H).status_code == 200
    r = c.post("/api/oura/me/connect", json={"person": pid}, headers=H)
    assert r.status_code == 200, r.text
    u = urlsplit(r.json()["url"])
    q = parse_qs(u.query)
    assert u.netloc == "cloud.ouraring.com" and q["client_id"] == ["cid_test"] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://bed.example.org/api/oura/callback"] and "daily" in q["scope"][0]
    return q["state"][0]


def test_owner_sets_the_app_once_and_secrets_never_come_back(tmp_path):
    with client(tmp_path) as c:
        g = c.get("/api/integrations/oura").json()
        assert not g["configured"] and g["redirect_uri"] == "https://bed.example.org/api/oura/callback"
        assert c.put("/api/integrations/oura", json={"client_id": "cid_test"}, headers=H).status_code == 422     # needs the secret
        c.put("/api/integrations/oura", json={"client_id": "cid_test", "client_secret": "sec_test"}, headers=H)
        g = c.get("/api/integrations/oura")
        assert g.json()["configured"] and g.json()["secret_saved"] and "sec_test" not in g.text
        c.put("/api/integrations/oura", json={"client_id": "cid_test2"}, headers=H)                           # keeps the secret
        assert c.app.state.oura.app == {"client_id": "cid_test2", "client_secret": "sec_test"}
        assert c.post("/api/oura/me/connect", json={"person": "p1"}, headers=H).status_code == 200


def test_oauth_flow_stores_tokens_server_side(tmp_path):
    fake = FakeOura()
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        assert c.get("/api/oura/me?person=p2").json() == {**c.get("/api/oura/me?person=p2").json(), "connected": False}
        state = connect(c, fake)
        bad = c.get("/api/oura/callback?code=good&state=nope")
        assert bad.status_code == 400 and "expired" in bad.text
        denied = c.get(f"/api/oura/callback?error=access_denied&state={state}")
        assert denied.status_code == 400
        state = connect(c, fake, owner_sets_app=False)
        ok = c.get(f"/api/oura/callback?code=good&state={state}")
        assert ok.status_code == 200 and "connected" in ok.text and "at1" not in ok.text
        again = c.get(f"/api/oura/callback?code=good&state={state}")                 # single-use state
        assert again.status_code == 400
        st = c.get("/api/oura/me?person=p2")
        assert st.json()["connected"] and "at1" not in st.text and "rt1" not in st.text
        assert c.app.state.oura.token("p2")["refresh_token"] == "rt1"
        assert c.post("/api/oura/me/disconnect", json={"person": "p2"}, headers=H).json()["ok"]
        assert not c.get("/api/oura/me?person=p2").json()["connected"] and c.app.state.oura.token("p2") is None


def test_partner_connects_their_own_ring_but_cant_touch_the_app(tmp_path):
    fake = FakeOura()
    with client(tmp_path, auth=True) as c:
        c.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": "p1"})
        code = c.post("/api/auth/invites", json={"person": "p2", "role": "sleeper"}, headers=H).json()["code"]
        c.app.state.oura.client = fake.client()
        c.put("/api/integrations/oura", json={"client_id": "cid_test", "client_secret": "sec_test"}, headers=H)
        g = TestClient(c.app)
        assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
        assert g.put("/api/integrations/oura", json={"client_id": "x", "client_secret": "y"}, headers=H).status_code == 403
        r = g.post("/api/oura/me/connect", json={"person": "p1"}, headers=H)          # body person ignored: her own side
        state = parse_qs(urlsplit(r.json()["url"]).query)["state"][0]
        assert TestClient(c.app).get(f"/api/oura/callback?code=good&state={state}").status_code == 200   # no sign-in needed
        assert c.app.state.oura.token("p2") and c.app.state.oura.token("p1") is None


def _connected(c, fake):
    c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
    state = connect(c, fake)
    o = c.app.state.oura
    import asyncio
    asyncio.run(o.finish("good", o.take_state(state)))
    return o


def test_sync_stores_wearable_data_like_health_connect(tmp_path):
    fake = FakeOura()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        r = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H)
        assert r.status_code == 200, r.text
        stored = r.json()["stored"]
        assert stored["hr"] == 80 and stored["hrv"] == 4 and stored["readiness"] == 1 and stored["temp_dev"] == 1
        st = c.app.state.oura.c.store
        assert st.sample_sources("wear:p2:hr:") == ["wear:p2:hr:Oura"]
        assert len(st.samples("wear:p2:hrv:Oura", 0, 2e9)) == 4
        assert st.samples("wear:p2:temp_dev:Oura", 0, 2e9)[0][1] == -0.3
        segs = st.get_stages("p2", T0 - 1, T0 + 9 * 3600)["oura"]["segments"]
        assert segs[0]["stage"] == "awake" and segs[0]["end"] - segs[0]["start"] == 600 and segs[1]["stage"] == "light"
        assert {s["stage"] for s in segs} == {"awake", "light", "deep", "rem"}
        imports = c.get("/api/people/p2/imports").json()
        assert any(i["device"] == "Oura" and i["platform"] == "server" for i in imports)
        assert c.get("/api/oura/me?person=p2").json()["last_sync"]
        # a second sync adds nothing: every reading is already there
        again = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H).json()["stored"]
        assert again["hr"] == 0 and again["hrv"] == 0
        assert len(st.samples("wear:p2:hr:Oura", 0, 2e9)) == 80
        assert o.status("p1")["connected"] is False


def test_merges_with_health_connect_readings_already_there(tmp_path):
    fake = FakeOura()
    with client(tmp_path) as c:
        _connected(c, fake)
        # the same ring's readings, already imported through a phone's health store
        pts = [[h["timestamp"], h["bpm"]] for h in HR[:40]]
        r = c.post("/api/people/p2/wearable", json={"device": "Health Connect", "metric": "hr", "points": pts, "align": "off", "platform": "android"}, headers=H)
        assert r.json()["stored"] == 40
        out = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H).json()
        assert out["stored"]["hr"] == 40                                     # only the new half
        st = c.app.state.oura.c.store
        assert len(st.samples("wear:p2:hr:Oura", 0, 2e9)) + len(st.samples("wear:p2:hr:Health Connect", 0, 2e9)) == 80


def test_expired_token_is_refreshed_and_the_new_refresh_token_kept(tmp_path):
    fake = FakeOura()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        t = o.token("p2")
        o.store.put("oura_token:p2", {**t, "expires_at": time.time() - 10})
        assert c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H).status_code == 200
        t = o.token("p2")
        assert t["access_token"] == "at2" and t["refresh_token"] == "rt2" and t["expires_at"] > time.time() + 3600
        refreshes = [parse_qs(r.content.decode()) for r in fake.calls if r.url.path == "/oauth/token"]
        assert [f["grant_type"][0] for f in refreshes] == ["authorization_code", "refresh_token"]     # refreshed exactly once


def test_401_refreshes_once_then_asks_to_reconnect(tmp_path):
    fake = FakeOura()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        fake.fail_data_401 = 1                                    # one 401 (revoked server side): refresh and retry works
        assert c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H).status_code == 200
        assert o.token("p2")["access_token"] == "at2"
        fake.valid, fake.refresh_ok = set(), set()               # access and refresh both dead
        r = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H)
        assert r.status_code == 502
        s = c.get("/api/oura/me?person=p2").json()
        assert s["needs_reconnect"] and "Connect again" in s["last_error"]
        assert not o.due("p2", c.app.state.oura.c.clock.now_ts() + 7200)       # stops polling until reconnected


def test_rate_limit_backs_off(tmp_path):
    fake = FakeOura()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        fake.rate_limited = 1
        r = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H)
        assert r.status_code == 429
        now = c.app.state.oura.c.clock.now_ts()
        st = o.store.get("oura_status:p2")
        assert st["backoff_until"] >= now + 590 and "busy" in st["last_error"]
        assert not o.due("p2", now + 300)
        assert o.due("p2", now + 3700)
        assert c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H).status_code == 200
        assert o.store.get("oura_status:p2")["backoff_until"] is None


def test_polls_after_wake_then_hourly_never_at_night(tmp_path):
    fake = FakeOura()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        p2 = c.app.state.oura.c.person("p2")
        sc = next(r for r in p2.rhythms if 3 in r.days).schedule          # Thursday night's rhythm
        assert sc.bedtime == "22:15" and sc.wake == "06:30"
        o.store.put("oura_status:p2", {"last_attempt": ts("2026-11-05T21:00")})
        assert not o.due("p2", ts("2026-11-05T23:30"))                   # in the night
        assert not o.due("p2", ts("2026-11-06T06:40"))                   # just woke: let the ring sync first
        assert o.due("p2", ts("2026-11-06T06:55"))                       # the morning look
        o.store.put("oura_status:p2", {"last_attempt": ts("2026-11-06T06:55")})
        assert not o.due("p2", ts("2026-11-06T07:30"))
        assert o.due("p2", ts("2026-11-06T08:00"))                       # then hourly
        assert not o.due("p1", ts("2026-11-06T08:00"))                   # not connected


def test_once_a_night_numbers_and_the_ring_card(tmp_path):
    """Readiness, sleep score, temperature vs normal (+ trend), lowest heart rate and breathing land as samples and
    come back on the night as plain lines next to the bed's numbers."""
    fake = FakeOura()
    with client(tmp_path) as c:
        _connected(c, fake)
        stored = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H).json()["stored"]
        assert stored["sleep_score"] == 1 and stored["temp_trend"] == 1 and stored["resting_hr"] == 1 and stored["night_breath"] == 1
        st = c.app.state.oura.c.store
        assert st.samples("wear:p2:resting_hr:Oura", 0, 2e9)[0] == (ts("2026-11-05T06:30"), 52.0)
        ring = c.get("/api/people/p2/night?date=2026-11-04").json()["ring"]
        assert ring["device"] == "Oura" and not ring["unusual"]
        by = {x["key"]: x for x in ring["lines"]}
        assert by["readiness"]["value"] == "81" and by["readiness"]["note"].startswith("Good")
        assert by["sleep_score"]["value"] == "77" and by["temp_dev"]["value"] == "-0.3 °C" and by["temp_dev"]["note"] == "About your normal."
        assert by["resting_hr"]["value"] == "52 bpm" and by["night_breath"]["value"] == "14.4 /min"
        assert by["hrv_avg"]["value"] == "45 ms"
        assert ring["trend"] == [{"date": "2026-11-04", "c": -0.3}]


def test_new_metrics_backfill_once(tmp_path):
    """When the connector starts bringing new numbers, the next sync looks back the full first window once."""
    fake = FakeOura()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        st = o.store.get(o.k("status", "p2"), {}) or {}
        o._status("p2", last_sync=1.0, data_version=1)                  # synced before the new metrics existed
        fake.calls.clear()
        c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H)
        q = [parse_qs(urlsplit(str(r.url)).query) for r in fake.calls if r.url.path.endswith("/daily_sleep")]
        assert q and q[0]["start_date"] == ["2026-10-22"]                # 14 days back, not 2
        fake.calls.clear()
        c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H)
        q = [parse_qs(urlsplit(str(r.url)).query) for r in fake.calls if r.url.path.endswith("/daily_sleep")]
        assert q[0]["start_date"] == ["2026-11-03"]                      # then back to 2 days


def test_naps_are_their_own_sleeps(tmp_path):
    """Every Oura sleep is kept: the night as before, a nap as its own record (its stages stay out of the night's
    stage view), shown on that evening's night and in the sleeps list with the day's total."""
    fake = FakeOura()
    fake.naps = True
    with client(tmp_path) as c:
        _connected(c, fake)
        r = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H).json()
        assert r["stored"]["sleeps"] == 2
        st = c.app.state.oura.c.store
        assert not st.get_stages("p2", ts("2026-11-05T13:00"), ts("2026-11-05T16:00"))       # nap stages stay with the nap
        out = c.get("/api/people/p2/sleeps?days=30").json()
        kinds = sorted(x["kind"] for x in out["sleeps"])
        assert kinds == ["nap", "night"]
        nap = next(x for x in out["sleeps"] if x["kind"] == "nap")
        assert nap["asleep_min"] == 35 and nap["when"] == "2:00 PM–2:45 PM" and nap["where"] in ("bed", "away", "unknown")
        day = next(d for d in out["days"] if d["date"] == "2026-11-05")
        assert day["nap_min"] == 35 and day["naps"] == 1
        n = c.get("/api/people/p2/night?date=2026-11-05").json()
        assert [x["id"] for x in n["naps"]] == ["oura:n1"] and n["nap_min"] == 35
        again = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H).json()
        assert again["stored"]["sleeps"] == 0 and len(c.get("/api/people/p2/sleeps?days=30").json()["sleeps"]) == 2


def test_a_nap_on_the_bed_answers_the_pet_question(tmp_path, monkeypatch):
    """The bed asked "was that the dog?" about a stretch on her side; her ring says she was napping there: it was her."""
    from embertide.engine import petq, sleeps
    with client(tmp_path) as c:
        ctl = c.app.state.oura.c
        c.put("/api/pets", headers=H, json={"pets": [{"name": "Biscuit", "kind": "dog", "weight_kg": 29.5, "sleeps_on_bed": "yes", "side": "either"}]})
        p = ctl.person("p2")
        a, b = ts("2026-11-05T14:00"), ts("2026-11-05T14:45")
        q = petq.consider(ctl, p.zone, {"since": a, "last_on": b, "pet_ticks": 3, "ticks": 10, "readings": []}, b + 60)
        assert q and petq.open_for(ctl.store, "p2", b + 120)
        monkeypatch.setattr(sleeps, "place", lambda c_, p_, x: {"where": "bed", "where_why": "test"})
        sleeps.save(ctl, p, [{"id": "oura:n1", "kind": "nap", "start": a, "end": b, "asleep_min": 35}], b + 3600)
        assert petq.open_for(ctl.store, "p2", b + 3700) is None
        got = next(x for x in petq.questions(ctl.store) if x["id"] == q["id"])
        assert got["answer"] == "me" and got["settled"] == "fix"
