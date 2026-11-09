"""Fixing a night, which source to trust, the bed-vs-wearable comparison, the learned in-bed offset and the in/out of
bed tuning."""

from datetime import date

import pytest

from embertide.engine import compare as C
from embertide.engine import petq
from embertide.engine.autopilot import evaluate
from embertide.engine.summary import summarize
from embertide.models import AutopilotPolicy, Pet
from test_autopilot_suite import H, NOW, make, night_rows, win
from test_scenarios import Night, ts

D = "2026-11-05"


async def _night(tmp_path, **kw) -> Night:
    n = Night(tmp_path, "2026-11-05T21:00", echo=False, **kw)
    n.present("left", False)
    await n.until("2026-11-05T22:30", dt=60)
    n.present("left", True)
    await n.until("2026-11-06T02:00", dt=60)
    n.present("left", False)                                   # up 6 min at 2 AM
    await n.until("2026-11-06T02:06", dt=60)
    n.present("left", True)
    await n.until("2026-11-06T06:30", dt=60)
    n.present("left", False)
    await n.until("2026-11-06T08:00", dt=60)
    return n


def _sum(n, raw=False):
    return summarize(n.store, n.p1, date.fromisoformat(D), n.clock.tz, n.clock.now_ts(), raw=raw)


async def test_corrections_win_and_raw_is_kept(tmp_path):
    n = await _night(tmp_path)
    bed = _sum(n)
    assert bed["times_up"] == 1 and abs(bed["in_bed_at"] - ts("2026-11-05T22:30")) <= 120
    fix = C.clean_fix({"in_bed_at": ts("2026-11-05T22:20"), "out_at": ts("2026-11-06T06:45"),
                       "ups_wrong": [{"start": ts("2026-11-06T01:59"), "end": ts("2026-11-06T02:07")}],
                       "ups_added": [{"start": ts("2026-11-06T04:00"), "end": ts("2026-11-06T04:10")}]},
                      *(ts("2026-11-05T20:00"), ts("2026-11-06T09:00")), [])
    n.store.put(f"night_fix:p1:{D}", fix)
    s = _sum(n)
    assert s["in_bed_at"] == ts("2026-11-05T22:20") and s["out_at"] == ts("2026-11-06T06:45")
    assert s["raw"]["in_bed_at"] == bed["in_bed_at"]                       # the bed's own value stays visible
    assert s["times_up"] == 1 and "in_bed_at" in s["corrected"]
    ups = [m for m in s["moments"] if m["kind"] == "out_of_bed"]
    assert len(ups) == 1 and ups[0]["corrected"] and ups[0]["minutes"] == 10
    assert _sum(n, raw=True)["in_bed_at"] == bed["in_bed_at"]               # raw data never overwritten
    rows = n.store.history("left", ts("2026-11-05T22:00"), ts("2026-11-05T23:00"), person="p1")
    assert rows and rows[0]["ts"] < ts("2026-11-05T22:30")


def test_fix_validation():
    lo, hi = 1_000_000.0, 1_050_000.0
    with pytest.raises(ValueError):
        C.clean_fix({"in_bed_at": lo + 5000, "out_at": lo + 4000}, lo, hi, [])        # out of order
    with pytest.raises(ValueError):
        C.clean_fix({"in_bed_at": lo - 30 * 3600}, lo, hi, [])                       # another night
    with pytest.raises(ValueError):
        C.clean_fix({"stretches": [{"start": lo, "end": lo + 60, "who": "pet", "pet": "zzz"}]}, lo, hi, [])
    ok = C.clean_fix({"stretches": [{"start": lo, "end": lo + 600, "who": "me"}]}, lo, hi, [])
    assert ok["stretches"][0]["who"] == "me"


