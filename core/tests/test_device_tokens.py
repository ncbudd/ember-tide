"""Device keys for the phone's widget / tile / notification buttons and the watch, single-use live tickets, and whose
address counts behind a trusted proxy."""

import logging
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from embertide import auth as auth_mod
from embertide import auth_api
from embertide.__main__ import RedactTokens
from embertide.api import for_viewer
from embertide.app import create_app
from embertide.auth_api import client_ip, from_home
from embertide.config import Config

H = {"X-Embertide": "1"}
APP = {"X-Embertide": "1", "X-Embertide-Client": "app"}
PW = "correct horse battery"
OUT = "93.184.216.34"


def cfg_for(tmp_path, trust_proxy=False):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, True
    cfg.trust_proxy = trust_proxy
    return cfg


def auth_of(app):
    return next(m for m in app.user_middleware if m.cls.__name__ == "AuthMiddleware").kwargs["state"].auth


def session_for(c, username):
    r = TestClient(c.app).post("/api/auth/login", json={"username": username, "password": PW}, headers=APP)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", **H}


@pytest.fixture
def home(tmp_path):
    """Owner alex on p1, sleeper sam on p2, viewer vic (side p2), each with an app session."""
    with TestClient(create_app(cfg_for(tmp_path))) as c:
        assert c.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": "p1"}).status_code == 200
        for name, person, role in (("sam", "p2", "sleeper"), ("vic", "p2", "viewer")):
            code = c.post("/api/auth/invites", json={"person": person, "role": role}, headers=H).json()["code"]
            assert TestClient(c.app).post("/api/auth/accept", json={"code": code, "username": name, "password": PW}).status_code == 200
        yield c, {n: session_for(c, n) for n in ("alex", "sam", "vic")}


def mint(c, sess, scope="phone", label=""):
    r = TestClient(c.app).post("/api/auth/me/device-tokens", json={"scope": scope, "label": label}, headers=sess)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["token"].startswith("et_d_")
    return {"Authorization": f"Bearer {j['token']}"}, j


# ───────────── device keys ─────────────

def test_device_key_does_exactly_its_own_side_calls(home):
    c, s = home
    k, row = mint(c, s["sam"], "phone", "Phone widget")
    assert row["this_device"] and row["scope"] == "phone" and row["person"] == "p2"
    bare = TestClient(c.app)
    assert bare.get("/api/snapshot", headers=k).status_code == 200
    ok = [("/api/people/p2/flash", {"kind": "hot", "source": "widget"}), ("/api/people/p2/flash/extend?source=widget", None),
          ("/api/people/p2/flash/stop?source=widget", None), ("/api/people/p2/adjust", {"temp_c": 30, "source": "widget"}),
          ("/api/people/p2/adjust/clear?source=widget", None), ("/api/people/p2/tonight", {"action": "resume", "source": "widget"}),
          ("/api/people/p2/feedback", {"temp": "right"})]
    for path, body in ok:
        r = bare.post(path, json=body, headers=k) if body is not None else bare.post(path, headers=k)
        assert r.status_code not in (401, 403), (path, r.status_code, r.text)
    # notification answers reach the route (no such question → the route's own 404/422, not a 403)
    assert bare.post("/api/people/p2/away-check", json={"night": "2026-01-01", "answer": "home"}, headers=k).status_code not in (401, 403)
    assert bare.post("/api/people/p2/pet-question/abc123", json={"answer": "me"}, headers=k).status_code not in (401, 403)


def test_device_key_forbidden_routes(home):
    c, s = home
    for who in ("alex", "sam"):               # the owner's own key has no owner powers
        k, _ = mint(c, s[who], "phone")
        pid = "p1" if who == "alex" else "p2"
        bare = TestClient(c.app)
        forbidden = [("GET", "/api/auth/me/sessions"), ("GET", "/api/auth/me/device-tokens"), ("POST", "/api/auth/me/device-tokens"),
                     ("POST", "/api/auth/me/password"), ("GET", "/api/auth/users"), ("GET", "/api/auth/tokens"),
                     ("POST", "/api/auth/tokens"), ("POST", "/api/auth/invites"), ("GET", "/api/sources"), ("GET", "/api/household"),
                     ("PUT", f"/api/people/{pid}/settings"), ("GET", f"/api/people/{pid}/nights"), ("POST", f"/api/people/{pid}/context"),
                     ("POST", f"/api/people/{pid}/watch-hr"), ("POST", f"/api/people/{pid}/wearable"), ("POST", f"/api/people/{pid}/away"),
                     ("POST", "/api/console"), ("GET", "/api/notify/me"), ("POST", "/api/notify/me/push"), ("POST", "/api/live/ticket"),
                     ("POST", "/api/assistant/ask"), ("GET", "/api/studio/overview"), ("POST", "/api/household/swap-sides"),
                     ("POST", "/api/guest/left/start"), ("GET", "/api/autopilot")]
        for m, path in forbidden:
            r = bare.request(m, path, json={}, headers=k)
            assert r.status_code == 403, (who, m, path, r.status_code)
        with pytest.raises(WebSocketDisconnect) as e:
            with bare.websocket_connect("/api/live", headers=k) as ws:
                ws.receive_text()
        assert e.value.code == 4403


