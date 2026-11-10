"""The bed's control unit ("hub"): health for the owner, and the few things an owner may do to it.

Everything here is owner-only (auth.OWNER_ONLY). Sleepers and viewers see the short status line that already rides
in every snapshot (system.hub); the network name and address stay in this owner-only read.
Restart and water fill need an explicit `confirm: true`, are refused while any side's night is running unless
`force: true`, and a restart can only be asked for once every 5 minutes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .drivers.base import Cap, CommandError

RESTART_GAP_S = 300


class LightBody(BaseModel):
    brightness: int | None = Field(None, ge=0, le=100)
    rgb: list[int] | None = Field(None, min_length=3, max_length=3)


class FanBody(BaseModel):
    quiet: bool


class ActBody(BaseModel):
    confirm: bool = False
    force: bool = False


def build_hub_router(state) -> APIRouter:
    r = APIRouter(prefix="/api/hub")

    def ctl():
        return state.controller

    def need(cap: Cap) -> None:
        if cap not in ctl().driver.capabilities:
            raise HTTPException(409, "This bed's hub can't do that.")

    def log(what: str, **kw) -> None:
        c = ctl()
        c.store.event(c.clock.now_ts(), "hub_action", None, "studio", action=what, **kw)

    async def send(coro):
        try:
            return await coro
        except CommandError as e:
            raise HTTPException(400, str(e))

    def check_safe(b: ActBody, what: str) -> None:
        if not b.confirm:
            raise HTTPException(400, f"Please confirm before {what}.")
        sides = ctl().running_sides()
        if sides and not b.force:
            raise HTTPException(409, f"A night is running, so this waits. Do it once everyone is up, or choose to do it anyway.")

    @r.get("")
    async def get_hub():
        d = ctl().hub_detail()
        return {"available": d is not None, "capabilities": sorted(c.value for c in ctl().driver.capabilities), **(d or {})}

    @r.put("/light")
    async def light(b: LightBody):
        need(Cap.LED_DIM)
        if b.brightness is None and b.rgb is None:
            raise HTTPException(422, "Give a brightness or a colour.")
        rgb = None
        if b.rgb is not None:
            if not all(0 <= v <= 255 for v in b.rgb):
                raise HTTPException(422, "Colour values are 0 to 255.")
            need(Cap.LED_RGB)
            rgb = (b.rgb[0], b.rgb[1], b.rgb[2])
        await send(ctl().driver.set_light(brightness=b.brightness, rgb=rgb))
        log("light", brightness=b.brightness, rgb=b.rgb)
        return {"ok": True}

    @r.put("/fan")
    async def fan(b: FanBody):
        need(Cap.FAN_QUIET)
        await send(ctl().driver.set_quiet_fan(b.quiet))
        log("quiet_fan", on=b.quiet)
        return {"ok": True}

    @r.post("/restart")
    async def restart(b: ActBody):
        need(Cap.REBOOT)
        check_safe(b, "restarting the hub")
        c = ctl()
        now = c.clock.now_ts()
        last = c.__dict__.get("_hub_restart_at")
        if last is not None and 0 <= now - last < RESTART_GAP_S:
            wait = int(RESTART_GAP_S - (now - last)) + 1
            raise HTTPException(429, f"The hub was restarted a moment ago. Try again in {wait // 60 + 1} min.")
        await send(c.driver.restart())
        c.__dict__["_hub_restart_at"] = now
        c.note_restart()                      # targets go back out as soon as it reports again
        log("restart", forced=bool(c.running_sides()))
        return {"ok": True, "message": "The hub restarts and is back in about a minute. Temperatures resume afterwards."}

    @r.post("/fill")
    async def fill(b: ActBody):
        need(Cap.WATER_FILL)
        check_safe(b, "adding water")
        await send(ctl().driver.start_water_fill())
        log("water_fill", forced=bool(ctl().running_sides()))
        return {"ok": True}

    return r
