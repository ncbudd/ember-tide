"""Detailed Autopilot suite: signal thresholds, sensitivity, switches, every guardrail and limit, modes over a
simulated night, replay, morning suggestions and cadence, trends, and the AI review's evidence checks."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.engine.autopilot import THRESHOLD, Window, evaluate, signals_at
from embertide.models import AutopilotPolicy
from test_scenarios import Night, ts

H = {"X-Embertide": "1"}
NOW = 50_000.0


def rows(now=NOW, n=40, status=0, hr=58, target=17.0, measured=17.2, step=15, start=None):
    t0 = start if start is not None else now - n * step
    return [{"ts": t0 + i * step, "status": status, "hr": hr, "target": target, "measured": measured, "occupied": 1, "phase": "deep"}
            for i in range(n)]


def win(**kw):
    base = dict(now=NOW, rows=rows(), in_bed=NOW - 3 * 3600, state="rhythm", phase="deep", flash_active=False, events=[], skin=[],
                persona="tide", wake=NOW + 5 * 3600)
    base.update(kw)
    return Window(**base)


def tap(feel, ago=60):
    return {"kind": "feel", "ts": NOW - ago, "feel": feel}


def night_rows(restless_last10=False, hr_base=56, hr_now=56):
    """Three hours settled, then the last 10 minutes as given."""
    settled = rows(n=int(3 * 3600 / 15) - 40, hr=hr_base, start=NOW - 3 * 3600)
    last = rows(n=40, status=4 if restless_last10 else 0, hr=hr_now)
    return settled + last


def skin(base=34.0, now_v=34.0):
    return [(NOW - 3 * 3600 + 1500 + i * 300, base) for i in range(25)] + [(NOW - 500 + i * 60, now_v) for i in range(8)]


# ───────────────────────── signals ─────────────────────────
class TestSignals:
    def test_restlessness_needs_enough_rows(self):
        assert signals_at(win(rows=rows(n=3, status=4)))["restless"] is None
        assert signals_at(win(rows=rows(n=40, status=4)))["restless"] == 1.0

    def test_restless_threshold_boundary(self):
        r = rows(n=100)
        last = [x for x in r if x["ts"] >= NOW - 600]
        for x in last[: int(len(last) * 0.29)]:
            x["status"] = 4
        assert signals_at(win(rows=r))["restless"] < 0.3
        d = evaluate(win(rows=r), AutopilotPolicy(sensitivity="high"), 17.0, [])
        assert not any(x.signal == "movement" for x in d.reasons)

    def test_hr_baseline_ignores_first_20_minutes_and_needs_20_samples(self):
        short = rows(n=10, hr=50, start=NOW - 900) + rows(n=10, hr=70)
        assert signals_at(win(rows=short, in_bed=NOW - 1000))["hr_rise"] is None
        assert signals_at(win(rows=night_rows(hr_base=55, hr_now=65)))["hr_rise"] == pytest.approx(10, abs=0.5)

    def test_skin_needs_a_baseline(self):
        assert signals_at(win(skin=[(NOW - 100, 35.0)]))["skin_delta"] is None
        assert signals_at(win(skin=skin(34.0, 34.5)))["skin_delta"] == pytest.approx(0.5, abs=0.01)

    def test_pad_over_only_counts_warm(self):
        d = evaluate(win(rows=rows(measured=19.5, target=17.0), events=[tap("too_hot")]), AutopilotPolicy(), 17.0, [])
        assert any(x.signal == "pad" for x in d.reasons)
        d2 = evaluate(win(rows=rows(measured=15.0, target=17.0), events=[tap("too_cold")]), AutopilotPolicy(), 17.0, [])
        assert not any(x.signal == "pad" for x in d2.reasons)

    def test_old_taps_and_flashes_expire(self):
        assert signals_at(win(events=[tap("too_hot", ago=1300)]))["feel"] is None
        assert signals_at(win(events=[tap("too_hot", ago=1200)]))["feel"] is None          # exactly 20 min: expired

    def test_a_tap_is_used_once(self):
        w = win(events=[tap("too_hot", ago=300)])
        first = evaluate(w, AutopilotPolicy(min_gap_min=0), 17.0, [])
        assert first.status == "suggested"
        again = evaluate(w, AutopilotPolicy(min_gap_min=0), 17.0, [{"ts": NOW - 200, "status": "suggested", "delta_c": -0.56}])
        assert again.direction == "none"
        assert signals_at(win(events=[{"kind": "hot_flash", "ts": NOW - 2800}]))["flash"] is None
        assert signals_at(win(events=[{"kind": "hot_flash", "ts": NOW - 600}]))["flash"] == "hot_flash"

    def test_future_events_are_never_seen(self):
        """A replay must only see the past."""
        assert signals_at(win(events=[tap("too_hot", ago=-60)]))["feel"] is None


# ───────────────────────── scoring ─────────────────────────
class TestScoring:
    @pytest.mark.parametrize("persona,direction", [("tide", "cooler"), ("neutral", "cooler"), ("ember", "warmer")])
    def test_restlessness_leans_on_type_when_alone(self, persona, direction):
        """Alone, restlessness (and the heart rate that goes with it) leans on the person's type for its sign, but
        it is one kind of evidence, so it only watches until something else agrees."""
        w = win(rows=night_rows(restless_last10=True, hr_base=55, hr_now=67), persona=persona)
        d = evaluate(w, AutopilotPolicy(sensitivity="high"), 17.0, [])
        mv = next(r for r in d.reasons if r.signal == "movement")
        assert (mv.push > 0) == (direction == "cooler")
        assert d.direction == "none" and d.status == "watching"

    def test_hr_only_counts_while_restless(self):
        d = evaluate(win(rows=night_rows(restless_last10=False, hr_base=55, hr_now=70), events=[tap("too_hot")]), AutopilotPolicy(), 17.0, [])
        assert not any(x.signal == "hr" for x in d.reasons)

    def test_conflicting_signals_cancel(self):
        d = evaluate(win(skin=skin(34.0, 34.6), events=[tap("too_cold")]), AutopilotPolicy(), 17.0, [])
        assert abs(d.score) < THRESHOLD["medium"] or d.direction == "warmer"      # your own word outweighs the watch
        assert d.direction != "cooler"

    def test_sensitivity_changes_what_it_needs(self):
        w = win(skin=skin(34.0, 34.5))                                         # skin alone, +0.5 °C
        scores = {s: evaluate(w, AutopilotPolicy(sensitivity=s), 17.0, []) for s in ("low", "medium", "high")}
        assert scores["low"].direction == "none"
        # one signal alone never acts, whatever the sensitivity, unless it's the person's own tap
        assert all(d.direction == "none" or d.blocked_by for d in scores.values())

    def test_strong_evidence_takes_a_bigger_step(self):
        w = win(rows=night_rows(restless_last10=True, hr_base=55, hr_now=68), skin=skin(34.0, 35.0), events=[tap("too_hot")])
        d = evaluate(w, AutopilotPolicy(), 17.0, [])
        assert d.delta_c == pytest.approx(-1.11)

    def test_switching_a_signal_off_removes_it(self):
        w = win(events=[tap("too_hot")])
        assert evaluate(w, AutopilotPolicy(), 17.0, []).direction == "cooler"
        off = AutopilotPolicy(signals=["skin_temp", "movement", "hr"])
        assert evaluate(w, off, 17.0, []).direction == "none"

    def test_every_reason_says_what_and_why(self):
        w = win(rows=night_rows(restless_last10=True, hr_base=55, hr_now=66), skin=skin(34.0, 34.6), events=[tap("too_hot")])
        d = evaluate(w, AutopilotPolicy(), 17.0, [])
        for r in d.reasons:
            assert r.label and r.value and r.detail and r.push != 0


# ───────────────────────── guardrails & limits ─────────────────────────
class TestGuardrails:
    def hot(self, **kw):
        return win(events=[tap("too_hot")], **kw)

    def test_not_in_bed_or_not_running(self):
        assert "only while" in " ".join(evaluate(self.hot(state="waiting"), AutopilotPolicy(), 17.0, []).blocked_by)
        assert "only while" in " ".join(evaluate(self.hot(in_bed=None), AutopilotPolicy(), 17.0, []).blocked_by)

    def test_manual_window_can_be_zero(self):
        w = win(events=[tap("too_hot"), {"kind": "adjust", "ts": NOW - 120, "source": "ha"}])
        assert evaluate(w, AutopilotPolicy(respect_manual_min=30), 17.0, []).status == "blocked"
        assert evaluate(w, AutopilotPolicy(respect_manual_min=0), 17.0, []).status == "suggested"

    def test_its_own_changes_dont_count_as_yours(self):
        w = win(events=[tap("too_hot"), {"kind": "adjust", "ts": NOW - 120, "source": "autopilot"}])
        assert evaluate(w, AutopilotPolicy(), 17.0, []).status == "suggested"

    def test_gap_between_changes(self):
        hist = [{"ts": NOW - 600, "status": "applied", "delta_c": -0.56}]
        assert any("since the last" in b for b in evaluate(self.hot(), AutopilotPolicy(min_gap_min=20), 17.0, hist).blocked_by)
        assert evaluate(self.hot(), AutopilotPolicy(min_gap_min=5), 17.0, hist).status == "suggested"

    def test_blocked_records_dont_use_up_the_limits(self):
        hist = [{"ts": NOW - 4000 + i * 10, "status": "blocked", "delta_c": -0.56} for i in range(10)]
        assert evaluate(self.hot(), AutopilotPolicy(max_actions=2), 17.0, hist).status == "suggested"

    def test_net_change_limit_counts_both_ways(self):
        hist = [{"ts": 1, "status": "applied", "delta_c": -0.56}, {"ts": 2, "status": "applied", "delta_c": +0.56}]
        assert evaluate(self.hot(), AutopilotPolicy(max_delta_c=0.6, min_gap_min=0), 17.0, hist).status == "suggested"

    def test_ceiling_for_warmer(self):
        d = evaluate(win(events=[tap("too_cold")]), AutopilotPolicy(ceiling_c=26.5), 26.3, [])
        assert d.status == "blocked" and any("never-above" in b for b in d.blocked_by)

    def test_all_reasons_are_listed_together(self):
        d = evaluate(self.hot(phase="wake", flash_active=True), AutopilotPolicy(), 17.0, [])
        assert len(d.blocked_by) >= 2


# ───────────────────────── modes over a simulated night ─────────────────────────
class TestModes:
    async def setup(self, tmp_path, mode):
        n = Night(tmp_path, "2026-11-05T22:20")
        n.p1.settings.autopilot.mode = mode
        n.p1.settings.autopilot.learn_nights = 0          # these tests are about the modes, not the calibration week
        n.present("left", True)
        await n.until("2026-11-06T00:30")
        return n

    async def test_off_writes_nothing(self, tmp_path):
        n = await self.setup(tmp_path, "off")
        n.store.event(n.clock.now_ts(), "feel", "p1", "app", feel="too_hot")
        await n.until("2026-11-06T00:40")
        assert not n.events("autopilot") and not n.store.samples("ap:p1:score", 0, 1e12)

    async def test_suggest_logs_once_per_gap_and_keeps_signals(self, tmp_path):
        n = await self.setup(tmp_path, "suggest")
        t = n.clock.now_ts()
        for k in range(4):                                               # tapped four times in 8 minutes
            n.store.event(t + k * 120, "feel", "p1", "app", feel="too_hot")
        await n.until("2026-11-06T00:45")
        sugg = [x for x in n.events("autopilot") if x["status"] == "suggested"]
        assert len(sugg) == 1                                            # not once per minute
        assert n.store.samples("ap:p1:score", 0, 1e12)                  # signal log for the timeline

    async def test_act_applies_then_respects_your_own_change(self, tmp_path):
        n = await self.setup(tmp_path, "act")
        e = n.e(n.p1)
        n.store.event(n.clock.now_ts(), "feel", "p1", "app", feel="too_hot")
        await n.until("2026-11-06T00:33")
        applied = [x for x in n.events("autopilot") if x["status"] == "applied"]
        assert len(applied) == 1 and e.s.override.source == "autopilot"
        e.adjust(n.clock.now_ts(), delta_c=0.56, until="next_phase", source="app")   # you push it back
        n.store.event(n.clock.now_ts(), "feel", "p1", "app", feel="too_hot")
        n.c.autopilot._last_logged_decision.clear()
        await n.until("2026-11-06T01:00")
        later = [x for x in n.events("autopilot") if x["ts"] > applied[0]["ts"]]
        assert all(x["status"] != "applied" for x in later)
        assert any("yourself" in " ".join(x.get("blocked_by") or []) for x in later)

    async def test_out_of_bed_stops_watching(self, tmp_path):
        n = await self.setup(tmp_path, "suggest")
        n.present("left", False)
        await n.until("2026-11-06T01:30")                               # well past the out-of-bed hold
        n.store.event(n.clock.now_ts(), "feel", "p1", "app", feel="too_hot")
        before = len(n.events("autopilot"))
        await n.until("2026-11-06T01:40")
        assert len(n.events("autopilot")) == before


# ───────────────────────── replay ─────────────────────────
class TestReplay:
    async def test_replay_is_read_only_and_repeatable(self, tmp_path):
        n = Night(tmp_path, "2026-11-05T22:20")
        n.present("left", True)
        await n.until("2026-11-06T00:30")
        n.store.event(n.clock.now_ts(), "feel", "p1", "app", feel="too_hot")
        await n.until("2026-11-06T02:00")
        before = len(n.store.events(limit=10000))
        pol = AutopilotPolicy()
        a = n.c.autopilot.replay(n.p1, ts("2026-11-05T22:00"), n.clock.now_ts(), pol)
        b = n.c.autopilot.replay(n.p1, ts("2026-11-05T22:00"), n.clock.now_ts(), pol)
        assert a == b and len(n.store.events(limit=10000)) == before
        assert any(d["status"] == "suggested" for d in a)

    async def test_replay_matches_what_happened_live(self, tmp_path):
        n = Night(tmp_path, "2026-11-05T22:20")
        n.p1.settings.autopilot.learn_nights = 0
        n.present("left", True)
        await n.until("2026-11-06T00:30")
        n.store.event(n.clock.now_ts(), "feel", "p1", "app", feel="too_hot")
        await n.until("2026-11-06T01:00")
        live = sorted(x["ts"] for x in n.events("autopilot") if x["status"] == "suggested")
        assert len(live) == 1                                             # one tap, one suggestion
        rep = [d["ts"] for d in n.c.autopilot.replay(n.p1, ts("2026-11-05T22:00"), n.clock.now_ts(), AutopilotPolicy()) if d["status"] == "suggested"]
        assert live and rep and abs(live[0] - rep[0]) <= 120


# ───────────────────────── morning suggestions ─────────────────────────
class TestMorning:
    async def night_with_change(self, tmp_path, *, clear=False, temp_c=None, delta=-0.56, presses=2):
        n = Night(tmp_path, "2026-11-05T22:20")
        n.present("left", True)
        await n.until("2026-11-06T00:30")
        e = n.e(n.p1)
        if temp_c is not None:
            e.adjust(n.clock.now_ts(), temp_c=temp_c, until="next_phase", source="app")
        else:
            for _ in range(presses):
                e.adjust(n.clock.now_ts(), delta_c=delta, until="next_phase", source="ha")
                await n.step(1)
        if clear:
            e.clear_override(n.clock.now_ts())
        await n.until("2026-11-06T09:00")
        return n

    async def test_absolute_change_from_the_phone(self, tmp_path):
        from embertide.autopilot_api import _suggest
        n = await self.night_with_change(tmp_path, temp_c=16.0)
        m = [x for x in _suggest(n.c, n.p1) if x["kind"] == "manual"]
        assert m and m[0]["proposal"]["to"] == pytest.approx(16.0) and "phone" in m[0]["detail"]

    async def test_undone_change_is_not_suggested(self, tmp_path):
        from embertide.autopilot_api import _suggest
        n = await self.night_with_change(tmp_path, clear=True)
        assert not [x for x in _suggest(n.c, n.p1) if x["kind"] == "manual"]

    async def test_huge_change_is_capped(self, tmp_path):
        from embertide.autopilot_api import _suggest
        n = await self.night_with_change(tmp_path, delta=-1.11, presses=5)       # 10 °F
        m = [x for x in _suggest(n.c, n.p1) if x["kind"] == "manual"][0]
        assert m["proposal"]["from"] - m["proposal"]["to"] == pytest.approx(2.8, abs=0.01)

    async def test_dismiss_not_now_and_forever(self, tmp_path):
        from embertide.autopilot_api import _release, _suggest
        n = await self.night_with_change(tmp_path)
        items = _suggest(n.c, n.p1)
        sid = next(x["id"] for x in items if x["kind"] == "manual")
        n.store.put("ap_dismissed:p1", {sid: {"ts": 1, "forever": False}})
        assert sid not in {x["id"] for x in _release(n.c, n.p1, _suggest(n.c, n.p1))["items"]}

    async def test_cadence_off_shows_nothing_on_the_phone(self, tmp_path):
        from embertide.autopilot_api import _release, _suggest
        n = await self.night_with_change(tmp_path)
        n.p1.settings.autopilot.suggest_cadence = "off"
        r = _release(n.c, n.p1, _suggest(n.c, n.p1))
        assert r["items"] == [] and r["waiting"] >= 1


# ───────────────────────── API, validation, AI ─────────────────────────
def make(tmp_path, llm=None):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", llm, False
    return TestClient(create_app(cfg))


class TestApi:
    def test_settings_validation(self, tmp_path):
        with make(tmp_path) as c:
            s = c.get("/api/household").json()["people"][0]["settings"]
            for bad in ({"sensitivity": "extreme"}, {"respect_manual_min": -1}, {"suggest_cadence": "hourly"}, {"suggest_weekday": 9}):
                assert c.put("/api/people/p1/settings", json={**s, "autopilot": {**s["autopilot"], **bad}}, headers=H).status_code == 422
            ok = {**s, "autopilot": {**s["autopilot"], "sensitivity": "high", "suggest_cadence": "weekly", "suggest_weekday": 0}}
            assert c.put("/api/people/p1/settings", json=ok, headers=H).status_code == 200

    def test_dismiss_forever_turns_manual_off(self, tmp_path):
        with make(tmp_path) as c:
            assert c.post("/api/people/p1/autopilot/suggestions/manual-2026-11-05-deep/dismiss", json={"forever": True}, headers=H).json()["manual_suggestions"] == "off"
            assert c.get("/api/household").json()["people"][0]["settings"]["autopilot"]["suggest_manual"] is False
            assert c.get("/api/people/p1/autopilot/suggestions").json()["cadence"] == "morning"

    def test_status_when_nobody_is_in_bed(self, tmp_path):
        with make(tmp_path) as c:
            c.post("/api/sim", json={"jump_to": "2026-11-05T14:00"}, headers=H)
            assert c.get("/api/people/p1/autopilot").json()["live"] is None

    def test_ai_review_drops_unsupported_proposals(self, tmp_path, monkeypatch):
        replies = []

        async def fake_post(self, url, json=None, headers=None, **kw):
            return httpx.Response(200, json={"choices": [{"message": {"content": replies.pop(0)}}]}, request=httpx.Request("POST", url))
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        block = lambda to: f'Fine night.\n```proposal\n{json.dumps({"rhythm_id": "weeknights", "phase_id": "deep", "field": "temp_c", "to": to})}\n```'
        with make(tmp_path, llm="http://fake/v1") as c:
            c.post("/api/sim", json={"jump_to": "2026-11-06T09:00"}, headers=H)
            replies.append(block(16.5))                                   # no evidence that night → dropped
            r = c.post("/api/people/p1/autopilot/ai-review", json={"date": "2026-11-05"}, headers=H).json()
            assert r["proposal"] is None and "Fine night" in r["answer"]
            c.post("/api/sim", json={"jump_to": "2026-11-06T00:30"}, headers=H)
            c.post("/api/people/p1/feel", json={"feel": "too_hot"}, headers=H)
            c.post("/api/sim", json={"jump_to": "2026-11-06T09:00"}, headers=H)
            replies.append(block(16.5))                                   # evidence, within 1.5 °C → kept
            assert c.post("/api/people/p1/autopilot/ai-review", json={"date": "2026-11-05"}, headers=H).json()["proposal"]["to"] == 16.5
            replies.append(block(10.0))                                   # too big a jump → dropped
            assert c.post("/api/people/p1/autopilot/ai-review", json={"date": "2026-11-05"}, headers=H).json()["proposal"] is None

    def test_ai_review_context_reads_settings_as_the_plan(self, tmp_path, monkeypatch):
        # 2026-11-12: the review called a planned warm-up (75 → 81 → 86 °F) "overshooting" and told a warm sleeper with no
        # complaints to go cooler, ignoring a 68 °F bedroom in November
        sent = []

        async def fake_post(self, url, json=None, headers=None, **kw):
            sent.append(json)
            return httpx.Response(200, json={"choices": [{"message": {"content": "Comfortable night."}}]}, request=httpx.Request("POST", url))
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        with make(tmp_path, llm="http://fake/v1") as c:
            c.post("/api/sim", json={"jump_to": "2026-11-06T09:00"}, headers=H)
            c.post("/api/people/p1/autopilot/ai-review", json={"date": "2026-11-05"}, headers=H)
        msgs = sent[-1]["messages"]
        system, user = msgs[0]["content"], msgs[-1]["content"]
        assert "planned_settings" in system and "NOT evidence" in system
        ctx = json.loads(user.split("```json\n", 1)[1].split("\n```", 1)[0])
        assert ctx["planned_settings"] and ctx["evidence_tonight"] is False
        assert ctx["season"].startswith("fall") and "room" in ctx and "pad_vs_setting" in ctx
        assert "don't recommend a rhythm change" in user
        ap = ctx["autopilot"]
        assert {"mode", "still_learning", "signals_overnight", "pending_suggestions"} <= set(ap) and "What Autopilot found" in system
        assert "sleep_stages" in ctx and "settled_min" in ctx["night"]


def test_room_and_pad_words():
    from embertide.autopilot_api import _pad_words, _room_words
    r = _room_words({"temp_c": {"min": 20.0, "max": 20.4, "avg": 20.2}, "humidity": {"min": 61, "max": 65, "avg": 63}})
    assert r["average"] == "20°C" and r["reads_as"] == "a cool bedroom"
    assert _room_words({"temp_c": {"min": 20.0, "max": 20.4, "avg": 20.2}}, "F")["average"] == "68.5°F"
    assert _room_words(None) == "no room sensor"
    assert "below its setting" in _pad_words([-0.8] * 10) and "cool room" in _pad_words([-0.8] * 10)
    assert _pad_words([0.1] * 10) == "the pad stayed at its setting" and _pad_words([]) == "not known"
