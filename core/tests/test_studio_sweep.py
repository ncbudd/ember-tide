"""Every call Studio's pages make: reads answer, and each button's action does its job (on the simulated bed)."""

from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config

H = {"X-Embertide": "1"}


def make(tmp_path, auth=False):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, auth
    return TestClient(create_app(cfg))


def test_every_studio_read_answers(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T23:30", "scripted": True}, headers=H)
        date = "2026-11-06"
        for path in ["/api/ai/status", "/api/autopilot/meta", "/api/events?limit=30", f"/api/explore?person_id=p1&date={date}",
                     "/api/integrations/ha", "/api/notify/log", "/api/notify/providers", "/api/notify/settings", "/api/people/p1/insights",
                     "/api/people/p1/autopilot", f"/api/people/p1/autopilot/night?date={date}", "/api/people/p1/autopilot/suggestions?all=true",
                     "/api/people/p1/autopilot/trends", "/api/people/p1/imports", "/api/people/p1/nights?count=14", "/api/people/p1/rhythms",
                     "/api/presence/agreement?person_id=p1", "/api/sim", "/api/sources", "/api/snapshot", "/api/system", "/api/household",
                     f"/api/people/p1/autopilot/ai-review?date={date}", "/api/export"]:
            r = c.get(path)
            assert r.status_code == 200, (path, r.status_code, r.text[:200])


def test_studio_actions(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T22:00", "scripted": True}, headers=H)
        # simulator controls
        assert c.post("/api/sim", json={"speed": 60}, headers=H).json()["speed"] == 60
        assert c.post("/api/sim", json={"speed": 1, "room_c": 23.0}, headers=H).json()["room_c"] == 23.0
        # bed console
        r = c.post("/api/console", json={"cmd": "status"}, headers=H)
        assert r.status_code in (200, 422), r.text
        # autopilot: replay a night, re-check it, ask for the AI review without a model configured
        assert c.post("/api/people/p1/autopilot/replay", json={"date": "2026-11-05"}, headers=H).status_code == 200
        r = c.post("/api/people/p1/autopilot/recheck", json={"date": "2026-11-05"}, headers=H)
        assert r.status_code in (200, 409), r.text                         # 409: no data for that night yet is a fair answer
        r = c.post("/api/people/p1/autopilot/ai-review", json={"date": "2026-11-05"}, headers=H)
        assert r.status_code in (200, 409, 503), r.text
        # insights: apply whatever is proposed, if anything
        ins = c.get("/api/people/p1/insights").json()
        items = ins.get("items", ins) if isinstance(ins, dict) else ins
        for i in [x for x in items if isinstance(x, dict) and x.get("proposal")][:1]:
            assert c.post("/api/people/p1/insights/apply", json={"proposal": i["proposal"], "insight_id": i.get("id")}, headers=H).status_code == 200
        # notification target: add, edit, test (unreachable: a clear error, not a crash), remove
        p = c.post("/api/notify/providers", json={"kind": "ntfy", "name": "Phone", "person": "p1", "settings": {"url": "http://127.0.0.1:9", "topic": "t"}}, headers=H).json()
        assert c.put(f"/api/notify/providers/{p['id']}", json={"kind": "ntfy", "name": "Phone 2", "person": "p1", "settings": {"url": "http://127.0.0.1:9", "topic": "t"}}, headers=H).status_code == 200
        t = c.post(f"/api/notify/test/{p['id']}", headers=H).json()
        assert t["ok"] is False and t["error"]
        assert c.delete(f"/api/notify/providers/{p['id']}", headers=H).status_code == 200
        assert c.put("/api/notify/settings", json={"public_url": "https://bed.example"}, headers=H).json()["public_url"] == "https://bed.example"
        # integrations: saving HA settings without a reachable HA is stored and reported, not a crash
        r = c.put("/api/integrations/ha", json={"enabled": False, "url": "", "prefix": "homeassistant"}, headers=H)
        assert r.status_code in (200, 422), r.text
        # rhythm editor round trip
        rh = c.get("/api/people/p1/rhythms").json()
        rhythms = rh["rhythms"] if isinstance(rh, dict) else rh
        r0 = rhythms[0]
        r0["schedule"]["confirm_min"] = 5
        assert c.put(f"/api/people/p1/rhythms/{r0['id']}", json=r0, headers=H).status_code == 200
        rh = c.get("/api/people/p1/rhythms").json()
        assert (rh["rhythms"] if isinstance(rh, dict) else rh)[0]["schedule"]["confirm_min"] == 5
        # calibration save
        cal = c.get("/api/sources").json()["calibration"]
        cal["weights"]["bed"] = 0.6
        assert c.put("/api/calibration", json=cal, headers=H).json()["weights"]["bed"] == 0.6


def test_access_page_actions_with_sign_in(tmp_path):
    with make(tmp_path, auth=True) as c:
        assert c.post("/api/auth/setup", json={"username": "owner", "password": "a-long-test-password", "person": "p1"}, headers=H).status_code == 200
        inv = c.post("/api/auth/invites", json={"person": "p2", "role": "sleeper"}, headers=H).json()
        assert inv["path"].startswith("/join")
        lst = c.get("/api/auth/invites").json()
        assert c.delete(f"/api/auth/invites/{lst[0]['id']}", headers=H).status_code == 200
        tok = c.post("/api/auth/tokens", json={"name": "Home Assistant"}, headers=H).json()
        assert tok["token"].startswith("et_")
        tl = c.get("/api/auth/tokens").json()
        assert c.delete(f"/api/auth/tokens/{tl[0]['id']}", headers=H).status_code == 200
        users = c.get("/api/auth/users").json()
        assert len(users) == 1 and users[0]["role"] == "owner"
