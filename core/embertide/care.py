"""Care and maintenance reminders: a short list of jobs for the bed (purification tablets, adding water, a quick check
of the hub), each with an interval the household can change.

- The bed's hardware profile (`drivers/profiles.py`) supplies the default tasks; this module is generic.
- State lives in the store (kv "care"), per household, so it survives restarts. Only what the household changed is
  stored for a built-in task (interval, on/off, last done, snooze); the wording always comes from the profile.
- Due dates count from "last done". A task never done counts from the day the list was first opened ("since"), so a new
  install asks nothing for a whole interval unless the owner enters "I last did this on...".
- A task with trigger "hub_water" has no schedule: it is due whenever the hub says its water needs a look (the same
  status that already raises the hub notification), so "Add water" shows as due without a second, separate message.
- Dates are whole days on the household clock.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta

from .drivers.profiles import GENERIC

KEY = "care"
MAX_CUSTOM = 20
MIN_INTERVAL, MAX_INTERVAL = 1, 3650
SNOOZE_DAYS = 7
MAX_SNOOZE = 60
SOON_DAYS = 7                 # "due soon" is shown from this many days before
FOLLOW_UP_DAYS = 7            # one gentle follow-up this long after the first message, if still not done
OLDEST_DONE_DAYS = 3650


class CareError(ValueError):
    """Something the caller can fix; the text is shown to the person."""


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


def parse_date(s: str, *, today: date) -> date:
    d = _d(s)
    if d is None:
        raise CareError("Use a date like 2026-09-30.")
    if d > today:
        raise CareError("That date is in the future.")
    if (today - d).days > OLDEST_DONE_DAYS:
        raise CareError("That date is too long ago.")
    return d


def clean_text(s: object, what: str, limit: int, *, required: bool = True) -> str:
    if not isinstance(s, str):
        raise CareError(f"{what} should be text.")
    s = " ".join(s.split())
    if required and not s:
        raise CareError(f"Please give {what.lower()}.")
    if len(s) > limit:
        raise CareError(f"{what} can be at most {limit} characters.")
    return s


def clean_steps(steps: object) -> list[str]:
    if not isinstance(steps, list) or len(steps) > 10:
        raise CareError("Steps should be a list of at most 10 short sentences.")
    return [clean_text(x, "A step", 200) for x in steps]


def clean_interval(n: object) -> int:
    if isinstance(n, bool) or not isinstance(n, int) or not MIN_INTERVAL <= n <= MAX_INTERVAL:
        raise CareError(f"Choose between {MIN_INTERVAL} and {MAX_INTERVAL} days.")
    return n


def words(days_until: int) -> str:
    if days_until > 1:
        return f"Due in {days_until} days"
    if days_until == 1:
        return "Due tomorrow"
    if days_until == 0:
        return "Due today"
    n = -days_until
    return f"{n} day{'s' if n != 1 else ''} overdue"


def interval_text(days: int) -> str:
    """A repeat interval in words: "about a week", "about 6 months"."""
    if days < 10:
        return "about a day" if days == 1 else "about a week" if days == 7 else f"about {days} days"
    if days < 45:
        w = round(days / 7)
        return "about a week" if w == 1 else f"about {w} weeks"
    if days < 330:
        m = round(days / 30.4)
        return "about a month" if m == 1 else f"about {m} months"
    y = round(days / 365)
    return "about a year" if y <= 1 else f"about {y} years"


def after_text(done: bool, interval: int | None) -> str:
    """What a notification becomes after a tap (the same wording on every channel)."""
    if not done:
        return "I'll remind you next week."
    return f"Marked done. Next reminder in {interval_text(interval)}." if interval else "Marked done."


class Care:
    def __init__(self, controller) -> None:
        self.c = controller
        self.store = controller.store

    # ---------- plumbing ----------
    def today(self, now: float | None = None) -> date:
        ts = self.c.clock.now_ts() if now is None else now
        return datetime.fromtimestamp(ts, self.c.clock.tz).date()

    @property
    def profile(self) -> dict:
        return getattr(self.c.driver, "profile", None) or GENERIC

    def _state(self) -> dict:
        st = self.store.get(KEY)
        if not isinstance(st, dict) or "since" not in st:
            st = {"since": self.today().isoformat(), "tasks": {}, "custom": []}
            self.store.put(KEY, st)
        st.setdefault("tasks", {})
        st.setdefault("custom", [])
        return st

    def _save(self, st: dict) -> None:
        self.store.put(KEY, st)

    def _defs(self, st: dict) -> dict[str, dict]:
        out = {t["id"]: {**t, "custom": False} for t in self.profile.get("tasks", [])}
        for t in st["custom"]:
            out[t["id"]] = {**t, "custom": True, "source": "custom", "trigger": "interval"}
        return out

    def _hub_water_low(self) -> bool:
        try:
            h = self.c.hub_summary()
        except Exception:  # noqa: BLE001
            return False
        return bool(h) and h.get("water_ok") is False

    # ---------- the list ----------
    def _view(self, d: dict, st: dict, today: date, water_low: bool) -> dict:
        rec = st["tasks"].get(d["id"], {})
        enabled = rec.get("enabled", True)
        interval = rec.get("interval_days", d.get("interval_days"))
        last = _d(rec.get("last_done"))
        since = _d(d.get("since")) or _d(st["since"]) or today
        snooze = _d(rec.get("snooze_until"))
        snoozed = bool(snooze and snooze > today)
        water = d.get("trigger") == "hub_water"
        due_date = days_until = None
        if water:
            is_due = enabled and water_low
            text = "Needs water now" if is_due else "Fine for now"
        else:
            due_date = (last or since) + timedelta(days=interval)
            days_until = (due_date - today).days
            is_due = enabled and days_until <= 0
            text = words(days_until)
        if not enabled:
            text = "Turned off"
        status = ("off" if not enabled else "water" if water and is_due else "ok" if water else
                  "overdue" if days_until < 0 else "due" if days_until == 0 else "soon" if days_until <= SOON_DAYS else "ok")
        return {
            "id": d["id"], "title": d["title"], "why": d.get("why", ""), "steps": list(d.get("steps", [])),
            "supplies": d.get("supplies"), "note": d.get("note"), "reminder": d.get("reminder"),
            "interval_days": interval, "default_interval_days": d.get("interval_days"),
            "source": d["source"], "source_label": {"maker": "From the maker's instructions", "default": "Suggested default",
                                                    "custom": "Added by you"}[d["source"]],
            "url": d.get("url"), "custom": d["custom"], "trigger": d.get("trigger", "interval"),
            "enabled": enabled, "last_done": last.isoformat() if last else None, "counting_from": (last or since).isoformat(),
            "due_date": due_date.isoformat() if due_date else None, "days_until": days_until,
            "status": status, "status_text": text, "due": is_due, "snoozed_until": snooze.isoformat() if snoozed else None,
            "due_now": is_due and not snoozed,
            "after_done": after_text(True, None if water else interval), "after_snooze": after_text(False, None),
        }

    def tasks(self, today: date | None = None) -> list[dict]:
        st = self._state()
        today = today or self.today()
        low = self._hub_water_low()
        return [self._view(d, st, today, low) for d in self._defs(st).values()]

    def get(self, tid: str) -> dict:
        for t in self.tasks():
            if t["id"] == tid:
                return t
        raise KeyError(tid)

    def summary(self) -> dict:
        ts = self.tasks()
        return {"due_now": [t["id"] for t in ts if t["due_now"]], "count": len(ts)}

    def listing(self) -> dict:
        st = self._state()
        return {"tasks": self.tasks(), "today": self.today().isoformat(), "since": st["since"],
                "hub_guide": self.profile.get("light_guide"), "can_add": len(st["custom"]) < MAX_CUSTOM}

    # ---------- changes ----------
    def _log(self, what: str, tid: str, who: str | None = None, **kw) -> None:
        self.store.event(self.c.clock.now_ts(), "care", who, "care", action=what, task=tid, **kw)

    def _check(self, tid: str, st: dict) -> dict:
        d = self._defs(st).get(tid)
        if d is None:
            raise KeyError(tid)
        return d

    def done(self, tid: str, on: str | None = None, who: str | None = None) -> dict:
        st = self._state()
        d = self._check(tid, st)
        today = self.today()
        day = parse_date(on, today=today) if on else today
        rec = st["tasks"].setdefault(d["id"], {})
        rec["last_done"] = day.isoformat()
        rec.pop("snooze_until", None)
        rec.pop("notify", None)
        self._save(st)
        self._log("done", tid, who, on=day.isoformat())
        return self.get(tid)

    def snooze(self, tid: str, days: int = SNOOZE_DAYS, who: str | None = None) -> dict:
        st = self._state()
        d = self._check(tid, st)
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_SNOOZE:
            raise CareError(f"Snooze for 1 to {MAX_SNOOZE} days.")
        until = self.today() + timedelta(days=days)
        st["tasks"].setdefault(d["id"], {})["snooze_until"] = until.isoformat()
        self._save(st)
        self._log("snooze", tid, who, until=until.isoformat())
        return self.get(tid)

    def edit(self, tid: str, fields: dict) -> dict:
        """Owner's changes. Built-in tasks: interval, on/off, last done. Your own tasks: also the words."""
        st = self._state()
        d = self._check(tid, st)
        today = self.today()
        rec = st["tasks"].setdefault(d["id"], {})
        custom = next((t for t in st["custom"] if t["id"] == tid), None)
        unknown = set(fields) - {"interval_days", "enabled", "last_done", "title", "why", "steps", "supplies"}
        if unknown:
            raise CareError("Unknown setting: " + ", ".join(sorted(unknown)))
        if "interval_days" in fields:
            if d.get("trigger") == "hub_water":
                raise CareError("This task isn't on a schedule: it comes up when the hub says the water is low.")
            rec["interval_days"] = clean_interval(fields["interval_days"])
        if "enabled" in fields:
            if not isinstance(fields["enabled"], bool):
                raise CareError("Turn it on or off.")
            if fields["enabled"] and not rec.get("enabled", True):
                rec.pop("notify", None)              # turned back on: it may speak up again
            rec["enabled"] = fields["enabled"]
        if "last_done" in fields:
            if fields["last_done"] is None:
                rec.pop("last_done", None)
            else:
                rec["last_done"] = parse_date(fields["last_done"], today=today).isoformat()
            rec.pop("snooze_until", None)
            rec.pop("notify", None)
        if {"title", "why", "steps", "supplies"} & set(fields):
            if custom is None:
                raise CareError("Only tasks you added yourself can be reworded.")
            if "title" in fields:
                custom["title"] = clean_text(fields["title"], "A title", 60)
            if "why" in fields:
                custom["why"] = clean_text(fields["why"], "The reason", 200, required=False)
            if "steps" in fields:
                custom["steps"] = clean_steps(fields["steps"])
            if "supplies" in fields:
                custom["supplies"] = clean_text(fields["supplies"], "Supplies", 120, required=False) or None
        self._save(st)
        self._log("edit", tid, None, fields=sorted(fields))
        return self.get(tid)

    def add(self, title: str, interval_days: int, why: str = "", steps: list | None = None, supplies: str | None = None,
            last_done: str | None = None) -> dict:
        st = self._state()
        if len(st["custom"]) >= MAX_CUSTOM:
            raise CareError(f"That's the most custom tasks ({MAX_CUSTOM}). Delete one first.")
        today = self.today()
        t = {"id": "c_" + secrets.token_hex(4), "title": clean_text(title, "A title", 60), "interval_days": clean_interval(interval_days),
             "why": clean_text(why or "", "The reason", 200, required=False), "steps": clean_steps(steps or []),
             "supplies": clean_text(supplies, "Supplies", 120, required=False) if supplies else None, "since": today.isoformat()}
        if any(x["title"].lower() == t["title"].lower() for x in self.tasks()):
            raise CareError("There's already a task with that name.")
        st["custom"].append(t)
        if last_done:
            st["tasks"].setdefault(t["id"], {})["last_done"] = parse_date(last_done, today=today).isoformat()
        self._save(st)
        self._log("add", t["id"])
        return self.get(t["id"])

    def delete(self, tid: str) -> None:
        st = self._state()
        self._check(tid, st)
        if not any(t["id"] == tid for t in st["custom"]):
            raise CareError("Built-in tasks can be turned off but not deleted.")
        st["custom"] = [t for t in st["custom"] if t["id"] != tid]
        st["tasks"].pop(tid, None)
        self._save(st)
        self._log("delete", tid)

    # ---------- reminders (notify.py decides when; this says what is owed) ----------
    def notices(self, today: date | None = None) -> list[tuple[dict, str]]:
        """Tasks that owe a message: ("first") not yet told about; ("follow") told 7+ days ago, still not done, and not
        told again. Most overdue first. Water tasks are never here: the hub notification already covers low water."""
        st = self._state()
        today = today or self.today()
        out = []
        for t in self.tasks(today):
            if t["trigger"] != "interval" or not t["due_now"]:
                continue
            n = st["tasks"].get(t["id"], {}).get("notify") or {}
            first = _d(n.get("first"))
            if first is None:
                out.append((t, "first"))
            elif not n.get("follow") and (today - first).days >= FOLLOW_UP_DAYS:
                out.append((t, "follow"))
        out.sort(key=lambda x: x[0]["days_until"])
        return out

    def mark_told(self, tid: str, kind: str, today: date | None = None) -> None:
        st = self._state()
        rec = st["tasks"].setdefault(tid, {})
        n = rec.setdefault("notify", {})
        if kind == "first":
            n["first"] = (today or self.today()).isoformat()
        else:
            n["follow"] = True
        self._save(st)


def of(controller) -> Care:
    """The household's care list (one per controller)."""
    c = controller.__dict__.get("_care")
    if c is None:
        c = controller.__dict__["_care"] = Care(controller)
    return c
