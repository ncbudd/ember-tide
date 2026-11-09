"""Sign-in endpoints and the middleware that guards every /api route."""

from __future__ import annotations

import json
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .auth import COOKIE, PUBLIC, PUBLIC_PATTERNS, SESSION_DAYS, Auth, Principal, authorize


class SetupBody(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=1, max_length=200)
    person: str | None = None


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class AcceptBody(BaseModel):
    code: str = Field(min_length=10, max_length=100)
    username: str = Field("", max_length=32)            # not needed for a password-reset link
    password: str = Field(min_length=1, max_length=200)


class PasswordBody(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(min_length=1, max_length=200)


class InviteBody(BaseModel):
    person: str | None = None
    role: Literal["owner", "sleeper", "viewer"] = "sleeper"


class UserPatch(BaseModel):
    role: Literal["owner", "sleeper", "viewer"] | None = None
    person: str | None = None
    set_person: bool = False


class TokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    role: Literal["owner", "sleeper", "viewer"] = "owner"
    person: str | None = None


_HASH_SLOTS = None


async def _hashing(fn, *args):
    """Password hashing (scrypt, ~0.1 s each) runs on a worker thread, two at a time: a flood of sign-in attempts
    can't stall the event loop that runs the bed, the live feed and everything else."""
    import asyncio
    global _HASH_SLOTS
    if _HASH_SLOTS is None:
        _HASH_SLOTS = asyncio.Semaphore(2)
    async with _HASH_SLOTS:
        return await asyncio.to_thread(fn, *args)


def client_ip(req_headers: dict[str, str], peer: str | None, trust_proxy: bool) -> str:
    if trust_proxy:
        for h in ("cf-connecting-ip", "x-real-ip"):
            if req_headers.get(h):
                return req_headers[h]
        if req_headers.get("x-forwarded-for"):
            return req_headers["x-forwarded-for"].split(",")[0].strip()
    return peer or "?"


def _is_home(addr: str) -> bool:
    import ipaddress
    try:
        a = ipaddress.ip_address(addr)
    except ValueError:
        return addr in ("testclient", "?")          # test client / unknown peer in tests
    return a.is_private or a.is_loopback or a.is_link_local


def principal_of(request: Request) -> Principal:
    p = request.scope.get("state", {}).get("principal")
    if p is None:
        raise HTTPException(401, "Please sign in.")
    return p


def build_auth_router(state) -> APIRouter:
    r = APIRouter(prefix="/api/auth")

    def auth() -> Auth:
        return state.auth

    def people_ids() -> list[str]:
        return [p.id for p in state.controller.household.people]

    def set_cookie(resp: Response, request: Request, tok: str) -> None:
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(COOKIE, tok, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure, path="/")

    def ip(request: Request) -> str:
        return client_ip({k.lower(): v for k, v in request.headers.items()}, request.client.host if request.client else None, state.cfg.trust_proxy)

    def me_view(p: Principal | None) -> dict | None:
        if p is None:
            return None
        return p.public()

    @r.get("/state")
    async def get_state(request: Request):
        a = auth()
        p = a.principal({k.lower(): v for k, v in request.headers.items()})
        return {"enabled": a.enabled, "setup_needed": a.setup_needed(), "me": me_view(p),
                "people": [{"id": x.id, "name": x.name, "taken": any(u["person"] == x.id for u in a.users())}
                           for x in state.controller.household.people] if a.setup_needed() else None}

    @r.post("/setup")
    async def setup(b: SetupBody, request: Request, response: Response):
        a = auth()
        if not a.setup_needed():
            raise HTTPException(409, "This home is already set up. Sign in instead.")
        if not _is_home(ip(request)):
            # until there's an owner, a stranger who finds the public address could claim it
            raise HTTPException(403, "Finish setup from your home network (open the server's local address), then use this address.")
        if b.person is not None and b.person not in people_ids():
            raise HTTPException(422, "unknown person")
        try:
            u = await _hashing(a.create_user, b.username.strip(), b.password, "owner", b.person)
        except ValueError as e:
            raise HTTPException(422, str(e))
        tok = a.new_session(u["id"], request.headers.get("user-agent"), ip(request))
        set_cookie(response, request, tok)
        app_token = tok if request.headers.get("x-embertide-client") == "app" else None
        state.controller.store.event(time.time(), "account_created", b.person, "auth", username=u["username"], role="owner")
        return {"ok": True, "me": {"kind": "user", "id": u["id"], "name": u["username"], "role": u["role"], "person": u["person"]},
                **({"token": app_token} if app_token else {})}

    @r.post("/login")
    async def login(b: LoginBody, request: Request, response: Response):
        a = auth()
        now = time.time()
        # throttles: this name from this address (5 / 5 min), this address (20 / 10 min), and this name from anywhere
        # (40 / hour, so guessing from many addresses stays slow). A stranger can't lock the owner out quickly: their
        # own tries count against their own address first.
        user = b.username.lower()
        who, where, name = f"u:{user}|{ip(request)}", f"ip:{ip(request)}", f"n:{user}"
        if (a.throttled(who, now, 5, 300) or a.throttled(where, now, 20, 600) or a.throttled(name, now, 40, 3600)
                or a.throttled("all", now, 60, 60)):                 # everyone together: a botnet can't keep the server busy
            raise HTTPException(429, "Too many tries. Wait a few minutes and try again.")
        u = await _hashing(a.verify, b.username.strip(), b.password)
        if not u:
            a.failed(who, now)
            a.failed(where, now)
            a.failed(name, now)
            a.failed("all", now)
            raise HTTPException(401, "That username and password don't match.")
        tok = a.new_session(u["id"], request.headers.get("user-agent"), ip(request))
        set_cookie(response, request, tok)
        app_token = tok if request.headers.get("x-embertide-client") == "app" else None
        return {"ok": True, "me": {"kind": "user", "id": u["id"], "name": u["username"], "role": u["role"], "person": u["person"]},
                **({"token": app_token} if app_token else {})}

    @r.post("/logout")
    async def logout(request: Request, response: Response):
        p = request.scope.get("state", {}).get("principal")
        if p is not None and p.session:
            auth().end_session(p.session)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @r.get("/invite")
    async def check_invite(code: str):
        inv = auth().invite(code)
        if not inv:
            raise HTTPException(404, "This invite link has expired or was already used. Ask for a new one.")
        name = next((x.name for x in state.controller.household.people if x.id == inv["person"]), None)
        return {**inv, "person_name": name}

    @r.post("/accept")
    async def accept(b: AcceptBody, request: Request, response: Response):
        a = auth()
        now = time.time()
        if a.throttled(f"ip:{ip(request)}", now, 20, 600):
            raise HTTPException(429, "Too many tries. Wait a few minutes and try again.")
        try:
            u = await _hashing(a.accept_invite, b.code, b.username.strip(), b.password)
        except ValueError as e:
            a.failed(f"ip:{ip(request)}", now)
            raise HTTPException(422, str(e))
        tok = a.new_session(u["id"], request.headers.get("user-agent"), ip(request))
        set_cookie(response, request, tok)
        app_token = tok if request.headers.get("x-embertide-client") == "app" else None
        state.controller.store.event(now, "account_created", u["person"], "auth", username=u["username"], role=u["role"])
        return {"ok": True, "me": {"kind": "user", "id": u["id"], "name": u["username"], "role": u["role"], "person": u["person"]},
                **({"token": app_token} if app_token else {})}

    # ---- my account ----
    @r.post("/me/password")
    async def change_password(b: PasswordBody, request: Request):
        p = principal_of(request)
        if p.kind != "user":
            raise HTTPException(400, "Only accounts have passwords.")
        try:
            await _hashing(auth().set_password, p.id, b.current, b.new)
        except ValueError as e:
            raise HTTPException(422, str(e))
        n = auth().end_other_sessions(p.id, p.session)
        return {"ok": True, "signed_out_elsewhere": n}

    @r.get("/me/sessions")
    async def my_sessions(request: Request):
        p = principal_of(request)
        if p.kind != "user":
            return []
        cur = (p.session or "")[:12]
        return [{**s, "current": s["id"] == cur} for s in auth().sessions(p.id)]

    @r.delete("/me/sessions/{sid}")
    async def end_my_session(sid: str, request: Request):
        p = principal_of(request)
        return {"ok": bool(auth().end_session_by_prefix(p.id, sid))}

    @r.post("/me/sessions/others")
    async def end_others(request: Request):
        p = principal_of(request)
        return {"ok": True, "ended": auth().end_other_sessions(p.id, p.session)}

    # ---- owner: people & access ----
    @r.get("/users")
    async def list_users():
        return auth().users()

    @r.patch("/users/{uid}")
    async def patch_user(uid: str, b: UserPatch):
        if not auth().user(uid):
            raise HTTPException(404, "unknown user")
        if b.set_person and b.person is not None and b.person not in people_ids():
            raise HTTPException(422, "unknown person")
        try:
            auth().update_user(uid, role=b.role, **({"person": b.person} if b.set_person else {}))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return auth().user(uid)

    @r.post("/users/{uid}/reset")
    async def reset_link(uid: str, request: Request):
        try:
            code = auth().new_reset(uid, principal_of(request).id)
        except ValueError:
            raise HTTPException(404, "unknown user")
        return {"code": code, "path": f"/join#{code}"}

    @r.delete("/users/{uid}")
    async def delete_user(uid: str, request: Request):
        if uid == principal_of(request).id:
            raise HTTPException(422, "You can't remove your own account here.")
        try:
            auth().delete_user(uid)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"ok": True}

    @r.get("/invites")
    async def list_invites():
        return auth().invites()

    @r.post("/invites")
    async def make_invite(b: InviteBody, request: Request):
        if b.person is not None and b.person not in people_ids():
            raise HTTPException(422, "unknown person")
        code = auth().new_invite(b.person, b.role, principal_of(request).id)
        return {"code": code, "path": f"/join#{code}"}

    @r.delete("/invites/{iid}")
    async def cancel_invite(iid: str):
        return {"ok": bool(auth().cancel_invite(iid))}

    @r.get("/tokens")
    async def list_tokens():
        return auth().tokens()

    @r.post("/tokens")
    async def make_token(b: TokenBody, request: Request):
        if b.person is not None and b.person not in people_ids():
            raise HTTPException(422, "unknown person")
        tok, row = auth().new_token(b.name, b.role, b.person, principal_of(request).id)
        return {"token": tok, **row}

    @r.delete("/tokens/{tid}")
    async def revoke_token(tid: str):
        return {"ok": bool(auth().revoke_token(tid))}

    return r


