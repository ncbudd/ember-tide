"""Studio → Guest mode: change the turn-off minutes, the end time and whether the bedside dial shows guest mode
while a guest is staying; the dial choice reaches the dial through the HA bridge's per-person guest sensor."""

from embertide.ha_bridge import HaBridge
from test_autopilot_suite import H, make
from test_scenarios import Night


def test_api_guest_settings_while_staying(tmp_path):
    with make(tmp_path) as c:
        g = c.post("/api/guest/right/start", headers=H, json={"temp_c": 22}).json()["guest"]["right"]
        assert g["dial"] is True and g["ends"] is None and g["morning_off_min"] == 40
        now = c.get("/api/snapshot").json()["now"]
        g = c.post("/api/guest/right/set", headers=H, json={"morning_off_min": 30, "ends": now + 86400, "dial": False}).json()["guest"]["right"]
        assert g["morning_off_min"] == 30 and g["ends"] == now + 86400 and g["dial"] is False and g["temp_c"] == 22
        # 0 clears the end; other fields stay
        g = c.post("/api/guest/right/set", headers=H, json={"ends": 0}).json()["guest"]["right"]
        assert g["ends"] is None and g["dial"] is False and g["morning_off_min"] == 30
        # starting again (e.g. the whole bed) keeps this side's dial choice unless told
        g = c.post("/api/guest/both/start", headers=H, json={}).json()["guest"]
        assert g["right"]["dial"] is False and g["left"]["dial"] is True
        assert c.post("/api/guest/left/start", headers=H, json={"dial": False}).json()["guest"]["left"]["dial"] is False
        # validation
        assert c.post("/api/guest/right/set", headers=H, json={"morning_off_min": 10}).status_code == 422
        assert c.post("/api/guest/right/set", headers=H, json={"ends": now - 60}).status_code == 422
        assert c.post("/api/guest/right/set", headers=H, json={"ends": now + 40 * 86400}).status_code == 422


async def test_dial_guest_sensor_follows_the_choice(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:40")
    b = HaBridge(n.c, "mqtt://x")
    n.c.guest_start(["right"], 23.0, None, None)
    await n.step(1)
    assert b.states(n.c.snapshot())[b.t(n.p2, "guest")] == "on"
    n.c.household.guest["right"].dial = False
    st = b.states(n.c.snapshot())
    assert st[b.t(n.p2, "guest")] == "off"              # the dial keeps its usual look
    assert st["embertide/bed/right/guest"] == "on"       # the bed-level sensor still says a guest is there
