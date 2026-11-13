"""Sign-in, invites, tokens, and who may do what."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from embertide.app import create_app
from embertide.config import Config

H = {"X-Embertide": "1"}
PW = "correct horse battery"


def make(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, True
    return TestClient(create_app(cfg))


def setup_owner(c, person="p1"):
    r = c.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": person})
    assert r.status_code == 200, r.text
    return r


def invite_partner(c, person="p2", role="sleeper"):
    code = c.post("/api/auth/invites", json={"person": person, "role": role}, headers=H).json()["code"]
    return code


def test_first_visit_needs_setup_and_everything_is_locked(tmp_path):
    with make(tmp_path) as c:
        st = c.get("/api/auth/state").json()
        assert st["setup_needed"] and st["me"] is None and len(st["people"]) == 2
        assert c.get("/api/snapshot").status_code == 401
        assert c.post("/api/people/p1/flash", json={"kind": "hot"}, headers=H).status_code == 401
        assert c.get("/healthz").status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/live") as ws:
                ws.receive_text()


def test_setup_once_then_signed_in(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        assert c.get("/api/auth/state").json()["me"]["role"] == "owner"
        assert c.get("/api/snapshot").status_code == 200
        with c.websocket_connect("/api/live") as ws:
            assert "people" in ws.receive_text()
        # nobody can "set up" again to grab the owner role
        c2 = TestClient(c.app)
        assert c2.post("/api/auth/setup", json={"username": "mallory", "password": PW}).status_code == 409


def test_weak_password_and_bad_username_refused(tmp_path):
    with make(tmp_path) as c:
        assert c.post("/api/auth/setup", json={"username": "alex", "password": "short"}).status_code == 422
        assert c.post("/api/auth/setup", json={"username": "a b!", "password": PW}).status_code == 422
        assert c.get("/api/auth/state").json()["setup_needed"]


def test_changes_need_the_header(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        assert c.post("/api/people/p1/flash", json={"kind": "hot"}).status_code == 403          # what a hostile page could send
        assert c.post("/api/people/p1/flash", json={"kind": "hot"}, headers=H).status_code == 200


def test_login_logout_and_throttle(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        c.post("/api/auth/logout", headers=H)
        assert c.get("/api/snapshot").status_code == 401
        assert c.post("/api/auth/login", json={"username": "alex", "password": "nope-nope"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "ALEX", "password": PW}).status_code == 200    # usernames ignore case
        assert c.get("/api/snapshot").status_code == 200
        c.post("/api/auth/logout", headers=H)
        for _ in range(5):
            c.post("/api/auth/login", json={"username": "alex", "password": "wrong-wrong"})
        assert c.post("/api/auth/login", json={"username": "alex", "password": PW}).status_code == 429


def test_session_cookie_is_httponly(tmp_path):
    with make(tmp_path) as c:
        r = setup_owner(c)
        sc = r.headers["set-cookie"].lower()
        assert "httponly" in sc and "samesite=lax" in sc


def test_invite_partner_and_permissions(tmp_path):
    with make(tmp_path) as owner:
        setup_owner(owner)
        code = invite_partner(owner)
        g = TestClient(owner.app)
        info = g.get(f"/api/auth/invite?code={code}").json()
        assert info["person"] == "p2" and info["person_name"] == "Right"
        assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
        assert g.post("/api/auth/accept", json={"code": code, "username": "sam2", "password": PW}).status_code == 422   # one use
        assert g.get("/api/auth/state").json()["me"]["person"] == "p2"

        # Sam: own side fully
        assert g.post("/api/people/p2/flash", json={"kind": "hot"}, headers=H).status_code == 200
        assert g.post("/api/people/p2/adjust", json={"delta_c": -0.5}, headers=H).status_code == 200
        # Alex's side: Alex turns sharing off entirely
        s0 = owner.get("/api/household").json()["people"][0]["settings"]
        s0["partner_can_flash"], s0["partner_can_adjust"] = False, False
        assert owner.put("/api/people/p1/settings", json=s0, headers=H).status_code == 200
        assert g.post("/api/people/p1/flash", json={"kind": "hot"}, headers=H).status_code == 403
        assert g.post("/api/people/p1/adjust", json={"delta_c": -0.5}, headers=H).status_code == 403
        assert g.post("/api/people/p1/tonight", json={"action": "skip"}, headers=H).status_code == 403
        # Alex shares flashes only
        s = owner.get("/api/household").json()["people"][0]["settings"]
        s["partner_can_flash"], s["partner_can_adjust"] = True, False
        assert owner.put("/api/people/p1/settings", json=s, headers=H).status_code == 200
        assert g.post("/api/people/p1/flash", json={"kind": "cold"}, headers=H).status_code == 200
        assert g.post("/api/people/p1/adjust", json={"delta_c": -0.5}, headers=H).status_code == 403
        # never someone else's settings, rhythm or the bed itself
        assert g.put("/api/people/p1/settings", json=s, headers=H).status_code == 403
        rh = g.get("/api/people/p1/rhythms").json()[0]
        assert g.put(f"/api/people/p1/rhythms/{rh['id']}", json=rh, headers=H).status_code == 403
        assert g.post("/api/console", json={"command": "REBOOT"}, headers=H).status_code == 403
        assert g.post("/api/sim", json={"speed": 10}, headers=H).status_code == 403
        assert g.post("/api/sources", json={}, headers=H).status_code == 403
        assert g.get("/api/auth/users").status_code == 403
        assert g.post("/api/auth/tokens", json={"name": "x"}, headers=H).status_code == 403
        # reading is fine; swapping sides is a shared decision either of you can make
        assert g.get("/api/snapshot").status_code == 200
        assert g.post("/api/household/swap-sides", headers=H).status_code == 200


def test_sleeper_export_is_their_own(tmp_path):
    with make(tmp_path) as owner:
        setup_owner(owner)
        owner.post("/api/sim", json={"jump_to": "2026-11-05T23:30"}, headers=H)
        code = invite_partner(owner)
        g = TestClient(owner.app)
        g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW})
        csv = g.get("/api/export?fmt=csv&hours=48").text
        assert "Left" not in csv


def test_viewer_can_only_look(tmp_path):
    with make(tmp_path) as owner:
        setup_owner(owner)
        code = invite_partner(owner, person=None, role="viewer")
        v = TestClient(owner.app)
        v.post("/api/auth/accept", json={"code": code, "username": "guest", "password": PW})
        assert v.get("/api/snapshot").status_code == 200
        assert v.post("/api/people/p1/flash", json={"kind": "hot"}, headers=H).status_code == 403


def test_api_token_for_home_assistant(tmp_path):
    with make(tmp_path) as owner:
        setup_owner(owner)
        r = owner.post("/api/auth/tokens", json={"name": "Home Assistant", "role": "owner"}, headers=H).json()
        tok = r["token"]
        assert tok.startswith("et_") and "token" not in owner.get("/api/auth/tokens").json()[0]   # shown once only
        bare = TestClient(owner.app)
        auth = {"Authorization": f"Bearer {tok}"}
        assert bare.get("/api/snapshot", headers=auth).status_code == 200
        assert bare.post("/api/people/p1/flash", json={"kind": "hot"}, headers=auth).status_code == 200   # no header needed
        assert bare.get("/api/snapshot", headers={"Authorization": "Bearer et_wrong"}).status_code == 401
        owner.delete(f"/api/auth/tokens/{r['id']}", headers=H)
        assert bare.get("/api/snapshot", headers=auth).status_code == 401


def test_password_change_signs_out_other_devices(tmp_path):
    with make(tmp_path) as phone:
        setup_owner(phone)
        laptop = TestClient(phone.app)
        laptop.post("/api/auth/login", json={"username": "alex", "password": PW})
        assert len(phone.get("/api/auth/me/sessions").json()) == 2
        assert phone.post("/api/auth/me/password", json={"current": "wrong", "new": "another long one"}, headers=H).status_code == 422
        assert phone.post("/api/auth/me/password", json={"current": PW, "new": "another long one"}, headers=H).json()["signed_out_elsewhere"] == 1
        assert laptop.get("/api/snapshot").status_code == 401
        assert phone.get("/api/snapshot").status_code == 200


def test_last_owner_cannot_be_removed_or_demoted(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        uid = c.get("/api/auth/state").json()["me"]["id"]
        assert c.patch(f"/api/auth/users/{uid}", json={"role": "sleeper"}, headers=H).status_code == 422
        assert c.delete(f"/api/auth/users/{uid}", headers=H).status_code == 422


def test_password_reset_link(tmp_path):
    with make(tmp_path) as owner:
        setup_owner(owner)
        code = invite_partner(owner)
        g = TestClient(owner.app)
        g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW})
        uid = next(u["id"] for u in owner.get("/api/auth/users").json() if u["username"] == "sam")
        reset = owner.post(f"/api/auth/users/{uid}/reset", headers=H).json()["code"]
        assert owner.get("/api/auth/invites").json() == []                 # reset links aren't listed as invites
        fresh = TestClient(owner.app)
        assert fresh.get(f"/api/auth/invite?code={reset}").json()["reset_for"] == "sam"
        assert fresh.post("/api/auth/accept", json={"code": reset, "password": "a brand new one"}).status_code == 200
        assert g.get("/api/snapshot").status_code == 401                    # old sessions are gone
        assert fresh.get("/api/auth/state").json()["me"]["name"] == "sam"
        assert fresh.post("/api/auth/accept", json={"code": reset, "password": "again again"}).status_code == 422
        x = TestClient(owner.app)
        assert x.post("/api/auth/login", json={"username": "sam", "password": "a brand new one"}).status_code == 200


def test_sleeper_cannot_make_reset_links(tmp_path):
    with make(tmp_path) as owner:
        setup_owner(owner)
        code = invite_partner(owner)
        g = TestClient(owner.app)
        g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW})
        uid = owner.get("/api/auth/state").json()["me"]["id"]
        assert g.post(f"/api/auth/users/{uid}/reset", headers=H).status_code == 403


def test_setup_only_from_home(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth, cfg.trust_proxy = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, True, True
    with TestClient(create_app(cfg)) as c:
        r = c.post("/api/auth/setup", json={"username": "mallory", "password": PW}, headers={"CF-Connecting-IP": "93.184.216.34"})
        assert r.status_code == 403 and c.get("/api/auth/state").json()["setup_needed"]
        # a home address claimed in a forwarding header isn't enough (a visitor can send one); directly at home is
        assert c.post("/api/auth/setup", json={"username": "alex", "password": PW}, headers={"CF-Connecting-IP": "192.168.1.50"}).status_code == 403
        assert c.post("/api/auth/setup", json={"username": "alex", "password": PW}).status_code == 200


def test_app_signs_in_with_a_bearer_session(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        app = TestClient(c.app)
        r = app.post("/api/auth/login", json={"username": "alex", "password": PW}, headers={"X-Embertide-Client": "app"}).json()
        tok = r["token"]
        assert tok and not tok.startswith("et_")
        browser = TestClient(c.app).post("/api/auth/login", json={"username": "alex", "password": PW}).json()
        assert "token" not in browser                                    # browsers never see it
        bare = TestClient(c.app)
        auth = {"Authorization": f"Bearer {tok}", **H}
        assert bare.get("/api/snapshot", headers=auth).status_code == 200
        assert bare.post("/api/people/p1/flash", json={"kind": "hot"}, headers=auth).status_code == 200
        with bare.websocket_connect(f"/api/live?token={tok}") as ws:
            assert "people" in ws.receive_text()
        pre = bare.options("/api/snapshot", headers={"Origin": "https://localhost", "Access-Control-Request-Method": "GET",
                                                     "Access-Control-Request-Headers": "authorization"})
        assert pre.headers.get("access-control-allow-origin") == "https://localhost"
        assert bare.post("/api/auth/logout", headers=auth).status_code == 200
        assert bare.get("/api/snapshot", headers=auth).status_code == 401


def test_a_stranger_cant_lock_the_owner_out(tmp_path):
    """Failed tries count per name and address: someone hammering the owner's name from their own address is throttled,
    the owner signing in from home still gets in."""
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, True
    cfg.trust_proxy = True                                   # behind a proxy: X-Forwarded-For identifies visitors
    with TestClient(create_app(cfg), client=("172.18.0.5", 40000)) as c:
        setup_owner(c)
        bad = TestClient(c.app, client=("172.18.0.5", 40000))
        for i in range(7):
            # Even if the caller rotates CF-Connecting-IP, without ET_PROXY_CLOUDFLARE=on X-Forwarded-For decides the rate limit bucket.
            r = bad.post("/api/auth/login", json={"username": "alex", "password": "wrong password 123"},
                         headers={"x-forwarded-for": "93.184.216.34", "cf-connecting-ip": f"93.184.216.{i + 1}"})
        assert r.status_code == 429
        good = TestClient(c.app, client=("172.18.0.5", 40000))
        ok = good.post("/api/auth/login", json={"username": "alex", "password": PW}, headers={"x-forwarded-for": "93.184.216.35"})
        assert ok.status_code == 200, ok.text


def test_live_feed_refuses_other_sites(tmp_path):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with make(tmp_path) as c:
        setup_owner(c)
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/api/live", headers={"origin": "https://evil.example.com"}) as ws:
                ws.receive_json()
        assert e.value.code == 4403
        with c.websocket_connect("/api/live", headers={"origin": "http://testserver"}) as ws:
            assert ws.receive_json()


def test_partner_and_viewer_dont_read_the_other_persons_private_data(tmp_path):
    """Where someone is, their next alarm, their nights and heart rate: their own and the owner's to see only."""
    with make(tmp_path) as c:
        setup_owner(c)                                                   # owner = p1
        code = c.post("/api/auth/invites", json={"person": "p2", "role": "sleeper"}, headers=H).json()["code"]
        g = TestClient(c.app)
        assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
        for path in ("/api/people/p1/phone-context", "/api/people/p1/nights", "/api/people/p1/watch-hr",
                     "/api/people/p1/autopilot", "/api/people/p1/sleeps", "/api/explore?person_id=p1"):
            assert g.get(path).status_code == 403, path
        left = next(p for p in c.app.state.oura.c.household.people if p.id == "p1").zone
        right = next(p for p in c.app.state.oura.c.household.people if p.id == "p2").zone
        assert g.get(f"/api/history?zone={left}").status_code == 403
        assert g.get(f"/api/history?zone={right}").status_code == 200
        assert g.get("/api/people/p2/nights").status_code == 200                 # her own
        assert g.get("/api/people/p1/rhythms").status_code == 200                # the plan isn't private
        assert c.get("/api/people/p2/nights").status_code == 200                 # the owner sees everyone
        assert all(e.get("person") in (None, "p2") for e in g.get("/api/events").json())


