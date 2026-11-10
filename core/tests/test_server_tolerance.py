"""Bad query parameters answer 4xx instead of 500 (or an unbounded query), Home Assistant trouble doesn't break
GET /api/dials, heavy wearable/stage imports run off the event loop, and an empty Orion fetch is retried later."""

import asyncio

import httpx

from test_autopilot_suite import H, make


def ctl(c):
    return c.app.state.notifier.c


def test_bad_dates_are_400_not_500(tmp_path):
    with make(tmp_path) as c:
        assert c.get("/api/people/p1/night?date=garbage", headers=H).status_code == 400
        assert c.get("/api/people/p1/stages?date=nope", headers=H).status_code == 400
        assert c.get("/api/presence/agreement?person_id=p1&date=x", headers=H).status_code == 400
        assert c.get("/api/explore?person_id=p1&date=2026-13-45", headers=H).status_code == 400
        assert c.get("/api/people/p1/night?date=2026-11-05", headers=H).status_code == 200


def test_count_limit_hours_have_lower_bounds(tmp_path):
    with make(tmp_path) as c:
        s = ctl(c).store
        for i in range(5):
            s.event(1000 + i, "note", "p1", "app")
        assert len(c.get("/api/events?limit=-1", headers=H).json()) == 1
        assert len(c.get("/api/events?limit=0", headers=H).json()) == 1
        assert c.get("/api/people/p1/nights?count=-3", headers=H).status_code == 200
        assert len(c.get("/api/people/p1/nights?count=0", headers=H).json()) <= 1
        assert c.get("/api/history?zone=left&hours=-5", headers=H).status_code == 200
        assert c.get("/api/export?hours=-5", headers=H).status_code == 200
        assert c.get("/api/notify/log?limit=-1", headers=H).status_code == 200


def _dials(c, handler):
    ctl(c).store.put("ha_connection", {"url": "http://ha.test:8123", "token": "t"})
    c.app.state.oura.c.sources.http_transport = httpx.MockTransport(handler)
    return c.get("/api/dials", headers=H)


def test_dials_degrade_when_home_assistant_misbehaves(tmp_path):
    def boom(req):
        raise httpx.ReadError("connection reset")

    def not_json(req):
        return httpx.Response(200 if req.method == "GET" else 404, text="<html>nope</html>")

    with make(tmp_path) as c:
        for h in (boom, not_json):
            r = _dials(c, h)
            assert r.status_code == 200, r.text
        ctl(c).store.put("ha_connection", {"url": "not a url", "token": "t"})
        assert c.get("/api/dials", headers=H).status_code == 200


def test_wearable_import_runs_off_the_event_loop(tmp_path, monkeypatch):
    import embertide.autopilot_api as AP
    seen = []
    real = AP._import_wearable

    def spy(c, p, b):
        try:
            asyncio.get_running_loop()
            seen.append("loop")
        except RuntimeError:
            seen.append("thread")
        return real(c, p, b)

    monkeypatch.setattr(AP, "_import_wearable", spy)
    with make(tmp_path) as c:
        now = ctl(c).clock.now_ts()
        body = {"device": "Test", "metric": "resting_hr", "points": [[now - 3600, 55.0]]}
        assert c.post("/api/people/p1/wearable", headers=H, json=body).status_code == 200
        assert seen == ["thread"]


def test_empty_orion_fetch_is_retried_later(tmp_path, monkeypatch):
    import embertide.studio_api as SA
    calls = []

    class Resp:
        def __init__(self, data):
            self._d = data

        def json(self):
            return {"data": self._d}

    class Http:
        async def get(self, path, params=None):
            calls.append(path)
            return Resp({} if len(calls) == 1 else {"d": {"sessions": [{"hypnogram": [1]}]}})

    class Drv:
        id = "orion-cloud"
        _http = Http()

    clock = [1000.0]
    monkeypatch.setattr(SA.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr("embertide.engine.stages.parse_orion_hypnogram",
                        lambda h: [{"start": 1.0, "end": 2.0, "stage": "deep"}] if h else [])
    with make(tmp_path) as c:
        ctl(c).driver = Drv()
        assert c.get("/api/people/p1/stages", headers=H).status_code == 200
        assert len(calls) == 1
        c.get("/api/people/p1/stages", headers=H)
        assert len(calls) == 1                       # too soon to ask again
        clock[0] += 16 * 60
        c.get("/api/people/p1/stages", headers=H)
        assert len(calls) == 2                       # asked again: the night had stages by now
        clock[0] += 16 * 60
        c.get("/api/people/p1/stages", headers=H)
        assert len(calls) == 2                       # got them: done
