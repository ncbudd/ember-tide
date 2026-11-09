"""Cloud accounts API: providers list, owner credentials, each person's own connection, webhooks and JSON pollers."""

from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .cloud import NOT_AVAILABLE, CloudError
from .cloud.generic import MAX_BODY, HookBody, PollBody, public_hook, public_poll
from .oura_api import _page


class AppBody(BaseModel):
    client_id: str = Field("", max_length=200)
    client_secret: str | None = Field(None, max_length=300)      # empty / None = keep the saved one


class MeBody(BaseModel):
    person: str | None = None


def build_cloud_router(state) -> APIRouter:
    r = APIRouter(prefix="/api")

    def cloud():
        return state.cloud

    def prov(pid_: str):
        try:
            return cloud().get(pid_)
        except KeyError:
            raise HTTPException(404, "unknown provider")

    def base(request: Request) -> str:
        pub = state.notifier.public_url if getattr(state, "notifier", None) else None
        return pub or str(request.base_url).rstrip("/")

    def me(request: Request, body_person: str | None) -> str:
        p = request.scope.get("state", {}).get("principal")
        pid = p.person if p is not None and p.kind != "open" else body_person
        if not pid or pid not in [x.id for x in state.controller.household.people]:
            raise HTTPException(403, "This account isn't linked to a side of the bed.")
        return pid

    def people() -> list[str]:
        return [p.id for p in state.controller.household.people]

    # ---------- everyone: what's there ----------
    @r.get("/cloud/providers")
    async def providers(request: Request):
        """Providers with configured/connected status (Studio → Integrations, the Wearables tile, setup)."""
        out = []
        for p in cloud().providers.values():
            out.append({"id": p.id, "name": p.name, "gives": p.gives, "docs_url": p.docs_url, "scopes": p.scopes,
                        "redirect_uri": base(request) + p.redirect_path(), "configured": p.configured,
                        "connected": {pid: p.status(pid)["connected"] for pid in people()}, "available": True})
        out += [{**x, "available": False, "configured": False, "connected": {}} for x in NOT_AVAILABLE]
        return {"providers": out}

    # ---------- owner: credentials ----------
    @r.get("/integrations/cloud/{provider}")
    async def get_app(provider: str, request: Request):
        p = prov(provider)
        a = p.app
        return {"id": p.id, "configured": p.configured, "client_id": a.get("client_id", ""), "secret_saved": bool(a.get("client_secret")),
                "redirect_uri": base(request) + p.redirect_path()}

    @r.put("/integrations/cloud/{provider}")
    async def put_app(provider: str, b: AppBody, request: Request):
        p = prov(provider)
        cid = b.client_id.strip()
        if cid and not re.fullmatch(r"[A-Za-z0-9_\-.]{4,200}", cid):
            raise HTTPException(422, f"That doesn't look like a {p.name} client id.")
        if cid and not (b.client_secret or p.app.get("client_secret")):
            raise HTTPException(422, "Add the client secret too.")
        p.save_app(cid, b.client_secret)
        return await get_app(provider, request)

    # ---------- each person: their own account ----------
    @r.get("/cloud/me/{provider}")
    async def status(provider: str, request: Request, person: str | None = None):
        return prov(provider).status(me(request, person))

    @r.post("/cloud/me/{provider}/connect")
    async def connect(provider: str, b: MeBody, request: Request):
        p = prov(provider)
        try:
            return {"url": p.authorize_url(me(request, b.person), base(request) + p.redirect_path())}
        except CloudError as e:
            raise HTTPException(409, str(e))

    @r.post("/cloud/me/{provider}/sync")
    async def sync(provider: str, b: MeBody, request: Request):
        p = prov(provider)
        pid = me(request, b.person)
        if not p.status(pid)["connected"]:
            raise HTTPException(409, f"{p.name} isn't connected.")
        try:
            return await p.sync(pid)
        except CloudError as e:
            raise HTTPException(429 if e.status == 429 else 502, str(e))

    @r.post("/cloud/me/{provider}/disconnect")
    async def disconnect(provider: str, b: MeBody, request: Request):
        await prov(provider).disconnect(me(request, b.person))
        return {"ok": True}

    @r.get("/cloud/{provider}/callback")
    async def callback(provider: str, request: Request, code: str | None = None, error: str | None = None):
        p = prov(provider)
        st = p.take_state(request.query_params.get("state") or "")
        if error:
            return _page(f"{p.name} wasn't connected", "Nothing was shared. You can try again from Settings whenever you like.", False)
        if not st or not code or len(code) > 2000:
            return _page("That link has expired", "Start again from Settings → Watch or ring.", False)
        try:
            await p.finish(code, st)
        except CloudError:
            return _page(f"{p.name} wasn't connected", f"{p.name} didn't accept the sign-in. Try again from Settings in a minute.", False)
        task = asyncio.get_running_loop().create_task(_quiet(p.sync(st["pid"])))
        state.__dict__.setdefault("_bg", set()).add(task)
        task.add_done_callback(state._bg.discard)
        return _page(f"{p.name} is connected", "New nights will show up within the hour. You can close this and go back to the app.", True)

    # ---------- owner: webhooks and JSON pollers ----------
    @r.get("/integrations/hooks")
    async def list_hooks(request: Request):
        return {"hooks": [public_hook(h, base(request)) for h in cloud().generic.hooks]}

    @r.post("/integrations/hooks")
    async def add_hook(b: HookBody, request: Request):
        if b.person not in people():
            raise HTTPException(422, "unknown person")
        return public_hook(cloud().generic.save_hook(b), base(request))

    @r.put("/integrations/hooks/{hid}")
    async def put_hook(hid: str, b: HookBody, request: Request):
        if b.person not in people():
            raise HTTPException(422, "unknown person")
        try:
            return public_hook(cloud().generic.save_hook(b, hid), base(request))
        except KeyError:
            raise HTTPException(404, "unknown webhook")

    @r.delete("/integrations/hooks/{hid}")
    async def del_hook(hid: str):
        cloud().generic.delete_hook(hid)
        return {"ok": True}

    @r.post("/hooks/{hid}/{secret}")
    async def receive(hid: str, secret: str, request: Request):
        """Public: the secret in the address is the key. JSON body, mapped by the webhook's settings."""
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "too big")
        try:
            body = json.loads(raw or b"null")
        except ValueError:
            raise HTTPException(400, "the body must be JSON")
        try:
            return cloud().generic.receive(hid, secret, body)
        except PermissionError:
            raise HTTPException(404, "unknown webhook")

    @r.get("/integrations/polls")
    async def list_polls():
        return {"polls": [public_poll(s) for s in cloud().generic.polls]}

    @r.post("/integrations/polls")
    async def add_poll(b: PollBody):
        if b.person not in people():
            raise HTTPException(422, "unknown person")
        return public_poll(cloud().generic.save_poll(b))

    @r.put("/integrations/polls/{sid}")
    async def put_poll(sid: str, b: PollBody):
        if b.person not in people():
            raise HTTPException(422, "unknown person")
        try:
            return public_poll(cloud().generic.save_poll(b, sid))
        except KeyError:
            raise HTTPException(404, "unknown source")

    @r.post("/integrations/polls/{sid}/run")
    async def run_poll(sid: str):
        s = next((x for x in cloud().generic.polls if x["id"] == sid), None)
        if not s:
            raise HTTPException(404, "unknown source")
        return await cloud().generic.poll(s)

    @r.delete("/integrations/polls/{sid}")
    async def del_poll(sid: str):
        cloud().generic.delete_poll(sid)
        return {"ok": True}

    return r


async def _quiet(coro) -> None:
    try:
        await coro
    except Exception:  # noqa: BLE001  (retries on the hourly poll)
        pass
