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
second apart) counts once, and so does a copying app's copy of another app's reading up to half a minute apart
(COPY_S). Disabled or deleted sources are never read.
"""

from __future__ import annotations

import bisect
import statistics
from datetime import datetime

RELEARN_NIGHTS = 3                        # nights of its own before a new device's level is trusted
ROLES = {"skin_temp": "skin_temp", "hr": "wearable_hr"}
MIN_PER_NIGHT = {"skin_temp": 5, "hr": 10}
DUP_S = 2.0                               # same value within this many seconds under another name = the same reading
# Apps that copy another app's readings into Health Connect (Health Sync copying Samsung Health). A copying app's reading
# with the same value as another app's within COPY_S is the same reading, and the original app's copy is the one kept
# (the phone's HealthMerge.dropCopies uses the same half minute). Two real devices (a watch and a ring worn together)
# agree on a value within half a minute all the time, so between those only DUP_S applies.
COPY_S = 30.0
COPY_APPS = {"health sync", "healthsync", "sleep"}   # the phone's names for nl.appyhapps.healthsync, com.healthsync, com.urbandroid.sleep
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


def is_copier(name: str) -> bool:
    """Is this device one of the copying apps (COPY_APPS)? Takes a device name or a sample source key."""
    return (name or "").rsplit(":", 1)[-1].strip().lower() in COPY_APPS


def same_reading_s(a: str, b: str) -> float:
    """How far apart the same value from these two different devices may be and still be one reading."""
    return COPY_S if is_copier(a) or is_copier(b) else DUP_S


FLAT_KEY = "wear_flat:{pid}"               # stretches of imported readings that hardly changed: probably not worn


def mark_flat(store, pid: str, src: str, a: float, b: float, now: float) -> None:
    """Mark a stretch of one device's readings as probably not worn (a watch left on the nightstand). The readings stay
    stored and visible; they are only left out of what Autopilot learns from and of the bed comparison."""
    key = FLAT_KEY.format(pid=pid)
    spans = [x for x in (store.get(key) or []) if isinstance(x, dict)]
    spans.append({"src": src, "a": float(a), "b": float(b), "ts": now})
    store.put(key, spans[-500:])


def flat_spans(store, pid: str) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list] = {}
    for x in store.get(FLAT_KEY.format(pid=pid)) or []:
        if isinstance(x, dict) and x.get("src") and x.get("a") is not None and x.get("b") is not None:
            out.setdefault(x["src"], []).append((float(x["a"]) - 1, float(x["b"]) + 1))
    return out


def not_worn_devices(c, p, since: float, until: float) -> list[str]:
    """Names of devices with a stretch marked as probably not worn inside this time."""
    return sorted({device_name(c, src) for src, spans in flat_spans(c.store, p.id).items()
                   if any(a <= until and b >= since for a, b in spans)})


def device_series(c, p, metric: str, since: float, until: float, worn_only: bool = True) -> dict[str, list[tuple[float, float]]]:
    """Each of this person's devices for one metric, raw. Keys: "src:<source id>" or the stored sample source.
    worn_only: leave out stretches marked as probably not worn (mark_flat)."""
    out: dict[str, list] = {}
    mgr = c.sources
    for cfg in (mgr.configs if mgr else []):
        if cfg.role == ROLES[metric] and cfg.enabled and (cfg.person == p.id or (cfg.person is None and cfg.zone == p.zone)):
            out[f"src:{cfg.id}"] = c.store.samples(cfg.id, since, until)
    flat = flat_spans(c.store, p.id) if worn_only else {}
    for src in c.store.sample_sources(f"wear:{p.id}:{metric}:"):
        pts = c.store.samples(src, since, until)
        if src in flat:
            pts = [(t, v) for t, v in pts if not any(a <= t <= b for a, b in flat[src])]
        out[src] = pts
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
    """Drop readings that another (bigger) series already has: same value within DUP_S seconds, or within COPY_S when
    one of the two is a copying app. Original apps come first, so it's always the copy that is dropped."""
    seen: list[tuple[float, float, bool]] = []
    out = {}
    for k in sorted(series, key=lambda k: (is_copier(k), -len(series[k]))):
        have = sorted(seen)
        mine = is_copier(k)
        keep = []
        for t, v in series[k]:
            i = bisect.bisect_left(have, (t - COPY_S, -1e9, False))
            dup = False
            while i < len(have) and have[i][0] <= t + COPY_S:
                ht, hv, hc = have[i]
                if round(hv, 1) == round(v, 1) and abs(ht - t) <= (COPY_S if (mine or hc) else DUP_S):
                    dup = True
                    break
                i += 1
            if not dup:
                keep.append((t, v))
        out[k] = keep
        seen += [(t, v, mine) for t, v in keep]
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
        for k, pts in device_series(c, p, metric, now - 14 * 86400, now, worn_only=False).items():
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
        for k, pts in device_series(c, p, metric, start, end, worn_only=False).items():
            name = device_name(c, k)
            out.setdefault(name, {"name": name, "kind": device_kind(name), "what": []})["what"].append(metric)
    for src, d in c.store.get_stages(p.id, start, end).items():
        if src == "orion":
            continue
        name = (d.get("label") or src).split(" · ")[0]
        out.setdefault(name, {"name": name, "kind": device_kind(name), "what": []})["what"].append("stages")
    return sorted(out.values(), key=lambda d: d["name"])


