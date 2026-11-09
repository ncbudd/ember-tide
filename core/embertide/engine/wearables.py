"""Watches and rings coming and going: one level per device, and a short re-learn for a new one.

Devices read at different levels (one ring reads skin temperature 0.6 °C above a watch on the same wrist-night; a
ring's heart rate can sit a few bpm under a watch's). Autopilot's learned normal range is in absolute numbers, so a
new device would look like a sudden change in the sleeper. So, per metric (skin temperature, wearable heart rate):

- every device's level is learned from the learning nights (the median of its nightly medians);
- the REFERENCE device is the one with the most of those nights; the normal range lives on its level;
- another device is lined up with it by the nights they both reported (two at once: one night is enough), or, once
  it has RELEARN_NIGHTS nights of its own, by the difference between the two levels;
- a device that can't be lined up yet is "learning": its readings still count WITHIN a night (a change against
  tonight's settled value), but never against the normal range, and they don't teach the range either. If a known
  device reports in the same window, the new one is matched to it there and then.

The same physical reading stored under two names (the live watch path and a Health Connect import of that watch, a
second apart) counts once. Disabled or deleted sources are never read.
"""

from __future__ import annotations

import bisect
import statistics
from datetime import datetime

RELEARN_NIGHTS = 3                        # nights of its own before a new device's level is trusted
ROLES = {"skin_temp": "skin_temp", "hr": "wearable_hr"}
MIN_PER_NIGHT = {"skin_temp": 5, "hr": 10}
DUP_S = 2.0                               # same value within this many seconds under another name = the same reading
QUIET_S = 30 * 3600                       # nothing for this long: "hasn't sent anything since …"


def device_kind(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("oura", "ring", "ultrahuman", "circular", "rogbid")):
        return "ring"
    if "whoop" in n or "band" in n:
        return "band"
    if any(k in n for k in ("watch", "sm-l", "sm-r", "fitbit", "garmin", "galaxy", "pixel", "amazfit", "polar")):
        return "watch"
    return "device"


def device_series(c, p, metric: str, since: float, until: float) -> dict[str, list[tuple[float, float]]]:
    """Each of this person's devices for one metric, raw. Keys: "src:<source id>" or the stored sample source."""
    out: dict[str, list] = {}
    mgr = c.sources
    for cfg in (mgr.configs if mgr else []):
        if cfg.role == ROLES[metric] and cfg.enabled and (cfg.person == p.id or (cfg.person is None and cfg.zone == p.zone)):
            out[f"src:{cfg.id}"] = c.store.samples(cfg.id, since, until)
    for src in c.store.sample_sources(f"wear:{p.id}:{metric}:"):
        out[src] = c.store.samples(src, since, until)
    return {k: v for k, v in out.items() if v}


def device_name(c, key: str) -> str:
    if key.startswith("src:"):
        cfg = next((x for x in (c.sources.configs if c.sources else []) if x.id == key[4:]), None)
        return cfg.name if cfg else key[4:]
    name = key.split(":", 3)[-1]
    return "Watch (live)" if name == "watch_live" else name


