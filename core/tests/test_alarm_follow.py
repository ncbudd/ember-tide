"""The warm wake follows the phone's alarm, end to end through what the phone sends (phone_context.update → merge →
NightEngine.follow_alarm): turning the alarm off brings the planned wake back, but only while the phone is reporting."""

from datetime import datetime

from embertide.engine import phone_context as PC
from test_scenarios import Night


async def _followed(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:00")
    n.present("left", True)
    await n.until("2026-11-05T23:00")
    e = n.e(n.p1)
    planned = e.night.wake
    alarm = planned - 3600
    PC.update(n.store, n.p1.id, {"next_alarm": alarm}, n.clock.now_ts())
    await n.step(2)
    assert e.s.wake_override == alarm and abs(e.night.wake - alarm) < 1
    return n, e, planned, alarm


def _cleared(e, planned) -> bool:
    return e.s.wake_override is None and e.s.wake_from_alarm is None and abs(e.night.wake - planned) < 1


async def test_cancelled_alarm_the_next_one_is_the_day_after(tmp_path):
    n, e, planned, alarm = await _followed(tmp_path)
    PC.update(n.store, n.p1.id, {"next_alarm": alarm + 86400}, n.clock.now_ts())     # more than 24 h away: dropped
    await n.step(2)
    assert _cleared(e, planned)
    assert any(x["kind"] == "wake_alarm_cleared" for x in n.store.events(limit=50))


async def test_cancelled_alarm_weekend_next_one_days_away(tmp_path):
    n, e, planned, alarm = await _followed(tmp_path)
    PC.update(n.store, n.p1.id, {"next_alarm": alarm + 2 * 86400}, n.clock.now_ts())
    await n.step(2)
    assert _cleared(e, planned)


async def test_no_alarm_at_all(tmp_path):
    n, e, planned, alarm = await _followed(tmp_path)
    PC.update(n.store, n.p1.id, {"next_alarm": None}, n.clock.now_ts())              # the app's 0 = none set
    await n.step(2)
    assert _cleared(e, planned)


async def test_phone_silent_keeps_the_alarm(tmp_path):
    n, e, planned, alarm = await _followed(tmp_path)
    await n.step(240)                                                                 # two hours without a word
    assert e.s.wake_override == alarm
    # an old "no alarm" (sent long before now) isn't the phone reporting: nothing changes
    PC.update(n.store, n.p1.id, {"next_alarm": None}, n.clock.now_ts() - 2 * 3600)
    await n.step(2)
    assert e.s.wake_override == alarm and abs(e.night.wake - alarm) < 1


async def test_snooze_and_by_hand_still_hold(tmp_path):
    n, e, planned, alarm = await _followed(tmp_path)
    await n.until(datetime.fromtimestamp(alarm + 60, n.clock.tz).strftime("%Y-%m-%dT%H:%M"))
    PC.update(n.store, n.p1.id, {"next_alarm": alarm + 9 * 60}, n.clock.now_ts())    # rang and snoozed
    await n.step(2)
    assert e.s.wake_override == alarm
    PC.update(n.store, n.p1.id, {"next_alarm": None}, n.clock.now_ts())              # dismissed: it already rang
    await n.step(2)
    assert e.s.wake_override == alarm


async def test_a_wake_set_by_hand_wins(tmp_path):
    n, e, planned, alarm = await _followed(tmp_path)
    e.s.wake_override, e.s.wake_by_hand = alarm - 600, True
    PC.update(n.store, n.p1.id, {"next_alarm": None}, n.clock.now_ts())
    await n.step(2)
    assert e.s.wake_override == alarm - 600
