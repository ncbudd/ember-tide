"""Who was in bed, and the person's say over it.

- POST /api/people/{pid}/watch-hr           the phone relays the watch's batched night heart rate (engine/watch.py)
- GET  /api/people/{pid}/watch-hr           when it last arrived, whether the watch should be collecting now
- GET  /api/people/{pid}/pet-question       the one open "was the pet on the bed?" question (or null)
- POST /api/people/{pid}/pet-question/{id}  answer it: yes (+ pet) / me / unsure (engine/petq.py)
- GET  /api/pet-questions                   Studio: recent questions and what has been learned per pet
- GET/PUT/DELETE /api/people/{pid}/night-fix?date=   "Fix this night" corrections (engine/compare.py)
- GET  /api/people/{pid}/compare?date=      the bed's night next to the wearable's
"""

from __future__ import annotations

import math
import time
from datetime import date as _date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .units import clock_style


class WatchHrBody(BaseModel):
    samples: list[tuple[float, float]] = Field(default_factory=list, max_length=600)   # [unix s, bpm]
    steps: list[tuple[float, float]] = Field(default_factory=list, max_length=300)     # [unix s, steps in that interval]
    device: str | None = Field(None, max_length=60)

    @field_validator("samples", "steps")
    @classmethod
    def _finite(cls, v):
        for t, x in v:
            if not (math.isfinite(t) and math.isfinite(x)):
                raise ValueError("must be numbers")
        return v


class AnswerBody(BaseModel):
    answer: str = Field(pattern="^(yes|me|unsure)$")
    pet: str | None = Field(None, max_length=16)


class Span(BaseModel):
    start: float
    end: float


class Stretch(Span):
    who: str = Field(pattern="^(pet|me)$")
    pet: str | None = Field(None, max_length=16)


class FixBody(BaseModel):
    date: str
    in_bed_at: float | None = None
    asleep_at: float | None = None
    woke_at: float | None = None
    out_at: float | None = None
    ups_wrong: list[Span] = Field(default_factory=list, max_length=30)
    ups_added: list[Span] = Field(default_factory=list, max_length=30)
    stretches: list[Stretch] = Field(default_factory=list, max_length=30)


MIN_GAP_S = 20


def _ck(c, q):
    try:
        return clock_style(c.person(q["person"]).settings)
    except (KeyError, TypeError):
        return "24"


