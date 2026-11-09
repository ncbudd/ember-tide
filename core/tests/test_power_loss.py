"""Power cut in the middle of the night (~02:30 → ~03:15): the bed, the server, both, in either order, and the MQTT
broker down. Both people in bed since 22:35 / 22:40 on the household-like work-night rhythm (bed 22:30, wake 07:00).
The night must pick up where it was: same in-bed time and phase, zones back on, nobody taken for gone, one clear
notification, no duplicated or invented history."""

import pytest

from embertide.drivers.base import SensorFrame
from embertide.engine.presence import PresenceFilter
from embertide.engine.summary import summarize
from sim_edges import TZ, Rig, ts

CUT, BACK = ts("2026-11-06T02:30"), ts("2026-11-06T03:15")


def both_in_bed(left_from="2026-11-05T22:35", right_from="2026-11-05T22:40", left=True, bath=None):
    def f(r, now):
        out = bath is not None and ts(bath[0]) <= now < ts(bath[1])
        r.bed.set_presence("left", left and ts(left_from) <= now < ts("2026-11-06T07:05") and not out)
        r.bed.set_presence("right", ts(right_from) <= now < ts("2026-11-06T07:10"))
    return f


def with_events(base, events):
    """events: {ISO time: callable(rig)} run once when the clock reaches it."""
    done = set()

    def f(r, now):
        base(r, now)
        for t, fn in events.items():
            if t not in done and now >= ts(t):
                done.add(t)
                fn(r)
    return f


def summaries(r):
    return {p.id: summarize(r.store, p, r.e(p).night.date, TZ, r.clock.now_ts()) for p in r.hh.people}


def check_resumed(r, *, at_least_by="2026-11-06T03:20"):
    for p, zone in ((r.p1, "left"), (r.p2, "right")):
        e = r.e(p)
        assert e.s.started and e.s.state == "rhythm", (p.id, e.s.state)
        assert e.s.phase_id == "late"                                     # 03:20: the phase the clock says
        assert r.bed.z[zone].on and r.bed.z[zone].setpoint == pytest.approx(e.s.target_c, abs=0.1)
    assert len(r.events("rhythm_started", "p1")) == 1 and len(r.events("rhythm_started", "p2")) == 1


async def run_to(r, script, until="2026-11-06T03:20"):
    await r.run(until, script=script)


# ───────────── the bed loses power, the server keeps running ─────────────
async def test_bed_power_cut_and_back(tmp_path):
    r = Rig(tmp_path, "2026-11-05T22:00")
    in_bed = {}
    script = with_events(both_in_bed(), {"2026-11-06T02:00": lambda r: in_bed.update({p.id: r.e(p).s.in_bed for p in r.hh.people}),
                                         "2026-11-06T02:30": lambda r: r.bed.power_off(),
                                         "2026-11-06T03:15": lambda r: r.bed.power_on()})
    await run_to(r, script)
    check_resumed(r)
    assert {p.id: r.e(p).s.in_bed for p in r.hh.people} == in_bed          # the same night, not a fresh start
    # one notification for the whole outage (not one per sensor as well), held for the morning: everyone's asleep
    assert r.told() == ["problem"] and not r.notes()
    # nothing recorded from the frozen last report while the bed was off
    rows = [x for z in ("left", "right") for x in r.store.history(z, CUT + 60, BACK - 1, step_s=15)]
    assert not rows


async def test_bed_comes_back_with_zones_on_at_its_default(tmp_path):
    r = Rig(tmp_path, "2026-11-05T22:00", notifier=False)
    script = with_events(both_in_bed(), {"2026-11-06T02:30": lambda r: r.bed.power_off(),
                                         "2026-11-06T03:15": lambda r: r.bed.power_on(zones_on_default=True)})
    await run_to(r, script)
    check_resumed(r)                                                      # 27 °C default replaced by the plan


