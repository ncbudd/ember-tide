"""Imported data must line up with the bed before Autopilot trusts it."""

from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from embertide import align as A
from embertide.app import create_app
from embertide.config import Config

TZ = ZoneInfo("America/New_York")
H = {"X-Embertide": "1"}


def test_time_formats():
    s = 1793948768
    assert A.parse_time(s, TZ) == s
    assert A.parse_time(s * 1000, TZ) == s                       # milliseconds
    assert A.parse_time("2026-11-06T02:00:00Z", TZ) == A.parse_time("2026-11-05T21:00:00", TZ)   # naive = household zone
    assert A.parse_time("garbage", TZ) is None and A.parse_time(True, TZ) is None and A.parse_time(-5, TZ) is None


def test_clean_rejects_and_dedupes():
    now = 1_800_000_000
    pts = [[now - 100, 34.1], [now - 100, 34.1], [(now - 50) * 1000, 34.2], [now + 3600, 34.0], ["nope", 34], [now - 10, 99]]
    out, rej = A.clean(pts, "skin_temp", TZ, now, existing={int(now - 200)})
    assert [round(t) for t, _ in out] == [now - 100, now - 50]
    assert rej == {"bad_time": 1, "future": 1, "too_old": 0, "out_of_range": 1, "duplicate": 1}


def night_hr(start, n=2000, seed=3):
    """A realistic-ish night: a slow random walk with sleep-cycle swings, never periodic."""
    import math
    import random
    rnd = random.Random(seed)
    v, out = 62.0, []
    for i in range(n):
        v += rnd.gauss(0, 0.6) - (v - 58) * 0.02
        out.append((start + i * 15, v + 3 * math.sin(i / 360)))
    return out


def test_best_lag_finds_clock_offset_and_timezone():
    bed = night_hr(1000)
    wear7 = [(t - 7 * 60, v) for t, v in bed[::4]]                 # watch 7 min behind
    lag, r, n = A.best_lag(wear7, bed)
    assert lag == 7 and r > 0.9
    wear1h = [(t + 3600, v) for t, v in bed[::4]]                  # export an hour off
    lag, r, n = A.best_lag(wear1h, bed)
    assert lag == -60 and "time-zone" in A.describe_lag(lag)


def make(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False
    return TestClient(create_app(cfg))


def test_import_lines_up_with_the_bed_and_teaches_skin_temp(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T09:00"}, headers=H)            # the night is over
        store = c.app.state.notifier.c.store
        start = A.parse_time("2026-11-05T22:20", TZ)
        bed = [(t, round(v)) for t, v in night_hr(start, n=1800)]
        store.telemetry([(t, "left", 1, 18.0, 18.2, 1, hr, 14, 0, 18.0, "deep", 1, "p1") for t, hr in bed])
        wear = [[t - 420, hr] for t, hr in bed[::2]]                # the watch runs 7 minutes behind
        r = c.post("/api/people/p1/wearable", json={"device": "Watch", "metric": "hr", "points": wear}, headers=H).json()
        assert any(n.get("lag_min") == 7 for n in r["nights"]), r["nights"]
        assert r["shift_applied_s"] == 420 and any("Shifted by +7" in x for x in r["notes"])
        again = c.post("/api/people/p1/wearable", json={"device": "Watch", "metric": "hr", "points": wear}, headers=H).json()
        assert again["stored"] == 0 and again["rejected"].get("duplicate"), (again["stored"], again["rejected"], again["notes"], r["stored"], r["rejected"])                 # same data twice: nothing doubled
        skin = [[t - 420, 34.2] for t, _ in bed[::8]]
        s = c.post("/api/people/p1/wearable", json={"device": "Watch", "metric": "skin_temp", "points": skin}, headers=H).json()
        assert s["shift_applied_s"] == 420 and any("learned from its heart rate" in x for x in s["notes"])
        assert s["nights"][0]["coverage_in_bed"] is not None
        chk = c.post("/api/people/p1/wearable", json={"device": "Other", "metric": "hr", "points": [[t + 3600, h] for t, h in bed[::2]], "align": "check"}, headers=H).json()
        assert chk["shift_applied_s"] == 0 and any("time-zone" in w for w in chk["warnings"])
        log = c.get("/api/people/p1/imports").json()
        assert len(log) == 4 and log[-1]["device"] == "Other"