def build_identity_router(state) -> APIRouter:
    r = APIRouter(prefix="/api")
    last_post: dict[str, float] = {}

    def ctl():
        return state.controller

    def person(pid: str):
        p = next((x for x in ctl().household.people if x.id == pid), None)
        if not p:
            raise HTTPException(404, "unknown person")
        return p

    def day(d: str | None):
        from .engine.rhythm import night_date
        if not d:
            return night_date(ctl().clock.now())
        try:
            return _date.fromisoformat(d)
        except ValueError:
            raise HTTPException(422, "date must be YYYY-MM-DD")

    # ---------- watch heart rate ----------
    @r.post("/people/{pid}/watch-hr")
    async def post_watch_hr(pid: str, b: WatchHrBody):
        from .engine import watch as W
        p = person(pid)
        c = ctl()
        now = c.clock.now_ts()
        mono = time.monotonic()
        if mono - last_post.get(pid, -1e9) < MIN_GAP_S:
            raise HTTPException(429, "too many updates; the phone sends the watch's readings every few minutes")
        last_post[pid] = mono
        if p.zone in c.household.guest:
            return {"ok": True, "stored": 0, "wanted": False}
        samples = [(t, v) for t, v in b.samples if now - 2 * 3600 <= t <= now + 300 and 25 <= v <= 230]
        steps = [(t, n) for t, n in b.steps if now - 3600 <= t <= now + 300 and 0 <= n <= 5000]
        stored = W.record(c.store, pid, samples, steps, now)
        return {"ok": True, "stored": stored, "rejected": len(b.samples) - len(samples), "wanted": W.wanted(c, p, now)}

    @r.get("/people/{pid}/watch-hr")
    async def get_watch_hr(pid: str):
        from .engine import watch as W
        p = person(pid)
        c = ctl()
        now = c.clock.now_ts()
        st = c.store.get(f"watch_live:{pid}") or {}
        hr, _ = W.recent(c.store, pid, now, 3600)
        return {"seen": st.get("seen"), "last": hr[-1] if hr else None, "count_hour": len(hr), "wanted": W.wanted(c, p, now),
                "fusion": (c.fused.get(p.zone) or {}).get("watch")}

    # ---------- pet questions ----------
    @r.get("/people/{pid}/pet-question")
    async def get_question(pid: str):
        from .engine import petq
        p = person(pid)
        c = ctl()
        if not p.settings.pet_questions:
            return None
        q = petq.open_for(c.store, pid, c.clock.now_ts())
        if not q:
            return None
        v = petq.view(q, c.household, c.clock.tz, clock_style(p.settings))
        from . import voice                               # the question, in this person's tone
        v["title"] = voice.say("pet_question", {"who": v["who"], "when": v["when"]}, person=p, day=c.clock.now().date())[0]
        return v

    @r.post("/people/{pid}/pet-question/{qid}")
    async def answer_question(pid: str, qid: str, b: AnswerBody):
        from .engine import petq
        person(pid)
        c = ctl()
        try:
            q = petq.answer(c, qid, pid, b.answer, b.pet, c.clock.now_ts())
        except KeyError:
            raise HTTPException(404, "that question is gone")
        except ValueError as e:
            raise HTTPException(422, str(e))
        msg = {"yes": "Thanks. Ember & Tide will know it better next time.",
               "me": "Thanks. Readings like these will count as you.",
               "unsure": "No problem. Nothing was changed."}[b.answer]
        return {"ok": True, "status": q["status"], "text": msg}

    @r.get("/pet-questions")
    async def all_questions(request: Request):
        from .engine import petq
        c = ctl()
        who = request.scope.get("state", {}).get("principal")
        mine = None if who is None or who.role == "owner" or who.kind == "open" else who.person   # partner/viewer: their own only
        qs = [petq.view(q, c.household, c.clock.tz, _ck(c, q)) | {"person": q["person"], "created": q["created"],
                                                         "answered_at": q.get("answered_at")}
              for q in petq.questions(c.store)[-20:] if mine is None or q["person"] == mine]
        return {"questions": list(reversed(qs)), "pets": petq.signature_summary(c.store, c.household),
                "ranges": {p.id: petq.learned_range(c.store, p.id) for p in c.household.people if mine is None or p.id == mine}}

    # ---------- corrections + comparison ----------
    @r.get("/people/{pid}/night-fix")
    async def get_fix(pid: str, date: str | None = None):
        from .engine.compare import get_fix as gf
        person(pid)
        return gf(ctl().store, pid, day(date).isoformat()) or {}

    @r.put("/people/{pid}/night-fix")
    async def put_fix(pid: str, b: FixBody):
        from .engine import compare as C
        from .engine.summary import night_window
        p = person(pid)
        c = ctl()
        d = day(b.date)
        win = night_window(p, d, c.clock.tz, c.clock.now_ts())
        if not win:
            raise HTTPException(404, "no rhythm that night")
        try:
            fix = C.clean_fix(b.model_dump(), win[0], win[1], c.household.pets)
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e))
        fix["updated"] = c.clock.now_ts()
        c.store.put(f"night_fix:{pid}:{d.isoformat()}", fix)
        C.learn_from_fix(c, p, d.isoformat(), fix, *win)
        from .engine import petq                       # a pet question about the same stretch is answered by the fix
        petq.settle_from_fix(c, pid, fix.get("stretches") or [], c.clock.now_ts())
        c.store.event(c.clock.now_ts(), "night_fixed", pid, "app", date=d.isoformat())
        _drop_caches(c, pid)
        return {"ok": True, "fix": fix, "compare": C.compare_night(c, p, d.isoformat())}

    @r.delete("/people/{pid}/night-fix")
    async def delete_fix(pid: str, date: str | None = None):
        person(pid)
        c = ctl()
        d = day(date).isoformat()
        c.store.put(f"night_fix:{pid}:{d}", None)
        offs = c.store.get(f"inbed_offsets:{pid}") or {}
        if offs.pop(d, None) is not None:
            c.store.put(f"inbed_offsets:{pid}", offs)
        _drop_caches(c, pid)
        return {"ok": True}

    @r.get("/people/{pid}/compare")
    async def compare(pid: str, date: str | None = None):
        from .engine.compare import compare_night
        p = person(pid)
        return compare_night(ctl(), p, day(date).isoformat())

    return r


def _drop_caches(c, pid: str) -> None:
    """Autopilot's learned nights and normal range are cached per hour: a correction should count at once."""
    ap = c.autopilot
    for k in ("_nights_cache", "_base_cache"):
        ap.__dict__.get(k, {}).pop(pid, None)
