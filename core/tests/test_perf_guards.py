"""Guards for the performance work: caches must never serve stale data, and thinned series must keep the picture."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from embertide.engine.summary import summarize
from embertide.seed import default_household
from embertide.store import Store
from embertide.studio_api import thin

TZ = ZoneInfo("America/New_York")


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


# ───────────── sample source names cache ─────────────
def test_sample_sources_sees_new_and_dropped_sources(tmp_path):
    st = Store(tmp_path / "s.db")
    st.sample(1.0, "wear:p1:hr:Watch", 60)
    assert st.sample_sources("wear:p1:hr:") == ["wear:p1:hr:Watch"]
    st.samples_many([(2.0, "wear:p1:hr:Ring", 61), (3.0, "wear:p2:hr:Ring", 62)])
    assert st.sample_sources("wear:p1:hr:") == ["wear:p1:hr:Ring", "wear:p1:hr:Watch"]
    assert st.sample_sources("wear:p1_hr") == []                          # a plain prefix, not a LIKE pattern
    st.drop_after(1.5)
    assert st.sample_sources("wear:p1:hr:") == ["wear:p1:hr:Watch"]


# ───────────── finished-night summary cache ─────────────
def _night(st, p, d, start, end, occupied=1):
    rows = []
    t = ts(start)
    while t <= ts(end):
        rows.append((t, p.zone, 1, 18.0, 18.1, occupied, 55, 14, 0, 18.0, "deep", occupied, p.id))
        t += 15
    st.telemetry(rows)


def test_summary_cache_invalidates_when_the_night_changes(tmp_path):
    st = Store(tmp_path / "s.db")
    p = default_household().people[0]
    d = date(2026, 11, 2)
    _night(st, p, d, "2026-11-02T22:30", "2026-11-03T06:30")
    now = ts("2026-11-04T12:00")
    a = summarize(st, p, d, TZ, now)
    b = summarize(st, p, d, TZ, now)
    assert a == b and a["has_data"]
    b["moments"].append({"kind": "x"})                                     # callers can't poison the cache
    assert summarize(st, p, d, TZ, now) == a
    st.event(ts("2026-11-03T02:00"), "hot_flash", p.id, "app", minutes=15)  # a late-arriving event inside the night
    c = summarize(st, p, d, TZ, now)
    assert any(m["kind"] == "hot_flash" for m in c["moments"]) and c != a
    st.put(f"feedback:{p.id}:{d.isoformat()}", {"temp": "warm"})           # a morning check-in
    assert summarize(st, p, d, TZ, now)["feedback"] == {"temp": "warm"}
    from embertide.engine import summary as S
    calls = []
    real = S._summarize
    S._summarize = lambda *a, **k: calls.append(1) or real(*a, **k)
    try:
        summarize(st, p, d, TZ, now)
        assert calls == []                                                 # unchanged night: served from the cache
        p.rhythms[0].schedule.wake = "07:30"                               # a rhythm edit means a fresh summary
        summarize(st, p, d, TZ, now)
        assert calls == [1]
    finally:
        S._summarize = real


def test_in_progress_night_is_never_cached(tmp_path):
    st = Store(tmp_path / "s.db")
    p = default_household().people[0]
    d = date(2026, 11, 2)
    _night(st, p, d, "2026-11-02T22:30", "2026-11-03T01:00")
    early = summarize(st, p, d, TZ, ts("2026-11-03T01:00"))
    _night(st, p, d, "2026-11-03T01:00:15", "2026-11-03T03:00")
    later = summarize(st, p, d, TZ, ts("2026-11-03T03:00"))
    assert later["in_bed_min"] > early["in_bed_min"]


def test_old_nights_keep_their_events_however_many_come_later(tmp_path):
    st = Store(tmp_path / "s.db")
    p = default_household().people[0]
    d = date(2026, 11, 2)
    _night(st, p, d, "2026-11-02T22:30", "2026-11-03T06:30")
    st.event(ts("2026-11-03T02:00"), "hot_flash", p.id, "app", minutes=15)
    for i in range(2500):                                                  # lots of later activity
        st.event(ts("2026-11-04T12:00") + i, "adjust", p.id, "app", delta_c=0.1)
    s = summarize(st, p, d, TZ, ts("2026-11-06T12:00"))
    assert any(m["kind"] == "hot_flash" for m in s["moments"])


# ───────────── explorer thinning ─────────────
def test_step_series_keep_every_change_and_real_gaps():
    pts = [[i * 15, 1 if 100 <= i < 200 else 0] for i in range(1000)] + [[20000 + i * 15, 1] for i in range(50)]
    out = thin(pts, "step", 2000)
    assert len(out) < len(pts) / 5
    vals = [v for _, v in out]
    changes = sum(1 for a, b in zip(vals, vals[1:]) if a != b)
    assert changes == 3                                                    # 0→1, 1→0, gap then 1
    assert max(b[0] - a[0] for a, b in zip(out, out[1:])) >= 20000 - 999 * 15 - 1   # the gap is still a gap
    assert all(b[0] - a[0] <= 300 for a, b in zip(out, out[1:]) if b[0] < 15000)    # a point at least every 5 min


def test_line_series_respect_budget_and_keep_peaks():
    import math
    pts = [[i * 15, 18 + math.sin(i / 50)] for i in range(5000)]
    pts[2345][1] = 30.0                                                    # one spike
    out = thin(pts, "line", 600)
    assert len(out) <= 650 and out[0] == pts[0] and out[-1] == pts[-1]
    assert max(v for _, v in out) == 30.0 and min(v for _, v in out) == min(v for _, v in pts)
    assert all(a[0] < b[0] for a, b in zip(out, out[1:]))


def test_flat_runs_shrink_but_short_series_are_untouched():
    flat = [[i * 15, 20.0] for i in range(500)]
    assert len(thin(flat, "line", 2000)) < 20
    few = [[0, 1.0], [15, 2.0]]
    assert thin(few, "line", 2000) == few
