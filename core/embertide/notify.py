"""Notifications: pluggable providers, a few useful triggers, and one-tap actions.

Providers (configured in Studio → Notifications, stored in kv "notify"):
- ntfy: any ntfy server (self-hosted or ntfy.sh). Works with the ntfy Android app and UnifiedPush. Action buttons
  POST straight back to this server.
- ha: a Home Assistant notify service (e.g. notify.mobile_app_pixel), so the HA Companion app on Android or iPhone
  shows it. Action buttons open a small confirm page.
- (Web Push comes with HTTPS: browsers only allow it on secure pages.)

Each provider is for one person (their phone) or for the household (problems with the bed go there).

Action buttons never carry a password or API key. Each button is a random single-use code that can do exactly one
thing (e.g. "pause Alex's side for an hour") and expires after 12 hours.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import ipaddress
import json
import re
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
ACTION_TTL_S = 12 * 3600
APP_ACTS = {"pause", "skip", "start_now", "resume", "manual_off", "feel_cold", "feel_right", "feel_warm", "pet_yes", "pet_me", "pet_unsure",
            "away_away", "away_night", "away_home", "away_back", "away_still", "care_done", "care_later"}   # the only things a notification button can do in the app
MAX_DEVICES = 6
OFFLINE_AFTER_S = 300
STALE_AFTER_S = 600
LEFT_ON_AFTER_S = 20 * 60     # on by hand, nobody in it this long → ask to turn it off
# How often (docs/notifications.md). A kind's daily cap per person (household messages count under "household"),
# and all capped kinds together per person per day. EXEMPT: things the person asked for themselves.
IGNORED_AFTER_S = 20 * 60          # a side ignoring commands this long gets a "not taking changes" alert
TICK_FAIL_AFTER = 120              # a person's update failing this many ticks in a row (~2 min) gets a "not updating" alert
CAPS = {"morning": 1, "digest": 1, "pet_question": 1, "away_precool": 1, "empty_side": 1, "not_started": 1, "left_on": 2, "problem": 3, "hub": 3, "maintenance": 1,
        "account": 1, "reach": 2, "bridge": 1, "storage": 1, "wearable": 1, "bed_silent": 1, "sensor": 3, "target": 2, "review": 1}
DAILY_MAX = 4
DEDUP_S = 6 * 3600            # the same message (person, kind, title) again within this is dropped
EXEMPT = {"test", "action_done"}
# about you being out of bed or away: you're up, so these go out in the evening even after bedtime; from midnight
# to your wake they're held like everything else (a 2:30 AM "your side is empty" would wake you wherever you are)
AWAKE_KINDS = {"away_precool", "empty_side", "left_on"}
# not_started follows its own time rule instead (see _observe_person): any time except the last hour before wake. Quiet hours
# don't hold it: it's about getting into bed, and holding it for the morning summary would make it useless
OWN_TIMING_KINDS = {"not_started", "maintenance", "account", "reach", "bridge", "storage", "wearable", "sensor"}    # maintenance and the "fix this when you can" kinds: _care() / _gaps() keep to daytime and out of anyone's night themselves
NOT_STARTED_QUIET_BEFORE_WAKE_S = 3600
# maintenance reminders (care.py): only in the day, local time, 9 AM up to (not including) 7 PM
CARE_FROM_HOUR, CARE_TO_HOUR = 9, 19
CARE_CHECK_EVERY_S = 300
# "fix this when you can" notices (_gaps): daytime only, at most once a day, a few reminders and then quiet until it is fixed
GAP_REMINDERS = 4
REACH_AFTER_FAILS = 3                  # this many notifications in a row that reached a person on no channel at all
BRIDGE_DOWN_AFTER_S = 3 * 3600         # the Home Assistant link down this long
STORAGE_LOW_BYTES = 1 << 30            # under 1 GB free, or under 5% of the disk
STORAGE_LOW_FRACTION = 0.05
WEARABLE_QUIET_NIGHTS = 2              # nights in bed in a row with no heart rate from any watch or ring
WEARABLE_SETTLE_S = 5 * 3600           # the newest night must have ended this long ago (the phone has had time to sync it)
WEARABLE_CHECK_EVERY_S = 3600
BLIND_MIN_RUN_S = 3 * 3600             # a night must have run this long for "the bed's sensors said nothing" to be judged
BLIND_SHARE = 0.9                      # ... with no usable presence reading for this share of it
SENSOR_FLIP_AFTER_S = 2 * 3600         # a presence sensor set aside as erratic this long
SENSOR_STUCK_AFTER_S = 24 * 3600       # a radar or motion sensor saying "someone is here" without a break this long ...
SENSOR_STUCK_EMPTY_S = 4 * 3600        # ... while the bed's own strip showed the side empty at least this long in the last 24 h
TARGET_STEADY_S = 40 * 60              # a steady target, still this far off, for this long
TARGET_OFF_C = 4 * 5 / 9               # more than 4 °F away
TARGET_FAR_C = 5.0                     # more than 9 °F away ...
TARGET_FAR_S = 3600                    # ... for over an hour: the owners are told at once
TARGET_TREND_S = 20 * 60               # "not moving towards it": compared with this long ago
TARGET_MOVE_C = 0.3                    # less progress than this over TARGET_TREND_S counts as not moving
TRACK_SAVE_EVERY_S = 300
GAP_KINDS_OWNER = {"hub": "hub", "bridge": "bridge", "storage": "storage", "sensor": "sensor"}    # message kind → the owner switch that governs it
CARE_DONE_LABEL, CARE_LATER_LABEL = "It's done", "Remind me next week"      # the same on every channel (app, ntfy, Home Assistant, web page)
RUNNING = ("rhythm", "out_of_bed_hold")
NIGHT_ACTS = {"pause", "skip", "start_now"}      # about one night: they stop working when that night ends (see new_action)


def review_excerpt(answer: str, limit: int = 180) -> str:
    """The first one or two sentences of a review, about `limit` characters at most, ending on a sentence boundary
    (a single long sentence is cut at a word instead)."""
    import re
    parts = [x.strip() for x in re.findall(r".+?(?:[.!?](?=\s|$)|$)", answer.strip()) if x.strip()]
    out = ""
    for x in parts[:2]:
        if len((out + " " + x).strip()) <= limit:
            out = (out + " " + x).strip()
        else:
            break
    if out:
        return out
    return answer[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"


def summary_line(s: dict) -> str:
    """The night in one line: "7 h 50 min in bed · settled in 12 min · 1 flash · up 1×"."""
    bits = []
    if s.get("in_bed_min"):
        h, m = divmod(int(s["in_bed_min"]), 60)
        bits.append(f"{h} h {m} min in bed")
    if s.get("time_to_settle_min") is not None:
        bits.append(f"settled in {s['time_to_settle_min']} min")
    fl = (s.get("hot_flashes") or 0) + (s.get("cold_flashes") or 0)
    if fl:
        bits.append(f"{fl} flash{'es' if fl != 1 else ''}")
    ups = s.get("times_up") or 0
    bits.append("didn't get up" if not ups else f"up {ups}×")
    return " · ".join(bits)


def save_feel(c, pid: str, temp: str, now: float, source: str = "app", night: str | None = None) -> str:
    """Store "how was the temperature" for the night that just ended (the morning check-in). Returns the night's date."""
    from datetime import datetime, timedelta
    from .engine.rhythm import night_date
    if temp not in ("cold", "right", "warm"):
        raise ValueError("temp is cold, right or warm")
    d = night or night_date(datetime.fromtimestamp(now, c.clock.tz) - timedelta(hours=12)).isoformat()
    prev = c.store.get(f"feedback:{pid}:{d}", {}) or {}
    c.store.put(f"feedback:{pid}:{d}", {**prev, "temp": temp, "ts": now})
    c.store.event(now, "feedback", pid, source, night=d, temp=temp)
    return d


@dataclass
class Action:
    label: str
    act: str                 # tonight action: pause | skip | start_now | resume; feel_*; pet_yes | pet_me | pet_unsure
    minutes: int | None = None
    ref: str | None = None   # what it's about (the pet question's id)


# Where a tap on a notification goes when the message doesn't name a page itself (phone screens; Studio only where a
# message asks for it). The front page is Tonight, where the pet and away questions are answered.
DEFAULT_LINKS = {"review": "/", "morning": "/sleep", "digest": "/sleep", "pet_question": "/", "away_check": "/", "maintenance": "/settings#care",
                 "hub": "/settings#hub", "problem": "/", "wearable": "/settings#watch", "account": "/settings"}
# What a Studio page is for someone without an owner account (Studio's owner tools aren't theirs)
PHONE_FOR_STUDIO = {"/studio/hub": "/settings#hub", "/studio/devices": "/", "/studio/notifications": "/settings",
                    "/studio/integrations": "/settings", "/studio/data": "/settings"}


def link_for(link: str | None, kind: str, owner: bool = True) -> str:
    """The page a notification opens: its own link, else the kind's usual page, else the front page; the phone
    equivalent of a Studio page when the person it goes to isn't an owner."""
    link = link or DEFAULT_LINKS.get(kind, "/")
    if link.startswith("/studio") and not owner:
        return PHONE_FOR_STUDIO.get(link.split("#")[0].rstrip("/"), "/settings")
    return link


@dataclass
class Message:
    kind: str
    title: str
    body: str
    person: str | None = None          # None = household
    actions: list[Action] = field(default_factory=list)
    priority: int = 3                  # 1..5, ntfy scale
    tags: list[str] = field(default_factory=list)
    app: bool = True                   # False: ntfy / Home Assistant only (the app shows its own "done" after a button)
    urgent: bool = False               # goes out even in quiet hours (still deduplicated and capped)
    link: str | None = None            # where a tap on a web notification goes (a page that fixes the problem); None = the app's front page
    subject: str | None = None         # who the message is about when it goes to someone else (reach: the person who can't be reached)


def voice_say(kind: str, slots: dict, **kw):
    from . import voice
    return voice.say(kind, slots, **kw)


