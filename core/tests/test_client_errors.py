"""App crash reports: open but small and rate-limited, owner-only to read, no query strings kept."""

from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from test_autopilot_suite import make


def test_report_store_and_read(tmp_path):
    with make(tmp_path) as c:
        r = c.post("/api/client-error", json={"kind": "render", "message": "Maximum update depth exceeded", "stack": "at TodayCard",
                                              "route": "/sleep?token=secret#x", "version": "0.1.0", "screen": "412x915@2.6"})
        assert r.status_code == 200
        got = c.get("/api/client-errors").json()
        assert got[0]["message"].startswith("Maximum") and got[0]["route"] == "/sleep"
        assert c.post("/api/client-error", json={"message": "x" * 5000}).status_code == 422
        for _ in range(10):
            last = c.post("/api/client-error", json={"message": "again"})
        assert last.status_code == 429
        assert c.delete("/api/client-errors").status_code == 200 and c.get("/api/client-errors").json() == []


def test_open_to_post_but_owner_only_to_read_with_sign_in(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", True
    with TestClient(create_app(cfg)) as c:
        assert c.post("/api/client-error", json={"message": "on the sign-in screen"}).status_code == 200
        assert c.get("/api/client-errors").status_code == 401
