"""Morning "up N×" counts only trips while the night was running (rhythm_started .. schedule end). 2026-11-11: in and
out of bed several times before the night began at 1:14 AM, one real trip at 1:15, a 4-minute stir after the night was
done: it said up 4×, should say 1×."""

from datetime import date, datetime, timedelta

import pytest

from embertide.engine.rhythm import pick_rhythm, resolve
from embertide.engine.summary import summarize
from test_scenarios import TZ, Night, ts

D = date(2026, 11, 11)


def _t(hhmm: str, day: int = 12) -> float:
    return ts(f"2026-11-{day:02d}T{hhmm}")


def _build(tmp_path, spans, started=_t("01:14"), now=None):
    """spans: [(from, to, occupied)] in epoch seconds; one row every 30 s; started: rhythm_started time or None."""
    n = Night(tmp_path, "2026-11-11T20:00", echo=False)
    p = n.p1
    rows = []
    for a, b, occ in spans:
        t = a
        while t < b:
            rows.append((t, p.zone, 1, 18.0, 18.0, occ, 58 if occ else None, 14 if occ else None, 0 if occ else None, 18.0, "deep", occ, p.id))
            t += 30
    n.store.telemetry(rows)
    if started is not None:
        n.store.event(started, "rhythm_started", person=p.id, in_bed=started - 480)
    end = resolve(pick_rhythm(p.rhythms, D), D, TZ).end
    return n, p, end


def _sum(n, p, now):
    return summarize(n.store, p, D, TZ, now, raw=True)


def test_real_night_2026_11_11(tmp_path):
    end = resolve(pick_rhythm(Night(tmp_path / "x", "2026-11-11T20:00", echo=False).p1.rhythms, D), D, TZ).end   # night done
    spans = [
        (_t("00:04"), _t("00:09"), 1),
        (_t("00:09"), _t("00:56"), 0),      # out 47 min (before the night started)
        (_t("00:56"), _t("01:15"), 1),      # back in (the night starts at 01:14)
        (_t("01:15"), _t("01:28"), 0),      # the real trip
        (_t("01:28"), end, 1),
        (end + 1140, end + 1140 + 240, 0),  # 4 min up ~19 min after the night is done
        (end + 1140 + 240, end + 3600, 1),
    ]
    n, p, end = _build(tmp_path / "y", spans)
    s = _sum(n, p, end + 7200)
    assert s["times_up"] == 1 and s["up_minutes"] == pytest.approx(13, abs=1)
    ups = [m for m in s["moments"] if m["kind"] == "out_of_bed"]
    assert len(ups) == 1 and ups[0]["ts"] == pytest.approx(_t("01:15"), abs=60)
    assert s["earlier_in_bed"] and s["earlier_in_bed"]["at"] == pytest.approx(_t("00:04"), abs=60)   # the pre-start stretch isn't lost
    assert s["in_bed_at"] == pytest.approx(_t("00:04"), abs=60)                                        # in_bed numbers unchanged


def test_without_a_start_event_every_gap_counts(tmp_path):
    spans = [(_t("00:04"), _t("00:09"), 1), (_t("00:09"), _t("00:56"), 0), (_t("00:56"), _t("01:15"), 1),
             (_t("01:15"), _t("01:28"), 0), (_t("01:28"), _t("03:00"), 1)]
    n, p, end = _build(tmp_path, spans, started=None)
    s = _sum(n, p, end + 7200)
    assert s["times_up"] == 2 and s["earlier_in_bed"] is None


def test_trip_straddling_the_start_is_not_counted(tmp_path):
    # out 01:10-01:20, the night starts at 01:14 (a trip that began before the start is not "up in the night")
    spans = [(_t("00:50"), _t("01:10"), 1), (_t("01:10"), _t("01:20"), 0), (_t("01:20"), _t("03:00"), 1)]
    n, p, end = _build(tmp_path, spans)
    assert _sum(n, p, end + 7200)["times_up"] == 0
    # one that began after the start counts whole
    spans = [(_t("00:50"), _t("01:30"), 1), (_t("01:30"), _t("01:40"), 0), (_t("01:40"), _t("03:00"), 1)]
    n, p, end = _build(tmp_path / "b", spans)
    s = _sum(n, p, end + 7200)
    assert s["times_up"] == 1 and s["up_minutes"] == 10


def test_night_in_progress_counts_trips_so_far(tmp_path):
    spans = [(_t("00:50"), _t("01:30"), 1), (_t("01:30"), _t("01:40"), 0), (_t("01:40"), _t("02:30"), 1)]
    n, p, end = _build(tmp_path, spans)
    s = _sum(n, p, _t("02:30"))
    assert s["in_progress"] and s["times_up"] == 1


def test_alarm_ends_the_night_before_the_schedule(tmp_path):
    # the alarm finished the night 45 min before the schedule's end: a stir after that isn't "up in the night"
    end0 = resolve(pick_rhythm(Night(tmp_path / "x", "2026-11-11T20:00", echo=False).p1.rhythms, D), D, TZ).end
    done = end0 - 2700
    spans = [(_t("01:00"), done + 1140, 1), (done + 1140, done + 1380, 0), (done + 1380, end0 + 600, 1)]
    n, p, end = _build(tmp_path / "y", spans)
    assert _sum(n, p, end + 7200)["times_up"] == 1          # no night_done event: the schedule's end is the cut
    n.store.event(done, "night_done", person=p.id, end=done)
    assert _sum(n, p, end + 7200)["times_up"] == 0


async def test_engine_records_night_done_once(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00", scripted=True)
    await n.until("2026-11-06T09:00", dt=60)
    for p in (n.p1, n.p2):
        assert len([e for e in n.events("night_done") if e.get("person") == p.id]) == 1
