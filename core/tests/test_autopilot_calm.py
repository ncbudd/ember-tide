"""Autopilot stays calm: the calibration nights teach a normal range (comfort band) per person; inside it there are
no live nudges and no suggestions; it acts only on a sustained step outside it, or on the person's own word.
Also: visible rate limits on suggestions, "Start over: recalibrate", and the plain recheck result."""

from embertide.auth import Principal, authorize
from embertide.autopilot_api import _release, recheck_text
from embertide.engine.autopilot import band_check, evaluate, signals_at
from embertide.models import AutopilotPolicy
from lab import Lab, Sleeper
from test_autopilot_suite import H, NOW, make, night_rows, rows, skin, tap, win
from test_scenarios import Night, ts

BAND = {"bed_c": [16.8, 17.6], "hr": [52, 61], "skin": [33.8, 34.4], "restless": 0.2, "feel_rate": 0.0}
WARM_ROOM = [(NOW - 1500 + i * 120, 24.1) for i in range(13)]       # 2.5 °C over usual: a second kind of evidence


def warmish(**kw):
    """Skin up a little against tonight's start and a warmer room than usual: enough to act on without a band."""
    base = dict(rows=night_rows(), skin=skin(34.0, 34.5), room=WARM_ROOM, room_usual=21.6)
    base.update(kw)
    return win(**base)


class TestComfortBand:
    def test_without_a_band_the_same_night_would_suggest(self):
        d = evaluate(warmish(), AutopilotPolicy(), 17.0, [])
        assert d.status == "suggested"

    def test_inside_the_band_it_stays_quiet(self):
        d = evaluate(warmish(baseline={"band": BAND}), AutopilotPolicy(), 17.0, [])
        assert d.direction == "none" and d.status == "watching"
        assert "inside your normal range" in d.blocked_by[0]

    def test_sustained_step_outside_the_band_acts(self):
        d = evaluate(warmish(skin=skin(34.0, 35.1), baseline={"band": BAND}), AutopilotPolicy(), 17.0, [])
        assert d.status == "suggested" and d.direction == "cooler"
        assert d.signals["outside_band"] == ["skin"]

    def test_a_short_blip_outside_is_not_sustained(self):
        s = [(t, v) for t, v in skin(34.0, 34.0)] + [(NOW - 120, 35.2), (NOW - 60, 35.2)]   # only the last 2 minutes
        w = warmish(skin=sorted(s), baseline={"band": BAND})
        assert band_check(w, signals_at(w))["out"] == []
        assert evaluate(w, AutopilotPolicy(), 17.0, []).direction == "none"

    def test_your_own_tap_still_counts_inside_the_band(self):
        d = evaluate(win(rows=rows(), events=[tap("too_hot")], baseline={"band": BAND}), AutopilotPolicy(), 17.0, [])
        assert d.status == "suggested"

    def test_heart_rate_and_bed_temperature_bands(self):
        hot_bed = rows(n=80, measured=18.6, start=NOW - 1200)
        w = win(rows=night_rows()[:-80] + hot_bed, baseline={"band": BAND})
        assert [x["what"] for x in band_check(w, signals_at(w))["out"]] == ["bed_c"]
        fast = night_rows(hr_base=56, hr_now=56)
        for r in fast[-80:]:
            r["hr"] = 70
        w = win(rows=fast, baseline={"band": BAND})
        assert [x["what"] for x in band_check(w, signals_at(w))["out"]] == ["hr"]


