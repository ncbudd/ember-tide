"""The morning after the alarm rang, and an early start in the calendar (2026-11-27: the 6:00 alarm rang, the phone's
next alarm flipped between the snooze and an 8:30 backup alarm, the wake moved to 8:30 and then back to the planned
9:30 on "no alarm", so getting up at 6:46 was read as a night-time trip and the side kept running)."""

from datetime import datetime

from embertide.engine import phone_context as PC
from test_scenarios import Night


def _at(n, ts: float) -> str:
    return datetime.fromtimestamp(ts, n.clock.tz).strftime("%Y-%m-%dT%H:%M")


async def _night(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:00")
    n.present("left", True)
    await n.until("2026-11-05T23:00")
    e = n.e(n.p1)
    return n, e, e.night.wake


async def test_backup_alarm_after_the_alarm_rang_keeps_the_wake(tmp_path):
    n, e, planned = await _night(tmp_path)
    alarm = planned - 3 * 3600
    PC.update(n.store, n.p1.id, {"next_alarm": alarm}, n.clock.now_ts())
    await n.step(2)
    assert e.s.wake_override == alarm
    await n.until(_at(n, alarm + 60))
    backup = alarm + 150 * 60                                                        # a backup alarm, before the planned wake
    for nxt in (backup, alarm + 10 * 60, backup, alarm + 55 * 60, backup, None):      # what the phone sent that morning
        PC.update(n.store, n.p1.id, {"next_alarm": nxt}, n.clock.now_ts())
        await n.step(2)
        assert e.s.wake_override == alarm and abs(e.night.wake - alarm) < 1, nxt
    assert not n.events("wake_alarm_cleared")


async def test_getting_up_after_the_alarm_ends_the_night(tmp_path):
    n, e, planned = await _night(tmp_path)
    alarm = planned - 3 * 3600
    PC.update(n.store, n.p1.id, {"next_alarm": alarm}, n.clock.now_ts())
    await n.until(_at(n, alarm + 40 * 60))
    PC.update(n.store, n.p1.id, {"next_alarm": alarm + 150 * 60}, n.clock.now_ts())  # backup alarm still set
    await n.step(2)
    n.present("left", False)
    await n.step(16)                                                                  # 8 min out of bed
    assert e.s.state == "done", e.s.state
    assert not n.side("left").on
    PC.update(n.store, n.p1.id, {"next_alarm": alarm + 150 * 60}, n.clock.now_ts())  # the same backup alarm: no reopen
    await n.step(4)
    assert e.s.state == "done" and e.s.wake_override == alarm


async def test_alarm_moved_later_before_it_rings_is_still_followed(tmp_path):
    n, e, planned = await _night(tmp_path)
    alarm = planned - 3600
    PC.update(n.store, n.p1.id, {"next_alarm": alarm}, n.clock.now_ts())
    await n.step(2)
    PC.update(n.store, n.p1.id, {"next_alarm": planned + 1800}, n.clock.now_ts())     # set later to sleep in
    await n.step(2)
    assert e.s.wake_override == planned + 1800


async def test_early_event_moves_the_wake_earlier(tmp_path):
    n, e, planned = await _night(tmp_path)
    event = planned - 2 * 3600
    PC.update(n.store, n.p1.id, {"early_start": event}, n.clock.now_ts())
    await n.step(2)
    assert e.s.wake_override == event - 45 * 60 and e.s.wake_source == "calendar"
    assert abs(e.night.wake - (event - 45 * 60)) < 1
    assert n.events("wake_from_calendar")


async def test_event_an_hour_after_the_planned_wake_changes_nothing(tmp_path):
    n, e, planned = await _night(tmp_path)
    PC.update(n.store, n.p1.id, {"early_start": planned + 3600}, n.clock.now_ts())
    await n.step(2)
    assert e.s.wake_override is None


async def test_earlier_alarm_wins_over_the_calendar(tmp_path):
    n, e, planned = await _night(tmp_path)
    alarm = planned - 3 * 3600
    PC.update(n.store, n.p1.id, {"next_alarm": alarm, "early_start": planned - 3600}, n.clock.now_ts())
    await n.step(2)
    assert e.s.wake_override == alarm and e.s.wake_source is None


async def test_calendar_earlier_than_the_alarm_wins_and_the_alarm_does_not_undo_it(tmp_path):
    n, e, planned = await _night(tmp_path)
    alarm = planned - 3600
    event = planned - 2 * 3600                                                        # warm wake 2 h 45 min early
    PC.update(n.store, n.p1.id, {"next_alarm": alarm, "early_start": event}, n.clock.now_ts())
    await n.step(4)
    assert e.s.wake_override == event - 45 * 60 and e.s.wake_source == "calendar"
    # the event is cancelled: back to the alarm, not the planned wake
    PC.update(n.store, n.p1.id, {"early_start": None}, n.clock.now_ts())
    await n.step(2)
    assert e.s.wake_override == alarm
    assert n.events("wake_calendar_cleared")


async def test_wake_set_by_hand_wins_over_the_calendar(tmp_path):
    n, e, planned = await _night(tmp_path)
    e.s.wake_override, e.s.wake_by_hand, e.s.wake_source = planned - 600, True, "hand"
    PC.update(n.store, n.p1.id, {"early_start": planned - 2 * 3600}, n.clock.now_ts())
    await n.step(2)
    assert e.s.wake_override == planned - 600


async def test_calendar_setting_off(tmp_path):
    n, e, planned = await _night(tmp_path)
    n.p1.settings.wake_with_calendar = False
    PC.update(n.store, n.p1.id, {"early_start": planned - 2 * 3600}, n.clock.now_ts())
    await n.step(2)
    assert e.s.wake_override is None


async def test_next_event_after_the_calendar_wake_came_moves_nothing(tmp_path):
    """Review: the first event starts, the calendar's 'first event' becomes the second one: no later wake, no reopen."""
    n, e, planned = await _night(tmp_path)
    ev1 = planned - 2 * 3600
    PC.update(n.store, n.p1.id, {"early_start": ev1}, n.clock.now_ts())
    await n.step(2)
    w = ev1 - 45 * 60
    assert e.s.wake_override == w
    await n.until(_at(n, w + 10 * 60))
    PC.update(n.store, n.p1.id, {"early_start": ev1 + 3600}, n.clock.now_ts())
    await n.step(4)
    assert e.s.wake_override == w


async def test_a_deleted_alarm_does_not_come_back_with_the_calendar(tmp_path):
    n, e, planned = await _night(tmp_path)
    alarm, event = planned - 3600, planned - 75 * 60                                 # calendar wake 2 h before planned
    PC.update(n.store, n.p1.id, {"next_alarm": alarm, "early_start": event}, n.clock.now_ts())
    await n.step(4)
    assert e.s.wake_override == event - 45 * 60
    PC.update(n.store, n.p1.id, {"next_alarm": None}, n.clock.now_ts())              # alarm deleted
    await n.step(2)
    assert e.s.wake_from_alarm is None and e.s.wake_override == event - 45 * 60
    PC.update(n.store, n.p1.id, {"early_start": None}, n.clock.now_ts())             # and the event cancelled
    await n.step(2)
    assert e.s.wake_override is None and abs(e.night.wake - planned) < 1


async def test_backup_alarm_first_seen_after_getting_up_is_not_followed_out_of_bed(tmp_path):
    n, e, planned = await _night(tmp_path)
    alarm = planned - 3 * 3600
    PC.update(n.store, n.p1.id, {"next_alarm": alarm}, n.clock.now_ts())
    await n.until(_at(n, alarm + 20 * 60))
    n.present("left", False)
    await n.step(16)
    assert e.s.state == "done"
    PC.update(n.store, n.p1.id, {"next_alarm": alarm + 150 * 60}, n.clock.now_ts())  # appears only now
    await n.step(4)
    assert e.s.state == "done" and e.s.wake_override == alarm and not n.side("left").on


async def test_calendar_setting_switched_off_gives_the_wake_back(tmp_path):
    n, e, planned = await _night(tmp_path)
    PC.update(n.store, n.p1.id, {"early_start": planned - 2 * 3600}, n.clock.now_ts())
    await n.step(2)
    assert e.s.wake_source == "calendar"
    n.p1.settings.wake_with_calendar = False
    await n.step(2)
    assert e.s.wake_override is None and abs(e.night.wake - planned) < 1
