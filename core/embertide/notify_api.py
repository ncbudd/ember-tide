"""Notification setup (owner), the log, and the endpoints behind notification buttons."""

from __future__ import annotations

import re
import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .notify import Message


class ProviderBody(BaseModel):
    kind: Literal["ntfy", "ha", "webhook", "apprise"]
    name: str = Field(min_length=1, max_length=60)
    person: str | None = None             # None = household (bed problems)
    enabled: bool = True
    settings: dict


class NotifySettingsBody(BaseModel):
    public_url: str | None = Field(None, max_length=300)
    push_server: str | None = Field(None, max_length=300)      # the ntfy server the Android app's UnifiedPush uses
    push_token: str | None = Field(None, max_length=200)       # ntfy access token for posting to it, if it needs one
    keep_push_token: bool = True


class PushBody(BaseModel):
    kind: Literal["up", "fcm"] = "up"
    endpoint: str = Field(min_length=10, max_length=400)        # UnifiedPush URL, or the FCM registration token
    p256dh: str = Field(min_length=40, max_length=200)
    auth: str = Field(min_length=10, max_length=60)
    device: str | None = Field(None, max_length=60)
    platform: Literal["android", "ios"] = "android"
    person: str | None = None                                  # only used when sign-in is off


class WebPushBody(BaseModel):
    """A browser's PushSubscription (iPhone Home Screen web app, desktop or Android browser)."""
    endpoint: str = Field(min_length=10, max_length=1000)
    p256dh: str = Field(min_length=40, max_length=200)
    auth: str = Field(min_length=10, max_length=60)
    device: str | None = Field(None, max_length=60)
    platform: Literal["ios", "android", "desktop"] = "desktop"
    person: str | None = None                                  # only used when sign-in is off


class PushRemoveBody(BaseModel):
    endpoint: str | None = Field(None, max_length=400)
    id: str | None = Field(None, max_length=20)
    person: str | None = None


def _check(kind: str, s: dict) -> dict:
    s = {k: (v.strip() if isinstance(v, str) else v) for k, v in s.items()}
    if kind == "ntfy":
        if not s.get("topic") or len(s["topic"]) > 64:
            raise HTTPException(422, "Pick a topic name (letters, numbers, dashes).")
        url = s.get("url") or "https://ntfy.sh"
        if not url.startswith(("http://", "https://")):
            raise HTTPException(422, "The ntfy server address should start with http:// or https://")
        return {"url": url.rstrip("/"), "topic": s["topic"], "token": s.get("token") or None}
    if kind in ("webhook", "apprise"):
        what = "webhook" if kind == "webhook" else "Apprise server"
        if not str(s.get("url", "")).startswith(("http://", "https://")):
            raise HTTPException(422, f"The {what} address should start with http:// or https://")
        hdrs = s.get("headers") or {}
        if not isinstance(hdrs, dict) or len(hdrs) > 10:
            raise HTTPException(422, "Headers: at most 10, name and value each.")
        clean = {}
        for k, v in hdrs.items():
            k, v = str(k).strip(), str(v).strip()
            if not k:
                continue
            if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,60}", k) or len(v) > 500 or any(ord(ch) < 32 for ch in v):
                raise HTTPException(422, "A header name or value isn't valid.")
            clean[k] = v
        out = {"url": s["url"].rstrip("/"), "headers": clean}
        if kind == "apprise":
            key = str(s.get("key") or "").strip()
            if key and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key):
                raise HTTPException(422, "The Apprise key can only have letters, numbers, dashes and underscores.")
            urls = str(s.get("urls") or "").strip()
            if not key and not urls:
                raise HTTPException(422, "Give the Apprise config key (saved on the server), or one or more Apprise addresses such as tgram://...")
            out.update(key=key or None, urls=urls or None, tag=str(s.get("tag") or "").strip() or None)
        return out
    if not str(s.get("url", "")).startswith(("http://", "https://")):
        raise HTTPException(422, "The Home Assistant address should start with http:// or https://")
    if not s.get("token"):
        raise HTTPException(422, "Home Assistant needs a long-lived access token.")
    if not s.get("service"):
        raise HTTPException(422, "Which notify service? For example notify.mobile_app_pixel_9.")
    return {"url": s["url"].rstrip("/"), "token": s["token"], "service": s["service"]}


def _public(p: dict, notifier=None) -> dict:
    """Never send stored tokens back to the browser."""
    s = dict(p.get("settings", {}))
    if s.get("token"):
        s["token"] = "••••" + s["token"][-4:]
    if isinstance(s.get("headers"), dict):
        s["headers"] = {k: "set" for k in s["headers"]}          # header values are secrets: names only
    if s.get("urls"):
        s["urls"] = "set"                                        # Apprise addresses hold tokens
    out = {**p, "settings": s}
    if notifier is not None and p.get("kind") == "ha":
        lis = notifier.ha_listeners.get(p["settings"].get("url", "").rstrip("/"))
        out["one_tap"] = bool(lis and lis.connected)
        out["one_tap_error"] = lis.error if lis else None
    return out


