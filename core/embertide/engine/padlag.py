"""How fast each side of the topper really warms and cools, and what that means in a cold or warm bedroom.

In a cold room the pad can lag well behind a warm setpoint (seen 2026-11-08: room 18.4 °C, side set 27.2 °C,
measured 26.4 for hours; in the warm wake 30.0 set, 29.3 measured). So, per side:

1. learn() — from the last 14 days of telemetry (one row a minute) and the room sensor, per 2 °C room bucket:
   - warm_rate / cool_rate: °C per minute the pad moved toward a setpoint at least 1 °C away.
   Kept in kv `padlag:<zone>`, refreshed at most once an hour.
2. lead_s() — when the next phase is warmer and the room is cool (or cooler and the room warm), start the change
   earlier by the time the pad needs for it beyond the phase's own ramp, never more than 30 min and never more than
   half of the current phase (it keeps its useful part). Nothing without a room sensor or without learned data.
3. the controller's shortfall check — the pad more than 0.5 °C under a steady warm target for 30+ min in a cold room
   (under 20 °C): said plainly
   ("The room is cold (65°), so your side is running a little below 81°") and not a reason for Autopilot to change the
   plan.
"""

from __future__ import annotations

import statistics
from ..units import fmt_temp

LEARN_DAYS = 14
BUCKET_C = 2.0
MIN_STEP_C = 1.0            # a setpoint change this big or more teaches the rate
MAX_LEAD_S = 30 * 60
COOL_ROOM_C = 21.0          # warming leads only below this room temperature
WARM_ROOM_C = 23.0          # cooling leads (and an earlier pre-condition) only above it
COLD_NOTE_C = 20.0          # "the room is cold" below this
SHORT_C = 0.5
SHORT_FOR_S = 30 * 60


def bucket(room_c: float) -> str:
    return str(int(room_c // BUCKET_C * BUCKET_C))


def room_now(samples: list[tuple[float, float]], now: float) -> float | None:
    v = [x for t, x in samples if now - 1800 <= t <= now]
    return round(statistics.mean(v), 1) if v else None


def learn(rows: list[dict], room: list[tuple[float, float]]) -> dict:
    """rows: one person's telemetry (ts, on, setpoint, measured), oldest first, ~1 per minute; room: (ts, °C)."""
    if not rows or not room:
        return {}
    room = sorted(room)
    j = 0

    def room_at(t: float) -> float | None:
        nonlocal j
        while j + 1 < len(room) and room[j + 1][0] <= t:
            j += 1
        return room[j][1] if abs(room[j][0] - t) <= 1800 else None
    acc: dict[str, dict[str, list[float]]] = {}
    for a, b in zip(rows, rows[1:]):
        sp, m0, m1 = a.get("setpoint"), a.get("measured"), b.get("measured")
        if not a.get("on") or sp is None or m0 is None:
            continue
        r = room_at(a["ts"])
        dt = (b["ts"] - a["ts"]) / 60
        if r is None or not b.get("on") or m1 is None or not 0.2 <= dt <= 5:
            continue
        k = acc.setdefault(bucket(r), {"warm": [], "cool": []})
        gap = sp - m0
        if gap >= MIN_STEP_C and m1 > m0:
            k["warm"].append((m1 - m0) / dt)
        elif gap <= -MIN_STEP_C and m1 < m0:
            k["cool"].append((m0 - m1) / dt)
    out = {}
    for k, v in acc.items():
        e = {}
        if len(v["warm"]) >= 5:
            e["warm_rate"] = round(statistics.median(v["warm"]), 4)
        if len(v["cool"]) >= 5:
            e["cool_rate"] = round(statistics.median(v["cool"]), 4)
        if e:
            out[k] = e
    return out


def model_for(model: dict, room_c: float) -> dict:
    """The learned numbers for this room temperature (nearest bucket learned, within 4 °C)."""
    if not model:
        return {}
    b = float(bucket(room_c))
    best = min(model, key=lambda k: abs(float(k) - b))
    return model[best] if abs(float(best) - b) <= 4 else {}


def lead_s(model: dict, room_c: float | None, cur_temp: float | None, nxt_temp: float, nxt_ramp_s: float,
           phase_left_s: float, phase_len_s: float) -> float:
    """Seconds to start the next phase's change early. 0 when nothing applies."""
    if room_c is None or cur_temp is None:
        return 0.0
    delta = nxt_temp - cur_temp
    m = model_for(model, room_c)
    if delta >= 0.3 and room_c < COOL_ROOM_C:
        rate = m.get("warm_rate")
    elif delta <= -0.3 and room_c > WARM_ROOM_C:
        rate = m.get("cool_rate")
    else:
        return 0.0
    if not rate or rate <= 0:
        return 0.0
    need = abs(delta) / rate * 60 - nxt_ramp_s
    cap = min(MAX_LEAD_S, phase_len_s / 2)
    lead = max(0.0, min(need, cap))
    return lead if lead >= 60 else 0.0


def precool_lead_s(model: dict, room_c: float | None, from_c: float | None, to_c: float) -> float:
    """Start the evening pre-condition earlier in a warm room (from `from_c`, the room / idle pad, to the first phase)."""
    if room_c is None or from_c is None or room_c <= WARM_ROOM_C or to_c >= from_c - 0.3:
        return 0.0
    rate = model_for(model, room_c).get("cool_rate")
    if not rate or rate <= 0:
        return 0.0
    lead = min(MAX_LEAD_S, (from_c - to_c) / rate * 60 / 2)       # half the way is already covered by the usual lead
    return lead if lead >= 60 else 0.0


def shortfall_text(room_c: float, target_c: float, units: str) -> str:
    def t(c: float) -> str:
        return fmt_temp(c, units)
    return f"The room is cold ({t(room_c)}), so your side is running a little below {t(target_c)}"
