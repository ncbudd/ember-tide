"""Lab: Autopilot's decision function against conflicting, lopsided and weighted evidence.

What we want: the person's own word is the strongest thing it hears, one source alone never moves the bed (except a
tap), restlessness on its own isn't proof of temperature, and turning a source's trust down really removes it.
"""

import pytest

from embertide.engine.autopilot import Window, evaluate, merge_hr, signals_at
from embertide.models import AutopilotPolicy

NOW = 100_000.0
IN_BED = NOW - 3 * 3600


def rows(*, restless_last10=0.0, hr_base=56, hr_now=None, measured=17.0, target=17.0, step=15):
    out = []
    n = int(3 * 3600 / step)
    for i in range(n):
        t = IN_BED + i * step
        last = t >= NOW - 600
        st = 4 if last and restless_last10 and (i % 10) < restless_last10 * 10 else 0
        hr = hr_now if (last and hr_now is not None and t >= NOW - 300) else hr_base
        out.append({"ts": t, "status": st, "hr": hr, "target": target, "measured": measured if last else target,
                    "occupied": 1, "phase": "deep"})
    return out


def skin(delta=0.0, base=34.0):
    return [(IN_BED + 1500 + i * 300, base) for i in range(25)] + [(NOW - 500 + i * 60, base + delta) for i in range(8)]


def wear_hr(base=55, now_v=None):
    return [(IN_BED + 1300 + i * 60, base) for i in range(140)] + [(NOW - 290 + i * 60, now_v if now_v is not None else base) for i in range(5)]


def tap(feel, ago=120):
    return {"kind": "feel", "ts": NOW - ago, "feel": feel}


def win(**kw):
    base = dict(now=NOW, rows=rows(), in_bed=IN_BED, state="rhythm", phase="deep", flash_active=False, events=[], skin=[],
                persona="neutral", wake=NOW + 4 * 3600)
    base.update(kw)
    return Window(**base)


def decide(w, **pol):
    return evaluate(w, AutopilotPolicy(**pol), 17.0, [])


def acts(d) -> bool:
    return d.status in ("suggested", "applied")


# ───────── one source alone never moves the bed (except your own tap) ─────────
@pytest.mark.parametrize("name,w", [
    ("skin only, strong", dict(skin=skin(+0.9))),
    ("room only, very warm", dict(room=[(NOW - 60 * i, 28.0) for i in range(20)])),
    ("bed warmer than plan only", dict(rows=rows(measured=19.5))),
    ("a hot flash only", dict(events=[{"kind": "hot_flash", "ts": NOW - 1200}])),
    ("humid only", dict(humidity=[(NOW - 60 * i, 80.0) for i in range(20)], room=[(NOW - 60 * i, 22.5) for i in range(20)])),
])
@pytest.mark.parametrize("sens", ["low", "medium", "high"])
def test_one_source_alone_never_acts(name, w, sens):
    d = decide(win(**w), sensitivity=sens)
    assert not acts(d), (name, d.score, [(r.signal, r.push) for r in d.reasons])


# fixed: restlessness and its heart-rate rise are one kind of evidence
@pytest.mark.parametrize("persona", ["tide", "ember", "neutral"])
def test_restlessness_and_its_heart_rate_alone_dont_act(persona):
    d = decide(win(rows=rows(restless_last10=0.7, hr_base=55, hr_now=70), persona=persona), sensitivity="high")
    assert not acts(d), (d.direction, d.score, [r.signal for r in d.reasons])


def test_a_tap_alone_does_act():
    assert acts(decide(win(events=[tap("too_hot")])))
    assert decide(win(events=[tap("too_cold")])).direction == "warmer"


# ───────── conflicting signals ─────────
def test_tap_cold_vs_warm_skin_never_cools():
    d = decide(win(events=[tap("too_cold")], skin=skin(+0.9)))
    assert d.direction != "cooler"


# fixed: your tap sets the direction
def test_your_tap_beats_a_disagreeing_watch():
    d = decide(win(events=[tap("too_hot")], skin=skin(-0.6)))
    assert d.direction == "cooler" and acts(d)


def test_hot_flash_then_cold_tap_does_not_cool():
    d = decide(win(events=[{"kind": "hot_flash", "ts": NOW - 1500}, tap("too_cold")]))
    assert d.direction != "cooler"


def test_warm_room_but_cold_tap_warms_or_waits():
    d = decide(win(events=[tap("too_cold")], room=[(NOW - 60 * i, 26.0) for i in range(20)]))
    assert d.direction in ("warmer", "none")


def test_all_warm_evidence_takes_the_bigger_step():
    d = decide(win(events=[tap("too_hot")], skin=skin(+0.8), rows=rows(restless_last10=0.6, hr_base=55, hr_now=68, measured=18.5)))
    assert d.direction == "cooler" and d.delta_c == pytest.approx(-1.11)


# ───────── trust: weighting one source over another ─────────
def test_trust_zero_removes_the_wearable():
    w = win(events=[tap("too_hot")], skin=skin(+0.9))
    full = decide(w)
    none = decide(w, trust={"bed": 1.0, "wearable": 0.0, "room": 1.0})
    assert any(r.signal == "skin_temp" for r in full.reasons)
    assert not any(r.signal == "skin_temp" for r in none.reasons)


def test_trust_zero_removes_the_bed_sensors():
    w = win(rows=rows(restless_last10=0.7, hr_base=55, hr_now=70, measured=19.5), events=[tap("too_hot")])
    d = decide(w, trust={"bed": 0.0, "wearable": 1.0, "room": 1.0})
    assert not {r.signal for r in d.reasons} & {"movement", "pad"}


