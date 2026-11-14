"""Half-degree Fahrenheit steps: setting validation, persistence, formatting, and +0.5 °F adjust."""

import pytest
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


def test_step_f_validation_and_formatting():
    assert PersonSettings().step_f == 1
    assert PersonSettings(step_f=0.5).step_f == 0.5
    assert PersonSettings(step_f=1.0).step_f == 1
    for bad in (0.25, 2, "0.5", True):
        with pytest.raises(ValueError):
            PersonSettings(step_f=bad)  # type: ignore[arg-type]
    assert fmt_temp(f_to_c(70.5), "F", half_f=True) == "70.5°F"
    assert fmt_temp(f_to_c(70.0), "F") == "70°F"
    # controller stores targets rounded to 0.1 °C (70.5 °F ≈ 21.4 °C), which still renders as 70.5°F
    assert fmt_temp(21.4, "F", half_f=True) == "70.5°F"


def test_step_f_saved_and_half_degree_adjust_moves_target(tmp_path, monkeypatch):
    import embertide.engine.controller as ctlmod
    monkeypatch.setattr(ctlmod, "MIN_CMD_GAP_S", 0)
    with TestClient(create_app(_cfg(tmp_path))) as c:
        r = c.patch("/api/people/p1/settings", json={"units": "F", "step_f": 0.5}, headers=H)
        assert r.status_code == 200
        p1 = next(p for p in r.json()["people"] if p["id"] == "p1")
        assert p1["settings"]["step_f"] == 0.5

        base = f_to_c(70.0)
        set_r = c.post("/api/people/p1/adjust", json={"temp_c": base, "until": "next_phase"}, headers=H)
        assert set_r.status_code == 200
        start_c = next(p for p in set_r.json()["people"] if p["id"] == "p1")["night"]["target_c"]

        nudge = c.post("/api/people/p1/adjust", json={"delta_c": 5 / 18, "until": "next_phase"}, headers=H)
        assert nudge.status_code == 200
        after = next(p for p in nudge.json()["people"] if p["id"] == "p1")
        # Engine stores 0.01 °C (~+0.28 °C), bed hardware setpoint (after background set_zone) rounds to 0.1 °C
        assert after["night"]["target_c"] - start_c == pytest.approx(5 / 18, abs=0.02)
        # Trigger a sim step so snapshot reflects the background driver setpoint update.
        refreshed = c.post("/api/sim", json={}, headers=H)
        assert refreshed.status_code == 200
        bed_sp = next(p for p in c.get("/api/snapshot").json()["people"] if p["id"] == "p1")["bed"]["setpoint_c"]
        assert bed_sp - start_c == pytest.approx(5 / 18, abs=0.06)
