"""Cold room pre-warm (and warm room pre-condition). Real case 2026-11-08: room 18.4 °C, side set 27.2, measured 26.4 for
hours; in the warm wake 30.0 set, 29.3 measured. The simulated topper (sim_edges.RoomBed) moves more slowly far from
the room and settles a little short of the setpoint. Each side learns its speed per room temperature from its own
history, starts a warmer (cooler) phase earlier in a cool (warm) room, and says plainly when the room keeps it short."""

import pytest

from embertide.engine import padlag
from embertide.engine.autopilot import Window, band_check
from embertide.engine.summary import summarize
from sim_edges import TZ, RoomBed, Rig, ts

DATES = ["2026-11-02", "2026-11-03", "2026-11-04"]          # Mon-Wed nights: work nights, bed 22:30, wake 07:00


async def nights(r, room_c, watch=None, tap=None):
    r.bed.room_c = room_c
    for d in DATES:
        nd = f"2026-11-0{int(d[-1]) + 1}"

        def script(rig, now, d=d, nd=nd):
            rig.bed.set_presence("left", ts(f"{d}T22:35") <= now < ts(f"{nd}T07:05"))
            rig.bed.set_presence("right", ts(f"{d}T22:40") <= now < ts(f"{nd}T07:05"))
            if tap and d == DATES[-1] and abs(now - ts(tap)) < 30:
                rig.store.event(now, "feel", "p2", "app", feel="too_hot")
        r.clock.jump_to(ts(f"{d}T20:30"))
        await r.run(f"{nd}T07:40", dt=60, script=script,
                    watch=(lambda rig, now, d=d: watch(rig, now) if d == DATES[-1] else None) if watch else None)


def recorder(times):
    seen = {}

    def w(rig, now):
        for t in times:
            if abs(now - ts(t)) < 1:
                seen[t] = {p.id: (rig.e(p).s.phase_id, rig.e(p).s.target_c, rig.e(p).s.state) for p in rig.hh.people}
    return seen, w


def plan_at(r, pid, t):
    from embertide.engine.rhythm import target_at
    v, _ = target_at(r.e(pid).night, ts(t))
    return v


async def test_cold_room_learns_and_warms_the_wake_up_early(tmp_path):
    r = Rig(tmp_path, "2026-11-02T20:30", bed_cls=RoomBed, room=True)
    seen, w = recorder(["2026-11-05T06:10", "2026-11-05T06:25", "2026-11-05T07:15"])
    await nights(r, 18.4, watch=w)
    model = (r.store.get("padlag:right") or {}).get("model") or {}
    assert model.get("18", {}).get("warm_rate", 0) > 0                         # learned from its own history
    # the warm wake (06:30) starts warming earlier, at most 30 min, while the name stays "Late night"
    ph, tgt, _ = seen["2026-11-05T06:10"]["p2"]
    assert ph == "late" and tgt > plan_at(r, "p2", "2026-11-05T06:10") + 0.3
    ph, tgt, _ = seen["2026-11-05T06:25"]["p2"]
    assert ph == "late" and tgt > plan_at(r, "p2", "2026-11-05T06:25") + 0.5
    # the shortfall is said plainly, once per phase, and in the story
    notes = [e for e in r.events("room_cold", "p2") if e["ts"] >= ts("2026-11-05T00:00")]
    assert len(notes) == 1 and notes[0]["phase"] == "wake"
    assert notes[0]["text"] == "The room is cold (65°F), so your side is running a little below 80°F"
    s = summarize(r.store, r.p2, r.e(r.p2).night.date, TZ, r.clock.now_ts())
    assert [m["text"] for m in s["moments"] if m["kind"] == "room_cold"] == [notes[0]["text"]]
    snap_note = r.c.snapshot()["people"][1]["night"]["room_note"]
    assert snap_note is None or "cold" in snap_note["text"]


async def test_warm_room_starts_the_precool_earlier(tmp_path):
    r = Rig(tmp_path, "2026-11-02T20:30", bed_cls=RoomBed, room=True)
    seen, w = recorder(["2026-11-04T21:20", "2026-11-04T21:40"])
    await nights(r, 26.0, watch=w)
    assert seen["2026-11-04T21:20"]["p1"][2] == "waiting"                      # usually from 21:45
    assert not r.events("room_cold")                                           # a warm room never says "cold"


async def test_no_room_sensor_changes_nothing(tmp_path):
    r = Rig(tmp_path, "2026-11-02T20:30", bed_cls=RoomBed, room=False)
    seen, w = recorder(["2026-11-05T06:10", "2026-11-05T06:25", "2026-11-04T21:20"])
    await nights(r, 18.4, watch=w)
    for t in ("2026-11-05T06:10", "2026-11-05T06:25"):
        assert seen[t]["p2"][1] == pytest.approx(plan_at(r, "p2", t), abs=0.01)
    assert seen["2026-11-04T21:20"]["p1"][2] == "day"
    assert not r.events("room_cold") and not r.store.get("padlag:right")


async def test_a_too_hot_tap_holds_the_early_warming(tmp_path):
    r = Rig(tmp_path, "2026-11-02T20:30", bed_cls=RoomBed, room=True)
    seen, w = recorder(["2026-11-05T06:10"])
    await nights(r, 18.4, watch=w, tap="2026-11-05T05:55")
    assert seen["2026-11-05T06:10"]["p2"][1] == pytest.approx(plan_at(r, "p2", "2026-11-05T06:10"), abs=0.01)


def test_lead_rules():
    m = {"18": {"warm_rate": 0.06, "cool_rate": 0.05}, "26": {"cool_rate": 0.04}}
    # warming 4.5 °C at 0.06 °C/min = 75 min, minus a 30 min ramp, capped at 30 min
    assert padlag.lead_s(m, 18.4, 22.2, 26.7, 1800, 3600, 4 * 3600) == 1800
    assert padlag.lead_s(m, 18.4, 22.2, 26.7, 1800, 3600, 40 * 60) == 20 * 60     # half of a short phase at most
    assert padlag.lead_s(m, 22.0, 22.2, 26.7, 1800, 3600, 4 * 3600) == 0           # a comfortable room: nothing
    assert padlag.lead_s(m, 18.4, 26.7, 22.2, 1800, 3600, 4 * 3600) == 0           # cooling in a cold room: nothing
    assert padlag.lead_s({}, 18.4, 22.2, 26.7, 1800, 3600, 4 * 3600) == 0          # nothing learned yet
    assert padlag.lead_s(m, None, 22.2, 26.7, 1800, 3600, 4 * 3600) == 0           # no room sensor
    assert padlag.precool_lead_s(m, 26.0, 26.0, 20.0) == 1800
    assert padlag.precool_lead_s(m, 22.0, 22.0, 20.0) == 0
    assert padlag.shortfall_text(18.4, 27.2, "C") == "The room is cold (18.5°C), so your side is running a little below 27°C"


def test_autopilot_does_not_take_a_cold_room_shortfall_as_a_reason():
    now = 10_000.0
    rows = [{"ts": now - 900 + i * 60, "measured": 26.0, "target": 27.2, "status": 0, "hr": 60} for i in range(16)]
    band = {"bed_c": [26.8, 27.6], "hr": [55, 66]}
    base = dict(now=now, rows=rows, in_bed=now - 7200, state="rhythm", phase="deep", flash_active=False, events=[],
                skin=[], persona="ember", wake=None, baseline={"band": band})
    assert [x["what"] for x in band_check(Window(**base), {"restless": None})["out"]] == ["bed_c"]
    assert band_check(Window(**base, room_short=True), {"restless": None})["out"] == []
