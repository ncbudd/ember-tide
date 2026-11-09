"""OPTIONAL private fixture: a real Health Connect export (a watch's heart rate and sleep stages), replayed through
imports, alignment, dedupe, the baseline and Autopilot. The data is personal and is never in this repo; these tests
skip cleanly when it is absent. To use your own, point ET_REAL_HC at the JSON file
({"tz", "hr": [[ts, bpm], ...], "stages": [{"start", "end", "stage"}, ...]}). Without it these tests skip.

The bed side is simulated from the real night: in bed during the sleep session, the bed strip's heart rate is
the real watch heart rate plus a little noise. Then the watch data is imported with known clock errors, and the
import must find and undo them.
"""

import json
import os
import random
import statistics
from pathlib import Path

import pytest

from test_autopilot_suite import H, make

PATH = Path(os.environ.get("ET_REAL_HC", Path(__file__).resolve().parents[3] / "ember-tide-private" / "data" / "hc-real-2026-11.json"))


@pytest.fixture(scope="module")
def real():
    if not PATH.exists():
        pytest.skip("no real Health Connect data on this machine")
    d = json.loads(PATH.read_text())
    st = d["stages"]
    session = (min(x["start"] for x in st), max(x["end"] for x in st))
    hr = [(float(t), float(v)) for t, v in d["hr"]]
    return {"hr": hr, "session": session, "stages": st}


def bed_from_real(ctl, real, person="p1", zone="left", noise=2.0, seed=1):
    """Telemetry rows every 30 s across the real night: occupied in the session, bed HR = real HR (+ noise)."""
    a, b = real["session"]
    hr = [x for x in real["hr"] if a - 3 * 3600 <= x[0] <= b + 3 * 3600]
    rnd = random.Random(seed)
    rows, j = [], 0
    t = a - 2 * 3600
    while t <= b + 2 * 3600:
        while j + 1 < len(hr) and hr[j + 1][0] <= t:
            j += 1
        inb = a <= t <= b
        v = hr[j][1] + rnd.uniform(-noise, noise) if inb and hr and abs(hr[j][0] - t) < 300 else None
        rows.append((t, zone, 1, 18.0, 18.2, int(inb), v, 14 if inb else None, 0 if inb else 5, 18.0, "deep" if inb else None, int(inb), person))
        t += 30
    ctl.store.telemetry(rows)


def night_hr(real, pad=3600):
    a, b = real["session"]
    return [x for x in real["hr"] if a - pad <= x[0] <= b + pad]


def test_real_night_shape(real):
    a, b = real["session"]
    hrs = [v for t, v in real["hr"] if a + 1800 <= t <= b - 1800]
    assert 6 <= (b - a) / 3600 <= 11 and len(hrs) > 100
    assert 35 <= statistics.median(hrs) <= 80, "sleeping heart rate should be in a normal range"
    assert {s["stage"] for s in real["stages"]} <= {"awake", "light", "deep", "rem"}


@pytest.mark.parametrize("offset_min", [0, 7, -7, 60, -60, 300])
def test_real_watch_clock_error_is_found_and_undone(tmp_path, real, offset_min):
    with make(tmp_path) as c:
        ctl = c.app.state.notifier.c
        bed_from_real(ctl, real)
        pts = [[t - offset_min * 60, v] for t, v in night_hr(real)]          # the watch's clock is wrong by offset_min
        r = c.post("/api/people/p1/wearable", headers=H, json={"device": "Galaxy Watch", "metric": "hr", "points": pts}).json()
        shift = r.get("shift_applied_s") or 0
        assert abs(shift - offset_min * 60) <= 120, (offset_min, shift, r.get("warnings"), r.get("notes"))


def test_real_hr_from_two_apps_is_stored_once(tmp_path, real):
    with make(tmp_path) as c:
        ctl = c.app.state.notifier.c
        bed_from_real(ctl, real)
        pts = [[t, v] for t, v in night_hr(real)]
        a = c.post("/api/people/p1/wearable", headers=H, json={"device": "Samsung Health", "metric": "hr", "points": pts, "align": "off"}).json()
        b = c.post("/api/people/p1/wearable", headers=H, json={"device": "Health Sync", "metric": "hr", "points": pts, "align": "off"}).json()
        assert a["stored"] == len(pts) and b["stored"] == 0
        assert any("already there" in n for n in b["notes"])
        now = real["session"][1] + 2 * 3600
        assert len(ctl.autopilot.wear_hr(ctl.household.people[0], now - 12 * 3600, now)) == len(pts)


def test_someone_elses_real_hr_is_not_shifted(tmp_path, real):
    """The partner's heart rate (here: the real night, mirrored in time) must not be 'aligned' onto this bed."""
    with make(tmp_path) as c:
        ctl = c.app.state.notifier.c
        bed_from_real(ctl, real)
        hr = night_hr(real)
        a, b = hr[0][0], hr[-1][0]
        other = [[a + (b - t), v] for t, v in hr]                            # same values, time reversed
        r = c.post("/api/people/p1/wearable", headers=H, json={"device": "Partner Ring", "metric": "hr", "points": other}).json()
        assert abs(r.get("shift_applied_s") or 0) < 60, (r.get("shift_applied_s"), r.get("nights"))


