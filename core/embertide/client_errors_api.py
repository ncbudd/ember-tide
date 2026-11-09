"""App errors reported by the phone app and Studio (a screen that crashed, an uncaught error), so a problem on
someone's phone shows up in Studio instead of only as a blank screen.

- POST /api/client-error is open (an error on the sign-in screen must still get through), but small: every field
  is length-limited, the server keeps only the newest 50, and each address may send a few per minute.
- No personal data: message, component stack, route path (no query string), app version, screen size, user agent.
- GET /api/client-errors is for the owner (Studio → Help → Troubleshooting); DELETE clears the list.
"""

from __future__ import annotations

import re
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

KEEP = 50
PER_MINUTE = 6                 # per address
PER_HOUR_ALL = 120             # everyone together
PER_HOUR_ONE = 20              # one address can't use up everyone's quota


class ClientError(BaseModel):
    kind: str = Field("render", max_length=20)          # render | error | rejection
    message: str = Field("", max_length=500)
    stack: str = Field("", max_length=4000)
    route: str = Field("", max_length=200)
    version: str = Field("", max_length=40)
    screen: str = Field("", max_length=40)              # e.g. "412x915@2.6"
    agent: str = Field("", max_length=300)
    app: bool = False                                   # inside the Android app


def _clean_route(r: str) -> str:
    return re.split(r"[?#]", r or "", maxsplit=1)[0][:200]


def build_client_errors_router(state) -> APIRouter:
    r = APIRouter(prefix="/api")
    hits: dict[str, list[float]] = {}
    all_hits: list[float] = []

    def store():
        return state.controller.store

    @r.post("/client-error")
    async def report(b: ClientError, request: Request):
        now = time.time()
        from .auth_api import client_ip                # the visitor's address (Cloudflare's header), not the tunnel's
        ip = client_ip({k.lower(): v for k, v in request.headers.items()}, request.client.host if request.client else None,
                       state.cfg.trust_proxy)
        recent = [t for t in hits.get(ip, []) if now - t < 3600]
        all_hits[:] = [t for t in all_hits if now - t < 3600]
        last_min = sum(1 for t in recent if now - t < 60)
        if last_min >= PER_MINUTE or len(recent) >= PER_HOUR_ONE or len(all_hits) >= PER_HOUR_ALL:
            raise HTTPException(429, "Too many error reports; try again later.")
        recent.append(now)
        hits[ip] = recent
        all_hits.append(now)
        if len(hits) > 500:                              # don't let the address table grow without end
            for k in list(hits)[:250]:
                hits.pop(k, None)
        e = b.model_dump()
        e["route"] = _clean_route(e["route"])
        e["ts"] = state.controller.clock.now_ts() if getattr(state, "controller", None) else now
        log = (store().get("client_errors", []) or [])[-(KEEP - 1):] + [e]
        store().put("client_errors", log)
        return {"ok": True}

    @r.get("/client-errors")
    async def recent_errors():
        return list(reversed(store().get("client_errors", []) or []))

    @r.delete("/client-errors")
    async def clear_errors():
        store().put("client_errors", [])
        return {"ok": True}

    return r
