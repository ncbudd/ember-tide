"""Lying in: still in bed when the night ends, the wake target runs on until they get up (or a cap)."""
from datetime import datetime

from test_scenarios import TZ, Night, ts


async def _to_end(tmp_path, snooze=None):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T06:00")
    e = n.e(n.p1)
    end = e.night.end
    return n, e, end, datetime.fromtimestamp(end, TZ).isoformat()


def iso(t):
    return datetime.fromtimestamp(t, TZ).replace(tzinfo=None).isoformat()


async def test_lie_in_keeps_side_on_until_they_get_up(tmp_path):
    n, e, end, _ = await _to_end(tmp_path)
    await n.until(iso(end + 600))
    assert e.s.state == "rhythm" and e.s.phase_id == "wake" and n.side("left").on
    assert not n.events("night_done") and n.events("lie_in_started")
    n.present("left", False)
    await n.until(iso(end + 600 + 120))
    assert e.s.state != "done"
    await n.until(iso(end + 600 + 300))
    assert e.s.state == "done" and not n.side("left").on
    nd = n.events("night_done")
    assert len(nd) == 1 and end + 600 + 150 <= nd[0]["ts"] <= end + 600 + 300


async def test_lie_in_capped_at_45_min(tmp_path):
    n, e, end, _ = await _to_end(tmp_path)
    await n.until(iso(end + 44 * 60))
    assert e.s.state == "rhythm"
    await n.until(iso(end + 46 * 60))
    assert e.s.state == "done" and len(n.events("night_done")) == 1


async def test_not_in_bed_at_end_is_done_on_time(tmp_path):
    n, e, end, _ = await _to_end(tmp_path)
    n.present("left", False)
    await n.until(iso(end - 600))
    await n.until(iso(end + 120))
    assert e.s.state == "done" and not n.events("lie_in_started")
    n.present("left", True)                                   # back in later: no lie-in for a finished night
    await n.until(iso(end + 600))
    assert e.s.state == "done"


async def test_snooze_extends_lie_in_through_flicker(tmp_path):
    n, e, end, _ = await _to_end(tmp_path)
    await n.until(iso(end - 60))
    alarm = end - 15 * 60  # the followed alarm rang before the end
    e.s.wake_override = e.s.wake_from_alarm = alarm
    later = alarm + 25 * 60
    assert not e.follow_alarm(n.clock.now_ts(), later)        # snooze: wake unchanged
    assert e.s.snoozed_until == later
    await n.step(1)
    assert n.events("alarm_snoozed")
    await n.until(iso(end + 120))
    n.present("left", False)                                  # a brief flicker (5 min, longer than the usual 3)
    await n.step(10)
    n.present("left", True)
    await n.until(iso(end + 44 * 60))
    assert e.s.state == "rhythm"
    await n.until(iso(end + 46 * 60))
    assert e.s.state == "done" and len(n.events("night_done")) == 1


async def test_skipped_night_has_no_lie_in(tmp_path):
    n, e, end, _ = await _to_end(tmp_path)
    e.s.skip = True
    await n.until(iso(end + 600))
    assert e.s.state == "skipped" and not n.events("lie_in_started")