def test_autopilot_stays_calm_on_a_real_ordinary_night(tmp_path, real):
    """An ordinary real night with no taps and a comfortable bed: Autopilot replay should suggest at most one change."""
    from embertide.models import AutopilotPolicy
    with make(tmp_path) as c:
        ctl = c.app.state.notifier.c
        bed_from_real(ctl, real)
        c.post("/api/people/p1/wearable", headers=H, json={"device": "Galaxy Watch", "metric": "hr", "points": [[t, v] for t, v in night_hr(real)], "align": "off"})
        p = ctl.household.people[0]
        a, b = real["session"]
        rec = ctl.autopilot.replay(p, a, b, AutopilotPolicy(learn_nights=0), wake=b)
        acted = [x for x in rec if x["status"] in ("suggested", "applied")]
        assert len(acted) <= 1, [(x["direction"], [r["signal"] for r in x["reasons"]]) for x in acted]


def sleep_windows(real):
    """Every night in the real heart rate: the quietest 7-hour stretch between 9 PM and 11 AM (stages exist for only
    one night, so the others are found from heart rate)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")
    hr = real["hr"]
    first = datetime.fromtimestamp(hr[0][0], tz).date()
    last = datetime.fromtimestamp(hr[-1][0], tz).date()
    out, d = [], first
    while d < last:
        a0 = datetime(d.year, d.month, d.day, 21, tzinfo=tz).timestamp()
        pts = [x for x in hr if a0 <= x[0] <= a0 + 14 * 3600]
        if len(pts) > 200:
            best = min(range(0, 7 * 3600 + 1, 900), key=lambda off: statistics.mean(
                [v for t, v in pts if a0 + off <= t <= a0 + off + 7 * 3600] or [999]))
            out.append((a0 + best, a0 + best + 7 * 3600))
        d += timedelta(days=1)
    return out


def real_week(real, nights=7, seed=3):
    """The watch was only worn through one night, so a week is built from it: the real night moved back day by
    day, starting up to ±30 min earlier or later and with heart rate a few beats higher or lower each night."""
    rnd = random.Random(seed)
    a, b = real["session"]
    base = night_hr(real)
    hr, wins = [], []
    for k in range(nights, 0, -1):
        dt = -k * 86400 + rnd.randint(-30, 30) * 60
        bump = rnd.uniform(-3, 3)
        hr += [(t + dt, round(v + bump, 1)) for t, v in base]
        wins.append((a + dt, b + dt))
    return dict(real, hr=sorted(hr)), wins


def test_real_week_learning_baseline_and_calm(tmp_path, real):
    from embertide.models import AutopilotPolicy
    found = sleep_windows(real)
    assert len(found) >= 1                                          # the one real night is found from heart rate alone
    wk, wins = real_week(real)
    with make(tmp_path) as c:
        ctl = c.app.state.notifier.c
        for i, (a, b) in enumerate(wins):
            bed_from_real(ctl, dict(wk, session=(a, b)), seed=i)
        real = wk
        pts = [[t, v] for t, v in real["hr"]]
        c.post("/api/people/p1/wearable", headers=H, json={"device": "Galaxy Watch", "metric": "hr", "points": pts, "align": "off"})
        p = ctl.household.people[0]
        p.settings.autopilot.learn_nights = len(wins)
        now = wins[-1][1] + 6 * 3600
        lr = ctl.autopilot.learning(p, now)
        assert lr["have"] >= len(wins) - 1, lr                     # the newest may still count as "in progress"
        base = ctl.autopilot.baseline(p, now)
        sleeping = [v for a, b in wins for t, v in real["hr"] if a + 1800 <= t <= b]
        assert base and abs(base["hr"] - statistics.median(sleeping)) <= 6, (base, statistics.median(sleeping))
        for a, b in wins:
            rec = ctl.autopilot.replay(p, a, b, AutopilotPolicy(learn_nights=0), wake=b)
            assert len([x for x in rec if x["status"] in ("suggested", "applied")]) <= 1


def test_weight_from_health_connect_sets_scale_person_weight(tmp_path):
    with make(tmp_path) as c:
        ctl = c.app.state.notifier.c
        import time
        now = time.time()
        r = c.post("/api/people/p1/wearable", headers=H, json={"device": "Scale", "metric": "weight_kg", "points": [[now - 86400, 81.2], [now - 3600, 80.6]]})
        assert r.status_code == 200 and ctl.sources.calibration.person_kg["p1"] == 80.6


def test_hourly_day_totals_stamped_at_end_of_hour_are_kept(tmp_path):
    """Health Connect hourly steps are stamped at the end of the hour, so the current hour's total looks up to an
    hour ahead: kept (at now), not rejected as 'future'."""
    import time
    with make(tmp_path) as c:
        now = time.time()
        r = c.post("/api/people/p1/wearable", headers=H, json={"device": "Phone", "metric": "steps",
                                                               "points": [[now - 3000, 400], [now + 1800, 120]]}).json()
        assert r["stored"] == 2 and not r.get("rejected", {}).get("future")
