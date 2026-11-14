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


class DeviceTokenBody(BaseModel):
    label: str = Field("", max_length=60)
    scope: Literal["watch", "phone"]


class McpTokenBody(BaseModel):
    label: str = Field("", max_length=60)
    scope: Literal["read", "change"] = "read"


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


# headers a reverse proxy or tunnel adds: their presence means the peer address is the proxy's, not the visitor's
PROXY_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "x-real-ip", "forwarded")


def _ip(v: str | None) -> str | None:
    """A bare address from one hop of a forwarding header (quotes, [brackets] and :port removed), or None if it isn't one."""
    import ipaddress
    if not v:
        return None
    v = v.strip().strip('"')
    if v.startswith("["):
        v = v[1:].split("]")[0]
    elif v.count(":") == 1:
        v = v.rsplit(":", 1)[0]                     # 1.2.3.4:5678
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        return None


def _forwarded_hops(v: str) -> list[str | None]:
    """The `for=` of each element of an RFC 7239 Forwarded header, in order (None where an element has none we can read)."""
    hops: list[str | None] = []
    for el in v.split(","):
        got = None
        for part in el.split(";"):
            k, _, val = part.strip().partition("=")
            if k.lower() == "for":
                got = _ip(val)
        hops.append(got)
    return hops


def parse_nets(spec: str | None) -> tuple:
    """ET_TRUSTED_PROXIES: comma-separated addresses or CIDRs ("172.18.0.0/16, 10.0.0.5") -> network objects.
    Empty = the default (any private / loopback / link-local peer). Raises ValueError naming the bad entry."""
    import ipaddress
    out = []
    for part in (spec or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            raise ValueError(f"ET_TRUSTED_PROXIES: {part!r} isn't an address or a CIDR range like 172.18.0.0/16") from None
    return tuple(out)


def is_trusted_peer(addr: str, nets: tuple = ()) -> bool:
    """Is this connection from a proxy we believe? With ET_TRUSTED_PROXIES: only those networks; otherwise any
    private / loopback address (the default)."""
    import ipaddress
    if not nets:
        return _is_home(addr)
    try:
        a = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(a in n for n in nets if n.version == a.version)


def _rightmost_untrusted(hops: list[str | None], nets: tuple = ()) -> str | None:
    """Walk a forwarding chain from the right (the hop our own proxy added) past our own proxies (private / loopback
    addresses) to the first address that isn't ours: that one was seen by a proxy we trust; anything left of it is
    whatever the visitor chose to send. A hop we can't read stops the walk (nothing beyond it can be believed)."""
    for h in reversed(hops):
        if h is None:
            return None
        if not is_trusted_peer(h, nets):
            return h
    return None                                     # all private: nothing outside to name (see from_home)


def _is_home(addr: str) -> bool:
    import ipaddress
    try:
        a = ipaddress.ip_address(addr)
    except ValueError:
        return addr in ("testclient", "?")          # test client / unknown peer in tests
    return a.is_private or a.is_loopback or a.is_link_local


def client_ip(req_headers: dict[str, str], peer: str | None, trust_proxy: bool, nets: tuple = (), cloudflare: bool = False) -> str:
    """The visitor's address, for per-address limits and the sign-in list.

    Forwarding headers count only with ET_TRUST_PROXY=on and only when the connection itself comes from a private or
    loopback address (plausibly the tunnel / proxy): straight from the internet they are the visitor's own words.
    Cloudflare's CF-Connecting-IP counts only when ET_PROXY_CLOUDFLARE=on (behind a non-Cloudflare proxy a client can
    forge it); otherwise the right-most address that isn't one of ours in X-Forwarded-For wins, then Forwarded, and
    X-Real-IP last (many proxies pass that one through untouched).

    ET_TRUSTED_PROXIES (`nets`) narrows "plausibly the proxy" from every private address to the listed networks."""
    if trust_proxy and is_trusted_peer(peer or "?", nets):
        if cloudflare and (cf := _ip(req_headers.get("cf-connecting-ip"))):
            return cf
        if req_headers.get("x-forwarded-for") and (f := _rightmost_untrusted([_ip(x) for x in req_headers["x-forwarded-for"].split(",")], nets)):
            return f
        if req_headers.get("forwarded") and (f := _rightmost_untrusted(_forwarded_hops(req_headers["forwarded"]), nets)):
            return f
        if (xr := _ip(req_headers.get("x-real-ip"))) and not _is_home(xr):
            return xr
    return peer or "?"


def peer_ip(req_headers: dict[str, str], peer: str | None, cfg) -> str:
    """Resolve the visitor's address using the server's proxy trust settings."""
    return client_ip(req_headers, peer, cfg.trust_proxy, parse_nets(cfg.trusted_proxies), getattr(cfg, "proxy_cloudflare", False))


NOT_KNOWN_HERE = ("Your sign-in worked, but there is no Ember & Tide account for you. Ask the owner to add you "
                  "(Studio, People) with the same username.")


def header_identity(a: Auth, headers: dict[str, str], peer: str | None, cfg):
    """Forward-auth (ET_AUTH_HEADER): the proxy in front has already signed the visitor in and names them in a header.

    Believed only when ET_TRUST_PROXY is on, ET_TRUSTED_PROXIES names the proxy, and the connection comes from that
    proxy: anywhere else the header is the visitor's own words and is ignored. Returns None (nothing to go on), a
    Principal (an existing account), or a str (the name of someone who has no account here; never created automatically)."""
    names = [h.lower() for h in (cfg.auth_header, cfg.auth_header_email) if h]
    # Never fall back to "any private peer" for forward-auth: an untrusted LAN/Docker container could spoof the header.
    if not names or not cfg.trust_proxy or not (cfg.trusted_proxies or "").strip() or not a.enabled:
        return None
    if not is_trusted_peer(peer or "?", parse_nets(cfg.trusted_proxies)):
        return None
    for n in names:
        v = (headers.get(n) or "").strip()
        if not v or len(v) > 200 or any(ord(ch) < 32 for ch in v):
            continue
        return a.user_principal(v) or v
    return None


def via_untrusted_proxy(req_headers: dict[str, str], trust_proxy: bool) -> bool:
    """Came through a tunnel / reverse proxy that ET_TRUST_PROXY doesn't vouch for: the peer address is the proxy's
    (often a private one on the same machine or Docker network) and says nothing about where the visitor is."""
    return not trust_proxy and any(req_headers.get(h) for h in PROXY_HEADERS)


def from_home(req_headers: dict[str, str], peer: str | None, trust_proxy: bool) -> bool:
    """Is this request from the home network (first-time setup, and never slowed by the server-wide sign-in limit)?

    Only a direct connection from a private address with no forwarding headers at all. Anything relayed by a tunnel or
    proxy is treated as outside, trusted or not, even when a header claims a home address: a visitor can write any
    address into X-Forwarded-For / Forwarded / X-Real-IP, and Cloudflare passes those through. `trust_proxy` only
    decides whose address the limits count (client_ip)."""
    if any(req_headers.get(h) for h in PROXY_HEADERS):
        return False
    return _is_home(peer or "?")


SETUP_VIA_PROXY = ("First-time setup has to be done from your home network. On a device at home, open the server's "
                   "local address (like http://192.168.1.20:8790) and create the owner account there, then use this "
                   "address.")


_SLOWED = 0
SLOW_S = 2.0              # the wait for each sign-in from outside while the whole server sees many failures
SLOW_MAX = 20             # slowed tries waiting at once; past that they're refused


async def _slow_down() -> None:
    import asyncio
    global _SLOWED
    if _SLOWED >= SLOW_MAX:
        raise HTTPException(429, "Too many tries. Wait a few minutes and try again.")
    _SLOWED += 1
    try:
        await asyncio.sleep(SLOW_S)
    finally:
        _SLOWED -= 1


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

    def hdrs(request: Request) -> dict[str, str]:
        return {k.lower(): v for k, v in request.headers.items()}

    def ip(request: Request) -> str:
        return peer_ip(hdrs(request), request.client.host if request.client else None, state.cfg)

    def home(request: Request) -> bool:
        return from_home(hdrs(request), request.client.host if request.client else None, state.cfg.trust_proxy)

    def me_view(p: Principal | None) -> dict | None:
        if p is None:
            return None
        return p.public()

    @r.get("/state")
    async def get_state(request: Request):
        a = auth()
        if state.cfg.demo:
            return {"enabled": False, "setup_needed": False, "demo": True,
                    "me": {"kind": "open", "id": "", "name": "Demo", "role": "viewer", "person": None},
                    "proxy_denied": None, "people": None}
        h = {k.lower(): v for k, v in request.headers.items()}
        p = a.principal(h)
        denied = None
        if p is None and not a.setup_needed():
            who = header_identity(a, h, request.client.host if request.client else None, state.cfg)
            if isinstance(who, str):
                denied = NOT_KNOWN_HERE
            else:
                p = who
        return {"enabled": a.enabled, "setup_needed": a.setup_needed(), "demo": False, "me": me_view(p), "proxy_denied": denied,
                "people": [{"id": x.id, "name": x.name, "taken": any(u["person"] == x.id for u in a.users())}
                           for x in state.controller.household.people] if a.setup_needed() else None}

    @r.post("/setup")
    async def setup(b: SetupBody, request: Request, response: Response):
        a = auth()
        if not a.setup_needed():
            raise HTTPException(409, "This home is already set up. Sign in instead.")
        if via_untrusted_proxy(hdrs(request), state.cfg.trust_proxy):
            # through a tunnel the peer is the tunnel's own (private) address: a stranger on the public URL would
            # otherwise look like they're at home and could claim the owner account
            raise HTTPException(403, SETUP_VIA_PROXY)
        if not home(request):
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
        if a.throttled(who, now, 5, 300) or a.throttled(where, now, 20, 600) or a.throttled(name, now, 40, 3600):
            raise HTTPException(429, "Too many tries. Wait a few minutes and try again.")
        # everyone together (60 failures a minute): a botnet can't keep the server busy. It never refuses the home
        # network, and from outside it slows each try down instead of locking everyone out; only a pile-up of
        # slowed tries is refused
        if not home(request) and a.throttled("all", now, 60, 60):
            await _slow_down()
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

    # ---- my device keys (the phone's widget and tile, the watch) ----
    @r.get("/me/device-tokens")
    async def my_device_tokens(request: Request):
        p = principal_of(request)
        if p.kind != "user":
            return []
        return auth().device_tokens(p.id, p.session)

    @r.post("/me/device-tokens")
    async def make_device_token(b: DeviceTokenBody, request: Request):
        p = principal_of(request)
        if p.kind != "user" or not p.session:
            raise HTTPException(403, "Sign in on the phone first.")          # not from an API key or another device key
        if not p.person:
            raise HTTPException(422, "This account has no side of the bed, so there's nothing for a widget or watch to control.")
        tok, row = auth().new_device_token(p.id, p.session, p.person, b.label, b.scope)
        return {"token": tok, **row}

    @r.delete("/me/device-tokens/{tid}")
    async def revoke_my_device_token(tid: str, request: Request):
        p = principal_of(request)
        if p.kind != "user":
            raise HTTPException(403, "Only accounts have device keys.")
        return {"ok": bool(auth().revoke_device_token(tid, p.id))}

    # ---- my MCP keys (AI apps: Claude Desktop, Claude Code, Copilot, Cursor ...) ----
    @r.get("/me/mcp-tokens")
    async def my_mcp_tokens(request: Request):
        p = principal_of(request)
        return auth().mcp_tokens(p.id) if p.kind == "user" else []

    @r.post("/me/mcp-tokens")
    async def make_mcp_token(b: McpTokenBody, request: Request):
        p = principal_of(request)
        if p.kind != "user":
            raise HTTPException(403, "Sign in with your own account to connect an AI app.")   # not from an API key or another key
        if not p.person:
            raise HTTPException(422, "This account has no side of the bed, so an AI app would have nothing to look at.")
        if p.role == "viewer" and b.scope == "change":
            raise HTTPException(403, "This account can only look, so the key can only read.")
        tok, row = auth().new_mcp_token(p.id, p.person, b.label, b.scope)
        return {"token": tok, **row}

    @r.delete("/me/mcp-tokens/{tid}")
    async def revoke_my_mcp_token(tid: str, request: Request):
        p = principal_of(request)
        if p.kind != "user":
            raise HTTPException(403, "Only accounts have AI app keys.")
        return {"ok": bool(auth().revoke_mcp_token(tid, p.id))}

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

    @r.get("/tokens/devices")
    async def list_device_tokens():
        return auth().device_tokens()

    @r.get("/tokens/mcp")
    async def list_mcp_tokens():
        return auth().mcp_tokens()

    @r.delete("/tokens/{tid}")
    async def revoke_token(tid: str):
        return {"ok": bool(auth().revoke_token(tid))}          # an API key or anyone's device key or AI app key

    return r


APP_ORIGINS = {"https://localhost", "http://localhost", "capacitor://localhost"}     # the Android / iPhone app's own page

# Older app builds open the live feed as /api/live?token=<session>, which puts the session in a URL (proxy logs).
# Current builds use ?ticket= (POST /api/live/ticket). Kept only so phones not yet updated keep their live feed; to
# remove: set this to False (or delete it and its use in AuthMiddleware) and drop test_legacy_token_query_still_works.
LEGACY_LIVE_TOKEN_QUERY = True


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
        p = None
        ticketed = False
        if scope["type"] == "websocket" and "authorization" not in headers:
            from urllib.parse import parse_qs
            qs = parse_qs(scope.get("query_string", b"").decode())
            if qs.get("ticket"):
                # the app's way in: a single-use ticket from POST /api/live/ticket (the session never goes in a URL)
                ticketed = True
                p = a.take_ticket(qs["ticket"][0]) if a.enabled else a.principal(headers)
                if p is None and a.enabled:
                    # used, expired or unknown: a distinct close code, so the app fetches a new ticket instead of
                    # treating it as signed out (a session that really ended fails the ticket request with 401)
                    return await self._deny(scope, receive, send, 4400, "That live ticket has expired.")
            elif qs.get("token") and LEGACY_LIVE_TOKEN_QUERY:
                headers["authorization"] = "Bearer " + qs["token"][0]
        method = scope.get("method", "GET") if scope["type"] == "http" else "GET"
        path = scope["path"].rstrip("/") or "/"
        if not ticketed:
            p = a.principal(headers)
        unknown = False
        if p is None and a.enabled and not ticketed:
            who = header_identity(a, headers, (scope.get("client") or (None,))[0], self.state.cfg)
            if isinstance(who, str):
                unknown = True
            else:
                p = who
        scope.setdefault("state", {})["principal"] = p
        if ((method, path) in PUBLIC or path.startswith("/api/notify/act/")      # single-use action codes are their own key
                or any(m == method and rx.match(path) for m, rx in PUBLIC_PATTERNS)):
            return await self.app(scope, receive, send)
        if p is None:
            if unknown:
                return await self._deny(scope, receive, send, 403, NOT_KNOWN_HERE)
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
            await send({"type": "websocket.close", "code": 4401 if code == 401 else 4403 if code == 403 else code})
            return
        body = json.dumps({"detail": msg}).encode()
        await send({"type": "http.response.start", "status": code,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