def test_device_key_cant_touch_the_other_side(home):
    c, s = home
    bare = TestClient(c.app)
    for who, other in (("sam", "p1"), ("alex", "p2")):
        k, _ = mint(c, s[who], "phone")
        assert bare.post(f"/api/people/{other}/flash", json={"kind": "hot"}, headers=k).status_code == 403
        assert bare.post(f"/api/people/{other}/tonight", json={"action": "skip"}, headers=k).status_code == 403
        assert bare.post(f"/api/people/{other}/adjust/clear", headers=k).status_code == 403


def test_watch_scope_is_narrower(home):
    c, s = home
    k, _ = mint(c, s["sam"], "watch", "Watch")
    bare = TestClient(c.app)
    assert bare.post("/api/people/p2/flash", json={"kind": "hot", "source": "watch"}, headers=k).status_code == 200
    assert bare.post("/api/people/p2/feedback", json={"temp": "right"}, headers=k).status_code == 403
    assert bare.post("/api/people/p2/pet-question/abc", json={"answer": "me"}, headers=k).status_code == 403


def test_viewer_device_key_can_only_look(home):
    c, s = home
    k, _ = mint(c, s["vic"], "watch")
    bare = TestClient(c.app)
    assert bare.get("/api/snapshot", headers=k).status_code == 200
    assert bare.post("/api/people/p2/flash", json={"kind": "hot"}, headers=k).status_code == 403


def test_device_key_snapshot_is_redacted_like_the_session(home):
    c, s = home
    bare = TestClient(c.app)
    for who in ("sam", "alex"):
        k, _ = mint(c, s[who], "phone")
        a = bare.get("/api/snapshot", headers=k).json()
        b = bare.get("/api/snapshot", headers=s[who]).json()
        for x in (a, b):
            x.pop("now", None)
        assert [p["presence"] for p in a["people"]] == [p["presence"] for p in b["people"]]
        assert [p["night"].get("context") for p in a["people"]] == [p["night"].get("context") for p in b["people"]]
    # the partner's key hides the owner's private bits, exactly as the partner's session does
    k, _ = mint(c, s["sam"], "phone")
    snap = bare.get("/api/snapshot", headers=k).json()
    owner_side = next(p for p in snap["people"] if p["id"] == "p1")
    assert owner_side["night"]["context"] is None and owner_side["presence"].get("hr") is None


def test_revoked_and_signed_out_keys_stop_working(home):
    c, s = home
    bare = TestClient(c.app)
    k, row = mint(c, s["sam"], "phone")
    assert [t["id"] for t in bare.get("/api/auth/me/device-tokens", headers=s["sam"]).json()] == [row["id"]]
    # someone else can't revoke it from their account; its person can
    assert bare.delete(f"/api/auth/me/device-tokens/{row['id']}", headers=s["alex"]).json()["ok"] is False
    assert bare.delete(f"/api/auth/me/device-tokens/{row['id']}", headers=s["sam"]).json()["ok"] is True
    assert bare.get("/api/snapshot", headers=k).status_code == 401
    # the owner sees and revokes anyone's
    k2, row2 = mint(c, s["sam"], "watch")
    listed = bare.get("/api/auth/tokens/devices", headers=s["alex"]).json()
    assert [t["id"] for t in listed] == [row2["id"]] and listed[0]["username"] == "sam"
    assert row2["id"] not in [t["id"] for t in bare.get("/api/auth/tokens", headers=s["alex"]).json()]   # not an API key
    assert bare.get("/api/auth/tokens/devices", headers=s["sam"]).status_code == 403
    assert bare.delete(f"/api/auth/tokens/{row2['id']}", headers=s["alex"]).json()["ok"] is True
    assert bare.get("/api/snapshot", headers=k2).status_code == 401
    # signing out of the phone that made a key ends the key
    k3, _ = mint(c, s["sam"], "watch")
    assert bare.get("/api/snapshot", headers=k3).status_code == 200
    assert bare.post("/api/auth/logout", headers=s["sam"]).status_code == 200
    assert bare.get("/api/snapshot", headers=k3).status_code == 401


