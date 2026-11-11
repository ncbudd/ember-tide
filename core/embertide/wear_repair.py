"""One-off repair: undo clock shifts that earlier versions applied to a watch's heart rate on too little evidence.

Before the stricter rule in align.judge, a short Health Connect sync (an hour or so of readings) could "line up" with
the wrong part of a bed night and be shifted by minutes or hours. Those readings are now stored at the wrong times.
Their original times can't be told apart from the device's genuine readings in the same stretch, so the repair
forgets that device's readings for the affected nights; the phone's "Re-import last 7 days" then sends them again,
and the import keeps them at the phone's time.

Idempotent: each repaired import is remembered (kv `wear_repair:<person>:<import time>`), so running it again does
nothing, even after the re-import has filled the nights back in. Only nights the phone can still re-import (the last
`REIMPORT_DAYS` days) are touched; older ones are listed and left alone.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from . import align as A

REIMPORT_DAYS = 7


def _weak(entry: dict) -> bool:
    """Was this import's measured shift taken from too little? (The import log keeps readings and coverage per night;
    fewer readings than the overlap a shift now needs, or under a third of the time in bed, can't prove a shift.)"""
    lag = round(entry["shift_applied_s"] / 60)
    voters = [n for n in entry.get("nights") or [] if n.get("lag_min") is not None and abs(n["lag_min"] - lag) <= 3]
    if not voters:
        return True
    return all((n.get("samples") or 0) < A.SHIFT_MIN_OVERLAP or (n.get("coverage_in_bed") or 0) < 0.2 for n in voters)


def find(store, pid: str) -> list[dict]:
    """Imports whose heart rate was shifted by a measurement (not a manual or learned correction) on weak evidence."""
    out = []
    for e in store.get(f"imports:{pid}", []) or []:
        if e.get("metric") != "hr" or not e.get("shift_applied_s") or not e.get("stored"):
            continue
        if not any(str(n).startswith("Shifted by") for n in e.get("notes") or []):
            continue
        if _weak(e):
            out.append(e)
    return out


def repair(store, pid: str, tz, now: float, apply: bool = False) -> dict:
    """List (apply=False) or carry out the repair. Returns {"items": [...], "text": plain summary}."""
    items = []
    cutoff = (datetime.fromtimestamp(now, tz) - timedelta(days=REIMPORT_DAYS)).date()
    for e in find(store, pid):
        key = f"wear_repair:{pid}:{int(e['ts'])}"
        device = e["device"]
        src = f"wear:{pid}:hr:{device}"
        per_night = {n["date"]: n for n in e.get("nights") or [] if n.get("date")}
        nights = sorted(per_night)
        item = {"import_ts": e["ts"], "device": device, "shift_min": round(e["shift_applied_s"] / 60), "nights": nights,
                "done": bool(store.get(key)), "deleted": 0, "too_old": [d for d in nights if date.fromisoformat(d) < cutoff]}
        if apply and not item["done"]:
            for d in nights:
                if d in item["too_old"]:
                    continue
                n = per_night[d]
                if n.get("first") is not None and n.get("last") is not None:
                    a, b = float(n["first"]) - 1, float(n["last"]) + 1               # newer logs keep the stretch it landed on
                else:
                    dd = date.fromisoformat(d)
                    a = datetime.combine(dd, time(12), tz).timestamp()             # otherwise the whole night, noon to noon
                    b = datetime.combine(dd + timedelta(days=1), time(12), tz).timestamp()
                item["deleted"] += store.delete_samples_between(src, a, b)
            off = store.get(f"device_offset:{pid}:{device}")
            if off and off.get("shift_s") and abs(float(off.get("ts", 0)) - float(e["ts"])) < 120:
                store.put(f"device_offset:{pid}:{device}", {"shift_s": 0.0, "ts": now, "nights": 0, "repaired": True})
            store.put(key, {"ts": now, "deleted": item["deleted"], "nights": nights})
            item["done"] = True
        items.append(item)
    todo = [i for i in items if not i["done"]]
    if not items:
        text = "Nothing to repair: no heart rate was shifted on too little evidence."
    elif apply:
        text = ("Removed the wrongly shifted heart rate for " + ", ".join(sorted({d for i in items for d in i["nights"]}))
                + ". Next, on the phone: Settings → Watch or ring → Re-import last 7 days.")
    else:
        text = f"{len(todo)} import(s) to repair" + (f" ({len(items) - len(todo)} already done)" if len(items) > len(todo) else "") + "."
    return {"items": items, "text": text, "applied": apply}
