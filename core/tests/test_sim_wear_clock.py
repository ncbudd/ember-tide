"""Clocks: a watch an hour off, a phone a few minutes off, daylight-saving nights, travel, and odd timestamps."""

from datetime import datetime, timedelta, timezone

import pytest

from wearkit import TZ, t, text_of, world

NIGHT = "2026-11-05"


def test_a_watch_an_hour_off_all_night_is_shifted_back_and_says_why(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        rep = w.hr("Export", [(x + 3600, v) for x, v in bed[::4]])
        assert rep["shift_applied_s"] == -3600
        assert any("Shifted by -60 min" in n and "time-zone" in n for n in rep["notes"]), rep["notes"]
        assert not rep["warnings"]
        got = w.samples("Export")
        assert abs(got[0][0] - bed[0][0]) <= 5 and abs(got[-1][0] - bed[::4][-1][0]) <= 5     # landed on the bed's night
        assert next(x for x in w.cover() if x["date"] == NIGHT)["hr"]["Export"] >= 0.95
        assert w.compare(NIGHT)["hr"]["together"] > 0.8


def test_the_learned_hour_is_applied_to_that_devices_skin_temperature_and_says_so(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        w.hr("Export", [(x + 3600, v) for x, v in bed[::4]])
        skin = w.post("Export", "skin_temp", [[x + 3600, 34.2] for x, _ in bed[::40]])
        assert skin["shift_applied_s"] == -3600
        assert any("learned from its heart rate" in n for n in skin["notes"])
        assert skin["nights"][0]["date"] == NIGHT


def test_one_hour_of_readings_an_hour_off_is_not_shifted_on_weak_evidence(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        short = [(x + 3600, v) for x, v in bed[400:640:4]]               # 60 readings = one hour, an hour late
        rep = w.hr("Export", short)
        assert rep["shift_applied_s"] == 0 and rep["stored"] == len(short)
        assert not rep["warnings"]                                       # no alarming text on too little evidence
        assert not w.store.get("device_offset:p1:Export")


def test_a_phone_clock_five_minutes_wrong_is_corrected_in_plain_words(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        rep = w.hr("Watch", [(x - 300, v) for x, v in bed[::2]])
        assert rep["shift_applied_s"] == 300
        assert any("5 min behind" in n for n in rep["notes"]), rep["notes"]


def test_autumn_clock_change_inside_the_night_with_correct_times(tmp_path):
    """Night of 31 Oct to 1 Nov 2026: clocks go back at 2:00, so the night is 9 real hours long."""
    with world(tmp_path) as w:
        w.jump("2026-11-01T11:00")
        bed = w.bed("2026-10-31T23:00", n=2160)                           # 9 h real time
        iso = [[datetime.fromtimestamp(x, timezone.utc).isoformat(), v] for x, v in bed[::4]]
        rep = w.post("Watch", "hr", iso)
        assert rep["stored"] == len(iso) and rep["shift_applied_s"] == 0 and not rep["warnings"]
        assert [n["date"] for n in rep["nights"]] == ["2026-10-31"]
        assert rep["nights"][0]["coverage_in_bed"] >= 0.95


def test_autumn_clock_change_with_local_times_without_an_offset_keeps_what_it_can(tmp_path):
    """A script that writes local wall-clock times without an offset sends 1:30 twice. The repeat hour is
    ambiguous: the server must not crash or move the rest of the night."""
    with world(tmp_path) as w:
        w.jump("2026-11-01T11:00")
        bed = w.bed("2026-10-31T23:00", n=2160)
        local = [[datetime.fromtimestamp(x, TZ).strftime("%Y-%m-%dT%H:%M:%S"), v] for x, v in bed[::4]]
        rep = w.post("Script", "hr", local)
        assert rep["stored"] >= len(local) - 61                           # at most the repeated hour is merged
        assert abs(rep["shift_applied_s"]) <= 3600


def test_spring_clock_change_inside_the_night(tmp_path):
    """Night of 7 to 8 March 2026: clocks jump forward at 2:00, so the night is 7 real hours."""
    with world(tmp_path) as w:
        w.jump("2026-03-08T11:00")
        bed = w.bed("2026-03-07T23:00", n=1680)
        iso = [[datetime.fromtimestamp(x, timezone.utc).isoformat(), v] for x, v in bed[::4]]
        rep = w.post("Watch", "hr", iso)
        assert rep["stored"] == len(iso) and rep["shift_applied_s"] == 0 and not rep["warnings"]
        assert [n["date"] for n in rep["nights"]] == ["2026-03-07"]


def test_a_watch_that_ignored_the_spring_change_is_shifted_by_the_hour(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-03-08T11:00")
        bed = w.bed("2026-03-07T23:00", n=1680)
        rep = w.hr("OldWatch", [(x - 3600, v) for x, v in bed[::4]])        # still on winter time
        assert rep["shift_applied_s"] == 3600
        assert any("1 h behind" in n or "exactly 1 h behind" in n for n in rep["notes"]), rep["notes"]


def test_travel_three_zones_west_then_home_again(tmp_path):
    """Away, the bed has nobody (away nights are not compared); at home the watch is back on local time. A stale
    correction learned from a trip must not move the next night's skin temperature."""
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        trip = w.hr("Watch", [(x + 3 * 3600, v) for x, v in bed[::4]])        # phone still on the other zone's clock
        assert trip["shift_applied_s"] == -3 * 3600
        w.jump("2026-11-07T09:00")
        bed2 = w.bed("2026-11-06T22:20", seed=5)
        home = w.hr("Watch", bed2[::4])
        assert home["shift_applied_s"] == 0 and not home["warnings"]
        off = w.store.get("device_offset:p1:Watch")
        assert off and off["shift_s"] == 0                                   # the trip's correction is forgotten
        skin = w.post("Watch", "skin_temp", [[x, 34.1] for x, _ in bed2[::40]])
        assert skin["shift_applied_s"] == 0


def test_a_batch_with_timestamps_in_the_future_stores_only_the_past(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        now = w.ctl.clock.now_ts()
        fut = [(now + 3600 * (i + 1), 70) for i in range(10)]
        rep = w.hr("Watch", bed[::4] + fut)
        assert rep["rejected"]["future"] == 10 and rep["stored"] == len(bed[::4])
        assert all(a <= now + 300 for a, _ in w.samples("Watch"))
        assert not rep["warnings"]


def test_timestamps_years_in_the_past_are_refused_and_counted(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        old = [(t("2022-01-02T03:00") + 60 * i, 60) for i in range(30)]
        rep = w.hr("Watch", old + bed[::4])
        assert rep["rejected"]["too_old"] == 30 and rep["stored"] == len(bed[::4])
        assert w.nights(count=30)[-1]["date"] != "2022-01-01"


def test_unsorted_and_duplicate_timestamps_store_each_moment_once(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        pts = bed[::4]
        shuffled = list(reversed(pts)) + pts[:50] + [(x, v + 5) for x, v in pts[50:100]]       # reversed, repeated, conflicting
        rep = w.hr("Watch", shuffled)
        assert rep["stored"] == len(pts) and rep["shift_applied_s"] == 0
        got = [a for a, _ in w.samples("Watch")]
        assert got == sorted(got) and len(set(round(a) for a in got)) == len(got)
        assert rep["rejected"]["duplicate"] == 100


def test_timestamps_as_text_milliseconds_and_seconds_mix_in_one_batch(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        pts = bed[::4]
        mixed = [[int(x * 1000) if i % 3 == 0 else (datetime.fromtimestamp(x, timezone.utc).isoformat() if i % 3 == 1 else x), v]
                 for i, (x, v) in enumerate(pts)]
        rep = w.post("Watch", "hr", mixed)
        assert rep["stored"] == len(pts) and rep["shift_applied_s"] == 0 and not rep["warnings"]
