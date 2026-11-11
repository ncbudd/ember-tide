"""One person's night: when the side pre-conditions, when the rhythm starts (in bed, on their side, inside the
window), overrides, flashes, absences, and what temperature the side should be right now."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from zoneinfo import ZoneInfo

from ..models import FlashSettings, Person
from .rhythm import ResolvedNight, fitted_late, next_phase, night_date, pick_rhythm, resolve, target_at


@dataclass
class Flash:
    kind: str            # "hot" | "cold"
    start: float
    end: float
    target_c: float
    ease_s: float
    max_end: float
    source: str = "app"


@dataclass
class Override:
    set_ts: float
    until: str           # "next_phase" | "night"
    phase_id: str | None
    temp_c: float | None = None
    delta_c: float | None = None
    source: str = "app"
    end_ts: float | None = None   # a timer (e.g. a nap): the change ends here
    manual: bool = False          # the side was turned on by hand while the rhythm had it off


@dataclass
class NightState:
    key: str | None = None
    started: bool = False
    in_bed: float | None = None
    presence_since: float | None = None
    absent_since: float | None = None
    skip: bool = False
    skip_next: bool = False
    unknown_since: float | None = None   # when this side's presence became unknown     # "off tonight" pressed after this night already ended: applies to the coming night
    pause_until: float | None = None
    wake_override: float | None = None
    wake_by_hand: bool = False           # set from the app ("wake me at"): the phone's alarm doesn't move it
    wake_from_alarm: float | None = None # the alarm the wake currently follows
    home: bool | None = None
    flash: Flash | None = None
    override: Override | None = None
    state: str = "day"
    phase_id: str | None = None
    base_c: float | None = None
    target_c: float | None = None
    started_by: str | None = None        # "presence" | "schedule" | "hand": how tonight's night started
    out_since: float | None = None       # when the current time out of bed began (absent_since, never moved by a restart)
    # a night given up early that waits to start again from the beginning: {"in_bed": the old anchor, "up_since": ...}
    reanchor: dict | None = None
    # in bed before the usual bedtime: when the night moved on from "Fall asleep" (a sign of sleep, or bedtime), and why
    sleep_at: float | None = None
    sleep_why: str | None = None
    events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("events", None)
        return d


class NightEngine:
    def __init__(self, person: Person, tz: ZoneInfo) -> None:
        self.person = person
        self.tz = tz
        self.s = NightState()
        self.night: ResolvedNight | None = None
        self.busy_until: float | None = None       # set by the controller from calendar context
        self.away = False                          # away for a while (engine/away.py), set by the controller each tick
        # settling in (controller._start_gate): False = the bed radar / phone don't back this side's presence yet, so
        # lying there doesn't start the night; None = no opinion (no radar), the usual start
        self.start_gate: bool | None = None
        # cold / warm room (engine/padlag.py), set by the controller each tick: the bedroom now, this side's learned
        # warm/cool speed, and "don't warm early" (a too hot tap lately)
        self.room_c: float | None = None
        self.pad_model: dict = {}
        self.hold_warm = False
        self.lead_s = 0.0                          # how early the coming change started (for the snapshot)
        # a sign that they are going to sleep, set by the controller each tick: (when, why) or None (engine/sleepsign.py)
        self.sleep_sign: tuple[float, str] | None = None

    # ---------- actions ----------
    def flash(self, kind: str, now: float, minutes: int | None = None, source: str = "app") -> Flash:
        cfg: FlashSettings = self.person.settings.hot_flash if kind == "hot" else self.person.settings.cold_flash
        base = self.s.base_c if self.s.base_c is not None else cfg.target_c
        target = cfg.target_c if not cfg.relative else base + (-cfg.delta_c if kind == "hot" else cfg.delta_c)
        dur = (minutes or cfg.duration_min) * 60
        self.s.flash = Flash(kind, now, now + dur, round(target, 2), cfg.ease_min * 60, now + cfg.max_total_min * 60, source)
        self._ev(now, f"{kind}_flash", source=source, minutes=dur / 60, target_c=self.s.flash.target_c, phase=self.s.phase_id)
        return self.s.flash

    def extend_flash(self, now: float, minutes: int | None = None, source: str = "app") -> None:
        f = self.s.flash
        if not f or now >= f.end + f.ease_s:
            return
        cfg = self.person.settings.hot_flash if f.kind == "hot" else self.person.settings.cold_flash
        f.end = min(f.max_end, max(f.end, now) + (minutes or cfg.extend_min) * 60)
        self._ev(now, "flash_extended", until=f.end, source=source)

    def stop_flash(self, now: float, source: str = "app") -> None:
        if self.s.flash:
            # jump to the ease-back immediately
            self.s.flash.end = min(self.s.flash.end, now)
            self._ev(now, "flash_stopped", source=source)

    def adjust(self, now: float, *, delta_c: float | None = None, temp_c: float | None = None, until: str = "next_phase",
               source: str = "app", minutes: int | None = None) -> None:
        step, set_to = delta_c, temp_c
        if self.s.skip or (self.s.pause_until and now < self.s.pause_until):
            # pressing + or − while paused / off for the night means "I want my side back": resume, then apply the step
            self.s.skip, self.s.pause_until = False, None
            self._ev(now, "tonight_resume", source=source, reason="adjusted while off")
        cur = self.s.override
        if delta_c is not None and cur and cur.delta_c is not None and cur.until == until:
            delta_c = cur.delta_c + delta_c
        if delta_c is not None and cur and cur.temp_c is not None and cur.until == until:
            temp_c, delta_c = cur.temp_c + delta_c, None
        merged = cur is not None and step is not None and cur.until == until and cur.phase_id == self.s.phase_id
        # turned on by hand while the rhythm has the side off (a nap, an early night): it runs until the timer, or
        # until the rhythm itself starts, and never carries on into the night
        idle = self.s.base_c is None and self.s.state not in ("skipped", "paused")   # off because of the rhythm, not the person
        manual = (cur.manual if merged else False) or (idle and temp_c is not None)
        end_ts = now + minutes * 60 if minutes else (cur.end_ts if merged else None)
        self.s.override = Override(cur.set_ts if merged else now, until, self.s.phase_id, temp_c=temp_c, delta_c=delta_c,
                                   source=source, end_ts=end_ts, manual=manual)
        self._ev(now, "adjust", source=source, delta_c=step, temp_c=set_to, until=until, phase=self.s.phase_id,
                 total_delta_c=self.s.override.delta_c, total_temp_c=self.s.override.temp_c, minutes=minutes, manual=manual)

    def clear_override(self, now: float, source: str = "app") -> None:
        self.s.override = None
        self._ev(now, "adjust_cleared", source=source)

    def tonight(self, now: float, action: str, minutes: int | None = None, source: str = "app") -> None:
        if action == "skip":
            if self.night is not None and now >= self.night.end:
                self.s.skip_next = True              # this morning's night is over; "tonight" means the next one
            else:
                self.s.skip = True
                self._end_flash(now, source)         # "off" means off, even mid-flash
        elif action == "resume":
            self.s.skip, self.s.skip_next, self.s.pause_until = False, False, None
        elif action == "start_now":
            self.s.skip, self.s.started, self.s.in_bed = False, True, now
            self._began(now, "hand")
        elif action in ("pause", "delay"):
            self.s.pause_until = now + (minutes or 60) * 60
            self._end_flash(now, source)
        self._ev(now, f"tonight_{action}", minutes=minutes, source=source)

    def _end_flash(self, now: float, source: str = "app") -> None:
        f = self.s.flash
        if f and now < f.end + f.ease_s:
            self.s.flash = None
            if now < f.end:                              # still running (not already stopped and easing back): note it once
                self._ev(now, "flash_stopped", reason="turned off", source=source)

    # ---------- the phone's alarm ----------
    ALARM_EARLIER_S = 4 * 3600         # an alarm up to 4 h before the planned wake moves it earlier
    ALARM_LATER_S = 3 * 3600           # and up to 3 h after (a second, later alarm to sleep in) moves it later

    SNOOZE_S = 30 * 60                                   # a later alarm this soon after the first one rang = snooze

    def follow_alarm(self, now: float, alarm: float | None, reported: bool = False) -> bool:
        """Line tonight's wake up with the phone's next alarm (Settings: "Wake with my alarm"). Returns True when the
        wake moved. A wake set by hand in the app wins; an alarm far from tonight's planned wake is someone else's day.
        `reported`: the phone sent its next alarm lately. Then "no alarm" (None: none set, or the next one a day or more
        away) means the followed alarm was turned off before it rang, and the planned wake comes back. A phone that has
        gone quiet says nothing either way."""
        if not getattr(self.person.settings, "wake_with_alarm", True) or self.s.wake_by_hand:
            return False
        if alarm is None:
            fa = self.s.wake_from_alarm
            if reported and fa and now < fa and self.s.wake_override == fa:
                self.s.wake_override, self.s.wake_from_alarm = None, None
                self._ev(now, "wake_alarm_cleared", was=fa, reason="no alarm")
                return True
            return False
        from datetime import datetime
        d = night_date(datetime.fromtimestamp(now, self.tz))
        rhythm = pick_rhythm(self.person.rhythms, d)
        if rhythm is None or alarm <= now:
            return False
        planned = resolve(rhythm, d, self.tz).wake
        if not (planned - self.ALARM_EARLIER_S <= alarm <= planned + self.ALARM_LATER_S):
            # the alarm this wake came from was turned off before it rang (the phone's next alarm is now a later,
            # other day's one): back to the planned wake, so sleeping in really is sleeping in. An earlier odd alarm
            # (a 3 AM reminder) means nothing about the morning one, which is still set.
            fa = self.s.wake_from_alarm
            if fa and now < fa < alarm and self.s.wake_override == fa:
                self.s.wake_override, self.s.wake_from_alarm = None, None
                self._ev(now, "wake_alarm_cleared", was=fa, planned=planned)
                return True
            return False
        if self.s.wake_from_alarm == alarm:
            return False
        fa = self.s.wake_from_alarm
        if fa and fa <= now and fa < alarm <= fa + self.SNOOZE_S:
            # the followed alarm already rang and the next one is a few minutes after it: a snooze. The wake holds
            # at the time the alarm first rang (a reset alarm well after it is a real change of plan and is followed)
            return False
        self.s.wake_override, self.s.wake_from_alarm = alarm, alarm
        self._ev(now, "wake_from_alarm", wake=alarm, planned=planned)
        return True

    # ---------- tick ----------
    def tick(self, now: float, present: bool | None, home: bool | None = None, active: float | None = None) -> float | None:
        """`active` = since when the people in bed have been awake and moving (engine/active.py), else None."""
        from datetime import datetime
        dt = datetime.fromtimestamp(now, self.tz)
        d = night_date(dt)
        key = d.isoformat()
        if key != self.s.key:
            keep_flash, skip_next = self.s.flash, self.s.skip_next
            self.s = NightState(key=key, flash=keep_flash, skip=skip_next or (self.s.key is None and self.s.skip))
        self.s.home = home                               # None = unknown (no devices, no manual switch)
        rhythm = pick_rhythm(self.person.rhythms, d)
        if rhythm is None:
            self.s.state, self.s.target_c = "no_rhythm", None
            return None
        sched = rhythm.schedule
        n = self._resolve(rhythm, d)
        self.night = n
        if self._given_up_early(now, n, sched, present, active):
            # back in bed after giving the night up early (a false start in the evening): it starts again from the
            # beginning, through the same start as a first one, instead of resuming hours along its timeline
            self._start_again(now)
            n = self._resolve(rhythm, d)
            self.night = n

        # presence bookkeeping
        if active is not None:
            # awake and moving, not sleeping yet: a night that hasn't started waits until they settle; one that only
            # just started keeps its "Fall asleep" phase from moving on; nobody counts as out of bed (they may be on
            # the other side)
            if not self.s.started:
                self.s.presence_since = None
            elif self.s.in_bed is not None and self.s.in_bed >= active - 900 and now > self.s.in_bed:
                self.s.in_bed = now
            self.s.absent_since = self.s.out_since = None
        elif present:
            self.s.presence_since = self.s.presence_since or now
            self.s.absent_since = self.s.out_since = None
        elif present is False:
            self.s.presence_since = None
            if self.s.started:
                if self.s.absent_since is None:
                    self.s.out_since = now
                self.s.absent_since = self.s.absent_since or now
        away = bool(getattr(self, "away", False))      # away for a while (engine/away.py): nothing starts it
        if (not self.s.started and not away and self.start_gate is not False and sched.start_on_presence and present
                and self.s.presence_since
                and n.window_start <= now < n.wake - 3600 and now - self.s.presence_since >= confirm_min(self.person, sched) * 60):
            # earliest start = window_start; after window_end the empty side stops pre-conditioning, but getting in still
            # starts the night (a 2:40 AM arrival should get a working bed), up to an hour before wake
            self.s.started, self.s.in_bed = True, max(self.s.presence_since, n.window_start)
            self.s.sleep_at = self.s.sleep_why = None
            late = fitted_late(rhythm, d, self.tz, self.s.in_bed, self.s.wake_override)
            self._ev(now, "rhythm_started", in_bed=self.s.in_bed, **({"fitted": True} if late else {}),
                     **({"restarted": True} if self.s.reanchor else {}))
            self._began(now, "presence")
            n = self._resolve(rhythm, d)
            self.night = n
            o = self.s.override
            if o and o.until == "next_phase":
                # a change made while settling in is meant for the night you're starting, not the phase that just ended
                _, ph = target_at(n, now)
                o.phase_id = ph.id if ph else o.phase_id
        if not self.s.started and not away and not sched.start_on_presence and now >= n.bedtime:
            self.s.started, self.s.in_bed = True, n.bedtime
            self._began(now, "schedule")
        if present is None and active is None:
            self.s.unknown_since = self.s.unknown_since or now
        else:
            self.s.unknown_since = None
        busy = getattr(self, "busy_until", None)
        if (not self.s.started and not away and sched.start_on_presence and self.s.home is not False and now >= n.bedtime
                and self.s.unknown_since is not None and now - self.s.unknown_since >= 600 and now < n.wake - 3600
                and not (busy and now < busy)):              # out at a late event: wait for it (plus travel) to end
            # the bed can't tell us whether anyone is in this side (sensor silent or frozen): don't leave the person
            # with a cold or warm bed all night, fall back to the schedule
            self.s.started, self.s.in_bed = True, n.bedtime
            n = resolve(rhythm, d, self.tz, in_bed=self.s.in_bed, wake=self.s.wake_override)
            self.night = n
            self._ev(now, "rhythm_started", in_bed=self.s.in_bed, reason="no presence data, started on schedule",
                     **({"restarted": True} if self.s.reanchor else {}))
            self._began(now, "schedule")
        n = self._sleep_tick(now, rhythm, d, n, present, active)

        base: float | None
        phase = None
        if self.s.skip:
            state, base = "skipped", None
        elif self.s.pause_until and now < self.s.pause_until:
            state, base = "paused", None
        elif now >= n.end:
            state, base = "done", None
        elif away:
            state, base = "away", None
        elif self.s.started:
            if self.s.absent_since and now - self.s.absent_since > sched.absent_hold_min * 60:
                state, base = "out_of_bed", None
            else:
                base, ph = target_at(n, now)
                phase = ph.id if ph else None
                state = "out_of_bed_hold" if self.s.absent_since else "rhythm"
                base = self._lead(n, now, base, ph)
        elif now < n.precool - self._precool_lead(n):
            state, base = "day", None
        elif self.s.home is False:
            state, base = "away", None
        elif now > n.window_end + sched.absent_hold_min * 60:
            state, base = "missed", None
        else:
            state, base = "waiting", n.phases[0].temp_c if n.phases else None
            phase = n.phases[0].id if n.phases else None

        self.s.state, self.s.phase_id, self.s.base_c = state, phase, base
        target = base

        o = self.s.override
        if o and o.end_ts is not None and now >= o.end_ts:
            self.s.override = o = None
            self._ev(now, "adjust_ended", reason="timer")
        if o and o.manual and base is not None:
            self.s.override = o = None                   # the rhythm has started: it takes over from the by-hand session
            self._ev(now, "adjust_ended", reason="rhythm started")
        if o:
            if o.until == "next_phase" and phase is not None and o.phase_id is not None and phase != o.phase_id:
                self.s.override = o = None
            elif base is not None:
                target = o.temp_c if o.temp_c is not None else base + (o.delta_c or 0)
            elif o.manual and o.temp_c is not None and state not in ("skipped", "paused"):
                # only a by-hand "turn my side on" fills the time the rhythm has the side off (off/paused always wins);
                # a temperature set DURING the night (app, dial, HA) never keeps an empty side running after it
                target = o.temp_c

        f = self.s.flash
        if f:
            if now < f.end:
                target = f.target_c
            elif now < f.end + f.ease_s and target is not None:
                frac = (now - f.end) / f.ease_s
                target = f.target_c + (target - f.target_c) * frac
            elif now < f.end + f.ease_s and target is None:
                target = None
            else:
                self.s.flash = None

        self.s.target_c = None if target is None else round(target, 2)
        return self.s.target_c

    # ---------- a night given up early starts again ----------
    # Lying down briefly in the evening (testing a sensor, a short rest) starts the night and anchors the rhythm there.
    # Getting up and staying up means that start was false: when they come back the night starts again from the
    # beginning instead of resuming hours along (straight into "Deep", the cool "Fall asleep" skipped). A night that
    # was really under way (a 3 AM hour up after hours asleep) resumes as always.
    REANCHOR_AWAY_S = 60 * 60          # out of bed (as the bed saw it) at least this long without a break
    REANCHOR_IN_BED_S = 45 * 60        # and they'd got up within this long of the night's start
    REANCHOR_EVENING_IN_BED_S = 90 * 60  # or within this long, when they got up before their usual bedtime
    REANCHOR_BEFORE_WAKE_S = 2 * 3600  # never in the last 2 h before wake

    def _given_up_early(self, now: float, n: ResolvedNight, sched, present: bool | None, active: float | None) -> bool:
        s = self.s
        if not (s.started and s.started_by == "presence" and sched.start_on_presence and s.in_bed is not None
                and s.absent_since is not None):
            return False
        if not (present or active is not None) or getattr(self, "away", False):
            return False                              # not back (yet), or away: nothing starts
        if now - s.absent_since < self.REANCHOR_AWAY_S or now >= n.wake - self.REANCHOR_BEFORE_WAKE_S:
            return False
        left = s.out_since if s.out_since is not None else s.absent_since
        stayed = left - s.in_bed                      # from the night's start until they got up, short trips included
        return stayed < self.REANCHOR_IN_BED_S or (left < n.bedtime and stayed < self.REANCHOR_EVENING_IN_BED_S)

    def _start_again(self, now: float) -> None:
        s = self.s
        s.reanchor = {"in_bed": s.in_bed, "up_since": s.out_since if s.out_since is not None else s.absent_since}
        o = s.override
        if o and o.until == "next_phase" and not o.manual and o.phase_id is not None:
            # made for a phase of the night that was given up: that phase is over
            s.override = None
            self._ev(now, "adjust_ended", reason="night started again")
        s.started, s.in_bed, s.started_by = False, None, None
        s.absent_since = s.out_since = s.presence_since = None
        s.sleep_at = s.sleep_why = None

    # ---------- in bed early: wait for a sign of sleep before the night moves on ----------
    # Getting in before the usual bedtime (to read, to watch something) starts the night as always, but the bed holds
    # the getting-in ("Fall asleep") temperature: the in-bed phases after it (e.g. "Deep" at in-bed + 40 min) count
    # from the first sign that they are going to sleep (engine/sleepsign.py, set by the controller as `sleep_sign`),
    # or from the usual bedtime at the latest. With no sign at all the night runs exactly as if they had got in at
    # bedtime. Getting in at or after bedtime is unchanged. Setting: Schedule.wait_for_sleep (on by default).

    def waiting_bedtime(self, rhythm, d) -> float | None:
        """The usual bedtime this night waits for (in bed early, started by getting in, setting on), else None."""
        s = self.s
        if not (s.started and s.started_by == "presence" and s.in_bed is not None
                and getattr(rhythm.schedule, "wait_for_sleep", True) and getattr(rhythm.schedule, "start_on_presence", True)):
            return None
        from .rhythm import local_ts
        bedtime = local_ts(d, rhythm.schedule.bedtime, self.tz)
        return bedtime if s.in_bed < bedtime else None

    def _resolve(self, rhythm, d) -> ResolvedNight:
        bt = self.waiting_bedtime(rhythm, d)
        sleep = None if bt is None else (self.s.sleep_at if self.s.sleep_at is not None else bt)
        return resolve(rhythm, d, self.tz, in_bed=self.s.in_bed, wake=self.s.wake_override, sleep_at=sleep)

    def waiting_for_sleep(self) -> bool:
        """In bed early and no sign of sleep yet: the later in-bed phases start "when you settle"."""
        n = self.night
        if n is None or self.s.sleep_at is not None:
            return False
        rhythm = next((r for r in self.person.rhythms if r.id == n.rhythm_id), None)
        return rhythm is not None and self.waiting_bedtime(rhythm, n.date) is not None

    def _sleep_tick(self, now: float, rhythm, d, n: ResolvedNight, present: bool | None, active: float | None) -> ResolvedNight:
        bt = self.waiting_bedtime(rhythm, d)
        if bt is None or self.s.sleep_at is not None:
            return n
        cands = []
        if now >= bt:
            cands.append((bt, "bedtime"))                 # the backstop: as if they had got in at bedtime
        sign = self.sleep_sign
        # a sign only counts with them in bed on their side, settled enough to be still (not up, not active)
        if sign and present and active is None and self.s.absent_since is None and sign[0] <= now:
            cands.append(sign)
        if not cands:
            return n
        at, why = min(cands, key=lambda c: c[0])
        at = max(at, self.s.in_bed or at)
        self.s.sleep_at, self.s.sleep_why = at, why
        self._ev(now, "sleep_anchor", at=at, why=why, in_bed=self.s.in_bed, bedtime=bt)
        n = self._resolve(rhythm, d)
        self.night = n
        return n

    def _began(self, now: float, by: str) -> None:
        """The night has just started (any way): note how, and whether it started again after being given up."""
        self.s.started_by = by
        r = self.s.reanchor
        if r:
            self.s.reanchor = None
            self._ev(now, "rhythm_restarted", in_bed=self.s.in_bed, was_in_bed=r.get("in_bed"), up_since=r.get("up_since"))

    def _lead(self, n: ResolvedNight, now: float, base: float | None, ph) -> float | None:
        """Start a phase's change earlier in a cool room (warming) or a warm room (cooling), by the time this side's
        pad needs beyond the phase's own ramp (engine/padlag.py). The phase name doesn't change early."""
        self.lead_s = 0.0
        if base is None or self.room_c is None or not self.pad_model or not n.phases:
            return base
        from .padlag import lead_s
        ids = [p.id for p in n.phases]
        i = ids.index(ph.id) if ph is not None and ph.id in ids else -1
        cands = []
        if i + 1 < len(n.phases):
            cands.append(i + 1)                       # the coming phase
        if i >= 1 and now < n.phases[i].start + n.phases[i].ramp_s:
            cands.append(i)                           # still ramping into the current one (started early)
        for k in cands:
            p, prev = n.phases[k], n.phases[k - 1] if k >= 1 else None
            if prev is None:
                continue
            warming = p.temp_c > prev.temp_c
            if warming and self.hold_warm:
                continue
            lead = lead_s(self.pad_model, self.room_c, prev.temp_c, p.temp_c, p.ramp_s, p.start - now, p.start - prev.start)
            if lead and p.start - lead <= now < p.start + p.ramp_s:
                v, _ = target_at(n, min(now + lead, p.start + p.ramp_s))
                if v is not None:
                    base = max(base, v) if warming else min(base, v)
                    self.lead_s = lead
        return base

    def _precool_lead(self, n: ResolvedNight) -> float:
        if self.room_c is None or not self.pad_model or not n.phases:
            return 0.0
        from .padlag import precool_lead_s
        # from the room (an idle pad sits at room temperature): steady, so the earlier start doesn't flip back and forth
        return precool_lead_s(self.pad_model, self.room_c, self.room_c, n.phases[0].temp_c)

    def override_end(self, n: ResolvedNight) -> float | None:
        """When tonight's change stops applying: the end of the phase it was made in, or the end of the night."""
        o = self.s.override
        if not o:
            return None
        if o.manual:                              # a by-hand session: its timer, and never past the rhythm's start
            return min(o.end_ts, n.precool) if o.end_ts else n.precool
        if o.end_ts:
            return o.end_ts
        if o.until == "night" or o.phase_id is None:
            return n.end
        ids = [p.id for p in n.phases]
        if o.phase_id not in ids:
            return n.end
        i = ids.index(o.phase_id)
        return n.phases[i + 1].start if i + 1 < len(n.phases) else n.end

    def adjusted_at(self, n: ResolvedNight, t: float, base: float | None) -> float | None:
        """The plan at time t with tonight's change and any flash applied, the same way tick() applies them."""
        v = base
        o = self.s.override
        if o:
            end = self.override_end(n) or n.end
            if o.set_ts <= t < end:
                if o.manual:
                    v = o.temp_c if base is None else v       # only fills the time the rhythm has the side off
                elif base is not None:
                    v = o.temp_c if o.temp_c is not None else base + (o.delta_c or 0)
        f = self.s.flash
        if f and v is not None and f.start <= t < f.end + f.ease_s:
            v = f.target_c if t < f.end else f.target_c + (v - f.target_c) * ((t - f.end) / max(1.0, f.ease_s))
        return v

    def curve(self, n: ResolvedNight, step_s: float = 600) -> list[list[float]]:
        """Tonight's plan as it will actually run: the rhythm, plus the change you made and any flash."""
        if not n.phases:
            return []
        ts = set()
        t = n.phases[0].start
        while t < n.end:
            ts.add(t)
            t += step_s
        ts.add(n.end - 1)
        for p in n.phases:                          # crisp corners at every phase and ramp end
            ts.update((p.start, p.start + p.ramp_s))
        o, f = self.s.override, self.s.flash
        if o:
            e = self.override_end(n) or n.end
            ts.update((o.set_ts - 1, o.set_ts, e - 1, e))
        if f:
            ts.update((f.start - 1, f.start, f.end, f.end + f.ease_s))
        out = []
        for t in sorted(x for x in ts if n.phases[0].start <= x < n.end):
            base, _ = target_at(n, t)
            v = self.adjusted_at(n, t, base)
            if v is not None:
                out.append([t, round(v, 2)])
        return out

    def projected(self, now: float) -> ResolvedNight | None:
        """The night as it will actually play out. Before the rhythm has started (waiting/away), that means
        starting from *now* (or the usual bedtime if that is still ahead), not from a bedtime that already passed."""
        n = self.night
        if n is None or self.s.started or self.s.state not in ("waiting", "away", "day"):
            return n
        from datetime import datetime
        rhythm = pick_rhythm(self.person.rhythms, night_date(datetime.fromtimestamp(now, self.tz)))
        if rhythm is None:
            return n
        start = max(now, n.bedtime)
        if start >= n.wake - 3600:
            return n
        return resolve(rhythm, n.date, self.tz, in_bed=start, wake=self.s.wake_override)

    def next_info(self, now: float) -> dict | None:
        night = self.projected(now)
        if not night:
            return None
        p = next_phase(night, now)
        if p is None:
            return {"phase": "off", "name": "Side off", "at": night.end, "temp_c": None}
        out = {"phase": p.id, "name": p.name, "at": p.start, "temp_c": p.temp_c}
        if p.id in self.settle_phase_ids():
            out["when_settled"] = True                  # starts once they settle to sleep (bedtime at the latest)
        return out

    def settle_phase_ids(self) -> set[str]:
        """While waiting for a sign of sleep: the phases that start "when you settle" (none otherwise)."""
        if not self.waiting_for_sleep():
            return set()
        from .rhythm import sleep_phase_ids
        rhythm = next((r for r in self.person.rhythms if r.id == self.night.rhythm_id), None)
        return sleep_phase_ids(rhythm) if rhythm else set()

    def _ev(self, ts: float, kind: str, **data) -> None:
        self.s.events.append({"ts": ts, "kind": kind, "person": self.person.id, **data})


def confirm_min(person, sched) -> float:
    """Minutes lying down before the night starts: the person's own tuning, else the rhythm's (recommended 3)."""
    v = getattr(person.settings, "in_bed_after_min", None)
    return sched.confirm_min if v is None else v
