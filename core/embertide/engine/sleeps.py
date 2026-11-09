"""Every sleep a person's watch or ring recorded, nights and naps alike, so the app can show the whole day's sleep.

A wearable connection (Oura, and later WHOOP / Polar / Apple Health / Health Connect) hands over its sleep periods;
each is kept as one record in kv `sleeps:<pid>` (newest 120 days), keyed by the provider's own id so a re-sync
replaces rather than duplicates:

    {"id": "oura:<id>", "source": "oura", "device": "Oura", "kind": "night" | "nap", "start", "end",
     "asleep_min", "deep_min", "rem_min", "light_min", "awake_min", "efficiency", "latency_min",
     "hr_avg", "hr_low", "hrv_avg", "breath", "segments": [...] (naps only; nights' stages live in the stages table),
     "where": "bed" | "away" | "unknown", "where_why": "..."}

Where a nap happened is worked out from the bed itself: if the person's side was occupied for most of the nap and
the bed's heart rate there matched the wearable's, it was on the bed; if the side was empty, or something else was on
it (heart rate far from the wearable's, e.g. the dog while they napped on the couch), it was away. A nap on the bed
also answers an open "was that the pet?" question about the same stretch: it was the person (engine/petq.py).
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

KEEP_DAYS = 120


def _key(pid: str) -> str:
    return f"sleeps:{pid}"


def all_for(store, pid: str) -> list[dict]:
    return store.get(_key(pid)) or []


def save(c, p, periods: list[dict], now: float) -> int:
    """Merge a provider's periods into the person's list (same id replaces), place naps, settle pet questions."""
    if not periods:
        return 0
    store = c.store
    have = {x["id"]: x for x in all_for(store, p.id)}
    new = 0
    for x in periods:
        if x["kind"] == "nap":
            x.update(place(c, p, x))
        if x["id"] not in have:
            new += 1
        have[x["id"]] = x
    cut = now - KEEP_DAYS * 86400
    keep = sorted((x for x in have.values() if x["end"] >= cut), key=lambda x: x["start"])
    store.put(_key(p.id), keep)
    for x in periods:
        if x["kind"] == "nap" and x.get("where") == "bed":
            _settle_pet_question(c, p, x, now)
    return new


def place(c, p, x: dict) -> dict:
    """On the bed or away, from the person's side of the bed during the nap."""
    rows = c.store.history(p.zone, x["start"], x["end"], step_s=30, person=p.id)
    if not rows:
        return {"where": "unknown", "where_why": "No bed readings for that time."}
    occ = sum(1 for r in rows if r.get("occupied") == 1) / len(rows)
    if occ < 0.3:
        return {"where": "away", "where_why": "Your side of the bed was empty."}
    bed_hr = [r["hr"] for r in rows if r.get("hr") and r.get("occupied") == 1]
    wear_hr = [v for src in c.store.sample_sources(f"wear:{p.id}:hr:") for _, v in c.store.samples(src, x["start"], x["end"])]
    if len(bed_hr) >= 6 and len(wear_hr) >= 3:
        gap = abs(statistics.median(bed_hr) - statistics.median(wear_hr))
        if gap > 10:
            return {"where": "away", "where_why": f"Something else was on your side (its heart rate was {gap:.0f} bpm off yours)."}
        if occ >= 0.5:
            return {"where": "bed", "where_why": "Your side was occupied and its heart rate matched your ring's."}
    if occ >= 0.6:
        return {"where": "bed", "where_why": "Your side was occupied the whole time."}
    return {"where": "unknown", "where_why": "Your side was occupied for only part of it."}


def _settle_pet_question(c, p, nap: dict, now: float) -> None:
    from . import petq
    petq.settle_from_fix(c, p.id, [{"start": nap["start"], "end": nap["end"], "who": "me"}], now)


def naps_before(store, pid: str, start: float, tz) -> list[dict]:
    """Naps on the day that ends with this night: from 6 AM that day until the night starts."""
    d = datetime.fromtimestamp(start - 6 * 3600, tz).date()
    a = datetime(d.year, d.month, d.day, 6, 0, tzinfo=tz).timestamp()
    return [x for x in all_for(store, pid) if x["kind"] == "nap" and a <= x["start"] < start - 600]


def view(x: dict, tz) -> dict:
    """A nap or night for the app: times, how long asleep, where, in plain words."""
    clk = lambda t: datetime.fromtimestamp(t, tz).strftime("%I:%M %p").lstrip("0")   # noqa: E731
    where = {"bed": "on the bed", "away": "away from the bed", "unknown": ""}.get(x.get("where") or "unknown", "")
    asleep = x.get("asleep_min")
    return {k: x.get(k) for k in ("id", "kind", "start", "end", "asleep_min", "deep_min", "rem_min", "light_min", "awake_min",
                                  "hr_avg", "hr_low", "hrv_avg", "breath", "where", "where_why", "device", "segments")} | {
        "when": f"{clk(x['start'])}–{clk(x['end'])}",
        "line": " · ".join(s for s in (f"{fmt_min(asleep)} asleep" if asleep else None, where) if s)}


def fmt_min(m: float | None) -> str:
    if not m:
        return "0 min"
    m = round(m)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def day_totals(store, pid: str, tz, days: int, now: float) -> list[dict]:
    """Per day (named by the evening's date of its night): night asleep, nap asleep, total, nap count."""
    out: dict[str, dict] = {}
    for x in all_for(store, pid):
        if x["end"] < now - (days + 1) * 86400:
            continue
        if x["kind"] == "night":
            d = (datetime.fromtimestamp(x["start"], tz) - timedelta(hours=12)).date()
        else:
            d = datetime.fromtimestamp(x["start"], tz).date()              # a nap counts for the evening that follows it
        e = out.setdefault(d.isoformat(), {"date": d.isoformat(), "night_min": 0, "nap_min": 0, "naps": 0})
        if x["kind"] == "night":
            e["night_min"] += x.get("asleep_min") or 0
        else:
            e["nap_min"] += x.get("asleep_min") or 0
            e["naps"] += 1
    for e in out.values():
        e["total_min"] = e["night_min"] + e["nap_min"]
    return [out[k] for k in sorted(out)][-days:]

