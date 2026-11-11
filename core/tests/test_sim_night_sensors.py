"""Whole-night simulations: which sensors start, hold and end a night, and what goes wrong when they fail.

Alex is on the left (p1), Sam on the right (p2). Work-night rhythm: bed 22:30, wake 07:00, the pad starts warming at
21:45. Each test runs a full night on a simulated clock (one-minute steps) through the real controller."""

import pytest

from embertide.units import f_to_c
from simnight import Plan, Sim, T, hm, ts


def deep_c() -> float:
    return f_to_c(63)


def assert_ordinary_night(r, p, zone="left", in_bed_by="22:50"):
    """The shape every healthy night has: pre-conditioning, one start, deep at midnight, warm wake, off at the end."""
    assert r.started_count(p) == 1
    tl = [s for _, s in r.timeline(p)]
    assert tl[:3] == ["day", "waiting", "rhythm"] and tl[-1] == "done", r.timeline(p)
    assert hm(ts(f"2026-11-05T{in_bed_by}")) >= dict(((s, t) for t, s in r.timeline(p)))["rhythm"]
    st, started, phase, on, sp = r.at(p, "2026-11-06T00:30")
    assert (st, phase, on) == ("rhythm", "deep", True) and sp == pytest.approx(deep_c() if zone == "left" else f_to_c(72), abs=0.15)
    assert r.at(p, "2026-11-06T03:30")[2] == "late"
    assert not r.bed.z[zone].on                                         # off after the warm wake


# ───────────────────────── sensor mixes ─────────────────────────
async def test_pad_only_normal_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    await r.run("2026-11-06T07:30", script=plan)
    assert_ordinary_night(r, r.p1)
    s = r.summary(r.p1)
    assert s["has_data"] and hm(s["in_bed_at"]) in ("22:40", "22:41", "22:42", "22:43") and s["times_up"] == 0
    assert "morning" in r.kinds("p1")
    assert not {"problem", "hub"} & set(r.kinds("p1"))