# ───────────── the server restarts (process restart, state from the store) ─────────────
async def test_server_restart_resumes_from_the_store(tmp_path):
    r = Rig(tmp_path, "2026-11-05T22:00")
    before = {}
    script = with_events(both_in_bed(), {"2026-11-06T02:29": lambda r: before.update({p.id: (r.e(p).s.in_bed, r.e(p).night.rhythm_id) for p in r.hh.people}),
                                         "2026-11-06T02:30": lambda r: r.crash(),
                                         "2026-11-06T03:15": lambda r: r.boot()})
    await run_to(r, script)
    check_resumed(r)
    assert {p.id: (r.e(p).s.in_bed, r.e(p).night.rhythm_id) for p in r.hh.people} == before
    assert not r.notes()                                                   # the bed was fine all along
    await r.run("2026-11-06T07:40", script=both_in_bed())
    assert r.told("p1").count("morning") == 1 and r.told("p2").count("morning") == 1
    r.crash()
    r.boot()                                                               # restart after the check-in: not sent again
    await r.run("2026-11-06T08:00", script=both_in_bed())
    assert r.told("p1").count("morning") == 1
    s = summaries(r)
    assert s["p1"]["times_up"] == 0 and s["p2"]["times_up"] == 0
    assert [m["kind"] for m in s["p1"]["moments"]].count("rhythm_started") == 1
    assert not any(m["kind"] == "rhythm_restarted" for m in s["p1"]["moments"])


@pytest.mark.parametrize("order", ["together", "tower_first", "bed_first"])
async def test_both_lose_power(tmp_path, order):
    r = Rig(tmp_path, "2026-11-05T22:00")
    ev = {"2026-11-06T02:30": lambda r: (r.crash(), r.bed.power_off())}
    if order == "together":
        ev["2026-11-06T03:15"] = lambda r: (r.boot(), r.bed.power_on())
    elif order == "tower_first":
        ev["2026-11-06T03:05"] = lambda r: r.boot()
        ev["2026-11-06T03:15"] = lambda r: r.bed.power_on()
    else:
        ev["2026-11-06T03:05"] = lambda r: r.bed.power_on()
        ev["2026-11-06T03:15"] = lambda r: r.boot()
    await run_to(r, with_events(both_in_bed(), ev))
    check_resumed(r)
    assert r.told().count("problem") <= 1                                  # (one outage, no "stopped updating" on top)


@pytest.mark.parametrize("order", ["server", "together", "bed_first"])
async def test_one_sleeper_on_the_right_is_not_lost_after_a_restart(tmp_path, order):
    # alone on the right, the left pad echoing them: after a restart both pads come back at once with the same
    # readings. The right sleeper must stay in bed and the empty left side must not start a night.
    r = Rig(tmp_path, "2026-11-05T22:00", notifier=False)
    ev = {"server": {"2026-11-06T02:30": lambda r: r.crash(), "2026-11-06T03:15": lambda r: r.boot()},
          "together": {"2026-11-06T02:30": lambda r: (r.crash(), r.bed.power_off()),
                       "2026-11-06T03:15": lambda r: (r.boot(), r.bed.power_on())},
          "bed_first": {"2026-11-06T02:30": lambda r: (r.crash(), r.bed.power_off()),
                        "2026-11-06T03:05": lambda r: r.bed.power_on(), "2026-11-06T03:15": lambda r: r.boot()}}[order]
    states = []
    await r.run("2026-11-06T04:30", script=with_events(both_in_bed(left=False), ev),
                watch=lambda rig, now: states.append(rig.e(rig.p2).s.state) if now >= BACK and rig.server else None)
    assert set(states) == {"rhythm"}
    assert not r.e(r.p1).s.started


async def test_up_to_the_bathroom_when_the_power_went(tmp_path):
    r = Rig(tmp_path, "2026-11-05T22:00", notifier=False)
    seen = []
    script = with_events(both_in_bed(bath=("2026-11-06T02:24", "2026-11-06T02:33")),
                         {"2026-11-06T02:30": lambda r: (r.crash(), r.bed.power_off()),
                          "2026-11-06T03:15": lambda r: (r.boot(), r.bed.power_on())})
    await r.run("2026-11-06T03:20", script=script,
                watch=lambda rig, now: seen.append(rig.e(rig.p1).s.state) if now >= BACK else None)
    assert "out_of_bed" not in seen                     # the 45 min the server was off don't count towards the hold
    check_resumed(r)
    await r.run("2026-11-06T07:30", script=both_in_bed())
    up = [m for m in summaries(r)["p1"]["moments"] if m["kind"] == "out_of_bed"]
    assert len(up) == 1 and up[0]["minutes"] <= 10      # what was seen, not the whole gap in the history