# ───────── the whole loop on the simulator: learn, stay quiet, act when it's really off ─────────
async def test_typical_nights_inside_the_band_give_nothing_and_a_hot_night_gives_one(tmp_path):
    sl = Sleeper(watch=True)
    lab = Lab(tmp_path, "2026-11-01T20:00", sleepers={"left": sl})
    pol = lab.p1.settings.autopilot
    pol.mode, pol.learn_nights = "suggest", 3
    await lab.nights(3)
    assert lab.c.autopilot.learning(lab.p1, lab.clock.now_ts())["done"]
    lab.c.autopilot.__dict__.pop("_base_cache", None)
    band = lab.c.autopilot.baseline(lab.p1, lab.clock.now_ts())["band"]
    assert band["skin"] and band["hr"] and band["bed_c"]
    learned_until = lab.clock.now_ts()
    await lab.nights(3)                                       # three more ordinary nights
    acted = [d for d in lab.decisions("p1") if d["ts"] > learned_until and d["status"] in ("suggested", "applied")]
    assert acted == [], "inside the normal range: no nudges"
    assert lab.suggest(lab.p1, show_all=False)["items"] == [], "and no suggestions"
    quiet_until = lab.clock.now_ts()
    sl.pref_c = {"*": -2.5}                                   # a night that really is too warm for them
    await lab.nights(1)
    hot = [d for d in lab.decisions("p1") if d["ts"] > quiet_until and d["status"] in ("suggested", "applied")]
    assert hot and all(d["direction"] == "cooler" for d in hot)


# ───────── suggestion rate limits ─────────
async def test_one_suggestion_per_period_and_not_now_snoozes_the_idea(tmp_path):
    n = Night(tmp_path, "2026-11-06T08:00")
    n.p1.settings.autopilot.learn_skip = True
    a = {"id": "ap-deep-cooler-2026-11-05", "kind": "pattern", "phase": "deep", "direction": "cooler"}
    b = {"id": "ap-late-warmer-2026-11-05", "kind": "pattern", "phase": "late", "direction": "warmer"}
    r = _release(n.c, n.p1, [a, b])
    assert [x["id"] for x in r["items"]] == [a["id"]] and r["waiting"] == 1 and r["limits"]["one_per"] == "morning"
    n.store.put("ap_dismissed:p1", {a["id"]: {"ts": n.clock.now_ts(), "forever": False}})
    r = _release(n.c, n.p1, [a, b])
    assert r["items"] == [] and r["limits"]["used_this_period"], "one shown this morning already: the rest wait"
    n.clock.jump_to(ts("2026-11-07T08:00"))
    a2 = {**a, "id": "ap-deep-cooler-2026-11-06"}             # a new night adds evidence for the same idea
    r = _release(n.c, n.p1, [a2, b])
    assert [x["id"] for x in r["items"]] == [b["id"]], "the dismissed idea stays snoozed"
    assert r["limits"]["snoozed"][0]["phase"] == "deep" and r["limits"]["snooze_nights"] == 14
    n.clock.jump_to(ts("2026-11-21T08:00"))
    assert [x["id"] for x in _release(n.c, n.p1, [a2])["items"]] == [a2["id"]], "back after 14 nights"
    assert len(_release(n.c, n.p1, [a2, b], show_all=True)["items"]) == 2          # Studio still sees everything


