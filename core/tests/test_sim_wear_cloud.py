"""Cloud accounts (Oura first): what is stored, what the person is told, and what keeps trying, across the failures a
real account has. Built on the fake Oura in test_oura.py."""

import asyncio
import time
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from fastapi.testclient import TestClient
from test_oura import HR, SLEEP, T0, FakeOura, _connected, client, ts

H = {"X-Embertide": "1"}


class Oura2(FakeOura):
    """The same fake with switches for the ways a real service misbehaves."""

    def __init__(self):
        super().__init__()
        self.errors = 0              # next N data calls answer 500
        self.status = None           # every data call answers this status
        self.hr = HR
        self.temp_dev = -0.3
        self.empty = False
        self.boom = 0                # next N calls fail at the network level

    def handler(self, req: httpx.Request):
        u = urlsplit(str(req.url))
        if u.path.startswith("/v2/"):
            if self.boom:
                self.boom -= 1
                raise httpx.ConnectError("unreachable")
            if self.status:
                self.calls.append(req)
                return httpx.Response(self.status, json={"detail": "x"})
            if self.errors:
                self.errors -= 1
                self.calls.append(req)
                return httpx.Response(500)
            if self.empty:
                self.calls.append(req)
                return httpx.Response(200, json={"data": [], "next_token": None})
            if u.path.endswith("/heartrate"):
                self.calls.append(req)
                return httpx.Response(200, json={"data": self.hr, "next_token": None})
            if u.path.endswith("/daily_readiness"):
                self.calls.append(req)
                return httpx.Response(200, json={"data": [{"day": "2026-11-05", "score": 70, "temperature_deviation": self.temp_dev,
                                                           "temperature_trend_deviation": 0.1}], "next_token": None})
        return super().handler(req)


def sync(c, expect=200):
    r = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H)
    assert r.status_code == expect, (r.status_code, r.text)
    return r


def status(c):
    return c.get("/api/oura/me?person=p2").json()


