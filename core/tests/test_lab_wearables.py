"""Lab: watch and ring data that doesn't line up with the bed.

Clock offsets, time-zone mistakes, gaps, duplicates, next-morning imports, two wearables that disagree, relative vs
absolute skin temperature, and a watch whose heart rate isn't the sleeper's. The import must line things up (or say it
can't), and misaligned data must never push Autopilot the wrong way.
"""

import math
import random

import pytest

from embertide.autopilot_api import WearableBody, _import_wearable
from embertide.engine.autopilot import signals_at
from lab import Lab, Sleeper, ts
from datetime import UTC

NIGHT = "2026-11-05T22:20"


def walk_hr(start, n=1800, step=15, seed=3):
    """A realistic-ish heart rate: slow random walk plus a gentle swing; not periodic, so one lag fits best."""
    rnd = random.Random(seed)
    v, out = 62.0, []
    for i in range(n):
        v += rnd.gauss(0, 0.6) - (v - 58) * 0.02
        out.append((start + i * step, v + 3 * math.sin(i / 360)))
    return out


def put_bed_night(lab, start=NIGHT, n=1800, seed=3, pid="p1", zone="left"):
    s = ts(start)
    bed = [(t, round(v)) for t, v in walk_hr(s, n=n, seed=seed)]
    lab.store.telemetry([(t, zone, 1, 18.0, 18.2, 1, hr, 14, 0, 18.0, "deep", 1, pid) for t, hr in bed])
    return bed


def imp(lab, metric, points, device="Watch", **kw):
    return _import_wearable(lab.c, lab.p1, WearableBody(device=device, metric=metric, points=points, **kw))


def lab_at(tmp_path, now="2026-11-06T18:00"):
    lab = Lab(tmp_path, now)
    return lab


# ───────── clock offsets and time-zone mistakes ─────────
@pytest.mark.parametrize("offset_min", [0, 2, 7, -7, 60, -60, 300, -300, -780])
def test_hr_import_finds_the_shift(tmp_path, offset_min):
    """The watch's times are `offset_min` off (positive = its clock is ahead). The import lines it up."""
    lab = lab_at(tmp_path, "2026-11-07T12:00")          # long after the night, so even +13 h shifted times aren't in the future
    bed = put_bed_night(lab)
    wear = [[t + offset_min * 60, hr] for t, hr in bed[::2]]
    r = imp(lab, "hr", wear)
    lags = [n.get("lag_min") for n in r["nights"] if n.get("lag_min") is not None]
    assert lags and all(l == -offset_min for l in lags), (r["nights"], r["warnings"])
    if abs(offset_min) >= 3:
        assert r["shift_applied_s"] == pytest.approx(-offset_min * 60)
    stored = lab.store.samples("wear:p1:hr:Watch", 0, 1e12)
    assert stored and abs(stored[0][0] - bed[0][0]) <= 180         # on the bed's clock (shifts under 3 min are left alone)


def test_ahead_clock_imported_next_morning(tmp_path):
    lab = lab_at(tmp_path, "2026-11-06T09:00")
    bed = put_bed_night(lab)
    r = imp(lab, "hr", [[t + 5 * 3600, hr] for t, hr in bed[::2]])
    assert r["stored"] >= 0.9 * len(bed[::2]), (r["stored"], r["rejected"])
    assert r["shift_applied_s"] == pytest.approx(-5 * 3600)


