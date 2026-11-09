"""Turn a Rhythm (phases anchored to bedtime / in-bed / clock / wake) into concrete times and temperatures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..models import Rhythm


def night_date(now: datetime) -> date:
    """The evening a night belongs to: before noon counts as the previous evening."""
    return (now - timedelta(days=1)).date() if now.hour < 12 else now.date()


def local_ts(d: date, hm: str, tz: ZoneInfo, evening_base: bool = True) -> float:
    """HH:MM on night `d`. Times before noon belong to the following morning."""
    h, m = map(int, hm.split(":"))
    day = d + timedelta(days=1) if (evening_base and h < 12) else d
    return datetime.combine(day, time(h, m), tz).timestamp()


def pick_rhythm(rhythms: list[Rhythm], d: date) -> Rhythm | None:
    for r in rhythms:
        if d.weekday() in r.days:
            return r
    return rhythms[0] if rhythms else None


@dataclass
class ResolvedPhase:
    id: str
    name: str
    start: float
    temp_c: float
    ramp_s: float
    autopilot: bool


@dataclass
class ResolvedNight:
    date: date
    rhythm_id: str
    bedtime: float
    precool: float
    window_start: float
    window_end: float
    wake: float
    end: float
    in_bed: float | None
    phases: list[ResolvedPhase]


def resolve(rhythm: Rhythm, d: date, tz: ZoneInfo, in_bed: float | None = None, wake: float | None = None) -> ResolvedNight:
    s = rhythm.schedule
    bedtime = local_ts(d, s.bedtime, tz)
    wake_ts = wake or local_ts(d, s.wake, tz)
    anchor_in_bed = in_bed if in_bed is not None else bedtime
    raw = _lay_out(rhythm, d, tz, bedtime, anchor_in_bed, wake_ts)
    if in_bed is not None and in_bed > bedtime:
        _fit_late_start(rhythm, raw, _lay_out(rhythm, d, tz, bedtime, bedtime, wake_ts))
    return ResolvedNight(
        date=d, rhythm_id=rhythm.id, bedtime=bedtime, precool=bedtime - s.precool_lead_min * 60,
        window_start=local_ts(d, s.window_start, tz), window_end=local_ts(d, s.window_end, tz),
        wake=wake_ts, end=wake_ts + s.after_wake_min * 60, in_bed=in_bed, phases=raw,
    )


def _lay_out(rhythm: Rhythm, d: date, tz: ZoneInfo, bedtime: float, in_bed: float, wake_ts: float) -> list[ResolvedPhase]:
    raw: list[ResolvedPhase] = []
    for p in rhythm.phases:
        a = p.start
        if a.kind == "bedtime":
            base = bedtime
        elif a.kind == "in_bed":
            base = in_bed
        elif a.kind == "wake":
            base = wake_ts
        else:
            base = local_ts(d, a.time or "03:00", tz)
        raw.append(ResolvedPhase(p.id, p.name, base + a.offset_min * 60, p.temp_c, p.ramp_min * 60, p.autopilot))
    _keep_order(raw)
    return raw


def _keep_order(raw: list[ResolvedPhase]) -> None:
    # keep phases in their defined order: pull earlier phases back if a later one starts sooner
    for i in range(len(raw) - 2, -1, -1):
        raw[i].start = min(raw[i].start, raw[i + 1].start)


FIT_SHARE = 0.5     # a phase left with less than half its fair share of a shorter night: squeeze the night to fit


def _fit_late_start(rhythm: Rhythm, raw: list[ResolvedPhase], usual: list[ResolvedPhase]) -> bool:
    """Getting in much later than usual (a 3 AM arrival with a 7 AM wake): laid out as usual, the clock-anchored
    phases (e.g. "Late night" at 03:00) would swallow the in-bed ones, so "Fall asleep" and "Deep" would be skipped
    and the night would jump straight to "Late night". When any phase between getting in and the warm wake would get
    less than half its fair share of the shorter night, those phases are squeezed to fit instead, each keeping its
    usual proportion; the warm wake stays where it is. A night that starts only a little late is left as it was.
    Changes `raw` in place; True when it squeezed."""
    kinds = [p.start.kind for p in rhythm.phases]
    if "in_bed" not in kinds:
        return False
    first = kinds.index("in_bed")
    last = next((k for k in range(first + 1, len(kinds)) if kinds[k] == "wake"), len(kinds))
    seq = range(first, last)
    if last == first + 1 and last < len(kinds):
        return False                                 # only one phase between getting in and the wake: nothing to share
    end_now = raw[last].start if last < len(raw) else None
    end_usual = usual[last].start if last < len(usual) else None
    if end_now is None or end_usual is None:
        return False                                 # no warm-wake phase to fit before: leave the rhythm alone
    span, span_usual = end_now - raw[first].start, end_usual - usual[first].start
    if span <= 0 or span_usual <= 0 or span >= span_usual:
        return False
    scale = span / span_usual
    fitted = [raw[first].start + (usual[i].start - usual[first].start) * scale for i in seq] + [end_now]
    laid = [raw[i].start for i in seq] + [end_now]
    if all(laid[j + 1] - laid[j] >= FIT_SHARE * (fitted[j + 1] - fitted[j]) for j in range(len(seq))):
        return False
    for j, i in enumerate(seq):
        raw[i].start = fitted[j]
    _keep_order(raw)
    return True


def fitted_late(rhythm: Rhythm, d: date, tz: ZoneInfo, in_bed: float, wake: float | None = None) -> bool:
    """Was this night's rhythm squeezed to fit a late start (see _fit_late_start)?"""
    bedtime = local_ts(d, rhythm.schedule.bedtime, tz)
    if in_bed <= bedtime:
        return False
    wake_ts = wake or local_ts(d, rhythm.schedule.wake, tz)
    raw = _lay_out(rhythm, d, tz, bedtime, in_bed, wake_ts)
    return _fit_late_start(rhythm, raw, _lay_out(rhythm, d, tz, bedtime, bedtime, wake_ts))


def target_at(n: ResolvedNight, t: float) -> tuple[float | None, ResolvedPhase | None]:
    ph = n.phases
    if not ph or t < ph[0].start or t >= n.end:
        return None, None
    i = max(k for k, p in enumerate(ph) if p.start <= t)
    cur = ph[i]
    prev = ph[i - 1].temp_c if i > 0 else cur.temp_c
    frac = 1.0 if cur.ramp_s <= 0 else min(1.0, (t - cur.start) / cur.ramp_s)
    return prev + (cur.temp_c - prev) * frac, cur


def next_phase(n: ResolvedNight, t: float) -> ResolvedPhase | None:
    for p in n.phases:
        if p.start > t:
            return p
    return None


def curve(n: ResolvedNight, step_s: float = 300) -> list[tuple[float, float]]:
    if not n.phases:
        return []
    out, t = [], n.phases[0].start
    while t < n.end:
        v, _ = target_at(n, t)
        if v is not None:
            out.append((t, round(v, 2)))
        t += step_s
    v, _ = target_at(n, n.end - 1)
    if v is not None:
        out.append((n.end - 1, round(v, 2)))
    return out
