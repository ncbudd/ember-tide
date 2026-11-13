"""The bed and the wearable side by side, which one to trust, and the person's own corrections.

Trust ("Which to trust more", AutopilotPolicy.trust_more): wearable (default; most watches and rings measure sleep
better than a bed strip) / balanced / bed. PREFER multiplies Autopilot's per-source trust; chosen_times() picks the
in-bed / asleep / woke / got-up times; stages come from the wearable unless the bed is trusted more.

Corrections ("Fix this night", kv `night_fix:<pid>:<date>`): got into bed / fell asleep / woke / got up, out-of-bed
stretches marked wrong or added, and stretches marked "that was the pet" or "that was me". The raw data is never
changed; summarize() lays the corrections over the bed's numbers (keeping the bed's in `raw`) and marks them
`corrected`. They win over both the bed and the wearable, so Autopilot's learning and normal range use them too.
Each corrected night also teaches a per-person offset (kv `inbed_offsets:<pid>`): if the bed keeps saying "in bed" ten
minutes after the correction or the watch, later nights' chosen times allow for it.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from ..units import clock_style, fmt_clock

PREFER = {"wearable": (0.8, 1.25), "balanced": (1.0, 1.0), "bed": (1.25, 0.8)}   # (bed, wearable) multipliers
TIME_KEYS = ("in_bed_at", "asleep_at", "woke_at", "out_at")
OFFSET_MIN_NIGHTS = 3
WAKE_UP_S = 5 * 60            # an awake stretch this long in the middle of a wearable night counts as a wake-up


# ---------- corrections ----------
def get_fix(store, pid: str, d: str) -> dict | None:
    v = store.get(f"night_fix:{pid}:{d}")
    return v or None


def clean_fix(body: dict, start: float, end: float, pets: list) -> dict:
    """Validate a correction against the night's window. Raises ValueError with a plain message."""
    lo, hi = start - 3 * 3600, end + 3 * 3600
    out: dict = {}
    for k in TIME_KEYS:
        v = body.get(k)
        if v is None:
            continue
        v = float(v)
        if not lo <= v <= hi:
            raise ValueError("times must be within that night")
        out[k] = round(v)
    order = [out.get(k) for k in TIME_KEYS if out.get(k) is not None]
    if order != sorted(order):
        raise ValueError("got into bed, fell asleep, woke and got up must be in that order")

    def spans(key: str, extra=None) -> list:
        res = []
        for s in (body.get(key) or [])[:30]:
            a, b = float(s["start"]), float(s["end"])
            if not (lo <= a < b <= hi) or b - a > 12 * 3600:
                raise ValueError("each stretch needs a start before its end, within that night")
            item = {"start": round(a), "end": round(b)}
            if extra:
                item.update(extra(s))
            res.append(item)
        return res

    out["ups_wrong"] = spans("ups_wrong")
    out["ups_added"] = spans("ups_added")
    ids = {p.id for p in pets}

    def who(s):
        w = s.get("who")
        if w not in ("pet", "me"):
            raise ValueError("a stretch is either the pet or you")
        pid = s.get("pet")
        if w == "pet" and pid is not None and pid not in ids:
            raise ValueError("unknown pet")
        return {"who": w, "pet": pid if w == "pet" else None}
    out["stretches"] = spans("stretches", who)
    return out


