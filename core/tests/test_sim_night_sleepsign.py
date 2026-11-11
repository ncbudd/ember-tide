"""In bed before the usual bedtime: the night starts, the bed holds "Fall asleep", and the in-bed phases after it
("Deep" at in-bed + 40 min) count from the first sign of sleep, or from the usual bedtime at the latest
(engine/night.py, engine/sleepsign.py).

Alex is on the left (p1), Sam on the right (p2). Work-night rhythm: bed 22:30, wake 07:00, Deep = in bed + 40 min."""

import pytest

from embertide.models import Pet
from simnight import Plan, Sim, hm, ts

CAT = Pet(name="Biscuit", kind="cat", weight_kg=4.5, sleeps_on_bed="yes")


def reading(plan: Plan, zone: str, a: str, b: str) -> Plan:
    """Awake and still in bed (reading): steady heart rate, a page turn now and then."""
    return plan.at(a, lambda r: r.bed.reading.update({zone: True})).at(b, lambda r: r.bed.reading.pop(zone, None))


def anchors(r, pid="p1"):
    return [(hm(e["at"]), e["why"]) for e in r.events("sleep_anchor", pid)]


def phase(r, when, p=None):
    return r.at(p or r.p1, when)[2]


def deep_from(r, p=None, a="2026-11-05T21:00", b="2026-11-06T02:00"):
    """When the Deep phase first showed (HH:MM)."""
    pid = (p or r.p1).id
    for t, row in r.rec:
        if ts(a) <= t < ts(b) and row[pid][2] == "deep":
            return hm(t)
    return None