def test_reminting_replaces_and_password_change_ends_other_phones_keys(home):
    c, s = home
    bare = TestClient(c.app)
    k1, _ = mint(c, s["sam"], "watch")
    k2, _ = mint(c, s["sam"], "watch")
    assert bare.get("/api/snapshot", headers=k1).status_code == 401           # replaced
    assert bare.get("/api/snapshot", headers=k2).status_code == 200
    other = session_for(c, "sam")
    k3, _ = mint(c, other, "phone")
    assert bare.post("/api/auth/me/password", json={"current": PW, "new": "another long password"}, headers=s["sam"]).status_code == 200
    assert bare.get("/api/snapshot", headers=k3).status_code == 401           # that phone was signed out
    assert bare.get("/api/snapshot", headers=k2).status_code == 200           # this one wasn't


def test_moving_the_account_to_another_side_ends_its_keys(home):
    c, s = home
    bare = TestClient(c.app)
    k, _ = mint(c, s["sam"], "phone")
    uid = next(u["id"] for u in bare.get("/api/auth/users", headers=s["alex"]).json() if u["username"] == "sam")
    bare.patch(f"/api/auth/users/{uid}", json={"person": "p1", "set_person": True}, headers=s["alex"])
    assert bare.get("/api/snapshot", headers=k).status_code == 401


def test_only_a_signed_in_account_can_mint(home):
    c, s = home
    bare = TestClient(c.app)
    api_key = bare.post("/api/auth/tokens", json={"name": "HA", "role": "owner"}, headers=s["alex"]).json()["token"]
    assert bare.post("/api/auth/me/device-tokens", json={"scope": "phone"}, headers={"Authorization": f"Bearer {api_key}"}).status_code == 403
    assert bare.post("/api/auth/me/device-tokens", json={"scope": "studio"}, headers=s["sam"]).status_code == 422
    assert bare.post("/api/auth/me/device-tokens", json={"scope": "phone"}).status_code == 401
    # a cookie session needs the app header, like every change made with a cookie
    cookie = TestClient(c.app)
    cookie.post("/api/auth/login", json={"username": "sam", "password": PW})
    assert cookie.post("/api/auth/me/device-tokens", json={"scope": "phone"}).status_code == 403
    assert cookie.post("/api/auth/me/device-tokens", json={"scope": "phone"}, headers=H).status_code == 200


def test_old_api_keys_still_work(home):
    c, s = home
    bare = TestClient(c.app)
    key = bare.post("/api/auth/tokens", json={"name": "HA", "role": "owner"}, headers=s["alex"]).json()["token"]
    assert not key.startswith("et_d_")
    assert bare.get("/api/auth/users", headers={"Authorization": f"Bearer {key}"}).status_code == 200


# ───────────── live tickets ─────────────

def ticket(c, sess):
    r = TestClient(c.app).post("/api/live/ticket", headers=sess)
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def test_ticket_opens_the_feed_once(home):
    c, s = home
    t = ticket(c, s["sam"])
    bare = TestClient(c.app)
    with bare.websocket_connect(f"/api/live?ticket={t}", headers={"origin": "https://localhost"}) as ws:
        assert "people" in ws.receive_text()
    with pytest.raises(WebSocketDisconnect) as e:
        with bare.websocket_connect(f"/api/live?ticket={t}") as ws:
            ws.receive_text()
    assert e.value.code == 4400                                     # not 4401: the app fetches a new ticket


def test_ticket_expires(home, monkeypatch):
    c, s = home
    t = ticket(c, s["sam"])
    real = time.time
    monkeypatch.setattr(auth_mod.time, "time", lambda: real() + auth_mod.TICKET_S + 1)
    with pytest.raises(WebSocketDisconnect) as e:
        with TestClient(c.app).websocket_connect(f"/api/live?ticket={t}") as ws:
            ws.receive_text()
    assert e.value.code == 4400


def test_ticket_dies_with_its_session_and_is_bound_to_its_account(home):
    c, s = home
    a = auth_of(c.app)
    t = ticket(c, s["sam"])
    p = a.take_ticket(t)
    assert p is not None and p.name == "sam" and p.person == "p2" and p.role == "sleeper"   # sam's, nobody else's
    t2 = ticket(c, s["sam"])
    TestClient(c.app).post("/api/auth/logout", headers=s["sam"])
    assert a.take_ticket(t2) is None
    assert a.take_ticket("made-up") is None


