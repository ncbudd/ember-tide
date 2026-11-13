"""The tuning guide: plain recommendations from how nights actually went, using the bed plus any context devices
(bedroom light, calendar, phone at home / location). Same shape as insights (title, detail, evidence, proposal),
so Studio and the phone show them in one list and "Apply" works the same way.

Each rule needs enough nights to be fair (usually 5), leaves out nights that were clearly unusual (late events,
away), and proposes small, reversible changes rounded to 15 minutes.
"""

from __future__ import annotations

import statistics
from datetime import datetime

from .rhythm import pick_rhythm, resolve
from .summary import recent
from ..units import clock_style, fmt_clock


def _hm(ts: float, tz, style: str = "24") -> str:
    return fmt_clock(ts, style, tz)


def _shift(hm: str, minutes: int) -> str:
    h, m = (int(x) for x in hm.split(":"))
    t = (h * 60 + m + minutes) % (24 * 60)
    return f"{t // 60:02d}:{t % 60:02d}"


def _say_hm(hm: str, style: str) -> str:
    """A schedule time ("23:00") in the person's clock style, for text only (proposals keep "HH:MM")."""
    h, m = (int(x) for x in hm.split(":"))
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}" if style == "12" else f"{h:02d}:{m:02d}"


def _round15(x: float) -> int:
    return int(round(x / 15.0) * 15)


def _sources(c, role, p=None):
    mgr = c.sources
    return [s for s in (mgr.configs if mgr else []) if s.role == role and s.enabled
            and (p is None or s.person == p.id or (s.person is None and (s.zone is None or s.zone == p.zone)))]


def _lights_off(c, start: float, end: float) -> float | None:
    """First time the bedroom light went off and stayed off for 20 min, inside [start, end]."""
    for s in _sources(c, "light"):
        pts = c.store.samples(s.id, start, end)
        for i, (t, v) in enumerate(pts):
            if v < 0.5 and (i == 0 or pts[i - 1][1] >= 0.5):
                later = [x for x in pts if t < x[0] <= t + 1200]
                if later and all(x[1] < 0.5 for x in later):
                    return t
    return None


def _ups_with_light(c, start: float, end: float) -> int:
    n = 0
    for s in _sources(c, "light"):
        pts = c.store.samples(s.id, start, end)
        n += sum(1 for i in range(1, len(pts)) if pts[i][1] >= 0.5 > pts[i - 1][1])
    return n