# ───────────────────────── reads for two hours, then sleeps ─────────────────────────
async def test_no_sign_at_all_moves_on_at_the_usual_bedtime(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    await r.run("2026-11-06T00:30", script=plan)
    assert r.started_count(r.p1) == 1
    assert anchors(r) == [("22:30", "bedtime")]
    assert phase(r, "2026-11-05T21:30") == "fall_asleep" and phase(r, "2026-11-05T22:45") == "fall_asleep"
    assert "23:10" <= deep_from(r) <= "23:14"                      # exactly as if they'd got in at bedtime
    s = r.summary(r.p1)
    sleep = [m for m in s["moments"] if m["kind"] == "sleep"]
    assert len(sleep) == 1 and "usual bedtime, 10:30 PM" in sleep[0]["text"]


async def test_phone_bedtime_mode_coming_on_in_bed_moves_the_night_on(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "22:00")
    plan.at("21:05", lambda x: x.phone(x.p1, bedtime_mode=False)).at("22:00", lambda x: x.phone(x.p1, bedtime_mode=True))
    await r.run("2026-11-06T00:30", script=plan)
    assert anchors(r) == [("22:00", "bedtime_mode")]
    assert "22:40" <= deep_from(r) <= "22:44"
    assert any("bedtime mode came on around 10:00 PM" in (m.get("text") or "") for m in r.summary(r.p1)["moments"])


async def test_phone_on_the_charger_by_the_bed_moves_the_night_on(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "21:50")
    plan.at("21:50", lambda x: x.phone(x.p1, charging=True, dials=[{"minor": 1, "rssi": -60, "seen_at": x.clock.now_ts()}]))
    await r.run("2026-11-06T00:30", script=plan)
    assert anchors(r) == [("21:50", "charger")]
    assert "22:30" <= deep_from(r) <= "22:34"


async def test_the_phone_on_a_charger_away_from_the_bed_is_not_a_sign(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    plan.at("21:50", lambda x: x.phone(x.p1, charging=True, dials=[{"minor": 1, "rssi": -92, "seen_at": x.clock.now_ts()}]))
    await r.run("2026-11-05T23:00", script=plan)
    assert anchors(r) == [("22:30", "bedtime")]


async def test_the_bed_sees_them_settle_after_reading(tmp_path):
    """Reads until 21:40, then falls asleep: still, and the heart rate drops from the reading level."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "21:40")
    await r.run("2026-11-06T00:30", script=plan)
    (at, why), = anchors(r)
    assert why == "settled" and "21:50" <= at <= "22:15", anchors(r)    # the lower heart rate held for ~15 min
    assert phase(r, "2026-11-05T21:48") == "fall_asleep"
    assert deep_from(r) < "23:00"
    assert any((m.get("text") or "").startswith("You settled around") for m in r.summary(r.p1)["moments"])


async def test_reading_quietly_for_hours_is_not_taken_for_sleep(tmp_path):
    """Still for two hours with a steady heart rate: no "settled" sign, only the bedtime backstop."""
    r = Sim(tmp_path, "2026-11-05T19:00")
    r.hh.people[0].rhythms[0].schedule.window_start = "19:00"
    plan = reading(Plan().sleep("left", "19:30", "06:50"), "left", "19:30", "23:30")
    await r.run("2026-11-05T22:40", script=plan)
    assert anchors(r) == [("22:30", "bedtime")]


async def test_a_watch_sending_sleep_stages_live_moves_the_night_on(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")

    def stage(x):
        now = x.clock.now_ts()
        x.store.put_stages("p1", "oura", "Oura", [{"start": ts("2026-11-05T21:45"), "end": now, "stage": "light"}], now - 3600, now)
    plan.at("22:00", stage)
    await r.run("2026-11-05T23:30", script=plan)
    assert anchors(r) == [("21:45", "watch")]


async def test_the_bedroom_light_going_off_moves_the_night_on(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    light = r.add("light", None, role="light")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    plan.at("21:00", lambda x: light.set(1.0)).at("22:06", lambda x: light.set(0.0))
    await r.run("2026-11-05T23:30", script=plan)
    (at, why), = anchors(r)
    assert why == "lights_off" and "22:06" <= at <= "22:10"


# ───────────────────────── other ways into an early night ─────────────────────────
async def test_getting_in_five_minutes_before_bedtime(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    await r.run("2026-11-06T00:30", script=Plan().sleep("left", "22:25", "06:50"))
    assert anchors(r) == [("22:30", "bedtime")]
    assert "23:10" <= deep_from(r) <= "23:14"


async def test_getting_in_at_bedtime_is_unchanged(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    await r.run("2026-11-06T00:30", script=Plan().sleep("left", "22:40", "06:50"))
    assert anchors(r) == []
    start = r.events("rhythm_started", "p1")[0]["in_bed"]
    assert hm(start + 40 * 60) <= deep_from(r) <= hm(start + 44 * 60)


async def test_getting_in_early_and_falling_asleep_at_once(tmp_path):
    """No reading: the bed sees them settle well before bedtime, and the night moves on then."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    await r.run("2026-11-06T00:30", script=Plan().sleep("left", "21:10", "06:50"))
    (at, why), = anchors(r)
    assert why == "settled" and at < "22:10", anchors(r)
    assert deep_from(r) < "22:50"


async def test_getting_in_early_then_up_for_an_hour_then_back(tmp_path):
    """In at 21:10 to read, up at 21:30 for over an hour: the early start is given up, and getting back in after bedtime
    starts the night again from the beginning (anchored on getting in, as always after bedtime)."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50", ups=[("21:30", "22:40")]), "left", "21:10", "21:30")
    await r.run("2026-11-06T01:00", script=plan)
    assert len(r.events("rhythm_restarted", "p1")) == 1
    assert phase(r, "2026-11-05T23:00") == "fall_asleep"
    assert "23:22" <= deep_from(r, a="2026-11-05T22:40") <= "23:30"
    assert r.e(r.p1).s.sleep_at is None


async def test_bedtime_mode_on_a_schedule_with_nobody_in_bed_is_not_a_sign(tmp_path):
    r = Sim(tmp_path, "2026-11-05T20:30")
    plan = reading(Plan().sleep("left", "21:15", "06:50"), "left", "21:15", "23:00")
    plan.at("20:30", lambda x: x.phone(x.p1, bedtime_mode=False)).at("21:00", lambda x: x.phone(x.p1, bedtime_mode=True))
    plan.at("21:40", lambda x: x.phone(x.p1, bedtime_mode=True)).at("22:20", lambda x: x.phone(x.p1, bedtime_mode=True))
    await r.run("2026-11-05T23:00", script=plan)
    assert anchors(r) == [("22:30", "bedtime")]


async def test_bedtime_mode_with_an_empty_bed_never_moves_anything(tmp_path):
    r = Sim(tmp_path, "2026-11-05T20:30")
    plan = Plan().sleep("left", "22:50", "06:50")
    plan.at("20:30", lambda x: x.phone(x.p1, bedtime_mode=False)).at("21:30", lambda x: x.phone(x.p1, bedtime_mode=True))
    await r.run("2026-11-05T22:45", script=plan)
    assert anchors(r) == [] and r.started_count(r.p1) == 0


async def test_the_partner_already_asleep_on_the_other_side(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = Plan().sleep("right", "21:05", "06:55").sleep("left", "21:30", "06:50")
    reading(plan, "left", "21:30", "23:00")
    await r.run("2026-11-06T00:30", script=plan)
    assert anchors(r, "p1") == [("22:30", "bedtime")]            # Sam's sleep is not Alex's
    assert phase(r, "2026-11-05T22:20") == "fall_asleep"
    (at, why), = anchors(r, "p2")
    assert why == "settled" and at < "22:20"                      # and Sam's own night moved on when Sam settled


async def test_a_pet_alone_on_the_side_sets_no_anchor(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.hh.pets = [CAT]
    plan = Plan().sleep("left", "23:10", "06:50")
    plan.at("21:00", lambda x: x.bed.pets.update(left=165)).at("23:00", lambda x: x.bed.pets.pop("left", None))
    await r.run("2026-11-05T23:05", script=plan)
    assert anchors(r) == []


async def test_with_the_setting_off_the_night_runs_from_getting_in_as_before(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    for rh in r.hh.people[0].rhythms:
        rh.schedule.wait_for_sleep = False
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    await r.run("2026-11-06T00:30", script=plan)
    assert anchors(r) == []
    assert "21:50" <= deep_from(r) <= "21:58"


async def test_a_server_restart_during_the_wait_resumes_it(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    await r.run("2026-11-05T21:50", script=plan)
    assert r.e(r.p1).waiting_for_sleep()
    r.crash()
    await r.run("2026-11-05T21:56", script=plan)
    r.boot()
    assert r.e(r.p1).s.started and r.e(r.p1).s.sleep_at is None
    await r.run("2026-11-05T23:30", script=plan)
    assert anchors(r) == [("22:30", "bedtime")] and r.started_count(r.p1) == 1
    assert phase(r, "2026-11-05T22:20") == "fall_asleep" and "23:10" <= deep_from(r) <= "23:14"
    # and the anchor itself comes back after another restart
    r.boot()
    assert r.e(r.p1).s.sleep_at == ts("2026-11-05T22:30")
    await r.run("2026-11-05T23:40", script=plan)
    assert phase(r, "2026-11-05T23:35") == "deep"


async def test_an_alarm_moved_wake_still_comes_on_time(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    plan.at("21:00", lambda x: x.phone(x.p1, next_alarm=ts("2026-11-06T05:30")))
    await r.run("2026-11-06T06:00", script=plan)
    assert phase(r, "2026-11-06T04:55") != "wake" and phase(r, "2026-11-06T05:05") == "wake"


@pytest.mark.parametrize("get_in", ["21:10", "22:25", "22:40"])
async def test_the_wait_never_delays_the_warm_wake_or_pre_wake_warming(tmp_path, get_in):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", get_in, "06:55"), "left", get_in, "23:30")
    await r.run("2026-11-06T06:45", script=plan)
    assert phase(r, "2026-11-06T03:05") == "late"
    assert phase(r, "2026-11-06T06:25") == "late" and phase(r, "2026-11-06T06:32") == "wake"


async def test_tonight_says_it_is_waiting_and_the_plan_shows_when_you_settle(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    await r.run("2026-11-05T21:40", script=plan)
    me = next(p for p in r.c.snapshot()["people"] if p["id"] == "p1")
    assert me["night"]["sleep"]["waiting"] is True and me["night"]["phase_id"] == "fall_asleep"
    assert me["next"]["phase"] == "deep" and me["next"]["when_settled"] is True
    assert [p["id"] for p in me["phases"] if p.get("when_settled")] == ["deep"]
    await r.run("2026-11-05T22:35", script=plan)
    me = next(p for p in r.c.snapshot()["people"] if p["id"] == "p1")
    assert me["night"]["sleep"] == {"waiting": False, "at": ts("2026-11-05T22:30"), "why": "bedtime"}
    assert not me["next"].get("when_settled") and not any(p.get("when_settled") for p in me["phases"])


async def test_pause_and_off_tonight_still_work_while_waiting(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    plan.at("21:30", lambda x: x.e(x.p1).tonight(x.clock.now_ts(), "pause", minutes=30))
    plan.at("22:10", lambda x: x.e(x.p1).tonight(x.clock.now_ts(), "skip"))
    await r.run("2026-11-05T23:00", script=plan)
    assert r.at(r.p1, "2026-11-05T21:40")[0] == "paused"
    assert r.at(r.p1, "2026-11-05T22:05")[2] == "fall_asleep"
    assert r.at(r.p1, "2026-11-05T22:20")[0] == "skipped" and r.at(r.p1, "2026-11-05T22:20")[3] is False


async def test_a_trace_line_shows_the_wait(tmp_path):
    from embertide.trace import frame
    r = Sim(tmp_path, "2026-11-05T21:00")
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "23:00")
    await r.run("2026-11-05T21:40", script=plan)
    row = next(x for x in frame(r.c)["people"] if x["id"] == "p1")
    assert row["night"]["sleep"]["waiting"] is True
