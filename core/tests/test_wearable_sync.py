"""Watch data from the phone must end up on its nights, whatever order and however late it arrives.

What went wrong in real use (a watch whose data reaches Health Connect through its maker's app):
- the phone read only from its last sync, and the watch app wrote the night hours later, so stretches were never read;
- a short sync (an hour of readings) "lined up" with the wrong part of a bed night and was shifted by hours;
- a weak match was shown as a warning to check, although the times were simply right (same phone clock);
- an hour's step total that changed after it was sent was stored twice.
"""

import random

from embertide import align as A
from embertide import wear_repair

from test_align import H, TZ, make, night_hr


def bed_night(c, start, n=1800, seed=3, zone="left"):
    """The bed's heart rate every 15 s for a night, on p1's side. Returns [(t, hr)]."""
    store = c.app.state.notifier.c.store
    bed = [(t, round(v)) for t, v in night_hr(start, n=n, seed=seed)]
    store.telemetry([(t, zone, 1, 18.0, 18.2, 1, hr, 14, 0, 18.0, "deep", 1, "p1") for t, hr in bed])
    return bed


def post(c, device, metric, points, **kw):
    return c.post("/api/people/p1/wearable", json={"device": device, "metric": metric, "points": points, **kw}, headers=H).json()


# ---------- the rules ----------
def test_judge_needs_strong_evidence_for_a_shift():
    assert A.judge(None) == "weak"
    assert A.judge({"lag": 1, "r": 0.5, "overlap": 40, "distinct": True, "r0": 0.48}) == "zero"
    strong = {"lag": 60, "r": 0.85, "overlap": 400, "distinct": True, "r0": 0.1}
    assert A.judge(strong) == "shift"
    assert A.judge({**strong, "overlap": 50}) == "weak"                    # an hour of readings proves nothing
    assert A.judge({**strong, "distinct": False}) != "shift"               # no clear peak
    assert A.judge({**strong, "r0": 0.8}) == "zero"                        # leaving the times alone fits about as well
    assert A.judge({"lag": 200, "r": 0.2, "overlap": 300, "distinct": False, "r0": 0.1}) == "unlike"


def test_decide_shift():
    assert A.decide_shift([("shift", 60)], False) == (60, "strong")
    assert A.decide_shift([("shift", 60), ("shift", 62)], False) == (61, "strong")
    assert A.decide_shift([("shift", 60), ("zero", 0)], False) == (None, "mixed")
    assert A.decide_shift([("shift", 60), ("shift", 300)], False) == (None, "mixed")
    assert A.decide_shift([("shift", 60)], True) == (None, "live")          # the live watch heart rate agrees as recorded
    assert A.decide_shift([("weak", 400)], False) == (None, "unproven")
    assert A.decide_shift([("zero", 0)], False) == (None, "lines_up")


def test_live_agreement():
    pts = [(1000.0 + 60 * i, 60.0 + (i % 5)) for i in range(60)]
    live = [(t + 17, v + 1) for t, v in pts]
    la = A.live_agreement(pts, live)
    assert la and la["agree"] and la["pairs"] == 60
    assert not A.live_agreement(pts, [(t + 17, v + 12) for t, v in pts])["agree"]
    assert A.live_agreement(pts[:10], live) is None                        # too few side by side


# ---------- imports ----------
def test_a_short_sync_is_never_shifted_onto_the_wrong_part_of_the_night(tmp_path):
    """Regression (real night): 52 evening readings matched a stretch of the previous bed night at r 0.64 and were
    moved 9.7 h back. Here 52 evening readings happen to rise and fall like an hour of the night (the strongest
    possible chance match): the old rule shifted them; the import must keep them at the phone's time."""
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T21:00"}, headers=H)
        start = A.parse_time("2026-11-05T22:20", TZ)
        bed = bed_night(c, start)
        evening = A.parse_time("2026-11-06T09:30", TZ)
        found = [(evening + 60 * i, hr + 8) for i, (_, hr) in enumerate(bed[600:600 + 52 * 4:4])]
        d = A.lag_detail(found, [(t, float(h)) for t, h in bed])
        assert d and d["r"] >= 0.6 and d["distinct"] and abs(d["lag"]) >= 3     # what the old rule would have shifted
        assert A.judge(d) == "weak"
        r = post(c, "Samsung Health", "hr", [[t, v] for t, v in found])
        assert r["shift_applied_s"] == 0 and r["stored"] == len(found), (r["notes"], r["warnings"], r["nights"])
        assert not r["warnings"]
        assert c.app.state.notifier.c.store.get("device_offset:p1:Samsung Health") in (None, {}) or \
            c.app.state.notifier.c.store.get("device_offset:p1:Samsung Health")["shift_s"] == 0