def levels(c, p, nights: list[dict]) -> dict:
    """Per metric: each device's nights and level over these learning nights, the reference device and the shift
    that puts each lined-up device onto the reference's level."""
    out = {}
    for metric in ROLES:
        per: dict[str, dict[str, float]] = {}            # device → {night date: median}
        for n in nights:
            if not n.get("in_bed_at") or not n.get("out_at"):
                continue
            for k, pts in device_series(c, p, metric, n["in_bed_at"] + 1200, n["out_at"]).items():
                if len(pts) >= MIN_PER_NIGHT[metric]:
                    # median of 5-minute medians: a dense burst (one device logging every second for an hour)
                    # can't outvote the rest of the night
                    bins: dict[int, list[float]] = {}
                    for t, v in pts:
                        bins.setdefault(int(t // 300), []).append(v)
                    per.setdefault(k, {})[n.get("date") or str(n["in_bed_at"])] = statistics.median(statistics.median(x) for x in bins.values())
        devs = {k: {"nights": len(v), "level": round(statistics.median(v.values()), 2), "last": max(v)} for k, v in per.items()}
        ref = max(devs, key=lambda k: (devs[k]["nights"], devs[k]["last"])) if devs else None
        shifts: dict[str, float] = {}
        if ref and devs[ref]["nights"] >= RELEARN_NIGHTS:
            shifts[ref] = 0.0
            for k, d in devs.items():
                if k == ref:
                    continue
                both = sorted(set(per[k]) & set(per[ref]))
                if both:
                    shifts[k] = round(statistics.median(per[ref][x] - per[k][x] for x in both), 2)
                elif d["nights"] >= RELEARN_NIGHTS:
                    shifts[k] = round(devs[ref]["level"] - d["level"], 2)
        out[metric] = {"devices": devs, "ref": ref, "shifts": shifts}
    return out


def _dedupe(series: dict[str, list]) -> dict[str, list]:
    """Drop readings that another (bigger) series already has: same value within DUP_S seconds."""
    seen: list[tuple[float, float]] = []
    out = {}
    for k in sorted(series, key=lambda k: -len(series[k])):
        have = sorted(seen)
        keep = []
        for t, v in series[k]:
            i = bisect.bisect_left(have, (t - DUP_S, -1e9))
            dup = False
            while i < len(have) and have[i][0] <= t + DUP_S:
                if round(have[i][1], 1) == round(v, 1):
                    dup = True
                    break
                i += 1
            if not dup:
                keep.append((t, v))
        out[k] = keep
        seen += keep
    return {k: v for k, v in out.items() if v}


def merge(series: dict[str, list], lv: dict | None) -> tuple[list[tuple[float, float]], bool]:
    """One series on the reference level. Returns (points, unlearned): unlearned = the readings come only from devices
    whose level isn't known yet, so they mustn't be compared with the normal range."""
    series = _dedupe(series)
    if not series:
        return [], False
    shifts = (lv or {}).get("shifts") or {}
    known = {k: shifts[k] for k in series if k in shifts}
    unknown = [k for k in series if k not in shifts]
    placed = {k: [(t, v + known[k]) for t, v in series[k]] for k in known}
    unlearned = False
    had = bool(placed)
    if unknown:
        if had:
            anchor = statistics.median(v for _, v in max(placed.values(), key=len))
        else:
            anchor = statistics.median(v for _, v in max((series[k] for k in unknown), key=len))
            unlearned = bool((lv or {}).get("ref"))           # a learned range exists, on some other device's level
        for k in unknown:
            m = statistics.median(v for _, v in series[k])
            placed[k] = [(t, v - m + anchor) for t, v in series[k]] if (had or len(unknown) > 1) else list(series[k])
    return sorted({(round(t), round(v, 3)) for x in placed.values() for t, v in x}), unlearned


def status(c, p, lv: dict, now: float, learning_done: bool) -> list[dict]:
    """Each watch or ring seen in the last 14 days: learning / lined up / gone quiet, with a plain sentence."""
    out: dict[str, dict] = {}
    for metric in ROLES:
        m = lv.get(metric) or {}
        for k, pts in device_series(c, p, metric, now - 14 * 86400, now).items():
            name = device_name(c, k)
            d = out.setdefault(name, {"name": name, "kind": device_kind(name), "metrics": [], "last": 0.0,
                                      "nights": 0, "learning": False, "shift": {}})
            d["metrics"].append(metric)
            d["last"] = max(d["last"], pts[-1][0])
            d["nights"] = max(d["nights"], (m.get("devices", {}).get(k) or {}).get("nights", 0))
            if k in (m.get("shifts") or {}):
                if m["shifts"][k]:
                    d["shift"][metric] = m["shifts"][k]
            elif learning_done or m.get("ref"):
                d["learning"] = True
    tz = c.clock.tz
    res = []
    for d in out.values():
        d["quiet"] = now - d["last"] > QUIET_S
        if d["quiet"]:
            d["learning"] = False
            d["text"] = f"{d['name']} hasn't sent anything since {datetime.fromtimestamp(d['last'], tz).strftime('%A')}"
        elif d["learning"]:
            left = max(1, RELEARN_NIGHTS - d["nights"])
            d["nights_left"] = left
            d["text"] = f"New {d['kind']} ({d['name']}): learning its readings for {left} more night{'s' if left != 1 else ''}"
        else:
            d["text"] = f"{d['name']}: lined up with your other readings" if d["shift"] else f"{d['name']}: in use"
        res.append(d)
    return sorted(res, key=lambda d: (d["quiet"], d["name"]))


def night_devices(c, p, start: float, end: float) -> list[dict]:
    """Which watch or ring a night's readings came from (heart rate, skin temperature, sleep stages)."""
    out: dict[str, dict] = {}
    for metric in ROLES:
        for k, pts in device_series(c, p, metric, start, end).items():
            name = device_name(c, k)
            out.setdefault(name, {"name": name, "kind": device_kind(name), "what": []})["what"].append(metric)
    for src, d in c.store.get_stages(p.id, start, end).items():
        if src == "orion":
            continue
        name = (d.get("label") or src).split(" · ")[0]
        out.setdefault(name, {"name": name, "kind": device_kind(name), "what": []})["what"].append("stages")
    return sorted(out.values(), key=lambda d: d["name"])