def _h(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class Notifier:
    def __init__(self, controller, public_url: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.c = controller
        self.store = controller.store
        self.default_public_url = public_url
        self.client = client
        from pathlib import Path
        from .fcm import Fcm
        self.fcm = Fcm(Path(self.store.path).parent if self.store.path != ":memory:" else Path("."), client)
        from .web_push import WebPushKeys
        self.webpush = WebPushKeys(Path(self.store.path).parent if self.store.path != ":memory:" else None)
        self._offline_since: float | None = None
        self._stale_since: dict[str, float] = {}
        self._empty_since: dict[str, float] = {}     # person → when their by-hand session was last seen empty
        self._sent_cache: list[dict] | None = None
        self._snap: dict | None = None               # the last snapshot seen (quiet hours, morning timing)
        self._tasks: set[asyncio.Task] = set()
        self.ha_listeners: dict[str, HaActionListener] = {}      # by HA url
        self.owner_persons = None        # () -> ids of people with an owner account (set by the app); hub messages go only to them
        self.care_persons = None         # () -> ids of people with an owner or sleeper account (set by the app); care reminders go to them
        self.account_states = None       # () -> [(person id, service name, needs signing in again)] for connected cloud accounts (set by the app)
        self.bridge_status = None        # () -> (configured, state) for the Home Assistant link (set by the app)
        self.disk_usage = None           # (path) -> (total, used, free) bytes; None = the real disk (tests set their own)
        self._bridge_down_since: float | None = None
        self.sensor_states = None        # () -> sensors that may need a look (SourceManager.attention); None = read them from the controller's sources

    # ---------- delivery log (kept in the store, on the household clock, so it survives restarts and the simulator) ----------
    @property
    def sent_log(self) -> list[dict]:
        if self._sent_cache is None:
            self._sent_cache = list(self.store.get("notify_deliveries", []))
        return self._sent_cache

    def _log_delivery(self, entry: dict) -> None:
        clock = getattr(getattr(self, "c", None), "clock", None)
        entry = {"ts": clock.now_ts() if clock else time.time(), **entry}
        log = (self.sent_log + [entry])[-50:]
        self._sent_cache = log
        self.store.put("notify_deliveries", log)

    # ---------- configuration ----------
    @property
    def providers(self) -> list[dict]:
        return self.store.get("notify", [])

    def save_providers(self, items: list[dict]) -> None:
        self.store.put("notify", items)

    @property
    def public_url(self) -> str | None:
        v = (self.store.get("notify_settings", {}) or {}).get("public_url") or self.default_public_url
        return v.rstrip("/") if v else None

    def _once(self, key: str) -> bool:
        sent = self.store.get("notify_sent", [])
        if key in sent:
            return False
        sent.append(key)
        self.store.put("notify_sent", sent[-300:])
        return True

    # ---------- how often, and when (docs/notifications.md) ----------
    def _day(self, now: float):
        return datetime.fromtimestamp(now, self.c.clock.tz).date()

    def _say(self, kind: str, slots: dict, person, now: float):
        from . import voice
        return voice.say(kind, slots, person=person, day=self._day(now), store=self.store)

    def _pv(self, pid: str) -> dict | None:
        return next((x for x in (self._snap or {}).get("people", []) if x.get("id") == pid), None)

    def quiet(self, pid: str, now: float) -> bool:
        """Quiet hours: this person is in bed, or it's between their bedtime and wake and they aren't known to be out
        or up. On by default (NotifyPrefs.quiet_hours)."""
        try:
            person = self.c.person(pid)
        except KeyError:
            return False
        if not person.settings.notify.quiet_hours:
            return False
        pv = self._pv(pid)
        if not pv:
            return False
        n, sch = pv.get("night") or {}, pv.get("schedule")
        if n.get("state") in RUNNING:
            return True
        return bool(sch) and sch["bedtime"] <= now < sch["wake"] and n.get("state") not in ("away", "missed")

    def _small_hours(self, pid: str, now: float) -> bool:
        """Midnight to this person's wake time (quiet hours on)."""
        try:
            if not self.c.person(pid).settings.notify.quiet_hours:
                return False
        except KeyError:
            return False
        sch = (self._pv(pid) or {}).get("schedule")
        if not sch:
            return False
        midnight = datetime.fromtimestamp(sch["wake"], self.c.clock.tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return midnight <= now < sch["wake"]

    def _morning_pending(self, person, now: float) -> bool:
        """This person's morning summary is still to come (so anything held, or a pet question, waits for it)."""
        pv = self._pv(person.id)
        if not person.settings.notify.morning or not pv or not pv.get("schedule"):
            return False
        sch, n = pv["schedule"], pv.get("night") or {}
        if n.get("guest") or n.get("state") in ("away", "missed", "guest"):
            return False
        if not sch["bedtime"] - 3 * 3600 <= now < sch["end"] + 4 * 3600:
            return False
        night = datetime.fromtimestamp(sch["bedtime"], self.c.clock.tz).date().isoformat()
        return f"morning:{person.id}:{night}" not in self.store.get("notify_sent", [])

    def _count(self, who: str, kind: str, now: float, add: bool = False) -> tuple[int, int]:
        """(sent of this kind today, all capped kinds sent today) for `who` (a person id or "household")."""
        day = self._day(now).isoformat()
        rec = self.store.get("notify_counts", {}) or {}
        if rec.get("day") != day:
            rec = {"day": day}
        mine = rec.setdefault(who, {})
        if add:
            mine[kind] = mine.get(kind, 0) + 1
            self.store.put("notify_counts", rec)
        return mine.get(kind, 0), sum(mine.values())

    def _hold(self, m: Message, now: float) -> None:
        held = list(self.store.get("notify_held", []) or [])
        held.append({"ts": now, "kind": m.kind, "title": m.title, "body": m.body, "person": m.person, "given": []})
        self.store.put("notify_held", held[-20:])
        self.store.event(now, "notification_held", m.person, "notify", title=m.title, about=m.kind)

    def _wants_held(self, h: dict, pid: str) -> bool:
        """Would this person have been sent this message when it was held? Their own switches and role still apply when
        it is told later (a hub fault is the owner's; bed trouble goes only to people who want it), and a note about the
        evening that is no longer true by morning ("your side is empty", "you're not home") is dropped, not told late."""
        try:
            person = self.c.person(pid)
        except KeyError:
            return False
        prefs, k = person.settings.notify, h["kind"]
        if k == "problem":
            return prefs.problems
        if k == "hub":
            owners = self.owner_persons() if self.owner_persons else set()
            return prefs.hub and (not owners or pid in owners)
        if k == "target":
            return prefs.target
        if k == "bed_silent":
            return prefs.bed_silent
        if k == "review":
            return False                  # a review of a night is not news a day later
        if k in ("away_precool", "empty_side"):
            return False
        if k == "left_on":
            pv = self._pv(pid)
            n = (pv or {}).get("night") or {}
            return bool(pv) and bool((n.get("override") or {}).get("manual")) and n.get("target_c") is not None                 and (pv.get("presence") or {}).get("present") is False
        return True

    def _take_held(self, pid: str) -> list[dict]:
        """Held messages for this person (their own, and household ones they haven't had yet) that are still theirs to
        hear and still true; all of them are marked given, so nothing is told twice."""
        held = list(self.store.get("notify_held", []) or [])
        if not held:
            return []
        people = [p.id for p in self.c.household.people]
        mine = [h for h in held if h["person"] in (None, pid) and pid not in h["given"]]
        for h in mine:
            h["given"].append(pid)
        keep = [h for h in held if not (h["person"] is not None and h["person"] in h["given"])
                and not (h["person"] is None and all(x in h["given"] for x in people))]
        self.store.put("notify_held", keep)
        return [h for h in mine if self._wants_held(h, pid)]

    def _held_lines(self, items: list[dict]) -> str:
        tz = self.c.clock.tz
        return " ".join(f"{datetime.fromtimestamp(h['ts'], tz).strftime('%I:%M %p').lstrip('0')}: {h['title'].rstrip('.')}."
                        for h in items[:4])

    def _allowed(self, m: Message, now: float) -> str | None:
        """None if this message may go out now, else why not: "held" (quiet hours), "duplicate", or "cap" (today's
        limit for its kind, or for the person all together)."""
        if m.kind in EXEMPT:
            return None
        recent = self.store.get("notify_recent", []) or []
        if any(r[0] == m.person and r[1] == m.kind and r[2] == m.title and now - r[3] < DEDUP_S for r in recent):
            return "duplicate"
        pids = [m.person] if m.person else [p.id for p in self.c.household.people]
        if m.kind in OWN_TIMING_KINDS:
            pass
        elif m.kind in AWAKE_KINDS:
            if any(self._small_hours(pid, now) for pid in pids):
                return "held"
        elif not m.urgent and any(self.quiet(pid, now) for pid in pids):
            return "held"
        kind_n, all_n = self._count(m.person or "household", m.kind, now)
        if kind_n >= CAPS.get(m.kind, 2) or (m.person and all_n >= DAILY_MAX):
            return "cap"
        return None

    def _sent(self, m: Message, now: float) -> None:
        if m.kind in EXEMPT:
            return
        self._count(m.person or "household", m.kind, now, add=True)
        recent = [r for r in (self.store.get("notify_recent", []) or []) if now - r[3] < DEDUP_S]
        self.store.put("notify_recent", (recent + [[m.person, m.kind, m.title, now]])[-40:])

    # ---------- triggers ----------
    def _observe_person(self, person, pv: dict, now: float, out: list, online: bool) -> None:
        from . import voice
        tz = self.c.clock.tz
        prefs = person.settings.notify
        n, sch = pv["night"], pv.get("schedule")
        if n.get("guest"):
            return                                  # a guest's side: no reminders, nothing about the guest
        # turned on by hand outside the rhythm (a nap, an early night) and nobody has been in it for a while
        o = n.get("override") or {}
        on_by_hand = bool(o.get("manual")) and n.get("target_c") is not None and not n.get("flash")
        if on_by_hand and pv.get("presence", {}).get("present") is False:
            since = self._empty_since.setdefault(person.id, now)
            if (prefs.left_on and now - since >= LEFT_ON_AFTER_S
                    and self._once(f"left_on:{person.id}:{int(o.get('set_ts') or 0)}")):
                t, b = self._say("left_on", {"name": person.name, "minutes": round((now - since) / 60)}, person, now)
                out.append(Message("left_on", t, b, person.id, [Action("Turn it off", "manual_off")], priority=3, tags=["zzz"]))
        else:
            self._empty_since.pop(person.id, None)
        m = self._pet_question(person, now)
        if m:
            out.append(m)
        m = self._away_check(person, now)
        if m:
            out.append(m)
        if not sch:
            return
        night = datetime.fromtimestamp(sch["bedtime"], tz).date().isoformat()
        if prefs.away_precool and n["state"] == "away" and not n.get("away") and now >= sch["precool"] and self._once(f"away:{person.id}:{night}"):
            t, b = self._say("away_precool", {"name": person.name}, person, now)
            out.append(Message("away_precool", t, b, person.id,
                               [Action("Wait an hour", "pause", 60), Action("Off tonight", "skip"), Action("Start anyway", "start_now")],
                               tags=["house"]))
        if prefs.empty_side and n["state"] == "missed" and self._once(f"empty:{person.id}:{night}"):
            t, b = self._say("empty_side", {}, person, now)
            out.append(Message("empty_side", t, b, person.id, [Action("Off tonight", "skip")], priority=2, tags=["zzz"]))
        # lying in their side a long while, but the bed radar never backed it, so the night hasn't started (controller:
        # settle.offer_start). Offered once a night, any time of the night
        # except the last hour before wake and after it (a late bedtime still gets it)
        if (prefs.not_started and (n.get("settle") or {}).get("offer_start") and n["state"] in ("waiting", "missed") and not n.get("away")
                and now < sch["wake"] - NOT_STARTED_QUIET_BEFORE_WAKE_S and self._once(f"not_started:{person.id}:{night}")):
            t, b = self._say("not_started", {}, person, now)
            out.append(Message("not_started", t, b, person.id, [Action("Start now", "start_now")], priority=3, tags=["zzz"]))
        if prefs.morning and n["state"] == "done" and sch["end"] <= now < sch["end"] + 4 * 3600 and self._once(f"morning:{person.id}:{night}"):
            m = self._morning(person, night, now)
            if m:
                out.append(m)
        # a bed sensor that stops updating while someone's night is running
        # (not while the whole bed is unreachable: "Can't reach the bed" already says so, once)
        stale = bool(pv.get("presence", {}).get("stale")) and n["state"] in ("rhythm", "out_of_bed_hold") and online
        if stale:
            since = self._stale_since.setdefault(pv["zone"], now)
            if now - since >= STALE_AFTER_S and self._once(f"stale:{pv['zone']}:{night}"):
                t, b = voice.say("problem_stale", {"side": pv["zone"].title()}, day=self._day(now), store=self.store)
                out.append(Message("problem", t, b, None, priority=4, tags=["warning"]))
        else:
            self._stale_since.pop(pv["zone"], None)
        # the bed is online but this side keeps ignoring what it's told (a whole night off-plan, otherwise unseen)
        since = (getattr(self.c, "unconfirmed_since", None) or {}).get(pv["zone"])
        if (online and since and now - since >= IGNORED_AFTER_S and n["state"] in ("rhythm", "out_of_bed_hold", "waiting")
                and self._once(f"ignored:{pv['zone']}:{night}")):
            side = pv["zone"].title()
            out.append(Message("problem", f"The bed isn't taking changes on the {side.lower()} side",
                               f"It has ignored the last few temperature changes for {round((now - since) / 60)} minutes, so that "
                               "side may not be following the plan. Turning the bed's power off and on usually clears it."
                               + self._ack_note(),
                               None, priority=4, tags=["warning"]))

    def _ack_note(self) -> str:
        """Whether the hub said it received the last command we sent (its own acknowledgement), for the "not taking
        changes" alert: it tells a lost message from a refused one. Empty when the driver can't say."""
        got = ((self._snap or {}).get("system", {}).get("hub") or {}).get("command_received")
        if got is False:
            return " The bed hasn't confirmed receiving the last change either."
        if got is True:
            return " The bed did report receiving the last change, so the connection is working."
        return ""

    def _hub(self, snap: dict, now: float, out: list) -> None:
        """The bed's hub needs a look (system.hub): a fault appears, or the water needs checking. Once per problem per
        day. Quiet hours hold it for the morning like other non-urgent messages, except when the hub's safety check
        is failing while a side's night is running (then it goes out at once)."""
        from . import hub as tw
        t = (snap.get("system") or {}).get("hub")
        if not t or not snap.get("system", {}).get("online", True):
            return
        day = self._day(now).isoformat()
        problems: list[tuple[str, str, str]] = []                    # (key, what, advice)
        for f in t.get("faults") or []:
            what = f["name"] + (f" ({f['zone']} side)" if f.get("zone") else "")
            problems.append((f"{f['code']}:{f.get('zone')}", what, tw.fault_advice(f["code"])))
        if t.get("water_ok") is False:
            problems.append(("water", tw.STATUS_WATER, "The hub's water may be low. Have a look when you can, and follow the maker's instructions for adding water."))
        if t.get("safety_ok") is False:
            problems.append(("safety", "The hub's safety check isn't passing", "It protects itself. Restarting the hub may clear it; if it comes back, contact the maker."))
        running = any((pv.get("night") or {}).get("state") in RUNNING for pv in snap.get("people", []))
        for key, what, advice in problems:
            if self._once(f"hub:{key}:{day}"):
                title, body = self._say("problem_hub", {"what": what, "advice": advice}, None, now)
                out.append(Message("hub", title, body, None, priority=3, tags=["warning"],
                                   urgent=t.get("safety_ok") is False and running))

    def _care(self, snap: dict, now: float, out: list) -> None:
        """A care job is due (care.py): one calm message to each owner or sleeper who has the switch on (not view-only
        accounts, not anyone marked away), with It's done and Remind me next week buttons. Daytime only (9 AM to 7 PM), never while anyone's night is running or inside anyone's quiet hours, at most one
        maintenance message a day (a second due job waits for tomorrow), one follow-up a week later if still not done,
        then silence until it is marked done or turned back on. Low water is not here: the hub message covers it."""
        from . import care as carelib
        last = self.__dict__.get("_care_at", 0)
        if last <= now < last + CARE_CHECK_EVERY_S:
            return
        self.__dict__["_care_at"] = now
        if not CARE_FROM_HOUR <= datetime.fromtimestamp(now, self.c.clock.tz).hour < CARE_TO_HOUR:
            return
        if any((pv.get("night") or {}).get("state") in RUNNING for pv in snap.get("people", [])):
            return
        if any(self.quiet(p.id, now) for p in self.c.household.people):
            return
        from .engine.away import is_away
        who = self.care_persons() if self.care_persons else set()      # owner and sleeper accounts; none linked = everyone
        pids = [p.id for p in self.c.household.people
                if p.settings.notify.maintenance and (not who or p.id in who) and not is_away(self.store, p.id, now)]
        if not pids:
            return
        care = carelib.of(self.c)
        todo = care.notices(self._day(now))
        if not todo:
            return
        task, kind = todo[0]
        what = task.get("reminder") or f"{task['title']} is due"
        if kind == "follow":
            what = what[:1].lower() + what[1:]
        more = len(todo) - 1
        extra = f" {more} other job{'s are' if more != 1 else ' is'} due too." if more else ""
        why = task.get("why") or "It's time for a bit of care for the bed."
        if not why.endswith((".", "!", "?")):
            why += "."
        msgs = []
        for pid in pids:
            t, b = self._say("care_again" if kind == "follow" else "care_due", {"what": what, "why": why, "more": extra},
                             self.c.person(pid), now)
            m = Message("maintenance", t, b.strip(), pid,
                        [Action(CARE_DONE_LABEL, "care_done", ref=task["id"]), Action(CARE_LATER_LABEL, "care_later", ref=task["id"])],
                        priority=2, tags=["broom"])
            if self._allowed(m, now) is None:             # a person at their cap (or a repeat) is skipped; the others still hear
                msgs.append(m)
        if not msgs:
            return                                        # nobody could be told: nothing is marked as told
        out.extend(msgs)
        care.mark_told(task["id"], kind, self._day(now))

    # ---------- "fix this when you can" (nothing urgent: a daytime note, at most once a day) ----------
    def _calm_daytime(self, snap: dict, now: float) -> bool:
        """9 AM to 7 PM, nobody's night running, nobody in quiet hours: the only time these notes go out (never held, never at night)."""
        if not CARE_FROM_HOUR <= datetime.fromtimestamp(now, self.c.clock.tz).hour < CARE_TO_HOUR:
            return False
        if any((pv.get("night") or {}).get("state") in RUNNING for pv in snap.get("people", [])):
            return False
        return not any(self.quiet(p.id, now) for p in self.c.household.people)

    def _gap_due(self, key: str, now: float, every_days: int = 1) -> bool:
        r = (self.store.get("notify_gaps", {}) or {}).get(key) or {}
        if r.get("n", 0) >= GAP_REMINDERS:
            return False
        if not r.get("day"):
            return True
        try:
            last = datetime.fromisoformat(r["day"]).date()
        except ValueError:
            return True
        d = (self._day(now) - last).days
        return d >= every_days or d < 0                 # (a clock that went back, as the simulator's can, counts as a new day)

    def _gap_told(self, key: str, now: float) -> None:
        rec = dict(self.store.get("notify_gaps", {}) or {})
        rec[key] = {"day": self._day(now).isoformat(), "n": (rec.get(key) or {}).get("n", 0) + 1}
        self.store.put("notify_gaps", rec)

    def _gap_clear(self, key: str) -> None:
        rec = dict(self.store.get("notify_gaps", {}) or {})
        if rec.pop(key, None) is not None:
            self.store.put("notify_gaps", rec)

    def _gaps(self, snap: dict, now: float, out: list) -> None:
        """Things a person needs to fix that used to raise nothing: a connected account that signed out, a phone that stopped
        getting notifications, the Home Assistant link down for hours, a nearly full disk. Each only when the server already
        knows it for sure, only by day, once a day, with a way to the screen that fixes it."""
        for check in (self._account_gap, self._reach_gap, self._bridge_gap, self._storage_gap,
                      self._track, self._wearable_gap, self._bed_silent_gap, self._sensor_gap, self._target_gap):
            try:
                check(snap, now, out)
            except Exception:  # noqa: BLE001  (one check failing never stops the others)
                log.exception("notification check %s failed", check.__name__)

    def _account_gap(self, snap: dict, now: float, out: list) -> None:
        states = self.account_states() if self.account_states else []
        for pid, service, needs in states:
            key = f"account:{pid}:{service}"
            if not needs:
                self._gap_clear(key)
                continue
            try:
                person = self.c.person(pid)
            except KeyError:
                continue
            if not person.settings.notify.account or not self._calm_daytime(snap, now) or not self._gap_due(key, now):
                continue
            t, b = self._say("account_again", {"service": service}, person, now)
            self._gap_told(key, now)
            out.append(Message("account", t, b, pid, priority=2, tags=["key"], link="/settings"))

    def _reach_gap(self, snap: dict, now: float, out: list) -> None:
        fails = self.store.get("notify_reach", {}) or {}
        for p in self.c.household.people:
            key = f"reach:{p.id}"
            n = fails.get(p.id, 0)
            if n < REACH_AFTER_FAILS:
                self._gap_clear(key)
                continue
            if not self._calm_daytime(snap, now) or not self._gap_due(key, now):
                continue
            t, b = voice_say("problem_reach", {"name": p.name, "n": n}, day=self._day(now), store=self.store)
            self._gap_told(key, now)
            out.append(Message("reach", t, b, None, priority=3, tags=["warning"], link="/studio/notifications", subject=p.id))

    def _bridge_gap(self, snap: dict, now: float, out: list) -> None:
        st = self.bridge_status() if self.bridge_status else None
        if not st or not st[0] or st[1] == "connected":
            self._bridge_down_since = None
            self._gap_clear("bridge")
            return
        self._bridge_down_since = self._bridge_down_since or now
        down = now - self._bridge_down_since
        if down < BRIDGE_DOWN_AFTER_S or not self._calm_daytime(snap, now) or not self._gap_due("bridge", now):
            return
        detail = st[1] if str(st[1]).startswith("not authorised") else "no answer from the MQTT broker"
        t, b = voice_say("problem_bridge", {"hours": max(1, round(down / 3600)), "detail": detail}, day=self._day(now), store=self.store)
        self._gap_told("bridge", now)
        out.append(Message("bridge", t, b, None, priority=2, tags=["warning"], link="/studio/integrations"))

    def _storage_gap(self, snap: dict, now: float, out: list) -> None:
        from pathlib import Path
        if self.store.path == ":memory:":
            return
        try:
            import shutil
            total, _used, free = (self.disk_usage or shutil.disk_usage)(Path(self.store.path).parent)
        except OSError:
            return
        if free >= STORAGE_LOW_BYTES and free >= STORAGE_LOW_FRACTION * total:
            self._gap_clear("storage")
            return
        if not self._calm_daytime(snap, now) or not self._gap_due("storage", now):
            return
        t, b = voice_say("problem_storage", {"free": f"{free / (1 << 30):.1f} GB" if free >= (1 << 30) else f"{max(1, free >> 20)} MB"},
                         day=self._day(now), store=self.store)
        self._gap_told("storage", now)
        out.append(Message("storage", t, b, None, priority=3, tags=["warning"], link="/studio/data"))

    # ---------- more things that went wrong without a word: a quiet watch, a silent bed, a bad presence sensor, a pad that won't heat ----------
    def _owner_ids(self) -> list[str]:
        """People with an owner account; empty when none is linked (then nothing is sent to "the owners" beyond the person it is about)."""
        owners = self.owner_persons() if self.owner_persons else set()
        return [p.id for p in self.c.household.people if p.id in owners]

    def _link(self, pid: str, m: Message) -> str:
        """Where a tap on this message opens the app for this person (no owner accounts linked = everyone counts as one)."""
        owners = self.owner_persons() if self.owner_persons else set()
        return link_for(m.link, m.kind, not owners or pid in owners)

    def _fix_link(self, pid: str, owner_page: str, other_page: str) -> str:
        owners = self.owner_persons() if self.owner_persons else set()
        return owner_page if (not owners or pid in owners) else other_page

    def _night_key(self, sch: dict) -> str:
        return datetime.fromtimestamp(sch["bedtime"], self.c.clock.tz).date().isoformat()

    @staticmethod
    def _deg(c: float, units: str) -> str:
        return f"{round(c * 9 / 5 + 32)}°" if units == "F" else f"{round(c)} °C"

    def _tracked(self) -> dict:
        """What the checks below add up between ticks (kept in memory, saved every few minutes so a restart loses little):
        per person, how much of this night ran with no usable presence reading; per side, when the bed's strip showed it empty."""
        t = self.__dict__.get("_trk")
        if t is None:
            saved = self.store.get("notify_track", {}) or {}
            t = self.__dict__["_trk"] = {"blind": dict(saved.get("blind") or {}), "empty": dict(saved.get("empty") or {}),
                                         "last": saved.get("last"), "saved": 0.0}
        return t

    def _track(self, snap: dict, now: float, out: list) -> None:
        t = self._tracked()
        last = t.get("last")
        dt = 0.0 if last is None or now < last else min(now - last, 900.0)
        t["last"] = now
        online = (snap.get("system") or {}).get("online", True)
        for pv in snap.get("people", []):
            sch, n = pv.get("schedule"), pv.get("night") or {}
            if not sch or n.get("guest"):
                continue
            night = self._night_key(sch)
            rec = t["blind"].get(pv["id"])
            if not rec or rec.get("night") != night:
                rec = t["blind"][pv["id"]] = {"night": night, "run": 0.0, "blind": 0.0}
            if dt and n.get("state") in RUNNING:
                rec["run"] += dt
                pr = pv.get("presence") or {}
                if online and pr.get("present") is None and pr.get("bed_present") is None:
                    rec["blind"] += dt
        hour = int(now // 3600)
        for zone, z in (snap.get("zones") or {}).items():
            e = t["empty"].setdefault(zone, {})
            if dt and online and z.get("bed_present") is False:
                e[str(hour)] = e.get(str(hour), 0.0) + dt
            for k in [k for k in e if int(k) < hour - 26]:
                del e[k]
        if now < t["saved"] or now - t["saved"] >= TRACK_SAVE_EVERY_S:
            t["saved"] = now
            self.store.put("notify_track", {k: v for k, v in t.items() if k != "saved"})

    def _empty_s(self, zone: str, now: float) -> float:
        hour = int(now // 3600)
        return sum(v for k, v in (self._tracked()["empty"].get(zone) or {}).items() if int(k) > hour - 24)

    def _bed_silent_gap(self, snap: dict, now: float, out: list) -> None:
        """The morning after a night that ran on the schedule because the bed's own sensors gave nothing usable for (nearly)
        all of it and no other presence sensor covered the side: told to that sleeper, once, after wake."""
        from .engine.away import is_away
        t = self._tracked()
        for pv in snap.get("people", []):
            sch, n = pv.get("schedule"), pv.get("night") or {}
            if not sch or n.get("guest") or n.get("state") != "done" or not sch["wake"] <= now < sch["end"] + 4 * 3600 or now < sch["end"]:
                continue
            night = self._night_key(sch)
            rec = t["blind"].get(pv["id"])
            if not rec or rec.get("night") != night or rec.get("judged") or rec["run"] < BLIND_MIN_RUN_S:
                continue
            try:
                person = self.c.person(pv["id"])
            except KeyError:
                continue
            key = f"bed_silent:{person.id}"
            rec["judged"] = True
            if rec["blind"] < BLIND_SHARE * rec["run"]:
                self._gap_clear(key)                               # a night with working sensors: it is fixed
                continue
            if is_away(self.store, person.id, now) or not person.settings.notify.bed_silent or not self._gap_due(key, now):
                continue
            if not self._once(f"bed_silent:{person.id}:{night}"):
                continue
            tt, b = self._say("bed_silent", {}, person, now)
            self._gap_told(key, now)
            out.append(Message("bed_silent", tt, b, person.id, priority=2, tags=["warning"],
                               link=self._fix_link(person.id, "/studio/hub", "/settings#hub")))

    def _wearable_silent(self, p, now: float) -> str | None:
        """None, or the device's name ("your watch or ring" when not known) if a watch or ring that has supplied night data in
        the last 14 days sent no heart rate on the last two nights in a row that the person was in bed."""
        from datetime import date, timedelta
        from .engine import away, wearables
        cov = [x for x in wearables.night_coverage(self.c, p, now, 4) if not x.get("in_progress")]
        if len(cov) < WEARABLE_QUIET_NIGHTS:
            return None
        a, b = cov[0], cov[1]

        def silent(x: dict) -> bool:
            return not any(v > 0 for v in x["hr"].values()) and not x.get("live")
        da, db = date.fromisoformat(a["date"]), date.fromisoformat(b["date"])
        today = datetime.fromtimestamp(now, self.c.clock.tz).date()
        if da < today - timedelta(days=1) or da - db != timedelta(days=1) or now - a["out_at"] < WEARABLE_SETTLE_S:
            return None                                            # not the last two nights in a row, or not given time to sync
        if not (silent(a) and silent(b)):
            return None
        periods, excluded = away.away_periods(self.store, p.id, now), away.excluded_nights(self.store, p.id)
        if any(x["date"] in excluded or away.away_share(periods, x["in_bed_at"], x["out_at"]) >= 0.5 for x in (a, b)):
            return None                                            # they were away on one of those nights
        for x in wearables.night_coverage(self.c, p, now, 14):
            if x["date"] in (a["date"], b["date"]):
                continue
            hr = {k: v for k, v in x["hr"].items() if v > 0}
            if hr:
                return max(hr, key=hr.get)[:40]
            if x.get("live"):
                return "your watch or ring"
        return None                                                # it never supplied a night: nothing to say it went quiet

    def _wearable_gap(self, snap: dict, now: float, out: list) -> None:
        """A watch or ring that used to supply nights has sent nothing for two nights: that person, daytime, once a day."""
        if not self._calm_daytime(snap, now):
            return
        from .engine.away import is_away
        checked = self.__dict__.setdefault("_wear_checked", {})
        for p in self.c.household.people:
            if not p.settings.notify.wearable or p.zone in self.c.household.guest or is_away(self.store, p.id, now):
                continue
            if 0 <= now - checked.get(p.id, -1e18) < WEARABLE_CHECK_EVERY_S:
                continue
            checked[p.id] = now
            key = f"wearable:{p.id}"
            device = self._wearable_silent(p, now)
            if device is None:
                self._gap_clear(key)
                continue
            if not self._gap_due(key, now):
                continue
            t, b = self._say("wearable_quiet", {"device": device}, p, now)
            self._gap_told(key, now)
            out.append(Message("wearable", t, b, p.id, priority=2, tags=["watch"], link="/settings#watch"))

    def _sensor_gap(self, snap: dict, now: float, out: list) -> None:
        """A presence sensor set aside for flipping for hours, or a radar/motion sensor that has said "someone is here" for over
        a day while the bed's own strip showed the side empty for hours: told to the owners, daytime, per device."""
        src = getattr(self.c, "sources", None)
        if self.sensor_states:
            states = self.sensor_states()
        elif src is not None and hasattr(src, "attention"):
            states = src.attention(now)
        else:
            states = []
        live: set[str] = set()
        for s in states:
            kf, ks = f"sensor:flip:{s['id']}", f"sensor:stuck:{s['id']}"
            name = (s.get("name") or "A presence sensor")[:40]
            fs, on = s.get("flapping_since"), s.get("on_since")
            if fs is not None:
                live.add(kf)
                if (now - fs >= SENSOR_FLIP_AFTER_S and self._calm_daytime(snap, now) and self._gap_due(kf, now)):
                    t, b = self._say("sensor_flip", {"name": name}, None, now)
                    self._gap_told(kf, now)
                    out.append(Message("sensor", t, b, None, priority=2, tags=["warning"], link="/studio/devices"))
            if on is not None:
                live.add(ks)
                if (now - on >= SENSOR_STUCK_AFTER_S and self._empty_s(s.get("zone") or "", now) >= SENSOR_STUCK_EMPTY_S
                        and self._calm_daytime(snap, now) and self._gap_due(ks, now, every_days=7)):
                    t, b = self._say("sensor_stuck", {"name": name}, None, now)
                    self._gap_told(ks, now)
                    out.append(Message("sensor", t, b, None, priority=2, tags=["warning"], link="/studio/devices"))
        for k in [k for k in (self.store.get("notify_gaps", {}) or {}) if k.startswith("sensor:") and k not in live]:
            self._gap_clear(k)                                     # behaving again, paused or removed: starts over if it returns

    def _slow_room(self, want: float, meas: float, now: float) -> bool:
        """A cold room with a warm target (or a warm room with a cool one): the pad is known to fall short there
        (engine/padlag.py), so a steady shortfall is not a fault."""
        try:
            room = self.c.room_c(now)
        except Exception:  # noqa: BLE001
            return False
        if room is None:
            return False
        from .engine.padlag import COLD_NOTE_C, WARM_ROOM_C
        return (want > meas and room < COLD_NOTE_C) or (want < meas and room > WARM_ROOM_C)

    def _target_gap(self, snap: dict, now: float, out: list) -> None:
        """A side whose target has been steady for 40 min while the pad is still more than 4 °F off and not moving towards it.
        Told to the sleeper (and the owners), held for the morning like other night trouble; far off (over 9 °F) for over an
        hour it goes at once to the owners. Once per night per side (the far one is its own, so it is never lost behind the first)."""
        trk = self.__dict__.setdefault("_tgt", {})
        ran = self.__dict__.setdefault("_tgt_ran", {})
        system = snap.get("system") or {}
        online = system.get("online", True)
        hub = system.get("hub") or {}
        hub_bad = bool(hub.get("faults")) or hub.get("water_ok") is False or hub.get("safety_ok") is False or hub.get("command_received") is False
        unconf = getattr(self.c, "unconfirmed_since", None) or {}
        for pv in snap.get("people", []):
            pid, zone = pv["id"], pv.get("zone")
            n, bed, sch = pv.get("night") or {}, pv.get("bed") or {}, pv.get("schedule")
            if not sch or n.get("guest") or not zone:
                trk.pop(pid, None)
                continue
            night = self._night_key(sch)
            rec = ran.get(pid)
            if not rec or rec["night"] != night:
                rec = ran[pid] = {"night": night, "s": 0.0, "flag": False, "last": now, "cleared": False}
            dt = min(max(0.0, now - rec["last"]), 900.0)
            rec["last"] = now
            running = n.get("state") in RUNNING
            if running:
                rec["s"] += dt
            if n.get("state") == "done" and not rec["cleared"] and rec["s"] >= 2 * 3600 and not rec["flag"]:
                rec["cleared"] = True                                # a whole night without it: fixed
                self._gap_clear(f"target:{zone}")
                self._gap_clear(f"target_far:{zone}")
            tgt, meas, sp = n.get("target_c"), bed.get("measured_c"), bed.get("setpoint_c")
            if (not running or not online or hub_bad or n.get("flash") or n.get("room_note") or not bed.get("on") or tgt is None
                    or meas is None or sp is None or abs(sp - tgt) > 0.6 or unconf.get(zone)):
                trk.pop(pid, None)
                continue
            t = trk.get(pid)
            if t is None or t["night"] != night or abs(tgt - t["anchor"]) > 0.3:
                t = trk[pid] = {"night": night, "anchor": tgt, "off_since": None, "far_since": None, "samples": []}
            if not t["samples"] or now - t["samples"][-1][0] >= 60 or now < t["samples"][-1][0]:
                t["samples"] = [x for x in t["samples"] if 0 <= now - x[0] <= TARGET_TREND_S + 600] + [(now, meas)]
            gap = abs(meas - sp)
            t["off_since"] = (t["off_since"] or now) if gap > TARGET_OFF_C else None
            t["far_since"] = (t["far_since"] or now) if gap > TARGET_FAR_C else None
            if t["off_since"] is None or now - t["off_since"] < TARGET_STEADY_S:
                continue
            old = [x for x in t["samples"] if now - x[0] >= TARGET_TREND_S]
            if not old or abs(old[-1][1] - sp) - gap >= TARGET_MOVE_C:
                continue                                             # no history yet, or it is on its way
            rec["flag"] = True
            far = t["far_since"] is not None and now - t["far_since"] >= TARGET_FAR_S
            moderate = not self._slow_room(sp, meas, now)
            owners = self._owner_ids()
            side = f"{zone} side"

            def build(rid: str, kind: str, urgent: bool) -> Message | None:
                try:
                    person = self.c.person(rid)
                except KeyError:
                    return None
                if not person.settings.notify.target:
                    return None
                u = getattr(person.settings, "units", "F")
                title, body = self._say(kind, {"side": side, "now": self._deg(meas, u), "want": self._deg(sp, u)}, person, now)
                return Message("target", title, body, rid, priority=4 if urgent else 3, tags=["warning"], urgent=urgent,
                               link=self._fix_link(rid, "/studio/hub", "/settings#hub"))
            if moderate and self._gap_due(f"target:{zone}", now) and self._once(f"target:{zone}:{night}"):
                msgs = [build(r, "target_short", False) for r in [pid] + [o for o in owners if o != pid]]
                out.extend(m for m in msgs if m)
                self._gap_told(f"target:{zone}", now)
            if far and self._gap_due(f"target_far:{zone}", now) and self._once(f"target_far:{zone}:{night}"):
                msgs = [build(r, "target_far", True) for r in (owners or [pid])]
                out.extend(m for m in msgs if m)
                self._gap_told(f"target_far:{zone}", now)

    def _note_reach(self, pid: str | None, ok: bool, m: Message) -> None:
        """Count messages in a row that reached a person on no channel at all (the owner is told after a few: see _reach_gap)."""
        if not pid or m.kind in EXEMPT:
            return
        rec = dict(self.store.get("notify_reach", {}) or {})
        if ok:
            if rec.pop(pid, None) is None:
                return
        else:
            rec[pid] = rec.get(pid, 0) + 1
        self.store.put("notify_reach", rec)

    async def _deliver_noted(self, p: dict, m: Message) -> None:
        r = await self.deliver(p, m)
        self._note_reach(m.person, bool(r.get("ok")), m)

    def observe(self, now: float, snap: dict) -> list[Message]:
        """Called every tick with the live snapshot. Returns whatever became due (sent now, or held for the morning)."""
        from . import voice
        out: list[Message] = []
        tz = self.c.clock.tz
        self._snap = snap
        online = snap.get("system", {}).get("online", True)
        for pv in snap.get("people", []):
            try:
                person = self.c.person(pv["id"])
            except KeyError:
                continue
            try:
                self._observe_person(person, pv, now, out, online)
            except Exception:  # noqa: BLE001  (one person's trouble never stops the other's messages)
                log.exception("notifications failed for %s", person.id)
        self._tick_errors(snap, now, out)
        try:
            self._hub(snap, now, out)
        except Exception:  # noqa: BLE001
            log.exception("hub notifications failed")
        try:
            self._care(snap, now, out)
        except Exception:  # noqa: BLE001
            log.exception("care notifications failed")
        self._gaps(snap, now, out)
        if not online:
            self._offline_since = self._offline_since or now
            if now - self._offline_since >= OFFLINE_AFTER_S and self._once(f"offline:{int(self._offline_since)}"):
                t, b = voice.say("problem_offline", {"detail": snap["system"].get("detail", "") or "no answer"},
                                 day=self._day(now), store=self.store)
                out.append(Message("problem", t, b, None, priority=4, tags=["warning"]))
        else:
            self._offline_since = None
        for m in out:
            self.send(m)
        self._release(now)
        return out

    def _tick_errors(self, snap: dict, now: float, out: list) -> None:
        """A person's update has failed many ticks in a row (controller: snapshot system.tick_errors): their side may be
        stuck at its last setting. Said once per night."""
        from datetime import timedelta
        night = (datetime.fromtimestamp(now, self.c.clock.tz) - timedelta(hours=12)).date().isoformat()
        for pid, n in ((snap.get("system") or {}).get("tick_errors") or {}).items():
            if n < TICK_FAIL_AFTER:
                continue
            try:
                person = self.c.person(pid)
            except KeyError:
                continue
            if self._once(f"tick_errors:{pid}:{night}"):
                out.append(Message("problem", f"{person.name}'s side isn't updating",
                                   f"Something went wrong working out {person.name}'s night, so that side may stay at its "
                                   "last setting. Restarting Ember & Tide usually clears it.", None, priority=4, tags=["warning"]))

    def _release(self, now: float) -> None:
        """Held messages: each person gets them once, inside their morning summary, or as one "While you slept" note
        once they're up and no summary is coming."""
        if not self.store.get("notify_held"):
            return
        from . import voice
        for p in self.c.household.people:
            if self.quiet(p.id, now) or self._small_hours(p.id, now) or self._morning_pending(p, now):
                continue
            items = self._take_held(p.id)
            if items:
                t, b = voice.say("digest", {"lines": self._held_lines(items)}, day=self._day(now), store=self.store)
                self.send(Message("digest", t, b, p.id, priority=3, tags=["warning"]))

    def _pet_slots(self, q: dict) -> dict:
        from .engine import petq
        v = petq.view(q, self.c.household, self.c.clock.tz)
        return {"who": v["who"], "when": v["when"]}

    def _pet_question(self, person, now: float) -> Message | None:
        """"Was the pet on the bed?" as a push, once per question, only between 8 AM and 9 PM (a question from the night
        waits for the morning summary, which asks it; it is also on Tonight and in Studio)."""
        if not (person.settings.pet_questions and person.settings.notify.pet_question):
            return None
        from .engine.away import is_away
        if is_away(self.store, person.id, now):
            return None                                   # marked away: whatever lay on their side wasn't their night
        if not 8 <= datetime.fromtimestamp(now, self.c.clock.tz).hour < 21:
            return None
        if (self.__dict__.get("_pq_checked") or {}).get(person.id, 0) > now - 60:
            return None                                   # looked a minute ago
        self.__dict__.setdefault("_pq_checked", {})[person.id] = now
        if self._morning_pending(person, now):
            return None                                   # the morning summary will ask it: one ping, not two
        from .engine import petq
        q = petq.open_for(self.store, person.id, now)
        if not q or not self._once(f"petq:{q['id']}"):
            return None
        t, b = self._say("pet_question", self._pet_slots(q), person, now)
        # answer right from the notification (one pet: "Yes, it was Rex"; with several, which one is picked in the app)
        acts = []
        if len(q["pets"]) == 1:
            v = petq.view(q, self.c.household, self.c.clock.tz)
            yes = next(c for c in v["choices"] if c["answer"] == "yes")
            acts = [Action(yes["label"][:40], "pet_yes", ref=q["id"]), Action("No, it was me", "pet_me", ref=q["id"]),
                    Action("Not sure", "pet_unsure", ref=q["id"])]
        return Message("pet_question", t, b, person.id, actions=acts, priority=2, tags=["dog"])

    def _away_check(self, person, now: float) -> Message | None:
        """The day after a night they clearly weren't in their side of the bed: "Were you away?" (or, marked away,
        "Back home?" when their ring shows them asleep in bed). Once per night, 7 AM to 9 PM; checked every 10 min."""
        last = self.__dict__.setdefault("_ac_checked", {})
        if last.get(person.id, 0) > now - 600 and now >= last.get(person.id, 0):
            return None
        last[person.id] = now
        from .engine import away
        q = away.morning_check(self.c, person, now)
        if not q:
            return None
        from datetime import date
        d = date.fromisoformat(q["night"])
        day = d.strftime("%A")
        if q["kind"] == "away":
            t, b = self._say("away_check_away", {"day": day, "why": q["why"]}, person, now)
            acts = [Action("Away a few days", "away_away", ref=q["night"]), Action("Just that night", "away_night", ref=q["night"]),
                    Action("I was home", "away_home", ref=q["night"])]
        else:
            t, b = self._say("away_check_back", {"day": day, "why": q["why"]}, person, now)
            acts = [Action("Yes, I'm back", "away_back", ref=q["night"]), Action("Still away", "away_still", ref=q["night"])]
        return Message("away_check", t, b, person.id, actions=acts, priority=3, tags=["house"])

    def _morning(self, person, night: str, now: float) -> Message | None:
        """The morning summary: the night in a line, anything held overnight, an open pet question, and one action
        (tap to check in)."""
        from datetime import date
        from .engine.summary import summarize
        from .engine.away import is_away
        if is_away(self.store, person.id, now):
            return None                                   # away: whatever was on their side wasn't their night
        from .engine.away import night_verdict
        if night_verdict(self.c, person, date.fromisoformat(night))[0] == "away":
            return None                                   # clearly not in bed: the away check asks instead
        s = summarize(self.store, person, date.fromisoformat(night), self.c.clock.tz, now)
        if not s or not s.get("has_data") or not s.get("in_bed_min"):
            return None                                   # no night in bed: nothing to sum up
        t, body = self._say("morning", {"name": person.name, "summary": summary_line(s)}, person, now)
        extra = []
        # (a held "your side is empty" is moot once there's a night in bed to sum up: dropped, not told)
        held = [h for h in self._take_held(person.id) if h["kind"] not in AWAKE_KINDS]
        if held:
            from . import voice
            extra.append(voice.say("overnight", {"lines": self._held_lines(held)}, day=self._day(now), store=self.store))
        if person.settings.pet_questions and person.settings.notify.pet_question:
            from .engine import petq
            q = petq.open_for(self.store, person.id, now)
            if q and self._once(f"petq:{q['id']}"):
                extra.append(self._say("pet_question", self._pet_slots(q), person, now)[0])
        body = " ".join([body, *extra, self._say("checkin", {}, person, now)])
        # the calibration week: how the temperature felt, one tap from the notification (it tunes Autopilot)
        acts = []
        if not self.c.autopilot.learning(person, now)["done"]:
            acts = [Action("Too cold", "feel_cold"), Action("Just right", "feel_right"), Action("Too warm", "feel_warm")]
        return Message("morning", t, body, person.id, acts, priority=2, tags=["sunrise"])

    def review_message(self, person, night: str, out: dict | None, now: float | None = None) -> Message | None:
        """The AI review of a finished night as a notification: its first sentence or two. None when the person has this
        switched off, is away, or there is nothing to say; otherwise at most once per person per night."""
        from datetime import date
        from .engine.away import is_away, night_verdict
        now = self.c.clock.now_ts() if now is None else now
        answer = " ".join(str((out or {}).get("answer") or "").split())
        if not answer or not (out or {}).get("model") or not person.settings.notify.review or not person.settings.autopilot.ai_review:
            return None                                   # (no model = only the built-in summary, not worth a ping)
        if is_away(self.store, person.id, now) or night_verdict(self.c, person, date.fromisoformat(night))[0] == "away":
            return None                                   # not their night: nothing to review
        if not self._once(f"review:{person.id}:{night}"):
            return None
        return Message("review", "Your night, reviewed", review_excerpt(answer), person.id, priority=2, tags=["memo"], link="/")

    def send_review(self, person, night: str, out: dict | None) -> bool:
        """Called once the morning's background review has finished (and only then, never for a manual review)."""
        m = self.review_message(person, night, out)
        return self.send(m) if m else False

    # ---------- actions ----------
    def _night_end(self, person: str) -> float | None:
        """When the night this person is being asked about ends (household clock), if it hasn't yet."""
        sch = (self._pv(person) or {}).get("schedule")
        end = sch.get("end") if sch else None
        return end if end and end > self.c.clock.now_ts() else None

    def _expired(self, a: dict) -> bool:
        if a["expires"] < time.time():
            return True
        # "Start anyway" / "Off tonight" / "Wait an hour" are about one night: tapped the next morning they would heat the
        # bed in the day or quietly cancel a night the message was never about
        return bool(a.get("until")) and self.c.clock.now_ts() > a["until"]

    def new_action(self, person: str, a: Action) -> str:
        code = secrets.token_urlsafe(16)
        acts = {k: v for k, v in self.store.get("notify_actions", {}).items() if v["expires"] > time.time()}
        acts[_h(code)] = {"person": person, "act": a.act, "minutes": a.minutes, "label": a.label, "ref": a.ref, "expires": time.time() + ACTION_TTL_S,
                          "until": self._night_end(person) if a.act in NIGHT_ACTS else None}
        self.store.put("notify_actions", acts)
        return code

    def peek_action(self, code: str) -> dict | None:
        a = self.store.get("notify_actions", {}).get(_h(code))
        if not a or self._expired(a):
            return None
        try:
            name = self.c.person(a["person"]).name
        except KeyError:
            return None
        return {**a, "person_name": name}

    def discard_action(self, code: str) -> None:
        acts = self.store.get("notify_actions", {})
        acts.pop(_h(code), None)
        self.store.put("notify_actions", acts)

    def use_action(self, code: str) -> dict | None:
        acts = self.store.get("notify_actions", {})
        a = acts.pop(_h(code), None)
        self.store.put("notify_actions", acts)
        if not a or self._expired(a):
            return None
        e = self.c.engine_for(a["person"])
        now = self.c.clock.now_ts()
        if a["act"] == "manual_off":
            if e.s.override and e.s.override.manual:
                e.clear_override(now)
        elif a["act"].startswith("care_"):
            from . import care as carelib
            try:                          # the job may already be done (or gone) in the app: then nothing
                if a["act"] == "care_done":
                    carelib.of(self.c).done(a.get("ref") or "")
                else:
                    carelib.of(self.c).snooze(a.get("ref") or "")
            except (KeyError, carelib.CareError):
                pass
        elif a["act"].startswith("feel_"):
            save_feel(self.c, a["person"], a["act"][5:], now, source="notification")
        elif a["act"].startswith("away_"):
            from .engine import away
            try:                          # the question may already be answered in the app (then nothing)
                away.answer_check(self.c, a["person"], a.get("ref") or "", a["act"][5:], now)
            except (KeyError, ValueError):
                pass
        elif a["act"].startswith("pet_"):
            from .engine import petq
            try:                          # one pet answer; the question may already be answered in the app (then nothing)
                petq.answer(self.c, a.get("ref") or "", a["person"], a["act"][4:], None, now)
            except (KeyError, ValueError):
                pass
        else:
            e.tonight(now, a["act"], a.get("minutes"))
        self.store.event(now, "notification_action", a["person"], "notification", act=a["act"], label=a["label"])
        a["done"] = self.done_text(a)
        # a quiet "it worked", because a button in a notification otherwise gives no feedback at all
        # (ntfy / Home Assistant only: the app and the web confirm page show their own "done", so no second ping there)
        self.send(Message("action_done", a["done"], "Open Ember & Tide to change it.", a["person"], priority=2, tags=["white_check_mark"],
                          app=False))
        return a

    def done_text(self, a: dict) -> str:
        from datetime import datetime
        e = self.c.engine_for(a["person"])
        name = self.c.person(a["person"]).name
        if a["act"] == "pause" and e.s.pause_until:
            t = datetime.fromtimestamp(e.s.pause_until, self.c.clock.tz).strftime("%I:%M %p").lstrip("0")
            return f"Paused {name}'s side until {t}"
        if a["act"].startswith("feel_"):
            return {"feel_cold": "Noted: too cold", "feel_right": "Noted: just right", "feel_warm": "Noted: too warm"}[a["act"]]
        if a["act"].startswith("care_"):
            from . import care as carelib
            try:
                t = carelib.of(self.c).get(a.get("ref") or "")
                return t["after_done" if a["act"] == "care_done" else "after_snooze"]
            except KeyError:
                return carelib.after_text(a["act"] == "care_done", None)
        if a["act"].startswith("away_"):
            return {"away_away": "Marked away: your side is off until you're back", "away_night": "Noted: that night is left out",
                    "away_home": "Noted: you were home", "away_back": "Welcome back: your side runs again tonight",
                    "away_still": "Noted: still away"}[a["act"]]
        if a["act"].startswith("pet_"):
            return {"pet_yes": "Thanks: noted as the pet", "pet_me": "Thanks: noted as you", "pet_unsure": "No problem: nothing changed"}[a["act"]]
        return {"manual_off": f"{name}'s side is off", "skip": f"{name}'s side is off for tonight", "start_now": f"Started {name}'s night",
                "resume": f"{name}'s side is back on"}.get(a["act"], f"Done: {a['label']}")

    # ---------- delivery ----------
    def recipients(self, m: Message) -> list[dict]:
        out = []
        for p in self.providers:
            if not p.get("enabled", True):
                continue
            if m.person is None and p.get("person") in (None, "", "household"):
                out.append(p)
            elif m.person is not None and p.get("person") == m.person:
                out.append(p)
        return out

    def send(self, m: Message) -> bool:
        """Send now, or hold it for the morning (quiet hours), or drop it (a duplicate, or over today's cap).
        True when it went out."""
        now = self.c.clock.now_ts()
        why = self._allowed(m, now)
        if why == "held":
            self._hold(m, now)
            return False
        if why:
            self.store.event(now, "notification_skipped", m.person, "notify", title=m.title, about=m.kind, why=why)
            return False
        targets = self.recipients(m)
        devices = self.push_targets(m) if m.app else []
        if not m.app and not targets:
            return True          # a button pressed in the app or a web page shows its own "done": nothing to send, nothing to log
        self._sent(m, now)
        self.store.event(now, "notification", m.person, "notify", title=m.title, body=m.body, about=m.kind,
                         delivered=None if (targets or devices) else False)
        if not targets and not devices:
            # nowhere to send it (notifications never switched on for this person's phone): recorded as not delivered
            # so Studio → Notifications can say so, instead of looking sent
            self.store.event(now, "notification_undelivered", m.person, "notify", title=m.title, about=m.kind,
                             why="no phone or channel set up for notifications")
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return True
        if devices:
            # one channel per message: the app first; ntfy / HA targets (this person's, or the household's for a
            # household message) only if no phone took it
            t = loop.create_task(self._app_first(devices, targets, m))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
            return True
        for p in targets:
            t = loop.create_task(self._deliver_noted(p, m))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        return True

    async def _app_first(self, devices: list[tuple[str, dict]], fallback: list[dict], m: Message) -> None:
        results = await asyncio.gather(*(self.push(pid, d, m) for pid, d in devices))
        ok = any(results)
        if not ok:
            for p in fallback:
                ok = bool((await self.deliver(p, m)).get("ok")) or ok
        self._note_reach(m.person, ok, m)

    # ---------- app notifications (UnifiedPush, Web Push encrypted) ----------
    def push_settings(self) -> dict:
        s = self.store.get("notify_settings", {}) or {}
        return {"server": s.get("push_server"), "token": s.get("push_token")}

    def devices(self, pid: str) -> list[dict]:
        return self.store.get(f"push:{pid}", [])

    def push_targets(self, m: Message) -> list[tuple[str, dict]]:
        if m.person:
            pids = [m.person]
        elif m.kind in GAP_KINDS_OWNER or m.kind == "reach":      # the owner's: people who want these and have an owner account (anyone, if none is linked)
            owners = self.owner_persons() if self.owner_persons else set()
            sw = GAP_KINDS_OWNER.get(m.kind, "reach")
            pids = [p.id for p in self.c.household.people if getattr(p.settings.notify, sw) and (not owners or p.id in owners) and p.id != m.subject]
        else:              # household problems: only the phones of people who want them (NotifyPrefs.problems)
            pids = [p.id for p in self.c.household.people if p.settings.notify.problems]
        return [(pid, d) for pid in pids for d in self.devices(pid)]

    def check_endpoint(self, endpoint: str) -> str | None:
        """None if the app may register this push address, else why not. Only the push server the owner chose is
        accepted, so a signed-in account can't make this server send requests anywhere else."""
        server = self.push_settings()["server"]
        if not server:
            return "App notifications aren't set up on this server yet. The owner turns them on in Studio → Notifications."
        try:
            e, s = urlsplit(endpoint), urlsplit(server)
        except ValueError:
            return "That push address isn't valid."
        if len(endpoint) > 400 or (e.scheme, e.hostname, e.port) != (s.scheme, s.hostname, s.port):
            return f"Push addresses must be on {server}. Make sure the ntfy app uses that server."
        if e.scheme == "http":
            try:
                if not ipaddress.ip_address(e.hostname or "").is_private:
                    return "Use an https:// push server."
            except ValueError:
                return "Use an https:// push server."
        if not re.fullmatch(r"/[A-Za-z0-9_-]{6,80}", e.path or "") or (e.query and e.query != "up=1"):
            return "That doesn't look like a UnifiedPush address."
        return None

    def add_device(self, pid: str, endpoint: str, p256dh: str, auth: str, device: str | None, kind: str = "up",
                   platform: str = "android") -> dict:
        """kind "up": endpoint is a UnifiedPush URL on the owner's ntfy; kind "fcm": endpoint is the phone's FCM token."""
        items = [d for d in self.devices(pid) if d["endpoint"] != endpoint]
        d = {"id": secrets.token_hex(6), "kind": kind, "endpoint": endpoint, "p256dh": p256dh, "auth": auth,
             "device": (device or "Phone")[:60], "ts": time.time(), "platform": platform}
        items.append(d)
        self.store.put(f"push:{pid}", items[-MAX_DEVICES:])
        fails = dict(self.store.get("notify_reach", {}) or {})
        if fails.pop(pid, None) is not None:                    # a phone just (re)registered: start counting again
            self.store.put("notify_reach", fails)
        return d

    def remove_device(self, pid: str, endpoint: str | None = None, did: str | None = None) -> bool:
        items = self.devices(pid)
        keep = [d for d in items if d["endpoint"] != endpoint and d["id"] != did]
        self.store.put(f"push:{pid}", keep)
        return len(keep) != len(items)

    def _expires(self, m: Message) -> int | None:
        """Epoch seconds when the night a message is about ends, for messages with night-bound buttons (NIGHT_ACTS): the
        phone removes the notification then, so "Start anyway" can't be tapped the next morning. None for everything else."""
        if not m.person or not any(a.act in NIGHT_ACTS for a in m.actions):
            return None
        end = self._night_end(m.person)
        return int(time.time() + (end - self.c.clock.now_ts())) if end else None

    async def push(self, pid: str, d: dict, m: Message) -> bool:
        if d.get("kind") == "web":
            return await self._push_web(pid, d, m)
        from . import webpush
        payload = {"t": "et", "v": 1, "kind": m.kind, "title": m.title, "body": m.body, "person": m.person,
                   "priority": m.priority, "ts": int(time.time()),
                   "link": self._link(pid, m), **({"expires": exp} if (exp := self._expires(m)) else {}),
                   "actions": [{"label": a.label, "act": a.act, "minutes": a.minutes, **({"ref": a.ref} if a.ref else {})}
                               for a in m.actions[:3] if a.act in APP_ACTS]}
        try:
            body = webpush.encrypt(json.dumps(payload, separators=(",", ":")).encode(), d["p256dh"], d["auth"])
        except Exception as e:  # noqa: BLE001
            log.warning("push: can't encrypt for %s: %s", d.get("device"), e)
            return False
        if d.get("kind") == "fcm":
            return await self._push_fcm(pid, d, m, body)
        headers = {"Content-Encoding": "aes128gcm", "TTL": "21600", "Urgency": "high" if m.priority >= 4 else "normal",
                   "Content-Type": "application/octet-stream"}
        tok = self.push_settings()["token"]
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        c = self._client()
        try:
            r = await c.post(d["endpoint"], content=body, headers=headers)
            if r.status_code in (404, 410):
                self.remove_device(pid, endpoint=d["endpoint"])        # the phone unregistered or the app was removed
                log.info("push: %s is gone, removed", d.get("device"))
                return False
            r.raise_for_status()
            self._log_delivery({"provider": f"app · {d.get('device')}", "title": m.title, "ok": True})
            return True
        except Exception as e:  # noqa: BLE001
            err = f"{e.__class__.__name__}: {getattr(getattr(e, 'response', None), 'status_code', '') or e}"
            log.warning("push to %s failed: %s", d.get("device"), err)
            self._log_delivery({"provider": f"app · {d.get('device')}", "title": m.title, "ok": False, "error": err})
            return False
        finally:
            if c is not self.client:
                await c.aclose()

    async def deliver(self, p: dict, m: Message) -> dict:
        """Send one message through one provider. Returns {"ok": bool, "error"?: str} (used by the Test button)."""
        try:
            if p["kind"] == "ntfy":
                await self._ntfy(p, m)
            elif p["kind"] == "ha":
                await self._ha(p, m)
            else:
                return {"ok": False, "error": f"Unknown kind {p['kind']}"}
            self._log_delivery({"provider": p.get("name"), "title": m.title, "ok": True})
            return {"ok": True}
        except httpx.ConnectError:
            err = "Couldn't reach the server. Check the address."
        except httpx.TimeoutException:
            err = "The server didn't answer in time."
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            err = {401: "The server rejected the token.", 403: "Not allowed. Check the token or topic permissions.",
                   404: "Not found. Check the topic or the notify service name."}.get(code, f"The server answered with an error ({code}).")
        except Exception as e:  # noqa: BLE001
            err = f"That didn't work: {e}"
        log.warning("notification via %s failed: %s", p.get("name"), err)
        self._log_delivery({"provider": p.get("name"), "title": m.title, "ok": False, "error": err})
        return {"ok": False, "error": err}

    def _client(self) -> httpx.AsyncClient:
        return self.client or httpx.AsyncClient(timeout=10)

    async def _ntfy(self, p: dict, m: Message) -> None:
        s = p.get("settings", {})
        base = (s.get("url") or "https://ntfy.sh").rstrip("/")
        body: dict[str, Any] = {"topic": s["topic"], "title": m.title, "message": m.body, "priority": m.priority, "tags": m.tags}
        pub = self.public_url
        if pub:
            body["click"] = pub + "/"
        if m.actions and m.person and pub:
            body["actions"] = [{"action": "http", "label": a.label, "url": f"{pub}/api/notify/act/{self.new_action(m.person, a)}",
                                "method": "POST", "clear": True} for a in m.actions[:3]]
        headers = {"Authorization": f"Bearer {s['token']}"} if s.get("token") else {}
        c = self._client()
        try:
            r = await c.post(base, json=body, headers=headers)
            r.raise_for_status()
        finally:
            if c is not self.client:
                await c.aclose()

    async def _ha(self, p: dict, m: Message) -> None:
        s = p.get("settings", {})
        service = s["service"].removeprefix("notify.")
        data: dict[str, Any] = {}
        pub = self.public_url
        if pub:
            data["url"] = pub + "/"                 # iPhone
            data["clickAction"] = pub + "/"         # Android
        lis = self.ha_listeners.get(s["url"].rstrip("/"))
        if m.actions and m.person and lis and lis.connected:
            # one tap: HA tells us which button was pressed over its event bus, so the phone never has to reach this server
            data["actions"] = [{"action": f"ET_{self.new_action(m.person, a)}", "title": a.label} for a in m.actions[:3]]
        elif m.actions and m.person and pub:
            data["actions"] = [{"action": "URI", "title": a.label, "uri": f"{pub}/act#{self.new_action(m.person, a)}"} for a in m.actions[:3]]
        c = self._client()
        try:
            r = await c.post(f"{s['url'].rstrip('/')}/api/services/notify/{service}", json={"title": m.title, "message": m.body, "data": data},
                             headers={"Authorization": f"Bearer {s['token']}"})
            r.raise_for_status()
        finally:
            if c is not self.client:
                await c.aclose()


    async def _push_web(self, pid: str, d: dict, m: Message) -> bool:
        """A browser / iPhone Home Screen web app subscription (Web Push with VAPID)."""
        from . import web_push
        pub = self.public_url
        acts = []
        if m.person and pub:     # buttons open the one-tap confirm page (iPhone shows none; tapping opens the app)
            acts = [{"action": f"a{i}", "title": a.label, "url": f"{pub}/act#{self.new_action(m.person, a)}"}
                    for i, a in enumerate(m.actions[:2]) if a.act in APP_ACTS]
        name = f"web · {d.get('device')}"
        try:
            url, headers, body = web_push.build(self.webpush, d, web_push.payload(m, self._link(pid, m), acts), web_push.contact_for(pub), urgent=m.priority >= 4)
        except Exception as e:  # noqa: BLE001
            log.warning("web push: can't prepare for %s: %s", d.get("device"), e)
            self._log_delivery({"provider": name, "title": m.title, "ok": False, "error": f"can't encrypt: {e.__class__.__name__}"})
            return False
        c = self._client()
        try:
            r = await c.post(url, content=body, headers=headers)
            if r.status_code in (404, 410):
                self.remove_device(pid, endpoint=d["endpoint"])       # unsubscribed, or the web app was removed
                log.info("web push: %s is gone, removed", d.get("device"))
                self._log_delivery({"provider": name, "title": m.title, "ok": False, "error": "subscription gone, removed"})
                return False
            if r.status_code == 429:
                err = f"push service busy, retry after {r.headers.get('retry-after', '?')} s"
            elif r.status_code >= 400:
                err = f"push service answered {r.status_code}: {r.text[:120]}"
            else:
                self._log_delivery({"provider": name, "title": m.title, "ok": True})
                return True
        except Exception as e:  # noqa: BLE001
            err = f"{e.__class__.__name__}"
        finally:
            if c is not self.client:
                await c.aclose()
        log.warning("web push to %s failed: %s", d.get("device"), err)
        self._log_delivery({"provider": name, "title": m.title, "ok": False, "error": err})
        return False

    async def _push_fcm(self, pid: str, d: dict, m: Message, body: bytes) -> bool:
        try:
            res = await self.fcm.send(d["endpoint"], body, urgent=True, ios=d.get("platform") == "ios")   # deliver promptly
            if res == "gone":
                self.remove_device(pid, endpoint=d["endpoint"])
                log.info("fcm: %s is gone, removed", d.get("device"))
                return False
            self._log_delivery({"provider": f"app · {d.get('device')}", "title": m.title, "ok": True})
            return True
        except Exception as e:  # noqa: BLE001
            err = f"FCM: {getattr(getattr(e, 'response', None), 'text', '') or e}"[:200]
            log.warning("fcm push to %s failed: %s", d.get("device"), err)
            self._log_delivery({"provider": f"app · {d.get('device')}", "title": m.title, "ok": False, "error": err})
            return False

    # ---------- Home Assistant button presses ----------
    def sync_ha_listeners(self) -> None:
        """One listener per Home Assistant used for notifications. Called at start and whenever targets change."""
        want = {}
        for p in self.providers:
            if p.get("kind") == "ha" and p.get("enabled", True):
                want[p["settings"]["url"].rstrip("/")] = p["settings"]["token"]
        for url in list(self.ha_listeners):
            if url not in want or self.ha_listeners[url].token != want[url]:
                self.ha_listeners.pop(url).stop()
        for url, tok in want.items():
            if url not in self.ha_listeners:
                lis = HaActionListener(url, tok, self._on_ha_action)
                self.ha_listeners[url] = lis
                lis.start()

    def stop(self) -> None:
        for lis in self.ha_listeners.values():
            lis.stop()
        self.ha_listeners.clear()

    async def _on_ha_action(self, action: str) -> None:
        if not action.startswith("ET_"):
            return
        a = self.use_action(action[3:])
        if a:
            await self.c.tick(force={a["person"]})


class HaActionListener:
    """Subscribes to `mobile_app_notification_action` on Home Assistant's websocket API."""

    def __init__(self, url: str, token: str, on_action) -> None:
        self.url, self.token, self.on_action = url, token, on_action
        self.connected = False
        self.error: str | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        try:
            self._task = asyncio.get_running_loop().create_task(self._run())
        except RuntimeError:
            pass

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        self.connected = False

    async def _run(self) -> None:
        import json as _json

        import websockets
        # nosemgrep: javascript.lang.security.detect-insecure-websocket.detect-insecure-websocket -- the Home Assistant URL is owner-configured; ws:// mirrors an http:// LAN URL, wss:// is used for https://
        ws_url = ("wss://" if self.url.startswith("https") else "ws://") + self.url.split("://", 1)[1] + "/api/websocket"
        backoff = 2
        while True:
            try:
                async with websockets.connect(ws_url, open_timeout=10, max_size=2 ** 22) as ws:
                    await ws.recv()                                             # auth_required
                    await ws.send(_json.dumps({"type": "auth", "access_token": self.token}))
                    r = _json.loads(await ws.recv())
                    if r.get("type") != "auth_ok":
                        self.error = "Home Assistant rejected the token."
                        self.connected = False
                        await asyncio.sleep(300)
                        continue
                    await ws.send(_json.dumps({"id": 1, "type": "subscribe_events", "event_type": "mobile_app_notification_action"}))
                    self.connected, self.error, backoff = True, None, 2
                    async for raw in ws:
                        msg = _json.loads(raw)
                        if msg.get("type") == "event":
                            act = (msg.get("event", {}).get("data", {}) or {}).get("action", "")
                            if isinstance(act, str) and act.startswith("ET_"):
                                await self.on_action(act)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.connected = False
                self.error = f"Can't reach Home Assistant ({type(e).__name__})"
                await asyncio.sleep(backoff)
                backoff = min(120, backoff * 2)
