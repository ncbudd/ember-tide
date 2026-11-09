"""Lining imported data up with the bed, before it's trusted.

Wearables and phone exports get time wrong in a handful of predictable ways:
- milliseconds instead of seconds, or ISO strings with/without a time zone
- a watch clock that's a few minutes off
- a whole-hour shift (time zone or daylight-saving mistake in an export)
- the same data imported twice
- data for when nobody was in bed, or with big gaps

This module normalises times, drops duplicates and impossible values, measures how well each night is covered, and,
when the import has heart rate, finds the time shift that best lines the wearable up with the bed's own heart rate.
A learned per-device offset is then applied to that device's other data (skin temperature has nothing in the bed to
compare against, so it borrows the offset from the same device's heart rate).
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

RANGES = {"skin_temp": (25.0, 42.0), "hr": (25.0, 220.0), "hrv": (1.0, 300.0), "resp_rate": (3.0, 60.0), "spo2": (50.0, 100.0),
          "resting_hr": (25.0, 150.0), "body_temp": (33.0, 43.0), "steps": (0.0, 100000.0), "active_kcal": (0.0, 10000.0),
          "exercise_min": (0.0, 1440.0), "caffeine_mg": (0.0, 2000.0), "weight_kg": (20.0, 400.0),
          "temp_dev": (-5.0, 5.0), "readiness": (0.0, 100.0), "temp_trend": (-5.0, 5.0), "sleep_score": (0.0, 100.0),
          "night_breath": (3.0, 60.0)}     # Oura: skin temperature vs your normal (°C), readiness score


def parse_time(v, tz: ZoneInfo) -> float | None:
    """Seconds since epoch from seconds, milliseconds, or an ISO string (naive = the household's time zone)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if not math.isfinite(v) or v <= 0:
            return None
        return float(v) / 1000.0 if v > 1e11 else float(v)
    if isinstance(v, str):
        s = v.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                return parse_time(float(s), tz)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.timestamp()
    return None


def clean(points, metric: str, tz: ZoneInfo, now: float, existing: set[int], shift_s: float = 0.0) -> tuple[list[tuple[float, float]], dict]:
    """Normalise and filter. `existing` = whole-second timestamps already stored for this source (duplicates)."""
    lo, hi = RANGES.get(metric, (-1e9, 1e9))
    out, seen = [], set()
    rej = {"bad_time": 0, "future": 0, "too_old": 0, "out_of_range": 0, "duplicate": 0}
    for p in points:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            rej["bad_time"] += 1
            continue
        t = parse_time(p[0], tz)
        try:
            v = float(p[1])
        except (TypeError, ValueError):
            rej["out_of_range"] += 1
            continue
        if t is None:
            rej["bad_time"] += 1
            continue
        t += shift_s
        if t > now + 300:
            rej["future"] += 1
            continue
        if t < now - 400 * 86400:
            rej["too_old"] += 1
            continue
        if not math.isfinite(v) or not lo <= v <= hi:
            rej["out_of_range"] += 1
            continue
        k = int(round(t))
        if k in existing or k in seen:
            rej["duplicate"] += 1
            continue
        seen.add(k)
        out.append((t, v))
    out.sort()
    return out, rej


def _bins(pts, start: float, end: float, size: float = 60.0) -> dict[int, float]:
    acc: dict[int, list[float]] = {}
    for t, v in pts:
        if start <= t <= end:
            acc.setdefault(int((t - start) // size), []).append(v)
    return {k: statistics.mean(v) for k, v in acc.items()}


def _highpass(b: dict[int, float], w: int = 7) -> dict[int, float]:
    """Each 1-minute bin minus the mean of the bins within ±w minutes: keeps the minute-to-minute ups and downs that
    line two recordings up, and drops the slow drift that makes any two nights look alike."""
    out: dict[int, float] = {}
    for k, v in b.items():
        tot = cnt = 0
        for j in range(k - w, k + w + 1):
            x = b.get(j)
            if x is not None:
                tot += x
                cnt += 1
        if cnt >= w:
            out[k] = v - tot / cnt
    return out


def lag_detail(wear: list[tuple[float, float]], bed: list[tuple[float, float]], max_lag_min: int = 13 * 60) -> dict | None:
    """Minutes to ADD to the wearable's times so its heart rate lines up with the bed's, searched over ±13 h at
    1-minute resolution. Returns {lag, r, overlap, runner_up, distinct} or None.

    Guards against chance matches between unrelated recordings:
    - correlation is on high-passed heart rate (minute-to-minute changes), not the level
    - a lag only counts when the overlap is most of the shorter recording (60%) or at least 3 hours
    - the best lag must stand clearly above the best one more than 20 minutes away (`distinct`)
    Sleep cycles repeat about every 90 minutes, so among near-equal fits the smallest shift wins."""
    if len(wear) < 30 or len(bed) < 30:
        return None
    start = min(bed[0][0], wear[0][0]) - max_lag_min * 60
    end = max(bed[-1][0], wear[-1][0]) + max_lag_min * 60
    bb = _highpass(_bins(bed, start, end))
    wb = _highpass(_bins(wear, start, end))
    if len(bb) < 30 or len(wb) < 30:
        return None
    need = max(30, min(180, int(0.6 * min(len(bb), len(wb)))))
    # high-passed series are close to zero-mean, so a one-pass normalised dot product stands in for Pearson's r.
    # Loop over the shorter series and look up the longer one (a multi-day watch export against one bed night).
    swap = len(bb) < len(wb)
    big, small = (wb, bb) if swap else (bb, wb)
    lo_b = min(big)
    dense: list[float | None] = [None] * (max(big) - lo_b + 1)
    for k, v in big.items():
        dense[k - lo_b] = v
    wl = sorted(small.items())
    nb = len(dense)
    scores: list[tuple[int, float, int]] = []
    for lag in range(-max_lag_min, max_lag_min + 1):
        sxy = sxx = syy = 0.0
        n = 0
        off = (-lag if swap else lag) - lo_b
        for k, x in wl:
            j = k + off
            if 0 <= j < nb:
                y = dense[j]
                if y is not None:
                    sxy += x * y
                    sxx += x * x
                    syy += y * y
                    n += 1
        if n >= need and sxx > 0 and syy > 0:
            scores.append((lag, sxy / math.sqrt(sxx * syy), n))
    if not scores:
        return None
    best = max(scores, key=lambda s: s[1])
    alt = min((s for s in scores if s[1] >= best[1] - 0.02), key=lambda s: abs(s[0]))
    pick = alt if abs(best[0]) - abs(alt[0]) >= 30 else best
    far = [s[1] for s in scores if abs(s[0] - pick[0]) > 20 and abs(s[0] - best[0]) > 20]
    runner = max(far) if far else None
    distinct = runner is None or pick[1] - runner >= 0.1
    return {"lag": pick[0], "r": pick[1], "overlap": pick[2], "runner_up": runner, "distinct": distinct}


def best_lag(wear: list[tuple[float, float]], bed: list[tuple[float, float]], max_lag_min: int = 13 * 60) -> tuple[int, float, int] | None:
    """(lag, r, overlap) for a clear match; an ambiguous one (no clear peak) is reported with r capped at 0.5 so
    callers that need a confident match (r >= 0.6) leave the data alone."""
    d = lag_detail(wear, bed, max_lag_min)
    if not d:
        return None
    r = d["r"] if d["distinct"] else min(d["r"], 0.5)
    return d["lag"], r, d["overlap"]


def coverage(pts: list[tuple[float, float]], in_bed: list[tuple[float, float]]) -> float | None:
    """Share of in-bed minutes that have at least one sample within ±5 min."""
    if not in_bed:
        return None
    mins = 0
    hit = 0
    j = 0
    for a, b in in_bed:
        t = a
        while t < b:
            mins += 1
            while j < len(pts) and pts[j][0] < t - 300:
                j += 1
            if j < len(pts) and abs(pts[j][0] - t) <= 300:
                hit += 1
            t += 60
    return round(hit / mins, 2) if mins else None


def in_bed_spans(rows: list[dict], gap_s: float = 600) -> list[tuple[float, float]]:
    spans: list[list[float]] = []
    for r in rows:
        if r.get("occupied") == 1:
            if spans and r["ts"] - spans[-1][1] <= gap_s:
                spans[-1][1] = r["ts"]
            else:
                spans.append([r["ts"], r["ts"]])
    return [(a, b) for a, b in spans if b - a >= 600]


def describe_lag(lag_min: int) -> str:
    h, m = divmod(abs(lag_min), 60)
    if m == 0 and h:
        return f"exactly {h} h {'behind' if lag_min > 0 else 'ahead'}: looks like a time-zone or daylight-saving mistake in the export"
    return f"{abs(lag_min)} min {'behind' if lag_min > 0 else 'ahead'} of the bed"
