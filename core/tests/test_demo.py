"""Tests for live public demo mode (ET_DEMO=on)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.demo import DEMO_MUTATE_LIMIT, reset_demo_state

H = {"X-EmberTide": "1"}


def _night_tz() -> str:
    """A fixed-offset zone where it is about 2 a.m. right now, so a night is always running."""
    import time as _t
    off = (2 - _t.gmtime().tm_hour) % 24
    if off > 14:
        off -= 24
    return "Etc/GMT" + ("+" if off <= 0 else "-") + str(abs(off))


def _demo_app(tmp_path: Path, **overrides):
    defaults = dict(
        data_dir=tmp_path,
        tz=_night_tz(),
        demo=True,
        bed="orion-mqtt",
        mqtt_url="mqtt://127.0.0.1:1883",
        llm_url="https://llm.invalid/v1",
        ha_mqtt_url="mqtt://ha.invalid:1883",
        push_relay=True,
        auth=True,
    )
    defaults.update(overrides)
    return create_app(Config(**defaults))


def test_demo_flag_forces_sim_and_disables_network(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ET_DEMO", "on")
    monkeypatch.setenv("ET_BED", "orion-cloud")
    monkeypatch.setenv("ET_ORION_API_KEY", "test_key")
    monkeypatch.setenv("ET_LLM_URL", "https://ai.invalid/v1")
    monkeypatch.setenv("ET_HA_MQTT_URL", "mqtt://ha.invalid:1883")
    monkeypatch.setenv("ET_PUSH_RELAY", "on")
    monkeypatch.setenv("ET_DATA_DIR", str(tmp_path))

    env_cfg = Config()
    assert env_cfg.demo is True
    assert env_cfg.bed == "sim"
    assert env_cfg.push_relay is False
    assert env_cfg.llm_url is None
    assert env_cfg.ha_mqtt_url is None

    # Mock outbound httpx clients before booting the app so any network attempt fails immediately.
    called: list[str] = []

    class _FailAsyncClient:
        def __init__(self, *a, **kw):
            called.append("AsyncClient")
            raise AssertionError("outbound httpx.AsyncClient is forbidden in demo mode")

    monkeypatch.setattr(httpx, "AsyncClient", _FailAsyncClient)

    with TestClient(_demo_app(tmp_path)) as c:
        st = c.app.state.et_state
        assert st.cfg.bed == "sim"
        assert st.controller.driver.id == "sim"
        assert st.cfg.push_relay is False
        assert st.cfg.llm_url is None
        assert st.cfg.ha_mqtt_url is None

        auth = c.get("/api/auth/state").json()
        assert auth["demo"] is True
        assert auth["enabled"] is False
        assert auth["setup_needed"] is False
        assert auth["me"]["role"] == "viewer"
        assert auth["me"]["kind"] == "open"

        snap = c.get("/api/snapshot").json()
        assert [p["name"] for p in snap["people"]] == ["Alex", "Sam"]
        assert all(p["settings"]["welcomed"] is True for p in snap["people"])
        pets = c.get("/api/pets").json()
        assert len(pets["pets"]) == 1 and pets["pets"][0]["name"] == "Milo"
        nights = c.get("/api/people/p1/nights?count=10").json()
        assert len(nights) >= 7

    assert called == []


def test_demo_blocks_external_account_security_export_and_console(tmp_path: Path):
    with TestClient(_demo_app(tmp_path)) as c:
        blocked = [
            ("GET", "/api/notify/providers", None),
            ("POST", "/api/notify/providers", {"kind": "ntfy", "url": "https://ntfy.sh/test"}),
            ("POST", "/api/notify/test/p1", {}),
            ("POST", "/api/notify/me/push/test", {}),
            ("POST", "/api/notify/me/webpush", {"endpoint": "https://push.invalid"}),
            ("GET", "/api/ai/settings", None),
            ("PUT", "/api/ai/settings", {"url": "https://ai.invalid/v1"}),
            ("POST", "/api/ai/settings/test", {}),
            ("POST", "/api/ai/settings/models", {}),
            ("GET", "/api/auth/me/mcp-tokens", None),
            ("POST", "/api/auth/me/mcp-tokens", {"label": "x"}),
            ("GET", "/api/auth/tokens", None),
            ("POST", "/api/auth/tokens", {"label": "x", "role": "owner"}),
            ("GET", "/api/auth/users", None),
            ("POST", "/api/auth/invites", {"role": "sleeper"}),
            ("POST", "/api/auth/me/password", {"current": "a", "new": "b"}),
            ("POST", "/api/auth/login", {"username": "x", "password": "y"}),
            ("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            ("GET", "/api/ha/connection", None),
            ("PUT", "/api/ha/connection", {"url": "mqtt://ha.invalid:1883"}),
            ("GET", "/api/integrations/ha", None),
            ("PUT", "/api/integrations/ha", {"url": "mqtt://ha.invalid:1883"}),
            ("GET", "/api/integrations/hooks", None),
            ("POST", "/api/integrations/hooks", {"name": "h"}),
            ("GET", "/api/integrations/polls", None),
            ("POST", "/api/integrations/polls", {"url": "https://example.com"}),
            ("POST", "/api/sources", {"kind": "ha"}),
            ("POST", "/api/sources/test-ha", {"url": "http://ha.invalid:8123"}),
            ("GET", "/api/export", None),
            ("POST", "/api/people/p1/wearable", {}),
            ("POST", "/api/console", {"cmd": "status"}),
            ("POST", "/api/sim", {"minutes": 10}),
            ("POST", "/api/care/check", {}),
        ]
        for method, path, body in blocked:
            kwargs = {"headers": H}
            if body is not None:
                kwargs["json"] = body
            r = c.request(method, path, **kwargs)
            assert r.status_code == 403, f"{method} {path} returned {r.status_code}"
            assert r.json() == {"detail": "Not available in the demo."}


def test_demo_allows_interactive_controls_and_resets_state(tmp_path: Path):
    with TestClient(_demo_app(tmp_path)) as c:
        orig_snap = c.get("/api/snapshot").json()
        orig_alex = next(p for p in orig_snap["people"] if p["id"] == "p1")
        orig_target = orig_alex["night"]["target_c"]

        # Bed controls work: adjust, flash, I'm up, feedback, settings, rhythm edits, built-in assistant.
        r_adj = c.post("/api/people/p1/adjust", json={"delta_c": 1.0, "until": "next_phase"}, headers=H)
        assert r_adj.status_code == 200
        new_target = next(p for p in r_adj.json()["people"] if p["id"] == "p1")["night"]["target_c"]
        assert abs(new_target - (orig_target + 1.0)) < 0.05

        assert c.post("/api/people/p1/flash", json={"kind": "cold"}, headers=H).status_code == 200
        assert c.post("/api/people/p1/flash/stop", json={}, headers=H).status_code == 200
        assert c.post("/api/people/p1/feedback", json={"temp": "warm", "rested": 4}, headers=H).status_code == 200
        assert c.patch("/api/people/p1/settings", json={"step_f": 0.5}, headers=H).status_code == 200
        orig_rhythm = dict(c.get("/api/people/p1/rhythms").json()[0])
        edited_rhythm = dict(orig_rhythm)
        edited_rhythm["name"] = "Demo Edited Rhythm"
        assert c.put(f"/api/people/p1/rhythms/{orig_rhythm['id']}", json=edited_rhythm, headers=H).status_code == 200
        assert c.post("/api/people/p1/tonight", json={"action": "start_now"}, headers=H).status_code == 200
        assert c.post("/api/people/p1/tonight", json={"action": "end"}, headers=H).status_code == 200
        assert c.post("/api/assistant/ask", json={"person_id": "p1", "question": "How is Alex sleeping?"}, headers=H).status_code == 200

        pets = c.get("/api/pets").json()["pets"]
        pets[0]["name"] = "VisitorCat"
        assert c.put("/api/pets", json={"pets": pets}, headers=H).status_code == 200
        assert c.get("/api/pets").json()["pets"][0]["name"] == "VisitorCat"

        # Simulated hourly reset restores seeded household, settings, flash/targets, rhythms, and pets.
        c.portal.call(reset_demo_state, c.app.state.et_state)

        after_snap = c.get("/api/snapshot").json()
        after_alex = next(p for p in after_snap["people"] if p["id"] == "p1")
        assert after_alex["settings"]["step_f"] == 1
        assert c.get("/api/people/p1/rhythms").json()[0]["name"] == orig_rhythm["name"]
        assert abs(after_alex["night"]["target_c"] - orig_target) < 0.05
        assert after_alex["night"]["state"] == orig_alex["night"]["state"]
        assert c.get("/api/pets").json()["pets"][0]["name"] == "Milo"
        assert len(c.get("/api/people/p1/nights?count=10").json()) >= 7


def test_demo_rate_limits_visitor_mutations(tmp_path: Path):
    with TestClient(_demo_app(tmp_path)) as c:
        for i in range(DEMO_MUTATE_LIMIT):
            r = c.post("/api/people/p1/adjust", json={"delta_c": 0.0, "until": "next_phase"}, headers=H)
            assert r.status_code == 200, f"failed early at request {i}"
        limited = c.post("/api/people/p1/adjust", json={"delta_c": 0.0, "until": "next_phase"}, headers=H)
        assert limited.status_code == 429


def test_demo_outbound_send_paths_assert_and_never_hit_httpx(tmp_path: Path, monkeypatch):
    from embertide import ai
    from embertide.fcm import Fcm
    from embertide.notify import Message, Notifier
    from embertide.push_relay import PushRelay

    called = False

    class _SpyClient:
        def __init__(self, *a, **kw):
            nonlocal called
            called = True

    monkeypatch.setattr(httpx, "AsyncClient", _SpyClient)

    with TestClient(_demo_app(tmp_path)) as c:
        st = c.app.state.et_state
        n: Notifier = st.notifier
        assert n.demo is True
        # No-op send() in demo mode returns False without scheduling network delivery.
        msg = Message(kind="test", title="hello", body="body", person="p1", urgent=True)
        assert n.send(msg) is False

        # Low-level transport methods assert demo mode is off before opening httpx clients.
        with pytest.raises(AssertionError):
            c.portal.call(n._ntfy, {"settings": {"topic": "t", "url": "https://ntfy.sh"}}, msg)
        with pytest.raises(AssertionError):
            c.portal.call(n._push_web, "p1", {"endpoint": "https://push.invalid"}, msg)
        with pytest.raises(AssertionError):
            c.portal.call(PushRelay(st.controller.store, demo=True).send_many, ["tok"], b"enc", False)
        with pytest.raises(AssertionError):
            c.portal.call(Fcm(tmp_path, demo=True).send, "tok", b"enc", False)

        eff = ai.effective(st.cfg, None)
        assert eff.off is True and eff.demo is True and eff.llm_key is None
        with pytest.raises(AssertionError):
            c.portal.call(ai.OpenAICompatModel(eff).run, "sys", "hi", [], lambda *a: None)

    assert called is False


def test_demo_refuses_large_bodies_and_hides_client_errors(tmp_path: Path):
    with TestClient(_demo_app(tmp_path)) as c:
        big = {"delta_c": 1.0, "until": "next_phase", "pad": "x" * (70 * 1024)}
        r = c.post("/api/people/p1/adjust", json=big, headers=H)
        assert r.status_code == 413

        def chunks():
            for _ in range(20):
                yield b"x" * 8192
        r = c.post("/api/people/p1/adjust", content=chunks(), headers={**H, "content-type": "application/json"})
        assert r.status_code in (400, 413)       # reading stops at the cap; FastAPI reports a body it couldn't read as 400

        assert c.post("/api/people/p1/adjust", json={"delta_c": 0.5, "until": "next_phase"}, headers=H).status_code == 200
        assert c.get("/api/client-errors").status_code == 403
        assert c.post("/api/client-error", json={"message": "hello"}, headers=H).status_code == 403


def test_demo_hides_api_docs_and_caps_live_sockets(tmp_path: Path):
    from starlette.websockets import WebSocketDisconnect
    from embertide.demo import DEMO_WS_PER_IP
    with TestClient(_demo_app(tmp_path)) as c:
        for path in ("/openapi.json", "/docs", "/redoc"):
            r = c.get(path)
            assert r.status_code == 404 or "openapi" not in r.text.lower()
        opened = []
        try:
            for _ in range(DEMO_WS_PER_IP):
                ws = c.websocket_connect("/api/live")
                opened.append(ws.__enter__())
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("/api/live") as extra:
                    extra.receive_json()
        finally:
            for ws in opened:
                ws.__exit__(None, None, None)
        with c.websocket_connect("/api/live") as again:      # slots come back once sockets close
            again.receive_json()


def test_rhythm_count_and_name_limits(tmp_path: Path):
    from embertide.api import MAX_RHYTHMS
    with TestClient(_demo_app(tmp_path)) as c:
        base = c.get("/api/people/p1/rhythms").json()
        rh = (base["rhythms"] if isinstance(base, dict) else base)[0]
        long = {**rh, "id": "x", "name": "n" * 81}
        assert c.put("/api/people/p1/rhythms/x", json=long, headers=H).status_code == 422
        have = len(base["rhythms"] if isinstance(base, dict) else base)
        codes = [c.put(f"/api/people/p1/rhythms/r{i}", json={**rh, "id": f"r{i}"}, headers=H).status_code
                 for i in range(MAX_RHYTHMS - have + 1)]
        assert codes[:-1] == [200] * (MAX_RHYTHMS - have) and codes[-1] == 422
        assert c.post("/api/people/p1/feedback", json={"date": "k1", "temp": "warm"}, headers=H).status_code == 422


def test_demo_resets_early_when_the_database_grows(tmp_path: Path, monkeypatch):
    import embertide.demo as demo
    calls = []

    async def fake_reset(state):
        calls.append(1)
    monkeypatch.setattr(demo, "reset_demo_state", fake_reset)
    monkeypatch.setattr(demo, "_db_bytes", lambda state: 10**9)

    async def run():
        task = asyncio.create_task(demo.demo_reset_loop(object(), every_s=3600, check_s=0.01, max_bytes=1))
        await asyncio.sleep(0.1)
        task.cancel()
    asyncio.run(run())
    assert calls


def test_seeded_demo_nights_start_and_finish(tmp_path: Path):
    """Every seeded night really runs: the radar settle check needs several samples, so seeding at coarse steps used
    to leave every night waiting, with a flat chart and a false "was the cat on the bed all night?" question."""
    import time as _t

    from embertide.demo import seed_demo_store
    from embertide.store import Store

    store = Store(tmp_path / "seed.db")
    now = _t.time()
    asyncio.run(seed_demo_store(store, "America/New_York", now, nights=3))
    evs = store.events_between(now - 4 * 86400, now + 60)
    for pid in ("p1", "p2"):
        mine = [e for e in evs if e.get("person") == pid]
        assert sum(e["kind"] == "rhythm_started" for e in mine) >= 2, pid
        assert sum(e["kind"] == "night_done" for e in mine) >= 2, pid
        assert not any(e["kind"] == "pet_question" for e in mine), pid
