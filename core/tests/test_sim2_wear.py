"""Second pass, area 5: wearables meeting each other and the corrections. The 30 s copy window with three apps and a ring,
a watch left on the nightstand followed by a real night, and a Health Connect sleep session that lands 30 hours late after
the owner has already fixed the night."""

from embertide.engine import wearables as W
from wearkit import H, t, text_of, world

NIGHT = "2026-11-05"


def test_three_apps_and_a_ring_the_original_wins_whatever_the_order(tmp_path):
    """Samsung Health is the original; Health Sync and a third copier ('Sleep as Android') each re-write the readings 20-25 s
    later, in the worst order (copies first). A ring worn together keeps its own readings."""
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        base = bed[::4]
        a = w.hr("Health Sync", [(x + 20, v) for x, v in base])
        b = w.hr("Sleep", [(x + 25, v) for x, v in base])
        assert a["stored"] == len(base)
        # the second copier is another copy of a copy: its readings sit 5 s from the first copy's, the same value
        assert b["stored"] in (0, len(base))
        orig = w.hr("Samsung Health", base)
        assert orig["stored"] == len(base)
        ring = w.hr("Oura", [(x + 20, v) for x, v in base])
        assert ring["stored"] == len(base), text_of(ring)
        assert len(w.samples("Samsung Health")) == len(base)
        assert len(w.samples("Health Sync")) == 0 and len(w.samples("Sleep")) == 0, (len(w.samples("Health Sync")), len(w.samples("Sleep")))
        p = w.ctl.household.people[0]
        merged = w.ctl.autopilot.wear_hr(p, base[0][0] - 60, base[-1][0] + 60, level=False)
        assert len(merged) == 2 * len(base)                           # the watch and the ring, once each