def test_a_morning_sync_with_part_of_the_night_is_kept_without_a_warning(tmp_path):
    """An hour and a half of the night's end (all the phone had read yet): weak evidence, kept as recorded, and said
    plainly as a note rather than something to check."""
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T09:00"}, headers=H)
        start = A.parse_time("2026-11-05T22:20", TZ)
        bed = bed_night(c, start)
        tail = [[t, hr + 1] for t, hr in bed[-360:][::4]]                  # last 90 min, one a minute
        r = post(c, "Samsung Health", "hr", tail)
        assert r["shift_applied_s"] == 0 and r["stored"] == len(tail)
        assert not r["warnings"], r["warnings"]


def test_live_watch_heart_rate_vouches_for_the_import(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T09:00"}, headers=H)
        start = A.parse_time("2026-11-05T22:20", TZ)
        bed = bed_night(c, start)
        noisy = random.Random(5)
        live = [[t + 7, hr + noisy.choice((-1, 0, 1))] for t, hr in bed[::2]]
        post(c, "watch_live", "hr", live, align="off")
        part = [[t, hr] for t, hr in bed[-400:][::4]]
        r = post(c, "Samsung Health", "hr", part)
        assert r["shift_applied_s"] == 0 and not r["warnings"]
        assert any("live heart rate" in n for n in r["notes"]), r["notes"]


def test_a_real_clock_error_is_still_corrected(tmp_path):
    """The stricter rule mustn't stop a genuine whole-night time-zone mistake from being fixed."""
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-07T09:00"}, headers=H)
        start = A.parse_time("2026-11-05T22:20", TZ)
        bed = bed_night(c, start)
        r = post(c, "Export", "hr", [[t + 3600, hr] for t, hr in bed[::4]])
        assert r["shift_applied_s"] == -3600, (r["nights"], r["notes"])


def test_re_reading_the_last_two_days_stores_only_whats_new(tmp_path):
    """Every sync re-sends the last 48 h: what's already there is counted as such, only new readings are stored, and
    the repeats don't make a short new stretch look like a clock problem."""
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T12:00"}, headers=H)
        start = A.parse_time("2026-11-05T22:20", TZ)
        bed = bed_night(c, start)
        first = [[t, hr] for t, hr in bed[:1200][::4]]                     # the first part, sent at 3 AM
        a = post(c, "Samsung Health", "hr", first)
        assert a["stored"] == len(first)
        everything = [[t, hr] for t, hr in bed[::4]]                       # the whole night, re-read in the morning
        b = post(c, "Samsung Health", "hr", everything)
        assert b["stored"] == len(everything) - len(first)
        assert b["rejected"]["duplicate"] == len(first)
        assert b["shift_applied_s"] == 0 and not b["warnings"]
        again = post(c, "Samsung Health", "hr", everything)
        assert again["stored"] == 0 and again["rejected"]["duplicate"] == len(everything)


def test_an_hours_step_total_that_changed_replaces_the_old_one(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T12:00"}, headers=H)
        t = A.parse_time("2026-11-06T08:00", TZ)
        post(c, "Phone", "steps", [[t, 100], [t + 3600, 40]])
        post(c, "Phone", "steps", [[t, 180], [t + 3600, 40]])
        got = c.app.state.notifier.c.store.samples("wear:p1:steps:Phone", t - 1, t + 3601)
        assert [(round(x), v) for x, v in got] == [(round(t), 180.0), (round(t + 3600), 40.0)]


# ---------- what the phone shows afterwards ----------
def test_nights_endpoint_says_what_is_there_and_what_is_missing(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T11:00"}, headers=H)
        start = A.parse_time("2026-11-05T22:20", TZ)
        bed = bed_night(c, start)
        post(c, "Samsung Health", "hr", [[t, hr] for t, hr in bed[: len(bed) // 3][::4]])
        nights = c.get("/api/people/p1/wearable/nights", headers=H).json()
        n = next(x for x in nights if x["date"] == "2026-11-05")
        assert 0 < n["hr"]["Samsung Health"] < 0.8 and not n["stages"]
        assert "no sleep stages" in n["text"] and "later sync" in n["text"]
        segs = [{"start": start + i * 1800, "end": start + (i + 1) * 1800, "stage": ["light", "deep", "rem"][i % 3]} for i in range(14)]
        c.post("/api/people/p1/stages", json={"source": "health-connect", "label": "Samsung Health · Health Connect", "segments": segs}, headers=H)
        post(c, "Samsung Health", "hr", [[t, hr] for t, hr in bed[::4]])
        n = next(x for x in c.get("/api/people/p1/wearable/nights", headers=H).json() if x["date"] == "2026-11-05")
        assert n["stages"] and n["hr"]["Samsung Health"] >= 0.8 and "whole night" in n["text"]
        # the Sleep screen shows the watch's stages first, the bed's estimate after
        st = c.get("/api/people/p1/stages?date=2026-11-05", headers=H).json()
        assert st[0]["kind"] == "wearable" and st[0]["label"].startswith("Samsung Health")


# ---------- the one-off repair ----------
def test_repair_forgets_wrongly_shifted_readings_once(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-07T09:00"}, headers=H)
        ctl = c.app.state.notifier.c
        store = ctl.store
        src = "wear:p1:hr:Samsung Health"
        t0 = A.parse_time("2026-11-06T09:40", TZ)
        store.samples_many([(t0 + 60 * i, src, 60.0) for i in range(52)])                    # landed in the wrong place
        keep = A.parse_time("2026-11-05T23:00", TZ)
        store.samples_many([(keep + 60 * i, src, 58.0) for i in range(30)])                  # another night: untouched
        bad = {"ts": t0 + 50000, "device": "Samsung Health", "metric": "hr", "stored": 52, "shift_applied_s": -35040.0,
               "notes": ["Shifted by -584 min: the device was 584 min ahead of the bed."],
               "nights": [{"date": "2026-11-05", "samples": 52, "first": t0, "last": t0 + 51 * 60, "coverage_in_bed": 0.11, "lag_min": -584, "match": 0.64}]}
        good = {"ts": t0 + 60000, "device": "Export", "metric": "hr", "stored": 900, "shift_applied_s": -3600.0,
                "notes": ["Shifted by -60 min: the device was exactly 1 h ahead: ..."],
                "nights": [{"date": "2026-11-05", "samples": 900, "coverage_in_bed": 0.95, "lag_min": -60, "match": 0.9}]}
        store.put("imports:p1", [bad, good])
        store.put("device_offset:p1:Samsung Health", {"shift_s": -35040.0, "ts": bad["ts"], "nights": 1})
        dry = c.post("/api/people/p1/wearable/repair", json={}, headers=H).json()
        assert [i["device"] for i in dry["items"]] == ["Samsung Health"] and not dry["applied"]
        assert len(store.samples(src, t0 - 1, t0 + 3600)) == 52                               # listing changes nothing
        done = c.post("/api/people/p1/wearable/repair", json={"apply": True}, headers=H).json()
        assert done["items"][0]["deleted"] == 52 and "Re-import last 7 days" in done["text"]
        assert store.samples(src, t0 - 1, t0 + 3600) == []
        assert len(store.samples(src, keep - 1, keep + 3600)) == 30
        assert store.get("device_offset:p1:Samsung Health")["shift_s"] == 0
        # the phone re-imports; running the repair again must not throw that away
        store.samples_many([(t0 + 60 * i, src, 61.0) for i in range(10)])
        again = wear_repair.repair(store, "p1", TZ, ctl.clock.now_ts(), apply=True)
        assert again["items"][0]["done"] and again["items"][0]["deleted"] == 0
        assert len(store.samples(src, t0 - 1, t0 + 3600)) == 10
