"""'I'm up': ending the night by hand, "off" in the morning, and a lie-in that ends when the person's own phone and
watch left even though something (a dog) stays on the pad."""
from datetime import datetime

import pytest
from test_api import client
from test_scenarios import TZ, Night

from embertide.engine.night import NightNotRunning


def _reason(ev):
    return ev.get("reason") or (ev.get("data") or {}).get("reason")


def iso(t):
    return datetime.fromtimestamp(t, TZ).replace(tzinfo=None).isoformat()


async def _lying_in(tmp_path, personal=None):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    if personal is not None:
        n.c._personal_signals = lambda zone: personal["v"]
    await n.until("2026-11-06T06:00")
    e = n.e(n.p1)
    end = e.night.end
    await n.until(iso(end + 600))
    assert e.s.lie_in and n.side("left").on
    return n, e, end


async def test_end_stops_lie_in_records_done_and_leaves_next_night(tmp_path):
    n, e, end = await _lying_in(tmp_path)
    now = n.clock.now_ts()
    e.tonight(now, "end")
    await n.step(2)
    assert e.s.state == "done" and not n.side("left").on and not e.s.lie_in
    assert e.s.skip_next is False and e.s.skip is False
    assert len(n.events("night_done")) == 1
    lie = n.events("lie_in_ended")
    assert len(lie) == 1 and _reason(lie[0]) == "ended by hand"
    await n.until(iso(end + 1800))
    assert e.s.state == "done" and len(n.events("night_done")) == 1       # getting back in doesn't restart it


async def test_end_before_the_night_is_over(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T03:00")
    e = n.e(n.p1)
    e.tonight(n.clock.now_ts(), "end")
    await n.step(2)
    assert e.s.state == "done" and not n.side("left").on and len(n.events("night_done")) == 1
    assert e.s.skip_next is False


async def test_end_after_a_pause_still_finishes_the_night(tmp_path):
    # a real morning: the side was paused from the app, then "I'm up": the night must read as done (the morning
    # summary waits for "done"), not stay "paused" until the pause runs out
    n, e, end = await _lying_in(tmp_path)
    e.tonight(n.clock.now_ts(), "pause", 60)
    await n.step(2)
    assert e.s.state == "paused"
    e.tonight(n.clock.now_ts(), "end")
    await n.step(2)
    assert e.s.state == "done" and e.s.pause_until is None and not n.side("left").on
    assert len(n.events("night_done")) == 1


async def test_end_refused_when_no_night_running(tmp_path):
    n = Night(tmp_path, "2026-11-05T12:00")
    await n.step(2)
    with pytest.raises(NightNotRunning):
        n.e(n.p1).tonight(n.clock.now_ts(), "end")


async def test_end_twice_is_refused(tmp_path):
    n, e, end = await _lying_in(tmp_path)
    e.tonight(n.clock.now_ts(), "end")
    with pytest.raises(NightNotRunning):
        e.tonight(n.clock.now_ts(), "end")


def test_end_api_409_when_no_night(tmp_path):
    with client(tmp_path) as c:
        assert c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}).status_code == 200
        r = c.post("/api/people/p1/tonight", json={"action": "end"})
        assert r.status_code == 409 and "no night" in r.json()["detail"].lower()


async def test_skip_during_lie_in_ends_this_night_not_the_next(tmp_path):
    n, e, end = await _lying_in(tmp_path)
    assert n.clock.now_ts() > end
    e.tonight(n.clock.now_ts(), "skip")
    await n.step(2)
    assert e.s.skip_next is False and e.s.skip is False
    assert e.s.state == "done" and not n.side("left").on and len(n.events("night_done")) == 1
    assert n.events("tonight_end") and not n.events("tonight_skip")


