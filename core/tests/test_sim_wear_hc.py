"""Health Connect style imports: late blocks, repeats, two app origins, sampling rates, short and overlapping syncs.

Each scenario checks the stored data, the night it lands on, what the phone's lists show, and the words in the import log.
"""

from wearkit import H, t, text_of, world

NIGHT = "2026-11-05"


def stored(w, device="Watch"):
    return len(w.samples(device))


def test_hourly_blocks_arriving_hours_late_fill_the_night_without_warnings(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        per_hour = 240
        covers = []
        for k in range(0, len(bed), per_hour):                      # one block per hour, in order, each its own sync
            block = bed[k:k + per_hour][::4]
            rep = w.hr("Watch", block)
            assert rep["stored"] == len(block) and not rep["warnings"], (k, text_of(rep))
            assert rep["shift_applied_s"] == 0
            covers.append(next(x for x in w.cover() if x["date"] == NIGHT)["hr"]["Watch"])
        assert covers == sorted(covers) and covers[-1] >= 0.95            # coverage only grows
        assert stored(w) == len(bed[::4]) - 0 or abs(stored(w) - len(bed[::4])) <= 8
        assert all(n["date"] == NIGHT for rep in w.log() for n in rep["nights"])
        cov = next(x for x in w.cover() if x["date"] == NIGHT)
        assert "Heart rate for the whole night" in cov["text"] and "later sync" in cov["text"]


def test_sleep_session_a_day_late_replaces_the_estimate_and_never_duplicates(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        w.hr("Watch", bed[::4])
        n0 = w.night(NIGHT)
        assert n0["stage_sources"][0]["kind"] == "estimate"
        w.jump("2026-11-07T09:30")                                          # a day later the watch app finally writes the session
        s0 = t("2026-11-05T22:30")
        segs = [{"start": s0 + i * 1800, "end": s0 + (i + 1) * 1800, "stage": ["light", "deep", "rem"][i % 3]} for i in range(14)]
        r = w.stages(segs)
        assert r.status_code == 200 and r.json()["stored"] == 14 and r.json()["rejected"] == 0
        again = w.stages(segs).json()                                      # re-sent on the next sync
        assert again["stored"] == 14
        rows = w.store.get_stages("p1", s0 - 1, s0 + 8 * 3600)
        assert len(rows["health-connect"]["segments"]) == 14                 # replaced, not doubled
        n1 = w.night(NIGHT)
        assert n1["stage_sources"][0]["kind"] == "wearable"
        assert n1["compare"]["has_wearable"] and n1["compare"]["wearable_name"] == "Watch"
        cov = next(x for x in w.cover() if x["date"] == NIGHT)
        assert cov["stages"] and "whole night" in cov["text"]


def test_the_same_48_hours_resent_every_sync_changes_nothing(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T12:00")
        bed = w.bed("2026-11-05T22:20")
        win = bed[::4]
        first = w.hr("Watch", win)
        counts = [stored(w)]
        for _ in range(4):
            rep = w.hr("Watch", win)
            assert rep["stored"] == 0 and rep["rejected"]["duplicate"] == len(win)
            assert not rep["warnings"] and rep["shift_applied_s"] == 0
            counts.append(stored(w))
        assert len(set(counts)) == 1 and counts[0] == first["stored"]
        # the hindsight check still has one stretch to show, not five
        assert [n["date"] for n in w.nights() if n["has_data"]] == [NIGHT]


def test_two_app_origins_with_the_same_readings_count_once(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        base = bed[::4]
        a = w.hr("Samsung Health", base)
        # a copying app's copy within 30 s is dropped (as on the phone, HealthMerge); a different app 90 s later is not a copy
        close = [(x + 1, v) for x, v in base]
        b = w.hr("Health Sync", close)
        assert b["stored"] == 0, text_of(b)
        assert "already there from another app" in text_of(b)
        far = [(x + 90, v) for x, v in base]                                # 90 s later: a different reading
        c = w.hr("Third App", far)
        assert c["stored"] == len(base)
        assert not c["warnings"] and c["shift_applied_s"] == 0
        assert a["stored"] == len(base)


def test_series_density_one_per_second_minute_and_ten_minutes(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20", n=1800, step=15)
        per_sec = [(bed[i // 15][0] + (i % 15), bed[i // 15][1] + (i % 3)) for i in range(0, 1800 * 15, 1)][:1800]
        r1 = w.hr("Dense", per_sec)
        assert r1["stored"] == 1800 and not r1["warnings"]
        r2 = w.hr("Minute", bed[::4])
        assert r2["stored"] + r2["rejected"].get("duplicate", 0) == len(bed[::4]) and r2["shift_applied_s"] == 0
        sparse = [(x + 100, v + 2) for x, v in bed[::40]]                   # one every 10 minutes: 45 readings
        r3 = w.hr("Sparse", sparse)
        assert r3["stored"] >= len(sparse) - 8 and not r3["warnings"] and r3["shift_applied_s"] == 0
        cov = {x["date"]: x for x in w.cover()}[NIGHT]
        assert cov["hr"]["Minute"] >= 0.6 and cov["hr"]["Sparse"] >= 0.8    # 10-minute readings still cover the night


def test_a_daytime_only_sync_is_kept_quietly_and_the_night_says_what_is_missing(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T15:00")
        bed = w.bed("2026-11-05T22:20")
        day = [(t("2026-11-06T08:00") + 120 * i, 80 + (i % 7)) for i in range(50)]
        rep = w.hr("Watch", day)
        assert rep["stored"] == 50 and not rep["warnings"] and rep["shift_applied_s"] == 0
        assert not any("doesn't follow" in x for x in rep["warnings"])
        cov = next(x for x in w.cover() if x["date"] == NIGHT)
        assert "Nothing from your watch or ring" in cov["text"]            # the night is still honestly empty


def test_a_sync_that_overlaps_the_night_by_twenty_minutes_is_kept_as_recorded(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        end = bed[-1][0]
        tail = [(end - 20 * 60 + 60 * i, 70 + (i % 5)) for i in range(20)] + [(end + 60 * i, 85) for i in range(1, 40)]
        rep = w.hr("Watch", tail)
        assert rep["shift_applied_s"] == 0 and rep["stored"] == len(tail)
        assert not rep["warnings"], rep["warnings"]
        assert w.log()[-1]["stored"] == len(tail)


def test_two_nights_in_one_sync_land_on_their_own_dates(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-07T09:00")
        b1 = w.bed("2026-11-05T22:20", seed=3)
        b2 = w.bed("2026-11-06T22:40", seed=4)
        rep = w.hr("Watch", b1[::4] + b2[::4])
        assert [n["date"] for n in rep["nights"]] == ["2026-11-05", "2026-11-06"]
        assert not rep["warnings"] and rep["shift_applied_s"] == 0
        assert {n["date"] for n in w.nights()} == {"2026-11-05", "2026-11-06"}


# ───────────────────────── a copying app's copies (same rule as the phone's HealthMerge) ─────────────────────────
def test_a_copy_that_arrives_before_the_original_is_replaced_by_the_original(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        base = bed[::4]
        copy = w.hr("Health Sync", [(x + 20, v) for x, v in base])
        assert copy["stored"] == len(base)                       # nothing else there yet: kept
        orig = w.hr("Samsung Health", base)
        assert orig["stored"] == len(base), text_of(orig)
        assert "replaced by the original" in text_of(orig)
        assert stored(w, "Health Sync") == 0 and stored(w, "Samsung Health") == len(base)


def test_a_copying_apps_own_readings_and_different_values_are_kept(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        base = bed[::4]
        w.hr("Samsung Health", base[:200])
        # the copying app also has the rest of the night (only it has those), and one reading 20 s after an original
        # with a different value: neither is a copy
        rest = w.hr("Health Sync", base[200:300] + [(base[10][0] + 20, base[10][1] + 7)])
        assert rest["stored"] == 101, text_of(rest)


def test_a_watch_and_a_ring_worn_together_are_both_kept(tmp_path):
    """Two real devices often read the same number within half a minute; that is two readings, not a copy."""
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        base = bed[::4]
        a = w.hr("Pixel Watch", base)
        b = w.hr("Oura", [(x + 20, v) for x, v in base])
        assert a["stored"] == len(base) and b["stored"] == len(base), text_of(b)
        p = w.ctl.household.people[0]
        merged = w.ctl.autopilot.wear_hr(p, base[0][0] - 60, base[-1][0] + 60, level=False)
        assert len(merged) == 2 * len(base)


def test_at_read_time_a_copy_counts_once_and_the_original_is_kept(tmp_path):
    """Stored before this rule: read-time merging drops the copy, not the original."""
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        base = bed[::4]
        w.store.samples_many([(x + 20, "wear:p1:hr:Health Sync", float(v)) for x, v in base])
        w.store.samples_many([(x, "wear:p1:hr:Samsung Health", float(v)) for x, v in base[:100]])
        p = w.ctl.household.people[0]
        got = w.ctl.autopilot.wear_hr(p, base[0][0] - 60, base[-1][0] + 60, level=False)
        assert len(got) == len(base)
        times = {round(x) for x, _ in got}
        assert all(round(x) in times for x, _ in base[:100])          # the originals' times, not the copies'