def night_coverage(c, p, now: float, days: int = 7) -> list[dict]:
    """For each recent night with the person in bed: how much of the time in bed the watch or ring covered with its
    own heart rate (per device), whether its sleep stages arrived, and a plain sentence about what is still missing.
    The phone's Health Connect card shows it after a sync, so a gap is explained instead of silently left."""
    from datetime import timedelta
    from .. import align as A
    from .summary import night_window, summarize
    tz = c.clock.tz
    today = datetime.fromtimestamp(now, tz)
    d0 = (today - timedelta(days=1)).date() if today.hour < 12 else today.date()
    out = []
    for i in range(days):
        d = d0 - timedelta(days=i)
        win = night_window(p, d, tz, now)
        s = summarize(c.store, p, d, tz, now) if win else None
        if not win or not s or not s.get("has_data") or not s.get("in_bed_at") or not s.get("out_at"):
            continue
        a, b = float(s["in_bed_at"]), float(s["out_at"])
        span = [(a, b)]
        hr: dict[str, float] = {}
        for src in c.store.sample_sources(f"wear:{p.id}:hr:"):
            pts = c.store.samples(src, a - 300, b + 300)
            if pts:
                hr[device_name(c, src)] = A.coverage(pts, span) or 0.0
        live = hr.pop("Watch (live)", None)
        stages = [x for k, x in c.store.get_stages(p.id, a - 2 * 3600, b + 2 * 3600).items() if k != "orion"]
        best = max(hr.values(), default=0.0)
        old = now - b > 48 * 3600                # older than the phone's automatic look-back: only a re-import reaches it
        later = ("If your watch recorded it, Re-import last 7 days fetches it again." if old
                 else "It's picked up on a later sync once it reaches Health Connect.")
        if stages and best >= 0.8:
            text = "Sleep stages and heart rate from your watch or ring for the whole night."
        elif stages:
            text = f"Sleep stages from your watch or ring. Its heart rate covers {round(best * 100)}% of the night. {later}"
        elif best >= 0.8:
            text = f"Heart rate for the whole night, but no sleep stages from your watch or ring yet, so the bed's estimate is shown. {later}"
        elif best > 0:
            text = f"Heart rate covers {round(best * 100)}% of the night and there are no sleep stages yet. {later}"
        else:
            text = f"Nothing from your watch or ring through Health Connect for this night. {later}"
        if live is not None and best < 0.8:
            text += f" The watch's live heart rate covers {round(live * 100)}%."
        nw = not_worn_devices(c, p, a, b)
        if nw:
            text += (f" The heart rate from {' and '.join(nw)} hardly changed, so it was probably not being worn; "
                     "those readings are kept but not used to learn your nights.")
        out.append({"date": d.isoformat(), "in_bed_at": a, "out_at": b, "in_progress": bool(s.get("in_progress")),
                    "hr": {k: round(v, 2) for k, v in sorted(hr.items())}, "live": None if live is None else round(live, 2),
                    "stages": [x.get("label") or x["source"] for x in stages], "text": text, "not_worn": nw})
    return out
