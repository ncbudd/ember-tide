"""Autopilot, feedback and wearable data endpoints."""

from __future__ import annotations

import asyncio
import json
import statistics
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .engine.autopilot import ALL_SIGNALS, LABELS, THRESHOLD
from .engine.rhythm import night_date, pick_rhythm, resolve
from .engine.summary import night_window, summarize
from .engine.wearables import night_devices
from . import voice
from .models import AutopilotPolicy
from .units import clock_style, fmt_gap, fmt_temp, language_line, season_words


class FeelBody(BaseModel):
    feel: Literal["too_hot", "too_cold", "just_right"]
    note: str | None = Field(None, max_length=1000)


class ReplayBody(BaseModel):
    date: str
    policy: dict | None = None            # overrides on top of the saved settings


class WearableBody(BaseModel):
    device: str = Field(min_length=1, max_length=60)          # e.g. "Pixel Watch", "Oura"
    # night signals, plus day context for the tuning guide (evening exercise, late caffeine) and body weight
    metric: Literal["skin_temp", "hr", "hrv", "resp_rate", "spo2", "resting_hr", "body_temp", "steps", "active_kcal",
                    "exercise_min", "caffeine_mg", "weight_kg", "temp_dev", "readiness", "temp_trend", "sleep_score", "night_breath"]
    unit: str | None = None                                    # "C" or "F" for skin_temp
    relative: bool = False                                     # Oura reports skin temperature as a deviation
    points: list[list] = Field(max_length=50000)               # [time, value]; time = seconds, ms, or ISO string
    align: Literal["auto", "check", "off"] = "auto"            # auto: fix a confident clock shift; check: report only
    shift_s: float = Field(0, ge=-86400, le=86400)             # a manual correction, if you know it
    # where it came from: "android" (Health Connect), "ios" (Apple Health, the iPhone app), "server" (a cloud
    # connection such as Oura), or None (a script). Recorded in the import log; the data is treated the same.
    platform: Literal["android", "ios", "server", "web"] | None = None

    @field_validator("device")
    @classmethod
    def _device_named(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("the device needs a name")
        return v


class DismissBody(BaseModel):
    forever: bool = False


class WearRepairBody(BaseModel):
    apply: bool = False                   # False: only list what would be repaired


class ReviewBody(BaseModel):
    date: str | None = None
    question: str | None = Field(None, max_length=1000)


REVIEW_SYSTEM = """You review one night for the Autopilot of Ember & Tide, a dual-zone water-cooled mattress topper.
You get JSON: the person's rhythm (°C), the night's sensor summary (estimates, not medical), what Autopilot noticed
(signals: restlessness, heart rate rise, skin temperature change, the bed vs plan) and what it did or would have done,
the person's own "too hot / too cold" taps, their morning check-in and anything they wrote about how they feel.
Interpret their own words carefully; they matter most. Assume nothing about where the person lives, their culture,
work week or holidays: only what the context says. Temperatures in the context are already written in the person's own
unit (person.units); use that unit and never convert or mention the other one. Dates are ISO and times are 24-hour;
write times the way person.clock says ("12" = 1:30 PM, "24" = 13:30).
How to read the numbers (get these right):
- "planned_settings" are the temperatures the person CHOSE for each phase. The bed follows them on purpose: a rise or
  fall from one phase to the next is the plan doing its job (e.g. warming toward wake), never the bed "overheating" or
  "overshooting". Only "bed_vs_plan" (tracking) says whether the bed actually reached its setting.
- These are water/pad settings, not body or room temperatures. How warm a setting feels depends on the room and the
  time of year: in a cold room or cold weather a person who runs warm can still choose fairly warm settings and be comfortable.
  Always weigh "room" (the bedroom's measured temperature) and "pad_vs_setting" (whether the pad reached its setting)
  before calling a setting too warm or too cool, and mention the room when it matters.
- "runs" (warm/cold) is only a general description of the person. It is NOT evidence about this night.
- Evidence about this night is only: their taps, their flashes, their own changes, their morning check-in, and what
  Autopilot noticed. If "evidence_tonight" is false, there was no sign of discomfort: say the night looked comfortable
  for temperature and do NOT recommend a rhythm change (you may mention one thing to watch).
Reply in plain language:
1. How the night went for temperature, in two or three sentences.
2. What you'd change in the rhythm, if anything, and why, pointing at the evidence.
3. What Autopilot found: sum up "autopilot" (its overnight signals, what it noticed, its pending suggestions, whether
   it's still learning) in a sentence or two, say whether you agree with its suggestions and why, and anything it
   should do differently (e.g. more or less sensitive, a signal to trust less).
Never give medical advice. Only use facts that are in the context; never invent taps, data or numbers.
Only if the evidence clearly supports a change, add ONE fenced block (never as an example or placeholder):
```proposal
{"rhythm_id": "...", "phase_id": "...", "field": "temp_c", "to": <number>}
```
Keep a change within ±1.5 °C. Numbers named *_c are Celsius; "to" in a proposal is Celsius too (the only
place °C appears when the person's unit is °F)."""


def build_autopilot_router(state) -> APIRouter:
    r = APIRouter(prefix="/api")

    def ctl():
        return state.controller

    def person(pid: str):
        try:
            return ctl().person(pid)
        except KeyError:
            raise HTTPException(404, f"unknown person {pid}")

    def night_of(p, d: str | None):
        c = ctl()
        now = c.clock.now_ts()
        dd = _day(d) if d else night_date(datetime.fromtimestamp(now, c.clock.tz))
        win = night_window(p, dd, c.clock.tz, now)
        if not win:
            raise HTTPException(404, "no rhythm that night")
        rh = pick_rhythm(p.rhythms, dd)
        n = resolve(rh, dd, c.clock.tz) if rh else None
        return dd, win, n

    @r.get("/autopilot/meta")
    async def meta():
        return {"signals": [{"id": s, "label": LABELS[s]} for s in ALL_SIGNALS], "sensitivity": THRESHOLD,
                "ai": _ai_on(_eff(state)), "model": _ai_model(_eff(state))}

    @r.get("/people/{pid}/autopilot")
    async def status(pid: str):
        p = person(pid)
        c = ctl()
        now = c.clock.now_ts()
        tonight = c.autopilot.tonight(pid, now)
        e = c.engine_for(pid)
        live = None
        if e.night and e.s.in_bed:
            from .engine.autopilot import evaluate
            w = c.autopilot.window(p, now, since=e.s.in_bed - 600)
            live = evaluate(w, p.settings.autopilot, e.s.target_c, tonight).to_dict()
        recent = [r for r in c.store.history(p.zone, now - 600, now, person=p.id) if r.get("occupied") == 1]
        sk = c.autopilot.skin(p, now - 900, now)
        beds = [r["measured"] for r in recent if r.get("measured") is not None]
        hrs = [r["hr"] for r in recent if r.get("hr")]
        current = {"bed_c": statistics.median(beds) if beds else None, "hr": statistics.median(hrs) if hrs else None,
                   "skin": statistics.median([v for _, v in sk]) if sk else None}     # for the normal-range band in Studio
        return {"policy": p.settings.autopilot.model_dump(), "state": e.s.state, "live": live, "current": current,
                "learning": c.autopilot.learning(p, now), "baseline": c.autopilot.baseline(p, now),
                "tonight": sorted(tonight, key=lambda x: x["ts"]),
                "has_skin": bool(c.autopilot.skin(p, now - 7 * 86400, now)),
                "wearables": c.autopilot.wearable_status(p, now)}

    @r.get("/people/{pid}/autopilot/night")
    async def night(pid: str, date: str | None = None):
        """Everything for the Autopilot timeline: plan/bed/HR rows, the signal series, decisions, feedback."""
        p = person(pid)
        c = ctl()
        dd, (start, end), n = night_of(p, date)
        rows = c.store.history(p.zone, start, end, step_s=60, person=p.id)
        series = {}
        for k in ("score", "restless", "hr_rise", "skin_delta", "pad_over"):
            series[k] = c.store.samples(f"ap:{pid}:{k}", start, end)
        evs = [x for x in c.store.events(since=start, limit=3000, person=pid) if x["ts"] <= end and x.get("person") == pid]
        fb = c.store.get(f"feedback:{pid}:{dd.isoformat()}")
        room, hum = c.autopilot.room(start, end)
        return {"date": dd.isoformat(), "start": start, "end": end, "wake": n.wake if n else None,
                "room": room[::max(1, len(room) // 400)], "humidity": hum[::max(1, len(hum) // 400)],
                "recheck": c.store.get(f"ap_recheck:{pid}:{dd.isoformat()}"),
                "phases": _actual_phases(rows, n),
                "rows": [{k: x[k] for k in ("ts", "target", "measured", "hr", "status", "occupied", "phase")} for x in rows],
                "skin": c.autopilot.skin(p, start, end), "series": series,
                "decisions": sorted([x for x in evs if x["kind"] == "autopilot"], key=lambda x: x["ts"]),
                "feel": sorted([x for x in evs if x["kind"] == "feel"], key=lambda x: x["ts"]),
                "flashes": sorted([x for x in evs if x["kind"] in ("hot_flash", "cold_flash")], key=lambda x: x["ts"]),
                "manual": sorted([x for x in evs if x["kind"] == "adjust" and x.get("source") != "autopilot"], key=lambda x: x["ts"]),
                "feedback": fb}

    @r.post("/people/{pid}/autopilot/replay")
    async def replay(pid: str, b: ReplayBody):
        p = person(pid)
        c = ctl()
        _, (start, end), n = night_of(p, b.date)
        pol = AutopilotPolicy.model_validate({**p.settings.autopilot.model_dump(), **(b.policy or {}), "mode": "suggest"})
        out = c.autopilot.replay(p, start, end, pol, wake=n.wake if n else None)
        return {"decisions": out, "policy": pol.model_dump(),
                "summary": dict(Counter(d["status"] for d in out))}

    @r.post("/people/{pid}/feel")
    async def feel(pid: str, b: FeelBody):
        p = person(pid)
        c = ctl()
        e = c.engine_for(p.id)
        now = c.clock.now_ts()
        z = c.driver.latest().zones.get(p.zone) if c.driver.latest() else None
        c.store.event(now, "feel", p.id, "app", feel=b.feel, note=b.note, phase=e.s.phase_id, target_c=e.s.target_c,
                      measured_c=z.measured_c if z else None)
        return {"ok": True, "logged": b.feel}

    @r.post("/people/{pid}/wearable")
    async def wearable(pid: str, b: WearableBody):
        """Import wearable samples (Health Connect, Oura, a script), lined up with the bed before they're stored."""
        p = person(pid)
        c = ctl()
        if b.metric == "weight_kg":                  # also updates the sources' calibration, which the controller reads: stay on the loop
            return _import_and_recheck(c, p, b, lock=False)
        # the import and up to 7 hindsight replays are long, blocking work: keep them off the event loop (the controller
        # tick and the WebSocket). They only read engine state and write through the Store, which has its own lock.
        return await asyncio.to_thread(_import_and_recheck, c, p, b)

    @r.get("/people/{pid}/imports")
    async def imports(pid: str):
        person(pid)
        return ctl().store.get(f"imports:{pid}", [])

    @r.get("/people/{pid}/wearable/nights")
    async def wearable_nights(pid: str, days: int = 7):
        """Recent nights: how much of each the watch or ring covered, whether its stages arrived, what's missing."""
        from .engine.wearables import night_coverage
        p = person(pid)
        c = ctl()
        return await asyncio.to_thread(night_coverage, c, p, c.clock.now_ts(), min(14, max(1, days)))

    @r.post("/people/{pid}/wearable/repair")
    async def wearable_repair(pid: str, b: WearRepairBody):
        """Undo heart-rate clock shifts that older versions applied on too little evidence (wear_repair.py). Lists
        by default; {"apply": true} carries it out. Safe to run more than once."""
        from . import wear_repair
        p = person(pid)
        c = ctl()
        def work() -> dict:
            with IMPORT_LOCK:                 # not while an import is writing the same readings
                return wear_repair.repair(c.store, p.id, c.clock.tz, c.clock.now_ts(), apply=b.apply)
        out = await asyncio.to_thread(work)
        if b.apply:
            for k in ("_nights_cache", "_base_cache"):
                c.autopilot.__dict__.get(k, {}).pop(p.id, None)
        return out

    @r.post("/people/{pid}/autopilot/recheck")
    async def recheck(pid: str, b: ReplayBody):
        return _recheck(ctl(), person(pid), b.date)

    @r.get("/people/{pid}/autopilot/trends")
    async def trends(pid: str, days: int = 30):
        return _trends(ctl(), person(pid), min(90, max(7, days)))

    @r.get("/people/{pid}/autopilot/suggestions")
    async def suggestions(pid: str, all: bool = False):
        """Morning suggestions (your own changes from last night, and patterns across the last 7 nights), shown on the
        person's chosen cadence. `all=true` (Studio) shows everything that's waiting regardless of cadence."""
        p = person(pid)
        return _release(ctl(), p, _suggest(ctl(), p), show_all=all)

    @r.post("/people/{pid}/autopilot/suggestions/{sid}/dismiss")
    async def dismiss(pid: str, sid: str, b: DismissBody):
        p = person(pid)
        c = ctl()
        if b.forever and sid.startswith("manual-"):
            p.settings.autopilot.suggest_manual = False
            c.reload_household(c.household)
            return {"ok": True, "manual_suggestions": "off"}
        d = c.store.get(f"ap_dismissed:{pid}", {})
        key = "-".join(sid.split("-")[:3]) if b.forever and sid.startswith("ap-") else sid    # ap-<phase>-<direction>
        d[key] = {"ts": c.clock.now_ts(), "forever": b.forever}
        c.store.put(f"ap_dismissed:{pid}", d)
        return {"ok": True}

    @r.post("/people/{pid}/autopilot/recalibrate")
    async def recalibrate(pid: str):
        """Start over: forget this person's learned normal range, trust settings, Autopilot decisions and suggestion
        history, recheck results and learning-night count, then learn again for the calibration nights. Sleep history,
        rhythms, other settings and other people are untouched. (Owner, or the person themselves: see auth.authorize.)"""
        p = person(pid)
        return _recalibrate(ctl(), p)

    @r.post("/people/{pid}/autopilot/ai-review")
    async def ai_review(pid: str, b: ReviewBody):
        p = person(pid)
        return await _review(state, p, b.date, b.question)

    @r.get("/people/{pid}/autopilot/ai-review")
    async def last_review(pid: str, date: str | None = None):
        p = person(pid)
        dd, _, _ = night_of(p, date)
        return ctl().store.get(f"ai_review:{pid}:{dd.isoformat()}") or {}

    @r.get("/people/{pid}/autopilot/ai-review/last")
    async def last_night_review(pid: str):
        """The stored review of the night that just ended (for the Tonight page), or {}. Last night is the one that
        began yesterday evening, whatever the hour today; Tonight hides the card itself once the next night starts."""
        p = person(pid)
        c = ctl()
        now = datetime.fromtimestamp(c.clock.now_ts(), c.clock.tz)
        dd = night_date(now) - timedelta(days=1) if now.hour >= 12 else night_date(now)
        rv = c.store.get(f"ai_review:{pid}:{dd.isoformat()}")
        if not rv:
            return {}
        return {**rv, "dismissed": bool(c.store.get(f"ai_review_dismissed:{pid}:{dd.isoformat()}"))}

    @r.post("/people/{pid}/autopilot/ai-review/dismiss")
    async def dismiss_review(pid: str, b: ReviewBody):
        """Hide one night's review card on Tonight (kept for Autopilot's own page); synced across this person's devices."""
        person(pid)
        dd = _day(b.date) if b.date else None
        if not dd:
            raise HTTPException(422, "which night?")
        ctl().store.put(f"ai_review_dismissed:{pid}:{dd.isoformat()}", ctl().clock.now_ts())
        return {"ok": True}

    return r


def _eff(state):
    from . import ai
    return ai.effective(state.cfg, state.controller.store)


def _ai_on(cfg) -> bool:
    from . import ai
    return ai.configured(cfg)


def _ai_model(cfg) -> str | None:
    from . import ai
    return ai.model_name(cfg) if ai.configured(cfg) else None


def _actual_phases(rows: list[dict], n) -> list[dict]:
    """Where each phase really began that night (from what was recorded), falling back to the schedule."""
    names = {ph.id: (ph.name, ph.temp_c) for ph in (n.phases if n else [])}
    out, last = [], None
    for r in rows:
        ph = r.get("phase")
        if ph and ph != last and ph in names:
            out.append({"id": ph, "name": names[ph][0], "start": r["ts"], "temp_c": names[ph][1]})
        last = ph or last
    if out:
        return out
    return [{"id": ph.id, "name": ph.name, "start": ph.start, "temp_c": ph.temp_c} for ph in (n.phases if n else [])]


IMPORT_LOCK = threading.Lock()          # imports read-modify-write the same keys (import log, learned offsets): one at a time


def _import_and_recheck(c, p, b, lock: bool = True) -> dict:
    if lock:
        IMPORT_LOCK.acquire()
    try:
        report = _import_wearable(c, p, b)
        rechecked = []
        if report["stored"] and b.metric in ("skin_temp", "hr"):
            # new evidence about past nights: re-run Autopilot over each night the samples cover, keep the hindsight view
            for d in report["nights_touched"][-7:]:
                try:
                    rechecked.append(_recheck(c, p, d))
                except HTTPException:
                    pass
        report["rechecked"] = rechecked
        return report
    finally:
        if lock:
            IMPORT_LOCK.release()


def _import_wearable(c, p, b) -> dict:
    from . import align as A
    tz, now = c.clock.tz, c.clock.now_ts()
    device = b.device.strip()[:60]
    src = f"wear:{p.id}:{b.metric}:{device}"
    warnings: list[str] = []
    notes: list[str] = []
    raw = []
    for pt in b.points:
        if not isinstance(pt, list) or len(pt) < 2:
            raw.append(pt)
            continue
        t, v = pt[0], pt[1]
        try:
            v = float(v)
        except (TypeError, ValueError):
            raw.append([t, None])
            continue
        if b.metric == "skin_temp":
            if b.unit and b.unit.upper().startswith("F"):
                v = v * 5 / 9 if b.relative else (v - 32) * 5 / 9
            if b.relative:
                if abs(v) > 5:
                    raw.append([t, None])
                    continue
                v = 34.0 + v                       # deviations ride on a nominal 34 C so deltas still work
        raw.append([t, v])
    shift = float(b.shift_s)
    learned = c.store.get(f"device_offset:{p.id}:{device}")
    if learned is None:                                   # older app versions labelled some devices differently
        for old in {"Health Sync": ["healthsync"]}.get(device, []):
            learned = c.store.get(f"device_offset:{p.id}:{old}")
            if learned is not None:
                break
    if not shift and learned and b.metric != "hr" and b.align == "auto" and learned.get("shift_s"):
        shift = learned["shift_s"]
        notes.append(f"Applied this device's correction ({A.describe_lag(round(shift / 60))}), learned from its heart rate.")
    # 1. normalise WITHOUT the "future" check yet: a clock that runs hours ahead has its night stamped after now, and
    #    those points are exactly what the shift is measured from (up to 13 h ahead is allowed through here)
    # duplicates against what's already stored are checked LAST, after any clock correction (raw times of a re-import
    # can land exactly on earlier, corrected samples)
    pts, rej = A.clean(raw, b.metric, tz, now + 13 * 3600 + 300, set(), shift)
    # readings this device already sent, exactly (same second, same value): a phone re-reads the last two days on every
    # sync so late-arriving data is never missed, and these repeats must neither be stored again nor sway the timing
    # check below. (A re-import of a device whose clock IS off still matches later, after its shift: see step 5.)
    if pts:
        own = {(int(round(t)), round(v, 1)) for t, v in c.store.samples(src, pts[0][0] - 5, pts[-1][0] + 5)}
        if own:
            before = len(pts)
            pts = [(t, v) for t, v in pts if (int(round(t)), round(v, 1)) not in own]
            rej["duplicate"] += before - len(pts)
    # 2. heart rate: measure the shift against each bed night, looking at the import's raw times up to ±13 h around
    #    it (grouping into nights comes AFTER shifting, so a 13 h shift can't split one night in two). Phone data
    #    carries the phone's clock, so the times are kept as recorded unless the evidence for a shift is strong
    #    (align.judge); a short or patchy import only gets a plain note, never a warning.
    bed_lag: dict[str, dict] = {}
    verdicts: list[tuple[str, int]] = []
    live_ok = False
    if b.metric == "hr" and pts:
        rows_all = c.store.history(p.zone, pts[0][0] - 14 * 3600, pts[-1][0] + 14 * 3600, person=p.id)
        if device != "watch_live":
            live = c.store.samples(f"wear:{p.id}:hr:watch_live", pts[0][0] - 60, pts[-1][0] + 60)
            la = A.live_agreement(pts, live)
            live_ok = bool(la and la["agree"])
            if la:
                notes.append(f"Matches your watch's live heart rate as recorded ({la['pairs']} readings side by side), so its timing is right."
                             if la["agree"] else
                             f"Reads about {la['median_diff']:.0f} bpm away from your watch's live heart rate.")
        for a_, z_ in A.in_bed_spans(rows_all):
            inside = sum(1 for x in pts if a_ - 3600 <= x[0] <= z_ + 3600)
            bed = [(r["ts"], float(r["hr"])) for r in rows_all if r.get("hr") and a_ - 600 <= r["ts"] <= z_ + 600]
            near = [x for x in pts if a_ - 13.5 * 3600 <= x[0] <= z_ + 13.5 * 3600]
            det = A.lag_detail(near, bed)
            verdict = A.judge(det)
            if inside < 10 and verdict != "shift":
                continue                    # this import barely touches that night: nothing to say about it
            d = night_date(datetime.fromtimestamp(a_, tz)).isoformat()
            if det:
                rr = det["r"] if det["distinct"] else min(det["r"], 0.5)
                bed_lag[d] = {"lag_min": det["lag"], "match": round(rr, 2), "overlap_min": det["overlap"], "verdict": verdict}
                verdicts.append((verdict, det["lag"]))
            if verdict == "unlike" and not live_ok:
                warnings.append(f"{d}: this heart rate doesn't follow the bed's (match {det['r']:.2f} over {det['overlap'] // 60} h). "
                                "Is it the right person and device?")
            elif verdict == "weak" and not live_ok:
                notes.append(f"{d}: too few readings next to the bed's heart rate to compare timing, so they were kept at the phone's time.")
    flat = False
    if b.metric == "hr" and len(pts) >= 30 and device != "watch_live":
        import statistics as _st
        hrs = [v for _, v in pts]
        if _st.pstdev(hrs) < 0.6:               # a real heart rate never sits on one number for half an hour or more
            flat = True
            warnings.append("The heart rate hardly changes at all, so the device may not have been on your wrist. "
                            "These readings were kept but won't be used to learn your nights; check that it's worn snugly.")
    applied = 0.0
    if verdicts and b.align != "off":
        lag, why = A.decide_shift(verdicts, live_ok)
        if lag is not None:
            if b.align == "auto":
                applied = lag * 60.0
                pts = [(t + applied, v) for t, v in pts]
                notes.append(f"Shifted by {lag:+d} min: the device was {A.describe_lag(lag)}.")
                c.store.put(f"device_offset:{p.id}:{device}", {"shift_s": applied, "ts": now, "nights": sum(1 for v, _ in verdicts if v == "shift")})
            else:
                warnings.append(f"The device looks {A.describe_lag(lag)}. Import again with align=auto (or shift_s={lag * 60}) to correct it.")
        elif why in ("lines_up", "live", "small"):
            if why != "live":
                notes.append("Heart rate lines up with the bed (within 3 min).")
            if b.align == "auto":
                c.store.put(f"device_offset:{p.id}:{device}", {"shift_s": 0.0, "ts": now, "nights": len(verdicts)})
        elif why == "mixed":
            notes.append("The nights don't agree on a time shift, so the times were kept as recorded.")
    if flat and pts:
        # marked, not deleted: the stretch stays stored and visible, but Autopilot doesn't learn from it and the bed
        # comparison leaves it out (engine/wearables.py mark_flat)
        from .engine.wearables import mark_flat
        mark_flat(c.store, p.id, src, pts[0][0], pts[-1][0], now)
    # 3. now that the clock is right, anything still in the future really is
    if b.metric in ("steps", "active_kcal", "exercise_min", "caffeine_mg"):
        # hourly totals are stamped at the END of their hour, so the current hour's total looks up to an hour ahead
        pts = [(min(t, now), v) if now + 300 < t <= now + 3600 else (t, v) for t, v in pts]
    fut = [x for x in pts if x[0] > now + 300]
    if fut:
        rej["future"] += len(fut)
        pts = [x for x in pts if x[0] <= now + 300]
    # 4. group by night for the report
    nights: dict[str, list] = {}
    for t, v in pts:
        nights.setdefault(night_date(datetime.fromtimestamp(t, tz)).isoformat(), []).append((t, v))
    per_night = []
    for d, ps in sorted(nights.items()):
        rows = c.store.history(p.zone, ps[0][0] - 8 * 3600, ps[-1][0] + 8 * 3600, person=p.id)
        spans = A.in_bed_spans(rows)
        # coverage counts what this device has for the night now, not just this batch (a re-read adds a few readings)
        have = sorted(ps + c.store.samples(src, ps[0][0] - 8 * 3600, ps[-1][0] + 8 * 3600)) if spans else ps
        info = {"date": d, "samples": len(ps), "first": ps[0][0], "last": ps[-1][0], "coverage_in_bed": A.coverage(have, spans),
                "in_bed_minutes": round(sum(y - x for x, y in spans) / 60)}
        if d in bed_lag:
            info.update(bed_lag[d])
        tonight = d == night_date(datetime.fromtimestamp(now, tz)).isoformat()      # still going: coverage means nothing yet
        night_metric = b.metric in ("skin_temp", "hr", "hrv", "resp_rate", "spo2")  # day metrics (steps, caffeine, ...) aren't meant to cover the night
        if (night_metric and len(ps) >= 10 and spans and info["coverage_in_bed"] is not None
                and info["coverage_in_bed"] < 0.5 and not tonight):
            notes.append(f"{d}: covers only {round(info['coverage_in_bed'] * 100)}% of the time in bed so far.")
        if not spans and night_metric and len(ps) >= 10:
            notes.append(f"{d}: the bed has no in-bed record for this night, so coverage can't be checked.")
        per_night.append(info)
    if pts:
        # the same reading can arrive under another device name (Health Connect reports both the watch's app and the
        # syncing app; the live watch path and a later import of that watch): a reading already stored for this metric
        # from ANY device, same value within a couple of seconds, is a duplicate. A copying app (Health Sync copying
        # Samsung Health) gets half a minute either way, as on the phone (HealthMerge.dropCopies), and the original
        # app's reading is the one kept: when the copy got here first, the original replaces it.
        import bisect
        from .engine.wearables import COPY_S, is_copier, same_reading_s
        # (this device's own earlier readings must match to the second: a fast sampler repeats values a second apart)
        stored_before: list[tuple[float, float, str]] = []
        other = 0
        replaced: list[tuple[str, float]] = []
        for s in c.store.sample_sources(f"wear:{p.id}:{b.metric}:"):
            stored_before += [(t, round(v, 1), s) for t, v in c.store.samples(s, pts[0][0] - COPY_S - 5, pts[-1][0] + COPY_S + 5)]
        stored_before.sort()
        before = len(pts)
        kept = []
        for t, v in pts:
            i = bisect.bisect_left(stored_before, (t - COPY_S, -1e9, ""))
            hits = []
            while i < len(stored_before) and stored_before[i][0] <= t + COPY_S:
                st, sv, s = stored_before[i]
                if sv == round(v, 1) and (int(round(st)) == int(round(t)) if s == src else abs(st - t) <= same_reading_s(s, src)):
                    hits.append(stored_before[i])
                i += 1
            copies = [h for h in hits if h[2] != src and is_copier(h[2])]
            if hits and not is_copier(src) and len(copies) == len(hits):
                replaced += [(h[2], h[0]) for h in copies]      # the original arrived after its copy: keep the original
                hits = []
            if hits:
                other += all(h[2] != src for h in hits)
                continue
            kept.append((t, v))
        pts = kept
        rej["duplicate"] += before - len(pts)
        for s, st in replaced:
            c.store.delete_samples_between(s, st - 0.5, st + 0.5)
        if other:
            notes.append(f"{other} readings were already there from another app or device, so they were skipped.")
        if replaced:
            notes.append(f"{len(replaced)} readings copied by another app were replaced by the original app's.")
        if pts:
            # a record revised after it was sent (an hourly total that grew; a provider correcting a reading): the new
            # value replaces the old one for the same device and second instead of being stored next to it.
            own_t = {int(round(t)) for t, _ in c.store.samples(src, pts[0][0] - 1, pts[-1][0] + 1)}
            for t, _ in pts:
                if int(round(t)) in own_t:
                    c.store.delete_samples_between(src, t - 0.5, t + 0.5)
    c.store.samples_many([(t, src, v) for t, v in pts])
    if b.metric == "weight_kg" and pts and c.sources:
        # the newest weight helps a bed scale tell the two sleepers apart (Studio → Devices → calibration)
        c.sources.calibration.person_kg[p.id] = round(pts[-1][1], 1)
        c.sources.save()
    touched = sorted({night_date(datetime.fromtimestamp(t, tz)).isoformat() for t, _ in pts})
    report = {"ok": True, "device": device, "metric": b.metric, "platform": b.platform, "received": len(b.points), "stored": len(pts), "not_worn": flat,
              "rejected": {k: v for k, v in rej.items() if v}, "shift_applied_s": applied or shift,
              "nights": per_night, "nights_touched": touched, "warnings": warnings, "notes": notes, "ts": now}
    log = c.store.get(f"imports:{p.id}", [])
    log.append({k: v for k, v in report.items() if k != "nights"}
               | {"nights": [{k: n.get(k) for k in ("date", "samples", "first", "last", "coverage_in_bed", "lag_min", "match", "verdict")} for n in per_night]})
    c.store.put(f"imports:{p.id}", log[-50:])
    return report


def _room_summary(c, start: float, end: float) -> dict | None:
    t, h = c.autopilot.room(start, end)
    if not t and not h:
        return None
    tv, hv = [v for _, v in t], [v for _, v in h]
    return {"temp_c": {"min": min(tv), "max": max(tv), "avg": round(sum(tv) / len(tv), 1)} if tv else None,
            "humidity": {"min": min(hv), "max": max(hv), "avg": round(sum(hv) / len(hv))} if hv else None}


def _room_words(r: dict | None, units: str = "C") -> dict | str:
    """The bedroom in words a model reads right (bare min/max numbers got ignored), in the person's unit."""
    if not r or not r.get("temp_c"):
        return "no room sensor"
    t = r["temp_c"]
    feel = "cool" if t["avg"] < 20.5 else "comfortable" if t["avg"] < 23.5 else "warm"
    out = {"average": fmt_temp(t["avg"], units), "range": f"{fmt_temp(t['min'], units)} to {fmt_temp(t['max'], units)}",
           "reads_as": f"a {feel} bedroom"}
    if r.get("humidity"):
        out["humidity"] = f"{r['humidity']['avg']}%"
    return out


def _pad_words(gaps: list[float], units: str = "C") -> str:
    """Autopilot's pad-vs-setting samples (measured minus setting, °C) over the night, in words."""
    if len(gaps) < 4:
        return "not known"
    m = sum(gaps) / len(gaps)
    if abs(m) < 0.3:
        return "the pad stayed at its setting"
    return (f"the pad ran about {fmt_gap(m, units)} {'below' if m < 0 else 'above'} its setting on average"
            + (" (in a cool room the pad can't always reach a warm setting: the bed felt cooler than the number says)" if m < 0 else ""))


def _stage_words(st: dict) -> dict | str:
    """Minutes per sleep stage from the first wearable/import that has them (estimates)."""
    for src in st.values():
        mins: dict[str, int] = {}
        for g in src["segments"]:
            mins[g["stage"]] = mins.get(g["stage"], 0) + round((g["end"] - g["start"]) / 60)
        if mins:
            return {"from": src.get("label") or src["source"], "minutes": mins}
    return "no stage data"


def _signal_words(c, p, start: float, end: float) -> dict:
    """What Autopilot measured through the night (its logged signals), as short averages / peaks."""
    out = {}
    for k, label in (("restless", "restlessness 0-1"), ("hr_rise", "heart rate vs settled, bpm"),
                     ("wear_hr_rise", "watch heart rate vs settled, bpm"), ("skin_delta", "skin temperature change, °C"),
                     ("room_vs_usual", "room vs its usual, °C"), ("score", "Autopilot discomfort score")):
        v = [x for _, x in c.store.samples(f"ap:{p.id}:{k}", start, end)]
        if len(v) >= 3:
            out[label] = {"avg": round(sum(v) / len(v), 2), "peak": round(max(v, key=abs), 2)}
    return out or {"note": "Autopilot logged no signals that night"}


def _season(d: date, tz_name: str | None = None, override: str = "auto") -> str:
    """The time of year for the review, so a warm setting in a cold month reads right. The hemisphere comes from the
    household time zone (or its own setting); in the tropics there is no season word, only the month."""
    return season_words(d, tz_name, override)


def _day(d) -> date:
    """A night's date from a request ("2026-11-05"); a clear 400 for anything else."""
    try:
        return date.fromisoformat(str(d)[:10]) if d is not None and len(str(d)) <= 32 else date.fromisoformat("")
    except ValueError:
        raise HTTPException(400, "date must look like 2026-11-05")


def _recheck(c, p, d: str) -> dict:
    """Hindsight: run Autopilot over a finished night with everything known now (e.g. watch data that arrived later)."""
    dd = _day(d)
    win = night_window(p, dd, c.clock.tz, c.clock.now_ts())
    if not win:
        raise HTTPException(404, "no rhythm that night")
    rh = pick_rhythm(p.rhythms, dd)
    n = resolve(rh, dd, c.clock.tz) if rh else None
    pol = AutopilotPolicy.model_validate({**p.settings.autopilot.model_dump(), "mode": "suggest"})
    out = c.autopilot.replay(p, win[0], win[1], pol, wake=n.wake if n else None)
    live = [x for x in c.store.events(since=win[0], limit=2000, person=p.id) if x["kind"] == "autopilot" and x["ts"] <= win[1] and x.get("person") == p.id]
    # what it would have changed in hindsight, and which of those it didn't act on (or note) live within 20 min
    live_acts = [x for x in live if x.get("status") in ("suggested", "applied", "learning")]
    would = [x for x in out if x["status"] in ("suggested", "applied")]
    moments = [{"ts": x["ts"], "direction": x["direction"], "delta_c": x["delta_c"], "phase": x.get("phase"),
                "missed": not any(abs(y["ts"] - x["ts"]) <= 1200 and y.get("direction") == x["direction"] for y in live_acts),
                "why": ", ".join(r["label"].lower() for r in x.get("reasons", [])[:3])} for x in would]
    missed = sum(1 for m in moments if m["missed"])
    rec = {"date": d, "ts": c.clock.now_ts(), "decisions": out, "live_count": len(live), "moments": moments,
           "would_change": len(moments), "missed_live": missed,
           "new_findings": max(0, len(would) - len([x for x in live if x.get("status") in ("suggested", "applied")])),
           "used_skin": bool(c.autopilot.skin(p, win[0], win[1])), "text": recheck_text(len(moments), missed),
           "devices": [x["name"] for x in night_devices(c, p, win[0], win[1])]}
    c.store.put(f"ap_recheck:{p.id}:{d}", rec)
    return {k: v for k, v in rec.items() if k != "decisions"} | {"count": len(out)}


def recheck_text(would: int, missed: int) -> str:
    """The recheck result in plain words."""
    if not would:
        return "Looked again with all the data now: nothing it would have changed."
    text = f"{would} moment{'s' if would != 1 else ''} it would have changed"
    if missed:
        text += f" ({missed} it missed live)"
    return text + "."


def _recalibrate(c, p) -> dict:
    now = c.clock.now_ts()
    pol = p.settings.autopilot
    pol.learn_since, pol.learn_skip = now, False
    pol.trust = AutopilotPolicy().trust
    wiped = {"decisions": c.store.delete_events("autopilot", p.id),
             "signal_samples": c.store.delete_samples(f"ap:{p.id}:"),
             "rechecks": c.store.delete_keys(f"ap_recheck:{p.id}:"),
             "suggestion_history": c.store.delete_keys(f"ap_dismissed:{p.id}") + c.store.delete_keys(f"ap_shown:{p.id}")}
    for k in ("_nights_cache", "_base_cache"):
        c.autopilot.__dict__.get(k, {}).pop(p.id, None)
    c.autopilot._last_logged_decision.pop(p.id, None)
    c.reload_household(c.household)
    c.store.event(now, "autopilot_recalibrated", p.id, "studio")
    return {"ok": True, "wiped": wiped, "learning": c.autopilot.learning(p, now), "policy": pol.model_dump()}


def _trends(c, p, days: int) -> list[dict]:
    """Patterns across many nights: what goes with nights that felt warm or cold. Plain findings with the evidence."""
    from .engine.summary import recent
    now = c.clock.now_ts()
    nights = [n for n in recent(c.store, p, c.clock.tz, now, days) if n.get("has_data") and not n.get("in_progress")]
    out: list[dict] = []
    if len(nights) < 4:
        return [{"id": "trend-few", "title": "Not enough nights yet", "detail": f"{len(nights)} finished night(s) so far. Trends start after about a week.", "evidence": []}]
    warm = [n for n in nights if (n.get("feedback") or {}).get("temp") == "warm"]
    cold = [n for n in nights if (n.get("feedback") or {}).get("temp") == "cold"]
    rated = [n for n in nights if (n.get("feedback") or {}).get("temp")]
    # room temperature on warm vs other nights
    rooms = {}
    for n in nights:
        win = night_window(p, date.fromisoformat(n["date"]), c.clock.tz, now)
        rs = _room_summary(c, *win) if win else None
        if rs and rs.get("temp_c"):
            rooms[n["date"]] = rs["temp_c"]["avg"]
    if rooms and warm:
        w = [rooms[n["date"]] for n in warm if n["date"] in rooms]
        o = [v for d, v in rooms.items() if d not in {n["date"] for n in warm}]
        if w and o and sum(w) / len(w) - sum(o) / len(o) >= 1.0:
            out.append({"id": "trend-room-warm", "title": "Warm nights follow a warm bedroom",
                        "detail": f"On nights you said were warm the room averaged {sum(w) / len(w):.1f} °C, versus {sum(o) / len(o):.1f} °C otherwise. "
                                  "Autopilot already weighs the room; a cooler start on warm evenings would help.",
                        "evidence": [n["date"] for n in warm if n["date"] in rooms]})
    if len(rated) >= 4 and len(warm) / len(rated) >= 0.5:
        out.append({"id": "trend-mostly-warm", "title": f"{len(warm)} of {len(rated)} rated nights felt warm",
                    "detail": "The rhythm as a whole may be a little warm for you. Look at the phase suggestions below.", "evidence": [n["date"] for n in warm]})
    if len(rated) >= 4 and len(cold) / len(rated) >= 0.5:
        out.append({"id": "trend-mostly-cold", "title": f"{len(cold)} of {len(rated)} rated nights felt cold",
                    "detail": "The rhythm as a whole may be a little cool for you.", "evidence": [n["date"] for n in cold]})
    settles = [(n["date"], n.get("time_to_settle_min")) for n in nights if n.get("time_to_settle_min") is not None]
    if len(settles) >= 5:
        avg = sum(s for _, s in settles) / len(settles)
        slow = [d for d, s in settles if s >= avg + 15]
        if slow:
            out.append({"id": "trend-settle", "title": f"Usually settled in {round(avg)} min; slower on {len(slow)} night(s)",
                        "detail": "Compare those nights in the explorer: room temperature and the Fall asleep phase are the usual suspects.", "evidence": slow})
    fl = [(n["date"], n.get("hot_flashes") or 0) for n in nights]
    many = [d for d, k in fl if k >= 2]
    if len(many) >= 2:
        out.append({"id": "trend-flashes", "title": f"Two or more hot flashes on {len(many)} nights",
                    "detail": "If they cluster in one phase, that phase is probably too warm. See the phase suggestions.", "evidence": many})
    return out or [{"id": "trend-none", "title": "No clear trends yet", "detail": f"Looked at {len(nights)} nights. Nothing stands out.", "evidence": []}]


def _release(c, p, items: list[dict], show_all: bool = False) -> dict:
    """Apply dismissals and the person's cadence (every morning / once a week / never)."""
    now = c.clock.now_ts()
    pol = p.settings.autopilot
    dis = c.store.get(f"ap_dismissed:{p.id}", {})
    # "not now" hides that idea (phase + direction) for SNOOZE_NIGHTS nights, even when new nights add evidence;
    # "never" hides that phase + direction for good
    never = {"-".join(k.split("-")[:3]) for k, v in dis.items() if k.startswith("ap-") and (v or {}).get("forever")}
    snoozed: dict[str, float] = {}
    for k, v in dis.items():
        if k.startswith("ap-") and not (v or {}).get("forever") and now - float((v or {}).get("ts") or 0) < SNOOZE_NIGHTS * 86400:
            key = "-".join(k.split("-")[:3])
            snoozed[key] = max(snoozed.get(key, 0), float(v["ts"]) + SNOOZE_NIGHTS * 86400)
    items = [x for x in items if x["id"] not in dis
             and not (x.get("kind") == "pattern" and f"ap-{x.get('phase')}-{x.get('direction')}" in never)
             and not (x.get("kind") == "pattern" and f"ap-{x.get('phase')}-{x.get('direction')}" in snoozed)]
    if not pol.suggest_manual:
        items = [x for x in items if x.get("kind") != "manual"]
    today = datetime.fromtimestamp(now, c.clock.tz)
    nxt = None
    shown = items
    lr = c.autopilot.learning(p, now)
    if show_all:
        pass
    elif not lr["done"]:
        shown = []                                       # calibration week: nothing until it knows your normal
    elif pol.suggest_cadence == "off":
        shown = []
    elif pol.suggest_cadence == "weekly":
        days = (pol.suggest_weekday - today.weekday()) % 7
        if days != 0:
            shown = []
            nxt = (today + timedelta(days=days)).date().isoformat()
    elif pol.suggest_cadence == "monthly":
        nd = _next_monthly(today.date(), pol.suggest_weekday)
        if nd != today.date():
            shown = []
            nxt = nd.isoformat()
    # at most ONE suggestion per cadence period (a morning, a week, a month): once one has been shown, the rest wait
    used = False
    if not show_all and shown:
        period = _period(today.date(), pol.suggest_cadence)
        rec = c.store.get(f"ap_shown:{p.id}") or {}
        if rec.get("period") == period:
            keep = [x for x in shown if x["id"] == rec.get("id")]
            used = not keep
            shown = keep
        else:
            shown = shown[:1]
            c.store.put(f"ap_shown:{p.id}", {"period": period, "id": shown[0]["id"], "ts": now})
    limits = {"one_per": {"weekly": "week", "monthly": "month"}.get(pol.suggest_cadence, "morning"),
              "used_this_period": used, "snooze_nights": SNOOZE_NIGHTS,
              "snoozed": [{"phase": k.split("-")[1], "direction": k.split("-")[2], "until": u}
                          for k, u in sorted(snoozed.items()) if k.count("-") >= 2]}
    return {"items": shown, "waiting": len(items) - len(shown), "cadence": pol.suggest_cadence, "next": nxt, "learning": lr,
            "limits": limits}


SNOOZE_NIGHTS = 14         # "not now" on an idea: it doesn't come back for this many nights


def _period(d: date, cadence: str) -> str:
    if cadence == "weekly":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w}"
    if cadence == "monthly":
        return f"{d.year}-{d.month:02d}"
    return d.isoformat()


def _next_monthly(d: date, weekday: int) -> date:
    """The first `weekday` of the month on or after d's month start; today if today is that day."""
    for month_offset in range(0, 3):
        y, m = d.year + (d.month - 1 + month_offset) // 12, (d.month - 1 + month_offset) % 12 + 1
        first = date(y, m, 1)
        cand = first + timedelta(days=(weekday - first.weekday()) % 7)
        if cand >= d:
            return cand
    return d


def _manual_changes(c, p, evs: list[dict]) -> list[dict]:
    """Changes the person made themselves (phone, dial, Home Assistant) that lasted, from last night, by phase."""
    now = c.clock.now_ts()
    last_night = night_date(datetime.fromtimestamp(now, c.clock.tz))
    # in the morning "last night" is the night that started yesterday evening
    if datetime.fromtimestamp(now, c.clock.tz).hour >= 12:
        last_night = last_night - timedelta(days=1)
    rh = pick_rhythm(p.rhythms, last_night)
    if not rh:
        return []
    final: dict[str, dict] = {}
    for x in sorted(evs, key=lambda e: e["ts"]):
        if night_date(datetime.fromtimestamp(x["ts"], c.clock.tz)) != last_night:
            continue
        if x["kind"] == "adjust" and x.get("source") not in ("autopilot", None) and x.get("phase"):
            final[x["phase"]] = x
        elif x["kind"] == "adjust_cleared":
            final = {}                                     # they went back to the rhythm: nothing to keep
    out = []
    names = {"app": "the phone", "ha": "a dial or Home Assistant", "studio": "Studio", "voice": "voice"}
    for ph_id, x in final.items():
        ph = next((q for q in rh.phases if q.id == ph_id), None)
        if not ph:
            continue
        if x.get("total_temp_c") is not None:
            to = float(x["total_temp_c"])
        elif x.get("total_delta_c"):
            to = ph.temp_c + float(x["total_delta_c"])
        else:
            continue
        diff = to - ph.temp_c
        if abs(diff) < 0.25:
            continue
        to = round(ph.temp_c + max(-2.8, min(2.8, diff)), 2)
        out.append({"id": f"manual-{last_night.isoformat()}-{ph_id}", "kind": "manual", "phase": ph_id, "phase_name": ph.name,
                    "direction": "cooler" if diff < 0 else "warmer", "nights": [last_night.isoformat()],
                    **voice.suggestion_words("manual", "cooler" if diff < 0 else "warmer", {"phase": ph.name, "deg": fmt_gap(diff, p.settings.units),
                                             "source": names.get(x.get("source"), x.get("source") or "the app")}, p, last_night, len(out)),
                    "proposal": {"rhythm_id": rh.id, "phase_id": ph_id, "field": "temp_c", "from": ph.temp_c, "to": to}})
    return out


def _suggest(c, p) -> list[dict]:
    now = c.clock.now_ts()
    monthly = p.settings.autopilot.suggest_cadence == "monthly"
    look, need = (31, 6) if monthly else (8, 3)          # the pattern must show on 3+ of 7 nights (6+ of 30 monthly)
    # (taps are the person's own word: a lot of them means the rhythm is off, never "normal", so they're not discounted
    # by the learned normal range; Autopilot's own records only exist when it saw something outside that range)
    evs = [x for x in c.store.events(since=now - look * 86400, limit=20000, person=p.id) if x.get("person") == p.id]
    by_phase: dict[str, Counter] = defaultdict(Counter)
    nights: dict[str, set] = defaultdict(set)
    for x in evs:
        # evidence = what Autopilot actually suggested or did, plus your own taps. Blocked and learning records are
        # only "it noticed something it wasn't allowed to act on", e.g. in a phase it doesn't touch: never evidence.
        if (x["kind"] == "autopilot" and x.get("status") in ("suggested", "applied")
                and x.get("direction") in ("cooler", "warmer") and x.get("phase")):
            nd = night_date(datetime.fromtimestamp(x["ts"], c.clock.tz)).isoformat()
            by_phase[x["phase"]][x["direction"]] += 1
            nights[(x["phase"], x["direction"])].add(nd)
        if x["kind"] == "feel" and x.get("feel") in ("too_hot", "too_cold") and x.get("phase"):
            d = "cooler" if x["feel"] == "too_hot" else "warmer"
            nd = night_date(datetime.fromtimestamp(x["ts"], c.clock.tz)).isoformat()
            by_phase[x["phase"]][d] += 2
            nights[(x["phase"], d)].add(nd)
        if x["kind"] in ("hot_flash", "cold_flash") and x.get("source") != "autopilot":
            # a flash you pressed is your own word too; the event doesn't carry the phase, so read it off the bed's log
            ph = x.get("phase")
            if not ph:
                near = c.store.history(p.zone, x["ts"] - 180, x["ts"] + 5, person=p.id)
                ph = next((r["phase"] for r in reversed(near) if r.get("phase")), None)
            if ph:
                d = "cooler" if x["kind"] == "hot_flash" else "warmer"
                nd = night_date(datetime.fromtimestamp(x["ts"], c.clock.tz)).isoformat()
                by_phase[ph][d] += 1
                nights[(ph, d)].add(nd)
    rh = pick_rhythm(p.rhythms, night_date(datetime.fromtimestamp(now, c.clock.tz)))
    out = _manual_changes(c, p, evs) if p.settings.autopilot.suggest_manual else []
    for ph, cnt in by_phase.items():
        d, k = cnt.most_common(1)[0]
        n_nights = len(nights[(ph, d)])
        if n_nights < need:
            continue
        phase = next((x for x in (rh.phases if rh else []) if x.id == ph), None)
        if not phase:
            continue
        mag = 0.56 if p.settings.units == "F" else 0.5
        step = -mag if d == "cooler" else mag
        out.append({"id": f"ap-{ph}-{d}-{max(nights[(ph, d)])}", "kind": "pattern", "phase": ph, "phase_name": phase.name, "direction": d, "nights": sorted(nights[(ph, d)]),
                    **voice.suggestion_words("pattern", d, {"phase": phase.name, "n": n_nights, "look": look - 1, "step": "1 °F" if p.settings.units == "F" else "0.5 °C"}, p,
                                             datetime.fromtimestamp(now, c.clock.tz).date(), len(out)),
                    "proposal": {"rhythm_id": rh.id, "phase_id": ph, "field": "temp_c", "from": phase.temp_c, "to": round(phase.temp_c + step, 2)}})
    return out


async def _review(state, p, d: str | None, question: str | None) -> dict:
    from . import ai
    c = state.controller
    now = c.clock.now_ts()
    dd = _day(d) if d else night_date(datetime.fromtimestamp(now, c.clock.tz))
    win = night_window(p, dd, c.clock.tz, now)
    if not win:
        raise HTTPException(404, "no rhythm that night")
    start, end = win
    s = summarize(c.store, p, dd, c.clock.tz, now) or {}
    evs = [x for x in c.store.events(since=start, limit=3000, person=p.id) if x["ts"] <= end and x.get("person") == p.id]
    rh = pick_rhythm(p.rhythms, dd)
    skin = c.autopilot.skin(p, start, end)
    tzname = c.clock.tz

    units = p.settings.units

    def hm(t):
        return datetime.fromtimestamp(t, tzname).strftime("%H:%M")

    def short_reason(x):
        return ", ".join(f"{r['signal']} {r['value']}" for r in (x.get("reasons") or []))

    ap = [x for x in evs if x["kind"] == "autopilot"]
    ctx = {
        "person": {"name": p.name, "runs": {"tide": "warm", "ember": "cold"}.get(p.persona, "varies"), "units": units,
                   "language": p.settings.language, "clock": clock_style(p.settings)},
        "planned_settings": [{"id": ph.id, "name": ph.name, "temp_c": ph.temp_c, "shown_as": fmt_temp(ph.temp_c, units)}
                             for ph in (rh.phases if rh else [])],
        "rhythm_id": rh.id if rh else None,
        "season": _season(dd, getattr(tzname, "key", None), c.household.hemisphere),
        "night": {k: s.get(k) for k in ("in_bed_min", "time_to_settle_min", "times_up", "hr_avg", "hr_low", "hot_flashes", "cold_flashes", "adjustments")},
        "bed_vs_plan": ({"avg_gap_c": s.get("tracking_c"), "means": "how far the bed was from its setting on average (small = it followed the plan)"}
                        if s.get("tracking_c") is not None else "not known"),
        "autopilot_noticed": [f"{hm(x['ts'])} {x.get('phase')}: {x.get('status')} {x.get('direction')} ({short_reason(x)})"
                              + (f" held back: {x['blocked_by'][0]}" if x.get("blocked_by") else "") for x in ap[:12]],
        "their_taps": [f"{hm(x['ts'])} {x.get('phase')}: {x.get('feel')}" + (f' "{x["note"]}"' if x.get("note") else "") for x in evs if x["kind"] == "feel"][:12],
        "flashes": [f"{hm(x['ts'])} {x['kind']}" for x in evs if x["kind"] in ("hot_flash", "cold_flash")][:8],
        "their_changes": [f"{hm(x['ts'])} {x.get('delta_c') or x.get('temp_c')}" for x in evs if x["kind"] == "adjust" and x.get("source") != "autopilot"][:8],
        "morning_checkin": c.store.get(f"feedback:{p.id}:{dd.isoformat()}"),
        "skin_temp_c": {"samples": len(skin), "min": min((v for _, v in skin), default=None), "max": max((v for _, v in skin), default=None)} if skin else "no wearable data",
        "room": _room_words(_room_summary(c, start, end), units),
        "pad_vs_setting": _pad_words([v for _, v in c.store.samples(f"ap:{p.id}:pad_over", start, end)], units),
        "trends_30_nights": [t["title"] for t in _trends(c, p, 30)],
    }
    # more of the night, and Autopilot's own view of it (signals it tracked, what it suggests), for the review to sum up
    ctx["night"].update({"settled_min": s.get("settled_min"), "awake_moving_min": s.get("awake_moving_min"),
                         "up_minutes": s.get("up_minutes"), "fell_asleep": hm(s["fell_asleep_est"]) if s.get("fell_asleep_est") else None,
                         "in_bed_from": hm(s["in_bed_at"]) if s.get("in_bed_at") else None,
                         "out_at": hm(s["out_at"]) if s.get("out_at") else None, "breathing_avg": s.get("br_avg")})
    ctx["sleep_stages"] = _stage_words(c.store.get_stages(p.id, start, end))
    ctx["autopilot"] = {
        "mode": p.settings.autopilot.mode,
        "still_learning": (lambda lr: None if lr["done"] else f"{lr['have']} of {lr['need']} nights")(c.autopilot.learning(p, now)),
        "signals_overnight": _signal_words(c, p, start, end),
        "pending_suggestions": [f"{x['title']}: {x['detail']}" for x in _suggest(c, p)][:5],
    }
    ctx["evidence_tonight"] = bool(ctx["autopilot_noticed"] or ctx["their_taps"] or ctx["morning_checkin"] or ctx["flashes"]
                                   or ctx["their_changes"])
    eff = ai.effective(state.cfg, c.store)
    if not ai.configured(eff):
        sug = _suggest(c, p)
        text = ("No AI model is connected, so this is the built-in summary. "
                + (f"Autopilot noticed {len(ctx['autopilot_noticed'])} moments; you tapped {len(ctx['their_taps'])} times. " if ctx["autopilot_noticed"] or ctx["their_taps"] else "Nothing stood out last night. ")
                + (sug[0]["title"] + ". " + sug[0]["detail"] if sug else ""))
        out = {"answer": text, "proposal": sug[0]["proposal"] if sug else None, "model": None, "ts": now, "date": dd.isoformat()}
    else:
        body_q = question or ("Review this night for temperature and tell me what you would change." if ctx["evidence_tonight"] else
                              "Review this night for temperature. There was no sign of discomfort (no taps, flashes, changes, "
                              "check-in or Autopilot notices), so don't recommend a rhythm change.")
        try:
            text = await ai.complete(eff, REVIEW_SYSTEM,
                                     "Context:\n```json\n" + json.dumps(ctx, default=str)[:14000] + "\n```\n\n" + body_q
                                     + "\n" + language_line(p.settings.language))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"The AI model didn't answer ({type(e).__name__}).")
        answer, proposal = ai.split_proposal(text)
        evidence = ctx["evidence_tonight"]
        # only a real phase of this night's rhythm, within ±1.5 °C, and only with evidence that night (a non-number,
        # an unknown phase or a big jump used to slip through or crash the review)
        proposal = ai.check_rhythm_proposal(p, proposal) if evidence else None
        if proposal and proposal["rhythm_id"] != ctx["rhythm_id"]:
            proposal = None
        out = {"answer": answer, "proposal": proposal, "model": ai.model_name(eff), "ts": now, "date": dd.isoformat()}
    c.store.put(f"ai_review:{p.id}:{dd.isoformat()}", out)
    return out
