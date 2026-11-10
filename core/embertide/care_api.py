"""Care and maintenance reminders (see care.py).

Who can do what (auth.authorize):
- Read the list: any signed-in account, including view-only (it holds no personal data).
- Mark a task done, or snooze it: the owner and sleepers. A sleeper can already work household-level things like
  swapping sides or a guest's side, and "I just added the tablets" is the same kind of thing. View-only accounts can't.
- Change an interval, turn a task on or off, set "last done", add or delete your own tasks: owner only.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import care as carelib


class DoneBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: str | None = Field(None, max_length=10)          # YYYY-MM-DD; default today


class SnoozeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(carelib.SNOOZE_DAYS, ge=1, le=carelib.MAX_SNOOZE)


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_days: int | None = Field(None, ge=carelib.MIN_INTERVAL, le=carelib.MAX_INTERVAL)
    enabled: StrictBool | None = None
    last_done: str | None = Field(None, max_length=10)   # "I last did this on..."; "" clears it
    title: str | None = Field(None, max_length=60)
    why: str | None = Field(None, max_length=200)
    steps: list[str] | None = Field(None, max_length=10)
    supplies: str | None = Field(None, max_length=120)


class AddBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=60)
    interval_days: int = Field(..., ge=carelib.MIN_INTERVAL, le=carelib.MAX_INTERVAL)
    why: str = Field("", max_length=200)
    steps: list[str] = Field(default_factory=list, max_length=10)
    supplies: str | None = Field(None, max_length=120)
    last_done: str | None = Field(None, max_length=10)


def build_care_router(state) -> APIRouter:
    r = APIRouter(prefix="/api/care")

    def care() -> carelib.Care:
        return carelib.of(state.controller)

    def run(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except KeyError:
            raise HTTPException(404, "There's no such task.")
        except carelib.CareError as e:
            raise HTTPException(422, str(e))

    @r.get("")
    async def listing():
        return care().listing()

    @r.post("")
    async def add(b: AddBody):
        return run(care().add, b.title, b.interval_days, b.why, b.steps, b.supplies, b.last_done or None)

    @r.put("/{tid}")
    async def edit(tid: str, b: EditBody):
        fields = b.model_dump(exclude_unset=True)
        if fields.get("last_done") == "":
            fields["last_done"] = None
        fields = {k: v for k, v in fields.items() if v is not None or k == "last_done"}
        return run(care().edit, tid, fields)

    @r.delete("/{tid}")
    async def delete(tid: str):
        run(care().delete, tid)
        return {"ok": True}

    @r.post("/{tid}/done")
    async def done(tid: str, b: DoneBody | None = None):
        return run(care().done, tid, (b.on if b else None) or None)

    @r.post("/{tid}/snooze")
    async def snooze(tid: str, b: SnoozeBody | None = None):
        return run(care().snooze, tid, b.days if b else carelib.SNOOZE_DAYS)

    return r
