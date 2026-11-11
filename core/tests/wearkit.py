"""Shared pieces for the wearable and cloud simulation scenarios (test_sim_wear_*.py).

A `World` is the real app on the simulator at a fixed point in time. Bed nights are written straight into the
bed's history (what the controller would have recorded), then the scenarios post watch data through the real
import routes and read back what the phone would show: the import log, the nights list, the night view, the
comparison and the per-night watch coverage.
"""

from __future__ import annotations

import math
import random
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from embertide import align as A
from embertide.app import create_app
from embertide.config import Config

TZ = ZoneInfo("America/New_York")
H = {"X-Embertide": "1"}


def t(s: str) -> float:
    return A.parse_time(s, TZ)


def night_hr(start: float, n: int = 1800, seed: int = 3, step: float = 15.0) -> list[tuple[float, float]]:
    """A believable night of heart rate: a slow random walk with sleep-cycle swings."""
    rnd = random.Random(seed)
    v, out = 62.0, []
    for i in range(n):
        v += rnd.gauss(0, 0.6) - (v - 58) * 0.02
        out.append((start + i * step, v + 3 * math.sin(i / 360)))
    return out


class World:
    def __init__(self, c: TestClient) -> None:
        self.c = c
        self.ctl = c.app.state.notifier.c
        self.store = self.ctl.store

    # ---- time and bed ----
    def jump(self, iso: str) -> None:
        self.c.post("/api/sim", json={"jump_to": iso}, headers=H)

    def bed(self, start_iso: str, n: int = 1800, seed: int = 3, zone: str = "left", person: str = "p1", step: float = 15.0,
            gaps: list[tuple[int, int]] | None = None) -> list[tuple[float, int]]:
        """The bed's own record of a night: someone in bed, heart rate every `step` s. `gaps` = sample index ranges
        where the bed saw nobody (got up). Returns [(t, hr)] for every sample."""
        start = t(start_iso)
        bed = [(ts_, round(v)) for ts_, v in night_hr(start, n=n, seed=seed, step=step)]
        gone = set()
        for a, b in gaps or []:
            gone.update(range(a, b))
        self.store.telemetry([(ts_, zone, 1, 18.0, 18.2, 0 if i in gone else 1, None if i in gone else hr, 14, 0, 18.0, "deep",
                               0 if i in gone else 1, person) for i, (ts_, hr) in enumerate(bed)])
        return bed

    def empty_bed(self, start_iso: str, hours: float, zone: str = "left", person: str = "p1") -> None:
        s = t(start_iso)
        self.store.telemetry([(s + i * 60, zone, 1, 18.0, 18.2, 0, None, None, 0, 18.0, "deep", 0, person) for i in range(int(hours * 60))])

    # ---- the import routes ----
    def post(self, device: str, metric: str, points, pid: str = "p1", **kw) -> dict:
        r = self.c.post(f"/api/people/{pid}/wearable", json={"device": device, "metric": metric, "points": points, **kw}, headers=H)
        assert r.status_code == 200, (r.status_code, r.text[:300])
        return r.json()

    def hr(self, device: str, pts, **kw) -> dict:
        return self.post(device, "hr", [[a, b] for a, b in pts], **kw)

    def stages(self, segs, source: str = "health-connect", label: str = "Pixel Watch · Health Connect", pid: str = "p1"):
        return self.c.post(f"/api/people/{pid}/stages", json={"source": source, "label": label, "segments": segs}, headers=H)

    def live(self, pts, pid: str = "p1") -> None:
        """Our Wear app's live heart rate, straight into the live watch path (the route rate-limits and only keeps the last
        two hours, so scenarios use the engine function the route calls)."""
        from embertide.engine import watch
        watch.record(self.store, pid, [(a, float(b)) for a, b in pts], [], self.ctl.clock.now_ts())

    # ---- what the phone shows ----
    def log(self, pid: str = "p1") -> list[dict]:
        return self.c.get(f"/api/people/{pid}/imports", headers=H).json()

    def nights(self, pid: str = "p1", count: int = 7) -> list[dict]:
        return self.c.get(f"/api/people/{pid}/nights?count={count}", headers=H).json()

    def night(self, date: str, pid: str = "p1") -> dict:
        r = self.c.get(f"/api/people/{pid}/night?date={date}", headers=H)
        assert r.status_code == 200, r.text[:300]
        return r.json()

    def cover(self, pid: str = "p1") -> list[dict]:
        return self.c.get(f"/api/people/{pid}/wearable/nights", headers=H).json()

    def compare(self, date: str, pid: str = "p1") -> dict:
        return self.c.get(f"/api/people/{pid}/compare?date={date}", headers=H).json()

    def samples(self, device: str, metric: str = "hr", pid: str = "p1", a: float = 0, b: float = 4e9):
        return self.store.samples(f"wear:{pid}:{metric}:{device}", a, b)


@contextmanager
def world(tmp_path):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, False
    with TestClient(create_app(cfg)) as c:
        yield World(c)


def text_of(rep: dict) -> str:
    """All the words an import answered with, for wording checks."""
    return " ".join(list(rep.get("notes") or []) + list(rep.get("warnings") or []))
