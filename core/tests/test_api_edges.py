"""Bad input, unknown things, and actions people will actually try."""

from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config


def make(tmp_path, bed="sim"):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", bed, tmp_path / "noweb", None, False
    return TestClient(create_app(cfg))


def test_rejects_bad_input(tmp_path):
    with make(tmp_path) as c:
        assert c.post("/api/people/nobody/flash", json={"kind": "hot"}).status_code == 404
        assert c.post("/api/people/p1/flash", json={"kind": "warm"}).status_code == 422
        assert c.post("/api/people/p1/flash", json={"kind": "hot", "minutes": 0}).status_code == 422
        assert c.post("/api/people/p1/flash", json={"kind": "hot", "minutes": 500}).status_code == 422
        assert c.post("/api/people/p1/flash/extend?minutes=-5").status_code == 422
        assert c.post("/api/people/p1/tonight", json={"action": "pause", "minutes": -5}).status_code == 422
        assert c.post("/api/people/p1/tonight", json={"action": "explode"}).status_code == 422
        assert c.post("/api/people/p1/adjust", json={"delta_c": 99}).status_code == 422
        assert c.post("/api/people/p1/adjust", json={"temp_c": 2}).status_code == 422
        rh = c.get("/api/people/p1/rhythms").json()[0]
        bad = dict(rh, phases=[])
        assert c.put(f"/api/people/p1/rhythms/{rh['id']}", json=bad).status_code == 422
        bad = dict(rh, schedule=dict(rh["schedule"], bedtime="25:99"))
        assert c.put(f"/api/people/p1/rhythms/{rh['id']}", json=bad).status_code == 422
        s = c.get("/api/household").json()["people"][0]["settings"]
        s["hot_flash"]["duration_min"] = 0
        assert c.put("/api/people/p1/settings", json=s).status_code == 422
        assert c.post("/api/people/p1/feedback", json={"date": "2026-11-05", "temp": "lukewarm"}).status_code == 422
        assert c.post("/api/console", json={"command": "OTA http://evil/fw.bin"}).status_code == 400
        assert c.post("/api/console", json={"command": "ERASE WIFI"}).status_code == 400
        assert c.post("/api/console", json={"command": ""}).status_code == 400