def tuning_guide(c, p, now: float) -> list[dict]:
    tz = c.clock.tz
    ck = clock_style(p.settings)
    nights = [n for n in recent(c.store, p, tz, now, 14) if n.get("has_data") and not n.get("in_progress")]
    out: list[dict] = []
    lr = c.autopilot.learning(p, now) if getattr(c, "autopilot", None) else None
    if lr and not lr["done"] and p.settings.autopilot.mode != "off":
        out.append({"id": "guide-learning", "severity": "info", "kind": "guide",
                    "title": f"Autopilot is learning your nights: {lr['have']} of {lr['need']}",
                    "detail": "For the first week it only watches, to learn what a normal night looks like for you. Suggestions start after that.",
                    "evidence": [], "proposal": None})
    if len(nights) < 3:
        return out
    rows = []
    for n in nights:
        d = datetime.fromisoformat(n["date"]).date()
        rh = pick_rhythm(p.rhythms, d)
        if not rh or not n.get("in_bed_at"):
            continue
        rn = resolve(rh, d, tz)
        hc = sorted((e for e in c.store.events(since=rn.precool - 24 * 3600, limit=3000, person=p.id)
                     if e["kind"] == "home_changed" and e.get("person") == p.id and e["ts"] <= rn.bedtime + 2 * 3600),
                    key=lambda e: e["ts"])
        before = [e for e in hc if e["ts"] <= rn.precool]
        # away if they were already out when pre-condition was due, or left during the evening
        away = bool(before and before[-1].get("home") is False) or any(
            e.get("home") is False for e in hc if e["ts"] > rn.precool)
        late_event = None
        for s in _sources(c, "calendar", p):
            ends = [v for t, v in c.store.samples(s.id, rn.precool - 6 * 3600, rn.bedtime + 4 * 3600) if v and v > rn.bedtime - 1800]
            if ends:
                late_event = max(ends)
        lo = _lights_off(c, rn.bedtime - 3 * 3600, rn.bedtime + 4 * 3600)
        ex_min = sum(v for src in c.store.sample_sources(f"wear:{p.id}:exercise_min:")
                     for t, v in c.store.samples(src, n["in_bed_at"] - 3 * 3600, n["in_bed_at"]))
        caf_mg = sum(v for src in c.store.sample_sources(f"wear:{p.id}:caffeine_mg:")
                     for t, v in c.store.samples(src, n["in_bed_at"] - 8 * 3600, n["in_bed_at"]))
        rows.append({"date": n["date"], "rh": rh, "rn": rn, "in_bed": n["in_bed_at"], "out": n.get("out_at"),
                     "settle": n.get("time_to_settle_min"), "late_exercise": ex_min, "late_caffeine": caf_mg,
                     "late_event": late_event, "away": away, "lights_off": lo,
                     "ups_light": _ups_with_light(c, n["in_bed_at"] + 1800, (n.get("out_at") or rn.wake) - 600)})
    if len(rows) < 3:
        return out
    typical = [r for r in rows if not r["late_event"] and not r["away"]]

    # 1. bedtime set too early / too late, and 2. getting up well before / after wake: per rhythm (weeknights and
    # weekends can drift differently, and must not cancel each other out)
    for rid in sorted({r["rh"].id for r in typical}):
        mine = [r for r in typical if r["rh"].id == rid]
        rh = mine[-1]["rh"]
        offs = [(r["in_bed"] - r["rn"].bedtime) / 60 for r in mine]
        need = min(5, max(3, 2 * len(rh.days or [0, 1, 2, 3, 4, 5, 6])))     # a weekend rhythm only gets 2 nights a week
        if len(offs) >= need:
            med = statistics.median(offs)
            if abs(med) >= 30:
                to = _shift(rh.schedule.bedtime, _round15(med))
                out.append({"id": f"guide-bedtime-{rh.id}", "severity": "suggest", "kind": "guide",
                            "title": f"{rh.name}: you usually get into bed {abs(round(med))} min {'after' if med > 0 else 'before'} your set bedtime",
                            "detail": f"On {len(offs)} ordinary nights (late events and nights away left out). Moving bedtime to "
                                      f"{_say_hm(to, ck)} lines up wind-down and pre-condition with when you actually go to bed.",
                            "evidence": [f"{r['date']}: in bed {_hm(r['in_bed'], tz, ck)}" for r in mine][-7:],
                            "proposal": {"rhythm_id": rh.id, "schedule": "bedtime", "from": rh.schedule.bedtime, "to": to}})
        wo = [((r["out"] - r["rn"].wake) / 60, r) for r in mine if r["out"]]
        if len(wo) >= need:
            med = statistics.median(x for x, _ in wo)
            if med <= -30 or med >= 45:
                to = _shift(rh.schedule.wake, _round15(med))
                out.append({"id": f"guide-wake-{rh.id}", "severity": "suggest", "kind": "guide",
                            "title": f"{rh.name}: you usually get up {abs(round(med))} min {'before' if med < 0 else 'after'} your wake time",
                            "detail": f"The warm wake is timed for {_say_hm(rh.schedule.wake, ck)}. Setting wake to {_say_hm(to, ck)} times it for when you really get up.",
                            "evidence": [f"{r['date']}: up {_hm(r['out'], tz, ck)}" for _, r in wo][-7:],
                            "proposal": {"rhythm_id": rh.id, "schedule": "wake", "from": rh.schedule.wake, "to": to}})

    # 3. lights: reading in bed, getting up in the night
    lit = [r for r in typical if r["lights_off"]]
    if len(lit) >= 4:
        gap = statistics.median((r["lights_off"] - r["in_bed"]) / 60 for r in lit)
        if gap >= 20:
            out.append({"id": "guide-lights-reading", "severity": "info", "kind": "guide",
                        "title": f"Lights usually go off about {round(gap)} min after you get into bed",
                        "detail": "Reading or winding down in bed is fine: the Fall asleep phase starts when you get in. If it feels too "
                                  "cool while you read, make Fall asleep a little longer or one degree warmer.",
                        "evidence": [f"{r['date']}: in bed {_hm(r['in_bed'], tz, ck)}, lights off {_hm(r['lights_off'], tz, ck)}" for r in lit][-5:],
                        "proposal": None})
    ups = [r["ups_light"] for r in rows]
    if len(ups) >= 5 and statistics.mean(ups) >= 1.5:
        out.append({"id": "guide-lights-ups", "severity": "info", "kind": "guide",
                    "title": f"The bedroom light comes on about {statistics.mean(ups):.1f} times a night",
                    "detail": "Gets-up in the night. Your side holds its temperature while you're up; if you're often up longer than "
                              f"{rows[-1]['rh'].schedule.absent_hold_min} min, raise 'out of bed hold' in the rhythm.",
                    "evidence": [f"{r['date']}: {r['ups_light']}" for r in rows][-7:], "proposal": None})

    # 4. late events and nights away
    late = [r for r in rows if r["late_event"]]
    if late:
        delay = statistics.median((r["in_bed"] - r["rn"].bedtime) / 60 for r in late)
        out.append({"id": "guide-late-events", "severity": "good", "kind": "guide",
                    "title": f"{len(late)} late evening event{'s' if len(late) > 1 else ''} in two weeks",
                    "detail": f"On those nights you got to bed about {round(delay)} min after bedtime. The schedule fallback waited for the "
                              "event to end, so the bed didn't run for nobody.",
                    "evidence": [f"{r['date']}: event until {_hm(r['late_event'], tz, ck)}" for r in late], "proposal": None})
    away = [r for r in rows if r["away"]]
    if away:
        out.append({"id": "guide-away", "severity": "good", "kind": "guide",
                    "title": f"Pre-condition held on {len(away)} evening{'s' if len(away) > 1 else ''} while you were out",
                    "detail": "Your phone showed you away, so your side waited and started again as you headed home.",
                    "evidence": [r["date"] for r in away], "proposal": None})

    # 5. your day (Health Connect day data): a workout late in the evening, caffeine in the afternoon
    for key, label, thr, what in (("late_exercise", "a workout in the 3 hours before bed", 15, "exercise"),
                                  ("late_caffeine", "caffeine in the 8 hours before bed", 50, "caffeine")):
        with_ = [r for r in rows if r[key] >= thr and r["settle"] is not None]
        without = [r for r in rows if r[key] < thr and r["settle"] is not None]
        if len(with_) >= 2 and len(without) >= 2:
            a_, b_ = statistics.median(r["settle"] for r in with_), statistics.median(r["settle"] for r in without)
            if a_ - b_ >= 10:
                tip = ("A cooler Fall asleep phase helps the body shed the heat from exercise; or try finishing workouts earlier."
                       if what == "exercise" else "Caffeine lasts about 5 hours; an earlier last coffee is the usual fix.")
                out.append({"id": f"guide-{what}", "severity": "info", "kind": "guide",
                            "title": f"Slower to settle after {label}",
                            "detail": f"About {round(a_)} min to settle on those {len(with_)} nights, against {round(b_)} min otherwise. {tip}",
                            "evidence": [f"{r['date']}: {round(r[key])} {'min' if what == 'exercise' else 'mg'}, settled in {r['settle']} min" for r in with_][-5:],
                            "proposal": None})

    # 6. devices that would help
    spread = statistics.pstdev([(r["in_bed"] - r["rn"].bedtime) / 60 for r in rows]) if len(rows) >= 5 else 0
    if spread >= 45 and not (_sources(c, "home", p) or _sources(c, "location", p) or _sources(c, "calendar", p)):
        out.append({"id": "guide-add-context", "severity": "info", "kind": "guide",
                    "title": f"Bedtimes vary a lot (about ±{round(spread)} min)",
                    "detail": "Adding your phone (home Wi-Fi or location) or your calendar in Studio → Devices lets pre-condition wait while "
                              "you're out and start as you head home.", "evidence": [], "proposal": None})
    if not _sources(c, "room_temp"):
        out.append({"id": "guide-add-room", "severity": "info", "kind": "guide", "title": "No bedroom temperature sensor",
                    "detail": "A room sensor lets Autopilot tell a warm bedroom from a rhythm that's set too warm.", "evidence": [], "proposal": None})
    return out


def apply_schedule(person, prop: dict) -> str | None:
    """Apply a schedule proposal ({"rhythm_id", "schedule": "bedtime"|"wake", "to": "HH:MM"}). None if not one."""
    field = prop.get("schedule")
    if field not in ("bedtime", "wake"):
        return None
    import re
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(prop.get("to", ""))):
        raise KeyError("time must be HH:MM")
    for rh in person.rhythms:
        if rh.id == prop["rhythm_id"]:
            old = getattr(rh.schedule, field)
            setattr(rh.schedule, field, prop["to"])
            return f"{rh.name}: {field} {old} → {prop['to']}"
    raise KeyError("rhythm not found")

