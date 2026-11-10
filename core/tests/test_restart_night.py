"""A night given up early starts again (engine/night.py, _given_up_early). 2026-11-09 on the real bed: lying down for
a few minutes at 9:06 PM (testing a sensor) started the night and anchored the rhythm there; back in bed for real at
12:10 AM, it resumed three hours along, straight into "Deep" with the cool "Fall asleep" skipped. Now a night given
up early starts again from the beginning; a night really under way (a 3 AM hour up) resumes as always."""

import pytest

from embertide.engine.controller import Controller
from embertide.engine.summary import summarize
from test_scenarios import TZ, Night, ts


def midnight_bedtime(n: Night) -> Night:
    for rh in n.p1.rhythms:
        rh.schedule.bedtime = "00:00"
    return n


async def last_night(tmp_path, *, back="2026-11-10T00:10", restart_at=None, hook=None) -> Night:
    """In at 21:06 for ~7 min (bedtime 00:00), up from 21:13, back at `back`."""
    n = midnight_bedtime(Night(tmp_path, "2026-11-09T20:50", echo=False))
    await n.until("2026-11-09T21:06")
    n.present("left", True)
    await n.until("2026-11-09T21:13")
    assert n.e(n.p1).s.started and n.e(n.p1).s.in_bed == pytest.approx(ts("2026-11-09T21:06"), abs=60)
    n.present("left", False)
    for at in ([restart_at] if restart_at else []):
        await n.until(at)
        restart(n)
    if hook:
        await hook(n)
    await n.until(back)
    n.present("left", True)
    return n


def restart(n: Night, down_s: float = 300) -> None:
    n.c._persist()
    n.clock.advance(down_s)
    n.c = Controller(n.hh, n.bed, n.store, n.clock)


async def test_last_night_starts_again_from_the_beginning(tmp_path):
    n = midnight_bedtime(Night(tmp_path, "2026-11-09T20:50", echo=False))
    await n.until("2026-11-09T21:06")
    n.present("left", True)
    await n.until("2026-11-09T21:10")
    e = n.e(n.p1)
    e.adjust(n.clock.now_ts(), temp_c=19.0, until="night")         # for the whole night
    await n.until("2026-11-09T21:13")
    n.present("left", False)
    await n.until("2026-11-09T22:00")
    assert e.s.state == "out_of_bed" and not n.side("left").on and e.s.in_bed == pytest.approx(ts("2026-11-09T21:06"), abs=60)
    await n.until("2026-11-10T00:10")
    n.present("left", True)
    await n.until("2026-11-10T00:16")
    assert e.s.started and e.s.in_bed == pytest.approx(ts("2026-11-10T00:10"), abs=60)
    assert e.s.phase_id == "fall_asleep" and e.s.state == "rhythm"
    assert e.night.phases[1].start == pytest.approx(ts("2026-11-10T00:10"), abs=60)                 # "Fall asleep" from the new start
    restarted = n.events("rhythm_restarted")
    assert len(restarted) == 1
    assert restarted[0]["in_bed"] == pytest.approx(ts("2026-11-10T00:10"), abs=60) and restarted[0]["was_in_bed"] == pytest.approx(ts("2026-11-09T21:06"), abs=60)
    assert restarted[0]["up_since"] == pytest.approx(ts("2026-11-09T21:13"), abs=31)
    assert len(n.events("rhythm_started")) == 2 and n.events("rhythm_started")[0].get("restarted")
    # a change for the whole night carries into the night that started again
    assert e.s.override is not None and e.s.override.until == "night" and e.s.target_c == pytest.approx(19.0)
    await n.until("2026-11-10T00:55")
    assert e.s.phase_id == "deep"                                            # 40 min after the new start
    await n.until("2026-11-10T02:00")
    s = summarize(n.store, n.p1, e.night.date, TZ, n.clock.now_ts())
    assert s["in_bed_at"] == pytest.approx(ts("2026-11-10T00:10"), abs=60)
    assert s["earlier_in_bed"]["at"] == pytest.approx(ts("2026-11-09T21:06"), abs=60) and s["earlier_in_bed"]["minutes"] <= 8
    assert 105 <= s["in_bed_min"] <= 112 and s["times_up"] == 0              # the 3 h up isn't a time up in the night
    kinds = [m["kind"] for m in s["moments"]]
    assert "rhythm_started" not in kinds and kinds.count("rhythm_restarted") == 1
    m = next(m for m in s["moments"] if m["kind"] == "rhythm_restarted")
    assert m["ts"] == pytest.approx(ts("2026-11-10T00:10"), abs=60) and m["text"] == "Started again from the beginning (you'd been up since 9:13 PM)"