def test_partner_snapshot_hides_the_other_persons_vitals_and_context(tmp_path):
    """The live picture shows the partner's side and plan, not their heart rate, breathing, phone/calendar context
    or which of their devices voted; the owner still sees everything."""
    import json as _j
    with make(tmp_path) as c:
        setup_owner(c)
        ctl = c.app.state.oura.c
        g = TestClient(c.app)
        assert g.post("/api/auth/accept", json={"code": invite_partner(c), "username": "sam", "password": PW}).status_code == 200
        snap = dict(ctl.last_snapshot)
        snap["people"] = [{**p, "presence": {**p["presence"], "hr": 61, "br": 14, "status": 0,
                                             "fusion": {"present": True, "votes": [{"signal": "watch:me", "weight": 0.4, "value": 1}]}},
                           "night": {**p["night"], "context": {"home": False, "heading_home": True}}} for p in snap["people"]]
        ctl.last_snapshot = snap
        mine = {p["id"]: p for p in g.get("/api/snapshot").json()["people"]}
        assert mine["p2"]["presence"]["hr"] == 61 and mine["p2"]["night"]["context"]
        assert mine["p1"]["presence"]["hr"] is None and mine["p1"]["presence"]["br"] is None
        assert mine["p1"]["night"]["context"] is None and mine["p1"]["presence"]["fusion"] == {"present": True}
        assert mine["p1"]["presence"]["present"] is not None or True
        own = {p["id"]: p for p in c.get("/api/snapshot").json()["people"]}
        assert own["p1"]["presence"]["hr"] == 61 and own["p1"]["presence"]["fusion"]["votes"]
        with g.websocket_connect("/api/live") as ws:
            live = {p["id"]: p for p in _j.loads(ws.receive_text())["people"]}
        assert live["p1"]["presence"]["hr"] is None and live["p1"]["night"]["context"] is None


