"""Hardening: first-run setup and the home-network test behind a tunnel, the global sign-in bucket, and one-time links
redeemed once."""

import threading
import time

from fastapi.testclient import TestClient

from embertide import auth_api
from embertide.app import create_app
from embertide.auth_api import from_home, via_untrusted_proxy
from embertide.config import Config

H = {"X-Embertide": "1"}
PW = "correct horse battery"
OUT = "93.184.216.34"                     # a public address (the documentation ranges count as private in Python)
TUNNEL = {"CF-Connecting-IP": OUT, "X-Forwarded-For": OUT, "CF-Ray": "abc"}


def auth_of(app):
    """The app's Auth (it lives on the app's internal state, which the auth middleware holds)."""
    return next(m for m in app.user_middleware if m.cls.__name__ == "AuthMiddleware").kwargs["state"].auth


def cfg_for(tmp_path, trust_proxy=False):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, True
    cfg.trust_proxy = trust_proxy
    return cfg


def setup_owner(c, person="p1"):
    r = c.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": person})
    assert r.status_code == 200, r.text


def add_partner(c, person="p2", role="sleeper"):
    code = c.post("/api/auth/invites", json={"person": person, "role": role}, headers=H).json()["code"]
    g = TestClient(c.app)
    assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
    return g


# ───────────── home network behind a tunnel ─────────────

def test_home_test_ignores_an_untrusted_proxys_private_address():
    assert from_home({}, "192.168.1.50", False)
    assert from_home({}, "127.0.0.1", False)
    assert not from_home({}, "93.184.216.34", False)
    for h in ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "x-real-ip", "forwarded"):
        hdr = {h: f"for={OUT}" if h == "forwarded" else OUT}
        assert via_untrusted_proxy(hdr, False), h
        assert not from_home(hdr, "172.18.0.7", False), h
    # trusted proxy: the visitor's address in the header decides
    assert from_home({"cf-connecting-ip": "192.168.1.50"}, "172.18.0.7", True)
    assert not from_home({"cf-connecting-ip": OUT}, "172.18.0.7", True)
    assert not from_home({"forwarded": 'for="[2606:4700::1111]:4711"'}, "172.18.0.7", True)
    assert from_home({"forwarded": "for=192.168.1.9:1234;proto=https"}, "172.18.0.7", True)


def test_setup_through_the_tunnel_is_refused_without_trust_proxy(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path)), client=("172.18.0.7", 40000)) as c:   # peer = the tunnel's container
        r = c.post("/api/auth/setup", json={"username": "mallory", "password": PW}, headers=TUNNEL)
        assert r.status_code == 403
        assert "home network" in r.json()["detail"] and "ET_TRUST_PROXY" in r.json()["detail"]
        assert c.get("/api/auth/state").json()["setup_needed"]
        # a real device on the home network (no proxy headers) still sets it up
        r = c.post("/api/auth/setup", json={"username": "alex", "password": PW})
        assert r.status_code == 200, r.text


def test_setup_from_a_public_peer_is_refused(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path)), client=("93.184.216.34", 40000)) as c:
        assert c.post("/api/auth/setup", json={"username": "mallory", "password": PW}).status_code == 403


# ───────────── the global sign-in bucket ─────────────

def _fill_global_bucket(app):
    now = time.time()
    for _ in range(60):
        auth_of(app).failed("all", now)


def test_global_bucket_never_refuses_home(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path)), client=("192.168.1.50", 40000)) as c:
        setup_owner(c)
        _fill_global_bucket(c.app)
        t = time.monotonic()
        r = TestClient(c.app, client=("192.168.1.51", 40000)).post("/api/auth/login", json={"username": "alex", "password": PW})
        assert r.status_code == 200, r.text
        assert time.monotonic() - t < auth_api.SLOW_S                      # not even slowed down


def test_global_bucket_slows_outsiders_instead_of_locking_them_out(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_api, "SLOW_S", 0.3)
    with TestClient(create_app(cfg_for(tmp_path, trust_proxy=True))) as c:
        setup_owner(c)
        _fill_global_bucket(c.app)
        t = time.monotonic()
        r = c.post("/api/auth/login", json={"username": "alex", "password": PW}, headers={"cf-connecting-ip": OUT})
        assert r.status_code == 200, r.text
        assert time.monotonic() - t >= 0.3
        # the same outsider through an untrusted tunnel is slowed too (the tunnel's private address isn't "home")
        monkeypatch.setattr(auth_api, "_SLOWED", auth_api.SLOW_MAX)        # a pile-up of slowed tries: refused
        r = c.post("/api/auth/login", json={"username": "alex", "password": PW}, headers={"cf-connecting-ip": "93.184.216.35"})
        assert r.status_code == 429


def test_untrusted_tunnel_counts_as_outside_for_the_global_bucket(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_api, "_SLOWED", auth_api.SLOW_MAX)
    with TestClient(create_app(cfg_for(tmp_path)), client=("172.18.0.7", 40000)) as c:
        c.post("/api/auth/setup", json={"username": "alex", "password": PW})     # from home: no proxy headers
        _fill_global_bucket(c.app)
        assert c.post("/api/auth/login", json={"username": "alex", "password": PW}, headers=TUNNEL).status_code == 429
        assert TestClient(c.app, client=("172.18.0.7", 40000)).post(
            "/api/auth/login", json={"username": "alex", "password": PW}).status_code == 200


# ───────────── one-time links ─────────────

def _race(fn, n=4):
    barrier = threading.Barrier(n)
    out = []

    def go(i):
        barrier.wait()
        try:
            out.append(("ok", fn(i)))
        except ValueError as e:
            out.append(("err", str(e)))
    ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


def test_an_invite_is_redeemed_once_even_at_the_same_moment(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path))) as c:
        setup_owner(c)
        a = auth_of(c.app)
        code = a.new_invite("p2", "sleeper", "x")
        out = _race(lambda i: a.accept_invite(code, f"sam{i}", PW))
        assert [k for k, _ in out].count("ok") == 1, out
        assert len(a.users()) == 2


def test_a_reset_link_is_redeemed_once_even_at_the_same_moment(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path))) as c:
        setup_owner(c)
        a = auth_of(c.app)
        uid = a.users()[0]["id"]
        code = a.new_reset(uid, uid)
        out = _race(lambda i: a.accept_invite(code, "", f"new password {i}"))
        assert [k for k, _ in out].count("ok") == 1, out


def test_a_failed_redeem_lets_the_link_go(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path))) as c:
        setup_owner(c)
        code = c.post("/api/auth/invites", json={"person": "p2", "role": "sleeper"}, headers=H).json()["code"]
        g = TestClient(c.app)
        assert g.post("/api/auth/accept", json={"code": code, "username": "alex", "password": PW}).status_code == 422   # taken
        assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": "short"}).status_code == 422
        assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
        assert TestClient(c.app).post("/api/auth/accept", json={"code": code, "username": "kim", "password": PW}).status_code == 422