def apply_fix(summary: dict, fix: dict | None) -> dict:
    """Lay a correction over a night summary (in place). The bed's own values stay in summary['raw']."""
    if not fix or not summary.get("has_data", True):
        return summary
    raw = {}
    corrected = []
    mapping = {"in_bed_at": "in_bed_at", "asleep_at": "fell_asleep_est", "woke_at": "woke_at", "out_at": "out_at"}
    for k, field in mapping.items():
        if fix.get(k) is not None:
            raw[field] = summary.get(field)
            summary[field] = fix[k]
            corrected.append(field)
    if summary.get("in_bed_at") and summary.get("fell_asleep_est"):
        summary["time_to_settle_min"] = max(0, round((summary["fell_asleep_est"] - summary["in_bed_at"]) / 60))
    if fix.get("in_bed_at") is not None or fix.get("out_at") is not None:
        a, b = summary.get("in_bed_at"), summary.get("out_at")
        if a and b and b > a:
            raw["in_bed_min"] = summary.get("in_bed_min")
            ups = summary.get("up_minutes") or 0
            summary["in_bed_min"] = max(0, round((b - a) / 60) - ups)
    if fix.get("in_bed_at") is not None or fix.get("out_at") is not None:
        # "You settled around 9:52" can't stand when they say they only got in at 10:20 (or were up by 9:30)
        a, b = summary.get("in_bed_at"), summary.get("out_at")
        moms = summary.get("moments") or []
        keep = [m for m in moms if m.get("kind") != "sleep" or ((not a or m["ts"] >= a) and (not b or m["ts"] <= b))]
        if len(keep) != len(moms):
            summary["moments"] = keep
    wrong, added = fix.get("ups_wrong") or [], fix.get("ups_added") or []
    if wrong or added:
        raw["times_up"], raw["up_minutes"] = summary.get("times_up"), summary.get("up_minutes")
        moms = summary.get("moments") or []
        kept = []
        for m in moms:
            if m.get("kind") == "out_of_bed" and any(w["start"] - 300 <= m["ts"] <= w["end"] + 300 for w in wrong):
                continue
            kept.append(m)
        removed = len(moms) - len(kept)
        rem_min = sum(m.get("minutes") or 0 for m in moms if m not in kept)
        for s in added:
            kept.append({"kind": "out_of_bed", "ts": s["start"], "minutes": max(1, round((s["end"] - s["start"]) / 60)),
                         "corrected": True})
        kept.sort(key=lambda m: m["ts"])
        summary["moments"] = kept
        summary["times_up"] = max(0, (summary.get("times_up") or 0) - removed + len(added))
        summary["up_minutes"] = max(0, (summary.get("up_minutes") or 0) - rem_min
                                    + sum(round((s["end"] - s["start"]) / 60) for s in added))
        corrected += ["times_up"]
    if fix.get("stretches"):
        summary["stretches"] = fix["stretches"]
        for s in fix["stretches"]:
            summary.setdefault("moments", []).append({"kind": "corrected_stretch", "ts": s["start"], "end": s["end"],
                                                      "who": s["who"], "pet": s.get("pet"), "corrected": True})
        summary["moments"].sort(key=lambda m: m["ts"])
    summary["raw"] = raw
    summary["corrected"] = corrected + (["stretches"] if fix.get("stretches") else [])
    return summary


def learn_from_fix(controller, p, d: str, fix: dict, start: float, end: float) -> None:
    """A saved correction teaches: pet stretches → that pet's signature; "that was me" → the person's range; the gap
    between the bed's own in/out times and the corrected ones → the person's offset."""
    from . import petq
    store = controller.store
    for s in fix.get("stretches") or []:
        rows = store.history(p.zone, s["start"], s["end"], person=p.id)
        readings = [[r["ts"], r["hr"], r["br"]] for r in rows if r.get("occupied") == 1 and (r.get("hr") or r.get("br"))]
        if not readings:
            continue
        if s["who"] == "pet":
            pid = s.get("pet") or (controller.household.pets[0].id if len(controller.household.pets) == 1 else None)
            if pid:
                petq.learn_pet(store, pid, readings)
        else:
            petq.widen_person(store, p.id, readings)
    from .summary import summarize
    from datetime import date as _date
    s = summarize(store, p, _date.fromisoformat(d), controller.clock.tz, controller.clock.now_ts(), raw=True)
    if s and s.get("has_data"):
        note_offset(store, p.id, d, s.get("in_bed_at"), fix.get("in_bed_at"), s.get("out_at"), fix.get("out_at"))


def note_offset(store, pid: str, d: str, bed_in, ref_in, bed_out, ref_out) -> None:
    offs = store.get(f"inbed_offsets:{pid}") or {}
    cur = offs.get(d) or {}
    if bed_in and ref_in and abs(bed_in - ref_in) <= 3 * 3600:
        cur["in"] = round(bed_in - ref_in)
    if bed_out and ref_out and abs(bed_out - ref_out) <= 3 * 3600:
        cur["out"] = round(bed_out - ref_out)
    if cur:
        offs[d] = cur
        keep = sorted(offs)[-21:]
        store.put(f"inbed_offsets:{pid}", {k: offs[k] for k in keep})