def test_milliseconds_and_iso_strings(tmp_path):
    from datetime import datetime
    lab = lab_at(tmp_path)
    bed = put_bed_night(lab)
    ms = [[int(t * 1000), hr] for t, hr in bed[::4]]
    iso = [[datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z"), hr] for t, hr in bed[1::4]]
    r1, r2 = imp(lab, "hr", ms, device="A"), imp(lab, "hr", iso, device="B")
    assert r1["stored"] == len(ms) and r2["stored"] == len(iso)
    assert all(n.get("lag_min") == 0 for n in r1["nights"] + r2["nights"])


# ───────── gaps, duplicates, partial coverage, late arrival ─────────
def test_duplicates_inside_one_import_and_across_imports(tmp_path):
    lab = lab_at(tmp_path)
    bed = put_bed_night(lab)
    pts = [[t, hr] for t, hr in bed[::2]]
    r = imp(lab, "hr", pts + pts[:100])
    assert r["stored"] == len(pts) and r["rejected"]["duplicate"] == 100
    again = imp(lab, "hr", pts)
    assert again["stored"] == 0


def test_partial_coverage_is_flagged(tmp_path):
    lab = lab_at(tmp_path)
    bed = put_bed_night(lab)
    r = imp(lab, "hr", [[t, hr] for t, hr in bed[:500:2]])          # first ~2 hours of a 7.5 h night
    assert any("covers only" in w for w in r["notes"]), r["notes"]


def test_gappy_watch_still_aligns(tmp_path):
    lab = lab_at(tmp_path)
    bed = put_bed_night(lab)
    pts = [[t + 420, hr] for i, (t, hr) in enumerate(bed[::2]) if (i // 60) % 3 != 1]   # a third missing, in stretches
    r = imp(lab, "hr", pts)
    assert any(n.get("lag_min") == -7 for n in r["nights"]), r["nights"]


def test_wrong_person_heart_rate_is_not_shifted(tmp_path):
    lab = lab_at(tmp_path)
    put_bed_night(lab, seed=3)
    other = walk_hr(ts(NIGHT), n=1800, seed=99)                   # someone else's night
    r = imp(lab, "hr", [[t, round(v)] for t, v in other[::2]])
    assert r["shift_applied_s"] == 0
    assert any("doesn't follow the bed" in w for w in r["warnings"]) or all((n.get("match") or 0) < 0.6 for n in r["nights"])


def test_skin_borrows_the_shift_learned_from_heart_rate(tmp_path):
    lab = lab_at(tmp_path)
    bed = put_bed_night(lab)
    imp(lab, "hr", [[t + 3600, hr] for t, hr in bed[::2]])
    s = imp(lab, "skin_temp", [[t + 3600, 34.2] for t, _ in bed[::8]])
    assert s["shift_applied_s"] == pytest.approx(-3600)
    first = lab.store.samples("wear:p1:skin_temp:Watch", 0, 1e12)[0][0]
    assert abs(first - bed[0][0]) <= 120


def test_skin_from_a_device_never_seen_with_hr_is_not_guessed(tmp_path):
    lab = lab_at(tmp_path)
    put_bed_night(lab)
    s = imp(lab, "skin_temp", [[ts(NIGHT) + i * 120 + 3600, 34.0] for i in range(200)], device="Ring")
    assert s["shift_applied_s"] == 0


# ───────── relative vs absolute skin temperature ─────────
def test_relative_fahrenheit_deviation(tmp_path):
    lab = lab_at(tmp_path)
    put_bed_night(lab)
    imp(lab, "skin_temp", [[ts(NIGHT) + 3600 + i * 300, 0.9] for i in range(10)], device="Ring", unit="F", relative=True)
    v = lab.store.samples("wear:p1:skin_temp:Ring", 0, 1e12)[0][1]
    assert v == pytest.approx(34.5, abs=0.01)                   # +0.9 °F = +0.5 °C on the nominal 34


def test_absolute_fahrenheit(tmp_path):
    lab = lab_at(tmp_path)
    put_bed_night(lab)
    imp(lab, "skin_temp", [[ts(NIGHT) + 3600 + i * 300, 93.2] for i in range(10)], device="Watch2", unit="F")
    assert lab.store.samples("wear:p1:skin_temp:Watch2", 0, 1e12)[0][1] == pytest.approx(34.0, abs=0.01)


def test_absurd_relative_values_dropped(tmp_path):
    lab = lab_at(tmp_path)
    put_bed_night(lab)
    r = imp(lab, "skin_temp", [[ts(NIGHT) + 3600, 12.0], [ts(NIGHT) + 3900, 0.2]], device="Ring", relative=True)
    assert r["stored"] == 1


# ───────── two wearables disagree ─────────
def _two_devices_window(lab):
    """Ring (absolute, reads 35.6 °C) for the first half of the night, watch (34.0 °C) for the second half. Neither
    changes at all: the sleeper's skin temperature is flat."""
    s = ts(NIGHT)
    lab.store.samples_many([(s + 1500 + i * 120, "wear:p1:skin_temp:Ring", 35.6) for i in range(80)])
    lab.store.samples_many([(s + 1500 + 80 * 120 + i * 120, "wear:p1:skin_temp:Watch", 34.0) for i in range(60)])
    return s


# fixed: devices are level-matched before merging
def test_two_wearables_with_different_levels_dont_fake_a_change(tmp_path):
    from embertide.engine.autopilot import Window
    lab = lab_at(tmp_path, "2026-11-06T12:00")
    s = _two_devices_window(lab)
    now = s + 1500 + 140 * 120
    skin = lab.c.autopilot.skin(lab.p1, now - 6 * 3600, now)
    w = Window(now=now, rows=[], in_bed=s, state="rhythm", phase="deep", flash_active=False, events=[], skin=skin,
               persona="neutral", wake=None)
    sd = signals_at(w)["skin_delta"]
    assert sd is None or abs(sd) < 0.3, sd


def test_live_and_imported_skin_both_reach_autopilot(tmp_path):
    from embertide.sources import SourceConfig
    lab = lab_at(tmp_path)
    s = ts(NIGHT)
    lab.sources.configs.append(SourceConfig(id="liveskin", name="Live skin", driver="sim", role="skin_temp", zone="left", person="p1", kind="wearable"))
    lab.store.samples_many([(s + i * 300, "liveskin", 34.0) for i in range(10)])
    lab.store.samples_many([(s + 150 + i * 300, "wear:p1:skin_temp:Ring", 34.1) for i in range(10)])
    got = lab.c.autopilot.skin(lab.p1, s - 10, s + 3600)
    assert len(got) == 20


def test_other_persons_wearable_is_not_used(tmp_path):
    lab = lab_at(tmp_path)
    s = ts(NIGHT)
    lab.store.samples_many([(s + i * 300, "wear:p2:skin_temp:Ring", 36.0) for i in range(10)])
    assert lab.c.autopilot.skin(lab.p1, s - 10, s + 3600) == []


# ───────── misaligned data must not cause a wrong decision ─────────
async def test_misaligned_watch_is_lined_up_before_hindsight(tmp_path):
    """A hot sleeper's watch runs an hour ahead. After importing HR (which teaches the offset) and then skin, the
    hindsight re-check sees the warm stretch where it really happened: no decisions before the sleeper was warm."""
    from embertide.autopilot_api import _recheck
    lab = Lab(tmp_path, "2026-11-05T21:00", sleepers={"left": Sleeper(pref_c={"late": -2.0})})
    lab.p1.settings.autopilot.learn_skip = True
    lab.p1.settings.autopilot.mode = "off"                       # nothing live: the import is the only evidence
    await lab.until("2026-11-06T07:30", dt=60)
    rows = lab.store.history("left", ts("2026-11-05T21:00"), ts("2026-11-06T07:30"), person="p1")
    hr = [[r["ts"] + 3600, r["hr"]] for r in rows if r.get("hr")]
    lab.clock.jump_to(ts("2026-11-06T20:00"))
    r = imp(lab, "hr", hr)
    assert r["shift_applied_s"] == pytest.approx(-3600, abs=180), (r["nights"], r["warnings"])
    # skin: warm from 03:00 (the late phase) on the real clock, written with the watch's +1 h error
    skin = [[t + 3600, 34.0 + (0.9 if t >= ts("2026-11-06T03:00") else 0.0)] for t in range(int(ts("2026-11-05T22:40")), int(ts("2026-11-06T06:30")), 120)]
    s = imp(lab, "skin_temp", skin)
    assert s["shift_applied_s"] == pytest.approx(-3600, abs=180)
    rec = _recheck(lab.c, lab.p1, "2026-11-05")
    decs = lab.store.get("ap_recheck:p1:2026-11-05")["decisions"]
    warm = [d for d in decs if any(x["signal"] == "skin_temp" for x in d["reasons"])]
    assert rec["used_skin"]
    assert all(d["ts"] >= ts("2026-11-06T03:00") for d in warm), [d["ts"] - ts("2026-11-06T03:00") for d in warm]


def test_hr_from_a_real_simulated_night_aligns_despite_sleep_cycles(tmp_path):
    """Sleep-stage cycles repeat every ~90 min, so shifts of ±90 min can look almost as good. The import must still
    pick the true offset."""
    import asyncio
    lab = Lab(tmp_path, "2026-11-05T21:00", sleepers={"left": Sleeper()})
    lab.p1.settings.autopilot.mode = "off"
    asyncio.get_event_loop_policy()
    asyncio.run(lab.until("2026-11-06T07:00", dt=30))
    rows = lab.store.history("left", ts("2026-11-05T21:00"), ts("2026-11-06T07:00"), person="p1")
    hr = [(r["ts"], r["hr"]) for r in rows if r.get("hr")]
    lab.clock.jump_to(ts("2026-11-06T20:00"))
    r = imp(lab, "hr", [[t - 25 * 60, v] for t, v in hr])
    lags = [n.get("lag_min") for n in r["nights"] if n.get("lag_min") is not None]
    assert lags == [25], (r["nights"], r["warnings"])