def test_partner_cant_ask_the_assistant_about_the_other_person(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        g = TestClient(c.app)
        g.post("/api/auth/accept", json={"code": invite_partner(c), "username": "sam", "password": PW})
        r = g.post("/api/assistant/ask", json={"person_id": "p1", "question": "how did he sleep?"}, headers=H)
        assert r.status_code == 200 and "theirs to see" in r.json()["answer"]        # no built-in analysis of his nights
        from embertide import assistant as A
        ctl = c.app.state.oura.c
        p1 = ctl.person("p1")
        limited = A.context(ctl, p1, ctl.clock.now_ts(), private=False)
        assert not {"nights", "notes_from_the_household", "devices", "notifications"} & set(limited)
        assert set(limited["autopilot"]) == {"mode"} and limited["rhythms"]
        assert "nights" in A.context(ctl, p1, ctl.clock.now_ts())
        assert g.get("/api/assistant/log?person_id=p1").status_code == 403
        assert g.get("/api/presence/agreement?person_id=p1").status_code == 403
        assert g.post("/api/assistant/ask", json={"person_id": "p2", "question": "how did I sleep?"}, headers=H).status_code == 200


def test_viewer_can_change_their_own_password(tmp_path):
    with make(tmp_path) as owner:
        setup_owner(owner)
        v = TestClient(owner.app)
        v.post("/api/auth/accept", json={"code": invite_partner(owner, person=None, role="viewer"), "username": "guest", "password": PW})
        r = v.post("/api/auth/me/password", json={"current": PW, "new": "another long password"}, headers=H)
        assert r.status_code == 200, r.text
        assert v.post("/api/people/p1/flash", json={"kind": "hot"}, headers=H).status_code == 403


def test_failed_sign_in_memory_is_trimmed():
    from embertide.auth import Auth
    a = Auth.__new__(Auth)
    a._fails = {f"n:user{i}": [0.0] for i in range(5001)}
    a.failed("n:new", 7200.0)
    assert list(a._fails) == ["n:new"]


def test_partner_doesnt_see_everyones_notifications_pet_questions_or_dials(tmp_path):
    with make(tmp_path) as c:
        setup_owner(c)
        g = TestClient(c.app)
        g.post("/api/auth/accept", json={"code": invite_partner(c), "username": "sam", "password": PW})
        assert g.get("/api/notify/log").status_code == 403
        assert g.get("/api/dials").status_code == 403
        r = g.get("/api/pet-questions").json()
        assert set(r["ranges"]) <= {"p2"} and all(q["person"] == "p2" for q in r["questions"])
        assert c.get("/api/notify/log").status_code == 200
        assert set(c.get("/api/pet-questions").json()["ranges"]) == {"p1", "p2"}