async def test_flash_change_and_guest_survive_a_restart(tmp_path):
    r = Rig(tmp_path, "2026-11-05T22:00", notifier=False)
    ev = {"2026-11-05T22:20": lambda r: r.c.guest_start(["right"], 24.0, None, None),
          "2026-11-06T01:00": lambda r: r.e(r.p1).adjust(r.clock.now_ts(), delta_c=-0.6, until="night"),
          "2026-11-06T02:22": lambda r: r.e(r.p1).flash("hot", r.clock.now_ts(), minutes=30),
          "2026-11-06T02:30": lambda r: (r.crash(), r.bed.power_off()),
          "2026-11-06T02:40": lambda r: (r.boot(), r.bed.power_on(boot_s=60))}
    await r.run("2026-11-06T02:45", script=with_events(both_in_bed(), ev))
    e = r.e(r.p1)
    assert e.s.flash is not None and e.s.flash.kind == "hot"              # still within its 30 min
    assert r.bed.z["left"].setpoint == pytest.approx(e.s.flash.target_c, abs=0.1)
    assert e.s.override is not None and e.s.override.delta_c == pytest.approx(-0.6)
    assert "right" in r.c.household.guest and r.e(r.p2).s.state == "guest"
    assert r.bed.z["right"].on and r.bed.z["right"].setpoint == pytest.approx(24.0, abs=0.1)
    await r.run("2026-11-06T03:30", script=both_in_bed())
    assert e.s.flash is None                                               # ended on time, not stuck
    assert r.bed.z["left"].setpoint == pytest.approx(e.s.target_c, abs=0.1)
    assert len(r.events("hot_flash")) == 1


async def test_broker_down_for_a_while(tmp_path):
    r = Rig(tmp_path, "2026-11-05T22:00")
    script = with_events(both_in_bed(), {"2026-11-06T02:30": lambda r: setattr(r.bed, "link", False),
                                         "2026-11-06T03:15": lambda r: setattr(r.bed, "link", True)})
    await run_to(r, script)
    check_resumed(r)                       # the late-night change made while unreachable reaches the bed once it's back
    assert r.told() == ["problem"]                                         # once, held for the morning (night time)


async def test_bed_back_retries_at_once(tmp_path):
    # commands the bed ignored while it was away are backed off; when it reports again they go out straight away
    import embertide.engine.controller as ctlmod
    r = Rig(tmp_path, "2026-11-05T22:00", notifier=False)
    await r.run("2026-11-06T02:30", script=both_in_bed())
    ctlmod.MIN_CMD_GAP_S = 8.0
    r.bed.power_off()
    await r.run("2026-11-06T03:15", script=both_in_bed(), )
    r.c._unconfirmed = {"left": ((True, r.e(r.p1).s.target_c), 6)}       # as after many ignored tries
    r.c._last_cmd["left"] = __import__("time").monotonic()
    r.bed.power_on(boot_s=30)
    await r.run("2026-11-06T03:17", script=both_in_bed())
    assert r.bed.z["left"].on


def test_presence_tie_after_a_restart_follows_the_running_night():
    def frames(i):
        f = SensorFrame(status=0, hr=58, br=13, clock=(i,))
        return {"left": f, "right": SensorFrame(status=0, hr=58, br=13, clock=(i,))}
    for prefer, real, echo in (({"right"}, "right", "left"), ({"left"}, "left", "right")):
        pf = PresenceFilter()
        pf.prefer = prefer
        for i in range(40):
            pf.update(1000.0 + i, frames(i))
        assert pf.present(real) is True and pf.present(echo) is False, prefer