def test_ticket_stream_matches_the_session_stream(home):
    c, s = home
    bare = TestClient(c.app)
    for who in ("sam", "vic", "alex"):
        t = ticket(c, s[who])
        with bare.websocket_connect(f"/api/live?ticket={t}") as ws:
            via_ticket = ws.receive_json()
        with bare.websocket_connect("/api/live", headers={"Authorization": s[who]["Authorization"]}) as ws:
            via_session = ws.receive_json()
        def private(snap):                                   # what redaction decides (the rest can move between ticks)
            return [(p["id"], {k: v is None for k, v in (p["presence"] or {}).items()},
                     *(p["night"].get(k) is None for k in ("context", "home", "away_check"))) for p in snap["people"]]
        assert private(via_ticket) == private(via_session), who
        p = auth_of(c.app).session_principal(s[who]["Authorization"][7:])
        assert private(via_ticket) == private(for_viewer(via_ticket, p))


def test_ticket_needs_sign_in_and_a_device_key_cant_get_one(home):
    c, s = home
    bare = TestClient(c.app)
    assert bare.post("/api/live/ticket").status_code == 401
    k, _ = mint(c, s["sam"], "phone")
    assert bare.post("/api/live/ticket", headers=k).status_code == 403
    cookie = TestClient(c.app)
    cookie.post("/api/auth/login", json={"username": "vic", "password": PW})
    assert cookie.post("/api/live/ticket").status_code == 403         # cookie writes need the app header
    assert cookie.post("/api/live/ticket", headers=H).status_code == 200    # a viewer may watch the feed


def test_ticket_keeps_the_origin_check(home):
    c, s = home
    t = ticket(c, s["sam"])
    with pytest.raises(WebSocketDisconnect) as e:
        with TestClient(c.app).websocket_connect(f"/api/live?ticket={t}", headers={"origin": "https://evil.example.com"}) as ws:
            ws.receive_text()
    assert e.value.code == 4403


def test_legacy_token_query_still_works_and_can_be_switched_off(home, monkeypatch):
    c, s = home
    tok = s["sam"]["Authorization"][7:]
    with TestClient(c.app).websocket_connect(f"/api/live?token={tok}") as ws:
        assert "people" in ws.receive_text()
    monkeypatch.setattr(auth_api, "LEGACY_LIVE_TOKEN_QUERY", False)
    with pytest.raises(WebSocketDisconnect) as e:
        with TestClient(c.app).websocket_connect(f"/api/live?token={tok}") as ws:
            ws.receive_text()
    assert e.value.code == 4401


def test_tickets_are_redacted_from_logs():
    r = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, '%s - "WebSocket %s" [accepted]',
                          ("1.2.3.4:5", "/api/live?ticket=Zx9_-abc&x=1"), None)
    assert RedactTokens().filter(r)
    assert "Zx9_-abc" not in r.getMessage() and "ticket=***&x=1" in r.getMessage()


# ───────────── whose address counts behind a trusted proxy ─────────────

TUN = "192.168.1.30"                     # cloudflared on the home network, connecting over plain http


def test_cf_connecting_ip_wins_only_when_cloudflare_enabled():
    h = {"cf-connecting-ip": OUT, "x-forwarded-for": "10.0.0.9", "x-real-ip": "10.0.0.8", "forwarded": "for=10.0.0.7"}
    assert client_ip(h, TUN, True, cloudflare=True) == OUT
    # Default (cloudflare=False): CF-Connecting-IP is ignored; rightmost untrusted X-Forwarded-For wins
    assert client_ip({"cf-connecting-ip": "8.8.8.8", "x-forwarded-for": OUT}, TUN, True) == OUT


def test_spoofed_private_forwarding_headers_through_cloudflare_dont_make_you_home():
    # Cloudflare overwrites CF-Connecting-IP, appends the real address to X-Forwarded-For and passes the rest through
    for extra in ({"x-forwarded-for": f"192.168.1.5, {OUT}"}, {"forwarded": "for=192.168.1.5"}, {"x-real-ip": "192.168.1.5"},
                  {"x-forwarded-for": "127.0.0.1"}):
        h = {"cf-connecting-ip": OUT, **extra}
        assert client_ip(h, TUN, True, cloudflare=True) == OUT, extra
        assert not from_home(h, TUN, True), extra