async def test_pad_and_radar_agree(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar = r.add("radar", "left")
    plan = Plan().sleep("left", "22:40", "06:50").drive(radar, "left")
    await r.run("2026-11-06T07:30", script=plan)
    assert_ordinary_night(r, r.p1)
    assert r.started_count(r.p1) == 1 and r.started_count(r.p2) == 0
    assert not {"problem", "not_started"} & set(r.kinds("p1"))


async def test_pad_radar_and_phone_on_charger(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar = r.add("radar", "left")
    plan = Plan().sleep("left", "22:40", "06:50").drive(radar, "left")
    plan.at("22:35", lambda r: r.phone(r.p1, wifi_home=True, charging=True, bedtime_mode=True))
    await r.run("2026-11-06T07:30", script=plan)
    assert_ordinary_night(r, r.p1)
    # the phone on the charger confirmed the start at once: the night began within the usual confirmation time
    ev = r.events("rhythm_started", "p1")[0]
    assert ts("2026-11-05T22:40") <= ev["ts"] <= ts("2026-11-05T22:47")


async def test_radar_only_when_the_pad_is_dead(tmp_path):
    """The strip sends garbage all night (status 255, no readings): the radar alone carries the night."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.bed.garbage["left"] = True
    radar = r.add("radar", "left")
    plan = Plan().sleep("left", "22:40", "06:50").drive(radar, "left")
    plan.pad_on["left"] = False
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    assert hm(r.events("rhythm_started", "p1")[0]["ts"]) <= "22:50"
    assert r.at(p, "2026-11-06T00:30")[2] == "deep"
    assert r.timeline(p)[-1][1] == "done" and not r.bed.z["left"].on


async def test_every_source_dead_falls_back_to_the_schedule(tmp_path):
    """No pad data, no other device: the bed does not stay cold. The night starts on the clock, and the owner is told the
    strip is silent (it would otherwise run on the schedule unnoticed)."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.bed.empty_block["left"] = r.bed.empty_block["right"] = True
    await r.run("2026-11-06T07:30")
    for p in (r.p1, r.p2):
        assert r.started_count(p) == 1
        ev = r.events("rhythm_started", p.id)[0]
        assert "schedule" in ev.get("reason", "") and hm(ev["ts"]) <= "22:50"
    assert r.at(r.p1, "2026-11-06T00:30")[2] == "deep"
    assert r.timeline(r.p1)[-1][1] == "done"


async def test_sources_that_disagree_for_half_an_hour_do_not_flap_the_night(tmp_path):
    """The pad says in bed; radar and a scale say empty for 30 minutes at 01:00. The pad is the strongest single voice,
    but two against one must not spin the side off and on."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar, scale = r.add("radar", "left"), r.add("scale", "left")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.drive(radar, "left", lambda o: 0.0 if ts("2026-11-06T01:00") <= r.clock.now_ts() < ts("2026-11-06T01:30") else float(o))
    plan.drive(scale, "left", lambda o: (0.0 if ts("2026-11-06T01:00") <= r.clock.now_ts() < ts("2026-11-06T01:30") else 70.0) if o else 0.0)
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    states = r.states(p, "2026-11-06T00:00", "2026-11-06T05:00")
    # whatever it decides, it never turns the side OFF for a person who is lying there
    assert "out_of_bed" not in states, r.timeline(p)
    assert r.at(p, "2026-11-06T03:30")[3] is True


async def test_a_stuck_occupied_radar_never_starts_a_night_nobody_is_in(tmp_path):
    """A radar stuck on 'someone is here' for days while the pad reads an empty side: no night, the empty side stops
    warming, and the owner is asked once whether to turn it off."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar = r.add("radar", "left", mode="event")
    radar.set(1.0)
    await r.run("2026-11-06T07:30", script=Plan())
    assert r.started_count(r.p1) == 0
    assert "missed" in r.states(r.p1, "2026-11-06T02:00", "2026-11-06T03:00")
    assert r.kinds("p1").count("empty_side") == 1


async def test_a_stuck_radar_with_a_dead_pad_is_the_known_blind_spot(tmp_path):
    """With the pad dead too, a radar stuck on is indistinguishable from a sleeper: the night runs. It is bounded: the
    side still turns off at the end of the night."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.bed.garbage["left"] = True
    radar = r.add("radar", "left", mode="event")
    radar.set(1.0)
    await r.run("2026-11-06T07:45", script=Plan())
    assert r.timeline(r.p1)[-1][1] == "done" and not r.bed.z["left"].on


async def test_a_flapping_radar_is_set_aside_and_a_good_pad_decides(tmp_path):
    """A radar flipping every ten seconds is set aside as erratic; the person in bed is never counted as getting up."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar = r.add("radar", "left", min_on_s=0, hold_s=0)
    plan = Plan().sleep("left", "22:40", "06:50")
    flip = {"n": 0}

    def f(r_, now):
        plan(r_, now)
        if ts("2026-11-06T01:00") <= now < ts("2026-11-06T01:30"):
            flip["n"] += 1
            radar.set(float(flip["n"] % 2))
        else:
            radar.set(1.0 if plan.occupied("left", now) else 0.0)
    r.dt = 10
    await r.run("2026-11-06T01:40", script=f)
    h = r.sources.health_of(radar.cfg, r.clock.now_ts())
    assert r.sources.health[radar.id]["status"] in ("erratic", "ok")
    assert any(e["kind"] == "device_health" and e["status"] == "erratic" for e in r.events()), "the flapping was noticed"
    assert "out_of_bed" not in r.states(r.p1, "2026-11-06T00:30", "2026-11-06T01:40")
    assert r.at(r.p1, "2026-11-06T01:35")[3] is True


async def test_event_only_mqtt_sensor_that_never_repeats(tmp_path):
    """An MQTT occupancy topic that publishes only on change: one 'on' at 22:40, one 'off' at 06:50, nothing between."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.bed.garbage["left"] = True
    mq = r.add("pad", "left", mode="event", driver="mqtt-topic", min_on_s=0, hold_s=0)
    plan = Plan().at("22:40", lambda r: mq.set(1.0)).at("06:50", lambda r: mq.set(0.0))
    plan.pad_on["left"] = False
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1 and r.at(p, "2026-11-06T04:00")[0] == "rhythm"
    assert r.timeline(p)[-1][1] == "done"


async def test_home_assistant_entity_goes_unavailable_mid_night(tmp_path):
    """The HA entity says 'unavailable' for two hours while the pad is fine: nothing changes. With the pad also silent,
    the running night carries on as it was (unknown is not 'got up')."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar = r.add("radar", "left")
    plan = Plan().sleep("left", "22:40", "06:50").drive(radar, "left")

    def go_unavailable(r_):
        radar.mode = "unavailable"

    def back(r_):
        radar.mode = "live"
    plan.at("01:00", go_unavailable).at("03:00", back)
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p1) == 1
    assert "out_of_bed" not in r.states(r.p1, "2026-11-06T00:00", "2026-11-06T06:00")
    assert r.at(r.p1, "2026-11-06T02:00")[0] == "rhythm"


async def test_unavailable_with_a_dead_pad_keeps_the_running_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar = r.add("radar", "left")
    plan = Plan().sleep("left", "22:40", "06:50").drive(radar, "left")
    plan.pad_on["left"] = False
    r.bed.garbage["left"] = True
    plan.at("01:00", lambda r_: setattr(radar, "mode", "unavailable")).at("03:00", lambda r_: setattr(radar, "mode", "live"))
    await r.run("2026-11-06T07:30", script=plan)
    states = r.states(r.p1, "2026-11-06T01:10", "2026-11-06T03:00")
    assert states == {"rhythm"}, states
    assert r.started_count(r.p1) == 1


@pytest.mark.parametrize("down_h", [10 / 60, 8])
async def test_home_assistant_down_mid_night(tmp_path, down_h):
    """HA stops answering at 01:00 (10 minutes or 8 hours). The pad keeps the night going; the radar is simply silent
    (the vote lapses, it does not stick)."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar = r.add("radar", "left")
    plan = Plan().sleep("left", "22:40", "06:50").drive(radar, "left")
    plan.at("01:00", lambda r_: setattr(radar, "mode", "down"))
    back = 1 + down_h
    if back < 7:
        plan.at(f"{int(back):02d}:{int(round((back % 1) * 60)):02d}", lambda r_: setattr(radar, "mode", "live"))
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p1) == 1
    assert "out_of_bed" not in r.states(r.p1, "2026-11-06T00:00", "2026-11-06T06:40")
    assert r.timeline(r.p1)[-1][1] == "done" and not r.bed.z["left"].on


async def test_mqtt_broker_down_for_eight_hours_with_the_pad_dead(tmp_path):
    """Broker down from 01:00 to morning, pad dead: the last 'in bed' message is all there is. The night must still
    end on time and the side turn off."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.bed.garbage["left"] = True
    mq = r.add("radar", "left", mode="event", driver="mqtt-topic")
    plan = Plan().at("22:40", lambda r_: mq.set(1.0))
    plan.pad_on["left"] = False
    await r.run("2026-11-06T07:45", script=plan)
    assert r.started_count(r.p1) == 1
    assert r.timeline(r.p1)[-1][1] == "done" and not r.bed.z["left"].on


async def test_a_radar_added_mid_night_does_not_disturb_it(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    holder = {}

    def add(r_):
        holder["radar"] = r_.add("radar", "left")
    plan.at("02:00", add)

    def f(r_, now):
        plan(r_, now)
        if "radar" in holder:
            holder["radar"].set(1.0 if plan.occupied("left", now) else 0.0)
    await r.run("2026-11-06T07:30", script=f)
    assert r.started_count(r.p1) == 1
    assert "out_of_bed" not in r.states(r.p1, "2026-11-06T01:00", "2026-11-06T06:40")


async def test_a_wrongly_aimed_radar_added_mid_night_is_outvoted(tmp_path):
    """A new radar that sees an empty room (aimed wrong) votes 'nobody' all night. Alone it must not send a sleeping
    person to 'out of bed'."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    holder = {}
    plan.at("02:00", lambda r_: holder.update(radar=r_.add("radar", "left")))

    def f(r_, now):
        plan(r_, now)
        if "radar" in holder:
            holder["radar"].set(0.0)
    await r.run("2026-11-06T07:30", script=f)
    assert "out_of_bed" not in r.states(r.p1, "2026-11-06T02:00", "2026-11-06T06:40")
    assert r.at(r.p1, "2026-11-06T04:00")[3] is True


async def test_retuning_a_radar_heavier_mid_night_can_take_over_cleanly(tmp_path):
    """The owner raises the radar's weight above the pad's at 03:00; the radar sees nobody (aimed wrong). The side goes
    to its out-of-bed hold, then comes back when the weight is lowered: no second start, nothing lost."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar = r.add("radar", "left")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.drive(radar, "left", lambda o: float(o) if r.clock.now_ts() < ts("2026-11-06T03:00") else 0.0)
    plan.at("03:00", lambda r_: r_.retune(radar, weight=1.0)).at("03:10", lambda r_: r_.retune(radar, weight=0.1))
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p1) == 1
    assert r.at(r.p1, "2026-11-06T03:50")[0] == "rhythm"
    assert r.at(r.p1, "2026-11-06T03:50")[3] is True


# ───────────────────────── the strip's own quirks ─────────────────────────
async def test_garbage_frames_for_ten_minutes_do_not_get_a_sleeper_up(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("02:00", lambda r_: r_.bed.garbage.update(left=True)).at("02:10", lambda r_: r_.bed.garbage.update(left=False))
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p1) == 1
    assert "out_of_bed" not in r.states(r.p1, "2026-11-06T01:00", "2026-11-06T06:40")
    assert r.at(r.p1, "2026-11-06T02:05")[3] is True


async def test_empty_sensor_block_for_an_hour_keeps_the_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("02:00", lambda r_: r_.bed.empty_block.update(left=True)).at("03:00", lambda r_: r_.bed.empty_block.update(left=False))
    await r.run("2026-11-06T07:30", script=plan)
    assert r.states(r.p1, "2026-11-06T02:10", "2026-11-06T03:00") <= {"rhythm", "out_of_bed_hold"}
    assert "out_of_bed" not in r.states(r.p1, "2026-11-06T01:00", "2026-11-06T06:40")
    assert r.started_count(r.p1) == 1


async def test_stale_sensor_clock_is_reported_once_and_the_night_goes_on(tmp_path):
    """The strip's own clock freezes at 01:00 (readings keep coming but never change). After a while the owner is told
    the bed sensor has stopped updating, once, with the bed itself still working."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("01:00", lambda r_: setattr(r_.bed.z["left"], "frozen", True))
    plan.at("04:00", lambda r_: setattr(r_.bed.z["left"], "frozen", False))
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p1) == 1
    assert r.at(r.p1, "2026-11-06T03:00")[3] is True
    assert r.kinds().count("problem") == 1


async def test_blips_on_an_empty_side_never_start_a_night_or_raise_alarms(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.bed.blips = True
    plan = Plan().sleep("left", "22:40", "06:50")
    await r.run("2026-11-06T07:30", script=plan)
    assert r.started_count(r.p2) == 0 and r.started_count(r.p1) == 1
    assert "problem" not in r.kinds()


async def test_an_unknown_status_code_for_ten_minutes_is_not_getting_up_for_good(tmp_path):
    """The strip sends a status code nobody has documented (9) for ten minutes at 02:00 while Alex sleeps. It reads as
    'empty', so the side goes to its out-of-bed hold, never to off, and is back on its plan when the codes return."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("02:00", lambda r_: r_.bed.junk.update(left=9)).at("02:10", lambda r_: r_.bed.junk.clear())
    await r.run("2026-11-06T07:30", script=plan)
    p = r.p1
    assert r.started_count(p) == 1
    assert r.states(p, "2026-11-06T02:00", "2026-11-06T02:10") <= {"rhythm", "out_of_bed_hold"}
    assert r.at(p, "2026-11-06T02:00")[3] is True and r.at(p, "2026-11-06T02:30")[0] == "rhythm"
