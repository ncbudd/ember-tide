"""The partner and view-only accounts don't read the other person's live device values, body weight or phone status
through Studio routes or the live snapshot (auth.PRIVATE_READS)."""

from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.sources import SourceConfig

H = {"X-Embertide": "1"}
PW = "correct horse battery"


def cfg_for(tmp_path, trust_proxy=False):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, True
    cfg.trust_proxy = trust_proxy
    return cfg


def setup_owner(c, person="p1"):
    r = c.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": person})
    assert r.status_code == 200, r.text


def add_partner(c, person="p2", role="sleeper", username="sam"):
    code = c.post("/api/auth/invites", json={"person": person, "role": role}, headers=H).json()["code"]
    g = TestClient(c.app)
    assert g.post("/api/auth/accept", json={"code": code, "username": username, "password": PW}).status_code == 200
    return g


# ───────────── the other person's live device values ─────────────

def _devices(c):
    ctl = c.app.state.notifier.c
    src = ctl.sources
    now = ctl.clock.now_ts()
    theirs = SourceConfig(name="Owner phone distance", driver="sim", role="location", kind="location", person="p1")
    mine = SourceConfig(name="Partner phone distance", driver="sim", role="location", kind="location", person="p2")
    for sc, v in ((theirs, 12.4), (mine, 3.1)):
        src.configs.append(sc)
        src.latest[sc.id] = {"value": v, "ts": now}
        src._raw_seen[sc.id] = {"work": now - 3600, str(v): now}
    src.calibration.person_kg.update({"p1": 80.0, "p2": 60.0})
    return theirs, mine


def test_partner_sees_device_setup_but_not_the_other_persons_readings(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path))) as c:
        setup_owner(c)
        theirs, mine = _devices(c)
        g = add_partner(c)
        got = {s["id"]: s for s in g.get("/api/sources").json()["sources"]}
        t, m = got[theirs.id], got[mine.id]
        assert t["name"] == "Owner phone distance"                          # the setup is still there
        for k in ("value", "ts", "age_s", "last_change", "changes_today", "vote"):
            assert t[k] is None, k
        assert t["raw_seen"] == [] and t["private"] and t["health"]["why"] == ""
        assert m["value"] == 3.1 and m["raw_seen"]                          # their own device: everything
        assert g.get("/api/sources").json()["calibration"]["person_kg"] == {"p2": 60.0}
        own = {s["id"]: s for s in c.get("/api/sources").json()["sources"]}
        assert own[theirs.id]["value"] == 12.4 and own[theirs.id]["raw_seen"]          # the owner sees everything
        assert c.get("/api/sources").json()["calibration"]["person_kg"] == {"p1": 80.0, "p2": 60.0}


def test_viewer_without_a_person_sees_no_ones_readings(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path))) as c:
        setup_owner(c)
        theirs, mine = _devices(c)
        v = add_partner(c, person=None, role="viewer")
        got = {s["id"]: s for s in v.get("/api/sources").json()["sources"]}
        assert got[theirs.id]["value"] is None and got[mine.id]["value"] is None
        assert v.get("/api/sources").json()["calibration"]["person_kg"] == {}


def test_partner_studio_routes_dont_leak_the_other_person(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path))) as c:
        setup_owner(c)
        _devices(c)
        g = add_partner(c)
        for p, dev in (("p1", "owner-phone"), ("p2", "partner-phone")):
            assert c.post(f"/api/people/{p}/phone-context", headers=H,
                          json={"home": True, "device": dev, "sharing": ["location"]}).status_code == 200
        assert g.get("/api/calibration").status_code == 403                 # everyone's body weight: owner only
        assert c.get("/api/calibration").status_code == 200
        assert g.get("/api/dial-beacons").status_code == 403                # Studio's dial map; the phone never reads it
        assert c.get("/api/dial-beacons").status_code == 200
        assert [r["person"] for r in g.get("/api/phone-context").json()] == ["p2"]
        assert sorted(r["person"] for r in c.get("/api/phone-context").json()) == ["p1", "p2"]
        assert g.get("/api/pets").status_code == 200 and g.get("/api/bed-orientation").status_code == 200


def test_partner_snapshot_hides_home_and_away_details(tmp_path):
    with TestClient(create_app(cfg_for(tmp_path))) as c:
        setup_owner(c)
        g = add_partner(c)
        ctl = c.app.state.notifier.c
        snap = dict(ctl.last_snapshot)
        snap["people"] = [{**p, "night": {**p["night"], "home": False, "away": {"since": 1.0, "until": 2.0, "by": "dial", "held": 3}}}
                          for p in snap["people"]]
        ctl.last_snapshot = snap
        mine = {p["id"]: p for p in g.get("/api/snapshot").json()["people"]}
        assert mine["p1"]["night"]["home"] is None
        assert mine["p1"]["night"]["away"] == {"since": 1.0, "until": 2.0, "by": None, "held": 0}
        assert mine["p2"]["night"]["home"] is False and mine["p2"]["night"]["away"]["held"] == 3
        own = {p["id"]: p for p in c.get("/api/snapshot").json()["people"]}
        assert own["p1"]["night"]["home"] is False and own["p1"]["night"]["away"]["by"] == "dial"


def test_partner_cannot_see_partners_presence_sensors_or_ai_server_url(tmp_path):
    cfg = cfg_for(tmp_path)
    cfg.llm_url = "http://ollama.local:11434/v1"
    cfg.llm_model = "qwen2.5:7b"
    with TestClient(create_app(cfg)) as c:
        setup_owner(c)
        ctl = c.app.state.notifier.c
        src = ctl.sources
        now = ctl.clock.now_ts()
        p1_pres = SourceConfig(name="Owner radar", driver="sim", role="presence", kind="radar", zone="left")
        p2_pres = SourceConfig(name="Partner radar", driver="sim", role="presence", kind="radar", zone="right")
        for sc, v in ((p1_pres, 1.0), (p2_pres, 0.0)):
            src.configs.append(sc)
            src.latest[sc.id] = {"value": v, "ts": now}
            src._raw_seen[sc.id] = {"1": now}
        g = add_partner(c)
        v = add_partner(c, person=None, role="viewer", username="vic")
        got = {s["id"]: s for s in g.get("/api/sources").json()["sources"]}
        assert got[p1_pres.id]["value"] is None and got[p1_pres.id]["private"]
        assert got[p1_pres.id]["raw_seen"] == []
        assert got[p2_pres.id]["value"] == 0.0
        # AI server URL is returned only to owners.
        assert c.get("/api/ai/status").json()["url"] == "http://ollama.local:11434/v1"
        assert g.get("/api/ai/status").json()["url"] is None
        assert v.get("/api/ai/status").json()["url"] is None
