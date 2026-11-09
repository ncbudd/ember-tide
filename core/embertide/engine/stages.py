"""Sleep stages from every available source, one timeline per source.

- "estimate": computed here from the bed's own heart rate, breathing and movement (5-minute epochs). An estimate,
  labeled as such: deep = still and heart rate near the night's low, awake = moving or heart rate well above
  the night's median, REM = still but heart rate restless and above baseline (after the first hour), light = the rest.
- imported sources (watch via Health Connect, Oura, Orion cloud, anything that POSTs segments) are stored as-is.
"""

from __future__ import annotations

from statistics import median, pstdev

STAGES = ("awake", "rem", "light", "deep")
CODE = {s: i for i, s in enumerate(STAGES)}


def estimate(rows: list[dict]) -> list[dict]:
    occ = [r for r in rows if r.get("occupied") == 1 and r.get("hr")]
    if len(occ) < 6:
        return []
    hrs = sorted(r["hr"] for r in occ)
    low, med = hrs[max(0, len(hrs) // 10)], median(hrs)
    start = occ[0]["ts"]
    epochs: dict[int, list[dict]] = {}
    for r in occ:
        epochs.setdefault(int((r["ts"] - start) // 300), []).append(r)
    segs: list[dict] = []
    for k in sorted(epochs):
        e = epochs[k]
        hr = sum(x["hr"] for x in e) / len(e)
        jitter = pstdev([x["hr"] for x in e]) if len(e) > 1 else 0.0
        moving = sum(1 for x in e if x.get("status") in (4, 6, 7)) / len(e)
        if moving >= 0.25 or hr >= med + 6:
            st = "awake"
        elif hr <= low + 2 and moving == 0:
            st = "deep"
        elif k >= 12 and hr >= med + 1.5 and jitter >= 1.4:
            st = "rem"
        else:
            st = "light"
        a = start + k * 300
        if segs and segs[-1]["stage"] == st and abs(segs[-1]["end"] - a) < 1:
            segs[-1]["end"] = a + 300
        else:
            segs.append({"start": a, "end": a + 300, "stage": st})
    return segs


def totals(segs: list[dict]) -> dict[str, int]:
    sec = {s: 0.0 for s in STAGES}
    for g in segs:
        sec[g["stage"]] = sec.get(g["stage"], 0.0) + (g["end"] - g["start"])
    return {k: round(v / 60) for k, v in sec.items()}


def agreement(a: list[dict], b: list[dict]) -> float | None:
    """Share of minutes where two stage timelines say the same thing (only where both have data)."""
    if not a or not b:
        return None
    t0, t1 = max(a[0]["start"], b[0]["start"]), min(a[-1]["end"], b[-1]["end"])
    same = total = 0
    t = t0
    ia = ib = 0
    while t < t1:
        while ia < len(a) and a[ia]["end"] <= t:
            ia += 1
        while ib < len(b) and b[ib]["end"] <= t:
            ib += 1
        if ia < len(a) and ib < len(b) and a[ia]["start"] <= t and b[ib]["start"] <= t:
            total += 1
            same += a[ia]["stage"] == b[ib]["stage"]
        t += 60
    return round(same / total, 3) if total else None


def normalize_stage(v) -> str | None:
    """Map whatever a device calls a stage onto awake / rem / light / deep."""
    if v is None:
        return None
    s = str(v).strip().lower()
    for key, st in (("deep", "deep"), ("rem", "rem"), ("light", "light"), ("core", "light"), ("awake", "awake"), ("wake", "awake"), ("out", "awake")):
        if key in s:
            return st
    try:   # Health Connect: 1 awake, 2 sleeping, 3 out of bed, 4 light, 5 deep, 6 REM, 7 awake in bed
        return {1: "awake", 2: "light", 3: "awake", 4: "light", 5: "deep", 6: "rem", 7: "awake"}.get(int(float(s)))
    except ValueError:
        return None


def parse_orion_hypnogram(hyp) -> list[dict]:
    """Best-effort parser for Orion's sleep_summary.hypnogram. The exact shape isn't documented publicly,
    so several plausible layouts are accepted. Unverified against a real account."""
    out: list[dict] = []
    if isinstance(hyp, dict):
        hyp = hyp.get("values") or hyp.get("data") or hyp.get("stages") or []
    if not isinstance(hyp, list):
        return out
    for i, it in enumerate(hyp):
        start = end = stage = None
        if isinstance(it, dict):
            start = it.get("start") or it.get("start_time") or it.get("timestamp") or it.get("ts")
            end = it.get("end") or it.get("end_time")
            stage = normalize_stage(it.get("stage") or it.get("value") or it.get("type"))
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            start, stage = it[0], normalize_stage(it[1])
        if isinstance(start, str):
            from datetime import datetime
            try:
                start = datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
            except ValueError:
                start = None
        if isinstance(end, str):
            from datetime import datetime
            try:
                end = datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()
            except ValueError:
                end = None
        if start is None or stage is None:
            continue
        start = float(start) / (1000 if float(start) > 1e11 else 1)
        if end is not None:
            end = float(end) / (1000 if float(end) > 1e11 else 1)
        out.append({"start": start, "end": end, "stage": stage})
    for i, g in enumerate(out):
        if g["end"] is None:
            g["end"] = out[i + 1]["start"] if i + 1 < len(out) else g["start"] + 300
    return [g for g in out if g["end"] > g["start"]]


def collect(store, person, start: float, end: float) -> list[dict]:
    """All stage timelines for a person's night: imported/device sources first, the bed estimate last."""
    rows = store.history(person.zone, start, end, person=person.id)
    out = []
    for src, d in store.get_stages(person.id, start, end).items():
        kind = "orion" if src == "orion" else "wearable"
        out.append({"id": src, "label": d["label"] or src, "kind": kind, "segments": d["segments"], "totals": totals(d["segments"])})
    est = estimate(rows)
    if est:
        out.append({"id": "estimate", "label": "Ember & Tide estimate · bed heart rate + movement", "kind": "estimate", "segments": est, "totals": totals(est)})
    rank = {"wearable": 0, "orion": 1, "estimate": 2}
    out.sort(key=lambda x: rank[x["kind"]])
    ref = out[0]["segments"] if out else []
    for x in out[1:]:
        x["agree_with_first"] = agreement(ref, x["segments"])
    return out
