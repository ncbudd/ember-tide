"""Rule-based insights over recent nights, each with an optional concrete proposal that can be applied to a rhythm.
Deterministic and explainable; the AI layer (ai.py) builds on top of the same context."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo

from ..models import Person
from .rhythm import night_date, pick_rhythm


from ..units import clock_style, fmt_clock, fmt_delta, fmt_gap, fmt_hour  # noqa: E402


def _hour(ts: float, tz: ZoneInfo) -> int:
    return datetime.fromtimestamp(ts, tz).hour


def insights(person: Person, nights: list[dict], tz: ZoneInfo, now: float) -> list[dict]:
    units, ck = person.settings.units, clock_style(person.settings)
    done = [n for n in nights if n.get("has_data") and not n.get("in_progress")]
    out: list[dict] = []
    if not done:
        return [{"id": "need_data", "title": "Not enough nights yet", "severity": "info",
                 "detail": "Insights start after the first full night. Keep sleeping on it.", "evidence": [], "proposal": None}]
    rh = pick_rhythm(person.rhythms, night_date(datetime.fromtimestamp(now, tz)))
    phase = {p.id: p for p in rh.phases} if rh else {}
    F = 5 / 9

    # 1. feedback says warm/cold
    fb = [n["feedback"]["temp"] for n in done if n.get("feedback")]
    c = Counter(fb)
    if fb and c["warm"] >= max(2, len(fb) // 2) and "deep" in phase:
        out.append({"id": "fb_warm", "severity": "suggest", "title": "You've said “too warm” on several mornings",
                    "detail": f"{c['warm']} of your last {len(fb)} check-ins were too warm. Try Deep one degree cooler.",
                    "evidence": [n["date"] for n in done if (n.get("feedback") or {}).get("temp") == "warm"],
                    "proposal": {"rhythm_id": rh.id, "phase_id": "deep", "field": "temp_c", "from": phase["deep"].temp_c, "to": round(phase["deep"].temp_c - F, 2)}})
    if fb and c["cold"] >= max(2, len(fb) // 2) and "late" in phase:
        out.append({"id": "fb_cold", "severity": "suggest", "title": "You've said “too cold” on several mornings",
                    "detail": f"{c['cold']} of your last {len(fb)} check-ins were too cold. Try Late night one degree warmer.",
                    "evidence": [n["date"] for n in done if (n.get("feedback") or {}).get("temp") == "cold"],
                    "proposal": {"rhythm_id": rh.id, "phase_id": "late", "field": "temp_c", "from": phase["late"].temp_c, "to": round(phase["late"].temp_c + F, 2)}})

    # 2. slow to settle
    tts = [n["time_to_settle_min"] for n in done if n.get("time_to_settle_min") is not None]
    if len(tts) >= 2 and mean(tts) > 25 and "fall_asleep" in phase:
        out.append({"id": "slow_settle", "severity": "suggest", "title": f"Settling takes about {round(mean(tts))} minutes",
                    "detail": "A slightly cooler Fall asleep phase often shortens this. Try one degree cooler for a week.",
                    "evidence": [f"{n['date']}: {n['time_to_settle_min']} min" for n in done if n.get("time_to_settle_min") is not None],
                    "proposal": {"rhythm_id": rh.id, "phase_id": "fall_asleep", "field": "temp_c", "from": phase["fall_asleep"].temp_c, "to": round(phase["fall_asleep"].temp_c - F, 2)}})

    # 3. hot flashes cluster in the late part of the night
    hf = [m for n in done for m in n.get("moments", []) if m["kind"] == "hot_flash"]
    if len(hf) >= 2:
        hours = Counter(_hour(m["ts"], tz) for m in hf)
        h, cnt = hours.most_common(1)[0]
        target = "late" if 2 <= h < 6 else "deep"
        p = phase.get(target)
        out.append({"id": "flash_cluster", "severity": "suggest" if p else "info", "title": f"{len(hf)} hot flashes, most around {fmt_hour(h, ck)}",
                    "detail": f"{cnt} of them started in the {fmt_hour(h, ck)} hour. Running {p.name if p else 'that phase'} a little cooler may prevent some." ,
                    "evidence": [f"{datetime.fromtimestamp(m['ts'], tz):%a} {fmt_clock(m['ts'], ck, tz)}" for m in hf][:8],
                    "proposal": {"rhythm_id": rh.id, "phase_id": target, "field": "temp_c", "from": p.temp_c, "to": round(p.temp_c - F, 2)} if p else None})

    # 4. lots of manual adjustment in one direction
    adj = [m for n in done for m in n.get("moments", []) if m["kind"] == "adjust" and m.get("delta_c")]
    if adj:
        net = sum(m["delta_c"] for m in adj)
        if abs(net) >= 1.5:
            hours = Counter(_hour(m["ts"], tz) for m in adj)
            h = hours.most_common(1)[0][0]
            target = "deep" if 23 <= h or h < 3 else "late" if 3 <= h < 6 else "fall_asleep"
            p = phase.get(target)
            if p:
                step = -F if net < 0 else F
                out.append({"id": "manual_bias", "severity": "suggest", "title": f"You keep making it {'cooler' if net < 0 else 'warmer'}",
                            "detail": f"{len(adj)} manual changes, net {fmt_delta(net, units)}, mostly around {fmt_hour(h, ck)}. Build it into {p.name}.",
                            "evidence": [f"{datetime.fromtimestamp(m['ts'], tz):%a} {fmt_clock(m['ts'], ck, tz)}" for m in adj][:8],
                            "proposal": {"rhythm_id": rh.id, "phase_id": target, "field": "temp_c", "from": p.temp_c, "to": round(p.temp_c + step, 2)}})

    # 5. the bed can't keep up with the plan → lengthen transitions
    trk = [n["tracking_c"] for n in done if n.get("tracking_c") is not None]
    if trk and mean(trk) > 1.2 and "deep" in phase:
        out.append({"id": "tracking", "severity": "suggest", "title": "The bed lags behind the plan",
                    "detail": f"On average the pad is {fmt_gap(mean(trk), units)} off its target. Longer transitions make the changes smoother and more reachable.",
                    "evidence": [f"{n['date']}: ±{fmt_gap(n['tracking_c'], units)}" for n in done if n.get("tracking_c") is not None],
                    "proposal": {"rhythm_id": rh.id, "phase_id": "deep", "field": "ramp_min", "from": phase["deep"].ramp_min, "to": phase["deep"].ramp_min + 10}})

    # 6. restless
    ups = [n.get("times_up", 0) for n in done]
    if len(ups) >= 3 and mean(ups) >= 2:
        out.append({"id": "up_often", "severity": "info", "title": f"Up about {mean(ups):.1f} times a night",
                    "detail": "Your side holds its temperature for 30 minutes when you get up. If you often stay up longer, raise the hold in Studio → Rhythm.",
                    "evidence": [f"{n['date']}: {n.get('times_up', 0)}" for n in done], "proposal": None})

    # one change per phase/field: later insights that propose the same thing point at the first
    seen: dict[tuple, str] = {}
    for i in out:
        pr = i.get("proposal")
        if not pr:
            continue
        key = (pr["phase_id"], pr["field"])
        if key in seen:
            i["proposal"] = None
            i["detail"] += f" (Same change as “{seen[key]}”.)"
        else:
            seen[key] = i["title"]
    if not out:
        out.append({"id": "all_good", "severity": "good", "title": "Nights look steady",
                    "detail": "No changes suggested. Your rhythm, check-ins and the bed's tracking line up.", "evidence": [], "proposal": None})
    return out


_FIELDS = {"temp_c": (10.0, 45.0), "ramp_min": (0, 120)}       # what a proposal may change, and its limits


def apply_proposal(person: Person, prop: dict) -> str:
    field = prop.get("field")
    if field not in _FIELDS:
        raise KeyError(f"can't change {field!r}")
    lo, hi = _FIELDS[field]
    try:
        to = float(prop["to"])
    except (KeyError, TypeError, ValueError):
        raise KeyError("missing or invalid value")
    if not lo <= to <= hi:
        raise KeyError(f"{field} must be between {lo} and {hi}")
    prop = {**prop, "to": int(to) if field == "ramp_min" else round(to, 2)}
    for rh in person.rhythms:
        if rh.id == prop["rhythm_id"]:
            for ph in rh.phases:
                if ph.id == prop["phase_id"]:
                    setattr(ph, prop["field"], prop["to"])
                    return f"{rh.name} · {ph.name}: {prop['field']} {prop.get('from')} → {prop['to']}"
    raise KeyError("phase not found")