def test_a_refresh_the_provider_refuses_asks_the_person_to_connect_again(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        o.store.put("oura_token:p2", {**o.token("p2"), "expires_at": time.time() - 10})
        fake.refresh_ok = set()                                        # the refresh token was revoked
        r = sync(c, 502)
        assert "Connect again" in r.text or "sign-in" in r.text
        s = status(c)
        assert s["needs_reconnect"] and "Connect again" in s["last_error"]
        assert not o.due("p2", c.app.state.oura.c.clock.now_ts() + 86400)         # not polled again for nothing


def test_reconnecting_clears_the_message_and_polling_resumes(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        fake.valid, fake.refresh_ok = set(), set()
        sync(c, 502)
        assert status(c)["needs_reconnect"]
        fake.valid, fake.refresh_ok = {"at1"}, {"rt1"}
        asyncio.run(o.finish("good", {"pid": "p2", "redirect_uri": "https://bed.example.org/api/oura/callback"}))
        s = status(c)
        assert not s["needs_reconnect"] and not s["last_error"]
        sync(c)
        assert status(c)["last_sync"]


def test_a_400_on_a_data_call_is_an_error_but_not_a_sign_out(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        fake.status = 400
        r = sync(c, 502)
        s = status(c)
        assert not s["needs_reconnect"], s                              # signed in fine; the request was refused
        assert s["last_error"] and "400" in s["last_error"]
        fake.status = None
        now = o.c.clock.now_ts()
        assert o.due("p2", now + 7200)                                  # still tried again later
        sync(c)
        assert status(c)["last_error"] is None


def test_a_403_says_a_permission_is_missing_without_asking_to_reconnect(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        _connected(c, fake)
        fake.status = 403
        sync(c, 502)
        s = status(c)
        assert "permission" in s["last_error"] and not s["needs_reconnect"]


def test_server_errors_for_a_day_keep_the_last_good_data_and_recover_quietly(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        sync(c)
        st = o.store
        good = len(st.samples("wear:p2:hr:Oura", 0, 2e9))
        assert good == 80
        last_ok = status(c)["last_sync"]
        fake.status = 500
        now = o.c.clock.now_ts()
        for hour in range(24):                                          # the hourly poll for a day
            o.c.clock.advance(3600) if hasattr(o.c.clock, "advance") else None
            r = sync(c, 502)
            assert "500" in status(c)["last_error"]
        assert len(st.samples("wear:p2:hr:Oura", 0, 2e9)) == good      # nothing lost or invented
        assert status(c)["last_sync"] == last_ok                        # the "last synced" time stays honest
        assert not status(c)["needs_reconnect"]
        fake.status = None
        sync(c)
        s = status(c)
        assert s["last_error"] is None and s["last_sync"] > last_ok
        assert len(st.samples("wear:p2:hr:Oura", 0, 2e9)) == good


def test_the_network_down_is_described_as_unreachable(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        _connected(c, fake)
        fake.boom = 1
        r = sync(c, 502)
        assert "Couldn't reach Oura" in status(c)["last_error"]
        sync(c)


def test_a_rate_limit_message_is_calm_and_clears_after_success(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        _connected(c, fake)
        fake.rate_limited = 1
        sync(c, 429)
        assert status(c)["last_error"] == "Oura is busy; trying again later."
        sync(c)
        assert status(c)["last_error"] is None


def test_data_the_provider_revises_the_next_day_does_not_pile_up_next_to_the_old_value(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        sync(c)
        revised = [dict(x, bpm=x["bpm"] + 1) for x in HR]              # same moments, new numbers
        fake.hr = revised
        sync(c)
        pts = o.store.samples("wear:p2:hr:Oura", 0, 2e9)
        moments = [round(a) for a, _ in pts]
        assert len(moments) == len(set(moments)), "two values stored for one moment"
        assert len(pts) == 80


def test_a_day_with_no_data_stores_nothing_and_is_not_an_error(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        _connected(c, fake)
        fake.empty = True
        r = sync(c)
        assert r.json()["stored"].get("hr", 0) == 0
        s = status(c)
        assert s["last_error"] is None and s["last_sync"]


def test_a_night_with_temperature_well_above_normal_is_marked_and_left_out_of_learning(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        fake.temp_dev = 1.4
        sync(c)
        ring = c.get("/api/people/p2/night?date=2026-11-04").json()["ring"]
        assert ring["unusual"]
        line = next(x for x in ring["lines"] if x["key"] == "temp_dev")
        assert "leaves this night out" in line["note"] and line["tone"] == "warn"
        from embertide.engine.ring import unusual
        assert unusual(o.store, "p2", T0, T0 + 8 * 3600) == 1.4


def test_a_temperature_just_under_the_threshold_still_counts_as_a_normal_night(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        fake.temp_dev = 0.9
        sync(c)
        ring = c.get("/api/people/p2/night?date=2026-11-04").json()["ring"]
        assert not ring["unusual"]
        from embertide.engine.ring import unusual
        assert unusual(o.store, "p2", T0, T0 + 8 * 3600) is None


def test_disconnecting_forgets_the_sign_in_but_keeps_the_nights_already_stored(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        sync(c)
        assert c.post("/api/oura/me/disconnect", json={"person": "p2"}, headers=H).json() == {"ok": True}
        s = status(c)
        assert not s["connected"] and not s["needs_reconnect"]
        assert len(o.store.samples("wear:p2:hr:Oura", 0, 2e9)) == 80
        r = c.post("/api/oura/me/sync", json={"person": "p2"}, headers=H)
        assert r.status_code == 409 and "isn't connected" in r.text
        assert not o.due("p2", o.c.clock.now_ts() + 86400)


def test_a_person_who_removed_the_app_at_oura_gets_the_reconnect_message_not_silence(tmp_path):
    """Oura answers 401 to the data call and refuses the refresh: the app sees it as signed out."""
    fake = Oura2()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        fake.valid, fake.refresh_ok = set(), set()
        for _ in range(3):
            sync(c, 502)
        s = status(c)
        assert s["needs_reconnect"] and s["connected"]
        calls = len([x for x in fake.calls if x.url.path.startswith("/v2")])
        asyncio.run(o.tick())                                           # the background poll leaves it alone
        assert len([x for x in fake.calls if x.url.path.startswith("/v2")]) == calls


def test_garbage_from_the_provider_never_poisons_the_stored_data(tmp_path):
    fake = Oura2()
    with client(tmp_path) as c:
        o = _connected(c, fake)
        bad = [{"bpm": 0, "timestamp": SLEEP["bedtime_start"]}, {"bpm": 300, "timestamp": SLEEP["bedtime_start"]},
               {"bpm": "x", "timestamp": SLEEP["bedtime_start"]}, {"bpm": None, "timestamp": None},
               {"bpm": 61, "timestamp": "not a time"}]
        fake.hr = bad + HR[:10]
        sync(c)
        vals = [v for _, v in o.store.samples("wear:p2:hr:Oura", 0, 2e9)]
        assert all(25 <= v <= 220 for v in vals) and len(vals) >= 10
        sync(c)


# ---------- webhooks (the generic way in) ----------
def _hook(c, **kw):
    body = {"name": "Ring bridge", "person": "p2", "metric": "hr", "items_path": "readings", "value_path": "bpm", "time_path": "at", **kw}
    return c.post("/api/integrations/hooks", json=body, headers=H).json()


def test_a_webhook_delivered_twice_stores_once(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        h = _hook(c)
        url = urlsplit(h["url"]).path
        body = {"readings": [{"at": T0 + i * 60, "bpm": 60 + i % 4} for i in range(40)]}
        a = c.post(url, json=body).json()
        b = c.post(url, json=body).json()
        assert a["stored"] == 40 and b["stored"] == 0 and b["ok"]
        assert len(c.app.state.cloud.c.store.samples("wear:p2:hr:Ring bridge", 0, 2e9)) == 40


def test_a_webhook_for_an_unknown_hook_or_secret_is_refused_and_stores_nothing(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        h = _hook(c)
        good = urlsplit(h["url"]).path
        for url in ("/api/hooks/h_nope/" + good.rsplit("/", 1)[1], good + "x", "/api/hooks/" + h["id"] + "/"):
            r = c.post(url, json={"readings": [{"at": T0, "bpm": 60}]})
            assert r.status_code in (404, 405), (url, r.status_code)
        assert c.app.state.cloud.c.store.samples("wear:p2:hr:Ring bridge", 0, 2e9) == []


def test_a_webhook_with_the_wrong_shape_says_what_was_wrong_and_stores_nothing(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        url = urlsplit(_hook(c)["url"]).path
        r = c.post(url, json={"something": "else"}).json()
        assert r["ok"] is False and r["stored"] == 0 and "no readings found" in r["error"]
        r = c.post(url, json={"readings": [{"at": T0, "bpm": 0}, {"at": T0 + 60, "bpm": 400}, {"at": T0 + 120, "bpm": "high"}]}).json()
        assert r["stored"] == 0
        assert c.post(url, json={"readings": [{"at": T0 + 180, "bpm": 61}]}).json()["stored"] == 1      # the next good one still works


def test_a_turned_off_webhook_answers_politely(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers=H)
        h = _hook(c)
        c.put(f"/api/integrations/hooks/{h['id']}", json={"name": "Ring bridge", "person": "p2", "metric": "hr", "items_path": "readings",
                                                          "value_path": "bpm", "time_path": "at", "enabled": False}, headers=H)
        r = c.post(urlsplit(h["url"]).path, json={"readings": [{"at": T0, "bpm": 60}]}).json()
        assert r["ok"] is False and "turned off" in r["error"]
