"""Bed scale (load cells) that copes with real-life clutter on the bed.

A scale under one side reads everything on it: the mattress topper, a heavier winter duvet, extra pillows, a weighted
blanket, laundry dropped there in the afternoon, a cat, someone perching on the edge. Load cells also drift slowly.
A fixed "above 20 kg means someone is in bed" rule gets all of these wrong sooner or later, so each scale keeps:

- an **empty-bed zero** that follows what the empty bed weighs. Small drift is followed slowly while the side is
  confirmed empty; a bigger step (a duvet, a 10 kg laundry pile) is taken in once the side has been confirmed
  empty and the reading has stayed steady for EMPTY_CONFIRM_S. When weight comes off (the laundry is put away)
  and stays off for DOWN_CONFIRM_S, the zero follows it down.
- a **learned weight for the person on this side** (from steady in-bed readings, or the weight the person gave),
  so "someone is in bed" means a person-sized load: PERSON_FRACTION of that weight. Perching on the edge puts
  only part of a body on that side's cells and stays under it; bedding, pillows and pets stay far below it.

The scale's own vote then still needs its settle time (min_on_s) and hold time (hold_s): short spikes and dips
are ignored. Nothing here decides alone; the scale is one vote in presence fusion (engine/controller.fuse).
"""

from __future__ import annotations

from statistics import median

EMPTY_CONFIRM_S = 30 * 60     # confirmed empty and steady this long: re-zero on what the bed weighs now
DOWN_CONFIRM_S = 3 * 60       # weight came off below the zero and stayed off this long: follow it down
STEADY_KG = 1.2               # readings within this spread count as steady (load cells are noisy)
DRIFT_KG = 1.5                # offsets this small are drift: followed slowly while the side is empty
DRIFT_TAU_S = 2 * 3600        # time constant for following drift
PERSON_FRACTION = 0.6         # a person-sized load is at least this share of the learned person weight
PERSON_CAP = 0.85             # ... and never more than this share (a light sleeper must still count)
LEARN_TAU_S = 4 * 3600        # time constant for learning the person's weight from in-bed readings
LEARN_STEADY_S = 5 * 60


class ScaleTracker:
    def __init__(self, empty_kg: float = 0.0, person_kg: float | None = None) -> None:
        self.empty_kg = float(empty_kg)
        self.person_kg = person_kg
        self.window: list[tuple[float, float]] = []
        self.last_ts: float | None = None
        self.changed = False          # something worth saving moved

    # ---- readings ----
    def net(self, value: float) -> float:
        return value - self.empty_kg

    def _steady(self, now: float, span: float) -> list[float] | None:
        """Readings of the last `span` seconds if they cover it and stay within STEADY_KG, else None."""
        vals = [v for t, v in self.window if t >= now - span]
        if not vals or self.window[0][0] > now - span * 0.95 or max(vals) - min(vals) > STEADY_KG:
            return None
        return vals

    def update(self, ts: float, value: float, empty: bool | None, in_bed: bool | None, threshold: float) -> None:
        """One reading. empty = the side is confirmed empty by the other signals (strip, radar) and no pet is
        suspected; None = nothing else knows. in_bed = the person's night is running and fusion says in bed."""
        if self.last_ts is not None and ts < self.last_ts:
            self.window = []                                   # the clock jumped back (simulator): start over
        self.last_ts = ts
        self.window.append((ts, value))
        keep = ts - max(EMPTY_CONFIRM_S, LEARN_STEADY_S) - 120
        while self.window and self.window[0][0] < keep:
            self.window.pop(0)
        net = self.net(value)
        dt = (self.window[-1][0] - self.window[-2][0]) if len(self.window) >= 2 else 0.0
        dt = min(max(dt, 0.0), 300.0)

        # weight came off: the bed can't weigh less than empty, so the zero follows it down once it settles
        if net < -0.5:
            vals = self._steady(ts, DOWN_CONFIRM_S)
            if vals and max(vals) < self.empty_kg - 0.5:
                self._set_empty(median(vals))
                return
        light = net < threshold * 0.5
        if empty is not False and light:
            # small drift: follow slowly, but only while the side is known to be empty
            if empty and abs(net) <= DRIFT_KG and dt:
                self._set_empty(self.empty_kg + (value - self.empty_kg) * min(1.0, dt / DRIFT_TAU_S), quiet=True)
            # a new steady load on an empty bed (bedding, laundry): take it in after a while
            vals = self._steady(ts, EMPTY_CONFIRM_S)
            if vals and abs(median(vals) - self.empty_kg) > DRIFT_KG * 0.5 and (empty or abs(net) <= threshold * 0.35):
                self._set_empty(median(vals))
        elif in_bed and net >= threshold and dt:
            vals = self._steady(ts, LEARN_STEADY_S)
            if vals:
                m = median(vals) - self.empty_kg
                self.person_kg = m if self.person_kg is None else self.person_kg + (m - self.person_kg) * min(1.0, dt / LEARN_TAU_S)
                self.changed = True

    def _set_empty(self, kg: float, quiet: bool = False) -> None:
        if not quiet:
            self.changed = True
        self.empty_kg = round(kg, 4)

    # ---- threshold ----
    def threshold(self, base_kg: float, known_kg: list[float]) -> float:
        """Person-sized load for this side: a share of the person's learned (or given) weight, never below the
        device's own setting and never above most of the person."""
        ref = self.person_kg or (min(known_kg) if known_kg else None)
        if not ref or ref < 15:
            return base_kg
        return min(max(base_kg, PERSON_FRACTION * ref), PERSON_CAP * ref)