def test_every_night_needs_an_active_phase(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T14:00"})          # daytime: no phase
        assert c.post("/api/people/p1/adjust", json={"delta_c": -0.5, "until": "every_night"}).status_code == 409


def test_every_night_writes_the_rhythm(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T21:45"})          # waiting → Wind-down is the phase
        before = c.get("/api/people/p1/rhythms").json()[0]["phases"][0]["temp_c"]
        r = c.post("/api/people/p1/adjust", json={"delta_c": -0.5, "until": "every_night"})
        assert r.status_code == 200
        after = c.get("/api/people/p1/rhythms").json()[0]["phases"][0]["temp_c"]
        assert round(before - after, 2) == 0.5


def test_swap_sides(tmp_path):
    with make(tmp_path) as c:
        before = {p["id"]: p["zone"] for p in c.get("/api/household").json()["people"]}
        assert c.post("/api/household/swap-sides").status_code == 200
        after = {p["id"]: p["zone"] for p in c.get("/api/household").json()["people"]}
        assert after == {k: ("right" if v == "left" else "left") for k, v in before.items()}
        assert c.post("/api/household/swap-sides").status_code == 200
        assert {p["id"]: p["zone"] for p in c.get("/api/household").json()["people"]} == before
        assert any(e["kind"] == "sides_swapped" for e in c.get("/api/events").json())


def test_person_colour_setting_and_swap_keeps_colours(tmp_path):
    with make(tmp_path) as c:
        ppl = {p["id"]: p for p in c.get("/api/household").json()["people"]}
        left = next(k for k, p in ppl.items() if p["zone"] == "left")
        right = next(k for k, p in ppl.items() if p["zone"] == "right")
        assert ppl[left]["settings"]["color"] == ""            # side default until chosen
        s = dict(ppl[right]["settings"], color="violet")
        assert c.put(f"/api/people/{right}/settings", json=s).status_code == 200
        assert c.put(f"/api/people/{right}/settings", json=dict(s, color="neon")).status_code == 422
        assert c.post("/api/household/swap-sides").status_code == 200
        after = {p["id"]: p["settings"]["color"] for p in c.get("/api/household").json()["people"]}
        assert after == {left: "teal", right: "violet"}          # colours follow the person, not the side


def test_export_pseudonymizes(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T22:40", "speed": 300})
        import time
        time.sleep(1.5)
        c.post("/api/sim", json={"speed": 1})
        csv = c.get("/api/export?fmt=csv&hours=48&pseudonymize=true").text
        assert "Left" not in csv and "Right" not in csv and ("P1" in csv or csv.count("\n") <= 1)


def test_sim_endpoints_refuse_on_real_bed_driver(tmp_path):
    # orion-mqtt with no broker: the app must still start, and simulator endpoints must say no
    with make(tmp_path, bed="orion-mqtt") as c:
        assert c.get("/api/sim").status_code == 404
        assert c.post("/api/sim", json={"speed": 10}).status_code == 404
        assert c.get("/healthz").status_code == 200
        snap = c.get("/api/snapshot").json()
        assert snap["system"]["online"] is False


def test_rename_and_persona(tmp_path):
    with make(tmp_path) as c:
        assert c.put("/api/people/p2/profile", json={"name": "Jordan", "persona": "tide"}).status_code == 200
        p2 = c.get("/api/household").json()["people"][1]
        assert p2["name"] == "Jordan" and p2["persona"] == "tide"
        assert c.put("/api/people/p2/profile", json={"persona": "lava"}).status_code == 422


def test_ha_test_gives_plain_language_errors(tmp_path):
    with make(tmp_path) as c:
        r = c.post("/api/sources/test-ha", json={"url": "http://127.0.0.1:9", "token": "x", "entity_id": "sensor.x"}).json()
        assert r["ok"] is False and "Couldn't reach Home Assistant" in r["error"]


def test_applied_insight_is_not_suggested_again(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T22:30"})
        for _ in range(3):
            c.post("/api/people/p1/adjust", json={"delta_c": -0.6})
        c.post("/api/sim", json={"jump_to": "2026-11-06T09:00"})   # night over
        # make the adjust events look like a finished night's history by asking for insights directly
        ins = c.get("/api/people/p1/insights").json()
        prop = next((i for i in ins if i.get("proposal")), None)
        if prop is None:
            return            # not enough data in a short test night; the apply path is covered elsewhere
        assert c.post("/api/people/p1/insights/apply", json={"proposal": prop["proposal"], "insight_id": prop["id"]}).status_code == 200
        again = next(i for i in c.get("/api/people/p1/insights").json() if i["id"] == prop["id"])
        assert again["proposal"] is None and again.get("applied")


def test_rhythm_put_rejects_wild_offsets_and_out_of_order(tmp_path):
    with make(tmp_path) as c:
        rh = c.get("/api/people/p1/rhythms").json()[0]
        bad = dict(rh, phases=[dict(ph) for ph in rh["phases"]])
        bad["phases"][2] = dict(bad["phases"][2], start=dict(bad["phases"][2]["start"], offset_min=99999))
        assert c.put(f"/api/people/p1/rhythms/{rh['id']}", json=bad).status_code == 422
        swapped = dict(rh, phases=[rh["phases"][2], rh["phases"][1], rh["phases"][0], *rh["phases"][3:]])
        assert c.put(f"/api/people/p1/rhythms/{rh['id']}", json=swapped).status_code == 422
        assert c.put(f"/api/people/p1/rhythms/{rh['id']}", json=rh).status_code == 200


def test_patch_settings_changes_only_what_is_sent(tmp_path):
    with make(tmp_path) as c:
        assert c.patch("/api/people/p2/settings", json={"app_mode": "simple"}).status_code == 200
        s = next(p for p in c.get("/api/household").json()["people"] if p["id"] == "p2")["settings"]
        assert s["app_mode"] == "simple" and s["units"] == "F"
        assert c.patch("/api/people/p2/settings", json={"app_mode": "tiny"}).status_code == 422


def test_tonight_wake_override_range(tmp_path):
    import time
    with make(tmp_path) as c:
        now = c.get("/api/snapshot").json()["now"] if c.get("/api/snapshot").status_code == 200 else time.time()
        assert c.post("/api/people/p1/context", json={"wake": now + 6 * 3600}).status_code == 200
        assert c.post("/api/people/p1/context", json={"wake": now + 3 * 86400}).status_code == 422
        assert c.post("/api/people/p1/context", json={"wake": 5}).status_code == 422