def offset(store, pid: str) -> dict:
    """The person's learned offset: how much later (seconds) the bed says in bed / got up than the truth, as the median
    of the last 14 corrected or watch-checked nights. Only from OFFSET_MIN_NIGHTS nights on; small ones are ignored."""
    offs = store.get(f"inbed_offsets:{pid}") or {}
    out = {"nights": len(offs)}
    for k in ("in", "out"):
        xs = [v[k] for _, v in sorted(offs.items())[-14:] if v.get(k) is not None]
        if len(xs) >= OFFSET_MIN_NIGHTS:
            m = statistics.median(xs)
            out[k] = round(m) if abs(m) >= 180 else 0
    return out


# ---------- the wearable's night ----------
def wearable_night(store, p, start: float, end: float) -> dict | None:
    """The watch or ring's view of a night from its imported sleep stages (+ heart rate)."""
    best = None
    for src, d in store.get_stages(p.id, start, end).items():
        if src in ("orion",):
            continue
        segs = sorted(d["segments"], key=lambda g: g["start"])
        if segs and (best is None or len(segs) > len(best[2])):
            best = (src, d.get("label") or src, segs)
    if not best:
        return None
    src, label, segs = best
    sleep = [g for g in segs if g["stage"] != "awake"]
    if not sleep:
        return None
    asleep, woke = sleep[0]["start"], sleep[-1]["end"]
    ups = [g for g in segs if g["stage"] == "awake" and asleep < g["start"] and g["end"] < woke and g["end"] - g["start"] >= WAKE_UP_S]
    return {"source": src, "label": label, "start": segs[0]["start"], "end": segs[-1]["end"], "asleep_at": asleep,
            "woke_at": woke, "wake_ups": len(ups), "wake_up_times": [g["start"] for g in ups], "segments": segs}


def _bin_series(pts: list[tuple[float, float]], t0: float, step: float = 300) -> dict[int, float]:
    b: dict[int, list[float]] = {}
    for t, v in pts:
        b.setdefault(int((t - t0) // step), []).append(v)
    return {k: statistics.median(v) for k, v in b.items()}


def _corr(a: list[float], b: list[float]) -> float | None:
    if len(a) < 4:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    if sa == 0 or sb == 0:
        return None
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) * sa * sb), 2)


def _clock(ts: float | None, tz, style: str = "24") -> str | None:
    return fmt_clock(ts, style, tz) if ts else None


def chosen_times(bed: dict, wear: dict | None, fix: dict | None, trust: str, off: dict) -> dict:
    """Which time to use for each of got-into-bed / fell asleep / woke / got up, and where it came from."""
    bed_vals = {"in_bed_at": bed.get("in_bed_at"), "asleep_at": bed.get("fell_asleep_est"), "woke_at": None, "out_at": bed.get("out_at")}
    wear_vals = {"in_bed_at": wear["start"], "asleep_at": wear["asleep_at"], "woke_at": wear["woke_at"], "out_at": wear["end"]} if wear else {}
    out = {}
    for k in TIME_KEYS:
        if fix and fix.get(k) is not None:
            out[k] = {"ts": fix[k], "source": "you"}
            continue
        b, w = bed_vals.get(k), wear_vals.get(k)
        if b is not None and k in ("in_bed_at", "out_at"):
            adj = off.get("in" if k == "in_bed_at" else "out") or 0
            b = b - adj
        if w is not None and b is not None and abs(w - b) <= 3 * 3600:
            if trust == "wearable" or (k in ("asleep_at", "woke_at") and trust != "bed"):
                out[k] = {"ts": round(w), "source": "watch"}
            elif trust == "bed":
                out[k] = {"ts": round(b), "source": "bed"}
            else:
                out[k] = {"ts": round((b + w) / 2), "source": "both"}
        elif w is not None and (trust != "bed" or b is None):
            out[k] = {"ts": round(w), "source": "watch"}
        elif b is not None:
            out[k] = {"ts": round(b), "source": "bed" if not (off.get("in") or off.get("out")) else "bed, adjusted"}
    return out


