"""Context about each person beyond the bed: are they home, on the way home, out at an evening event, and are
the bedroom lights on. Built from the context devices in Studio → Devices (phone on Wi-Fi, phone location,
calendar, bedroom light) and used by the night engine and the tuning guide.

Rules
- Home wins over away when two devices disagree and one of them is the phone's location: GPS at home is
  definite, while "phone not on Wi-Fi" can just mean the phone is asleep.
- A phone only counts as away after it has explicitly reported "not home" for `away_after_min` (default 20 min).
  A phone that simply goes quiet (flat battery, switched off, app killed) is unknown, never away.
- Heading home needs a steady fall in distance over about ten minutes, bigger than the GPS jitter; with the
  arrival estimate inside `ready_min`, the person counts as home so pre-cool runs and the bed is ready.
- A calendar event keeps the person "busy" until it ends plus the travel time home, even after the calendar
  has moved on to the next event. All-day events don't count.
- No context devices for a person: nothing is known and nothing changes (the manual home switch still works).
- Nothing here can stop a night: getting into bed always starts and keeps the night running.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass

from ..sources import tuning_for

QUIET_UNKNOWN_S = 6 * 3600          # a phone silent this long tells us nothing


@dataclass
class PersonContext:
    home: bool | None = None          # None = unknown
    why: str = ""                     # plain-language reason, for the app and the story
    distance_km: float | None = None
    eta_min: float | None = None      # estimated minutes until home, when heading home
    heading_home: bool = False
    busy_until: float | None = None   # an evening calendar event (plus travel) ends at this time
    event_end: float | None = None
    lights_on: bool | None = None     # bedroom light, household-wide
    early_start: float | None = None  # first event early tomorrow morning (from a calendar feed)
    # from the person's own phone (Android app, "From this phone"): see engine/phone_context.py
    next_alarm: float | None = None
    charging: bool | None = None
    bedtime_mode: bool | None = None
    watch_connected: bool | None = None
    near_dial: str | None = None      # "left"/"right": the phone is lying by that side's dial at night
    phone_seen: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _mine(c, p, mgr=None) -> bool:
    if mgr is not None and getattr(mgr, "quarantined", None) and mgr.quarantined(c):
        return False                                  # acting up (teleporting GPS, flapping phone): set aside
    # a calendar with no person is shared ("both of us"): its evening events count for everyone
    return c.enabled and (c.person == p.id or (c.person is None and (c.zone == p.zone or (c.zone is None and c.role == "calendar"))))


def _wifi_state(store, mgr, c, now: float, invert: bool, away_after_s: float) -> bool | None:
    """True = on the home Wi-Fi recently, False = has said 'not home' for at least away_after_s, None = unknown."""
    latest = mgr.latest.get(c.id) or {}
    pts = sorted(store.samples(c.id, now - QUIET_UNKNOWN_S, now))
    if latest.get("value") is not None and latest.get("ts") and now - latest["ts"] <= QUIET_UNKNOWN_S:
        pts.append((latest["ts"], latest["value"]))
        pts.sort()
    if not pts:
        return None
    at_home = lambda v: (v >= 0.5) != invert        # noqa: E731
    last_t, last_v = pts[-1]
    if at_home(last_v):
        return True if now - last_t <= QUIET_UNKNOWN_S else None
    # its latest word is "not home": since when?
    since = last_t
    for t, v in reversed(pts):
        if at_home(v):
            break
        since = t
    return False if now - since >= away_after_s else True


def _heading(pts: list[tuple[float, float]], radius: float) -> tuple[bool, float | None]:
    """A steady fall in distance over the last ~6-15 minutes, clearly bigger than the GPS jitter.
    Returns (heading home, speed km/h)."""
    if len(pts) < 4 or pts[-1][0] - pts[0][0] < 360:
        return False, None
    t0 = pts[0][0]
    xs = [(t - t0) / 3600 for t, _ in pts]
    ys = [d for _, d in pts]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return False, None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx       # km per hour (negative = closer)
    resid = statistics.pstdev([y - (my + slope * (x - mx)) for x, y in zip(xs, ys)])
    drop = -slope * (xs[-1] - xs[0])
    if slope < -2 and drop >= max(0.5, 3 * resid):
        return True, -slope
    return False, None


def person_context(mgr, store, p, now: float) -> PersonContext:
    ctx = PersonContext()
    if not mgr:
        _phone(ctx, store, p, now)                     # PHONE CONTEXT (phone_context.py)
        return ctx
    events = mgr.__dict__.setdefault("_last_event", {})       # person → (event end, travel s): survives the calendar moving on
    wifi: list[bool | None] = []
    gps_home: bool | None = None
    for c in mgr.configs:
        t = tuning_for(c.kind, c.tuning)
        latest = mgr.latest.get(c.id) or {}
        if c.role == "light" and c.enabled:
            v, ts = latest.get("value"), latest.get("ts")
            if v is not None and ts is not None and now - ts <= 1800:
                on = v >= 0.5
                on = (not on) if t.get("invert") else on
                ctx.lights_on = bool(on) if ctx.lights_on is None else (ctx.lights_on or on)
        if not _mine(c, p, mgr):
            continue
        if c.role == "home":
            wifi.append(_wifi_state(store, mgr, c, now, bool(t.get("invert")), float(t.get("away_after_min", 20)) * 60))
        elif c.role == "location":
            max_age = float(t.get("max_age_s", 1800) or 1800)
            pts = [(ts, v) for ts, v in store.samples(c.id, now - 900, now) if v is not None]
            if latest.get("value") is not None and latest.get("ts") and now - latest["ts"] <= max_age:
                pts.append((latest["ts"], latest["value"]))
            pts = sorted(set(pts))
            if not pts or now - pts[-1][0] > max_age:
                continue
            d = float(pts[-1][1])
            ctx.distance_km = round(d, 2)
            radius = float(t.get("home_radius_km", 0.3))
            if d <= radius:
                gps_home = True
                continue
            gps_home = False
            going, speed = _heading(pts, radius)
            if going and speed:
                ctx.heading_home = True
                speed = max(10.0, min(130.0, speed))
                ctx.eta_min = round(d / speed * 60, 1)
                if ctx.eta_min <= float(t.get("ready_min", 30)):
                    gps_home = True                       # close enough: get the bed ready
                    ctx.why = f"on the way home, about {round(ctx.eta_min)} min out"
        elif c.role == "calendar":
            v = latest.get("value")
            travel = float(t.get("travel_min", 20)) * 60
            ea = (mgr.latest.get(f"{c.id}:early") or {}).get("value")
            if ea and ea > now:
                ctx.early_start = ea if ctx.early_start is None else min(ctx.early_start, ea)
            if v and v > now:
                ctx.event_end = float(v)
                prev = events.get(p.id)
                if not prev or prev[0] < v or prev[0] <= now:
                    events[p.id] = (float(v), travel)
    last = events.get(p.id)
    if last:
        end, travel = last
        if now < end + travel:
            ctx.busy_until = end + travel
            if ctx.event_end is None and now < end:
                ctx.event_end = end
        else:
            events.pop(p.id, None)
    if gps_home is not None:
        ctx.home = gps_home
        if not ctx.why:
            ctx.why = "phone location: home" if gps_home else f"phone location: {ctx.distance_km} km away"
    elif any(x is True for x in wifi):
        ctx.home, ctx.why = True, "phone is on the home Wi-Fi"
    elif wifi and all(x is False for x in wifi):
        ctx.home, ctx.why = False, "phone has been off the home Wi-Fi for a while"
    _phone(ctx, store, p, now)                         # PHONE CONTEXT (phone_context.py): the person's own phone
    return ctx


def _phone(ctx, store, p, now: float) -> None:
    from .phone_context import merge
    try:
        merge(ctx, store, p, now)
    except Exception:  # noqa: BLE001  never let a phone report break the night
        import logging
        logging.getLogger(__name__).exception("phone context merge failed")
