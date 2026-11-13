"""Active, not sleeping yet: a stretch where the people in bed are clearly awake and moving (reading in bed is
still; this is not), with fast hearts and breathing on one or both sides. It is not sleep and it says nothing about
how warm or cool the bed should be, so while it lasts (and afterwards, looking back at it):
- Autopilot neither learns from it nor nudges because of it (a "too hot" tap still counts: that's the person's word);
- the pet guard, the watch's "is it me?" check and the near-copy echo check leave it alone (a fast heart on a pad
  here is a person, not the cat, and two people moving together are not one sleeper's echo);
- a night that hasn't started waits until they settle, and an out-of-bed hold doesn't run out (someone may simply
  be on the other side of the bed);
- the night's story counts it as time before sleep, not as restless sleep or getting up.

Signs, judged over the last 5 minutes per side (sampled from the bed strip):
- moving / turned over / moving briefly (codes 4, 6, 7) in at least 35% of readings, and
- a median heart rate of 90 bpm or more.
It starts when a side shows both, and the other side shows them too or is empty (both people on one side). One
person lying still next to it (the partner asleep) means it isn't this. One side alone counts only when the other
side had a person on it in the last 30 minutes or either side's night is running (a big dog alone on the bed has a
heart rate like a person's). Guards:
- the side must have started with a person's heart rate (100 bpm or less when someone lay down) or carry a night
  that is already running: a cat's heart is far faster than that from the moment it jumps up;
- two sides at once don't need that, unless there are fast-hearted pets in the household;
- it ends once every occupied side has been calm for the 5 minutes (moving under 20%, heart under 85 bpm), or both
  sides are empty; and after 2 hours at most (then it isn't this, and normal rules apply again for an hour).
Recorded as two short events without a person ("active_started" / "active_ended", with the sides), worded neutrally.
"""

from __future__ import annotations

from collections import deque
from statistics import median

MOVING = {4, 6, 7}
WINDOW_S = 300
SAMPLE_S = 5
START_MOVE, START_HR = 0.35, 90
END_MOVE, END_HR = 0.20, 85
HUMAN_HR = 100
FIRST_S = 180               # the readings when someone lies down that tell a person from a cat
MAX_S = 2 * 3600
COOLDOWN_S = 3600
MIN_SPAN_S = 120            # the window must cover at least this much to judge
TEXT = "Active, not sleeping yet"


class ActiveDetector:
    def __init__(self) -> None:
        self.hist: dict[str, deque] = {"left": deque(), "right": deque()}
        self.ep: dict[str, dict] = {}          # zone -> {"since", "first": [hr], "last_on"}
        self.since: float | None = None
        self.began: float | None = None         # the first busy reading before it was recognised (≤ 5 min earlier)
        self.zones: list[str] = []
        self.cool_until = 0.0

    @property
    def on(self) -> bool:
        return self.since is not None

    def update(self, now: float, sides: dict, known: set[str] | None = None, fast_pets: bool = False) -> str | None:
        """Feed the strip's current SideStates. `known` = sides whose sleeper's night is already running in bed.
        Returns "started" / "ended" when that happens, else None."""
        known = known or set()
        for z in ("left", "right"):
            s = sides.get(z)
            occ = bool(s is not None and s.occupied and not s.echo)
            h = self.hist[z]
            if not h or now - h[-1][0] >= SAMPLE_S or now < h[-1][0]:
                h.append((now, occ, (s.status in MOVING) if occ and s.status is not None else None, s.hr if occ else None))
            while h and now - h[0][0] > WINDOW_S:
                h.popleft()
            e = self.ep.get(z)
            if occ:
                if e is None or now - e["last_on"] > 120:
                    e = self.ep[z] = {"since": now, "first": [], "last_on": now}
                e["last_on"] = now
                if now - e["since"] <= FIRST_S and s.hr:
                    e["first"].append(s.hr)
        like = {z: self._looks(z, START_MOVE, START_HR, high=True) for z in ("left", "right")}
        empty = {z: self._empty(z) for z in ("left", "right")}
        human = {z: z in known or self._human(z) for z in ("left", "right")}
        if self.since is None:
            if now < self.cool_until:
                return None
            hit = None
            for a, b in (("left", "right"), ("right", "left")):
                if like[a] and like[b] and (human[a] or human[b] or not fast_pets):
                    hit = [a, b]
                elif like[a] and human[a] and empty[b] and (a in known or b in known or now - self._person_on(b) <= 1800):
                    hit = [a]                    # two people on one side: the other side had someone on it lately
                if hit:
                    break
            if hit:
                self.since, self.zones = now, sorted(hit)
                # it was going on for a few minutes before it was clear: from the first busy reading in the window
                busy = [x[0] for z in hit for x in self._window(z) if x[2] or (x[3] or 0) >= START_HR]
                self.began = min(busy) if busy else now
                return "started"
            return None
        for z in ("left", "right"):
            if like[z] and z not in self.zones:
                self.zones = sorted(self.zones + [z])
        calm = all(empty[z] or self._looks(z, END_MOVE, END_HR, high=False) for z in ("left", "right"))
        if calm or now - self.since >= MAX_S:
            if now - self.since >= MAX_S:
                self.cool_until = now + COOLDOWN_S
            self.since, self.zones = None, []
            return "ended"
        return None

    def _window(self, z: str) -> list:
        return [x for x in self.hist[z] if x[1]]

    def _empty(self, z: str) -> bool:
        h = list(self.hist[z])
        recent = [x for x in h if h[-1][0] - x[0] <= 60] if h else []
        return bool(recent) and not any(x[1] for x in recent)

    def _looks(self, z: str, move: float, hr: float, high: bool) -> bool:
        """high=True: moving share ≥ move and median HR ≥ hr ("active"); high=False: moving < move and HR < hr ("calm")."""
        w = self._window(z)
        if len(w) < 3 or w[-1][0] - w[0][0] < (MIN_SPAN_S if high else 0):
            return not high and not w                      # nothing to go on: not active; calm only if nobody's there
        mv = [x[2] for x in w if x[2] is not None]
        hrs = [x[3] for x in w if x[3]]
        share = sum(1 for x in mv if x) / len(mv) if mv else 0.0
        mh = median(hrs) if len(hrs) >= 3 else None
        if high:
            return share >= move and mh is not None and mh >= hr
        return share < move and (mh is None or mh < hr)

    def _person_on(self, z: str) -> float:
        """When this side last had a person on it (an occupancy that started with a person's heart rate)."""
        e = self.ep.get(z)
        return e["last_on"] if e and e["first"] and median(e["first"]) <= HUMAN_HR else -1e18

    def _human(self, z: str) -> bool:
        e = self.ep.get(z)
        return bool(e and e["first"] and median(e["first"]) <= HUMAN_HR)


def windows(events: list[dict], zone: str | None, until: float) -> list[tuple[float, float]]:
    """Active stretches (from the active_started / active_ended events) touching this side (None = any side)."""
    out, start = [], None
    for e in sorted((x for x in events if x["ts"] <= until and x.get("kind") in ("active_started", "active_ended")), key=lambda x: x["ts"]):
        if zone is not None and zone not in (e.get("zones") or ["left", "right"]):
            continue
        if e["kind"] == "active_started" and start is None:
            start = min(e["ts"], e.get("began") or e["ts"])
        elif e["kind"] == "active_ended" and start is not None:
            out.append((start, e["ts"]))
            start = None
    if start is not None:
        out.append((start, until))
    return out


def inside(ts: float, wins: list[tuple[float, float]]) -> bool:
    return any(a <= ts <= b for a, b in wins)
