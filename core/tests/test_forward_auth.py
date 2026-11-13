"""Forward-auth header mode (ET_AUTH_HEADER): believed only from a trusted proxy, never creates accounts."""

import pytest
from fastapi.testclient import TestClient

from embertide.app import create_app
from test_auth_hardening import auth_of, cfg_for, setup_owner


def make(tmp_path, header="Remote-User", email="", nets="127.0.0.1", trust=True, peer="127.0.0.1"):
    cfg = cfg_for(tmp_path, trust_proxy=trust)
    cfg.auth_header, cfg.auth_header_email, cfg.trusted_proxies = header, email, nets
    return TestClient(create_app(cfg), client=(peer, 50000))


def test_header_signs_in_existing_account(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        anon = TestClient(c.app, client=("127.0.0.1", 50000))
        assert anon.get("/api/snapshot").status_code == 401
        r = anon.get("/api/snapshot", headers={"Remote-User": "Alex"})          # case-insensitive username
        assert r.status_code == 200
        me = anon.get("/api/auth/state", headers={"Remote-User": "alex"}).json()["me"]
        assert me["name"] == "alex" and me["role"] == "owner"
        # writes still need the app header
        assert anon.post("/api/flash", json={}, headers={"Remote-User": "alex"}).status_code == 403


def test_unknown_user_is_refused_not_created(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        anon = TestClient(c.app, client=("127.0.0.1", 50000))
        r = anon.get("/api/snapshot", headers={"Remote-User": "mallory"})
        assert r.status_code == 403 and "no Ember & Tide account" in r.json()["detail"]
        st = anon.get("/api/auth/state", headers={"Remote-User": "mallory"}).json()
        assert st["me"] is None and st["proxy_denied"]
        assert [u["username"] for u in auth_of(c.app).users()] == ["alex"]
        assert anon.get("/api/snapshot", headers={"Remote-User": "alex"}).status_code == 200


def test_spoofed_header_from_an_untrusted_peer_is_ignored(tmp_path):
    with make(tmp_path, nets="10.0.0.0/8") as c:                 # the test client is not in 10.0.0.0/8
        setup_owner(c)
        anon = TestClient(c.app, client=("127.0.0.1", 50000))
        assert anon.get("/api/snapshot", headers={"Remote-User": "alex"}).status_code == 401
        assert anon.get("/api/auth/state", headers={"Remote-User": "alex"}).json()["me"] is None


def test_off_by_default_and_needs_trust_proxy_and_trusted_proxies(tmp_path):
    with make(tmp_path, header="", nets="") as c:
        setup_owner(c)
        assert TestClient(c.app, client=("127.0.0.1", 50000)).get("/api/snapshot", headers={"Remote-User": "alex"}).status_code == 401
    with pytest.raises(ValueError, match="ET_TRUST_PROXY"):
        make(tmp_path, trust=False)
    with pytest.raises(ValueError, match="ET_TRUSTED_PROXIES"):
        make(tmp_path, nets="")
    with pytest.raises(ValueError, match="ET_TRUSTED_PROXIES"):
        make(tmp_path, header="", email="Remote-Email", nets="   ")


def test_email_header(tmp_path):
    with make(tmp_path, header="", email="X-Forwarded-Email") as c:
        setup_owner(c)
        anon = TestClient(c.app, client=("127.0.0.1", 50000))
        assert anon.get("/api/snapshot", headers={"X-Forwarded-Email": "alex"}).status_code == 200


def test_header_identity_refuses_empty_trusted_proxies(tmp_path):
    from embertide.auth_api import header_identity
    with make(tmp_path) as c:
        setup_owner(c)
        cfg = cfg_for(tmp_path, trust_proxy=True)
        cfg.auth_header, cfg.trusted_proxies = "Remote-User", ""
        assert header_identity(auth_of(c.app), {"remote-user": "alex"}, "127.0.0.1", cfg) is None
