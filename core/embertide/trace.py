"""Night trace: a compact JSON line every 15 s with everything that decides who is in bed, for tuning on real nights.

Written to <data dir>/traces/trace-YYYY-MM-DD.jsonl (local date), kept 14 days. Holds per side: the bed pad's raw
reading, the presence filter's echo state, every fusion vote, pet suspicion, the night engine state and the bed's
setpoint, plus each person's latest phone context (charging, dials heard, watch heart rate when shared).
No names: people are listed by id and side. Turn off with ET_TRACE=off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("embertide.trace")
EVERY_S = 15.0
KEEP_DAYS = 14


def _r(v, n=1):
    return round(v, n) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def frame(ctl) -> dict:
    """One trace line from the live controller (pure read, never raises on missing parts)."""
    now = ctl.clock.now_ts()
    out: dict = {"ts": round(now, 1)}
    pf = getattr(ctl, "presence", None)
    if pf is not None:
        out["filter"] = {
            "follower": getattr(pf, "_follower", None), "echo_pair": getattr(pf, "_echo_pair", None),
            "streak": getattr(pf, "_together_streak", None),
            "weak": {k: len(v) for k, v in (getattr(pf, "_weak", {}) or {}).items()},
            "bins": len(getattr(pf, "_bins", []) or []),
        }
        out["pads"] = {z: {"raw": s.raw_occupied, "occ": s.occupied, "echo": s.echo, "hr": s.hr, "br": s.br,
                           "st": s.status, "stale": s.stale} for z, s in pf.sides.items()}
    out["fused"] = {z: {"present": f.get("present"), "score": f.get("score"),
                        "votes": [[v["signal"], v["weight"], _r(v["value"], 2)] for v in f.get("votes", [])],
                        **({"pet": {k: f["pet"].get(k) for k in ("suspected", "candidate", "with_person", "why", "pet")}}
                           if f.get("pet") else {}),
                        **({"crosstalk": f["crosstalk"]} if f.get("crosstalk") else {}),
                        **({k: f[k] for k in f if k.startswith("watch")})}
                    for z, f in (getattr(ctl, "fused", {}) or {}).items()}
    people = []
    from .engine import phone_context as PC
    for p in ctl.household.people:
        e = ctl.engines.get(p.id) if hasattr(ctl, "engines") else None
        st = getattr(e, "s", None)
        row = {"id": p.id, "zone": p.zone}
        if st is not None:
            row["night"] = {"state": getattr(st, "state", None), "phase": getattr(st, "phase_id", None),
                            "target": _r(getattr(st, "target_c", None), 2), "started": getattr(st, "started", None)}
        cur = PC.latest(ctl.store, p.id) or {}
        data = cur.get("data") or {}
        if data:
            row["phone"] = {k: data.get(k) for k in ("charging", "bedtime_mode", "dials", "home", "watch_hr",
                                                     "watch", "watch_connected") if k in data}
            row["phone_seen"] = round(cur.get("seen") or 0)
        people.append(row)
    out["people"] = people
    try:
        fr = ctl.driver.latest() if hasattr(ctl.driver, "latest") else None
        if fr is not None:
            out["bed"] = {z: {"on": zf.on, "set": _r(zf.setpoint_c), "meas": _r(zf.measured_c)} for z, zf in fr.zones.items()}
    except Exception:  # noqa: BLE001
        pass
    return out


async def run(ctl, data_dir: Path) -> None:
    if os.environ.get("ET_TRACE", "on").lower() in ("off", "0", "false"):
        return
    d = Path(data_dir) / "traces"
    d.mkdir(parents=True, exist_ok=True)
    last_prune = 0.0
    while True:
        try:
            line = json.dumps(frame(ctl), separators=(",", ":"), default=str)
            day = datetime.now().strftime("%Y-%m-%d")
            with open(d / f"trace-{day}.jsonl", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if time.time() - last_prune > 3600:
                last_prune = time.time()
                for f in d.glob("trace-*.jsonl"):
                    if time.time() - f.stat().st_mtime > KEEP_DAYS * 86400:
                        f.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            log.exception("trace line failed")
        await asyncio.sleep(EVERY_S)
