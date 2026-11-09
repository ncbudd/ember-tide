"""A ring's (or any wearable's) own verdict on a night, in plain words, next to what the bed measured.

Cloud wearables such as Oura report a few once-a-night numbers on top of the dense heart rate and stages:
readiness, the ring's sleep score, skin temperature compared with the person's normal (and its 3-day trend),
lowest heart rate, average breathing. They are stored as ordinary wearable samples (wear:<pid>:<metric>:<device>)
stamped at the end of that night's sleep, so a night's values are the ones between its start and a few hours
after it ends.

Temperature vs normal also says when a night wasn't a normal night: a jump of a degree or more is usually
illness, alcohol or a very late meal. Autopilot leaves those nights out of learning (`unusual`) so one fever
night doesn't teach it that the person now runs warm. Smaller, steady swings (a few tenths of a degree that
come and go over weeks, often the menstrual cycle) are real and stay in.
"""

from __future__ import annotations

import statistics
from datetime import datetime

DAILY = ("readiness", "sleep_score", "temp_dev", "temp_trend", "resting_hr", "night_breath")
UNUSUAL_C = 1.0          # temperature vs normal at or above this: not a normal night
WARM_C = 0.5             # noticeably warmer / cooler than normal
AFTER_S = 6 * 3600       # a night's once-a-night numbers are stamped at its end (or the morning, ~07:00)


def _sources(store, pid: str, metric: str) -> list[str]:
    return store.sample_sources(f"wear:{pid}:{metric}:")


def _last_in(store, pid: str, metric: str, a: float, b: float) -> tuple[float, str] | None:
    """The latest value of a once-a-night metric in [a, b], with the device it came from."""
    best: tuple[float, float, str] | None = None
    for src in _sources(store, pid, metric):
        for t, v in store.samples(src, a, b):
            if best is None or t > best[0]:
                best = (t, v, src.split(":", 3)[-1])
    return (best[1], best[2]) if best else None


def unusual(store, pid: str, start: float, end: float) -> float | None:
    """The temperature-vs-normal reading when it marks this night as not normal (≥ 1 °C), else None."""
    got = _last_in(store, pid, "temp_dev", start, end + AFTER_S)
    return got[0] if got and got[0] >= UNUSUAL_C else None


def _band(xs: list[float]) -> list[float] | None:
    if len(xs) < 5:
        return None
    s = sorted(xs)
    return [round(s[int(0.1 * (len(s) - 1))], 1), round(s[int(0.9 * (len(s) - 1))], 1)]


def ring_night(c, p, start: float, end: float, bed: dict | None = None) -> dict | None:
    """The wearable's once-a-night numbers for this night, plain lines, and the temperature trend for 30 nights."""
    store, tz = c.store, c.clock.tz
    vals: dict[str, float] = {}
    device = None
    for m in DAILY:
        got = _last_in(store, p.id, m, start, end + AFTER_S)
        if got:
            vals[m], device = got[0], device or got[1]
    hrv_pts = [v for src in _sources(store, p.id, "hrv") for _, v in store.samples(src, start, end + 3600)]
    if hrv_pts:
        vals["hrv_avg"] = round(statistics.mean(hrv_pts), 1)
        device = device or _sources(store, p.id, "hrv")[0].split(":", 3)[-1]
    if not vals:
        return None
    bed = bed or {}

    # the person's own typical values over the last 30 days, for "normal for you"
    since = start - 30 * 86400
    trend = []
    for src in _sources(store, p.id, "temp_dev"):
        trend += store.samples(src, since, end + AFTER_S)
    by_day: dict[str, float] = {}
    for t, v in sorted(trend):
        by_day[datetime.fromtimestamp(t - 12 * 3600, tz).date().isoformat()] = round(v, 2)   # the evening's date
    nightly_hrv: list[float] = []
    for src in _sources(store, p.id, "hrv"):
        per: dict[str, list[float]] = {}
        for t, v in store.samples(src, since, start):
            per.setdefault(datetime.fromtimestamp(t - 12 * 3600, tz).date().isoformat(), []).append(v)
        nightly_hrv += [statistics.mean(x) for x in per.values() if len(x) >= 6]
    typical = {"hrv_avg": _band(nightly_hrv)}
    for m in ("readiness", "sleep_score", "resting_hr"):
        typical[m] = _band([v for src in _sources(store, p.id, m) for _, v in store.samples(src, since, start)])

    lines: list[dict] = []          # {label, value, note, tone}: tone "good" | "warn" | None

    def typ(m: str, v: float, unit: str = "") -> str:
        b = typical.get(m)
        if not b:
            return ""
        if v < b[0]:
            return f"lower than your usual {b[0]:g}–{b[1]:g}{unit}"
        if v > b[1]:
            return f"higher than your usual {b[0]:g}–{b[1]:g}{unit}"
        return f"your usual is {b[0]:g}–{b[1]:g}{unit}"

    if "readiness" in vals:
        r = vals["readiness"]
        word = "Optimal" if r >= 85 else "Good" if r >= 70 else "Fair" if r >= 60 else "Pay attention"
        lines.append({"key": "readiness", "label": "Readiness", "value": f"{r:.0f}", "note": " · ".join(x for x in (word, typ("readiness", r)) if x),
                      "tone": "good" if r >= 70 else "warn" if r < 60 else None})
    if "sleep_score" in vals:
        s = vals["sleep_score"]
        lines.append({"key": "sleep_score", "label": f"{device or 'Ring'} sleep score", "value": f"{s:.0f}",
                      "note": typ("sleep_score", s), "tone": "good" if s >= 85 else "warn" if s < 60 else None})
    if "temp_dev" in vals:
        d = vals["temp_dev"]
        if d >= UNUSUAL_C:
            note, tone = "Much warmer than your normal: often illness, alcohol or a late meal. Autopilot leaves this night out of what it learns.", "warn"
        elif d >= WARM_C:
            note, tone = "Warmer than your normal.", None
        elif d <= -WARM_C:
            note, tone = "Cooler than your normal.", None
        else:
            note, tone = "About your normal.", "good"
        lines.append({"key": "temp_dev", "label": "Body temperature", "value": f"{d:+.1f} °C", "note": note, "tone": tone, "c": d})
    if "hrv_avg" in vals:
        lines.append({"key": "hrv_avg", "label": "Heart rate variability", "value": f"{vals['hrv_avg']:.0f} ms",
                      "note": typ("hrv_avg", vals["hrv_avg"], " ms") or "Higher usually means more rested.", "tone": None})
    if "resting_hr" in vals:
        bed_low = bed.get("hr_low")
        lines.append({"key": "resting_hr", "label": "Lowest heart rate", "value": f"{vals['resting_hr']:.0f} bpm",
                      "note": f"Bed: {bed_low} bpm" if bed_low else typ("resting_hr", vals["resting_hr"], " bpm"),
                      "tone": None, "bed": bed_low})
    if "night_breath" in vals:
        bed_br = bed.get("br_avg")
        lines.append({"key": "night_breath", "label": "Breathing", "value": f"{vals['night_breath']:.1f} /min",
                      "note": f"Bed: {bed_br} /min" if bed_br else "", "tone": None, "bed": bed_br})
    return {"device": device or "Ring", "values": vals, "typical": typical, "lines": lines,
            "unusual": bool(vals.get("temp_dev", 0) >= UNUSUAL_C),
            "trend": [{"date": k, "c": v} for k, v in sorted(by_day.items())][-30:]}