# ───────── start over: recalibrate ─────────
def test_recalibrate_wipes_only_autopilot_learning_for_that_person(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", headers=H, json={"jump_to": "2026-11-06T09:00"})
        ctl = c.app.state.notifier.c
        st, now = ctl.store, ctl.clock.now_ts()
        for pid in ("p1", "p2"):
            st.event(now - 3600, "autopilot", pid, "autopilot", status="suggested", direction="cooler", delta_c=-0.56)
            st.sample(now - 3600, f"ap:{pid}:score", 1.2)
            st.put(f"ap_recheck:{pid}:2026-11-05", {"moments": []})
            st.put(f"ap_dismissed:{pid}", {"ap-deep-cooler-x": {"ts": now}})
        st.event(now - 3000, "feel", "p1", "app", feel="too_hot")
        s = c.get("/api/household").json()["people"][0]["settings"]
        s["autopilot"]["trust"] = {"bed": 0.5, "wearable": 2, "room": 1}
        s["autopilot"]["sensitivity"] = "high"
        assert c.put("/api/people/p1/settings", json=s, headers=H).status_code == 200
        r = c.post("/api/people/p1/autopilot/recalibrate", headers=H).json()
        assert r["ok"] and r["wiped"]["decisions"] == 1 and r["wiped"]["rechecks"] == 1
        assert r["learning"] == {"need": 7, "have": 0, "done": False, "skipped": False}
        pol = c.get("/api/people/p1/autopilot").json()["policy"]
        assert pol["trust"] == {"bed": 1.0, "wearable": 1.0, "room": 1.0} and pol["learn_since"] >= now
        assert pol["sensitivity"] == "high", "other settings are kept"
        assert not [e for e in st.events(limit=500, person="p1") if e["kind"] == "autopilot" and e["person"] == "p1"]
        assert st.samples("ap:p1:score", 0, now) == [] and st.get("ap_recheck:p1:2026-11-05") is None
        assert st.get("ap_dismissed:p1") is None
        assert [e for e in st.events(limit=500, person="p1") if e["kind"] == "feel"], "your own taps (sleep history) stay"
        # the other person is untouched
        assert [e for e in st.events(limit=500, person="p2") if e["kind"] == "autopilot" and e["person"] == "p2"]
        assert st.samples("ap:p2:score", 0, now) and st.get("ap_recheck:p2:2026-11-05") and st.get("ap_dismissed:p2")
        assert c.get("/api/people/p1/rhythms").json()
        # a phone with an older copy of the settings saving later can't undo the fresh start
        s["autopilot"]["learn_since"] = None
        assert c.put("/api/people/p1/settings", json=s, headers=H).status_code == 200
        assert c.get("/api/people/p1/autopilot").json()["policy"]["learn_since"] == pol["learn_since"]


def test_recalibrate_is_for_the_owner_or_the_person():
    me = Principal(kind="user", id="u2", name="A", role="sleeper", person="p2")
    other = Principal(kind="user", id="u3", name="B", role="sleeper", person="p1")
    viewer = Principal(kind="user", id="u4", name="V", role="viewer", person=None)
    shares = lambda pid: (True, True)                               # noqa: E731
    path = "/api/people/p2/autopilot/recalibrate"
    assert authorize(me, "POST", path, shares) is None
    assert authorize(other, "POST", path, shares) is not None
    assert authorize(viewer, "POST", path, shares) is not None


async def test_learning_counts_only_nights_after_recalibrating(tmp_path):
    lab = Lab(tmp_path, "2026-11-01T20:00", sleepers={"left": Sleeper()})
    lab.p1.settings.autopilot.learn_nights = 2
    await lab.nights(2)
    assert lab.c.autopilot.learning(lab.p1, lab.clock.now_ts())["have"] == 2
    from embertide.autopilot_api import _recalibrate
    _recalibrate(lab.c, lab.p1)
    assert lab.c.autopilot.learning(lab.p1, lab.clock.now_ts())["have"] == 0
    assert lab.c.autopilot.baseline(lab.p1, lab.clock.now_ts()) is None
    await lab.nights(2)
    assert lab.c.autopilot.learning(lab.p1, lab.clock.now_ts())["done"]


# ───────── recheck in plain words ─────────
def test_recheck_text():
    assert recheck_text(0, 0) == "Looked again with all the data now: nothing it would have changed."
    assert recheck_text(1, 0) == "1 moment it would have changed."
    assert recheck_text(2, 1) == "2 moments it would have changed (1 it missed live)."


def test_recheck_reports_moments_and_what_live_missed(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", headers=H, json={"jump_to": "2026-11-06T09:00"})
        r = c.post("/api/people/p1/autopilot/recheck", headers=H, json={"date": "2026-11-05"}).json()
        assert r["text"] and r["would_change"] == len(r["moments"]) and r["missed_live"] <= r["would_change"]
        night = c.get("/api/people/p1/autopilot/night?date=2026-11-05").json()
        assert night["recheck"]["text"] == r["text"]
