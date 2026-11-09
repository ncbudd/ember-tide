"""Autopilot calibration week, source trust, bed + watch heart-rate merging, the learned baseline, and the
monthly suggestion cadence."""

from datetime import date

from embertide.autopilot_api import _next_monthly, _release
from embertide.engine.autopilot import evaluate, merge_hr
from embertide.models import AutopilotPolicy
from test_autopilot_suite import NOW, night_rows, skin, tap, win
from test_scenarios import Night, ts


# ───────────── trust: how much each data source counts ─────────────
class TestTrust:
    def test_trust_clamped_and_filled(self):
        p = AutopilotPolicy(trust={"wearable": 5, "bogus": 1})
        assert p.trust == {"bed": 1.0, "wearable": 2.0, "room": 1.0}

    def test_ignoring_the_wearable_drops_skin_temperature(self):
        w = win(skin=skin(34.0, 34.8), rows=night_rows(restless_last10=True))
        on = evaluate(w, AutopilotPolicy(), 17.0, [])
        off = evaluate(w, AutopilotPolicy(trust={"wearable": 0}), 17.0, [])
        assert any(r.signal == "skin_temp" for r in on.reasons)
        assert not any(r.signal == "skin_temp" for r in off.reasons)

    def test_double_trust_doubles_the_push(self):
        w = win(skin=skin(34.0, 34.6), rows=night_rows(restless_last10=True))
        one = next(r for r in evaluate(w, AutopilotPolicy(), 17.0, []).reasons if r.signal == "skin_temp")
        two = next(r for r in evaluate(w, AutopilotPolicy(trust={"wearable": 2}), 17.0, []).reasons if r.signal == "skin_temp")
        assert abs(two.push - 2 * one.push) < 0.02

    def test_ignoring_the_bed_drops_movement(self):
        w = win(rows=night_rows(restless_last10=True), events=[tap("too_hot")])
        d = evaluate(w, AutopilotPolicy(trust={"bed": 0}), 17.0, [])
        assert not any(r.signal in ("movement", "pad") for r in d.reasons)
        assert any(r.signal == "feedback" for r in d.reasons)           # your own taps always count


# ───────────── bed vs watch heart rate ─────────────
class TestHeartRateMerge:
    def test_agreeing_sources_average(self):
        v, m, src = merge_hr(8.0, 10.0, 1.0, 1.0)
        assert v == 9.0 and src == "bed and watch agree"

    def test_disagreeing_sources_go_with_the_more_trusted(self):
        assert merge_hr(12.0, 1.0, 1.5, 1.0)[0] == 12.0
        assert merge_hr(12.0, 1.0, 1.0, 1.5)[0] == 1.0
        assert merge_hr(12.0, 1.0, 1.0, 1.0)[2].startswith("watch")      # a tie goes to the watch

    def test_one_source_only(self):
        assert merge_hr(None, 7.0, 1.0, 1.0)[:2] == (7.0, 1.0)
        assert merge_hr(7.0, None, 0.0, 1.0)[0] is None                  # bed ignored, no watch: nothing

    def test_watch_hr_feeds_the_decision(self):
        w = win(rows=night_rows(restless_last10=True), wear_hr=[(NOW - 3 * 3600 + 1500 + i * 120, 55.0) for i in range(60)]
                + [(NOW - 240 + i * 60, 68.0) for i in range(4)])
        d = evaluate(w, AutopilotPolicy(), 17.0, [])
        hr = next(r for r in d.reasons if r.signal == "hr")
        assert "watch" in hr.detail and d.signals["wear_hr_rise"] >= 10


# ───────────── learned baseline ─────────────
def test_naturally_restless_sleeper_needs_more_movement():
    # 40% of the last 10 minutes moving: normally a signal, but not for someone whose usual is 50%
    rows = night_rows()
    for r in rows[-16:]:
        r["status"] = 4
    plain = evaluate(win(rows=rows), AutopilotPolicy(), 17.0, [])
    learned = evaluate(win(rows=rows, baseline={"restless_p90": 0.5}), AutopilotPolicy(), 17.0, [])
    assert any(r.signal == "movement" for r in plain.reasons)
    assert not any(r.signal == "movement" for r in learned.reasons)


# ───────────── monthly cadence ─────────────
def test_next_monthly_is_first_chosen_weekday():
    assert _next_monthly(date(2026, 11, 1), 6) == date(2026, 11, 1)     # Sunday 1 Nov is the first Sunday
    assert _next_monthly(date(2026, 11, 2), 6) == date(2026, 12, 6)
    assert _next_monthly(date(2026, 11, 7), 0) == date(2026, 12, 7)     # first Monday of December


# ───────────── the calibration week, live ─────────────
async def _night(n, date_s):
    n.present("left", True)
    await n.until(f"{date_s}T07:30", dt=120)
    n.present("left", False)