def test_trust_scales_the_push():
    w = win(skin=skin(+0.6), events=[tap("too_hot")])
    lo = next(r.push for r in decide(w, trust={"bed": 1, "wearable": 0.5, "room": 1}).reasons if r.signal == "skin_temp")
    hi = next(r.push for r in decide(w, trust={"bed": 1, "wearable": 1.0, "room": 1}).reasons if r.signal == "skin_temp")
    assert lo == pytest.approx(hi / 2, abs=0.02)


def test_trust_never_touches_your_own_taps():
    w = win(events=[tap("too_hot")])
    d = decide(w, trust={"bed": 0.0, "wearable": 0.0, "room": 0.0})
    assert acts(d) and d.direction == "cooler"


@pytest.mark.parametrize("given,expect", [({"bed": -1}, 0.0), ({"wearable": 5}, 2.0), ({"room": 0.333}, 0.33)])
def test_trust_values_are_clamped(given, expect):
    t = AutopilotPolicy(trust={"bed": 1.0, "wearable": 1.0, "room": 1.0, **given}).trust
    assert t[next(iter(given))] == expect


# fixed
def test_nan_trust_is_not_double_trust():
    assert AutopilotPolicy(trust={"room": float("nan")}).trust["room"] <= 1.0


@pytest.mark.parametrize("bed,wear,tb,tw,expect_src,expect_v", [
    (8, 9, 1, 1, "agree", 8.5),          # close: trust-weighted average
    (10, 0, 1, 1, "watch", 0),           # disagree, tie → the watch
    (10, 0, 2, 1, "bed", 10),            # disagree, bed trusted more
    (None, 7, 1, 1, "watch", 7),
    (7, None, 1, 1, "bed", 7),
    (7, 7, 1, 0, "bed", 7),              # watch switched off
])
def test_merge_hr(bed, wear, tb, tw, expect_src, expect_v):
    v, mult, src = merge_hr(bed, wear, tb, tw)
    assert v == pytest.approx(expect_v) and expect_src in src


def test_watch_hr_counts_when_the_bed_has_none():
    """Bed lost the heart signal (status 3); the watch still sees the rise while restless."""
    r = rows(restless_last10=0.7)
    for x in r:
        x["hr"] = None
    w = win(rows=r, wear_hr=wear_hr(55, 70), events=[tap("too_hot")])
    d = decide(w)
    assert any(x.signal == "hr" for x in d.reasons), [x.signal for x in d.reasons]


# ───────── guardrails, combined ─────────
GUARDS = [
    ("not in bed", dict(in_bed=None), {}),
    ("waiting state", dict(state="waiting"), {}),
    ("phase not allowed", dict(phase="wind_down"), {}),
    ("flash running", dict(flash_active=True), {}),
    ("quiet before wake", dict(wake=NOW + 20 * 60), {}),
    ("your own change", dict(events=[tap("too_hot"), {"kind": "adjust", "ts": NOW - 300, "source": "app"}]), {}),
    ("your own flash", dict(events=[tap("too_hot"), {"kind": "hot_flash", "ts": NOW - 300, "source": "app"}]), {}),
]


@pytest.mark.parametrize("name,w,pol", GUARDS, ids=[g[0] for g in GUARDS])
@pytest.mark.parametrize("mode", ["suggest", "act"])
def test_guardrails_block_in_both_modes(name, w, pol, mode):
    base = dict(events=[tap("too_hot")])
    base.update(w)
    d = evaluate(win(**base), AutopilotPolicy(mode=mode, **pol), 17.0, [])
    assert d.status == "blocked" and d.blocked_by, (name, d.status, d.blocked_by)


@pytest.mark.parametrize("mode,status", [("suggest", "suggested"), ("act", "applied")])
def test_mode_sets_the_status(mode, status):
    assert evaluate(win(events=[tap("too_hot")]), AutopilotPolicy(mode=mode), 17.0, []).status == status


def test_limits_over_a_night_never_exceeded():
    """Keep tapping every 25 min all night in Act mode: the night's changes stay inside every limit."""
    pol = AutopilotPolicy(mode="act", max_actions=3, max_delta_c=1.2, min_gap_min=20)
    hist, target = [], 17.0
    for k in range(16):
        now = NOW + k * 25 * 60
        w = win(now=now, events=[{"kind": "feel", "ts": now - 60, "feel": "too_hot"}], wake=NOW + 12 * 3600)
        d = evaluate(w, pol, target, hist)
        if d.status == "applied":
            hist.append(d.to_dict())
            target += d.delta_c
    assert len(hist) <= 3
    assert abs(sum(h["delta_c"] for h in hist)) <= 1.2 + 0.01
    gaps = [b["ts"] - a["ts"] for a, b in zip(hist, hist[1:])]
    assert all(g >= 20 * 60 for g in gaps)


def test_floor_is_respected_when_already_cold():
    d = evaluate(win(events=[tap("too_hot")]), AutopilotPolicy(floor_c=16.8), 17.0, [])
    assert d.status == "blocked" and any("never-below" in b for b in d.blocked_by)


def test_signals_never_see_the_future():
    s = signals_at(win(events=[tap("too_hot", ago=-300), {"kind": "hot_flash", "ts": NOW + 60}]))
    assert s["feel"] is None and s["flash"] is None