def test_learned_offset_needs_three_nights_and_is_used():
    from embertide.store import Store
    import tempfile, pathlib
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "s.db")
    for i, d in enumerate(("2026-11-01", "2026-11-02")):
        C.note_offset(s, "p1", d, 10_000 + 600, 10_000, None, None)
    assert "in" not in C.offset(s, "p1")
    C.note_offset(s, "p1", "2026-11-03", 10_000 + 660, 10_000, None, None)
    assert C.offset(s, "p1")["in"] == 600
    bed = {"in_bed_at": 50_600, "fell_asleep_est": 51_000, "out_at": 80_000}
    t = C.chosen_times(bed, None, None, "wearable", C.offset(s, "p1"))
    assert t["in_bed_at"] == {"ts": 50_000, "source": "bed, adjusted"}
    C.note_offset(s, "p1", "2026-11-04", 10_000 + 60, 10_000, None, None)
    C.note_offset(s, "p1", "2026-11-05", 10_000 + 30, 10_000, None, None)
    C.note_offset(s, "p1", "2026-11-06", 10_000, 10_000, None, None)
    C.note_offset(s, "p1", "2026-11-07", 10_000 + 20, 10_000, None, None)
    assert C.offset(s, "p1")["in"] == 0                                     # mostly a small lag now: ignored


def test_which_to_trust_picks_the_times():
    bed = {"in_bed_at": 1000.0, "fell_asleep_est": 2000.0, "out_at": 9000.0}
    wear = {"start": 1600.0, "asleep_at": 2600.0, "woke_at": 8400.0, "end": 8800.0}
    w = C.chosen_times(bed, wear, None, "wearable", {})
    b = C.chosen_times(bed, wear, None, "bed", {})
    m = C.chosen_times(bed, wear, None, "balanced", {})
    assert w["in_bed_at"] == {"ts": 1600, "source": "watch"} and b["in_bed_at"] == {"ts": 1000, "source": "bed"}
    assert m["in_bed_at"] == {"ts": 1300, "source": "both"} and m["asleep_at"]["source"] == "watch"
    you = C.chosen_times(bed, wear, {"in_bed_at": 1200}, "wearable", {})
    assert you["in_bed_at"] == {"ts": 1200, "source": "you"}                 # a correction wins over both


def test_trust_setting_weights_autopilot_heart_rate():
    hr_rows = night_rows(restless_last10=True)
    wear = [(NOW - 3 * 3600 + 1500 + i * 120, 55.0) for i in range(60)] + [(NOW - 240 + i * 60, 68.0) for i in range(4)]

    def src(pref):
        d = evaluate(win(rows=hr_rows, wear_hr=wear), AutopilotPolicy(trust_more=pref), 17.0, [])
        return next((r.detail for r in d.reasons if r.signal == "hr"), "")
    assert "watch" in src("wearable")
    assert AutopilotPolicy().trust_more == "wearable"                        # most wearables beat a bed strip
    from embertide.engine.compare import PREFER
    assert PREFER["bed"][0] > PREFER["bed"][1] and PREFER["wearable"][1] > PREFER["wearable"][0]


async def test_comparison_lines_up_the_wearable_night(tmp_path):
    n = await _night(tmp_path)
    segs = [{"start": ts("2026-11-05T22:40"), "end": ts("2026-11-05T22:55"), "stage": "awake"},
            {"start": ts("2026-11-05T22:55"), "end": ts("2026-11-06T02:00"), "stage": "light"},
            {"start": ts("2026-11-06T02:00"), "end": ts("2026-11-06T02:10"), "stage": "awake"},
            {"start": ts("2026-11-06T02:10"), "end": ts("2026-11-06T06:20"), "stage": "deep"},
            {"start": ts("2026-11-06T06:20"), "end": ts("2026-11-06T06:35"), "stage": "awake"}]
    n.store.put_stages("p1", "hc", "Galaxy Watch · Health Connect", segs, segs[0]["start"], segs[-1]["end"])
    cmp = C.compare_night(n.c, n.p1, D)
    assert cmp["has_wearable"] and cmp["lines"][0].startswith("Watch: in bed 10:40 PM · Bed: in bed 10:3")
    assert any(l.startswith("Watch: asleep 10:55 PM") for l in cmp["lines"])
    assert cmp["times"]["in_bed_at"]["source"] == "watch" and "Using your watch for stages" in cmp["using"]
    assert 0 <= cmp["stage_agreement"] <= 100
    n.p1.settings.autopilot.trust_more = "bed"
    cmp2 = C.compare_night(n.c, n.p1, D)
    assert cmp2["times"]["in_bed_at"]["source"] == "bed" and "Using the bed for stages" in cmp2["using"]
    # the bed said "in bed" ~10 min before the watch: noted for the learned offset
    assert C.offset(n.store, "p1")["nights"] == 1


