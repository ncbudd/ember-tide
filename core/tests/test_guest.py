"""Guest mode: a side (or the whole bed) lent to a guest. Plain on/off and a temperature, the person who usually
sleeps there idles, the side turns itself off in the morning once it's been empty a while, and nothing about the
guest is recorded."""

import pytest
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.ha_bridge import HaBridge
from embertide.sources import SourceConfig, SourceManager
from test_autopilot_suite import H, make
from test_scenarios import Night, ts


def tel_rows(n, zone):
    return [r for r in n.store.history(zone, 0, 2e10) if r.get("zone", zone) == zone]


# ───────────── the engine side ─────────────
async def test_guest_drives_the_side_and_the_person_idles(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:40")
    n.c.guest_start(["right"], 24.0, None, None)
    n.present("right", True)
    await n.until("2026-11-05T23:30")
    e2 = n.e(n.p2)
    assert e2.s.state == "guest" and not e2.s.started
    assert n.side("right").on and n.side("right").setpoint == pytest.approx(24.0, abs=0.1)
    # the partner's own side runs as usual
    assert n.e(n.p1).s.state in ("waiting", "rhythm", "missed")
    n.c.guest_set("right", None, 20.5)
    await n.step(2)
    assert n.side("right").setpoint == pytest.approx(20.5, abs=0.1)
    n.c.guest_set("right", False, None)
    await n.step(2)
    assert not n.side("right").on


async def test_whole_bed_guest(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:40")
    n.c.guest_start(["left", "right"], 22.0, None, None)
    await n.step(3)
    assert n.side("left").on and n.side("right").on
    assert n.e(n.p1).s.state == n.e(n.p2).s.state == "guest"
    n.c.guest_stop(["left", "right"])
    await n.step(2)
    assert n.e(n.p1).s.state != "guest" and n.c.household.guest == {}


async def test_guest_start_clears_a_running_flash_and_change(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T00:30")
    e = n.e(n.p1)
    e.flash("hot", n.clock.now_ts())
    e.adjust(n.clock.now_ts(), delta_c=1.0)
    n.c.guest_start(["left"], 25.0, None, None)
    await n.step(2)
    assert e.s.flash is None and e.s.override is None and n.side("left").setpoint == pytest.approx(25.0, abs=0.1)


# ───────────── morning auto-off ─────────────
async def test_morning_off_after_side_empty_long_enough(tmp_path):
    n = Night(tmp_path, "2026-11-06T06:00", echo=False)
    n.c.guest_start(["right"], 23.0, 30, None)
    n.present("right", True)
    await n.until("2026-11-06T07:00")
    n.present("right", False)                                  # the guest gets up at 7:00
    await n.until("2026-11-06T07:25")
    assert n.c.household.guest["right"].on, "not yet: only 25 min empty"
    await n.until("2026-11-06T07:32")
    assert not n.c.household.guest["right"].on and not n.side("right").on
    assert "right" in n.c.household.guest                      # still in guest mode until the owner ends it
    ev = n.events("guest_side_off")
    assert ev and ev[0]["zone"] == "right" and ev[0].get("person") is None


async def test_no_morning_off_at_night_or_with_unknown_presence(tmp_path):
    n = Night(tmp_path, "2026-11-05T23:00")
    n.c.guest_start(["right"], 23.0, 30, None)
    n.present("right", False)                                  # empty side late at night: that's the owner's call
    await n.until("2026-11-06T02:00")
    assert n.c.household.guest["right"].on
    # in the morning the bed sensor goes silent: unknown is never "empty"
    n.side("right").frozen = True
    n.side("left").frozen = True
    await n.until("2026-11-06T09:00")
    assert n.c.household.guest["right"].on


async def test_guest_mode_ends_by_itself(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:40")
    n.c.guest_start(["left"], 22.0, None, ts("2026-11-06T10:00"))
    await n.until("2026-11-06T10:01")
    assert "left" not in n.c.household.guest
    assert any(e.get("reason") == "ended" for e in n.events("guest_stopped"))


# ───────────── nothing recorded ─────────────
async def test_nothing_about_the_guest_is_stored(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:40")
    mgr = SourceManager(n.store, n.clock, lambda: n.bed)
    mgr.configs = [SourceConfig(id="radr", name="Radar right", driver="sim", role="presence", zone="right", kind="radar", params={"kind": "presence"}),
                   SourceConfig(id="radl", name="Radar left", driver="sim", role="presence", zone="left", kind="radar", params={"kind": "presence"})]
    n.c.sources = mgr
    n.c.guest_start(["right"], 23.0, None, None)
    n.present("right", True)
    n.present("left", True)
    for _ in range(120):
        mgr.sim_step(n.clock.now_ts())
        await n.step(1, 30)
    assert n.store.history("right", 0, 2e10, person=n.p2.id) == []
    assert n.store.samples("radr", 0, 2e10) == [] and n.store.samples("radl", 0, 2e10) != []
    personal = [e for e in n.store.events(limit=5000) if e.get("person") == n.p2.id]
    assert personal == [], personal
    assert {e["kind"] for e in n.store.events(limit=5000) if e.get("zone") == "right"} <= {"guest_started", "guest_side_off", "guest_stopped"}


async def test_guest_nights_dont_count_for_learning(tmp_path):
    n = Night(tmp_path, "2026-11-02T21:00")
    n.p2.settings.autopilot.learn_nights = 1
    n.c.guest_start(["right"], 23.0, None, None)
    n.present("right", True)
    await n.until("2026-11-03T07:30", dt=120)
    n.present("right", False)
    await n.until("2026-11-03T13:00", dt=300)
    assert n.c.autopilot.learning(n.p2, n.clock.now_ts())["have"] == 0


# ───────────── HA bridge ─────────────
async def test_ha_bridge_guest_entities_and_climate(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:40")
    n.p2.name = "Sam"
    b = HaBridge(n.c, "mqtt://x")
    n.c.guest_start(["right"], 23.0, None, None)
    await n.step(2)
    topics = [t for t, _ in b.discovery()]
    assert any(t.endswith("/sensor/embertide_right_guest/config") for t in topics)
    bed_cfg = next(c for t, c in b.discovery() if t.endswith("/sensor/embertide_right_guest/config"))
    assert "Sam" not in str(bed_cfg) and n.p2.id not in str(bed_cfg)   # no personal data on the bed-level guest sensor
    st = b.states(n.c.snapshot())
    assert st["embertide/bed/right/guest"] == "on" and st["embertide/bed/left/guest"] == "off"
    assert st[b.t(n.p2, "guest")] == "on" and st[b.t(n.p1, "guest")] == "off"
    # the climate entity works the guest's side; flashes are ignored
    f = n.p2.settings.units == "F"
    assert await b.handle(f"embertide/{n.p2.id}/set/target", "75" if f else "24") == f"guest target {'75' if f else '24'}"
    assert await b.handle(f"embertide/{n.p2.id}/set/mode", "off") == "guest side off"
    assert not n.c.household.guest["right"].on
    assert await b.handle(f"embertide/{n.p2.id}/set/flash", "hot") is None
    assert n.e(n.p2).s.flash is None


# ───────────── API ─────────────
def test_api_guest_flow_and_validation(tmp_path):
    with make(tmp_path) as c:
        assert c.get("/api/guest").json() == {}
        r = c.post("/api/guest/right/start", headers=H, json={"temp_c": 23, "morning_off_min": 35})
        assert r.status_code == 200 and r.json()["guest"]["right"]["morning_off_min"] == 35
        assert c.post("/api/household/swap-sides", headers=H).status_code == 409
        assert c.post("/api/guest/right/set", headers=H, json={"temp_c": 99}).status_code == 422
        assert c.post("/api/guest/left/set", headers=H, json={"on": False}).status_code == 409
        assert c.post("/api/guest/right/set", headers=H, json={"on": False}).json()["guest"]["right"]["on"] is False
        assert c.post("/api/guest/right/start", headers=H, json={"morning_off_min": 90}).status_code == 422
        assert c.post("/api/guest/middle/start", headers=H, json={}).status_code == 422
        assert c.post("/api/guest/both/stop", headers=H).json()["guest"] == {}


def test_api_guest_permissions(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", True
    with TestClient(create_app(cfg)) as c:
        a = c.app.state.notifier.c                              # the controller
        auth = c.app.state.notifier.c.store                     # noqa: F841 (kept for readability)
        from embertide.auth import Auth
        au = Auth(a.store, enabled=True)
        au.create_user("owner1", "a long password 1", "owner", "p1")
        au.create_user("sleeper2", "a long password 2", "sleeper", "p2")
        au.create_user("viewer3", "a long password 3", "viewer", None)

        def login(u, pw):
            c.cookies.clear()
            assert c.post("/api/auth/login", headers=H, json={"username": u, "password": pw}).status_code == 200
        login("sleeper2", "a long password 2")
        assert c.post("/api/guest/right/start", headers=H, json={}).status_code == 403
        login("owner1", "a long password 1")
        assert c.post("/api/guest/right/start", headers=H, json={}).status_code == 200
        login("sleeper2", "a long password 2")
        assert c.post("/api/guest/right/set", headers=H, json={"temp_c": 22}).status_code == 200
        login("viewer3", "a long password 3")
        assert c.post("/api/guest/right/set", headers=H, json={"temp_c": 22}).status_code == 403
        assert c.get("/api/guest").status_code == 200


def test_person_controls_refused_on_a_guest_side(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/guest/left/start", headers=H, json={})
        for path, body in (("adjust", {"delta_c": 0.5}), ("flash", {"kind": "hot"}), ("tonight", {"action": "skip"})):
            assert c.post(f"/api/people/p1/{path}", headers=H, json=body).status_code == 409, path
        assert c.post("/api/people/p1/flash/stop", headers=H).status_code == 409
        assert c.post("/api/people/p2/adjust", headers=H, json={"delta_c": 0.5}).status_code == 200   # the other side is fine