def build_notify_router(state) -> APIRouter:
    r = APIRouter(prefix="/api/notify")

    def n():
        return state.notifier

    def people_ids():
        return [p.id for p in state.controller.household.people]

    @r.get("/providers")
    async def list_providers():
        return [_public(p, n()) for p in n().providers]

    @r.post("/providers")
    async def add_provider(b: ProviderBody):
        if b.person is not None and b.person not in people_ids():
            raise HTTPException(422, "unknown person")
        item = {"id": "n_" + secrets.token_hex(4), "kind": b.kind, "name": b.name.strip(), "person": b.person,
                "enabled": b.enabled, "settings": _check(b.kind, b.settings)}
        n().save_providers([*n().providers, item])
        n().sync_ha_listeners()
        return _public(item)

    @r.put("/providers/{nid}")
    async def update_provider(nid: str, b: ProviderBody):
        items = n().providers
        cur = next((p for p in items if p["id"] == nid), None)
        if not cur:
            raise HTTPException(404, "unknown notification target")
        s = dict(b.settings)
        if str(s.get("token", "")).startswith("••••"):      # unchanged masked token: keep the stored one
            from urllib.parse import urlsplit
            def host(u):
                try:
                    x = urlsplit(u or "")
                    return (x.scheme, x.hostname, x.port)
                except ValueError:
                    return None
            if host(s.get("url")) != host(cur["settings"].get("url")):
                raise HTTPException(422, "The address changed: enter the token again.")      # never sent to a new place
            s["token"] = cur["settings"].get("token")
        if b.kind in ("webhook", "apprise"):
            old = cur["settings"]
            if str(s.get("url", "")).rstrip("/") != str(old.get("url", "")).rstrip("/") and (old.get("headers") or old.get("urls")):
                if any(v == "set" for v in (s.get("headers") or {}).values()) or s.get("urls") == "set":
                    raise HTTPException(422, "The address changed: enter the headers again.")   # never sent to a new place
            s["headers"] = {k: (old.get("headers", {}).get(k, "") if v == "set" else v) for k, v in (s.get("headers") or {}).items()}
            if s.get("urls") == "set":
                s["urls"] = old.get("urls")
        cur.update(kind=b.kind, name=b.name.strip(), person=b.person, enabled=b.enabled, settings=_check(b.kind, s))
        n().save_providers(items)
        n().sync_ha_listeners()
        return _public(cur)

    @r.delete("/providers/{nid}")
    async def delete_provider(nid: str):
        n().save_providers([p for p in n().providers if p["id"] != nid])
        n().sync_ha_listeners()
        return {"ok": True}

    @r.post("/test/{nid}")
    async def test(nid: str):
        p = next((x for x in n().providers if x["id"] == nid), None)
        if not p:
            raise HTTPException(404, "unknown notification target")
        who = next((x for x in state.controller.household.people if x.id == p.get("person")), None)
        from .notify import Action
        m = Message("test", "Ember & Tide test", f"Notifications reach {'this phone' if who else 'the household'}. Nothing changes if you tap the button.",
                    who.id if who else None, [Action("Looks good", "noop")] if who else [], priority=3, tags=["white_check_mark"])
        return await n().deliver(p, m)

    def _settings_out():
        ps = n().push_settings()
        return {"public_url": n().public_url, "from_env": bool(n().default_public_url),
                "push_server": ps["server"], "push_token_saved": bool(ps["token"]),
                "fcm": n().fcm.configured, "fcm_project": n().fcm.project}

    @r.get("/settings")
    async def get_settings():
        return _settings_out()

    @r.put("/settings")
    async def put_settings(b: NotifySettingsBody):
        old = state.controller.store.get("notify_settings", {}) or {}
        sent = b.model_fields_set                     # only change what was sent (each form saves its own part)
        url = ((b.public_url or "").strip().rstrip("/") or None) if "public_url" in sent else old.get("public_url")
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(422, "The address should start with http:// or https://")
        push = ((b.push_server or "").strip().rstrip("/") or None) if "push_server" in sent else old.get("push_server")
        if push and not push.startswith(("http://", "https://")):
            raise HTTPException(422, "The push server address should start with https://")
        tok = (b.push_token or "").strip() or None
        if tok is None and b.keep_push_token:
            tok = old.get("push_token")
        state.controller.store.put("notify_settings", {"public_url": url, "push_server": push, "push_token": tok if push else None})
        return _settings_out()

    # ---------- this person's phones (the Android app's UnifiedPush registration) ----------
    def _me(request: Request, body_person: str | None) -> str:
        p = request.scope.get("state", {}).get("principal")
        pid = p.person if p is not None and p.kind != "open" else body_person
        if not pid or pid not in people_ids():
            raise HTTPException(403, "This account isn't linked to a side of the bed.")
        return pid

    @r.get("/me/push")
    async def my_push(request: Request, person: str | None = None):
        pid = _me(request, person)
        return {"server": n().push_settings()["server"], "fcm": n().fcm.configured,
                "devices": [{"id": d["id"], "device": d["device"], "ts": d["ts"], "kind": d.get("kind", "up")} for d in n().devices(pid)]}

    @r.post("/me/push")
    async def add_push(b: PushBody, request: Request):
        from .webpush import valid_keys
        import re as _re
        pid = _me(request, b.person)
        if b.kind == "fcm":
            if not n().fcm.configured:
                raise HTTPException(422, "Google push isn't set up on this server yet. The owner adds the Firebase file (see Studio → Notifications).")
            if not _re.fullmatch(r"[A-Za-z0-9_:\-]{100,400}", b.endpoint.strip()):
                raise HTTPException(422, "That doesn't look like a push token.")
        else:
            err = n().check_endpoint(b.endpoint.strip())
            if err:
                raise HTTPException(422, err)
        if not valid_keys(b.p256dh, b.auth):
            raise HTTPException(422, "The app's push keys aren't valid.")
        d = n().add_device(pid, b.endpoint.strip(), b.p256dh.strip(), b.auth.strip(), b.device, kind=b.kind, platform=b.platform)
        return {"ok": True, "id": d["id"]}

    # ---------- this person's browsers (Web Push) ----------
    @r.get("/me/webpush")
    async def my_webpush(request: Request, person: str | None = None):
        """The key a browser subscribes with, and this person's browsers already subscribed."""
        pid = _me(request, person)
        wp = n().webpush
        return {"available": wp.available, "key": wp.public_key() if wp.available else None,
                "devices": [{"id": d["id"], "device": d["device"], "ts": d["ts"], "platform": d.get("platform")}
                            for d in n().devices(pid) if d.get("kind") == "web"]}

    @r.post("/me/webpush")
    async def add_webpush(b: WebPushBody, request: Request):
        from .web_push import check_endpoint
        from .webpush import valid_keys
        pid = _me(request, b.person)
        if not n().webpush.available:
            raise HTTPException(409, "Web notifications aren't available on this server.")
        err = check_endpoint(b.endpoint.strip())
        if err:
            raise HTTPException(422, err)
        if not valid_keys(b.p256dh, b.auth):
            raise HTTPException(422, "The browser's push keys aren't valid.")
        name = b.device or {"ios": "iPhone", "android": "Android browser", "desktop": "Browser"}[b.platform]
        d = n().add_device(pid, b.endpoint.strip(), b.p256dh.strip(), b.auth.strip(), name, kind="web", platform=b.platform)
        return {"ok": True, "id": d["id"]}

    @r.post("/me/push/remove")
    async def remove_push(b: PushRemoveBody, request: Request):
        pid = _me(request, b.person)
        return {"ok": n().remove_device(pid, endpoint=b.endpoint, did=b.id)}

    @r.post("/me/push/test")
    async def test_push(request: Request, person: str | None = None):
        pid = _me(request, person)
        devs = n().devices(pid)
        if not devs:
            raise HTTPException(409, "No phone is registered for app notifications yet.")
        m = Message("test", "Notifications work", "This is how Ember & Tide will reach this phone.", pid, priority=3)
        results = [await n().push(pid, d, m) for d in devs]
        if not any(results):
            last = next((x for x in reversed(n().sent_log) if not x.get("ok")), {})
            raise HTTPException(502, f"The push server didn't accept it ({last.get('error', 'unknown error')}). "
                                     "It may need write access for UnifiedPush topics or an access token.")
        return {"ok": True, "sent": sum(results)}

    @r.get("/log")
    async def log(limit: int = 30):
        limit = max(1, min(limit, 200))
        sent = [e for e in state.controller.store.events(limit=1000) if e["kind"] == "notification"][:limit]
        # how many phones each person can be reached on (0 = their notifications go nowhere)
        reach = {p.id: len(n().devices(p.id)) for p in state.controller.household.people}
        return {"sent": sent, "deliveries": list(reversed(n().sent_log))[:limit], "reach": reach}

    # ---- buttons in notifications (no sign-in: the code is single-use and does one thing) ----
    @r.get("/act/{code}")
    async def peek(code: str):
        a = n().peek_action(code)
        if not a:
            raise HTTPException(404, "This button has expired or was already used.")
        return {"label": a["label"], "act": a["act"], "person_name": a["person_name"], "minutes": a.get("minutes")}

    @r.post("/act/{code}")
    async def act(code: str):
        a = n().peek_action(code)
        if not a:
            raise HTTPException(404, "This button has expired or was already used.")
        if a["act"] == "noop":
            n().discard_action(code)
            return {"ok": True, "done": "Nothing to change. Notifications work."}
        used = n().use_action(code)
        await state.controller.tick(force={a["person"]})
        return {"ok": True, "done": used["done"] if used else "Done."}

    return r

