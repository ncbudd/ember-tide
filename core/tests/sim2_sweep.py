"""Sweep of realistic "awake and reading in bed" heart-rate profiles through the bed-settle check (engine/sleepsign.settled).

A reader is awake the whole time, so every time the check fires for one is a false anchor. `python tests/sim2_sweep.py`
prints the table the notes quote; test_sim2_sleepsign.py uses `fires`/`reading_rows` for its bounded assertions.
Profile: heart rate relaxes after getting in (exponential, `amp` bpm over `tau` minutes), may also drift down `slope` bpm
an hour, with reading noise `sd` and a share `move` of readings showing a page turn."""

from __future__ import annotations

import itertools
import math
import random
from collections import defaultdict

from embertide.engine.sleepsign import settled


def reading_rows(h0, amp, tau_min, slope_h, sd, move, rng, hours=3.0, still_from_min=0.0):
    rows = []
    for i in range(int(hours * 3600 / 15)):
        t = i * 15.0
        m = t / 60
        hr = h0 - amp * (1 - math.exp(-m / tau_min)) - slope_h * (m / 60) + rng.gauss(0, sd)
        rows.append({"ts": t, "occupied": 1, "status": 0 if (rng.random() > move or m < still_from_min) else 4, "hr": round(hr)})
    return rows


def fires(rows, step=120):
    """First minute (after getting in at 0) the settle check says yes, or None."""
    t, end = 1800, rows[-1]["ts"]
    while t <= end:
        if settled([r for r in rows if r["ts"] <= t], 0.0, t):
            return t / 60
        t += step
    return None


def sweep(seed=1, reps=3):
    rng = random.Random(seed)
    out = []
    for h0, amp, tau, slope, sd, move in itertools.product([62, 70, 80], [0, 3, 6, 10], [10, 25], [0, 2, 5], [1.5, 3], [0.02, 0.06, 0.10]):
        out.append(((h0, amp, tau, slope, sd, move), [fires(reading_rows(h0, amp, tau, slope, sd, move, rng)) for _ in range(reps)]))
    return out


if __name__ == "__main__":
    res = sweep()
    fired = [x for _, f in res for x in f if x is not None]
    print("fired", len(fired), "of", sum(len(f) for _, f in res))
    for name, idx in [("amp", 1), ("slope", 3), ("sd", 4), ("move", 5), ("tau", 2), ("h0", 0)]:
        d = defaultdict(lambda: [0, 0])
        for k, f in res:
            for x in f:
                d[k[idx]][1] += 1
                d[k[idx]][0] += x is not None
        print(name, {k: f"{v[0]}/{v[1]}={v[0] / v[1]:.0%}" for k, v in sorted(d.items())})
    fired.sort()
    if fired:
        print("fire minute quantiles", fired[0], fired[len(fired) // 4], fired[len(fired) // 2], fired[3 * len(fired) // 4], fired[-1])
    for label, cond in [("amp<=6,slope<=2", lambda k: k[1] <= 6 and k[3] <= 2), ("amp<=3,slope<=2", lambda k: k[1] <= 3 and k[3] <= 2),
                        ("amp 0,slope 0", lambda k: k[1] == 0 and k[3] == 0)]:
        sub = [f for k, f in res if cond(k)]
        print(label, sum(x is not None for f in sub for x in f), "of", sum(len(f) for f in sub))