def test_a_copy_just_inside_and_just_outside_the_window(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        base = [(bed[0][0] + 120 * i, 55 + i % 7) for i in range(120)]        # readings 2 min apart: no neighbour within 30 s
        w.hr("Samsung Health", base)
        inside = w.hr("Health Sync", [(x + 29, v) for x, v in base[:50]])
        outside = w.hr("Health Sync", [(x + 31, v) for x, v in base[50:100]])
        assert inside["stored"] == 0, text_of(inside)
        assert outside["stored"] == 50, text_of(outside)              # 31 s apart is a different reading


def test_two_copiers_agreeing_with_each_other_but_not_the_original_are_kept_once(tmp_path):
    """Two copying apps with the same (different from the original) values: they aren't copies of the original, and they
    are copies of each other only if both are copiers: the second one's readings must not be dropped as copies of a
    reading the first one 'stole' from nobody."""
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        base = bed[::4][:100]
        w.hr("Samsung Health", base)
        diff = [(x + 10, v + 6) for x, v in base]
        a = w.hr("Health Sync", diff)
        b = w.hr("Sleep", [(x + 3, v) for x, v in diff])
        assert a["stored"] == len(diff)
        assert len(w.samples("Samsung Health")) == len(base)
        total = len(w.samples("Health Sync")) + len(w.samples("Sleep"))
        assert total >= len(diff)                                      # at least one copy of each of their own readings stays


def test_a_watch_left_on_the_nightstand_then_worn_again(tmp_path):
    """Two flat nights (not worn), then a real one from the same watch: the flat nights teach nothing and are told as 'not
    worn', the real night is the watch's; the 'watch went quiet' note applies only to the nights it was off."""
    with world(tmp_path) as w:
        w.jump("2026-11-08T12:00")
        b1 = w.bed("2026-11-05T22:20", seed=3)
        b2 = w.bed("2026-11-06T22:20", seed=4)
        b3 = w.bed("2026-11-07T22:20", seed=5)
        w.hr("Watch", [(x, 71) for x, _ in b1[::4]])
        w.hr("Watch", [(x, 71) for x, _ in b2[::4]])
        p = w.ctl.household.people[0]
        flat_only = w.cover()
        assert all(x["not_worn"] == ["Watch"] for x in flat_only if x["date"] in (NIGHT, "2026-11-06"))
        rep = w.hr("Watch", b3[::4])
        assert not rep["not_worn"] and rep["stored"] == len(b3[::4])
        cov = {x["date"]: x for x in w.cover()}
        assert cov["2026-11-07"]["hr"]["Watch"] > 0.9 and not cov["2026-11-07"].get("not_worn")
        got = w.ctl.autopilot.wear_hr(p, b1[0][0] - 60, b3[-1][0] + 60, level=False)
        assert len(got) == len(b3[::4]) and all(x >= b3[0][0] - 1 for x, _ in got)
        # the next real night shows no comparison troubles
        cmp3 = w.compare("2026-11-07")
        assert "hr" in cmp3 and not cmp3.get("not_worn")


def _silent_setup(w, flat: bool):
    w.jump("2026-11-09T12:00")
    nt = w.c.app.state.notifier
    p = w.ctl.household.people[0]
    beds = {d: w.bed(f"2026-11-0{d}T22:20", seed=d) for d in (3, 4, 5, 6, 7, 8)}
    now = w.ctl.clock.now_ts()
    w.hr("Watch", beds[4][::4])
    if flat:
        for d in (7, 8):
            w.hr("Watch", [(x, 71) for x, _ in beds[d][::4]])
    return nt, p, beds, now


def test_a_watch_that_worked_then_sent_nothing_for_two_nights_is_named_and_clears_when_it_returns(tmp_path):
    with world(tmp_path) as w:
        nt, p, beds, now = _silent_setup(w, flat=False)
        assert nt._wearable_silent(p, now) == "Watch"
        w.hr("Watch", beds[8][::4])
        assert nt._wearable_silent(p, now) is None


import pytest  # noqa: E402


@pytest.mark.xfail(reason="JUDGEMENT: a watch lying flat on the nightstand for two nights (it sends constant readings, stored and marked "
                          "'probably not worn') never counts as quiet, so 'your watch has gone quiet' is not sent although the owner "
                          "gets no night from it. Should flat nights count as quiet for that note?", strict=True)
def test_a_watch_left_on_the_nightstand_for_two_nights_is_named(tmp_path):
    with world(tmp_path) as w:
        nt, p, beds, now = _silent_setup(w, flat=True)
        assert nt._wearable_silent(p, now) == "Watch"


def test_a_late_sleep_session_after_a_fix_this_night_correction(tmp_path):
    """The owner fixed the night (got in 22:10, out 06:45) the day after; the watch app's sleep session only arrives 30 hours
    after the night. The correction stays, the session lands as the wearable's stages, the comparison and the night agree."""
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        w.hr("Watch", bed[::4])
        fix = {"date": NIGHT, "in_bed_at": t("2026-11-05T22:10"), "out_at": t("2026-11-06T06:45")}
        r = w.c.put(f"/api/people/p1/night-fix", json=fix, headers=H)
        assert r.status_code == 200, r.text[:300]
        before = w.night(NIGHT)
        assert before["in_bed_at"] == fix["in_bed_at"] and before["out_at"] == fix["out_at"]
        w.jump("2026-11-07T15:00")                                          # 30 hours after the night's end
        s0 = t("2026-11-05T22:30")
        segs = [{"start": s0 + i * 1800, "end": s0 + (i + 1) * 1800, "stage": ["light", "deep", "rem"][i % 3]} for i in range(16)]
        rep = w.stages(segs)
        assert rep.status_code == 200 and rep.json()["stored"] == 16
        after = w.night(NIGHT)
        assert after["in_bed_at"] == fix["in_bed_at"] and after["out_at"] == fix["out_at"]       # the correction still wins
        assert after["corrected"] and "in_bed_at" in after["corrected"]
        assert after["stage_sources"][0]["kind"] == "wearable"
        cmp_ = after["compare"]
        assert cmp_["has_wearable"]
        # the sleep story and the numbers agree: no 'settled' line outside the corrected span
        for m in after["moments"]:
            if m["kind"] == "sleep":
                assert fix["in_bed_at"] <= m["ts"] <= fix["out_at"]
        nights = {n["date"]: n for n in w.nights()}
        assert nights[NIGHT]["in_bed_at"] == fix["in_bed_at"]
