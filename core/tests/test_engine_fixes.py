"""Night engine fixes from the 2026-11 bug pass: Start now really starts, "off" after "I'm up" is for tonight, a night
switched off still finishes, the empty-side cutoff doesn't outlive its afternoon, and a by-hand temperature right after
"Off tonight" turns the side on."""
from datetime import datetime

from test_scenarios import TZ, Night


def iso(t):
    return datetime.fromtimestamp(t, TZ).replace(tzinfo=None).isoformat()


async def test_start_now_after_a_pause_starts(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    await n.step(2)
    e = n.e(n.p1)
    now = n.clock.now_ts()
    e.tonight(now, "pause", minutes=60)
    await n.step(2)
    assert e.s.state == "paused"
    e.tonight(n.clock.now_ts(), "start_now")
    await n.step(2)
    assert e.s.state in ("rhythm", "out_of_bed_hold") and n.side("left").on


async def test_start_now_after_out_of_bed_starts(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T00:30")
    n.present("left", False)
    await n.until("2026-11-06T02:00")
    e = n.e(n.p1)
    assert e.s.state == "out_of_bed" and not n.side("left").on
    e.tonight(n.clock.now_ts(), "start_now")
    await n.step(2)
    assert e.s.state != "out_of_bed" and n.side("left").on


async def test_off_after_im_up_skips_tonight_not_the_ended_night(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T05:00")
    e = n.e(n.p1)
    assert n.clock.now_ts() < e.night.end
    e.tonight(n.clock.now_ts(), "end")                   # "I'm up" an hour or two early
    await n.step(2)
    e.tonight(n.clock.now_ts(), "skip")                  # then "Off tonight": meant for the coming night
    await n.step(2)
    assert e.s.skip_next is True and e.s.skip is False and e.s.state == "done"


async def test_a_night_switched_off_still_finishes_at_its_end(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T02:00")
    e = n.e(n.p1)
    e.tonight(n.clock.now_ts(), "skip")                  # off at 2 AM, still in bed
    await n.step(2)
    assert e.s.state == "skipped" and not n.side("left").on
    n.present("left", False)
    await n.until(iso(e.night.end + 300))
    assert e.s.state == "done" and len(n.events("night_done")) == 1 and not n.side("left").on


async def test_empty_side_cutoff_from_the_afternoon_doesnt_stop_preconditioning(tmp_path):
    n = Night(tmp_path, "2026-11-05T13:00")
    await n.step(2)
    e = n.e(n.p1)
    n.present("left", False)
    e.adjust(n.clock.now_ts(), temp_c=24.0)              # turned on by hand in the afternoon, then nobody came
    await n.until("2026-11-05T17:30")
    assert e.s.empty_off and not n.side("left").on
    await n.until(iso(e.night.precool + 600))
    assert e.s.state == "waiting" and not e.s.empty_off and n.side("left").on


async def test_temperature_right_after_off_tonight_turns_the_side_on(tmp_path):
    n = Night(tmp_path, "2026-11-05T15:00")
    await n.step(2)
    e = n.e(n.p1)
    e.tonight(n.clock.now_ts(), "skip")
    await n.step(2)
    assert e.s.state == "skipped"
    e.adjust(n.clock.now_ts(), temp_c=22.0)
    await n.step(2)
    assert e.s.override is not None and e.s.override.manual and n.side("left").on
