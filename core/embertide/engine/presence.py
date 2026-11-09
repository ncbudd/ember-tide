"""Per-side presence from the topper's own sensors, with the known quirks filtered out.

Rules (from community captures on firmware 2.6.2):
- occupied must hold for `confirm_s` (default 3 s) before it counts; clearing is immediate
- codes 1 (initializing) and 255 (invalid) mean unknown, never occupied
- a sensor whose clock token stops changing for `stale_s` (15 s) is stale → unknown
- one sleeper often shows on BOTH pads with identical HR/BR ("echo"). When both pads report the same
  readings for `echo_s` seconds, the side that became occupied later is marked as an echo.
- the echo isn't always identical: the other pad can pick up the sleeper's heartbeat through the mattress and report
  it a beat or two off (seen 2026-11-07: right pad 66/16, 70/13, 72/16 vs left 67/16, 69/15, 71/13). So both pads'
  readings are also compared over a few minutes: close readings on a pad that keeps dropping out (flickering)
  while the other is steady mark the flickering side as the echo. A steady echo is caught from 10 minutes of 30 s
  averages: breathing within 1/min, heart rates within 2 bpm moving together; the pad with the weaker signal (more
  "breathing pause") is the echo. It is dropped as soon as the two drift apart, or when the echo side is really empty for 2 minutes.
Extra sensors (radar, load cells) plug in later through the same output shape.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from ..drivers.base import SensorFrame

OCCUPIED_CODES = {0, 2, 3, 4, 6, 7}
STATUS_TEXT = {0: "in bed", 1: "initializing", 2: "breathing pause", 3: "heart signal lost", 4: "moving",
               5: "empty", 6: "turned over", 7: "moving briefly", 255: "invalid"}


@dataclass
class SideState:
    raw_occupied: bool | None = None
    pending_since: float | None = None
    occupied: bool | None = None
    since: float | None = None            # when the current confirmed state began
    last_clock: tuple | None = None
    clock_changed_at: float | None = None
    echo: bool = False
    hr: int | None = None
    br: int | None = None
    status: int | None = None
    stale: bool = False


@dataclass
class PresenceFilter:
    confirm_s: float = 3.0
    stale_s: float = 15.0
    echo_s: float = 20.0
    sides: dict[str, SideState] = field(default_factory=lambda: {"left": SideState(), "right": SideState()})
    _same_since: float | None = None
    # near-copy ("follower") echo: paired readings while both pads say occupied, and each pad's raw occupancy history
    follow_window_s: float = 240.0
    follow_min_s: float = 90.0
    _pairs: deque = field(default_factory=deque)
    _occ_hist: dict = field(default_factory=lambda: {"left": deque(), "right": deque()})
    _follower: str | None = None
    _f_empty_since: float | None = None
    # a steady echo (no flicker): 30 s averages of both pads over 10 min; breathing is the clearest copy
    _bin: list | None = None
    _bins: deque = field(default_factory=lambda: deque(maxlen=20))
    _together_streak: int = 0
    _echo_pair: bool = False
    _weak: dict = field(default_factory=lambda: {"left": deque(), "right": deque()})
    # sides whose sleeper's night is running in bed (set by the controller each tick): breaks a tie when both pads
    # start at once with identical readings (after a restart or a bed reboot)
    prefer: set = field(default_factory=set)
    # an "active, not sleeping yet" stretch is on (engine/active.py): no new near-copy echo is decided from it
    active: bool = False

    def update(self, ts: float, frames: dict[str, SensorFrame]) -> dict[str, SideState]:
        for name, f in frames.items():
            s = self.sides.setdefault(name, SideState())
            if f.clock is not None and f.clock != s.last_clock:
                s.last_clock, s.clock_changed_at = f.clock, ts
            elif s.clock_changed_at is None:
                s.clock_changed_at = ts
            s.stale = s.clock_changed_at is not None and ts - s.clock_changed_at > self.stale_s
            s.status = f.status
            raw = None if (s.stale or f.status is None or f.status in (1, 255)) else (f.status in OCCUPIED_CODES)
            s.raw_occupied = raw
            s.hr, s.br = (f.hr, f.br) if raw else (None, None)
            if raw is None:
                s.pending_since = None
                if s.occupied is not None:
                    s.occupied, s.since = None, ts
            elif raw is False:
                s.pending_since = None
                if s.occupied is not False:
                    s.occupied, s.since = False, ts
            else:
                if s.occupied is True:
                    pass
                elif self._copies_other(name, frames):
                    s.pending_since = None          # identical to a side that is already occupied: an echo, not a person
                    if s.occupied is None:
                        s.occupied, s.since = False, ts
                elif s.pending_since is None:
                    s.pending_since = ts
                elif ts - s.pending_since >= self.confirm_s:
                    s.occupied, s.since = True, s.pending_since
        self._echo(ts)
        self._follow(ts)
        return self.sides

    def _follow(self, ts: float) -> None:
        l, r = self.sides.get("left"), self.sides.get("right")
        if not l or not r:
            return
        for name, sd in (("left", l), ("right", r)):          # sampled every ~5 s, kept 10 min
            # when each pad came on, ignoring short blips: a pad that reads empty for a few seconds (it does, a dozen
            # times a night) keeps its time; only 3 min of empty means its person really left
            on = self.__dict__.setdefault("_on_since", {})
            off = self.__dict__.setdefault("_off_since", {})
            if sd.raw_occupied and sd.occupied:          # confirmed: a half-second blip isn't anyone coming on
                on.setdefault(name, sd.since or ts)
                off.pop(name, None)
            elif sd.raw_occupied:
                off.pop(name, None)
            else:
                off.setdefault(name, ts)
                if ts - off[name] >= 180:
                    on.pop(name, None)
            h = self._occ_hist[name]
            if not h or ts - h[-1][0] >= 5:
                h.append((ts, 1 if sd.raw_occupied else 0))
            while h and ts - h[0][0] > 600:
                h.popleft()
        both = l.raw_occupied and r.raw_occupied and None not in (l.hr, l.br, r.hr, r.br)
        self._steady(ts, l, r, both)
        if both and (not self._pairs or ts - self._pairs[-1][0] >= 5):
            self._pairs.append((ts, l.hr, l.br, r.hr, r.br))
        while self._pairs and ts - self._pairs[0][0] > self.follow_window_s:
            self._pairs.popleft()
        # a pad really empty for 2 min (the echo stopped, or someone got up): forget the echo, so a person who gets in
        # later is judged afresh and not held back by old readings
        if l.raw_occupied is False or r.raw_occupied is False:
            self._f_empty_since = self._f_empty_since or ts
            if ts - self._f_empty_since >= 120:
                self._follower, self._echo_pair, self._together_streak = None, False, 0
                self._pairs.clear()
                self._bins.clear()
        else:
            self._f_empty_since = None
        if not (l.raw_occupied and r.raw_occupied):
            if self._follower and not (l.raw_occupied or r.raw_occupied):
                self._follower = None
            self._apply_follower()
            return
        ps = list(self._pairs)
        self._onset_hold(ts, l, r, ps)
        if len(ps) < 12 or ps[-1][0] - ps[0][0] < self.follow_min_s:
            self._apply_follower()
            return
        dh = sum(abs(a[1] - a[3]) for a in ps) / len(ps)
        db = sum(abs(a[2] - a[4]) for a in ps) / len(ps)
        fl = sum(x[1] for x in self._occ_hist["left"]) / max(1, len(self._occ_hist["left"]))
        fr = sum(x[1] for x in self._occ_hist["right"]) / max(1, len(self._occ_hist["right"]))
        def ons(h) -> int:                                   # times the pad came back on in the last 10 min
            v = [x[1] for x in h]
            return sum(1 for a, b in zip(v, v[1:]) if not a and b)
        flaky = "left" if fl < fr else "right"
        # one pad kept dropping out and coming back (3+ times in 10 min) while the other stayed put. A person getting
        # up once (bathroom) is one gap, not a flicker.
        flicker = abs(fl - fr) >= 0.15 and ons(self._occ_hist[flaky]) >= 3
        # an echo: close readings on a pad that has been flickering, or very close readings held for the whole window
        # close readings alone aren't enough (two real sleepers can match for minutes by chance); the echo pad also
        # keeps dropping out while the real one is steady. Without that, other sensors (radar, scale) decide.
        together = flicker and dh <= 2.5 and db <= 3.0
        # the echo pad's heart-rate estimate can wander 5-6 bpm off while its breathing still matches (2026-11-07 log),
        # so it's let go when the breathing differs, or the heart rates are far apart
        apart = db > 2.5 or dh > 10.0
        if self._follower is None and together and not self.active:
            self._follower = flaky
        elif self._follower is not None and apart:
            self._follower = None
        self._apply_follower()

    def _steady(self, ts: float, l: SideState, r: SideState, both: bool) -> None:
        """A steady echo: 10 min of 30 s averages where the breathing matches (within 1/min), the heart rates are
        within 2 bpm and move together. The pad whose signal is weaker (more "breathing pause" / "heart signal lost")
        is the echo. Calibrated on the 2026-11-07 log; a pad paired with the same sleeper 15-50 min later never
        matched."""
        b = self._bin
        if b is None or ts - b[0] >= 30:
            if b is not None and b[5] >= 10:
                prev = self._bins[-1][0] if self._bins else None
                if prev is not None and b[0] - prev > 75:
                    self._bins.clear()                         # a gap of more than a bin: start the 10 min again
                self._bins.append((b[0], b[1] / b[5], b[2] / b[5], b[3] / b[5], b[4] / b[5], b[6], b[7]))
                self._judge_steady(l, r)
            b = self._bin = [ts, 0.0, 0.0, 0.0, 0.0, 0, 0, 0]  # a bin with too few paired readings is just skipped
        if not (l.raw_occupied and r.raw_occupied):
            return
        b[6] += 1 if l.status in (2, 3) else 0
        b[7] += 1 if r.status in (2, 3) else 0
        # weak-signal frames ("breathing pause", "heart signal lost") while both pads say occupied, kept for the hour:
        # the echo pad has far more of them (2026-11-07: 269 vs 0). This is the only thing that decides WHICH pad is
        # the echo; without a clear difference nothing is dropped.
        for name, sd in (("left", l), ("right", r)):
            if sd.status in (2, 3):
                self._weak[name].append(ts)
            while self._weak[name] and ts - self._weak[name][0] > 3600:
                self._weak[name].popleft()
        if both:
            b[1] += l.hr; b[2] += l.br; b[3] += r.hr; b[4] += r.br; b[5] += 1

    def _judge_steady(self, l: SideState, r: SideState) -> None:
        w = list(self._bins)
        if len(w) < self._bins.maxlen:
            return
        lh, lb, rh, rb = [x[1] for x in w], [x[2] for x in w], [x[3] for x in w], [x[4] for x in w]
        dh = sum(abs(a - b) for a, b in zip(lh, rh)) / len(w)
        db = sum(abs(a - b) for a, b in zip(lb, rb)) / len(w)
        lc = _corr(lh, rh)
        dc = _corr([b - a for a, b in zip(lh, lh[1:])], [b - a for a, b in zip(rh, rh[1:])])
        together = dh <= 2.0 and db <= 0.8 and (lc or 0) >= 0.6 and (dc or 0) >= 0.4
        self._together_streak = self._together_streak + 1 if together else 0
        if self._together_streak >= 6 and not self.active:
            self._echo_pair = True               # one sleeper on both pads; which pad is the copy may show later
        if db > 2.0 or dh > 10.0:                # the two have drifted apart: two people (or the echo stopped)
            self._echo_pair = False
            self._follower = None
        if self._echo_pair and self._follower is None:
            wl, wr = len(self._weak["left"]), len(self._weak["right"])
            if max(wl, wr) >= 20 and max(wl, wr) >= 4 * min(wl, wr):
                cand = "left" if wl > wr else "right"
                # a copy starts when someone lies down on the OTHER side: only a pad that came on within 2 min after
                # the other one can be its echo. The pad that was on first (e.g. one sleeper in at 00:55, the other
                # 14 min later: the first pad was taken for the echo and its side went "out of bed") never is.
                if self.echo_onset_ok(cand):
                    self._follower = cand

    ECHO_ONSET_S = 120
    ONSET_HOLD_S = 600

    def _onset_hold(self, ts: float, l: SideState, r: SideState, ps: list) -> None:
        """A pad that lit up within 2 min after the other one, reading like it: probably the copy of the person who
        just lay down (2026-11-09: the empty side came on 15 s after hers and her readings showed on it until he got
        in 14 min later, so his night started early). It doesn't count as someone until its readings stay apart for
        3 min, the other side empties, or 10 min pass (other signals, like a watch, can still say it's a person)."""
        on = self.__dict__.get("_on_since", {})
        for cand, other, sd in (("left", "right", l), ("right", "left", r)):
            a, b = on.get(cand), on.get(other)
            if a is None or b is None or not (0 < a - b <= self.ECHO_ONSET_S) or ts - a >= self.ONSET_HOLD_S:
                continue
            recent = [x for x in ps if ts - x[0] <= 180]
            if len(recent) >= 12:
                dh = sum(abs(x[1] - x[3]) for x in recent) / len(recent)
                db = sum(abs(x[2] - x[4]) for x in recent) / len(recent)
                if db > 1.5 or dh > 6.0:
                    continue                              # its own readings: a second person
            sd.echo = True

    def echo_onset_ok(self, cand: str) -> bool:
        on = self.__dict__.get("_on_since", {})
        other = "right" if cand == "left" else "left"
        a, b = on.get(cand), on.get(other)
        return a is not None and b is not None and 0 < a - b <= self.ECHO_ONSET_S

    def _apply_follower(self) -> None:
        if self._follower is None:
            return
        f = self.sides[self._follower]
        other = self.sides["right" if self._follower == "left" else "left"]
        if f.raw_occupied and other.raw_occupied:
            f.echo, other.echo = True, False

    def _copies_other(self, name: str, frames: dict[str, SensorFrame]) -> bool:
        other = "right" if name == "left" else "left"
        o, f = self.sides.get(other), frames.get(name)
        of = frames.get(other)
        if not (o and o.occupied is True and f and of and f.hr is not None and f.hr == of.hr and f.br == of.br):
            return False
        me = self.sides.get(name)
        if me is not None and me.pending_since is not None and o.since is not None and abs(o.since - me.pending_since) <= 1.0:
            # both pads started at the same moment (restart, bed reboot): the other side only confirmed first because
            # it is looked at first, which says nothing about who is really there
            return self._tie_echo(self.sides["left"], self.sides["right"]) is me
        return True

    def _echo(self, ts: float) -> None:
        l, r = self.sides.get("left"), self.sides.get("right")
        if not l or not r:
            return
        same = (l.raw_occupied and r.raw_occupied and l.hr is not None and l.hr == r.hr and l.br == r.br)
        if same:
            self._same_since = self._same_since or ts
        else:
            self._same_since = None
            l.echo = r.echo = False             # _follow() re-applies a near-copy echo right after
            return
        on = self.__dict__.get("_on_since", {})
        if (l.occupied is True and r.occupied is True and on.get("left") is not None and on.get("right") is not None
                and abs(on["left"] - on["right"]) > self.ECHO_ONSET_S):
            # two pads that came on minutes (or hours) apart: two people who got in separately. Identical readings for
            # a minute are their signals bleeding across (2026-11-09: 03:36, 06:21 a sleeper was briefly dropped),
            # not a copy; a real copy starts within seconds of the person who lay down
            l.echo = r.echo = False
            return
        if ts - self._same_since >= self.echo_s:
            # if only one side is confirmed occupied, the other is the echo; otherwise the later one is
            if l.occupied is True and r.occupied is not True:
                later = r
            elif r.occupied is True and l.occupied is not True:
                later = l
            elif abs((l.since or 0) - (r.since or 0)) > 1.0:
                later = l if (l.since or 0) > (r.since or 0) else r
            else:
                later = self._tie_echo(l, r)
            other = r if later is l else l
            later.echo, other.echo = True, False

    def _tie_echo(self, l: SideState, r: SideState) -> SideState:
        """Both pads became occupied at the same moment with the same readings (after a server restart or a bed
        reboot both are already lying there, so neither "got in first"). Which one is the copy:
        - the side whose sleeper's night is already running in bed (the controller's `prefer` hint) is the real one;
        - else the pad with clearly more weak-signal frames ("breathing pause" / "heart signal lost") is the echo;
        - else the echo already decided before, if any; else the right pad (an arbitrary but stable choice)."""
        pl, pr = "left" in self.prefer, "right" in self.prefer
        if pl != pr:
            return r if pl else l
        wl, wr = len(self._weak["left"]), len(self._weak["right"])
        if max(wl, wr) >= 5 and max(wl, wr) >= 2 * min(wl, wr):
            return l if wl > wr else r
        if l.echo != r.echo:
            return l if l.echo else r
        return r

    def present(self, side: str) -> bool | None:
        s = self.sides.get(side)
        if not s or s.occupied is None:
            return None
        return bool(s.occupied and not s.echo)


def _sd(xs: list) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _corr(a: list, b: list) -> float | None:
    sa, sb = _sd(a), _sd(b)
    if sa == 0 or sb == 0:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) * sa * sb)
