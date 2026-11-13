from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config


def client(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False
    return TestClient(create_app(cfg))


def test_every_write_endpoint_accepts_json(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/healthz").json()["ok"]
        assert c.post("/api/sim", json={"jump_to": "2026-11-05T22:40"}).status_code == 200
        ok = lambda r: r.status_code < 300 or print(r.status_code, r.text)  # noqa: E731
        assert ok(c.post("/api/people/p1/adjust", json={"delta_c": -0.5}))
        assert ok(c.post("/api/people/p1/adjust/clear"))
        assert ok(c.post("/api/people/p1/flash", json={"kind": "hot"}))
        assert ok(c.post("/api/people/p1/flash/extend"))
        assert ok(c.post("/api/people/p1/flash/stop"))
        assert ok(c.post("/api/people/p1/tonight", json={"action": "pause", "minutes": 30}))
        assert ok(c.post("/api/people/p1/context", json={"home": True}))
        assert ok(c.put("/api/people/p1/profile", json={"name": "Robin"}))
        assert c.get("/api/household").json()["people"][0]["name"] == "Robin"
        s = c.get("/api/household").json()["people"][0]["settings"]
        s["simple_default"] = True
        assert ok(c.put("/api/people/p1/settings", json=s))
        assert ok(c.post("/api/people/p1/feedback", json={"date": "2026-11-05", "temp": "warm"}))
        rh = c.get("/api/people/p1/rhythms").json()[0]
        rh["phases"][2]["temp_c"] -= 0.5
        assert ok(c.put(f"/api/people/p1/rhythms/{rh['id']}", json=rh))
        assert ok(c.post("/api/console", json={"command": "IB DIMMER 20"}))
        assert c.post("/api/console", json={"command": "OTA http://x"}).status_code in (200, 400)
        src = c.post("/api/sources", json={"name": "Test radar", "driver": "sim", "role": "presence", "zone": "left", "params": {"kind": "presence"}}).json()
        assert ok(c.put("/api/calibration", json={"weights": {"bed": 0.6, "presence": 0.2, "weight": 0.4}, "weight_threshold_kg": 25}))
        assert ok(c.delete(f"/api/sources/{src['id']}"))
        assert ok(c.post("/api/sources/test-ha", json={"url": "http://127.0.0.1:9", "token": "x", "entity_id": "sensor.x"}))
        ins = c.get("/api/people/p1/insights").json()
        assert isinstance(ins, list)
        assert ok(c.post("/api/people/p1/insights/apply", json={"proposal": {"rhythm_id": rh["id"], "phase_id": "deep", "field": "temp_c", "to": 17.0}}))
        assert ok(c.post("/api/ai/ask", json={"person_id": "p1", "question": "How did I sleep?"}))
        assert c.get("/api/explore?person_id=p1").json()["series"]
        assert isinstance(c.get("/api/presence/agreement?person_id=p1").json(), list)
        assert c.get("/api/people/p1/night").status_code == 200
        assert c.get("/api/export?fmt=csv&hours=24").status_code == 200


def test_stage_import_health_connect_codes(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T07:30"})
        from datetime import datetime
        from zoneinfo import ZoneInfo
        base = datetime(2026, 11, 6, 0, 30, tzinfo=ZoneInfo("America/New_York")).timestamp()
        segs = [{"start": base, "end": base + 1800, "stage": "4"}, {"start": base + 1800, "end": base + 3600, "stage": 5},
                {"start": base + 3600, "end": base + 4200, "stage": "REM"}, {"start": base + 4200, "end": base + 4300, "stage": "nonsense"}]
        r = c.post("/api/people/p1/stages", json={"source": "health-connect", "label": "Pixel Watch · Health Connect", "segments": segs})
        assert r.status_code == 200 and r.json()["stored"] == 3
        st = c.get("/api/people/p1/stages?date=2026-11-05").json()
        hc = next(x for x in st if x["id"] == "health-connect")
        assert [g["stage"] for g in hc["segments"]] == ["light", "deep", "rem"]


def test_explorer_labels_are_plain_words(tmp_path):
    """Chart labels are read by people, not programmers: no engine words, vendor names or raw codes jargon."""
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T23:30"})
        series = {s["id"]: s for s in c.get("/api/explore?person_id=p1").json()["series"]}
        assert series["occupied"]["label"] == "In bed: final answer"
        assert series["bed_occ"]["label"] == "In bed: bed's own sensor"
        assert series["status"]["label"] == "Bed sensor reading (unfiltered)"
        assert series["target"]["label"] == "Planned temperature"
        for s in series.values():
            text = f"{s['label']} {s['source']} {s['device']}".lower()
            for word in ("fusion", "sleepace", "raw", "setpoint", "tec)", "combined", "engine", "echo"):
                assert word not in text, (s["id"], word)


def test_explorer_side_follows_bed_orientation(tmp_path):
    """A bed that names its sides from the foot: the chart's source line says the side as you lie."""
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T23:30"})
        src = lambda: {s["id"]: s for s in c.get("/api/explore?person_id=p1").json()["series"]}["measured"]["source"]
        assert "left side" in src()
        assert c.put("/api/bed-orientation", json={"from_foot": True}, headers={"X-Embertide": "1"}).status_code == 200
        assert "right side" in src()


def test_every_night_insights_and_put_rhythm_clamp_to_household_limits(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T22:40"})
        r = c.post("/api/people/p1/adjust", json={"temp_c": 44.0, "until": "every_night"})
        assert r.status_code == 200
        rhs = c.get("/api/people/p1/rhythms").json()
        wn = next(x for x in rhs if x["id"] == "weeknights")
        active_ph = r.json()["people"][0]["night"]["phase_id"]
        assert next(ph["temp_c"] for ph in wn["phases"] if ph["id"] == active_ph) == 35.0

        r2 = c.post("/api/people/p1/adjust", json={"temp_c": 10.0, "until": "every_night"})
        assert r2.status_code == 200
        rhs2 = c.get("/api/people/p1/rhythms").json()
        wn2 = next(x for x in rhs2 if x["id"] == "weeknights")
        assert next(ph["temp_c"] for ph in wn2["phases"] if ph["id"] == active_ph) == 12.0

        r3 = c.post("/api/people/p1/insights/apply", json={
            "proposal": {"rhythm_id": "weeknights", "phase_id": "deep", "field": "temp_c", "to": 42.0}
        })
        assert r3.status_code == 200
        rhs3 = c.get("/api/people/p1/rhythms").json()
        wn3 = next(x for x in rhs3 if x["id"] == "weeknights")
        assert next(ph["temp_c"] for ph in wn3["phases"] if ph["id"] == "deep") == 35.0

        wn3["phases"][0]["temp_c"] = 41.0
        r4 = c.put("/api/people/p1/rhythms/weeknights", json=wn3)
        assert r4.status_code == 200
        rhs4 = c.get("/api/people/p1/rhythms").json()
        wn4 = next(x for x in rhs4 if x["id"] == "weeknights")
        assert wn4["phases"][0]["temp_c"] == 35.0


def test_startup_clamps_out_of_range_saved_household(tmp_path, caplog):
    from embertide.seed import default_household
    from embertide.store import Store

    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False
    saved = default_household().model_dump()
    saved["people"][0]["rhythms"][0]["phases"][0]["temp_c"] = 50.0
    saved["people"][0]["rhythms"][0]["phases"][1]["temp_c"] = 5.0
    st = Store(cfg.db_path)
    st.put("household", saved)
    st._db.close()

    with caplog.at_level("WARNING", logger="embertide"):
        with TestClient(create_app(cfg)) as c:
            ph = c.get("/api/people/p1/rhythms").json()[0]["phases"]
            assert ph[0]["temp_c"] == 45.0
            assert ph[1]["temp_c"] == 10.0
    assert any("clamped saved phase" in rec.message for rec in caplog.records)


def test_set_away_ends_active_flash(tmp_path):
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T22:40"})
        r1 = c.post("/api/people/p1/flash", json={"kind": "hot", "minutes": 30})
        assert r1.status_code == 200 and r1.json()["people"][0]["night"]["flash"] is not None
        r2 = c.post("/api/people/p1/away", json={"away": True})
        assert r2.status_code == 200 and r2.json()["people"][0]["night"]["flash"] is None
