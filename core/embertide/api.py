"""HTTP + WebSocket API used by the web app, Studio, the Android app, dials and the MCP server."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .drivers.base import CommandError
from .engine.night import NightNotRunning
from .units import clock_style
from .models import Person, PersonSettings, Rhythm, clamp_phase_temp, off_nights, sync_rhythm_days, work_nights


class AdjustBody(BaseModel):
    delta_c: float | None = Field(None, ge=-10, le=10)
    temp_c: float | None = Field(None, ge=10, le=45)
    until: Literal["next_phase", "night", "every_night"] = "next_phase"
    source: str = Field("app", max_length=20)          # app | widget | tile | watch | voice | ha …
    minutes: int | None = Field(None, ge=5, le=720)            # a timer, e.g. a nap


class FlashBody(BaseModel):
    kind: Literal["hot", "cold"]
    minutes: int | None = Field(None, ge=1, le=120)
    source: str = Field("app", max_length=20)          # app | widget | tile | watch | voice | ha …


GuestZone = Literal["left", "right", "both"]


class GuestStartBody(BaseModel):
    temp_c: float | None = Field(None, ge=10.0, le=45.0)
    morning_off_min: int | None = Field(None, ge=30, le=45)
    ends: float | None = None
    dial: bool | None = None                       # None = keep the current choice (on for a new guest)
    source: str = Field("app", max_length=20)


class GuestSetBody(BaseModel):
    on: bool | None = None
    temp_c: float | None = Field(None, ge=10.0, le=45.0)
    morning_off_min: int | None = Field(None, ge=30, le=45)
    ends: float | None = None                      # 0 = until stopped
    dial: bool | None = None


class TonightBody(BaseModel):
    action: Literal["skip", "resume", "start_now", "pause", "delay", "end"]
    minutes: int | None = Field(None, ge=1, le=720)
    source: str = Field("app", max_length=20)


class AwayCheckBody(BaseModel):
    night: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    answer: str


class AwayBody(BaseModel):
    away: bool
    until: float | None = None    # unix seconds; back automatically then


class ContextBody(BaseModel):
    home: bool | None = None
    wake: float | None = None     # unix seconds, e.g. from a phone alarm


class SimBody(BaseModel):
    speed: float | None = Field(None, ge=0.1, le=3600)
    jump_to: str | None = Field(None, max_length=40)    # ISO local datetime, e.g. 2026-11-06T22:20
    presence: dict[Literal["left", "right"], bool] | None = None
    scripted: bool | None = None
    echo: bool | None = None
    blips: bool | None = None
    room_c: float | None = Field(None, ge=0, le=45)
    fault: str | None = Field(None, max_length=40)       # raise a control-unit fault (a code from hub.FAULTS)
    fault_zone: Literal["left", "right"] | None = None
    clear_faults: bool | None = None
    low_water: bool | None = None


class ConsoleBody(BaseModel):
    command: str


class ProfileBody(BaseModel):
    name: str | None = None
    persona: Literal["tide", "ember", "neutral"] | None = None
    zone: Literal["left", "right"] | None = None
    persona_temps: bool = False          # first-time setup: also start the rhythms from that persona's temperatures


MAX_RHYTHMS = 12


class FeedbackBody(BaseModel):
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")         # the night (evening date); left out = the night that just ended (a notification button)
    temp: Literal["cold", "right", "warm"] | None = None
    note: str | None = Field(None, max_length=2000)       # "how do you feel", in their own words
    rested: int | None = Field(None, ge=1, le=5)


_last_dump: list = [None, ""]          # [the snapshot dict itself, its JSON]; holding it avoids id() reuse


def _dumps(s: dict) -> str:
    """Serialise a snapshot once for all connected clients (every client gets the same dict each tick)."""
    if _last_dump[0] is not s:
        _last_dump[0], _last_dump[1] = s, json.dumps(s, separators=(",", ":"))
    return _last_dump[1]


PRIVATE_PRESENCE = ("hr", "br", "status", "status_text")
LEAK_HOLD_MSG = "The bed is off because a leak sensor went off. Check for water, then clear it in the app."


def for_viewer(snap: dict | None, who) -> dict | None:
    """The live snapshot as one account may see it. The owner (or sign-in off) sees everything; the partner and
    view-only accounts see the other person's side and plan, but not their heart rate and breathing, their phone /
    calendar / location context, or which of their devices voted on presence (auth.PRIVATE_READS, live)."""
    if not snap or who is None or who.role == "owner" or who.kind == "open":
        return snap
    people = []
    for p in snap.get("people", []):
        if p.get("id") == who.person:
            people.append(p)
            continue
        pres = {k: (None if k in PRIVATE_PRESENCE else v) for k, v in (p.get("presence") or {}).items()}
        if isinstance(pres.get("fusion"), dict):
            pres["fusion"] = {"present": pres["fusion"].get("present")}
        n = p.get("night") or {}
        away = n.get("away")
        # `home` is the phone / home-sensor verdict (location context, private like `context`); `away` stays because
        # the partner is meant to see "away until Friday, their side stays off", but only its dates
        night = {**n, "context": None, "away_check": None, "home": None,
                 "away": {"since": away.get("since"), "until": away.get("until"), "by": None, "held": 0} if isinstance(away, dict) else away}
        people.append({**p, "presence": pres, "night": night})
    return {**snap, "people": people}


class RegionBody(BaseModel):
    hemisphere: Literal["auto", "north", "south", "none"]


def build_router(app_state) -> APIRouter:
    r = APIRouter(prefix="/api")

    def ctl():
        return app_state.controller

    def find(pid: str) -> Person:
        try:
            return ctl().person(pid)
        except KeyError:
            raise HTTPException(404, f"unknown person {pid}")

    def not_guest(pid: str) -> None:
        if find(pid).zone in ctl().household.guest:
            raise HTTPException(409, "that side is lent to a guest: use the guest controls")

    def not_leaking() -> None:
        if ctl().leak_hold:
            raise HTTPException(409, LEAK_HOLD_MSG)

    async def refresh(pid: str | None = None):
        return await ctl().tick(force={pid} if pid else None)

    @r.get("/snapshot")
    async def snapshot(request: Request):
        return for_viewer(ctl().last_snapshot or await refresh(), request.scope.get("state", {}).get("principal"))

    @r.post("/live/ticket")
    async def live_ticket(request: Request):
        """A single-use ticket (30 s) for opening /api/live?ticket=… as this account, so the app's session never goes in
        a URL. Device keys can't get one (auth.DEVICE_SCOPES)."""
        from .auth import TICKET_S
        who = request.scope.get("state", {}).get("principal")
        if who is None:
            raise HTTPException(401, "Please sign in.")
        return {"ticket": app_state.auth.new_ticket(who), "expires_in": TICKET_S}

    @r.websocket("/live")
    async def live(ws: WebSocket):
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=2)

        def push(s: dict):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(s)
        ctl().listeners.append(push)
        who = ws.scope.get("state", {}).get("principal")
        try:
            if ctl().last_snapshot:
                await ws.send_text(_dumps(for_viewer(ctl().last_snapshot, who)))
            while True:
                s = await q.get()
                await ws.send_text(_dumps(for_viewer(s, who)))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if push in ctl().listeners:
                ctl().listeners.remove(push)

    # ---------- control ----------
    @r.post("/leak/clear")
    async def leak_clear(request: Request, source: str = Query("app", max_length=20, pattern=r"^[a-z_]{1,20}$")):
        c = ctl()
        who = request.scope.get("state", {}).get("principal")
        actor = getattr(who, "person", None) or None
        ok, err = c.clear_leak_hold(source=source, actor=actor)
        if not ok:
            raise HTTPException(409, err)
        return await ctl().tick(force={p.id for p in c.household.people})

    @r.post("/people/{pid}/adjust")
    async def adjust(pid: str, b: AdjustBody):
        p = find(pid)
        not_guest(pid)
        not_leaking()
        e = ctl().engine_for(pid)
        now = ctl().clock.now_ts()
        if b.until == "every_night":
            # write the change into the current phase of tonight's rhythm
            phase_id = e.s.phase_id
            if not phase_id or not e.night:
                raise HTTPException(409, "no active phase to change")
            for rh in p.rhythms:
                if rh.id == e.night.rhythm_id:
                    for ph in rh.phases:
                        if ph.id == phase_id:
                            raw = b.temp_c if b.temp_c is not None else ph.temp_c + (b.delta_c or 0)
                            ph.temp_c = clamp_phase_temp(raw, ctl().household)
            ctl().reload_household(ctl().household)
            e.clear_override(now)
            ctl().store.event(now, "rhythm_changed", pid, b.source, phase=phase_id)
        else:
            e.adjust(now, delta_c=b.delta_c, temp_c=b.temp_c, until=b.until, source=b.source, minutes=b.minutes)
        return await refresh(pid)

    @r.post("/people/{pid}/adjust/clear")
    async def adjust_clear(pid: str, source: str = Query("app", max_length=20, pattern=r"^[a-z_]{1,20}$")):
        find(pid)
        not_guest(pid)
        ctl().engine_for(pid).clear_override(ctl().clock.now_ts(), source)
        return await refresh(pid)

    @r.post("/people/{pid}/flash")
    async def flash(pid: str, b: FlashBody):
        find(pid)
        not_guest(pid)
        not_leaking()
        ctl().engine_for(pid).flash(b.kind, ctl().clock.now_ts(), b.minutes, b.source)
        return await refresh(pid)

    @r.post("/people/{pid}/flash/extend")
    async def flash_extend(pid: str, minutes: int | None = Query(None, ge=1, le=60), source: str = Query("app", max_length=20, pattern=r"^[a-z_]{1,20}$")):
        find(pid)
        not_guest(pid)
        not_leaking()
        ctl().engine_for(pid).extend_flash(ctl().clock.now_ts(), minutes, source)
        return await refresh(pid)

    @r.post("/people/{pid}/flash/stop")
    async def flash_stop(pid: str, source: str = Query("app", max_length=20, pattern=r"^[a-z_]{1,20}$")):
        find(pid)
        not_guest(pid)
        ctl().engine_for(pid).stop_flash(ctl().clock.now_ts(), source)
        return await refresh(pid)

    @r.post("/people/{pid}/tonight")
    async def tonight(pid: str, b: TonightBody):
        find(pid)
        not_guest(pid)
        if b.action in ("resume", "start_now"):
            not_leaking()
        try:
            ctl().engine_for(pid).tonight(ctl().clock.now_ts(), b.action, b.minutes, b.source)
        except NightNotRunning as ex:
            raise HTTPException(409, str(ex)) from None
        return await refresh(pid)

    @r.post("/people/{pid}/context")
    async def context(pid: str, b: ContextBody):
        find(pid)
        if b.home is not None:
            ctl().home[pid] = b.home
        if b.wake is not None:
            now = ctl().clock.now_ts()
            if not now - 3600 <= b.wake <= now + 24 * 3600:
                raise HTTPException(422, "wake time must be within the next 24 hours")
            st = ctl().engine_for(pid).s
            st.wake_override, st.wake_by_hand = b.wake, True       # by hand: the phone's alarm no longer moves it
        return await refresh(pid)

    @r.post("/people/{pid}/away")
    async def set_away(pid: str, b: AwayBody, request: Request):
        """Away for a while: the night pauses (and so does the calibration week) until they're back."""
        find(pid)
        not_guest(pid)
        from .engine import away
        c = ctl()
        now = c.clock.now_ts()
        who = request.scope.get("state", {}).get("principal")
        by = getattr(who, "person", None) or "app"
        if b.away:
            if b.until is not None and not now < b.until <= now + 90 * 86400:
                raise HTTPException(422, "the end date must be within the next 90 days")
            away.start(c, pid, now, by=by, until=b.until)
            e = c.engine_for(pid)
            e._end_flash(now, by)
            if e.s.started and e.s.state in ("waiting", "rhythm", "out_of_bed_hold", "out_of_bed"):
                e.s.started, e.s.in_bed, e.s.reanchor = False, None, None   # started by whatever lay there: not their night
        else:
            away.end(c, pid, now, "back", by=by)
        return await refresh(pid)

    @r.post("/people/{pid}/away-check")
    async def away_check(pid: str, b: AwayCheckBody):
        """The morning check answered: away / night / home, or back / still."""
        find(pid)
        from .engine import away
        try:
            away.answer_check(ctl(), pid, b.night, b.answer, ctl().clock.now_ts())
        except KeyError:
            raise HTTPException(404, "no such question")
        except ValueError as ex:
            raise HTTPException(422, str(ex))
        return await refresh(pid)

    # ---------- configuration ----------
    @r.get("/household")
    async def household():
        return ctl().household.model_dump()

    @r.get("/household/region")
    async def region():
        """The time zone the server runs in (Studio shows it so people can check) and the hemisphere used for seasons."""
        from .units import hemisphere_of
        c = ctl()
        tzn = getattr(c.clock.tz, "key", str(c.clock.tz))
        return {"timezone": tzn, "timezone_source": getattr(app_state.cfg, "tz_source", ""), "now": c.clock.now().isoformat(timespec="minutes"), "hemisphere_setting": c.household.hemisphere,
                "hemisphere": hemisphere_of(tzn, c.household.hemisphere)}

    @r.put("/household/region")
    async def put_region(b: RegionBody):
        ctl().household.hemisphere = b.hemisphere
        ctl().reload_household(ctl().household)
        return await region()

    @r.put("/people/{pid}/settings")
    async def put_settings(pid: str, s: PersonSettings):
        p = find(pid)
        s.autopilot.learn_since = p.settings.autopilot.learn_since    # only "Start over: recalibrate" moves this
        p.settings = s
        sync_rhythm_days(p)
        ctl().reload_household(ctl().household)
        return await refresh(pid)


    @r.patch("/people/{pid}/settings")
    async def patch_settings(pid: str, b: dict):
        """Change only the given settings (e.g. the owner switching a partner's phone to the Simple app), so a change
        the person makes at the same moment isn't overwritten."""
        p = find(pid)
        try:
            keep = p.settings.autopilot.learn_since
            p.settings = PersonSettings(**{**p.settings.model_dump(), **b})
            p.settings.autopilot.learn_since = keep
        except Exception as e:  # noqa: BLE001  (pydantic ValidationError)
            raise HTTPException(422, str(e).splitlines()[0])
        sync_rhythm_days(p)                      # work days changed: the work-night / day-off rhythms follow
        ctl().reload_household(ctl().household)
        return await refresh(pid)

    @r.put("/people/{pid}/profile")
    async def put_profile(pid: str, b: ProfileBody):
        p = find(pid)
        if b.zone and b.zone != p.zone and any(x.id != pid and x.zone == b.zone for x in ctl().household.people):
            raise HTTPException(409, "someone already sleeps on that side; use 'swap sides' instead")
        for k, v in b.model_dump(exclude_none=True, exclude={"persona_temps"}).items():
            setattr(p, k, v)
        if b.persona and b.persona_temps:
            from .seed import apply_persona_temps
            apply_persona_temps(p)
        ctl().reload_household(ctl().household)
        return await refresh(pid)

    # ---------- guest mode ----------
    @r.get("/guest")
    async def guest_get():
        return {z: g.model_dump() for z, g in ctl().household.guest.items()}

    @r.post("/guest/{zone}/start")
    async def guest_start(zone: GuestZone, b: GuestStartBody):
        c = ctl()
        not_leaking()
        if b.ends is not None and not c.clock.now_ts() < b.ends <= c.clock.now_ts() + 30 * 86400:
            raise HTTPException(422, "the end has to be within the next 30 days")
        zones = ["left", "right"] if zone == "both" else [zone]
        dial = {z: b.dial if b.dial is not None else (g.dial if (g := c.household.guest.get(z)) else True) for z in zones}
        c.guest_start(zones, b.temp_c, b.morning_off_min, b.ends, b.source)
        for z in zones:
            c.household.guest[z].dial = dial[z]
        c.reload_household(c.household)
        return await refresh()

    @r.post("/guest/{zone}/stop")
    async def guest_stop(zone: GuestZone, source: str = Query("app", max_length=20, pattern=r"^[a-z_]{1,20}$")):
        ctl().guest_stop(["left", "right"] if zone == "both" else [zone], source)
        return await refresh()

    @r.post("/guest/{zone}/set")
    async def guest_set(zone: GuestZone, b: GuestSetBody):
        c = ctl()
        if b.on is True or b.temp_c is not None:
            not_leaking()
        zones = ["left", "right"] if zone == "both" else [zone]
        if not all(z in c.household.guest for z in zones):
            raise HTTPException(409, "that side isn't in guest mode")
        now = c.clock.now_ts()
        if b.ends and not now < b.ends <= now + 30 * 86400:
            raise HTTPException(422, "the end has to be within the next 30 days")
        for z in zones:
            c.guest_set(z, b.on, b.temp_c)
            g = c.household.guest[z]
            if b.morning_off_min is not None:
                g.morning_off_min = b.morning_off_min
            if b.ends is not None:
                g.ends = b.ends or None
            if b.dial is not None:
                g.dial = b.dial
        c.reload_household(c.household)
        return await refresh()

    @r.post("/household/swap-sides")
    async def swap_sides():
        """Swap which side of the bed each person sleeps on (Controller.swap_sides says what moves and what stays)."""
        c = ctl()
        if c.household.guest:
            raise HTTPException(409, "end guest mode before swapping sides")
        if len(c.household.people) != 2:
            raise HTTPException(409, "swapping needs exactly two people")
        c.swap_sides()
        c.store.event(c.clock.now_ts(), "sides_swapped", None, "app", left=next((p.name for p in c.household.people if p.zone == "left"), None))
        return await refresh()

    @r.get("/people/{pid}/rhythms")
    async def get_rhythms(pid: str):
        return [x.model_dump() for x in find(pid).rhythms]

    @r.put("/people/{pid}/rhythms/{rid}")
    async def put_rhythm(pid: str, rid: str, rh: Rhythm):
        p = find(pid)
        if any(abs(ph.start.offset_min) > 720 for ph in rh.phases):
            raise HTTPException(422, "a phase can start at most 12 hours from its anchor")
        if not rh.phases:
            raise HTTPException(422, "a rhythm needs at least one phase")
        from datetime import date as _date
        from .engine.rhythm import local_ts as _lt
        d0, tz, sc = _date(2026, 1, 5), ctl().clock.tz, rh.schedule
        base = {"bedtime": _lt(d0, sc.bedtime, tz), "in_bed": _lt(d0, sc.bedtime, tz), "wake": _lt(d0, sc.wake, tz)}
        starts = [(base.get(ph.start.kind) or _lt(d0, ph.start.time or "03:00", tz)) + ph.start.offset_min * 60 for ph in rh.phases]
        if any(b < a for a, b in zip(starts, starts[1:])):
            raise HTTPException(422, "phases are out of order: each one has to start after the one before")
        hh = ctl().household
        for ph in rh.phases:
            ph.temp_c = clamp_phase_temp(ph.temp_c, hh)
        if rh.nights:
            want = work_nights(p.settings.work_days) if rh.nights == "work" else off_nights(p.settings.work_days)
            if rh.days != want:
                rh.nights = ""                   # days picked by hand: this rhythm no longer follows work days
        if not any(x.id == rid for x in p.rhythms) and len(p.rhythms) >= MAX_RHYTHMS:
            raise HTTPException(422, f"a person can have up to {MAX_RHYTHMS} rhythms; delete one first")
        p.rhythms = [rh if x.id == rid else x for x in p.rhythms]
        if not any(x.id == rid for x in p.rhythms):
            p.rhythms.append(rh)
        ctl().reload_household(ctl().household)
        ctl().store.event(ctl().clock.now_ts(), "rhythm_changed", pid, "studio", rhythm=rid)
        return await refresh(pid)

    # ---------- nights (Sleep screen) ----------
    @r.get("/people/{pid}/nights")
    async def nights(pid: str, count: int = 7):
        from .engine.summary import recent
        c = ctl()
        from .engine.away import hide_away_night
        now = c.clock.now_ts()
        return [hide_away_night(c.store, pid, n, now, c.clock.tz) for n in recent(c.store, find(pid), c.clock.tz, now, max(1, min(30, count)))]

    @r.get("/people/{pid}/night")
    async def night(pid: str, date: str | None = None):
        from .autopilot_api import _day
        from .engine.rhythm import night_date
        from .engine.summary import summarize
        c = ctl()
        d = _day(date) if date else night_date(c.clock.now())
        p = find(pid)
        s = summarize(c.store, p, d, c.clock.tz, c.clock.now_ts(), series=True)
        if s is None:
            raise HTTPException(404, "no rhythm for that night")
        from .engine.away import hide_away_night
        gone = hide_away_night(c.store, pid, s, c.clock.now_ts(), c.clock.tz)
        if gone is not s:
            return gone                              # away for most of it: no sleep numbers, just the marker
        from .engine.stages import collect
        from .engine.summary import night_window
        win = night_window(p, d, c.clock.tz, c.clock.now_ts())
        st = collect(c.store, p, *win) if win else []
        s["stages"] = st[0] if st else None
        s["stage_sources"] = [{"id": x["id"], "label": x["label"], "kind": x["kind"]} for x in st]
        # the wearable's night next to the bed's, and which one is used (engine/compare.py)
        from .engine.compare import compare_night
        try:
            s["compare"] = compare_night(c, p, d.isoformat())
        except Exception:  # noqa: BLE001
            s["compare"] = None
        trust = getattr(p.settings.autopilot, "trust_more", "wearable")
        # naps earlier that day (Oura and other wearables; on the bed or away) and the day's whole sleep (engine/sleeps.py)
        from .engine import sleeps as SL
        if win:
            naps = SL.naps_before(c.store, p.id, win[0], c.clock.tz)
            s["naps"] = [SL.view(x, c.clock.tz, clock_style(p.settings)) for x in naps]
            s["nap_min"] = sum(x.get("asleep_min") or 0 for x in naps)
            night = next((x for x in SL.all_for(c.store, p.id) if x["kind"] == "night" and x["start"] < win[1] and x["end"] > win[0]), None)
            s["night_asleep_min"] = night.get("asleep_min") if night else None      # the wearable's own "asleep" for the night
        # the ring's own numbers for the night (readiness, temperature vs normal, HRV...) next to the bed's (engine/ring.py)
        from .engine.ring import ring_night
        try:
            s["ring"] = ring_night(c, p, *win, bed=s) if win else None
        except Exception:  # noqa: BLE001
            s["ring"] = None
        if st and trust == "bed":
            est = next((x for x in st if x["kind"] == "estimate"), None)
            if est:
                s["stages"] = est                        # "trust the bed more": the bed's own estimate first
        from .voice import night_words
        return night_words(s, p, d)                      # story lines and the check-in question in this person's tone

    @r.get("/people/{pid}/sleeps")
    async def sleeps(pid: str, days: int = 14):
        """Every sleep the person's watch or ring recorded (nights and naps), newest first, plus per-day totals."""
        from .engine import sleeps as SL
        c = ctl()
        p = find(pid)
        now = c.clock.now_ts()
        days = max(1, min(90, days))
        items = [SL.view(x, c.clock.tz, clock_style(p.settings)) for x in SL.all_for(c.store, p.id) if x["end"] >= now - days * 86400]
        for x in items:
            x.pop("segments", None)
        return {"sleeps": list(reversed(items)), "days": SL.day_totals(c.store, p.id, c.clock.tz, days, now)}

    @r.get("/voice/samples")
    async def voice_samples(name: str = "Alex"):
        """A few example lines per tone, for the tone picker's preview (Settings, Studio → Your settings)."""
        from .voice import TONES, samples
        return {t: samples(t, name[:40], ctl().clock.now().date()) for t in TONES}


    @r.post("/people/{pid}/feedback")
    async def feedback(pid: str, b: FeedbackBody):
        find(pid)
        c = ctl()
        if b.date is None:
            from datetime import datetime, timedelta
            from .engine.rhythm import night_date
            b.date = night_date(datetime.fromtimestamp(c.clock.now_ts(), c.clock.tz) - timedelta(hours=12)).isoformat()
        prev = c.store.get(f"feedback:{pid}:{b.date}", {}) or {}
        merged = {**prev, **{k: v for k, v in {"temp": b.temp, "note": b.note, "rested": b.rested}.items() if v is not None}, "ts": c.clock.now_ts()}
        c.store.put(f"feedback:{pid}:{b.date}", merged)
        c.store.event(c.clock.now_ts(), "feedback", pid, "app", night=b.date, temp=b.temp, note=b.note, rested=b.rested)
        return {"ok": True}

    # ---------- history / events / export ----------
    def _viewer(request: Request):
        """The signed-in principal when it isn't the owner (None for the owner, or with sign-in off)."""
        who = request.scope.get("state", {}).get("principal")
        return who if who is not None and who.role != "owner" and who.kind != "open" else None

    @r.get("/history")
    async def history(request: Request, zone: str, hours: float = 12, step: float = 60):
        who = _viewer(request)
        if who is not None:                                # heart rate and breathing: your own side only
            mine = next((p.zone for p in ctl().household.people if p.id == who.person), None)
            if zone != mine:
                raise HTTPException(403, "That's their own sleep data.")
        now = ctl().clock.now_ts()
        return ctl().store.history(zone, now - max(0.0, min(hours, 24 * 14)) * 3600, step_s=max(step, 15))

    @r.get("/events")
    async def events(request: Request, limit: int = 100, person: str | None = None):
        who = _viewer(request)
        evs = ctl().store.events(limit=max(1, min(limit, 5000)), person=person)
        if who is not None:                                # yours and the household's, not the partner's notes and nights
            evs = [e for e in evs if e.get("person") in (None, who.person)]
        return evs

    @r.get("/export")
    async def export(request: Request, fmt: Literal["csv", "jsonl"] = "csv", hours: float = 24 * 30, pseudonymize: bool = False):
        c = ctl()
        now = c.clock.now_ts()
        rows = []
        who = request.scope.get("state", {}).get("principal")
        for i, p in enumerate(c.household.people):
            if who is not None and who.role != "owner" and who.person != p.id:
                continue                                   # your own data only, unless you're the owner
            name = f"P{i + 1}" if pseudonymize else p.name
            for h in c.store.history(p.zone, now - max(0.0, hours) * 3600, person=p.id):
                rows.append({"person": name, "zone": p.zone,
                             "time": datetime.fromtimestamp(h["ts"], c.clock.tz).isoformat(), **{k: v for k, v in h.items() if k != "ts"}})
        rows.sort(key=lambda x: x["time"])
        if fmt == "jsonl":
            body = "\n".join(json.dumps(x) for x in rows)
            return PlainTextResponse(body, media_type="application/x-ndjson",
                                     headers={"Content-Disposition": "attachment; filename=embertide-telemetry.jsonl"})
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["time", "person", "zone", "on", "setpoint", "measured", "target", "phase", "occupied", "hr", "br", "status"])
        w.writeheader()
        for x in rows:
            w.writerow({k: x.get(k) for k in w.fieldnames})
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=embertide-telemetry.csv"})

    # ---------- bed ----------
    @r.get("/system")
    async def system():
        s = ctl().last_snapshot or await refresh()
        return s["system"]

    @r.post("/console")
    async def console(b: ConsoleBody):
        # while a leak sensor has the bed off, nothing that moves water (filling, priming) runs from here either
        if getattr(ctl(), "leak_hold", None) and any(w in b.command.upper() for w in ("FILL", "PRIME", "PUMP", "SET", "ON")):
            raise HTTPException(409, "The bed is off because a leak sensor went off. Check for water, then clear it in the app.")
        try:
            out = await ctl().driver.command(b.command)
        except CommandError as e:
            raise HTTPException(400, str(e))
        ctl().store.event(ctl().clock.now_ts(), "console", None, "studio", command=b.command)
        return {"result": out}

    # ---------- simulator ----------
    @r.get("/sim")
    async def sim_get():
        d = ctl().driver
        if getattr(d, "id", "") != "sim":
            raise HTTPException(404, "not running the simulator")
        return {"speed": ctl().clock.speed, "now": ctl().clock.now().isoformat(), "scripted": d.scripted, "echo": d.echo,
                "blips": d.blips, "room_c": d.room_c, "low_water": d.low_water,
                "faults": [c for c, _ in d.faults], "presence": {k: v.occupied for k, v in d.z.items()}}

    @r.post("/sim")
    async def sim_post(b: SimBody):
        d = ctl().driver
        if getattr(d, "id", "") != "sim":
            raise HTTPException(404, "not running the simulator")
        c = ctl().clock
        if b.speed is not None:
            c.set_speed(b.speed)
        if b.jump_to:
            try:
                dt = datetime.fromisoformat(b.jump_to)
            except ValueError:
                raise HTTPException(422, "jump_to must be a date and time like 2026-11-06T22:20")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=c.tz)
            c.jump_to(dt.timestamp())
            d._last_ts = c.now_ts()
            ctl().store.drop_after(c.now_ts())
            ctl()._last_tel = 0.0
            from .engine.night import NightState
            for e in ctl().engines.values():
                e.s = NightState()
            ctl().presence.sides.clear()
        if b.scripted is not None:
            d.scripted = b.scripted
        if b.presence:
            for zone, occ in b.presence.items():
                d.set_presence(zone, occ)
        if b.echo is not None:
            d.echo = b.echo
        if b.blips is not None:
            d.blips = b.blips
        if b.room_c is not None:
            d.room_c = b.room_c
        if b.clear_faults:
            d.clear_faults()
        if b.fault:
            from .hub import FAULTS
            if b.fault not in FAULTS:
                raise HTTPException(422, "Unknown fault. Known: " + ", ".join(sorted(FAULTS)))
            d.inject_fault(b.fault, b.fault_zone)
        if b.low_water is not None:
            d.low_water = b.low_water
        d._tick()
        await refresh()
        return await sim_get()

    return r