def test_a_forwarded_private_claim_is_never_home():
    # even if a header says 192.168.x, through a proxy it isn't "home" (first-time setup, the global sign-in bucket)
    for h in ({"cf-connecting-ip": "192.168.1.50"}, {"x-forwarded-for": "192.168.1.50"}, {"forwarded": "for=192.168.1.9:1234"},
              {"x-real-ip": "10.0.0.2"}):
        assert not from_home(h, TUN, True), h
        assert not from_home(h, TUN, False), h
    assert from_home({}, "192.168.1.50", True) and from_home({}, "192.168.1.50", False)


def test_xff_takes_the_rightmost_untrusted_hop():
    assert client_ip({"x-forwarded-for": f"1.1.1.1, {OUT}"}, TUN, True) == OUT               # the visitor wrote 1.1.1.1
    assert client_ip({"x-forwarded-for": f"1.1.1.1, {OUT}, 10.0.0.3"}, TUN, True) == OUT     # past our own LAN proxy
    assert client_ip({"x-forwarded-for": f"{OUT}, garbage"}, TUN, True) == TUN               # unreadable hop: not believed
    assert client_ip({"forwarded": f'for=1.1.1.1, for="[2606:4700::1111]:443"'}, TUN, True) == "2606:4700::1111"
    assert client_ip({"x-real-ip": OUT}, TUN, True) == OUT


def test_headers_from_a_public_peer_are_ignored():
    # straight from the internet (a port forward): forwarding headers are the visitor's own words
    for h in ({"cf-connecting-ip": "8.8.8.8"}, {"x-forwarded-for": "8.8.8.8"}, {"forwarded": "for=8.8.8.8"}, {"x-real-ip": "8.8.8.8"}):
        assert client_ip(h, OUT, True, cloudflare=True) == OUT, h
        assert not from_home(h, OUT, True)


def test_trust_proxy_off_ignores_headers():
    assert client_ip({"cf-connecting-ip": "8.8.8.8", "x-forwarded-for": "8.8.4.4"}, TUN, False, cloudflare=True) == TUN


def test_spoofed_header_cant_set_up_the_owner_through_the_tunnel(tmp_path):
    cfg = cfg_for(tmp_path, trust_proxy=True)
    cfg.proxy_cloudflare = True
    with TestClient(create_app(cfg), client=(TUN, 40000)) as c:
        for h in ({"CF-Connecting-IP": OUT, "X-Forwarded-For": "192.168.1.5"}, {"CF-Connecting-IP": "192.168.1.5"},
                  {"X-Forwarded-For": "192.168.1.5"}, {"Forwarded": "for=192.168.1.5"}, {"X-Real-IP": "192.168.1.5"}):
            assert c.post("/api/auth/setup", json={"username": "mallory", "password": PW}, headers=h).status_code == 403, h
        assert c.get("/api/auth/state").json()["setup_needed"]
        assert c.post("/api/auth/setup", json={"username": "alex", "password": PW}).status_code == 200   # at home, directly


def test_spoofed_private_header_doesnt_skip_the_global_bucket(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_api, "_SLOWED", auth_api.SLOW_MAX)          # slowed tries piled up: outsiders are refused
    cfg = cfg_for(tmp_path, trust_proxy=True)
    cfg.proxy_cloudflare = True
    with TestClient(create_app(cfg), client=(TUN, 40000)) as c:
        assert c.post("/api/auth/setup", json={"username": "alex", "password": PW}).status_code == 200
        now = time.time()
        for _ in range(60):
            auth_of(c.app).failed("all", now)
        for h in ({"CF-Connecting-IP": OUT, "X-Forwarded-For": "192.168.1.5"}, {"X-Forwarded-For": "192.168.1.5"},
                  {"CF-Connecting-IP": "10.0.0.4"}):
            assert c.post("/api/auth/login", json={"username": "alex", "password": PW}, headers=h).status_code == 429, h
        assert c.post("/api/auth/login", json={"username": "alex", "password": PW}).status_code == 200   # at home, directly


def test_per_address_limit_follows_the_real_visitor_not_a_spoofed_xff(tmp_path):
    cfg = cfg_for(tmp_path, trust_proxy=True)
    cfg.proxy_cloudflare = True
    with TestClient(create_app(cfg), client=(TUN, 40000)) as c:
        assert c.post("/api/auth/setup", json={"username": "alex", "password": PW}).status_code == 200
        codes = []
        for i in range(22):                 # a new made-up X-Forwarded-For each try, same real address from Cloudflare
            codes.append(c.post("/api/auth/login", json={"username": f"user{i}", "password": "wrong password 1"},
                                headers={"CF-Connecting-IP": OUT, "X-Forwarded-For": f"203.0.113.{i}, {OUT}"}).status_code)
        assert codes[-1] == 429