async def test_a_change_for_a_phase_of_the_given_up_night_ends_with_it(tmp_path):
    n = midnight_bedtime(Night(tmp_path, "2026-11-09T20:50", echo=False))
    await n.until("2026-11-09T21:06")
    n.present("left", True)
    await n.until("2026-11-09T21:10")
    e = n.e(n.p1)
    e.adjust(n.clock.now_ts(), delta_c=-0.5, until="next_phase")   # "Fall asleep" a little cooler
    await n.until("2026-11-09T21:13")
    n.present("left", False)
    await n.until("2026-11-10T00:10")
    assert e.s.override is not None                               # kept while up (nothing changes while they're out)
    n.present("left", True)
    await n.until("2026-11-10T00:16")
    assert e.s.phase_id == "fall_asleep" and e.s.override is None and e.s.target_c == pytest.approx(e.s.base_c, abs=0.01)
    assert any(x.get("reason") == "night started again" for x in n.events("adjust_ended"))
    # a change made while up, before getting back in (no phase), is meant for the night they're starting
    n2 = await last_night(tmp_path / "b", hook=None, back="2026-11-10T00:09")
    n2.present("left", False)
    e2 = n2.e(n2.p1)
    e2.adjust(n2.clock.now_ts(), delta_c=-0.5, until="next_phase")
    n2.present("left", True)
    await n2.until("2026-11-10T00:16")
    assert e2.s.phase_id == "fall_asleep" and e2.s.override is not None and e2.s.target_c == pytest.approx(e2.s.base_c - 0.5, abs=0.01)


async def test_an_earlier_stretch_inside_the_night_window_isnt_the_nights_start(tmp_path):
    """Usual bedtime 22:15: the summary's window opens at 20:30, so the given-up stretch is in its history too."""
    n = Night(tmp_path, "2026-11-05T20:55", echo=False)
    n.present("left", True)
    await n.until("2026-11-05T21:20")
    n.present("left", False)
    await n.until("2026-11-05T23:00")
    n.present("left", True)
    await n.until("2026-11-06T01:00")
    e = n.e(n.p1)
    assert e.s.in_bed == pytest.approx(ts("2026-11-05T23:00"), abs=60) and len(n.events("rhythm_restarted")) == 1
    s = summarize(n.store, n.p1, e.night.date, TZ, n.clock.now_ts())
    assert s["in_bed_at"] == pytest.approx(ts("2026-11-05T23:00"), abs=60) and s["times_up"] == 0
    assert 115 <= s["in_bed_min"] <= 121 and 18 <= s["earlier_in_bed"]["minutes"] <= 21
    assert [m["kind"] for m in s["moments"]].count("rhythm_restarted") == 1
    assert not any(m["kind"] in ("rhythm_started", "out_of_bed") for m in s["moments"])


