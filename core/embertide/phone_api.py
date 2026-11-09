"""The Android app's "From this phone" reports: POST /api/people/{pid}/phone-context.

Only the person themselves (or the owner) may send or read it (the usual person-path rules in auth.authorize).
Values are validated and the send rate is limited; times are unix seconds. No titles or coordinates are accepted.
"""

from __future__ import annotations

import math
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .engine import phone_context as PC

SOURCES = ("location", "wifi", "calendar", "alarm", "charging", "watch", "dial", "watch_hr")
MIN_GAP_S = 5            # per person: the app reports on changes and a slow heartbeat; anything faster is a bug


class DialSighting(BaseModel):
    minor: int = Field(ge=1, le=65535)                 # which dial (its iBeacon minor)
    rssi: float = Field(ge=-120, le=0)                 # median signal strength, dBm
    seen_at: float | None = None


class BeaconMap(BaseModel):
    minor: int = Field(ge=1, le=65535)
    side: str = Field(pattern="^(left|right)$")
    name: str = Field("", max_length=40)


class PhoneContextBody(BaseModel):
    home: bool | None = None
    distance_km: float | None = Field(None, ge=0, le=20000)
    heading_home: bool | None = None
    eta_min: float | None = Field(None, ge=0, le=24 * 60)
    wifi_home: bool | None = None
    next_alarm: float | None = None
    evening_event_end: float | None = None
    early_start: float | None = None
    charging: bool | None = None
    bedtime_mode: bool | None = None
    watch_connected: bool | None = None
    dials: list[DialSighting] | None = Field(None, max_length=8)      # bedside dials heard (strongest first)
    device: str | None = Field(None, max_length=60)
    sharing: list[str] | None = Field(None, max_length=12)          # which switches are on in the app
    ts: float | None = None                                           # phone's clock, for information only

    @field_validator("distance_km", "eta_min", "next_alarm", "evening_event_end", "early_start", "ts")
    @classmethod
    def _finite(cls, v):
        if v is not None and not math.isfinite(v):
            raise ValueError("must be a number")
        return v

    @field_validator("sharing")
    @classmethod
    def _known(cls, v):
        return None if v is None else [s for s in v if s in SOURCES]


def build_phone_router(state) -> APIRouter:
    r = APIRouter(prefix="/api")
    last_post: dict[str, float] = {}

    def ctl():
        return state.controller

    def person(pid: str):
        p = next((x for x in ctl().household.people if x.id == pid), None)
        if not p:
            raise HTTPException(404, "unknown person")
        return p

    @r.post("/people/{pid}/phone-context")
    async def post_phone_context(pid: str, b: PhoneContextBody):
        person(pid)
        now = ctl().clock.now_ts()
        mono = time.monotonic()
        if mono - last_post.get(pid, -1e9) < MIN_GAP_S:
            raise HTTPException(429, "too many updates; the app sends on changes and every few minutes")
        for k in ("next_alarm", "evening_event_end", "early_start"):
            v = getattr(b, k)
            if v is not None and v != 0 and not (now - 3600 <= v <= now + 3 * 86400):
                raise HTTPException(422, f"{k} must be within the next three days (or 0 for none)")
        last_post[pid] = mono
        fields = {k: v for k, v in b.model_dump(exclude={"device", "sharing", "ts"}).items() if v is not None}
        if b.dials is not None:
            fields["dials"] = [{**d.model_dump(), "seen_at": d.seen_at or now} for d in b.dials]
        # "nothing tonight" is a real answer from the calendar / alarm: an explicit 0 clears it
        for k in ("next_alarm", "evening_event_end", "early_start"):
            if getattr(b, k) == 0:
                fields[k] = None
        if b.home is True:                              # back home: the trip's distance and arrival time are over
            for k in ("distance_km", "heading_home", "eta_min"):
                fields.setdefault(k, None)
        cur = PC.update(ctl().store, pid, fields, now, b.device, b.sharing)
        out = {"ok": True, "seen": cur["seen"]}
        if b.sharing and "watch_hr" in b.sharing:            # tells the phone whether the watch should collect heart rate now
            from .engine.watch import wanted
            out["watch_hr_wanted"] = wanted(ctl(), person(pid), now)
        return out

    @r.get("/people/{pid}/phone-context")
    async def get_phone_context(pid: str):
        person(pid)
        return PC.latest(ctl().store, pid) or {}

    @r.delete("/people/{pid}/phone-context")
    async def delete_phone_context(pid: str):
        person(pid)
        PC.forget(ctl().store, pid)
        return {"ok": True}

    @r.get("/dial-beacons")
    async def get_beacons():
        """Which bedside dial is which (their Bluetooth beacon number → side), for the app's scan."""
        return {"uuid": PC.DIAL_UUID, "major": 1, "dials": PC.dial_beacons(ctl().store)}

    @r.put("/dial-beacons")
    async def put_beacons(b: list[BeaconMap]):
        if len(b) > 8 or len({x.minor for x in b}) != len(b):
            raise HTTPException(422, "up to 8 dials, each with its own number")
        ctl().store.put("dial_beacons", [x.model_dump() for x in b])
        return {"uuid": PC.DIAL_UUID, "major": 1, "dials": PC.dial_beacons(ctl().store)}

    @r.get("/phone-context")
    async def all_phone_context():
        """Studio: every person's phone (what it shares, when it last reported)."""
        out = []
        for p in ctl().household.people:
            cur = PC.latest(ctl().store, p.id)
            if cur:
                out.append({"person": p.id, "name": p.name, "device": cur.get("device"), "sharing": cur.get("sharing") or [],
                            "seen": cur.get("seen")})                  # what it shares and when, never the values
        return out

    return r