APP_ORIGINS = {"https://localhost", "http://localhost", "capacitor://localhost"}     # the Android / iPhone app's own page


def origin_ok(headers: dict[str, str]) -> bool:
    """A browser always sends Origin on a WebSocket: it must be this server's own address or the native app's page.
    No Origin at all (scripts, the phone's native code with a token) is fine: it can't ride on a browser's cookie."""
    o = headers.get("origin")
    if not o:
        return True
    if o in APP_ORIGINS:
        return True
    from urllib.parse import urlsplit
    try:
        return urlsplit(o).netloc.lower() == (headers.get("host") or "").lower()
    except ValueError:
        return False


class AuthMiddleware:
    """Pure ASGI, so it guards the WebSocket as well as normal requests."""

    def __init__(self, app, state) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not scope["path"].startswith("/api"):
            return await self.app(scope, receive, send)
        a: Auth | None = getattr(self.state, "auth", None)
        if a is None:                                          # still starting up
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if scope["type"] == "websocket" and not origin_ok(headers):
            # a browser on another site (even a sibling subdomain, whose cookies are sent) can't open the live feed
            return await self._deny(scope, receive, send, 403, "Not from this app.")
        if scope["type"] == "websocket" and "authorization" not in headers:
            from urllib.parse import parse_qs
            qs = parse_qs(scope.get("query_string", b"").decode())
            if qs.get("token"):
                headers["authorization"] = "Bearer " + qs["token"][0]
        method = scope.get("method", "GET") if scope["type"] == "http" else "GET"
        path = scope["path"].rstrip("/") or "/"
        p = a.principal(headers)
        scope.setdefault("state", {})["principal"] = p
        if ((method, path) in PUBLIC or path.startswith("/api/notify/act/")      # single-use action codes are their own key
                or any(m == method and rx.match(path) for m, rx in PUBLIC_PATTERNS)):
            return await self.app(scope, receive, send)
        if p is None:
            return await self._deny(scope, receive, send, 401, "Please sign in.")
        if a.enabled and p.kind == "user" and method not in ("GET", "HEAD", "OPTIONS") and headers.get("x-embertide") != "1":
            return await self._deny(scope, receive, send, 403, "Missing X-Embertide header.")

        def shares(pid: str) -> tuple[bool, bool]:
            try:
                s = self.state.controller.person(pid).settings
            except KeyError:
                return True, True                              # unknown person: let the route answer 404
            return s.partner_can_flash, s.partner_can_adjust
        why = authorize(p, method, path, shares)
        if why:
            return await self._deny(scope, receive, send, 403, why)
        return await self.app(scope, receive, send)

    async def _receive_connect(self, receive):
        try:
            await receive()               # the websocket.connect message, before we refuse (this request's own)
        except Exception:  # noqa: BLE001
            pass

    async def _deny(self, scope, receive, send, code: int, msg: str):
        if scope["type"] == "websocket":
            await self._receive_connect(receive)
            await send({"type": "websocket.close", "code": 4401 if code == 401 else 4403})
            return
        body = json.dumps({"detail": msg}).encode()
        await send({"type": "http.response.start", "status": code,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
