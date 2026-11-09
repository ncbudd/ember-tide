"""External data sources: anything that isn't the bed itself. Room sensors, presence radar, load cells,
wearables, lights. Each source has a driver, a role, optionally a side or person, and produces samples.

Drivers:
- ha-entity   : polls a Home Assistant entity (REST, long-lived token). Numbers stay numbers; on/home/true → 1.
- mqtt-topic  : subscribes to a topic; optional dotted JSON path into the payload.
- sim         : simulated signals derived from the simulated bed, so Studio can be exercised without hardware.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
import uuid
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

Role = Literal["room_temp", "room_humidity", "presence", "weight", "wearable_hr", "skin_temp", "wearable_hrv", "light",
               "home", "location", "calendar", "other"]

ROLE_INFO = {
    "room_temp": {"label": "Room temperature", "unit": "°C", "lane": "temp"},
    "room_humidity": {"label": "Room humidity", "unit": "%", "lane": "env"},
    "presence": {"label": "Presence", "unit": "", "lane": "presence"},
    "weight": {"label": "Weight on side", "unit": "kg", "lane": "presence"},
    "wearable_hr": {"label": "Wearable heart rate", "unit": "bpm", "lane": "body"},
    "skin_temp": {"label": "Skin temperature", "unit": "°C", "lane": "body"},
    "wearable_hrv": {"label": "Heart rate variability", "unit": "ms", "lane": "body"},
    "light": {"label": "Light", "unit": "", "lane": "env"},
    "home": {"label": "Phone at home", "unit": "", "lane": "context"},
    "location": {"label": "Distance from home", "unit": "km", "lane": "context"},
    "calendar": {"label": "Calendar event ends", "unit": "", "lane": "context"},
    "other": {"label": "Other", "unit": "", "lane": "env"},
}


# ---------- device types ----------
# Each kind of device behaves differently in bed: a radar loses perfectly still sleepers for a few seconds, a motion
# sensor only sees you move, a bed scale is solid but drifts. Each type gets defaults that fit it and only the tuning
# that makes sense for it. "votes" types take part in the in-bed decision for their side.
def _t(key, label, kind="number", default=None, unit="", help="", min=None, max=None, step=None, options=None):
    return {k: v for k, v in dict(key=key, label=label, type=kind, default=default, unit=unit, help=help, min=min, max=max,
                                   step=step, options=options).items() if v is not None and not (k in ("unit", "help") and v == "")}

_WEIGHT = _t("weight", "How much it counts in the in-bed vote", default=0.35, min=0, max=1, step=0.05,
             help="Compared with the bed's own strip and any other sensors on this side.")
# on/off devices: what each raw value means (raw state, lowercased → "yes" | "no" | "ignore"); empty = the usual
# reading (on/home/detected = yes, off/not_home/clear = no) with "invert" flipping it, as older setups have
_VMAP = _t("value_map", "What each reading means", "map", default={})
_MAXAGE = _t("max_age_s", "Forget its reading after", default=0, unit="s", min=0, max=3600, step=30,
             help="0 keeps the last state until it reports again (right for sensors that only report changes).")

DEVICE_TYPES: list[dict] = [
    {"id": "radar", "label": "Presence radar", "role": "presence", "votes": True, "side": True,
     "blurb": "mmWave radar zones that see people even when they are lying still.",
     "examples": "Everything Presence Lite/One, Aqara FP2, LD2410 / LD2450 boards",
     "tuning": [_WEIGHT | {"default": 0.35},
                _t("hold_s", "Keep counting as occupied for", default=45, unit="s", min=0, max=600, step=5,
                   help="Radar loses a perfectly still sleeper for a moment; this bridges those gaps."),
                _t("min_on_s", "Only count once detected for", default=10, unit="s", min=0, max=300, step=5,
                   help="Ignores someone leaning over the bed or a pet passing through."),
                _t("invert", "Reading is reversed (on means empty)", "bool", default=False), _VMAP, _MAXAGE]},
    {"id": "motion", "label": "Motion sensor (PIR)", "role": "presence", "votes": True, "side": True,
     "blurb": "Only sees movement, so it is a weak hint: good for noticing someone getting in or up.",
     "examples": "Hue motion, Aqara P1, any PIR",
     "tuning": [_WEIGHT | {"default": 0.15},
                _t("hold_s", "Count as occupied for, after any motion", default=300, unit="s", min=30, max=3600, step=30,
                   help="Sleepers are still for long stretches, so motion only says someone was here recently."),
                _t("invert", "Reading is reversed", "bool", default=False), _VMAP, _MAXAGE]},
    {"id": "scale", "label": "Bed scale / load cells", "role": "weight", "votes": True, "side": True,
     "blurb": "Weight under one side: the most reliable in-bed signal, and it can tell people apart.",
     "examples": "Load cells under the legs via HX711 + ESPHome, Withings scale bridges",
     "tuning": [_WEIGHT | {"default": 0.6},
                _t("threshold_kg", "Counts as occupied above", default=20, unit="kg", min=1, max=150, step=1,
                   help="The least it ever needs. Once it knows the sleeper's weight it asks for most of a person, and "
                        "it re-zeroes on extra bedding, pillows or laundry when the bed has been empty a while."),
                _t("hysteresis_kg", "Must drop this much below to count as empty", default=5, unit="kg", min=0, max=40, step=1,
                   help="Stops it flickering while you shift your weight near the threshold."),
                _t("min_on_s", "Only count once over the threshold for", default=10, unit="s", min=0, max=120, step=5),
                _t("hold_s", "Keep counting as occupied for", default=20, unit="s", min=0, max=300, step=5,
                   help="Rolling over or leaning out of bed for a moment doesn't count as getting up."),
                _MAXAGE | {"default": 180}]},
    {"id": "pad", "label": "Bed occupancy pad", "role": "presence", "votes": True, "side": True,
     "blurb": "A pressure strip or contact pad under the mattress that reports in or out of bed.",
     "examples": "Withings Sleep Analyzer, FSR or contact strips",
     "tuning": [_WEIGHT | {"default": 0.4},
                _t("min_on_s", "Only count once occupied for", default=20, unit="s", min=0, max=300, step=5),
                _t("hold_s", "Keep counting as occupied for", default=20, unit="s", min=0, max=300, step=5),
                _t("invert", "Reading is reversed", "bool", default=False), _VMAP, _MAXAGE]},
    {"id": "room", "label": "Room temperature / humidity", "role": "room_temp", "roles": ["room_temp", "room_humidity"],
     "votes": False, "side": False,
     "blurb": "Lets Autopilot see a warm or muggy room before it blames your rhythm.",
     "examples": "Aqara, Sonoff SNZB-02, Airthings, a thermostat sensor",
     "tuning": [_t("offset", "Calibration offset (added to every reading)", default=0, min=-10, max=10, step=0.1,
                   help="If it reads 0.8 degrees high against a trusted thermometer, enter -0.8 (in the sensor's unit)."),
                _MAXAGE | {"default": 1800}]},
    {"id": "wearable", "label": "Wearable", "role": "wearable_hr", "roles": ["wearable_hr", "skin_temp", "wearable_hrv"],
     "votes": False, "side": True, "person": True,
     "blurb": "A watch or ring streaming live readings (for imports use the app's Health Connect or the import API).",
     "examples": "Watch heart rate via the HA companion app, ring skin temperature",
     "tuning": [_t("offset", "Calibration offset (added to every reading)", default=0, min=-10, max=10, step=0.1),
                _MAXAGE | {"default": 600}]},
    {"id": "light", "label": "Bedroom light", "role": "light", "votes": False, "side": False,
     "blurb": "Bedroom lights on or off. Shows when you really went to bed and any gets-up in the night, so the tuning guide can tell if bedtime is set right.",
     "examples": "Any light or lamp entity",
     "tuning": [_t("invert", "Reading is reversed", "bool", default=False), _VMAP, _MAXAGE]},
    {"id": "phone", "label": "Phone on home Wi-Fi", "role": "home", "votes": False, "side": False, "person": True,
     "blurb": "Whether someone's phone is on the home network. Away holds that side's pre-cool so it doesn't run for nobody.",
     "examples": "UniFi or router device tracker, HA companion app Wi-Fi sensor, ping",
     "tuning": [_t("away_after_min", "Count as away after not seen for", default=20, unit="min", min=1, max=240, step=1,
                   help="Phones drop off Wi-Fi when they sleep. Too short and a sleeping phone looks like you left."),
                _t("invert", "Reading is reversed", "bool", default=False), _VMAP]},
    {"id": "location", "label": "Phone location (GPS)", "role": "location", "votes": False, "side": False, "person": True,
     "blurb": "Distance from home. When you are on the way back, pre-cool starts so the bed is ready when you arrive.",
     "examples": "HA Proximity integration (distance), companion app location",
     "tuning": [_t("home_radius_km", "Counts as home within", default=0.3, unit="km", min=0.05, max=5, step=0.05),
                _t("ready_min", "Have the bed ready this long before you arrive", default=30, unit="min", min=0, max=120, step=5,
                   help="Pre-cool starts when the estimated arrival is this close."),
                _t("speed_kmh", "Assumed travel speed when it can't tell", default=40, unit="km/h", min=5, max=150, step=5),
                _MAXAGE | {"default": 1800}]},
    {"id": "calendar", "label": "Calendar", "role": "calendar", "votes": False, "side": False, "person": True,
     "blurb": "Evening events. A late event moves the 'start on schedule' fallback and feeds the tuning guide.",
     "examples": "HA calendar entity (Google, iCloud, CalDAV), a shared family calendar",
     "tuning": [_t("travel_min", "Travel time home after an event", default=20, unit="min", min=0, max=180, step=5)]},
    {"id": "custom", "label": "Custom device", "role": "other",
     "roles": ["presence", "weight", "room_temp", "room_humidity", "wearable_hr", "skin_temp", "wearable_hrv", "light", "other"],
     "votes": None, "side": True, "custom": True,
     "blurb": "Anything else: you decide what the number means and how it is read.",
     "examples": "A DIY sensor, a template entity, a sensor this list does not know",
     "tuning": [_t("scale", "Multiply readings by", default=1, min=-1000, max=1000, step=0.01),
                _t("offset", "Then add", default=0, min=-1000, max=1000, step=0.1),
                _t("unit", "Unit to show", "text", default=""),
                _t("compare", "Counts as occupied when the reading is", "select", default=">=", options=[">=", "<=", "==", "!="],
                   help="Used when it measures presence or weight."),
                _t("threshold", "compared with this value", default=0.5, min=-100000, max=100000, step=0.1),
                _t("min_on_s", "Only count once true for", default=0, unit="s", min=0, max=3600, step=5),
                _t("hold_s", "Keep counting for", default=0, unit="s", min=0, max=3600, step=5),
                _t("invert", "Flip the result", "bool", default=False),
                _WEIGHT | {"default": 0.3}, _MAXAGE]},
]
_TRUST = _t("ignore_health", "Trust it even when it acts up", "bool", default=False,
            help="Ember & Tide sets aside a device that floods, jumps or freezes. Turn this on if it's fine and just unusual.")
for _dt in DEVICE_TYPES:
    _dt["tuning"].append(_TRUST)
TYPES = {t["id"]: t for t in DEVICE_TYPES}


# ---------- device health ----------
# What "acting up" means, per role. Readings outside RANGE are impossible and dropped. A jump faster than SPIKE
# (change per minute, plus a small allowance) is implausible and dropped. Three dropped readings within 30 min, a
# presence device flapping, or a reading frozen where it always moves = erratic: the device is set aside (no vote,
# no samples for Autopilot) until it has behaved for 30 minutes. Thresholds are deliberately loose: a radar losing a
# perfectly still sleeper for a few seconds is normal (its hold time covers that) and never counts as flapping.
RANGE = {"room_temp": (-20.0, 55.0), "room_humidity": (0.0, 100.0), "weight": (-30.0, 350.0), "wearable_hr": (25.0, 230.0),
         "skin_temp": (25.0, 43.0), "wearable_hrv": (1.0, 300.0), "location": (0.0, 20000.0)}
# (on/off roles aren't range-checked: a radar may report a target count, a light its brightness)
SPIKE = {"room_temp": (2.0, 1.0), "room_humidity": (20.0, 5.0), "skin_temp": (1.5, 0.5), "wearable_hr": (50.0, 20.0),
         "location": (5.0, 2.0)}                       # (per minute, allowance)
STUCK_S = {"skin_temp": 90 * 60, "wearable_hr": 90 * 60, "weight": 120 * 60}
FLAPS = {"presence": (6, 600), "weight": (6, 600), "home": (6, 1800)}     # (transitions, within seconds)
BAD_LIMIT, BAD_WINDOW_S, CALM_S = 3, 1800, 1800


def plain_error(e: BaseException, what: str = "Home Assistant", driver: str | None = None) -> str:
    """An exception from a device driver, in words someone can act on (worded for that kind of connection)."""
    n = type(e).__name__
    msg = str(e)
    status = getattr(getattr(e, "response", None), "status_code", None)
    if driver == "ics" or what == "the calendar feed":
        if status in (404, 410):
            return ("This calendar link no longer works. If you reset it in Fastmail (or iCloud/Google), paste the new "
                    "link in Tune → Link.")
        if status in (401, 403):
            return "The calendar service refused the link (it may be private or reset)."
        if status and status >= 500:
            return f"The calendar service had a problem ({status}). Retrying."
        if status:
            return f"The calendar service answered {status}. Retrying."
        if "web page" in msg:
            return "That link opens a web page, not a calendar feed."
        if "larger than" in msg:
            return "That calendar feed is too large (over 5 MB)."
        if "Timeout" in n:
            return "The calendar service didn't answer in time. Retrying."
        if "Connect" in n:
            return "Can't reach the calendar service. Retrying."
        if n in ("RemoteProtocolError", "ReadError") or "disconnected" in msg.lower():
            return "The calendar service dropped the connection. Retrying."
        if isinstance(e, (ValueError, KeyError)):
            return f"That link didn't return a calendar ({msg[:60]})."
        return f"{n}: {msg}"[:160]
    if driver == "mqtt-topic":
        if "authori" in msg.lower() or "password" in msg.lower() or "code:5" in msg.replace(" ", "").lower():
            return "The MQTT broker refused the username or password."
        if "Timeout" in n:
            return "The MQTT broker didn't answer in time. Retrying."
        return "Can't reach the MQTT broker. Retrying."
    if isinstance(e, KeyError) and what == "Home Assistant":
        return "No Home Assistant connection is set up. Add one in Studio → Integrations."
    if status in (401, 403):
        return (f"{what} refused the token ({status}). Make a new long-lived token in your {what} profile and "
                "update it in Studio → Integrations.")
    if status == 404:
        return (f"{what} doesn't have that entity any more (it may have been renamed or removed). "
                "Put the new entity ID in Tune → Connection, or remove this device.")
    if status and status >= 500:
        return f"{what} had a problem ({status}). Retrying."
    if status:
        return f"{what} answered {status}. Retrying."
    if "Timeout" in n:
        return f"{what} didn't answer in time. Retrying."
    if "Connect" in n or "refused" in msg.lower() or "unreachable" in msg.lower():
        return f"Can't reach {what}. Retrying."
    if n in ("RemoteProtocolError", "ReadError", "WriteError", "LocalProtocolError") or "disconnected" in msg.lower():
        return f"{what} dropped the connection (it may be restarting). Retrying."
    if "MqttError" in n or "MqttCodeError" in n or "mqtt" in n.lower():
        if "authori" in msg.lower() or "password" in msg.lower() or "code:5" in msg.replace(" ", "").lower():
            return "The MQTT broker refused the username or password."
        return "Can't reach the MQTT broker. Retrying."
    if isinstance(e, (ValueError, KeyError)) and what == "the calendar feed":
        return f"That address didn't return a calendar ({msg[:60]})."
    return f"{n}: {msg}"[:160]
_LEGACY_KIND = {"presence": "radar", "weight": "scale", "room_temp": "room", "room_humidity": "room",
                "wearable_hr": "wearable", "skin_temp": "wearable", "wearable_hrv": "wearable", "light": "light", "other": "custom"}


# role -> (store at least this often in seconds, a change of at least this much is stored at once)
SAVE_EVERY: dict[str, tuple[float, float | None]] = {
    "room_temp": (300, 0.2), "room_humidity": (300, 2.0), "home": (600, 0.5), "location": (300, 0.2),
    "calendar": (900, 60.0), "light": (600, 0.5),
}


FAST_POLL_ROLES = ("light", "presence", "home", "weight")     # on/off things people notice lagging


def poll_s(c) -> float:
    """How often a Home Assistant entity is read: the device's own setting, else 5 s for on/off things (a light
    switching should show at once) and 30 s for slow sensors."""
    v = c.params.get("interval_s")
    if v not in (None, ""):
        try:
            return max(2.0, float(v))
        except (TypeError, ValueError):
            pass
    return 5.0 if c.role in FAST_POLL_ROLES else 30.0


def tuning_for(kind: str, tuning: dict) -> dict:
    """The type's defaults with the device's own settings on top."""
    t = TYPES.get(kind) or TYPES["custom"]
    return {f["key"]: tuning.get(f["key"], f.get("default")) for f in t["tuning"]}


WEARABLE_ROLES = ("wearable_hr", "skin_temp", "wearable_hrv")


def votes(c) -> bool:
    t = TYPES.get(c.kind) or TYPES["custom"]
    if t["votes"] is None:                        # custom: votes when it measures presence or weight
        return c.role in ("presence", "weight")
    return bool(t["votes"])


class SourceConfig(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    driver: Literal["ha-entity", "mqtt-topic", "sim", "ics"]      # ics: a calendar feed address (calendar devices)
    role: Role
    zone: Literal["left", "right"] | None = None
    person: str | None = None
    unit: str | None = None
    device: str | None = None          # e.g. "Everything Presence Lite", "Airthings Wave"
    kind: str = ""                     # device type (DEVICE_TYPES); filled in from the role for older setups
    tuning: dict = Field(default_factory=dict)   # the type's tuning (see DEVICE_TYPES); missing keys use its defaults
    params: dict = Field(default_factory=dict)   # how to connect (HA entity, MQTT topic, ...)
    enabled: bool = True
    # one device that watches both sides (a radar with a left and a right zone, a scale with load cells per side) is
    # stored as one source per side sharing this id; None = a device of its own
    group: str | None = Field(None, pattern=r"^[a-z0-9-]{1,40}$")

    def model_post_init(self, _ctx) -> None:
        if not self.kind or self.kind not in TYPES:
            self.kind = _LEGACY_KIND.get(self.role, "custom")


class Calibration(BaseModel):
    weights: dict[str, float] = Field(default_factory=lambda: {"bed": 0.5, "presence": 0.3, "weight": 0.4})
    weight_threshold_kg: float = 20.0
    tare_kg: dict[str, float] = Field(default_factory=dict)       # source id → offset
    person_kg: dict[str, float] = Field(default_factory=dict)     # person id → body weight (identity)
    empty_kg: dict[str, float] = Field(default_factory=dict)      # scale id → learned empty-bed zero (engine/bedscale.py)
    side_kg: dict[str, float] = Field(default_factory=dict)       # scale id → learned weight of the person on that side


def ics_url(u: str) -> str:
    u = (u or "").strip()
    if u.lower().startswith("webcal://"):
        u = "https://" + u[9:]
    p = urlparse(u)
    if p.scheme not in ("https", "http") or not p.hostname:
        raise ValueError("calendar feed must be an https:// or webcal:// address")
    return u


def ics_tonight(text: str, now: float, tz) -> tuple[float, float | None]:
    """From a calendar feed: when tonight's evening event ends (epoch; 0 = none), and the start of the first event
    early tomorrow (03:00-11:00, for an earlier wake). Recurring events are expanded; all-day, cancelled and
    'free' (transparent) events are skipped."""
    from datetime import date as _date, datetime, timedelta

    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(text)
    # an event repeating every second or minute (by mistake, or in a calendar someone else edits) would expand into
    # thousands of copies for the 42 h looked at: it's never an evening plan, so it's left out
    def too_often(x) -> bool:
        rules = x.get("RRULE")
        for r in (rules if isinstance(rules, list) else [rules] if rules is not None else []):
            try:
                if str((r.get("FREQ") or [""])[0]).upper() in ("SECONDLY", "MINUTELY"):
                    return True
            except (AttributeError, IndexError, TypeError):
                return True                                           # unreadable rule: not worth expanding
        return False
    cal.subcomponents = [x for x in cal.subcomponents if not (x.name == "VEVENT" and too_often(x))]
    start = datetime.fromtimestamp(now - 12 * 3600, tz)
    stop = datetime.fromtimestamp(now + 30 * 3600, tz)
    end_tonight, early = 0.0, None
    local = datetime.fromtimestamp(now, tz)
    tomorrow = (local + timedelta(days=1)).date() if local.hour >= 12 else local.date()
    # a "free/busy only" feed (Fastmail's privacy-friendly publish option) has VFREEBUSY periods instead of events
    for fb in cal.walk("VFREEBUSY"):
        periods = fb.get("FREEBUSY")
        for prop in (periods if isinstance(periods, list) else [periods] if periods is not None else []):
            if str(prop.params.get("FBTYPE", "BUSY")).upper() == "FREE":
                continue
            for per in [prop]:
                try:
                    a0, a1 = per.dt
                    if not isinstance(a1, datetime):
                        a1 = a0 + a1                                  # (start, duration)
                    st, en = a0.timestamp(), a1.timestamp()
                except (TypeError, ValueError, AttributeError):
                    continue
                if en > now and st - now <= 6 * 3600 and en - st < 20 * 3600:
                    end_tonight = max(end_tonight, en)
                sl = datetime.fromtimestamp(st, tz)
                if sl.date() == tomorrow and 3 <= sl.hour < 11 and st > now:
                    early = st if early is None else min(early, st)
    for ev in recurring_ical_events.of(cal).between(start, stop):
        s, e = ev.get("DTSTART"), ev.get("DTEND")
        if s is None:
            continue
        s, e = s.dt, (e.dt if e is not None else None)
        if isinstance(s, _date) and not isinstance(s, datetime):
            continue                                                  # all-day
        if str(ev.get("STATUS", "")).upper() == "CANCELLED" or str(ev.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue
        if s.tzinfo is None:
            s = s.replace(tzinfo=tz)
        if e is None:
            e = s + (ev.get("DURATION").dt if ev.get("DURATION") else timedelta(hours=1))
        elif e.tzinfo is None:
            e = e.replace(tzinfo=tz)
        st, en = s.timestamp(), e.timestamp()
        if en > now and st - now <= 6 * 3600 and en - st < 20 * 3600:
            end_tonight = max(end_tonight, en)
        sl = s.astimezone(tz)
        if sl.date() == tomorrow and 3 <= sl.hour < 11 and st > now:
            early = st if early is None else min(early, st)
    return float(end_tonight), early


def _calendar_value(d: dict, now: float) -> float:
    """An HA calendar entity -> when tonight's event ends (epoch), or 0 when there's none. HA shows the current or
    next event in the attributes; only events running now or starting within 6 hours count."""
    from datetime import datetime
    a = d.get("attributes", {}) or {}

    def ep(v):
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return None
    start, end = ep(a.get("start_time")), ep(a.get("end_time"))
    if end is None or end <= now:
        return 0.0
    st, en = str(a.get("start_time") or ""), str(a.get("end_time") or "")
    whole_days = start is not None and (end - start) % 86400 == 0 and (len(st) <= 10 or st[11:19] == "00:00:00") and (len(en) <= 10 or en[11:19] == "00:00:00")
    if a.get("all_day") or whole_days:
        return 0.0                                    # all-day events (birthdays, holidays) aren't evenings out
    if str(d.get("state")).lower() == "on" or (start is not None and start - now <= 6 * 3600):
        return float(end)
    return 0.0


def _to_value(state) -> float | None:
    if state is None:
        return None
    if isinstance(state, bool):
        return 1.0 if state else 0.0
    if isinstance(state, (int, float)):
        return float(state) if math.isfinite(state) else None
    s = str(state).strip().lower()
    if s in ("on", "home", "true", "detected", "occupied", "open"):
        return 1.0
    if s in ("off", "not_home", "away", "false", "clear", "closed", "unoccupied"):
        return 0.0
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


_UNAVAILABLE = ("unavailable", "unknown", "none", "", "null")


class SourceManager:
    def __init__(self, store, clock, bed_getter) -> None:
        self.store = store
        self.clock = clock
        self.bed = bed_getter                    # returns the bed driver (for sim sources)
        self.configs: list[SourceConfig] = [SourceConfig(**c) for c in store.get("sources", [])]
        self.calibration = Calibration(**store.get("calibration", {}))
        self.latest: dict[str, dict] = {}        # id → {value, ts, error}
        self._tasks: dict[str, asyncio.Task] = {}
        self._last_saved: dict[str, float] = {}
        self._activity: dict[str, dict] = {}     # id -> {"day", "changes", "last_change", "prev"} for on/off devices
        self._last_value: dict[str, float] = {}
        self.paused_zones: set[str] = set()      # sides lent to a guest: their devices still vote, nothing is stored
        self._rand = random.Random(3)
        self._stage_t: dict[str, float] = {}
        self._occ: dict[str, dict] = {}          # id -> {"raw", "since", "last_true"} for voting devices
        self.health: dict[str, dict] = {}        # id -> device health (see RANGE / SPIKE / FLAPS / STUCK_S)
        self.events = lambda ts, kind, **d: self.store.event(ts, kind, None, "devices", **d)    # device_health events
        self.http_transport = None               # tests: an httpx transport for the HA / calendar drivers
        self.backoff = (2.0, 120.0)              # first retry, longest wait
        self._sleep = asyncio.sleep
        self.scales: dict = {}                   # id -> ScaleTracker (empty-bed zero + person weight, engine/bedscale.py)
        self._raw_seen: dict[str, dict[str, float]] = {}   # source id → raw value → last seen (newest last)
        self.side_hints: dict[str, dict] = {}    # zone -> {"empty", "in_bed", "people_kg"} from the controller
        self._scale_saved = 0.0

    # ---------- config ----------
    def save(self) -> None:
        self.store.put("sources", [c.model_dump() for c in self.configs])
        self.store.put("calibration", self.calibration.model_dump())

    async def start(self) -> None:
        for c in self.configs:
            self._launch(c)

    async def stop(self) -> None:
        for t in self._tasks.values():
            t.cancel()

    def _launch(self, c: SourceConfig) -> None:
        if c.id in self._tasks:
            self._tasks.pop(c.id).cancel()
        if not c.enabled:
            return
        runner = {"ha-entity": self._run_ha, "mqtt-topic": self._run_mqtt, "sim": self._run_sim, "ics": self._run_ics}[c.driver]
        self._tasks[c.id] = asyncio.create_task(self._guard(c, runner))

    async def add(self, c: SourceConfig) -> SourceConfig:
        self._occ.pop(c.id, None)                # tuning may have changed what its readings mean: start its in-bed state fresh
        self.configs = [x for x in self.configs if x.id != c.id] + [c]
        self.save()
        self._launch(c)
        return c

    async def remove(self, sid: str) -> None:
        self.configs = [x for x in self.configs if x.id != sid]
        if sid in self._tasks:
            self._tasks.pop(sid).cancel()
        self.latest.pop(sid, None)
        self.save()

    def get(self, sid: str) -> SourceConfig | None:
        return next((c for c in self.configs if c.id == sid), None)

    def migrate(self, people) -> bool:
        """Bring older setups up to the "who is it for" model (run at start, with the household's people):
        - a watch or ring stored with only a side gets the person who sleeps on that side;
        - a person's own device on a side follows that person's side;
        - the simulator's left/right radar and scale pairs become one device each (group).
        Returns True when something changed (the caller saves)."""
        by_zone = {p.zone: p.id for p in people}
        changed = False
        for c in self.configs:
            personal = c.kind == "wearable" or (c.kind == "custom" and c.role in WEARABLE_ROLES)
            if personal and c.person is None and c.zone in by_zone:
                c.person, changed = by_zone[c.zone], True
        changed = self.follow_owners(people) or changed
        pairs: dict[tuple, list[SourceConfig]] = {}
        for c in self.configs:
            if c.driver == "sim" and c.group is None and c.zone in ("left", "right") and votes(c):
                pairs.setdefault((c.kind, c.device or ""), []).append(c)
        for (kind, _), cs in pairs.items():
            if sorted(x.zone for x in cs) == ["left", "right"]:
                for x in cs:
                    x.group, changed = f"sim-{kind}", True
        return changed

    def follow_owners(self, people) -> bool:
        """A person's own device (watch or ring, phone, location) is on its owner's side, wherever that is now
        (after "swap sides", say). Bed sensors stay with their side of the bed. Returns True when something changed."""
        zone_of = {p.id: p.zone for p in people}
        changed = False
        for c in self.configs:
            personal = c.kind in ("wearable", "phone", "location") or (c.kind == "custom" and c.role in WEARABLE_ROLES)
            if not personal or c.person not in zone_of:
                continue
            want = zone_of[c.person] if (TYPES.get(c.kind) or {}).get("side") else None
            if c.zone != want:
                c.zone, changed = want, True
        return changed

    # ---------- samples ----------
    def _sample(self, c: SourceConfig, value: float | None, ts: float | None = None, note: str | None = None) -> None:
        ts = ts or self.clock.now_ts()
        t = tuning_for(c.kind, c.tuning)
        if value is not None:
            value = value * float(t.get("scale", 1) if t.get("scale") not in (None, "") else 1) + float(t.get("offset", 0) or 0)
            if c.role == "weight":
                value = value - self.calibration.tare_kg.get(c.id, 0.0)
            value = round(value, 3)
            if not math.isfinite(value):
                value = None
        if value is not None and not self._check(c, t, value, ts):
            prev = self.latest.get(c.id, {})
            self.latest[c.id] = {**prev, "error": None, "seen": ts}             # it reported, but not something to use
            return
        self.latest[c.id] = {"value": value, "ts": ts, "error": None, "seen": ts, "note": note}
        if value is not None and c.role in ("light", "presence", "home"):
            self._note_activity(c, value >= 0.5, ts)
        if value is not None and c.kind == "scale":
            self._scale_update(c, t, value, ts)
        if value is not None and votes(c):
            self._track(c, t, value, ts)
        last = self._last_saved.get(c.id, 0)
        # slow things (room, phone, location, calendar, light) are stored when they change or every few minutes;
        # body and presence readings every 15 s
        every, step = SAVE_EVERY.get(c.role, (15, None))
        prev = self._last_value.get(c.id)
        changed = prev is None or value is None or step is None or abs(value - prev) >= step
        # a real change on a slow device (phone home/away, light, calendar) is stored at once: how long something has
        # been in a state is read from this history, so a change dropped here would be lost for good
        due = ts - last >= ((1 if step is not None else 15) if changed else every)
        guest = c.zone in self.paused_zones and c.person is None
        if value is not None and not guest and not self.quarantined(c) and (due or ts < last):     # ts < last: the simulator jumped back
            self._last_saved[c.id] = ts
            self._last_value[c.id] = value
            self.store.sample(ts, c.id, value)

    # ---------- bed scale: empty-bed zero and person-sized threshold ----------
    def scale(self, c: SourceConfig):
        from .engine.bedscale import ScaleTracker
        tr = self.scales.get(c.id)
        if tr is None:
            tr = self.scales[c.id] = ScaleTracker(self.calibration.empty_kg.get(c.id, 0.0), self.calibration.side_kg.get(c.id))
        return tr

    def scale_threshold(self, c: SourceConfig, t: dict | None = None) -> float:
        t = t if t is not None else tuning_for(c.kind, c.tuning)
        base = float(t.get("threshold_kg") or self.calibration.weight_threshold_kg)
        if c.kind != "scale":
            return base
        known = (self.side_hints.get(c.zone or "") or {}).get("people_kg") or []
        if c.person and self.calibration.person_kg.get(c.person):
            known = [self.calibration.person_kg[c.person]]
        return self.scale(c).threshold(base, known)

    def net_kg(self, c: SourceConfig, value: float) -> float:
        """A scale reading above the learned empty-bed zero (what's on the bed that wasn't there when it was empty)."""
        return self.scale(c).net(value) if c.kind == "scale" else value

    def _scale_update(self, c: SourceConfig, t: dict, value: float, ts: float) -> None:
        hint = self.side_hints.get(c.zone or "") or {}
        tr = self.scale(c)
        tr.update(ts, value, hint.get("empty"), hint.get("in_bed"), self.scale_threshold(c, t))
        if tr.changed and (ts - self._scale_saved >= 600 or ts < self._scale_saved):
            self.calibration.empty_kg[c.id] = round(tr.empty_kg, 2)
            if tr.person_kg:
                self.calibration.side_kg[c.id] = round(tr.person_kg, 1)
            tr.changed, self._scale_saved = False, ts
            self.save()

    def hint_side(self, zone: str, empty: bool | None, in_bed: bool | None, people_kg: list[float]) -> None:
        self.side_hints[zone] = {"empty": empty, "in_bed": in_bed, "people_kg": people_kg}

    # ---------- health ----------
    def _h(self, c: SourceConfig) -> dict:
        return self.health.setdefault(c.id, {"status": "ok", "why": "", "since": None, "bad": [], "flips": [], "hist": [],
                                             "last_problem": None, "dropped": 0})

    def _check(self, c: SourceConfig, t: dict, value: float, ts: float) -> bool:
        """Health bookkeeping for one reading. False = drop this reading (impossible or an implausible jump)."""
        h = self._h(c)
        role = c.role
        ok, why = True, ""
        lo_hi = RANGE.get(role) if c.kind != "custom" else None
        if lo_hi and not (lo_hi[0] <= value <= lo_hi[1]):
            ok, why = False, f"reported {value:g}, which isn't possible for {ROLE_INFO[role]['label'].lower()}"
        hist = h["hist"]
        if ok and role in SPIKE and hist:
            pt, pv = hist[-1]
            dt = max(60.0, ts - pt) / 60
            per_min, allow = SPIKE[role]
            if abs(value - pv) > per_min * dt + allow:
                ok, why = False, f"jumped from {pv:g} to {value:g} in {max(1, round((ts - pt) / 60))} min"
        if not ok:
            h["dropped"] += 1
            h["bad"] = [x for x in h["bad"] if ts - x <= BAD_WINDOW_S] + [ts]
            if len(h["bad"]) >= BAD_LIMIT:
                self._erratic(c, h, ts, f"{why} (and {len(h['bad']) - 1} more odd readings in 30 min)")
            return False
        hist.append((ts, value))
        while hist and ts - hist[0][0] > 4 * 3600:
            hist.pop(0)
        if len(hist) > 2000:
            del hist[: len(hist) - 1500]
        # flapping: presence-type devices count real changes (an off longer than the hold time, then on again)
        if role in FLAPS and (votes(c) or role == "home"):
            n_max, win = FLAPS[role]
            on = self._raw(c, t, value) if votes(c) else ((value >= 0.5) != bool(t.get("invert")))
            last_on = h.get("on")
            if last_on is not None and on != last_on:
                gap = ts - h.get("on_since", ts)
                hold = float(t.get("hold_s", 0) or 0)
                if on and gap > max(hold, 5.0) or role == "home":
                    h["flips"] = [x for x in h["flips"] if ts - x <= win] + [ts]
                    if len(h["flips"]) >= n_max:
                        self._erratic(c, h, ts, f"changed {len(h['flips'])} times in {round(win / 60)} min")
            if on != last_on:
                h["on"], h["on_since"] = on, ts
        # frozen: exactly the same reading for hours where it always moves
        if role in STUCK_S:
            span = STUCK_S[role]
            same = [v for x, v in hist if ts - x <= span]
            if (len(same) >= 20 and hist[0][0] <= ts - span and max(same) - min(same) < (0.05 if role == "weight" else 1e-9)
                    and (role != "weight" or value >= float(t.get("threshold_kg") or self.calibration.weight_threshold_kg))):
                self._erratic(c, h, ts, f"the same reading ({value:g}) for {round(span / 60)} min, which a working sensor never does",
                              stuck=True)
            elif h["status"] == "erratic" and h.get("stuck") and len(set(v for _, v in hist[-3:])) > 1:
                self._recover(c, h, ts)
        if h["status"] == "erratic" and not h.get("stuck") and h["last_problem"] and ts - h["last_problem"] >= CALM_S:
            self._recover(c, h, ts)
        return True

    def _erratic(self, c: SourceConfig, h: dict, ts: float, why: str, stuck: bool = False) -> None:
        h["last_problem"] = ts
        if h["status"] != "erratic":
            h.update(status="erratic", why=why, since=ts, stuck=stuck)
            self._occ.pop(c.id, None)
            if self.events:
                self.events(ts, "device_health", device=c.id, name=c.name, status="erratic", why=why,
                            trusted=bool(tuning_for(c.kind, c.tuning).get("ignore_health")))

    def _recover(self, c: SourceConfig, h: dict, ts: float) -> None:
        h.update(status="ok", why="", since=ts, stuck=False, bad=[], flips=[])
        if self.events:
            self.events(ts, "device_health", device=c.id, name=c.name, status="ok", why="behaving normally again")

    def quarantined(self, c: SourceConfig) -> bool:
        h = self.health.get(c.id)
        return bool(h and h["status"] == "erratic" and not tuning_for(c.kind, c.tuning).get("ignore_health"))

    def health_of(self, c: SourceConfig, now: float | None = None) -> dict:
        """ok | erratic | error | offline | stale, with a plain reason."""
        now = now if now is not None else self.clock.now_ts()
        h = self._h(c)
        l = self.latest.get(c.id, {})
        t = tuning_for(c.kind, c.tuning)
        trusted = bool(t.get("ignore_health"))
        if not c.enabled:
            return {"status": "paused", "why": "paused", "trusted": trusted}
        if l.get("error"):
            return {"status": "error", "why": l["error"], "trusted": trusted}
        if h["status"] == "erratic":
            return {"status": "erratic", "why": h["why"] + (" · trusted anyway" if trusted else " · set aside until it settles"),
                    "since": h["since"], "trusted": trusted}
        seen = l.get("seen") or l.get("ts")
        max_age = float(t.get("max_age_s", 0) or 0)
        limit = max_age or (3 * poll_s(c) + 60 if c.driver == "ha-entity" else 0)
        if seen is None:
            return {"status": "offline", "why": "no readings yet", "trusted": trusted}
        if limit and now - seen > limit:
            return {"status": "offline", "why": f"nothing for {_ago(now - seen)}", "trusted": trusted}
        if l.get("value") is None:
            return {"status": "stale", "why": l.get("note") or "reported nothing usable", "trusted": trusted}
        why = f"{h['dropped']} odd reading{'s' if h['dropped'] != 1 else ''} ignored" if h["dropped"] else ""
        return {"status": "ok", "why": why, "trusted": trusted}

    async def _guard(self, c: SourceConfig, runner) -> None:
        base, top = self.backoff
        backoff = base
        what = {"ics": "the calendar feed", "mqtt-topic": "the MQTT broker"}.get(c.driver, "Home Assistant")
        while True:
            started = time.monotonic()
            try:
                await runner(c)
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                if time.monotonic() - started > 120:
                    backoff = base                   # it worked for a while: this is a new problem, retry quickly
                self.latest[c.id] = {**self.latest.get(c.id, {"value": None, "ts": None}), "error": plain_error(e, what, c.driver),
                                     "retry_s": backoff}
                await self._sleep(backoff)
                backoff = min(top, backoff * 2)

    # ---------- the shared Home Assistant connection ----------
    def ha_conn(self) -> dict | None:
        """The one Home Assistant connection (Studio → Integrations): {url, token}, or None."""
        c = self.store.get("ha_connection")
        return c if c and c.get("url") and c.get("token") else None

    def ha_creds(self, c: SourceConfig) -> tuple[str, str]:
        """Address and token for an HA device: the shared connection when the device uses it, else its own."""
        if c.params.get("conn") == "ha":
            conn = self.ha_conn()
            if not conn:
                raise KeyError("no shared Home Assistant connection")
            return conn["url"], conn["token"]
        return c.params["url"], c.params["token"]

    def relaunch_shared(self) -> int:
        """The shared connection changed: restart every device that uses it, so the new token is used at once."""
        n = 0
        for c in self.configs:
            if c.driver == "ha-entity" and c.params.get("conn") == "ha":
                self._launch(c)
                n += 1
        return n

    async def _run_ha(self, c: SourceConfig) -> None:
        _, token = self.ha_creds(c)
        async with httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bearer {token}"},
                                     transport=self.http_transport) as http:
            while True:
                await self.poll_ha(c, http)
                await self._sleep(poll_s(c))

    async def poll_ha(self, c: SourceConfig, http) -> None:
        """Read one Home Assistant entity once. Raises on HTTP/connection problems (the guard retries)."""
        p = c.params
        base, _ = self.ha_creds(c)
        r = await http.get(base.rstrip("/") + f"/api/states/{p['entity_id']}")
        r.raise_for_status()
        try:
            d = r.json()
        except ValueError:
            raise ValueError("Home Assistant sent something that isn't JSON")
        if not isinstance(d, dict):
            raise ValueError("Home Assistant sent an unexpected reply")
        attrs = d.get("attributes") or {}
        if c.role == "calendar":
            self._sample(c, _calendar_value(d, self.clock.now_ts()))
            return
        state = attrs.get(p["attribute"]) if p.get("attribute") else d.get("state")
        mapped = self.map_raw(c, state)
        if mapped is not None:
            self._sample(c, mapped[0], note=mapped[1])
            return
        if str(state).strip().lower() in _UNAVAILABLE:
            self._sample(c, None, note=f"Home Assistant says it's {str(state).strip().lower() or 'empty'}")
            return
        if c.role == "location" and str(state).lower() == "home":
            state = 0.0
        v = _to_value(state)
        if v is None and c.role == "home":
            v = 0.0                                       # a phone/person in a named zone ("Work", "Gym") is away from home
        unit = str(attrs.get("unit_of_measurement") or "").upper().replace("°", "").strip()
        if v is not None and c.role in ("room_temp", "skin_temp") and unit == "F":
            v = round((v - 32) * 5 / 9, 3)                # Ember & Tide works in °C; HA may switch units at any time
        if v is not None and c.role == "weight" and unit in ("LB", "LBS"):
            v = round(v * 0.45359237, 3)                  # and kg
        if v is not None and c.role == "location":
            if unit == "MI":
                v = round(v * 1.609344, 3)                # and km
            elif unit == "M":
                v = round(v / 1000, 3)
            elif unit == "FT":
                v = round(v * 0.0003048, 3)
        if v is None:
            self._sample(c, None, note=f"Home Assistant sent '{str(state)[:30]}', which isn't a reading")
            return
        self._sample(c, v)

    async def _run_ics(self, c: SourceConfig) -> None:
        """A calendar feed (Fastmail private link, iCloud public calendar, Google secret address): fetched
        every 15 minutes, read on this server; nothing is written back."""
        url = ics_url(c.params.get("url", ""))
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "EmberTide/1"},
                                     transport=self.http_transport) as http:
            while True:
                text = await self.fetch_ics(http, url)
                now = self.clock.now_ts()
                # parsed on a worker thread: a big feed never holds up the bed, the live feed or the API
                end, early = await asyncio.to_thread(ics_tonight, text, now, self.clock.tz)
                self._sample(c, end, now)
                self.latest[f"{c.id}:early"] = {"value": early, "ts": now, "error": None}
                # keep a reading every few minutes between fetches, so "busy until" stays fresh for the night engine
                for _ in range(15):
                    await self._sleep(60 / max(1.0, self.clock.speed))
                    t = self.clock.now_ts()
                    self._sample(c, (await asyncio.to_thread(ics_tonight, text, t, self.clock.tz))[0], t)

    @staticmethod
    async def fetch_ics(http, url: str, limit: int = 5_000_000) -> str:
        """Download a feed without ever holding more than `limit` bytes; it must look like a calendar."""
        async with http.stream("GET", url) as r:
            r.raise_for_status()
            if int(r.headers.get("content-length") or 0) > limit:
                raise ValueError("the calendar feed is larger than 5 MB")
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise ValueError("the calendar feed is larger than 5 MB")
        text = buf.decode("utf-8", errors="replace")
        head = text[:2000].lstrip().lower()
        if head.startswith("<!doctype html") or head.startswith("<html") or "<html" in head[:300]:
            raise ValueError("it's a web page, not a calendar feed")
        if "BEGIN:VCALENDAR" not in text[:2000].upper():
            raise ValueError("it isn't a calendar (no BEGIN:VCALENDAR)")
        return text

    async def _run_mqtt(self, c: SourceConfig) -> None:
        import aiomqtt
        p = c.params
        u = urlparse(p["url"])
        async with aiomqtt.Client(u.hostname or "localhost", u.port or 1883, username=p.get("username") or u.username,
                                  password=p.get("password") or u.password) as client:
            await client.subscribe(p["topic"])
            self.latest[c.id] = {**self.latest.get(c.id, {"value": None, "ts": None}), "error": None}
            async for msg in client.messages:
                self.on_mqtt(c, bytes(msg.payload), bool(getattr(msg, "retain", False)))

    def on_mqtt(self, c: SourceConfig, payload: bytes, retained: bool = False) -> None:
        """One MQTT message. A retained message is the broker's memory of the last value, of unknown age: fine for a
        room temperature, but never trusted as 'someone is in bed right now'."""
        if retained and votes(c):
            return
        if len(payload) > 65536:
            self._sample(c, None, note="message too large to be a reading")
            return
        raw = payload.decode(errors="replace")
        val = raw
        p = c.params
        if p.get("json_path"):
            try:
                val = json.loads(raw)
                for k in p["json_path"].split("."):
                    val = val[int(k)] if isinstance(val, list) else val.get(k)
            except (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError):
                self._sample(c, None, note=f"couldn't find '{p['json_path']}' in the message")
                return
        mapped = self.map_raw(c, val)
        if mapped is not None:
            self._sample(c, mapped[0], note=mapped[1])
            return
        v = _to_value(val)
        self._sample(c, v, note=None if v is not None else "the message isn't a reading")

    async def _run_sim(self, c: SourceConfig) -> None:
        while True:
            now = self.clock.now_ts()
            v = self._sim_value(c, now)
            if v is not None:
                self._sample(c, v, now)
            await asyncio.sleep(max(0.05, 2.0 / max(1.0, self.clock.speed)))

    def sim_step(self, now: float | None = None) -> None:
        """One reading from every simulated device (tests drive time by hand instead of running the tasks)."""
        now = now if now is not None else self.clock.now_ts()
        for c in self.configs:
            if c.enabled and c.driver == "sim":
                v = self._sim_value(c, now)
                if v is not None:
                    self._sample(c, v, now)

    def _at(self, hm: str, now: float) -> float:
        """'22:30' tonight as a timestamp: evening times belong to the evening this night started, morning
        times (before noon) to the morning after."""
        from datetime import datetime, timedelta
        local = datetime.fromtimestamp(now, self.clock.tz)
        evening = (local - timedelta(days=1)).date() if local.hour < 12 else local.date()
        h, m = (int(x) for x in hm.split(":"))
        d = evening + timedelta(days=1) if h < 12 else evening
        return datetime(d.year, d.month, d.day, h, m, tzinfo=self.clock.tz).timestamp()

    def _sim_context(self, c: SourceConfig, kind: str, now: float) -> float | None:
        p = c.params
        if kind == "home":
            for a, b in p.get("away", []):
                if self._at(a, now) <= now < self._at(b, now):
                    return 0.0
            # sleeping phones drop off Wi-Fi now and then for a few minutes
            if p.get("naps", True) and (int(now // 180) * 2654435761 % 100) < 6:
                return None
            return 1.0
        if kind == "location":
            t = p.get("trip")
            if not t:
                return 0.0
            km, sp = float(t.get("km", 15)), float(t.get("speed", 45))
            leave, arrive = self._at(t["leave"], now), self._at(t["arrive"], now)
            drive = km / sp * 3600
            if now < leave or now >= arrive:
                return 0.0
            if now < leave + drive:
                return round(km * (now - leave) / drive, 2)
            if now >= arrive - drive:
                return round(km * (arrive - now) / drive, 2)
            return km
        if kind == "calendar":
            for ev in p.get("events", []):
                a, b = self._at(ev[0], now), self._at(ev[1], now)
                if a - 6 * 3600 <= now < b:
                    return float(b)
            return 0.0
        if kind == "light":
            off, on = self._at(p.get("off", "23:00"), now), self._at(p.get("on", "07:00"), now)
            for a, b in p.get("night", []):
                if self._at(a, now) <= now < self._at(b, now):
                    return 1.0
            return 0.0 if off <= now < on else 1.0
        return None

    def _sim_value(self, c: SourceConfig, now: float) -> float | None:
        kind = c.params.get("kind", c.role)
        if kind in ("home", "location", "calendar", "light"):
            return self._sim_context(c, kind, now)
        bed = self.bed()
        z = getattr(bed, "z", None)
        v: float | None = None
        if kind == "room_temp":
            v = round(getattr(bed, "room_c", 21.5) + 0.8 * math.sin(now / 5400) + self._rand.uniform(-0.05, 0.05), 2)
        elif kind == "room_humidity":
            v = round(45 + 4 * math.sin(now / 7200) + self._rand.uniform(-0.3, 0.3), 1)
        elif kind == "presence" and z and c.zone:
            occ = z[c.zone].occupied
            # radar loses perfectly still sleepers now and then
            v = 1.0 if occ and self._rand.random() > 0.08 else 0.0
        elif kind == "weight" and z and c.zone:
            base = float(c.params.get("kg", 75))
            v = round((base if z[c.zone].occupied else 0.0) + 1.8 + self._rand.uniform(-0.4, 0.4), 1)
        elif kind == "wearable_hr" and z and c.zone:
            fr = bed.latest().zones[c.zone].sensor if bed.latest() else None
            v = float(fr.hr + self._rand.choice([-1, 0, 0, 1])) if fr and fr.hr and z[c.zone].occupied else None
            st = bed.true_stage(c.zone, now) if hasattr(bed, "true_stage") else None
            if st and c.person:
                block = int(now // 600)                     # watches are wrong in stretches, not every 30 s
                if (block * 2654435761 % 100) < 15:
                    st = {"deep": "light", "light": "deep" if block % 2 else "rem", "rem": "light", "awake": "light"}[st]
                last = self._stage_t.get(c.id, now - 30)
                if now - last >= 30 or now < last:
                    start = last if 0 <= now - last <= 300 else now - 30     # never bridge a gap (out of bed, time jump)
                    self.store.append_stage(c.person, c.id, f"{c.device or c.name} · {c.driver}", start, now, st)
                    self._stage_t[c.id] = now
        elif kind == "skin_temp" and z and c.zone:
            # wrist skin temperature: drifts with the pad, rises when the sleeper is restless and warm
            if z[c.zone].occupied:
                zz = z[c.zone]
                pad = zz.measured if getattr(zz, "measured", None) is not None else 22.0
                v = round(33.6 + 0.22 * (pad - 20.0) + 0.15 * math.sin(now / 2400) + self._rand.uniform(-0.05, 0.05), 2)
        return v

    # ---------- views ----------
    def _note_activity(self, c: SourceConfig, on: bool, ts: float) -> None:
        """Count on/off changes per local day (Studio: "changed 6 times today · last at 11:42 PM")."""
        from datetime import datetime
        day = datetime.fromtimestamp(ts, getattr(self.clock, "tz", None)).date().isoformat()
        a = self._activity.get(c.id)
        if not a or a["day"] != day:
            a = {"day": day, "changes": 0, "last_change": (a or {}).get("last_change"), "prev": (a or {}).get("prev")}
            self._activity[c.id] = a
        if a["prev"] is not None and a["prev"] != on:
            a["changes"] += 1
            a["last_change"] = ts
        a["prev"] = on

    def describe(self, c: SourceConfig) -> dict:
        info = ROLE_INFO[c.role]
        l = self.latest.get(c.id, {})
        age = (self.clock.now_ts() - l["ts"]) if l.get("ts") else None
        def shown(k, v):
            if k in ("token", "password"):
                return "•••"
            if c.driver == "ics" and k == "url":                    # a feed address is a secret: show only where it points
                h = urlparse(ics_url(v)).hostname if v else ""
                return f"{h}/•••" if h else "•••"
            if c.driver == "mqtt-topic" and k == "url" and isinstance(v, str) and urlparse(v).password:
                u = urlparse(v)                                        # a broker password stays on the server
                return v.replace(f":{u.password}@", ":•••@", 1)
            return v
        return {**c.model_dump(exclude={"params"}), "params": {k: shown(k, v) for k, v in c.params.items()},
                "unit": c.unit or info["unit"], "lane": info["lane"], "role_label": info["label"],
                "value": l.get("value"), "ts": l.get("ts"), "age_s": round(age, 1) if age is not None else None,
                "error": l.get("error"),
                "online": age is not None and not l.get("error") and age < max(180, float(tuning_for(c.kind, c.tuning).get("max_age_s", 0) or 0) or 10**9),
                "kind_label": (TYPES.get(c.kind) or TYPES["custom"])["label"], "votes": votes(c),
                "vote": self.vote_value(c) if votes(c) else None, "vote_weight": self.vote_weight(c) if votes(c) else None,
                "tuning_resolved": tuning_for(c.kind, c.tuning), "health": self.health_of(c),
                "poll_s": poll_s(c) if c.driver == "ha-entity" else None,
                "changes_today": (self._activity.get(c.id) or {}).get("changes"),
                "last_change": (self._activity.get(c.id) or {}).get("last_change"),
                # the raw values it has sent lately (newest last), for "what each reading means" in Studio
                "raw_seen": [{"value": k, "ts": v} for k, v in (self._raw_seen.get(c.id) or {}).items()]}

    def map_raw(self, c: SourceConfig, state) -> tuple[float | None, str | None] | None:
        """Note the raw value (Studio shows the live one and the last few), and apply the device's value map:
        (value, note) when the map says what this raw value means, None to read it the usual way."""
        if isinstance(state, (dict, list)):
            return None
        raw = str(state).strip().lower()[:30]
        seen = self._raw_seen.setdefault(c.id, {})
        seen.pop(raw, None)
        seen[raw] = self.clock.now_ts() if self.clock else 0.0
        while len(seen) > 8:
            seen.pop(next(iter(seen)))
        vm = tuning_for(c.kind, c.tuning).get("value_map") or {}
        if not isinstance(vm, dict) or raw not in vm:
            return None
        m = vm[raw]
        return (1.0, None) if m == "yes" else (0.0, None) if m == "no" else (None, f"\"{raw}\" is set to be ignored")

    def _raw(self, c: SourceConfig, t: dict, value: float) -> bool:
        """Does this one reading say someone is here (before settle and hold timing)?"""
        st = self._occ.get(c.id, {})
        if c.kind == "scale":
            # a person-sized load above the empty-bed zero (bedding, laundry, pets and drift are not; engine/bedscale.py)
            thr = self.scale_threshold(c, t)
            hyst = max(float(t.get("hysteresis_kg", 0) or 0), 0.1 * thr)
            on = self.net_kg(c, value) >= (thr - hyst if st.get("raw") else thr)   # once on, it has to drop clearly below to go off
        elif c.kind == "custom":
            thr, cmp = float(t.get("threshold", 0.5)), t.get("compare", ">=")
            on = {">=": value >= thr, "<=": value <= thr, "==": abs(value - thr) < 1e-9, "!=": abs(value - thr) >= 1e-9}.get(cmp, value >= thr)
        else:
            on = value >= 0.5
        return (not on) if t.get("invert") else on

    def _track(self, c: SourceConfig, t: dict, value: float, ts: float) -> None:
        st = self._occ.setdefault(c.id, {"raw": None, "since": ts, "last_true": None})
        raw = self._raw(c, t, value)
        settle = float(t.get("min_on_s", 0) or 0)
        if st["raw"] and not raw and ts - st["since"] >= settle:
            st["last_true"] = ts                  # it was on right up to now: the hold starts from this moment
        if raw != st["raw"] or ts < st.get("since", ts):
            st["raw"], st["since"] = raw, ts
        if raw and ts - st["since"] >= settle:
            st["last_true"] = ts

    def vote_value(self, c: SourceConfig, now: float | None = None) -> float | None:
        """This device's in-bed vote right now (1 or 0), with its settle and hold timing, or None if it has nothing to say."""
        now = now if now is not None else self.clock.now_ts()
        t = tuning_for(c.kind, c.tuning)
        if self.quarantined(c):
            return None                                   # acting up: set aside until it settles
        l = self.latest.get(c.id)
        if not l or l.get("value") is None:
            return None
        max_age = float(t.get("max_age_s", 0) or 0)
        if max_age and now - (l.get("ts") or 0) > max_age:
            return None                                   # quiet for too long to trust
        if c.driver == "ha-entity" and now - (l.get("seen") or l.get("ts") or 0) > 3 * poll_s(c) + 60:
            return None                                   # polled, but the polls stopped coming back: HA or the device is gone
        st = self._occ.get(c.id)
        if not st:
            return None
        if st["raw"] and now - st["since"] >= float(t.get("min_on_s", 0) or 0):
            st["last_true"] = now                         # still on: keep the hold window fresh between reports
        last = st.get("last_true")
        return 1.0 if last is not None and now - last <= float(t.get("hold_s", 0) or 0) + 0.5 else 0.0

    def vote_weight(self, c: SourceConfig) -> float:
        w = tuning_for(c.kind, c.tuning).get("weight")
        return float(w) if w is not None else 0.3

    def zone_signals(self, zone: str) -> dict[str, float | None]:
        """Each voting device's current vote for one side (0/1), keyed role:id."""
        out: dict[str, float | None] = {}
        now = self.clock.now_ts()
        for c in self.configs:
            if c.zone != zone or not c.enabled or not votes(c):
                continue
            v = self.vote_value(c, now)
            if v is not None:
                out[f"{'weight' if c.role == 'weight' else 'presence'}:{c.id}"] = v
        return out


def _ago(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 3600} h" if s >= 7200 else f"{s // 60} min" if s >= 120 else f"{s} s"


def default_sim_sources() -> list[SourceConfig]:
    return [
        SourceConfig(id="simroom", name="Bedroom temperature", driver="sim", role="room_temp", device="Simulated room sensor", params={"kind": "room_temp"}),
        SourceConfig(id="simhum", name="Bedroom humidity", driver="sim", role="room_humidity", device="Simulated room sensor", params={"kind": "room_humidity"}),
        SourceConfig(id="simradl", name="Bedroom radar", driver="sim", role="presence", zone="left", kind="radar", device="Simulated mmWave radar", group="sim-radar", params={"kind": "presence"}),
        SourceConfig(id="simradr", name="Bedroom radar", driver="sim", role="presence", zone="right", kind="radar", device="Simulated mmWave radar", group="sim-radar", params={"kind": "presence"}),
        SourceConfig(id="simkgl", name="Bed scale", driver="sim", role="weight", zone="left", device="Simulated bed scale", group="sim-scale", params={"kind": "weight", "kg": 78}),
        SourceConfig(id="simkgr", name="Bed scale", driver="sim", role="weight", zone="right", device="Simulated bed scale", group="sim-scale", params={"kind": "weight", "kg": 64}),
        SourceConfig(id="simwatch", name="Watch heart rate", driver="sim", role="wearable_hr", zone="left", person="p1", device="Simulated watch", params={"kind": "wearable_hr"}),
        SourceConfig(id="simskin", name="Watch skin temperature", driver="sim", role="skin_temp", zone="left", person="p1", device="Simulated watch", params={"kind": "skin_temp"}),
    ]