async def test_a_3am_bathroom_hour_resumes(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:00", echo=False)
    n.present("left", True)
    await n.until("2026-11-06T03:00")
    in_bed = n.e(n.p1).s.in_bed
    n.present("left", False)
    await n.until("2026-11-06T04:05")
    n.present("left", True)
    await n.step(4)
    e = n.e(n.p1)
    assert e.s.state == "rhythm" and e.s.in_bed == in_bed and e.s.phase_id == "late"
    assert not n.events("rhythm_restarted") and len(n.events("rhythm_started")) == 1


async def test_up_61_min_after_3_hours_asleep_resumes(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:00", echo=False)
    n.present("left", True)
    await n.until("2026-11-06T01:20")
    in_bed = n.e(n.p1).s.in_bed
    n.present("left", False)
    await n.until("2026-11-06T02:21")
    n.present("left", True)
    await n.step(10)
    assert n.e(n.p1).s.state == "rhythm" and n.e(n.p1).s.in_bed == in_bed and n.e(n.p1).s.phase_id == "deep"
    assert not n.events("rhythm_restarted")


async def test_an_hour_reading_before_bedtime_then_up_starts_again(tmp_path):
    """Up before their usual bedtime: up to 90 min in bed still counts as given up early."""
    n = midnight_bedtime(Night(tmp_path, "2026-11-09T21:00", echo=False))
    n.present("left", True)
    await n.until("2026-11-09T22:05")
    n.present("left", False)
    await n.until("2026-11-10T00:20")
    n.present("left", True)
    await n.step(10)
    assert n.e(n.p1).s.in_bed == pytest.approx(ts("2026-11-10T00:20"), abs=60) and n.e(n.p1).s.phase_id == "fall_asleep"


async def test_up_just_under_an_hour_resumes(tmp_path):
    n = await last_night(tmp_path, back="2026-11-09T22:12")      # 59 min up
    await n.step(10)
    assert n.e(n.p1).s.in_bed == pytest.approx(ts("2026-11-09T21:06"), abs=60) and not n.events("rhythm_restarted")


async def test_not_in_the_last_two_hours_before_wake(tmp_path):
    n = await last_night(tmp_path, back="2026-11-10T04:40")      # wake 06:30
    await n.step(10)
    assert n.e(n.p1).s.in_bed == pytest.approx(ts("2026-11-09T21:06"), abs=60) and n.e(n.p1).s.state == "rhythm"
    assert not n.events("rhythm_restarted")


async def test_the_radar_refusing_the_second_start_starts_nothing(tmp_path, monkeypatch):
    n = midnight_bedtime(Night(tmp_path, "2026-11-09T20:50", echo=False))
    seen = {"left": 1, "right": 0}
    monkeypatch.setattr(n.c, "_radar", lambda zone: seen[zone])
    await n.until("2026-11-09T21:06")
    n.present("left", True)
    await n.until("2026-11-09T21:20")
    assert n.e(n.p1).s.started                                   # the radar backed the first one
    n.present("left", False)
    seen["left"] = 0
    await n.until("2026-11-10T00:10")
    n.present("left", True)                                      # the strip lights, the radar sees nobody
    await n.until("2026-11-10T00:40")
    e = n.e(n.p1)
    assert not e.s.started and e.s.in_bed is None and not n.events("rhythm_restarted")
    assert e.s.state != "rhythm"
    seen["left"] = 1                                             # now it does: as for any start, from when they lay down
    await n.until("2026-11-10T00:55")
    assert e.s.started and len(n.events("rhythm_restarted")) == 1
    assert e.s.in_bed == pytest.approx(ts("2026-11-10T00:10"), abs=60)


@pytest.mark.parametrize("when", ["while_up", "while_starting_again"])
async def test_a_restart_before_or_while_starting_again(tmp_path, when):
    if when == "while_starting_again":
        n = await last_night(tmp_path)
        await n.step(3)                                          # given up, starting again (not started yet)
        assert not n.e(n.p1).s.started and n.e(n.p1).s.reanchor
        restart(n, down_s=30)
    else:
        n = await last_night(tmp_path, restart_at="2026-11-09T22:30")
    await n.until("2026-11-10T00:20")
    e = n.e(n.p1)
    assert e.s.started and e.s.phase_id == "fall_asleep" and len(n.events("rhythm_restarted")) == 1
    assert e.s.in_bed == pytest.approx(ts("2026-11-10T00:10"), abs=60)
    restart(n, down_s=60)                                        # and after: the new start stays
    await n.step(2)
    assert n.e(n.p1).s.in_bed == e.s.in_bed and len(n.events("rhythm_restarted")) == 1


async def test_away_starts_nothing(tmp_path):
    from embertide.engine import away

    async def go_away(n):
        await n.until("2026-11-09T23:00")
        away.start(n.c, "p1", n.clock.now_ts())
    n = await last_night(tmp_path, hook=go_away)
    await n.step(20)
    assert n.e(n.p1).s.state == "away" and not n.side("left").on and not n.events("rhythm_restarted")


async def test_a_guest_on_the_side_starts_nothing(tmp_path):
    async def guest(n):
        await n.until("2026-11-09T23:00")
        n.c.guest_start(["left"], 22.0, None, None)
    n = await last_night(tmp_path, hook=guest)
    await n.step(20)
    assert n.e(n.p1).s.state == "guest" and not n.events("rhythm_restarted")


async def test_off_tonight_stays_off(tmp_path):
    async def skip(n):
        await n.until("2026-11-09T23:00")
        n.e(n.p1).tonight(n.clock.now_ts(), "skip")
    n = await last_night(tmp_path, hook=skip)
    await n.step(20)
    assert n.e(n.p1).s.state == "skipped" and not n.side("left").on


async def test_paused_stays_off_until_the_pause_ends(tmp_path):
    async def pause(n):
        await n.until("2026-11-10T00:00")
        n.e(n.p1).tonight(n.clock.now_ts(), "pause", minutes=30)
    n = await last_night(tmp_path, hook=pause)
    await n.step(10)
    assert n.e(n.p1).s.state == "paused" and not n.side("left").on
    await n.until("2026-11-10T00:35")
    assert n.e(n.p1).s.state == "rhythm" and n.e(n.p1).s.phase_id == "fall_asleep"


async def test_a_night_started_by_hand_isnt_started_again(tmp_path):
    n = midnight_bedtime(Night(tmp_path, "2026-11-09T21:00", echo=False))
    await n.step(1)
    n.e(n.p1).tonight(n.clock.now_ts(), "start_now")
    n.present("left", True)
    await n.until("2026-11-09T21:10")
    n.present("left", False)
    await n.until("2026-11-10T00:10")
    n.present("left", True)
    await n.step(10)
    assert n.e(n.p1).s.in_bed == pytest.approx(ts("2026-11-09T21:00"), abs=31) and not n.events("rhythm_restarted")


async def test_the_story_and_rollover_after_starting_again(tmp_path):
    n = await last_night(tmp_path)
    await n.until("2026-11-10T07:00")
    n.present("left", False)
    await n.until("2026-11-10T12:05")                            # noon: the next night begins as usual
    e = n.e(n.p1)
    assert e.s.key == "2026-11-10" and not e.s.started and e.s.reanchor is None and e.s.started_by is None
    s = summarize(n.store, n.p1, __import__("datetime").date(2026, 11, 9), TZ, n.clock.now_ts())
    assert s["in_bed_at"] == pytest.approx(ts("2026-11-10T00:10"), abs=60) and not s["late_by_min"]
    assert s["in_bed_min"] >= 380 and s["times_up"] == 0