def asleep_away_first(rows: list[dict], wear: dict | None, bed: dict) -> tuple[float, float] | None:
    """The wearable's night began 20+ min before the bed saw anyone, and this side was (nearly) empty meanwhile:
    asleep somewhere else first (2026-11-09: asleep on the couch at 11:46 PM, in bed at 12:55 AM). (from, to) or None."""
    if not (wear and wear.get("start") and bed.get("in_bed_at") and bed["in_bed_at"] - wear["start"] >= 20 * 60):
        return None
    span = [r for r in rows if wear["start"] <= r["ts"] < bed["in_bed_at"] and r.get("occupied") is not None]
    if span and sum(1 for r in span if r["occupied"] == 1) <= 0.2 * len(span):
        return (wear.get("asleep_at") or wear["start"], bed["in_bed_at"])
    return None


def compare_night(controller, p, d: str) -> dict:
    """The bed's night next to the wearable's: times, wake-ups, stages, heart rate; where they disagree; what's used."""
    from datetime import date as _date
    from .stages import agreement, estimate
    from .summary import night_window, summarize
    store, tz, now = controller.store, controller.clock.tz, controller.clock.now_ts()
    ck = clock_style(p.settings)
    dd = _date.fromisoformat(d)
    win = night_window(p, dd, tz, now)
    if not win:
        return {"date": d, "available": False}
    start, end = win
    bed = summarize(store, p, dd, tz, now, raw=True) or {}
    wear = wearable_night(store, p, start - 2 * 3600, end + 2 * 3600)
    fix = get_fix(store, p.id, d)
    trust = getattr(p.settings.autopilot, "trust_more", "wearable")
    off = offset(store, p.id)
    out = {"date": d, "available": True, "trust": trust, "has_wearable": bool(wear), "corrected": bool(fix),
           "fix": fix, "offset": off}
    rows = store.history(p.zone, start, end, person=p.id)
    est = estimate(rows)
    lines, disagree = [], []

    def pair(label_w, label_b, tw, tb, what):
        if tw or tb:
            parts = []
            if tw:
                parts.append(f"{wear_name}: {label_w} {_clock(tw, tz, ck)}")
            if tb:
                parts.append(f"Bed: {label_b} {_clock(tb, tz, ck)}")
            lines.append(" · ".join(parts))
        if tw and tb and abs(tw - tb) >= 15 * 60:
            disagree.append(f"{what}: {round(abs(tw - tb) / 60)} min apart")

    from .wearables import device_kind, night_devices
    out["devices"] = night_devices(controller, p, start, end)          # which watch or ring this night's readings came from
    kind = device_kind(f"{wear['source']} {wear['label']}") if wear else "watch"
    wear_name = {"ring": "Ring", "band": "Band"}.get(kind, "Watch")
    out["wearable_name"] = wear_name if wear else None
    away_first = asleep_away_first(rows, wear, bed)
    if wear:
        if away_first:
            lines.append(f"{wear_name}: asleep away from the bed {_clock(away_first[0], tz, ck)}–{_clock(away_first[1], tz, ck)}, "
                         f"then in bed")
        else:
            pair("in bed", "in bed", wear["start"], bed.get("in_bed_at"), "Got into bed")
            pair("asleep", "settled", wear["asleep_at"], bed.get("fell_asleep_est"), "Fell asleep")
        pair("awake", "up", wear["woke_at"], bed.get("out_at"), "Woke")
        if (bed.get("times_up") or 0) != wear["wake_ups"]:
            lines.append(f"Wake-ups · {wear_name}: {wear['wake_ups']} · Bed: up {bed.get('times_up') or 0}×")
        # stages: wearable asleep/awake vs the bed's settled/restless estimate, minute by minute
        to_two = lambda segs: [{**g, "stage": "awake" if g["stage"] == "awake" else "asleep"} for g in segs]  # noqa: E731
        a2 = agreement(to_two(wear["segments"]), to_two(est)) if est else None
        a4 = agreement(wear["segments"], est) if est else None
        out["stage_agreement"] = None if a2 is None else round(a2 * 100)
        out["stage_agreement_detail"] = None if a4 is None else round(a4 * 100)
        if a2 is not None and a2 < 0.7:
            disagree.append(f"Asleep vs awake agree only {round(a2 * 100)}% of the time")
        # heart rate traces, 5-minute medians where both have data
        whr = controller.autopilot.wear_hr(p, start, end, level=False)     # the device as it reads, not lined up
        bhr = [(r["ts"], r["hr"]) for r in rows if r.get("hr") and r.get("occupied") == 1]
        bw, bb = _bin_series(whr, start), _bin_series(bhr, start)
        both = sorted(set(bw) & set(bb))
        if both:
            diffs = [bb[k] - bw[k] for k in both]
            out["hr"] = {"bins": len(both), "bed_minus_watch": round(statistics.median(diffs), 1),
                         "together": _corr([bb[k] for k in both], [bw[k] for k in both])}
            if abs(out["hr"]["bed_minus_watch"]) >= 5:
                disagree.append(f"Heart rate: the bed reads {abs(out['hr']['bed_minus_watch']):.0f} bpm "
                                f"{'higher' if out['hr']['bed_minus_watch'] > 0 else 'lower'} than the {wear_name.lower()}")
    if not wear:
        # no sleep tracking from a watch or ring, but live heart rate from the watch (Wear OS relay): still compare it
        whr = controller.autopilot.wear_hr(p, start, end, level=False)
        bhr = [(r["ts"], r["hr"]) for r in rows if r.get("hr") and r.get("occupied") == 1]
        bw, bb = _bin_series(whr, start), _bin_series(bhr, start)
        both = sorted(set(bw) & set(bb))
        if both:
            diffs = [bb[k] - bw[k] for k in both]
            med = round(statistics.median(diffs), 1)
            out["hr"] = {"bins": len(both), "bed_minus_watch": med, "together": _corr([bb[k] for k in both], [bw[k] for k in both])}
            out["wearable_name"] = "Watch"
            side = "matched" if abs(med) < 2 else f"read {abs(med):.0f} bpm {'higher' if med > 0 else 'lower'} than"
            lines.append(f"Watch heart rate ({len(whr)} readings): the bed {side} the watch"
                         + ("" if side == "matched" else " on average"))
            if abs(med) >= 5:
                disagree.append(f"Heart rate: the bed reads {abs(med):.0f} bpm {'higher' if med > 0 else 'lower'} than the watch")
    # readings marked as probably not worn (flat heart rate) stay visible but are left out of the comparison above
    from .wearables import not_worn_devices
    out["not_worn"] = not_worn_devices(controller, p, start, end)
    for name in out["not_worn"]:
        lines.append(f"{name}: the heart rate hardly changed, so it was probably not being worn. Not used for this comparison")
    out["times"] = chosen_times(bed, wear, fix, trust, off)
    out["away_first"] = {"from": away_first[0], "to": away_first[1]} if away_first else None
    if away_first and not (fix or {}).get("in_bed_at"):
        out["times"]["in_bed_at"] = {"ts": bed["in_bed_at"], "source": "bed"}     # in bed is when the BED saw them
    out["lines"] = lines
    out["disagree"] = disagree
    src_label = {"watch": f"your {(wear_name or 'watch').lower()}", "bed": "the bed", "both": "both", "you": "your correction",
                 "bed, adjusted": "the bed (allowing for its usual lag)"}
    using = []
    if wear and trust != "bed":
        using.append(f"Using your {wear_name.lower()} for stages")
    elif est:
        using.append("Using the bed for stages")
    t = out["times"].get("in_bed_at")
    if t:
        using.append(f"In-bed time from {src_label.get(t['source'], t['source'])}")
    if out["devices"]:
        using.append("Readings from " + " and ".join(d["name"] for d in out["devices"]))
    if fix:
        using.append("Your corrections win over both")
    out["using"] = using
    out["summary"] = (lines[0] if lines else ("No watch or ring data for this night" if not wear else ""))
    if wear and not fix:
        note_offset(store, p.id, d, bed.get("in_bed_at"), wear["start"], bed.get("out_at"), wear["end"])
    return out