async def test_learning_week_changes_nothing_then_starts(tmp_path):
    n = Night(tmp_path, "2026-11-02T22:20")
    pol = n.p1.settings.autopilot
    pol.mode, pol.learn_nights = "act", 2
    n.present("left", True)
    await n.until("2026-11-03T00:30", dt=60)
    before = n.e(n.p1).s.target_c
    n.store.event(n.clock.now_ts(), "feel", "p1", "app", feel="too_hot", phase="deep")
    await n.until("2026-11-03T01:00", dt=60)
    recs = n.events("autopilot")
    assert recs and all(r["status"] in ("learning", "blocked", "watching") for r in recs)
    assert any(r["status"] == "learning" for r in recs)
    assert n.e(n.p1).s.target_c == before                                 # learning: nothing applied
    lr = n.c.autopilot.learning(n.p1, n.clock.now_ts())
    assert lr == {"need": 2, "have": 0, "done": False, "skipped": False}
    # finish this night and one more: learning is done
    await n.until("2026-11-03T07:30", dt=120)
    n.present("left", False)
    await n.until("2026-11-03T22:20", dt=300)
    n.present("left", True)
    await n.until("2026-11-04T07:30", dt=120)
    n.present("left", False)
    await n.until("2026-11-04T13:00", dt=300)
    assert n.c.autopilot.learning(n.p1, n.clock.now_ts())["done"]
    b = n.c.autopilot.baseline(n.p1, n.clock.now_ts())
    assert b is None or b["nights"] >= 2                                  # baseline needs 3 nights


async def test_learning_hides_morning_suggestions_but_studio_sees_them(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    item = [{"id": "x", "kind": "pattern"}]
    r = _release(n.c, n.p1, item)
    assert r["items"] == [] and r["waiting"] == 1 and not r["learning"]["done"]
    assert _release(n.c, n.p1, item, show_all=True)["items"] == item
    n.p1.settings.autopilot.learn_skip = True
    assert _release(n.c, n.p1, item)["items"] == item


async def test_learn_nights_zero_means_no_learning(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:20")
    n.p1.settings.autopilot.learn_nights = 0
    assert n.c.autopilot.learning(n.p1, ts("2026-11-05T22:20"))["done"]


def test_normal_range_learns_only_real_sleep(tmp_path):
    """The empty bed, the pad off after the rhythm, and the pet don't shape the normal range (real night 11-07:
    21% of the window was an empty bed and dragged the bed range down to 22 °C)."""
    from test_autopilot_suite import make
    with make(tmp_path) as c:
        ctl = c.app.state.oura.c
        ap, p = ctl.autopilot, ctl.person("p1")
        nights = [{"date": f"2026-11-0{i}", "in_bed_at": 1000.0 + i * 86400, "out_at": 1000.0 + i * 86400 + 8 * 3600} for i in (1, 2, 3)]
        ap._nights = lambda p_, now: nights

        def history(zone, a, b, step_s=15, person=None):
            out = []
            t = a
            while t < b:
                frac = (t - a) / (b - a)
                if frac < 0.6:                       # asleep, rhythm running
                    out.append({"ts": t, "status": 0, "occupied": 1, "target": 27.0, "measured": 27.0, "hr": 58, "br": 14})
                elif frac < 0.8:                     # got up; empty pad still reading the room
                    out.append({"ts": t, "status": 5, "occupied": 0, "target": 27.0, "measured": 21.0, "hr": None, "br": None})
                else:                                # back in after the pad turned off (lie-in): no target
                    out.append({"ts": t, "status": 0, "occupied": 1, "target": None, "measured": 22.0, "hr": 66, "br": 15})
                t += 15
            return out
        ctl.store.history = history
        b = ap.baseline(p, 2e9)
        assert b and b["band"]["bed_c"] == [27.0, 27.0] and b["band"]["hr"] == [58.0, 58.0]


def test_a_night_the_rhythm_didnt_run_isnt_learned(tmp_path):
    """"Off tonight" (or run by hand): in bed, but the rhythm wasn't steering. Not this person's normal."""
    from test_autopilot_suite import make
    with make(tmp_path) as c:
        ap = c.app.state.oura.c.autopilot
        p = c.app.state.oura.c.person("p1")
        c.app.state.oura.c.store.history = lambda zone, a, b, step_s=15, person=None: [
            {"ts": a + i * 60, "occupied": 1, "target": None if i < 300 else 27.0} for i in range(int((b - a) // 60))]
        n = {"in_bed_at": 1000.0, "out_at": 1000.0 + 8 * 3600}
        assert ap._rhythm_share(p, n) < 0.6
        c.app.state.oura.c.store.history = lambda zone, a, b, step_s=15, person=None: [
            {"ts": a + i * 60, "occupied": 1, "target": 27.0} for i in range(int((b - a) // 60))]
        assert ap._rhythm_share(p, n) == 1.0
