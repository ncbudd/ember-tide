"""°F is whole degrees only: the old half-degree setting is ignored when loaded or sent, and a step is 1 °F."""

from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.models import PersonSettings
from embertide.units import f_to_c, fmt_temp

H = {"X-Embertide": "1"}


def _cfg(tmp_path) -> Config:
    cfg = Config()
    cfg.data_dir = tmp_path
    cfg.tz = "America/New_York"
    cfg.bed = "sim"
    cfg.web_dir = tmp_path / "noweb"
    cfg.llm_url = None
    cfg.auth = False
    return cfg


def test_saved_half_degree_setting_is_ignored_and_f_shows_whole_degrees():
    s = PersonSettings(**{"units": "F", "step_f": 0.5})          # settings saved by an older version
    assert "step_f" not in s.model_dump()
    assert fmt_temp(f_to_c(70.0), "F") == "70°F"
    assert fmt_temp(f_to_c(70.4), "F") == "70°F"
    assert fmt_temp(21.4, "F") == "71°F"                         # an old 70.5 °F target shows as a whole degree


def test_older_app_sending_step_f_still_saves_other_settings(tmp_path):
    with TestClient(create_app(_cfg(tmp_path))) as c:
        r = c.patch("/api/people/p1/settings", json={"units": "F", "step_f": 0.5, "clock": "24"}, headers=H)
        assert r.status_code == 200
        p1 = next(p for p in r.json()["people"] if p["id"] == "p1")
        assert p1["settings"]["clock"] == "24" and "step_f" not in p1["settings"]
