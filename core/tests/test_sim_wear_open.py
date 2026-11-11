"""Open questions and gaps found by the wearable and cloud simulations. Each is an xfail or a skip with the question as its
reason, so the owner can decide and then turn it into a plain test."""

import pytest
from wearkit import t, world


def test_a_copy_from_another_app_twenty_seconds_later_counts_once(tmp_path):
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        w.hr("Samsung Health", bed[::4])
        copy = w.hr("Health Sync", [(x + 20, v) for x, v in bed[::4]])
        assert copy["stored"] == 0


def test_flat_nightstand_readings_are_kept_but_teach_nothing(tmp_path):
    """Decided: flat readings (a watch left on the nightstand) stay stored and visible, marked as probably not worn, and
    are left out of everything Autopilot learns from and of the wearable-vs-bed comparison."""
    with world(tmp_path) as w:
        w.jump("2026-11-06T09:00")
        bed = w.bed("2026-11-05T22:20")
        flat = [(x, 71) for x, _ in bed[::4]]
        rep = w.hr("Watch", flat)
        assert rep["stored"] == len(flat) and rep["not_worn"] is True
        assert len(w.samples("Watch")) == len(flat)                         # kept, not deleted
        p = w.ctl.household.people[0]
        ap = w.ctl.autopilot
        assert ap.wear_hr(p, bed[0][0] - 60, bed[-1][0] + 60) == []          # lined-up series Autopilot uses
        assert ap.wear_hr(p, bed[0][0] - 60, bed[-1][0] + 60, level=False) == []
        from embertide.engine import wearables as W
        assert W.levels(w.ctl, p, [{"date": "2026-11-05", "in_bed_at": bed[0][0], "out_at": bed[-1][0]}])["hr"]["devices"] == {}
        cmp_ = w.compare("2026-11-05")
        assert "hr" not in cmp_ and cmp_["not_worn"] == ["Watch"]
        assert any("probably not being worn" in x for x in cmp_["lines"])
        cov = next(x for x in w.cover() if x["date"] == "2026-11-05")
        assert cov["not_worn"] == ["Watch"] and "probably not being worn" in cov["text"]
        assert any(d["name"] == "Watch" for d in cmp_["devices"])          # still listed as where readings came from


def test_a_worn_watch_next_to_a_flat_stretch_still_counts(tmp_path):
    """Only the flagged stretch is set aside: the same watch's real readings on another night still teach."""
    with world(tmp_path) as w:
        w.jump("2026-11-07T09:00")
        b1 = w.bed("2026-11-05T22:20", seed=3)
        b2 = w.bed("2026-11-06T22:20", seed=4)
        w.hr("Watch", [(x, 71) for x, _ in b1[::4]])
        rep = w.hr("Watch", b2[::4])
        assert not rep["not_worn"]
        p = w.ctl.household.people[0]
        got = w.ctl.autopilot.wear_hr(p, b1[0][0] - 60, b2[-1][0] + 60, level=False)
        assert len(got) == len(b2[::4]) and all(t >= b2[0][0] - 1 for t, _ in got)


@pytest.mark.skip(reason="GAP: no message when Oura (or another cloud account) needs reconnecting. status.needs_reconnect is only "
                         "visible if the person opens Settings. Trigger: needs_reconnect true for 12 hours. Proposed wording: "
                         "'Oura needs you to sign in again. Open Settings > Watch or ring > Connect Oura.'")
def test_gap_cloud_account_needs_reconnecting():
    pass


def test_watch_quiet_for_two_nights_is_noticed_and_names_the_device(tmp_path):
    """Kind `wearable` (notify.py): a device that supplied nights in the last 14 days, then nothing on the last two nights in bed."""
    with world(tmp_path) as w:
        w.jump("2026-11-06T13:00")
        for d in ("01", "02", "03"):
            bed = w.bed(f"2026-11-{d}T22:20", seed=int(d))
            w.hr("Pixel Watch", bed[::4])
        w.bed("2026-11-04T22:20", seed=4)
        w.bed("2026-11-05T22:20", seed=5)                       # in bed on both, the watch says nothing
        nt, p = w.ctl.notifier, w.ctl.person("p1")
        assert nt._wearable_silent(p, w.ctl.clock.now_ts()) == "Pixel Watch"
        w.hr("Pixel Watch", w.bed("2026-11-05T22:20", seed=5)[::4])      # the second night syncs late: no longer two in a row
        assert nt._wearable_silent(p, w.ctl.clock.now_ts()) is None


@pytest.mark.skip(reason="GAP: when Health Connect stops supplying a type it used to (sleep stages, or heart rate), nothing says "
                         "so. Trigger: a type present on 3 of the last 4 nights is absent on the last 2 while other types still "
                         "arrive. Proposed wording: 'Health Connect has stopped sharing your sleep stages. Check the permission "
                         "in Settings > Watch or ring.'")
def test_gap_health_connect_dropped_a_type():
    pass


@pytest.mark.skip(reason="GAP: readings dropped for being in the future, years old or out of range are counted in the import log "
                         "but no sentence says so. Trigger: more than 10% of a batch rejected as future. Proposed wording: "
                         "'Some readings were dated in the future, so your phone's clock may be wrong.'")
def test_gap_future_readings_not_explained():
    pass