async def test_pet_stretch_correction_teaches_the_pet(tmp_path):
    n = await _night(tmp_path)
    n.hh.pets = [Pet(name="Biscuit", kind="dog", weight_kg=29.5)]
    fix = C.clean_fix({"stretches": [{"start": ts("2026-11-05T23:00"), "end": ts("2026-11-05T23:40"), "who": "pet"}]},
                      ts("2026-11-05T20:00"), ts("2026-11-06T09:00"), n.hh.pets)
    n.store.put(f"night_fix:p1:{D}", fix)
    C.learn_from_fix(n.c, n.p1, D, fix, ts("2026-11-05T20:00"), ts("2026-11-06T09:00"))
    assert petq.sig_view(petq.pet_sigs(n.store)[n.hh.pets[0].id])
    s = _sum(n)
    assert any(m["kind"] == "corrected_stretch" and m["who"] == "pet" for m in s["moments"])


async def test_tuning_counts_short_trips_and_waits_longer_before_in_bed(tmp_path):
    n = await _night(tmp_path)
    assert _sum(n)["times_up"] == 1
    n.p1.settings.up_after_min = 10                              # a 6-minute trip is a bathroom visit, not "up"
    assert _sum(n)["times_up"] == 0
    (tmp_path / "x").mkdir()
    m = Night(tmp_path / "x", "2026-11-05T22:00", echo=False)
    m.p1.settings.in_bed_after_min = 15
    m.present("left", True)
    await m.until("2026-11-05T22:10", dt=30)
    assert not m.e(m.p1).s.started
    await m.until("2026-11-05T22:16", dt=30)
    assert m.e(m.p1).s.started


def test_fix_api_round_trip_and_settings(tmp_path):
    with make(tmp_path) as c:
        night = c.get("/api/people/p1/night").json()
        d = night["date"]
        assert "compare" in night
        bad = c.put("/api/people/p1/night-fix", headers=H, json={"date": d, "in_bed_at": 1.0})
        assert bad.status_code == 422
        g = c.get("/api/people/p1/night-fix", params={"date": d}).json()
        assert g == {}
        assert c.delete("/api/people/p1/night-fix", headers=H, params={"date": d}).json()["ok"]
        cmp = c.get("/api/people/p1/compare", params={"date": d}).json()
        assert cmp["date"] == d


def test_asleep_on_the_couch_first_is_described_not_a_disagreement():
    """The ring's night starts at 11:31 PM on the couch; the bed's side is empty until 12:55 AM."""
    from embertide.engine.compare import asleep_away_first
    a = 1_800_000_000.0
    rows = [{"ts": a + i * 60, "occupied": 0} for i in range(85)] + [{"ts": a + 5100 + i * 60, "occupied": 1} for i in range(300)]
    bed = {"in_bed_at": a + 5100}
    wear = {"start": a, "asleep_at": a + 900}
    assert asleep_away_first(rows, wear, bed) == (a + 900, a + 5100)
    busy = [{"ts": a + i * 60, "occupied": 1} for i in range(85)] + rows[85:]       # someone WAS on this side
    assert asleep_away_first(busy, wear, bed) is None
    assert asleep_away_first(rows, {"start": a + 4800}, bed) is None                # only 5 min apart: same thing
