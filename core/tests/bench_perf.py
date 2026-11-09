"""Performance benchmark (not collected by pytest). Seeds a store with ~60 nights of realistic data for two
people and times the hot paths: controller ticks, snapshot, explore, nights, insights, autopilot status.

    cd core && .venv/Scripts/python.exe tests/bench_perf.py [data_dir]

Re-uses the data dir if it already holds a seeded store (delete it to reseed).
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from embertide.seed import default_household  # noqa: E402
from embertide.sources import default_sim_sources  # noqa: E402
from embertide.store import Store  # noqa: E402

TZ = ZoneInfo("America/New_York")
NIGHTS = 60


def seed(db: Path) -> None:
    st = Store(db)
    if st.get("bench_seeded"):
        return
    rnd = random.Random(1)
    hh = default_household()
    st.put("household", hh.model_dump())
    st.put("sources", [c.model_dump() for c in default_sim_sources()])
    today = datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0)
    t0 = (today - timedelta(days=NIGHTS)).timestamp()
    t1 = today.timestamp() - 3600
    tel, smp, evs = [], [], []
    for p in hh.people:
        zone = p.zone
        for day in range(NIGHTS):
            noon = t0 + day * 86400
            inb = noon + 10.5 * 3600 + rnd.uniform(-1800, 1800)      # ~22:30
            out = noon + 18.5 * 3600 + rnd.uniform(-1800, 1800)      # ~06:30
            evs.append((inb + 180, "rhythm_started", p.id, "engine", json.dumps({"in_bed": inb})))
            if rnd.random() < 0.3:
                evs.append((inb + 7200, "adjust", p.id, "app", json.dumps({"delta_c": -0.56, "phase": "deep"})))
            for k in range(6):
                evs.append((inb + 3600 + k * 1800, "autopilot", p.id, "autopilot",
                            json.dumps({"direction": "cooler", "status": "blocked", "phase": "deep", "delta_c": -0.56, "reasons": [], "score": 1.1})))
            t = noon
            while t < noon + 86400 and t < t1:
                occ = inb <= t <= out
                hr = int(55 + 6 * math.sin(t / 2000) + rnd.uniform(-2, 2)) if occ else None
                tel.append((t, zone, 1, 18.0, 18.0 + rnd.uniform(-0.3, 0.3), int(occ), hr, 14 if occ else None,
                            (4 if rnd.random() < 0.08 else 0) if occ else 5, 18.0 if occ else None, "deep" if occ else None, int(occ), p.id))
                if occ and int(t) % 300 < 15:
                    for sig in ("score", "restless", "hr_rise", "skin_delta", "pad_over"):
                        smp.append((t, f"ap:{p.id}:{sig}", rnd.uniform(-1, 1)))
                t += 15
            if p.id == "p1":                                          # a watch import per night
                t = inb
                while t < out:
                    smp.append((t, "wear:p1:hr:Galaxy Watch", 54 + rnd.uniform(-3, 3)))
                    t += 60
    # device samples every 15 s, all day, for every default sim source
    for c in default_sim_sources():
        t = t0
        while t < t1:
            smp.append((t, c.id, rnd.uniform(0, 1) if c.role in ("presence",) else 20 + rnd.uniform(-1, 1)))
            t += 15
    st.telemetry(tel)
    st.samples_many(smp)
    with st._lock, st._db:
        st._db.executemany("INSERT INTO events(ts,kind,person,source,data) VALUES(?,?,?,?,?)", evs)
    st.put("bench_seeded", True)
    print(f"seeded: telemetry {len(tel)}, samples {len(smp)}, events {len(evs)}")


def timed(label, fn, n=3):
    best, size = 1e9, None
    for _ in range(n):
        a = time.perf_counter()
        r = fn()
        best = min(best, time.perf_counter() - a)
        size = r
    extra = f"  {size / 1024:.0f} KB" if isinstance(size, int) else ""
    print(f"{label:<44} {best * 1000:8.1f} ms{extra}")
    return best


def main(data_dir: str) -> None:
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    seed(d / "embertide.db")
    from fastapi.testclient import TestClient

    from embertide.app import create_app
    from embertide.config import Config
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.auth = d, "America/New_York", "sim", d / "noweb", False
    with TestClient(create_app(cfg)) as c:
        ctl = c.app.state.notifier.c
        yday = (datetime.now(TZ) - timedelta(days=1)).date().isoformat()

        def get(path):
            r = c.get(path)
            assert r.status_code == 200, (path, r.status_code, r.text[:200])
            return len(r.content)
        timed("GET /api/snapshot", lambda: get("/api/snapshot"))
        timed("snapshot() + json", lambda: len(json.dumps(ctl.snapshot())))
        timed("GET /api/explore (yesterday)", lambda: get(f"/api/explore?person_id=p1&date={yday}"))
        timed("GET /api/people/p1/nights", lambda: get("/api/people/p1/nights"))
        timed("GET /api/people/p1/night", lambda: get(f"/api/people/p1/night?date={yday}") if c.get(f"/api/people/p1/night?date={yday}").status_code == 200 else 0)
        timed("GET /api/people/p1/insights", lambda: get("/api/people/p1/insights"))
        timed("GET /api/people/p1/autopilot", lambda: get("/api/people/p1/autopilot"))
        timed("GET /api/people/p1/autopilot/night", lambda: get(f"/api/people/p1/autopilot/night?date={yday}"))
        timed("GET /api/people/p1/autopilot/suggestions", lambda: get("/api/people/p1/autopilot/suggestions?all=true"))
        timed("GET /api/people/p1/autopilot/trends", lambda: get("/api/people/p1/autopilot/trends"))
        p = ctl.household.people[0]
        now = ctl.clock.now_ts()
        timed("autopilot.wear_hr + skin (1 tick's reads)", lambda: (ctl.autopilot.wear_hr(p, now - 6 * 3600, now), ctl.autopilot.skin(p, now - 6 * 3600, now)) and 0)
        timed("store.sample_sources('wear:p1:hr:')", lambda: len(ctl.store.sample_sources("wear:p1:hr:")) and 0)

        async def ticks(n=60):
            for _ in range(n):
                await ctl.tick()
                ctl.autopilot._last = {}                         # force the autopilot to evaluate every tick
        loop = asyncio.new_event_loop()
        a = time.perf_counter()
        loop.run_until_complete(ticks())
        loop.close()
        print(f"{'controller tick (incl. autopilot), avg':<44} {(time.perf_counter() - a) / 60 * 1000:8.1f} ms")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "AppData/Local/Temp/et-bench"))
