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
