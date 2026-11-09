"""The web app's file serving never leaves the built web folder, however "../" is spelled; security headers are set."""

from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config


def test_no_path_traversal_and_headers(tmp_path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html>app</html>")
    (web / "icon.svg").write_text("<svg/>")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path / "data", "America/New_York", "sim", web, None, True
    (tmp_path / "data").mkdir()
    with TestClient(create_app(cfg)) as c:
        assert c.get("/icon.svg").text == "<svg/>"
        for p in ("/..%2fsecret.txt", "/%2e%2e/secret.txt", "/%2e%2e%2fsecret.txt", "/..%2F..%2F" + str(secret).lstrip("/").replace("\\", "/"),
                  "/../secret.txt", "/assets/..%2f..%2fsecret.txt"):
            r = c.get(p)
            assert "TOPSECRET" not in r.text, p
        r = c.get("/")
        assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"


def test_huge_bodies_are_refused(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, True
    with TestClient(create_app(cfg)) as c:
        r = c.post("/api/client-error", content=b"x" * (600 * 1024), headers={"content-type": "application/json"})
        assert r.status_code == 413
        r = c.post("/api/hooks/ab/aaaaaaaaaaaaaaaaaaaa", content=b"{" * (2 * 1024 * 1024), headers={"content-type": "application/json"})
        assert r.status_code == 413
        def gen():                                             # streamed without a length
            for _ in range(20):
                yield b"x" * 65536
        r = c.post("/api/client-error", content=gen(), headers={"content-type": "application/json"})
        assert r.status_code in (400, 413), r.status_code     # refused part-way (FastAPI reports it as a bad body)
