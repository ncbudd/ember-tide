"""Oura cloud connection: the owner's app credentials (Studio → Integrations) and each person's own connection."""

from __future__ import annotations

import html
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .oura import OuraError


class OuraAppBody(BaseModel):
    client_id: str = Field("", max_length=200)
    client_secret: str | None = Field(None, max_length=300)      # empty / None = keep the saved one


class MeBody(BaseModel):
    person: str | None = None                                    # only used when sign-in is off


def _page(title: str, text: str, ok: bool) -> HTMLResponse:
    """The small page Oura sends the browser back to. Plain, readable in light and dark."""
    t, x = html.escape(title), html.escape(text)
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>{t}</title>
<style>:root{{color-scheme:light dark}}body{{margin:0;font:17px/1.5 system-ui,-apple-system,sans-serif;background:#f6f3ee;color:#1d1a17;
display:grid;place-items:center;min-height:100vh;padding:max(24px,env(safe-area-inset-top)) 20px max(24px,env(safe-area-inset-bottom))}}
@media (prefers-color-scheme:dark){{body{{background:#100e12;color:#efe9e1}}a{{background:#efe9e1!important;color:#100e12!important}}}}
main{{max-width:420px;text-align:center}}h1{{font-weight:500;font-size:26px;margin:0 0 8px}}p{{margin:0 0 24px}}
a{{display:inline-block;min-height:48px;line-height:48px;padding:0 28px;border-radius:24px;background:#1d1a17;color:#f6f3ee;text-decoration:none}}</style></head>
<body><main><div style="font-size:40px" aria-hidden="true">{'✓' if ok else '!'}</div><h1>{t}</h1><p>{x}</p>
<a href="/">Back to Ember &amp; Tide</a></main></body></html>""", status_code=200 if ok else 400)


def build_oura_router(state) -> APIRouter:
    r = APIRouter(prefix="/api")

    def o():
        return state.oura

    def redirect_uri(request: Request) -> str:
        pub = state.notifier.public_url if getattr(state, "notifier", None) else None
        base = pub or str(request.base_url).rstrip("/")
        return f"{base}/api/oura/callback"

    def me(request: Request, body_person: str | None) -> str:
        p = request.scope.get("state", {}).get("principal")
        pid = p.person if p is not None and p.kind != "open" else body_person
        if not pid or pid not in [x.id for x in state.controller.household.people]:
            raise HTTPException(403, "This account isn't linked to a side of the bed.")
        return pid

    # ---------- owner: the Oura app's credentials ----------
    @r.get("/integrations/oura")
    async def get_app(request: Request):
        a = o().app
        return {"configured": o().configured, "client_id": a.get("client_id", ""), "secret_saved": bool(a.get("client_secret")),
                "redirect_uri": redirect_uri(request),
                "people": {p.id: o().status(p.id)["connected"] for p in state.controller.household.people}}

    @r.put("/integrations/oura")
    async def put_app(b: OuraAppBody, request: Request):
        cid = b.client_id.strip()
        if cid and not re.fullmatch(r"[A-Za-z0-9_\-.]{4,200}", cid):
            raise HTTPException(422, "That doesn't look like an Oura client id.")
        if cid and not (b.client_secret or o().app.get("client_secret")):
            raise HTTPException(422, "Add the client secret too.")
        o().save_app(cid, b.client_secret)
        return await get_app(request)

    @r.get("/oura/status")
    async def household_status():
        """For Studio's device catalog: is the Oura connection set up, and is anyone's ring connected."""
        people = {p.id: o().status(p.id)["connected"] for p in state.controller.household.people}
        return {"configured": o().configured, "connected": any(people.values()), "people": people}

    # ---------- each person: their own ring ----------
    @r.get("/oura/me")
    async def status(request: Request, person: str | None = None):
        return o().status(me(request, person))

    @r.post("/oura/me/connect")
    async def connect(b: MeBody, request: Request):
        pid = me(request, b.person)
        try:
            return {"url": o().authorize_url(pid, redirect_uri(request))}
        except OuraError as e:
            raise HTTPException(409, str(e))

    @r.post("/oura/me/sync")
    async def sync(b: MeBody, request: Request):
        pid = me(request, b.person)
        if not o().status(pid)["connected"]:
            raise HTTPException(409, "Oura isn't connected.")
        try:
            return await o().sync(pid)
        except OuraError as e:
            raise HTTPException(429 if e.status == 429 else 502, str(e))

    @r.post("/oura/me/disconnect")
    async def disconnect(b: MeBody, request: Request):
        await o().disconnect(me(request, b.person))
        return {"ok": True}

    @r.get("/oura/callback")
    async def callback(request: Request, code: str | None = None, error: str | None = None):
        st = o().take_state(request.query_params.get("state") or "")      # single-use, whatever happens next
        if error:
            return _page("Oura wasn't connected", "Nothing was shared. You can try again from Settings whenever you like.", False)
        if not st or not code or len(code) > 2000:
            return _page("That link has expired", "Start again from Settings → Watch or ring → Connect Oura.", False)
        try:
            await o().finish(code, st)
        except OuraError:
            return _page("Oura wasn't connected", "Oura didn't accept the sign-in. Try again from Settings in a minute.", False)
        import asyncio
        task = asyncio.get_running_loop().create_task(_first_sync(o(), st["pid"]))
        state.__dict__.setdefault("_bg", set()).add(task)
        task.add_done_callback(state._bg.discard)
        return _page("Oura is connected", "Your ring's nights will show up within the hour. You can close this and go back to the app.", True)

    return r


async def _first_sync(oura, pid: str) -> None:
    try:
        await oura.sync(pid)
    except Exception:  # noqa: BLE001  (it retries on the hourly poll)
        pass
