"""Which way this bed names its sides.

Ember & Tide's zones are the bed's own names ("left"/"right" in its firmware). Some beds name them as you lie in
bed; others (this household's, found 2026-11-08) name them from the foot of the bed, so the bed's "left" is the
right side as you lie. Only how sides are SHOWN changes; zones, plans and history never move.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel


class Orientation(BaseModel):
    from_foot: bool


def build_bed_router(state) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/bed-orientation")
    async def get_orientation():
        return {"from_foot": bool(state.controller.store.get("bed_from_foot") or False)}

    @r.put("/bed-orientation")
    async def put_orientation(b: Orientation):
        c = state.controller
        if bool(c.store.get("bed_from_foot") or False) != b.from_foot:
            c.store.event(c.clock.now_ts(), "bed_orientation", None, "app", from_foot=b.from_foot)
        c.store.put("bed_from_foot", b.from_foot)
        return {"from_foot": b.from_foot}

    return r
