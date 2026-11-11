"""Notification lab: a normal week and a messy week on the simulator, counting every ping each person would get.

Used by test_notify_week.py (budgets) and runnable by hand to print the table in docs/notifications.md:
    cd core && PYTHONPATH=. .venv/Scripts/python.exe tests/notify_week.py

The real Controller, NightEngine and Notifier run; only the world is scripted:
- normal: both sleepers on the simulator's usual script for 7 nights.
- messy: a late night out (not home at pre-condition, in bed near midnight), a pet on the bed overnight (a pet question),
  the bed offline 25 min at 2 AM plus a frozen sensor at 3 AM, a guest on one side, an afternoon nap left on by hand,
  and a night where one person never comes to bed.
A ping = a notification that actually went out (store event "notification"); household messages count for everyone.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import embertide.engine.controller as ctlmod
from embertide.clock import FixedClock
from embertide.drivers.sim import SimBed
from embertide.engine.controller import Controller
from embertide.models import Pet
from embertide.notify import Notifier
from embertide.seed import default_household
from embertide.store import Store

TZ = ZoneInfo("America/New_York")
START = "2026-11-09T12:00"          # a Monday noon


def ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


class LabNotifier(Notifier):
    """Lets the lab break things the simulator never breaks: the bed going offline, a frozen sensor."""

    def __init__(self, c, windows: dict) -> None:
        super().__init__(c, None)
        self.windows = windows

    def observe(self, now, snap):
        for a, b in self.windows.get("offline", []):
            if a <= now < b:
                snap.setdefault("system", {})["online"] = False
                snap["system"]["detail"] = "no answer"
        for zone, a, b in self.windows.get("stale", []):
            if a <= now < b:
                for pv in snap.get("people", []):
                    if pv["zone"] == zone:
                        pv.setdefault("presence", {})["stale"] = True
        return super().observe(now, snap)


async def run_week(tmp: Path, messy: bool, dt: float = 120) -> dict:
    ctlmod.MIN_CMD_GAP_S = 0
    clock = FixedClock(ts(START), "America/New_York")
    bed = SimBed(clock)
    store = Store(tmp / "week.db")
    hh = default_household()
    if messy:
        hh.pets = [Pet(name="Biscuit", kind="cat", weight_kg=4.5, sleeps_on_bed="yes")]
    c = Controller(hh, bed, store, clock)
    for p in hh.people:
        p.settings.autopilot.mode = "off"            # (Autopilot ideas are in the app, never pushed)
    p1, p2 = hh.people
    day = lambda n, hm: ts((datetime.fromisoformat(START) + timedelta(days=n)).strftime("%Y-%m-%dT") + hm)  # noqa: E731
    windows: dict = {"offline": [], "stale": []}
    if messy:
        windows["offline"].append((day(4, "02:00"), day(4, "02:25")))          # Thursday 2 AM
        windows["stale"].append(("left", day(4, "03:00"), day(4, "03:20")))
    c.notifier = LabNotifier(c, windows)
    end = ts(START) + 7 * 86400
    script0 = dict(bed.script)
    while clock.now_ts() < end:
        now = clock.now_ts()
        if messy:
            n = int((now - ts(START)) // 86400)                # 0 = Monday noon → Tuesday noon
            bed.script = dict(script0)
            c.home[p1.id] = None
            if n == 1:                                       # Tuesday: out late, home 23:40, in bed 23:50
                bed.script["left"] = ("23:50", "07:10", [])
                if now < day(1, "23:40"):
                    c.home[p1.id] = False
            if n == 2 and abs(now - day(3, "03:00")) < dt / 2 and not store.get("pet_questions"):
                store.put("pet_questions", [{                 # the cat lay on the right side 2:40–3:00 AM
                    "id": uuid.uuid4().hex[:10], "person": p2.id, "zone": "right", "group": "x", "kind": "pet",
                    "created": now, "pets": [hh.pets[0].id], "status": "open", "answer": None, "answered_at": None,
                    "pushed": False, "spans": [{"start": day(3, "02:40"), "end": day(3, "03:00"), "evidence": "heart"}]}])
                store.event(now, "pet_question", p2.id, "pets", zone="right")
            if n == 4 and abs(now - day(4, "20:00")) < dt / 2 and not hh.guest:
                c.guest_start(["right"], None, 30, None)       # Friday: a guest on the right side
                bed.script["right"] = ("22:50", "08:10", [])
            if n == 4:
                bed.script["right"] = ("22:50", "08:10", [])
            if n == 5 and hh.guest and now > day(5, "10:00"):
                c.guest_stop(["right"], source="app")
            if n == 5:                                       # Saturday: a nap, left on by hand
                if abs(now - day(5, "14:00")) < dt / 2:
                    c.engine_for(p1.id).adjust(now, temp_c=20.0, minutes=120)
                if day(5, "14:00") <= now < day(5, "14:40"):
                    bed.script["left"] = ("13:59", "14:40", [])
                bed.script["right"] = ("23:58", "00:00", [])  # Saturday night: right never comes to bed
        bed._tick()
        await c.tick()
        for t in list(c._inflight.values()):
            await t
        clock.advance(dt)
    out: dict = {"by_person": {}, "night": {}, "kinds": {}, "titles": defaultdict(list)}
    evs = [x for x in store.events(limit=100000) if x["kind"] == "notification"]
    for p in hh.people:
        mine = [x for x in evs if x.get("person") in (p.id, None)]
        per_day: Counter = Counter()
        night = 0
        for x in mine:
            t = datetime.fromtimestamp(x["ts"], TZ)
            per_day[t.date().isoformat()] += 1
            if t.hour >= 23 or t.hour < 6 or (t.hour == 6 and t.minute < 30):
                night += 1
            out["titles"][p.name].append(f"{t:%a %H:%M} [{x.get('about')}] {x.get('title')} | {x.get('body')}")
        out["by_person"][p.name] = {"total": len(mine), "max_day": max(per_day.values(), default=0), "per_day": dict(per_day)}
        out["night"][p.name] = night
        out["kinds"][p.name] = dict(Counter(x.get("about") for x in mine))
    return out


def main() -> None:
    for messy in (False, True):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            r = asyncio.run(run_week(Path(d), messy))
        print("=== messy week ===" if messy else "=== normal week ===")
        for name, v in r["by_person"].items():
            print(f"{name}: {v['total']} pings, at most {v['max_day']} a day, {r['night'][name]} at night; {r['kinds'][name]}")
            for line in r["titles"][name]:
                print("   ", line)


if __name__ == "__main__":
    main()
