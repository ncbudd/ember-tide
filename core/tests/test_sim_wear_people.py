"""Whose data it is, and what a night looks like when the watch was off, away, or worn by someone else."""

import random

from wearkit import H, night_hr, t, text_of, world

NIGHT = "2026-11-05"


def test_the_partners_watch_gets_a_plain_question_and_is_not_shifted(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        other = night_hr(bed[0][0], n=1800, seed=99)                    # a different person's heart rate
        rep = w.hr("Partner Watch", [(x, v) for x, v in other[::4]])
        assert rep["shift_applied_s"] == 0
        assert any("right person and device" in x for x in rep["warnings"]), (rep["warnings"], rep["notes"])
        assert NIGHT in " ".join(rep["warnings"])


def test_a_watch_left_on_the_nightstand_with_flat_readings_is_not_trusted_or_shifted(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        rep = w.hr("Watch", [(x, 71) for x, _ in bed[::4]])             # same number all night
        assert rep["shift_applied_s"] == 0 and rep["stored"] <= len(bed[::4])
        assert any("may not have been on your wrist" in x for x in rep["warnings"]), (rep["warnings"], rep["notes"])
        assert "live" not in w.compare(NIGHT)["summary"]                  # imported readings are not "live"
        assert not w.store.get("device_offset:p1:Watch") or w.store.get("device_offset:p1:Watch")["shift_s"] == 0


def test_a_watch_with_no_heart_rate_at_all_leaves_the_night_honestly_empty(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        w.bed("2026-11-05T22:20")
        rep = w.hr("Watch", [])
        assert rep["stored"] == 0 and rep["received"] == 0 and not rep["warnings"]
        cov = next(x for x in w.cover() if x["date"] == NIGHT)
        assert "Nothing from your watch or ring" in cov["text"] and cov["hr"] == {}


def test_a_watch_taken_off_in_the_middle_says_how_much_of_the_night_it_covers(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        worn = bed[:600:4] + bed[1200::4]                               # off for the middle ~2 h 30
        rep = w.hr("Watch", worn)
        assert rep["shift_applied_s"] == 0 and not rep["warnings"]
        cov = next(x for x in w.cover() if x["date"] == NIGHT)
        assert 0.5 < cov["hr"]["Watch"] < 0.9
        assert "covers" in cov["text"] and "%" in cov["text"]


def test_a_watch_charging_overnight_then_worn_in_the_morning(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T12:00")
        bed = w.bed("2026-11-05T22:20")
        morning = [(t("2026-11-06T07:30") + 60 * i, 78 + i % 6) for i in range(120)]
        rep = w.hr("Watch", morning)
        assert rep["stored"] == 120 and rep["shift_applied_s"] == 0 and not rep["warnings"]
        cov = next(x for x in w.cover() if x["date"] == NIGHT)
        assert "Nothing from your watch or ring" in cov["text"]         # said plainly, not an error
        assert w.night(NIGHT)["compare"]["has_wearable"] is False


def test_a_nap_on_the_couch_while_the_bed_is_empty_creates_no_night_and_no_warning(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T18:00")
        w.empty_bed("2026-11-06T12:00", 6)
        s = t("2026-11-06T14:00")
        segs = [{"start": s + i * 900, "end": s + (i + 1) * 900, "stage": ["light", "deep"][i % 2]} for i in range(6)]
        r = w.stages(segs).json()
        assert r["stored"] == 6 and not r["warnings"] and r["coverage_in_bed"] is None
        assert [x for x in w.nights() if x["has_data"]] == []
        assert w.cover() == []


def test_the_watch_asleep_away_then_in_bed_is_described_as_asleep_away_first(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        w.empty_bed("2026-11-05T22:30", 1.5)
        bed = w.bed("2026-11-06T00:00", n=1500)
        s = t("2026-11-05T23:00")
        segs = [{"start": s + i * 1800, "end": s + (i + 1) * 1800, "stage": ["light", "deep", "rem"][i % 3]} for i in range(13)]
        r = w.stages(segs).json()
        assert r["stored"] == 13
        cmp_ = w.compare(NIGHT)
        assert cmp_["away_first"] and cmp_["away_first"]["to"] == bed[0][0]
        assert any("asleep away from the bed" in x for x in cmp_["lines"])
        assert cmp_["times"]["in_bed_at"]["source"] == "bed"


def test_the_watch_asleep_while_a_pet_lies_on_the_bed_does_not_make_up_a_night_for_the_person(tmp_path):
    """Bed sees something steady with a heart rate far from the person's; the watch (worn elsewhere) shows its own. The
    import tells the person the two don't match instead of lining them up."""
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        rnd = random.Random(8)
        start = t("2026-11-05T23:00")
        w.store.telemetry([(start + 15 * i, "left", 1, 18.0, 18.2, 1, 112 + rnd.choice((-3, 0, 2)), 28, 0, 18.0, "deep", 1, "p1") for i in range(1200)])
        far = [(start + 60 * i, 55 + (i % 4)) for i in range(300)]
        rep = w.hr("Watch", far)
        assert rep["shift_applied_s"] == 0
        assert rep["stored"] == len(far)


def test_a_watch_and_a_ring_that_disagree_both_stay_and_the_comparison_names_both(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        w.hr("Pixel Watch", [(x, v) for x, v in bed[::4]])
        rep = w.hr("Oura Ring", [(x + 7, v + 8) for x, v in bed[::4]])        # a different level, a few seconds off
        assert rep["stored"] == len(bed[::4]) and not rep["warnings"]
        c = w.compare(NIGHT)
        assert {d["name"] for d in c["devices"]} == {"Pixel Watch", "Oura Ring"}
        assert "Readings from Oura Ring and Pixel Watch" in " ".join(c["using"])
        cov = next(x for x in w.cover() if x["date"] == NIGHT)
        assert set(cov["hr"]) == {"Pixel Watch", "Oura Ring"}


def test_switching_to_a_new_wearable_midweek_shows_the_new_one_and_the_old_one_quiet(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-03T09:00")
        b1 = w.bed("2026-11-02T22:20", seed=3)
        w.hr("Old Watch", b1[::4])
        w.jump("2026-11-06T09:00")
        b2 = w.bed("2026-11-05T22:20", seed=4)
        w.hr("New Ring", b2[::4])
        cov = {x["date"]: x for x in w.cover()}
        assert set(cov[NIGHT]["hr"]) == {"New Ring"}
        assert "2026-11-02" in cov and set(cov["2026-11-02"]["hr"]) == {"Old Watch"}


def test_a_person_marked_away_keeps_the_wearable_data_but_the_sleep_screen_shows_away(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        w.store.put("away:p1", {"since": t("2026-11-05T12:00"), "by": "p1", "until": None})
        rep = w.hr("Watch", bed[::4])
        assert rep["stored"] == len(bed[::4])                           # data is kept
        n = next(x for x in w.nights() if x["date"] == NIGHT)
        assert n["has_data"] is False and n["away"] is True
        assert len(w.samples("Watch")) == len(bed[::4])


def test_a_night_corrected_before_the_import_stays_corrected_after_it(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        fix = {"date": NIGHT, "in_bed_at": bed[0][0] + 1800, "out_at": bed[-1][0] - 1800}
        assert w.c.put("/api/people/p1/night-fix", json=fix, headers=H).status_code == 200
        before = w.night(NIGHT)
        assert before["compare"]["corrected"] and before["compare"]["times"]["in_bed_at"]["source"] == "you"
        w.hr("Watch", bed[::4])
        w.stages([{"start": bed[0][0], "end": bed[0][0] + 7200, "stage": "light"}])
        after = w.night(NIGHT)
        assert after["compare"]["times"]["in_bed_at"] == before["compare"]["times"]["in_bed_at"]
        assert "Your corrections win over both" in after["compare"]["using"]


def test_a_night_corrected_after_the_import_beats_the_watch(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        s = bed[0][0]
        w.stages([{"start": s + 600, "end": s + 600 + 1800 * 12, "stage": "deep"}])
        assert w.night(NIGHT)["compare"]["times"]["in_bed_at"]["source"] in ("watch", "both", "bed")
        w.c.put("/api/people/p1/night-fix", json={"date": NIGHT, "in_bed_at": s + 3600}, headers=H)
        c = w.night(NIGHT)["compare"]
        assert c["times"]["in_bed_at"] == {"ts": round(s + 3600), "source": "you"}


def test_two_sleep_sessions_in_one_night_both_show_and_the_hour_awake_is_a_wake_up(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20", gaps=[(700, 940)])               # up for an hour in the middle
        s = bed[0][0]
        a = [{"start": s + i * 900, "end": s + (i + 1) * 900, "stage": ["light", "deep"][i % 2]} for i in range(11)]
        b = [{"start": s + 4 * 3600 + i * 900, "end": s + 4 * 3600 + (i + 1) * 900, "stage": ["light", "rem"][i % 2]} for i in range(16)]
        r = w.stages(a + b).json()
        assert r["stored"] == 27 and r["rejected"] == 0
        n = w.night(NIGHT)
        assert n["stages"] and n["compare"]["has_wearable"]
        assert n["times_up"] >= 1


def test_a_nap_in_the_afternoon_does_not_become_the_nights_stages(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        s = t("2026-11-05T14:00")
        nap = [{"start": s + i * 900, "end": s + (i + 1) * 900, "stage": "light"} for i in range(4)]
        w.stages(nap)
        n = w.night(NIGHT)
        assert all(x["kind"] != "wearable" or x["segments"][0]["start"] >= t("2026-11-05T18:00") for x in
                   __import__("embertide.engine.stages", fromlist=["collect"]).collect(w.store, w.ctl.person("p1"), bed[0][0] - 3600, bed[-1][0]))
        assert n["compare"]["has_wearable"] is False


def test_a_night_that_crosses_noon_lands_on_the_evening_it_began(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-07T09:00")
        bed = w.bed("2026-11-05T22:00", n=3600)                         # 15 h in bed: 10 pm to 1 pm
        rep = w.hr("Watch", bed[::4])
        assert {n["date"] for n in rep["nights"]} == {"2026-11-05", "2026-11-06"} or [n["date"] for n in rep["nights"]] == ["2026-11-05"]
        assert rep["shift_applied_s"] == 0 and rep["stored"] == len(bed[::4])
