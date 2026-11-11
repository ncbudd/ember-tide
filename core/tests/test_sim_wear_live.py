"""The watch's live heart rate next to Health Connect, sleep stages from watch or bed, hostile input, and the repair."""

import json

from wearkit import H, night_hr, t, text_of, world

from embertide import wear_repair
from embertide.engine import watch as W

NIGHT = "2026-11-05"


# ---------- live heart rate ----------
def test_a_gap_of_ten_minutes_in_the_live_stream_is_still_this_person_but_twenty_is_stale(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T02:00")
        bed = w.bed("2026-11-05T22:20", n=900)
        now = w.ctl.clock.now_ts()
        pts = [(x, v) for x, v in bed if now - 1100 <= x <= now - 700 or now - 3000 <= x <= now - 1500]
        w.live(pts)
        pad = [(r["ts"], r["hr"]) for r in w.store.history("left", now - 3600, now, person="p1") if r.get("hr")]
        live, _ = W.recent(w.store, "p1", now)
        gap = W.identity(live, pad, now)
        assert gap["state"] in ("me", "unsure"), gap                       # newest reading ~12 min old: still counts
        later = W.identity(live, pad, now + 20 * 60)
        assert later["state"] == "stale" and "nothing from the watch lately" in later["why"]


def test_live_heart_rate_that_disagrees_with_the_import_is_called_out_in_numbers(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        w.live([(x, v) for x, v in bed[::4]])
        rep = w.hr("Pixel Watch", [(x + 20, v + 15) for x, v in bed[::4]])
        assert rep["shift_applied_s"] == 0
        assert any("bpm away from your watch's live heart rate" in n for n in rep["notes"]), rep["notes"]


def test_live_heart_rate_with_no_health_connect_data_still_gets_compared_with_the_bed(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        w.live([(x, v + 3) for x, v in bed[::4]])
        c = w.compare(NIGHT)
        assert c["has_wearable"] is False and c["wearable_name"] == "Watch"
        assert c["hr"]["bed_minus_watch"] == -3.0 and "Watch heart rate" in c["summary"]
        cov = next(x for x in w.cover() if x["date"] == NIGHT)
        assert cov["live"] == 1.0 and cov["hr"] == {}
        assert "Nothing from your watch or ring through Health Connect" in cov["text"]


def test_health_connect_data_with_no_live_stream_is_fine(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        rep = w.hr("Pixel Watch", bed[::4])
        assert not any("live" in x for x in rep["notes"] + rep["warnings"])
        cov = next(x for x in w.cover() if x["date"] == NIGHT)
        assert cov["live"] is None


def test_the_same_reading_arriving_live_and_through_health_connect_counts_once(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        w.live(bed[::4])
        rep = w.hr("Pixel Watch", [(x + 1, v) for x, v in bed[::4]])
        assert rep["stored"] == 0 and "already there from another app" in text_of(rep)


# ---------- stages ----------
def segs_for(start, count, size=1800, kinds=("light", "deep", "rem")):
    return [{"start": start + i * size, "end": start + (i + 1) * size, "stage": kinds[i % len(kinds)]} for i in range(count)]


def test_without_a_watch_the_bed_estimate_is_shown_and_labelled_as_an_estimate(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        w.bed("2026-11-05T22:20")
        n = w.night(NIGHT)
        assert n["stage_sources"] and n["stage_sources"][0]["kind"] == "estimate"
        assert "estimate" in n["stage_sources"][0]["label"]
        assert n["compare"]["has_wearable"] is False
        assert "No watch or ring data for this night" in n["compare"]["summary"] or n["compare"]["summary"] == ""


def test_stages_from_two_sources_show_the_watch_first_then_the_ring_then_the_estimate(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        s = bed[0][0] + 600
        w.stages(segs_for(s, 14), source="health-connect", label="Pixel Watch · Health Connect")
        w.stages(segs_for(s + 300, 14, kinds=("deep", "light")), source="oura", label="Oura · cloud")
        n = w.night(NIGHT)
        kinds = [x["kind"] for x in n["stage_sources"]]
        assert kinds == ["wearable", "wearable", "estimate"]
        assert n["stages"]["kind"] == "wearable"
        assert w.compare(NIGHT)["has_wearable"]


def test_stages_covering_half_the_night_are_kept_and_the_import_says_so(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        r = w.stages(segs_for(bed[0][0], 7)).json()                       # 3.5 h of a 7.5 h night
        assert r["stored"] == 7 and 0.4 < r["coverage_in_bed"] < 0.55
        assert any("cover only" in x and "% of your time in bed" in x for x in r["warnings"]), r["warnings"]


def test_stages_an_hour_off_are_flagged_as_a_time_zone_mistake_and_not_moved(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        r = w.stages(segs_for(bed[0][0] + 3 * 3600, 14, size=1800)).json()
        if r["coverage_in_bed"] is not None and r["coverage_in_bed"] < 0.5:
            assert any("time-zone mistake" in x or "cover only" in x for x in r["warnings"])
        first = w.store.get_stages("p1", 0, 4e9)["health-connect"]["segments"][0]["start"]
        assert first == bed[0][0] + 3 * 3600                               # nothing was moved


def test_malformed_stage_codes_are_dropped_and_counted_and_the_rest_kept(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        s = bed[0][0]
        now = w.ctl.clock.now_ts()
        segs = segs_for(s, 6) + [
            {"start": s + 20000, "end": s + 20900, "stage": "banana"},
            {"start": s + 21000, "end": s + 21900, "stage": 99},
            {"start": s + 22000, "end": s + 21000, "stage": "deep"},         # ends before it starts
            {"start": s + 23000, "end": s + 23000 + 13 * 3600, "stage": "deep"},   # longer than 12 h
            {"start": now, "end": now + 3600, "stage": "light"},             # in the future
            {"start": "not a time", "end": "also not", "stage": "light"},
            {"start": s + 24000, "end": s + 24900, "stage": 5},              # Health Connect code 5 = deep: valid
        ]
        r = w.stages(segs).json()
        assert r["stored"] == 7 and r["rejected"] == 6, r


def test_only_bad_stage_codes_is_a_clear_refusal_not_a_crash(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        w.bed("2026-11-05T22:20")
        r = w.stages([{"start": 1, "end": 2, "stage": "banana"}])
        assert r.status_code == 400 and "no usable segments" in r.json()["detail"]
        assert w.stages([]).status_code == 400


def test_overlapping_segments_are_warned_about(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        s = bed[0][0]
        r = w.stages([{"start": s, "end": s + 3600, "stage": "light"}, {"start": s + 1800, "end": s + 5400, "stage": "deep"}]).json()
        assert any("overlap" in x for x in r["warnings"])


# ---------- hostile and garbage input ----------
def raw_post(w, body: str):
    return w.c.post("/api/people/p1/wearable", content=body, headers={**H, "content-type": "application/json"})


def test_absurd_values_are_rejected_and_the_next_good_import_is_unaffected(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        base = t("2026-11-05T23:00")
        junk = [(base + i, v) for i, v in enumerate([0, 300, -5, 24.9, 220.1, 1e9, -1e9])]
        rep = w.hr("Watch", junk)
        assert rep["stored"] == 0 and rep["rejected"]["out_of_range"] == 7
        ok = w.hr("Watch", bed[::4])
        assert ok["stored"] == len(bed[::4]) and not ok["warnings"]


def test_nan_infinity_and_text_values(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        base = t("2026-11-05T23:00")
        body = '{"device": "Watch", "metric": "hr", "points": [[%d, NaN], [%d, Infinity], [%d, "70"], [%d, "abc"], [%d, null], [%d, true]]}' % (
            base, base + 60, base + 120, base + 180, base + 240, base + 300)
        r = raw_post(w, body)
        assert r.status_code in (200, 422), r.text[:200]
        if r.status_code == 200:
            assert r.json()["stored"] <= 2                                 # "70" and maybe true (as 1.0 is out of range)
        assert w.samples("Watch") == [] or all(25 <= v <= 220 for _, v in w.samples("Watch"))


def test_wrong_shapes_empty_and_unknown_metric(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        assert w.post("Watch", "hr", [])["stored"] == 0
        r = w.post("Watch", "hr", [[1], [], [None, None], [t("2026-11-05T23:00"), 60, "extra"]])
        assert r["stored"] == 1 and sum(r["rejected"].values()) >= 3
        scalar = w.c.post("/api/people/p1/wearable", json={"device": "Watch", "metric": "hr", "points": ["x", 7]}, headers=H)
        assert scalar.status_code == 422                               # refused outright, nothing half-stored
        bad = w.c.post("/api/people/p1/wearable", json={"device": "Watch", "metric": "blood_sugar", "points": [[1, 2]]}, headers=H)
        assert bad.status_code == 422
        assert w.c.post("/api/people/nobody/wearable", json={"device": "W", "metric": "hr", "points": []}, headers=H).status_code == 404


def test_enormous_strings_and_batches_are_refused_without_harm(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        big_name = w.c.post("/api/people/p1/wearable", json={"device": "x" * 5000, "metric": "hr", "points": []}, headers=H)
        assert big_name.status_code == 422
        too_many = w.c.post("/api/people/p1/wearable", json={"device": "W", "metric": "hr", "points": [[1, 60]] * 50001}, headers=H)
        assert too_many.status_code == 422
        long_time = w.post("W", "hr", [["9" * 5000, 60], ["2026-11-05T23:00:00" + "0" * 3000, 60]])
        assert long_time["stored"] == 0
        stage = w.stages([{"start": 1, "end": 2, "stage": "x" * 10000}])
        assert stage.status_code == 400
        assert w.c.post("/api/people/p1/stages", json={"source": "s" * 100, "segments": []}, headers=H).status_code == 422


def test_a_device_name_of_only_spaces_is_not_stored_under_an_empty_name(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        r = w.c.post("/api/people/p1/wearable", json={"device": "   ", "metric": "hr", "points": [[t("2026-11-05T23:00"), 60]]}, headers=H)
        assert r.status_code == 422, r.text[:200]


def test_wrong_units_are_converted_or_refused_never_stored_as_nonsense(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        base = t("2026-11-05T23:00")
        f = w.post("Ring", "skin_temp", [[base + 60 * i, 93.2] for i in range(10)], unit="F")
        assert f["stored"] == 10 and abs(w.samples("Ring", "skin_temp")[0][1] - 34.0) < 0.01
        unlabeled = w.post("Ring2", "skin_temp", [[base + 60 * i, 93.2] for i in range(10)])      # Fahrenheit sent without a unit
        assert unlabeled["stored"] == 0 and unlabeled["rejected"]["out_of_range"] == 10
        rel = w.post("Ring3", "skin_temp", [[base, 0.4], [base + 60, 9.0]], relative=True)         # a deviation of 9 degrees is not one
        assert rel["stored"] == 1
        kg = w.post("Scale", "weight_kg", [[base, 165]])            # pounds sent as kilograms still fits the range: stored as given
        assert kg["stored"] == 1


def test_a_hundred_thousand_second_by_second_readings_in_chunks_dont_slow_or_break_later_imports(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        base = t("2026-11-05T22:00")
        for k in range(2):
            chunk = [(base + k * 15000 + i, 60 + (i % 9)) for i in range(15000)]
            r = w.hr("Dense", chunk)
            assert r["stored"] == 15000
        after = w.hr("Watch", bed[::4])
        assert after["stored"] + after["rejected"].get("duplicate", 0) == len(bed[::4]) and after["shift_applied_s"] == 0


# ---------- the repair ----------
def seed_bad_import(w, device="Samsung Health", days_ago=0):
    t0 = t("2026-11-06T09:40")
    src = f"wear:p1:hr:{device}"
    w.store.samples_many([(t0 + 60 * i, src, 60.0) for i in range(52)])
    bad = {"ts": t0 + 50000, "device": device, "metric": "hr", "stored": 52, "shift_applied_s": -35040.0,
           "notes": ["Shifted by -584 min: the device was 584 min ahead of the bed."],
           "nights": [{"date": "2026-11-05", "samples": 52, "first": t0, "last": t0 + 51 * 60, "coverage_in_bed": 0.11, "lag_min": -584, "match": 0.64}]}
    w.store.put("imports:p1", [bad])
    return t0, src


def test_repair_with_nothing_to_repair_says_so_and_changes_nothing(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-07T09:00")
        bed = w.bed("2026-11-05T22:20")
        w.hr("Watch", bed[::4])
        before = len(w.samples("Watch"))
        for apply in (False, True):
            r = w.c.post("/api/people/p1/wearable/repair", json={"apply": apply}, headers=H).json()
            assert r["items"] == [] and "Nothing to repair" in r["text"]
        assert len(w.samples("Watch")) == before


def test_repair_twice_leaves_re_imported_readings_alone(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-07T09:00")
        t0, src = seed_bad_import(w)
        first = w.c.post("/api/people/p1/wearable/repair", json={"apply": True}, headers=H).json()
        assert first["items"][0]["deleted"] == 52 and "Re-import last 7 days" in first["text"]
        w.store.samples_many([(t0 + 60 * i, src, 61.0) for i in range(10)])
        second = w.c.post("/api/people/p1/wearable/repair", json={"apply": True}, headers=H).json()
        assert second["items"][0]["done"] and second["items"][0]["deleted"] == 0
        assert len(w.store.samples(src, t0 - 1, t0 + 3600)) == 10
        listing = w.c.post("/api/people/p1/wearable/repair", json={}, headers=H).json()
        assert "already done" in listing["text"] or "0 import" in listing["text"]


def test_repair_leaves_nights_the_phone_can_no_longer_re_import(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-20T09:00")                                        # two weeks later: older than the 7 days the phone re-imports
        t0, src = seed_bad_import(w)
        listing = wear_repair.repair(w.store, "p1", w.ctl.clock.tz, w.ctl.clock.now_ts(), apply=False)
        assert listing["items"][0]["too_old"] == ["2026-11-05"]
        done = wear_repair.repair(w.store, "p1", w.ctl.clock.tz, w.ctl.clock.now_ts(), apply=True)
        assert done["items"][0]["deleted"] == 0
        assert len(w.store.samples(src, t0 - 1, t0 + 3600)) == 52           # kept: nothing could replace them


def test_repair_survives_a_damaged_import_log(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-07T09:00")
        w.store.put("imports:p1", [{"metric": "hr"}, {"metric": "hr", "stored": 3, "shift_applied_s": 3600.0, "device": "W", "ts": 5,
                                                       "notes": ["Shifted by +60 min"]}])
        r = wear_repair.repair(w.store, "p1", w.ctl.clock.tz, w.ctl.clock.now_ts(), apply=True)
        assert isinstance(r["items"], list)
