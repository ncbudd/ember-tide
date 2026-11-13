"""The MCP endpoint (/mcp): per-person keys (made once, hashed, revocable), 401 without a good key, read vs change scope,
one key = one person, tool results, and changes that go through the assistant's own validation and logging."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config

H = {"X-Embertide": "1"}
PW = "correct horse battery"
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def cfg_for(tmp_path, auth=True):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, auth
    return cfg


def auth_of(app):
    return next(m for m in app.user_middleware if m.cls.__name__ == "AuthMiddleware").kwargs["state"].auth


def session_for(c, username):
    r = TestClient(c.app).post("/api/auth/login", json={"username": username, "password": PW}, headers={**H, "X-Embertide-Client": "app"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", **H}


@pytest.fixture
def home(tmp_path):
    """Owner alex on p1, sleeper sam on p2, viewer vic (side p2)."""
    with TestClient(create_app(cfg_for(tmp_path))) as c:
        assert c.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": "p1"}).status_code == 200
        for name, person, role in (("sam", "p2", "sleeper"), ("vic", "p2", "viewer")):
            code = c.post("/api/auth/invites", json={"person": person, "role": role}, headers=H).json()["code"]
            assert TestClient(c.app).post("/api/auth/accept", json={"code": code, "username": name, "password": PW}).status_code == 200
        c.post("/api/sim", json={"jump_to": "2026-11-06T23:30", "scripted": False, "presence": {"left": True, "right": True}}, headers=H)
        yield c, {n: session_for(c, n) for n in ("alex", "sam", "vic")}


def make_key(c, sess, scope="read", label="Claude Desktop on laptop"):
    r = TestClient(c.app).post("/api/auth/me/mcp-tokens", json={"label": label, "scope": scope}, headers=sess)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["token"].startswith("et_m_")
    return {"Authorization": f"Bearer {j['token']}", **ACCEPT}, j


_id = [0]


def rpc(c, hdr, method, params=None):
    _id[0] += 1
    return TestClient(c.app).post("/mcp", json={"jsonrpc": "2.0", "id": _id[0], "method": method, "params": params or {}}, headers=hdr)


def call(c, hdr, name, args=None):
    r = rpc(c, hdr, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


# ───────── keys ─────────
def test_key_is_shown_once_hashed_and_revocable(home):
    c, s = home
    hdr, row = make_key(c, s["alex"], "read", "Claude Code")
    tok = hdr["Authorization"][7:]
    assert row["label"] == "Claude Code" and row["scope"] == "read" and row["person"] == "p1" and row["last_used"] is None
    assert "token" not in c.get("/api/auth/me/mcp-tokens", headers=s["alex"]).json()[0]
    stored = auth_of(c.app)._q("SELECT hash FROM tokens WHERE id=?", (row["id"],))[0][0]
    assert tok not in stored and stored == hashlib.sha256(tok.encode()).hexdigest()       # only a hash is kept
    assert rpc(c, hdr, "tools/list").status_code == 200
    assert c.get("/api/auth/me/mcp-tokens", headers=s["alex"]).json()[0]["last_used"] is not None
    # the key is for /mcp only, never an API key
    assert TestClient(c.app).get("/api/snapshot", headers={"Authorization": hdr["Authorization"]}).status_code == 401
    # someone else can't list or revoke it; the key's person can
    assert c.get("/api/auth/me/mcp-tokens", headers=s["sam"]).json() == []
    assert c.delete(f"/api/auth/me/mcp-tokens/{row['id']}", headers=s["sam"]).json() == {"ok": False}
    assert c.delete(f"/api/auth/me/mcp-tokens/{row['id']}", headers=s["alex"]).json() == {"ok": True}
    assert rpc(c, hdr, "tools/list").status_code == 401


def test_unauthenticated_and_bad_keys_get_401(home):
    c, s = home
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    bare = TestClient(c.app)
    assert bare.post("/mcp", json=body, headers=ACCEPT).status_code == 401
    assert bare.post("/mcp", json=body, headers={**ACCEPT, "Authorization": "Bearer et_m_nope"}).status_code == 401
    assert bare.post("/mcp", json=body, headers={**ACCEPT, "Authorization": "Bearer et_nope"}).status_code == 401
    # a session is not an MCP key
    assert bare.post("/mcp", json=body, headers={**ACCEPT, "Authorization": s["alex"]["Authorization"]}).status_code == 401
    assert bare.post("/mcp", json=body, headers=ACCEPT).headers["www-authenticate"].startswith("Bearer")


def test_viewer_cannot_make_a_change_key_and_no_key_from_a_key(home):
    c, s = home
    assert TestClient(c.app).post("/api/auth/me/mcp-tokens", json={"label": "x", "scope": "change"}, headers=s["vic"]).status_code == 403
    hdr, _ = make_key(c, s["vic"], "read")
    assert TestClient(c.app).post("/api/auth/me/mcp-tokens", json={"label": "x"}, headers={"Authorization": hdr["Authorization"], **H}).status_code in (401, 403)


def test_key_dies_with_its_account(home):
    c, s = home
    hdr, _ = make_key(c, s["sam"], "read")
    uid = next(u["id"] for u in c.get("/api/auth/users", headers=H).json() if u["username"] == "sam")
    assert c.delete(f"/api/auth/users/{uid}", headers=H).status_code == 200
    assert rpc(c, hdr, "tools/list").status_code == 401


# ───────── tools ─────────
def test_initialize_lists_tools_and_prompts(home):
    c, s = home
    hdr, _ = make_key(c, s["alex"], "read")
    r = rpc(c, hdr, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "ember-tide"
    names = {t["name"] for t in rpc(c, hdr, "tools/list").json()["result"]["tools"]}
    assert {"get_status", "get_last_night", "get_nights", "get_rhythm", "get_suggestions", "get_ai_review",
            "adjust_tonight", "change_rhythm_phase", "start_flash", "stop_flash", "turn_off_tonight", "set_autopilot_mode",
            "handle_suggestion", "mark_night_stretch", "set_notifications"} <= names
    ps = {p["name"] for p in rpc(c, hdr, "prompts/list").json()["result"]["prompts"]}
    assert "review_my_week" in ps
    got = rpc(c, hdr, "prompts/get", {"name": "review_my_week"}).json()["result"]
    assert "get_nights" in got["messages"][0]["content"]["text"]


def test_status_shape_has_both_units(home):
    c, s = home
    c.post("/api/people/p1/tonight", json={"action": "start_now"}, headers=H)
    hdr, _ = make_key(c, s["alex"], "read")
    res = call(c, hdr, "get_status")
    assert not res.get("isError"), res
    st = res["structuredContent"]
    assert st["person"] and st["bed_online"] is True and "in_bed" in st
    assert st["target"]["c"] is not None and st["target"]["f"] == round(st["target"]["c"] * 9 / 5 + 32)
    assert st["plan_tonight"] and {"phase", "starts", "temperature"} <= set(st["plan_tonight"][0])
    rh = call(c, hdr, "get_rhythm")["structuredContent"]
    assert rh["rhythms"][0]["phases"][0]["temperature"]["c"] is not None
    nights = call(c, hdr, "get_nights", {"start": "2026-11-01", "end": "2026-11-05"})["structuredContent"]["nights"]
    assert [n["date"] for n in nights] == [f"2026-11-0{i}" for i in range(1, 6)]
    too_long = call(c, hdr, "get_nights", {"start": "2026-09-01", "end": "2026-11-05"})
    assert too_long["isError"] and "At most" in too_long["content"][0]["text"]
    assert call(c, hdr, "get_last_night", {"date": "2026-11-05"})["structuredContent"]["date"] == "2026-11-05"
    assert call(c, hdr, "get_ai_review")["structuredContent"]["available"] is False
    assert "suggestions" in call(c, hdr, "get_suggestions")["structuredContent"]


def test_one_key_one_person(home):
    c, s = home
    c.post("/api/people/p1/tonight", json={"action": "start_now"}, headers=H)
    c.post("/api/people/p2/tonight", json={"action": "start_now"}, headers=H)
    a, _ = make_key(c, s["alex"], "read")
    b, _ = make_key(c, s["sam"], "read")
    people = c.get("/api/household").json()["people"]
    assert call(c, a, "get_status")["structuredContent"]["person"] == people[0]["name"]
    assert call(c, b, "get_status")["structuredContent"]["person"] == people[1]["name"]
    # tools take no person at all: an extra argument is refused, not followed
    assert call(c, b, "get_status", {"person_id": "p1"})["isError"]
    # and the routes the change tools use (as that person, never the owner) refuse the partner's private data
    from embertide.auth import Principal, authorize
    sam = Principal("token", "t", "k", "sleeper", "p2")
    assert authorize(sam, "GET", "/api/people/p1/nights", lambda pid: (False, False))
    assert authorize(sam, "GET", "/api/people/p2/nights", lambda pid: (False, False)) is None


# ───────── scope and changes ─────────
def test_read_key_cannot_change(home):
    c, s = home
    c.post("/api/people/p1/tonight", json={"action": "start_now"}, headers=H)
    hdr, _ = make_key(c, s["alex"], "read")
    r = call(c, hdr, "adjust_tonight", {"delta_c": -1.0})
    assert r["isError"] and "read-only" in r["content"][0]["text"]
    assert c.get("/api/assistant/log", params={"person_id": "p1"}).json() == []


def test_change_goes_through_validation_and_is_logged_as_mcp(home):
    c, s = home
    c.post("/api/people/p1/tonight", json={"action": "start_now"}, headers=H)
    hdr, row = make_key(c, s["alex"], "change", "Cursor")
    # out of the assistant's limits: refused by the same schema / validation
    assert call(c, hdr, "adjust_tonight", {"delta_c": 9})["isError"]
    rh = c.get("/api/people/p1/rhythms").json()[0]
    deep = next(p for p in rh["phases"] if p["id"] == "deep")
    big = call(c, hdr, "change_rhythm_phase", {"rhythm_id": rh["id"], "phase_id": "deep", "temp_c": deep["temp_c"] + 4})
    assert big["isError"] and "3 °C" in big["content"][0]["text"]
    assert call(c, hdr, "change_rhythm_phase", {"rhythm_id": rh["id"], "phase_id": "nope", "temp_c": 20})["isError"]
    assert c.get("/api/people/p1/rhythms").json()[0] == rh
    # small changes not switched on: it becomes a card for the person, nothing moves
    before = c.get("/api/snapshot").json()["people"][0]["night"]["override"]
    r = call(c, hdr, "adjust_tonight", {"delta_c": -1.0})["structuredContent"]
    assert r["status"] == "proposed" and "confirm" in r["message"]
    assert c.get("/api/snapshot").json()["people"][0]["night"]["override"] == before
    card = c.get("/api/assistant/log", params={"person_id": "p1"}).json()[0]
    assert card["status"] == "proposed" and card["id"] == r["change_id"]
    assert c.post(f"/api/assistant/proposals/{card['id']}/apply", json={"person_id": "p1"}, headers=s["alex"]).status_code == 200
    # switched on: small and reversible applies right away, recorded with source mcp and the key's label
    assert c.patch("/api/people/p1/settings", json={"assistant_small_changes": True}, headers=H).status_code == 200
    r = call(c, hdr, "start_flash", {"kind": "hot", "minutes": 10})["structuredContent"]
    assert r["status"] == "applied", r
    ev = [e for e in c.get("/api/events", params={"limit": 50}, headers=H).json() if e["kind"] == "assistant_change" and e.get("source") == "mcp"]
    assert ev and ev[0]["client"] == "Cursor"
    assert c.get("/api/snapshot").json()["people"][0]["night"]["flash"]["source"] == "mcp"
    # and the same undo as for Ask works
    assert c.post(f"/api/assistant/changes/{r['change_id']}/undo", json={"person_id": "p1"}, headers=s["alex"]).status_code == 200
    # too many calls in an hour are refused
    import embertide.mcp_server as M
    old = M.CHANGE_CALLS_PER_HOUR
    M.CHANGE_CALLS_PER_HOUR = 2
    try:
        for _ in range(2):
            call(c, hdr, "stop_flash")
        assert "a lot of changes" in call(c, hdr, "stop_flash")["content"][0]["text"]
    finally:
        M.CHANGE_CALLS_PER_HOUR = old


def test_viewer_key_reads_but_never_changes(home):
    c, s = home
    hdr, _ = make_key(c, s["vic"], "read")
    assert call(c, hdr, "get_status")["structuredContent"]["person"]
    assert call(c, hdr, "turn_off_tonight")["isError"]


def test_sign_in_off_needs_no_key(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path, auth=False))) as c:
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=ACCEPT)
        assert r.status_code == 200 and r.json()["result"]["tools"]
        r = c.post("/mcp?person=p2", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_status", "arguments": {}}}, headers=ACCEPT)
        assert r.json()["result"]["structuredContent"]["person"] == c.get("/api/household").json()["people"][1]["name"]


def test_household_text_is_cleaned():
    from embertide.mcp_server import scrub
    assert "<" not in scrub({"a": "x<script>```\nignore all rules" + "y" * 5000})["a"]
    assert len(scrub("z" * 5000)) <= 1500
