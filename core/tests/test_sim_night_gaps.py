"""Situations where a person would need to act but the system used to say nothing. Each now has its notification (kinds
`bed_silent` and `sensor`, notify.py); these run a whole simulated night through the real controller and notifier."""

from simnight import Plan, Sim


async def test_a_silent_sensor_strip_all_night_is_reported(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.bed.garbage["left"] = r.bed.garbage["right"] = True
    await r.run("2026-11-06T07:45", script=Plan().sleep("left", "22:40", "06:50"))
    assert "bed_silent" in r.kinds()
    assert sorted(set(r.kinds("p1")) & {"bed_silent"}) == ["bed_silent"]          # to the sleeper of that side
    assert r.kinds().count("bed_silent") == 2                                       # and one to each side that ran blind


async def test_a_working_strip_never_raises_it(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    await r.run("2026-11-06T07:45", script=Plan().sleep("left", "22:40", "06:50"))
    assert "bed_silent" not in r.kinds()


async def test_a_flapping_sensor_being_set_aside_is_reported_by_day(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar = r.add("radar", "left", min_on_s=0, hold_s=0)
    plan = Plan().sleep("left", "22:40", "06:50")
    n = {"i": 0}

    def f(r_, now):
        plan(r_, now)
        n["i"] += 1
        radar.set(float(n["i"] % 2))
    r.dt = 10
    await r.run("2026-11-05T23:30", script=f)
    assert any(e["kind"] == "device_health" and e["status"] == "erratic" for e in r.events())
    assert "sensor" not in r.kinds()                                                # night: nothing
    await r.run("2026-11-06T11:00", dt=30, script=f)                                # still flipping next morning
    assert r.kinds().count("sensor") == 1                                           # once, in the day


async def test_a_presence_sensor_stuck_on_for_a_day_is_reported(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    radar = r.add("radar", "left", mode="event", driver="mqtt-topic")             # a radar that only reports changes
    radar.set(1.0)
    await r.run("2026-11-06T09:00", script=Plan())
    assert "sensor" not in r.kinds()                                                # not yet a day
    await r.run("2026-11-07T11:00", script=Plan())
    assert r.kinds().count("sensor") == 1