async def test_dial_off_in_the_wake_phase_ends_the_night(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T05:50")
    e = n.e(n.p1)
    while e.s.phase_id != "wake":
        await n.step(1)
    assert n.clock.now_ts() < e.night.end
    e.tonight(n.clock.now_ts(), "skip")                                      # what the dial's off press sends
    await n.step(2)
    assert e.s.state == "done" and e.s.skip is False and e.s.skip_next is False
    assert len(n.events("night_done")) == 1


async def test_skip_in_the_evening_still_means_off_tonight(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-06T01:00")
    e = n.e(n.p1)
    e.tonight(n.clock.now_ts(), "skip")
    await n.step(2)
    assert e.s.state == "skipped" and e.s.skip


# a real morning, with synthetic numbers: person + watch + phone present, lie-in, the person walks away (the personal
# votes vanish, the pad is empty a few minutes) and the dog gets on (pad and radar say present, watch can't match)
async def test_lie_in_ends_when_phone_and_watch_left_but_pad_stays(tmp_path):
    v = {"v": {"voting": True, "disagree": False}}
    n, e, end = await _lying_in(tmp_path, v)
    assert e.s.personal_seen
    e.s.snoozed_until = n.clock.now_ts() + 5 * 60                            # snoozed: a 10-min break doesn't end it
    n.present("left", False)                                                 # up and gone
    v["v"] = {"voting": False, "disagree": False}
    await n.step(10)                                                         # 5 min: the old 10-min tolerance is in force
    n.present("left", True)                                                  # the dog lies down on the warm side
    v["v"] = {"voting": False, "disagree": True}
    t0 = n.clock.now_ts()
    await n.step(2 * 8)
    assert n.events("lie_in_ended"), "should have ended by now"
    assert _reason(n.events("lie_in_ended")[0]) == "your phone and watch left the bed"
    assert e.s.state == "done" and not n.side("left").on
    assert n.clock.now_ts() - t0 <= 9 * 60


async def test_lie_in_stays_while_the_watch_still_vouches(tmp_path):
    v = {"v": {"voting": True, "disagree": False}}
    n, e, end = await _lying_in(tmp_path, v)
    await n.step(2 * 20)
    assert e.s.lie_in and n.side("left").on and not n.events("lie_in_ended")


async def test_lie_in_unchanged_without_phone_or_watch(tmp_path):
    v = {"v": {"voting": False, "disagree": True}}                           # never any personal vote
    n, e, end = await _lying_in(tmp_path, v)
    assert not e.s.personal_seen
    n.present("left", False)
    await n.step(2 * 2)                                                      # 2 min: still within the 3-min rule
    assert e.s.lie_in
    n.present("left", True)                                                  # something stays on the pad
    await n.until(iso(end + 44 * 60))
    assert e.s.lie_in and n.side("left").on                                  # until the cap, as today
    await n.until(iso(end + 46 * 60))
    assert e.s.state == "done"
    assert _reason(n.events("lie_in_ended")[0]) == "cap"


async def test_manual_adjust_in_inactive_states_never_keeps_bed_on(tmp_path):
    from test_scenarios import ts
    from embertide.engine.night import NightEngine
    from embertide.seed import default_household

    n = Night(tmp_path, "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-05T23:00")
    e = n.e(n.p1)
    n.present("left", False)
    await n.until("2026-11-05T23:35")
    assert e.s.state == "out_of_bed" and e.s.target_c is None
    e.adjust(n.clock.now_ts(), temp_c=28.0)
    await n.step(2)
    assert e.s.override is None and e.s.target_c is None and not n.side("left").on

    # Before precool in an inactive state ('away'), a manual override must neither heat now nor survive precool.
    e2 = NightEngine(default_household().people[0], TZ)
    e2.tick(ts("2026-11-05T21:05"), False)
    e2.away = True
    e2.tick(ts("2026-11-05T21:06"), False)
    assert e2.s.state == "away"
    e2.adjust(ts("2026-11-05T21:06:10"), temp_c=27.0)
    assert e2.tick(ts("2026-11-05T21:07"), False) is None
    assert e2.tick(ts("2026-11-05T21:35"), False) is None and e2.s.override is None


async def test_empty_side_safety_cutoff_after_two_hours(tmp_path):
    n = Night(tmp_path, "2026-11-05T14:00")
    n.present("left", False)
    await n.step(1)
    e = n.e(n.p1)
    e.adjust(n.clock.now_ts(), temp_c=27.0)   # no timer: "until tonight's schedule"
    await n.until("2026-11-05T15:58")
    assert e.s.target_c == 27.0 and n.side("left").on
    await n.until("2026-11-05T16:02")
    assert e.s.target_c is None and not n.side("left").on
    await n.step(10)
    assert len(n.events("empty_side_off")) == 1 and e.s.target_c is None


async def test_empty_side_cutoff_skips_flash_first_30min_and_guest_mode(tmp_path):
    from embertide.models import GuestSide

    n = Night(tmp_path / "flash", "2026-11-05T14:00")
    n.present("left", False)
    await n.step(1)
    e = n.e(n.p1)
    e.adjust(n.clock.now_ts(), temp_c=26.0)   # no timer
    await n.until("2026-11-05T15:50")
    assert e.s.target_c == 26.0
    e.flash("hot", n.clock.now_ts(), minutes=40)
    await n.until("2026-11-05T16:10")
    assert e.s.target_c is not None and n.side("left").on and not n.events("empty_side_off")
    # starting a flash is someone at the bed: the 2-hour empty-side clock starts again from it
    await n.until("2026-11-05T16:22")
    assert not n.events("empty_side_off")
    await n.until("2026-11-05T17:52")
    assert e.s.target_c is None and not n.side("left").on and len(n.events("empty_side_off")) == 1

    ng = Night(tmp_path / "guest", "2026-11-05T21:00")
    ng.present("right", False)
    ng.hh.guest["right"] = GuestSide(since=ng.clock.now_ts(), on=True, temp_c=23.0)
    await ng.until("2026-11-05T23:15")
    assert ng.side("right").on and not ng.events("empty_side_off")


async def test_flash_ends_on_out_of_bed_done_and_away(tmp_path):
    from embertide.engine import away as away_mod

    n = Night(tmp_path / "oob", "2026-11-05T22:20")
    n.present("left", True)
    await n.until("2026-11-05T23:00")
    e = n.e(n.p1)
    e.flash("hot", n.clock.now_ts(), minutes=60)
    await n.step(2)
    assert e.s.flash is not None and e.s.target_c is not None
    n.present("left", False)
    await n.until("2026-11-05T23:35")
    assert e.s.state == "out_of_bed" and e.s.flash is None and e.s.target_c is None and not n.side("left").on

    n2 = Night(tmp_path / "done", "2026-11-05T22:20")
    n2.present("left", True)
    await n2.until("2026-11-06T06:00")
    e2 = n2.e(n2.p1)
    e2.flash("cold", n2.clock.now_ts(), minutes=60)
    await n2.step(2)
    assert e2.s.flash is not None
    n2.present("left", False)
    await n2.until(iso(e2.night.end + 120))
    assert e2.s.state == "done" and e2.s.flash is None and e2.s.target_c is None

    n3 = Night(tmp_path / "away", "2026-11-05T22:20")
    n3.present("left", True)
    await n3.until("2026-11-05T22:45")
    e3 = n3.e(n3.p1)
    e3.flash("hot", n3.clock.now_ts(), minutes=60)
    away_mod.start(n3.c, "p1", n3.clock.now_ts(), by="app")
    await n3.step(2)
    assert e3.s.state == "away" and e3.s.flash is None and e3.s.target_c is None


async def test_empty_side_cutoff_leaves_a_chosen_timer_alone(tmp_path):
    # "turn my side on for 4 hours" to pre-warm an empty bed: the person chose that, so it runs its timer
    n = Night(tmp_path, "2026-11-05T13:00")
    n.present("left", False)
    await n.step(1)
    e = n.e(n.p1)
    e.adjust(n.clock.now_ts(), temp_c=27.0, minutes=240)
    await n.until("2026-11-05T16:30")
    assert e.s.target_c == 27.0 and not n.events("empty_side_off")
