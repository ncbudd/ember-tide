"""Cloud accounts: a person's own account at a wearable maker, read server side over the maker's public API.

One module per provider (oura, withings, whoop, polar) subclasses `CloudConnector` and supplies only what differs:
the OAuth URLs and scopes, how its token endpoint wants to be called, and `fetch()` turning its API into our metrics.
Everything else is shared here:

- the owner's app credentials, entered once (kv `<id>_app`; the secret never goes back to a browser);
- per-person OAuth2 authorization-code flow with a single-use `state` (kv `<id>_states`), tokens kept server side
  (kv `<id>_token:<pid>`), refreshed before they expire (refresh tokens may be single-use: the newest is kept);
- polite polling: once after the person wakes (the device has usually synced by then), then hourly, never during
  the night; a 429 backs off for the provider's Retry-After; a dead sign-in stops polling until reconnected;
- storage through the ordinary wearable import (`_import_wearable`, device = the provider's name, platform "server"),
  so clock alignment and the cross-device duplicate rule apply, plus sleep stages through `put_stages`; nights the
  heart rate touched are re-checked by Autopilot in hindsight.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

UA = {"User-Agent": "EmberTide/1 (+self-hosted)"}
STATE_TTL_S = 15 * 60
HOURLY_S = 3600
AFTER_WAKE_S = 20 * 60
CHECK_EVERY_S = 300


class CloudError(Exception):
    def __init__(self, text: str, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(text)
        self.status, self.retry_after = status, retry_after


@dataclass
class CloudData:
    """What a provider's fetch returns: series per metric ([[t, v], ...]) and nights of stages ((start, end, segs))."""
    series: dict[str, list] = field(default_factory=dict)
    stages: list[tuple[float, float, list[dict]]] = field(default_factory=list)
    periods: list[dict] = field(default_factory=list)      # every sleep, nights and naps (engine/sleeps.py)

    def add(self, metric: str, t: float | None, v) -> None:
        if t is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
            self.series.setdefault(metric, []).append([float(t), float(v)])


class CloudConnector:
    id = "base"
    name = "Base"
    device = "Base"                      # the wearable device name its data is stored under
    auth_url = ""
    token_url = ""
    revoke_url: str | None = None
    scopes = ""
    scope_sep = " "
    token_auth = "body"                  # "body": client id/secret in the form; "basic": HTTP Basic
    refreshes = True                     # False: tokens are long-lived and there's no refresh (Polar)
    first_days = 14
    data_version = 1                     # bump when fetch() starts bringing a new metric: the next sync looks back first_days again
    gives = ""                           # plain words for the UI: "sleep stages, heart rate, ..."
    docs_url = ""
    callback_path: str | None = None     # default /api/cloud/<id>/callback

    def __init__(self, controller, client: httpx.AsyncClient | None = None) -> None:
        self.c = controller
        self.store = controller.store
        self.client = client
        self._locks: dict[str, asyncio.Lock] = {}

    # ---------- configuration (owner) ----------
    def k(self, kind: str, pid: str | None = None) -> str:
        return f"{self.id}_{kind}" + (f":{pid}" if pid else "")

    @property
    def app(self) -> dict:
        return self.store.get(self.k("app"), {}) or {}

    @property
    def configured(self) -> bool:
        a = self.app
        return bool(a.get("client_id") and a.get("client_secret"))

    def save_app(self, client_id: str | None, client_secret: str | None) -> None:
        cur = self.app
        cid = (client_id if client_id is not None else cur.get("client_id") or "").strip()
        sec = (client_secret if client_secret else cur.get("client_secret") or "").strip()
        self.store.put(self.k("app"), {"client_id": cid, "client_secret": sec} if cid else {})

    def redirect_path(self) -> str:
        return self.callback_path or f"/api/cloud/{self.id}/callback"

    # ---------- per person ----------
    def token(self, pid: str) -> dict | None:
        return self.store.get(self.k("token", pid))

    def status(self, pid: str) -> dict:
        t = self.token(pid) or {}
        st = self.store.get(self.k("status", pid), {}) or {}
        return {"available": self.configured, "connected": bool(t.get("access_token")), "needs_reconnect": bool(st.get("needs_reconnect")),
                "connected_at": t.get("connected_at"), "last_sync": st.get("last_sync"), "last_error": st.get("last_error"),
                "last_stored": st.get("last_stored")}

    def _status(self, pid: str, **kv) -> None:
        st = self.store.get(self.k("status", pid), {}) or {}
        st.update(kv)
        self.store.put(self.k("status", pid), st)

    def auth_params(self) -> dict:
        return {}

    def authorize_url(self, pid: str, redirect_uri: str) -> str:
        if not self.configured:
            raise CloudError(f"{self.name} isn't set up on this server yet. The owner adds it in Studio → Integrations.")
        state = secrets.token_urlsafe(24)
        now = time.time()
        states = {k: v for k, v in (self.store.get(self.k("states"), {}) or {}).items() if now - v["ts"] < STATE_TTL_S}
        states[state] = {"pid": pid, "ts": now, "redirect_uri": redirect_uri}
        self.store.put(self.k("states"), dict(list(states.items())[-20:]))
        q = {"response_type": "code", "client_id": self.app["client_id"], "redirect_uri": redirect_uri,
             "scope": self.scope_sep.join(self.scopes.split()), "state": state, **self.auth_params()}
        return f"{self.auth_url}?{urlencode(q)}"

    def take_state(self, state: str) -> dict | None:
        states = self.store.get(self.k("states"), {}) or {}
        s = states.pop(state, None) if isinstance(state, str) else None
        self.store.put(self.k("states"), states)
        if not s or time.time() - s["ts"] > STATE_TTL_S:
            return None
        return s

    async def finish(self, code: str, st: dict) -> None:
        tok = await self._token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": st["redirect_uri"]})
        self._save_token(st["pid"], tok, connected=True)
        self._status(st["pid"], needs_reconnect=False, last_error=None)
        await self.after_connect(st["pid"], tok)

    async def after_connect(self, pid: str, tok: dict) -> None:
        """Some providers need a registration call once connected (Polar)."""

    async def disconnect(self, pid: str) -> None:
        t = self.token(pid)
        self.store.put(self.k("token", pid), None)
        self.store.put(self.k("status", pid), {})
        if t and t.get("access_token") and self.revoke_url:
            try:
                async with self._http() as c:
                    await c.post(self.revoke_url, params={"access_token": t["access_token"]}, headers=UA)
            except Exception:  # noqa: BLE001  (best effort)
                pass

    def _save_token(self, pid: str, tok: dict, connected: bool = False) -> None:
        old = self.token(pid) or {}
        if not tok.get("access_token"):
            raise CloudError(f"{self.name} didn't send a token.")
        self.store.put(self.k("token", pid), {
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token") or old.get("refresh_token"),
            "expires_at": time.time() + float(tok.get("expires_in") or (10 * 365 * 86400 if not self.refreshes else 86400)),
            "scope": tok.get("scope", old.get("scope")),
            "user": str(tok.get("x_user_id") or tok.get("userid") or old.get("user") or "") or None,
            "connected_at": time.time() if connected else old.get("connected_at")})

    # ---------- HTTP ----------
    def _http(self):
        if self.client is not None:
            class _Keep:                     # an injected client (tests) is reused, not closed
                def __init__(s, c): s.c = c
                async def __aenter__(s): return s.c
                async def __aexit__(s, *a): return False
            return _Keep(self.client)
        return httpx.AsyncClient(timeout=20)

    def parse_token(self, r: httpx.Response) -> dict:
        return r.json()

    async def _token_request(self, form: dict) -> dict:
        a = self.app
        auth = None
        if self.token_auth == "basic":
            auth = (a.get("client_id", ""), a.get("client_secret", ""))
        else:
            form = {**form, "client_id": a.get("client_id", ""), "client_secret": a.get("client_secret", "")}
        async with self._http() as c:
            r = await c.post(self.token_url, data=form, headers={**UA, "Accept": "application/json"}, auth=auth)
        if r.status_code == 429:
            raise CloudError(f"{self.name} is busy right now.", 429, retry_after(r))
        if r.status_code >= 400:
            # a refused token request (invalid_grant…) means the sign-in is gone: reported as 401 so the person is asked
            # to connect again; a refused DATA call (400) is just an error and polling carries on
            dead = r.status_code in (400, 401)
            raise CloudError(f"{self.name} didn't accept the sign-in." if dead else f"{self.name} answered {r.status_code}.",
                             401 if dead else r.status_code)
        return self.parse_token(r)

    async def fresh_token(self, pid: str, force: bool = False) -> str:
        t = self.token(pid)
        if not t or not t.get("access_token"):
            raise CloudError(f"{self.name} isn't connected.", 401)
        if self.refreshes and (force or t.get("expires_at", 0) - time.time() < 300):
            if not t.get("refresh_token"):
                raise CloudError(f"{self.name}'s sign-in ran out. Connect again.", 401)
            tok = await self._token_request({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
            self._save_token(pid, tok)
            t = self.token(pid)
        elif force:
            raise CloudError(f"{self.name}'s sign-in ran out. Connect again.", 401)
        return t["access_token"]

    async def request(self, pid: str, method: str, url: str, allow: tuple[int, ...] = (), **kw) -> httpx.Response:
        """One API call with the person's token; a 401 gets one refresh and a retry; 429/403/5xx become CloudErrors."""
        tok = await self.fresh_token(pid)
        extra = kw.pop("headers", {})
        for attempt in (0, 1):
            async with self._http() as c:
                r = await c.request(method, url, headers={**UA, "Authorization": f"Bearer {tok}", **extra}, **kw)
            if r.status_code == 401 and attempt == 0:
                tok = await self.fresh_token(pid, force=True)
                continue
            break
        if r.status_code in allow:
            return r
        if r.status_code == 429:
            raise CloudError(f"{self.name} is busy right now.", 429, retry_after(r))
        if r.status_code == 401:
            raise CloudError(f"{self.name}'s sign-in ran out. Connect again.", 401)
        if r.status_code == 403:
            raise CloudError(f"{self.name} didn't allow this data (a permission wasn't granted, or the membership has lapsed).", 403)
        if r.status_code >= 400 and r.status_code != 404:
            raise CloudError(f"{self.name} answered {r.status_code}.", r.status_code)
        return r

    # ---------- provider part ----------
    async def fetch(self, pid: str, start: date, end: date, now: float) -> CloudData:
        raise NotImplementedError

    # ---------- syncing ----------
    async def sync(self, pid: str, days: int | None = None) -> dict:
        lock = self._locks.setdefault(pid, asyncio.Lock())
        async with lock:
            return await self._sync(pid, days)

    async def _sync(self, pid: str, days: int | None) -> dict:
        c = self.c
        p = c.person(pid)
        now = c.clock.now_ts()
        st = self.store.get(self.k("status", pid), {}) or {}
        self._status(pid, last_attempt=now)
        fresh = st.get("last_sync") and (st.get("data_version") or 1) >= self.data_version
        back = days if days else (2 if fresh else self.first_days)
        today = datetime.fromtimestamp(now, c.clock.tz).date()
        try:
            data = await self.fetch(pid, today - timedelta(days=back), today + timedelta(days=1), now)
        except CloudError as e:
            if e.status == 429:
                self._status(pid, last_error=f"{self.name} is busy; trying again later.", backoff_until=now + (e.retry_after or 900))
            elif e.status == 401:
                self._status(pid, last_error=f"{self.name}'s sign-in ran out. Connect again.", needs_reconnect=True)
            else:
                self._status(pid, last_error=str(e))
            raise
        except httpx.HTTPError:
            self._status(pid, last_error=f"Couldn't reach {self.name}.")
            raise CloudError(f"Couldn't reach {self.name}.")
        stored = store_data(c, p, self.device, data, f"{self.id}", f"{self.name} · cloud", now)
        self._status(pid, last_sync=now, last_error=None, needs_reconnect=False, backoff_until=None, last_stored=stored,
                     data_version=self.data_version)
        return {"ok": True, "stored": {k: v for k, v in stored.items() if k != "nights"}, "nights": stored.get("nights", 0)}

    # ---------- polling ----------
    def due(self, pid: str, now: float) -> bool:
        """After waking (once), then hourly; never during the night or while backing off."""
        if not (self.token(pid) or {}).get("access_token"):
            return False
        st = self.store.get(self.k("status", pid), {}) or {}
        if st.get("needs_reconnect") or now < (st.get("backoff_until") or 0):
            return False
        return after_wake_due(self.c, pid, now, st.get("last_attempt") or 0)

    async def tick(self) -> None:
        if not self.configured:
            return
        now = self.c.clock.now_ts()
        for p in self.c.household.people:
            if self.due(p.id, now):
                try:
                    await self.sync(p.id)
                except Exception as e:  # noqa: BLE001
                    log.info("%s sync for %s: %s", self.id, p.id, e)


def after_wake_due(c, pid: str, now: float, last: float, every_s: float = HOURLY_S) -> bool:
    from ..engine.rhythm import night_date, pick_rhythm, resolve
    p = c.person(pid)
    tz = c.clock.tz
    d = night_date(datetime.fromtimestamp(now, tz))
    rh = pick_rhythm(p.rhythms, d)
    if rh:
        n = resolve(rh, d, tz)
        woke = n.wake + AFTER_WAKE_S
        if n.bedtime <= now < woke:
            return False                                       # asleep, or the device hasn't synced yet
        if woke <= now and last < woke:
            return True                                        # the morning's first look
    return now - last >= every_s


def store_data(c, p, device: str, data: CloudData, stage_source: str, stage_label: str, now: float, platform: str = "server") -> dict:
    """Into the wearable store exactly like a Health Connect import (same dedupe), then stages, then hindsight."""
    from ..autopilot_api import WearableBody, _import_wearable, _recheck
    stored: dict[str, int] = {}
    touched: set[str] = set()
    for metric, pts in data.series.items():
        if not pts:
            continue
        pts.sort(key=lambda x: x[0])
        rep = _import_wearable(c, p, WearableBody(device=device, metric=metric, points=pts[-50000:], platform=platform))
        stored[metric] = rep["stored"]
        if metric in ("hr", "skin_temp"):
            touched.update(rep["nights_touched"])
    nights = 0
    for t0, t1, segs in data.stages:
        segs = [g for g in segs if g["end"] <= now + 300]
        if segs:
            c.store.put_stages(p.id, stage_source, stage_label, segs, t0 - 60, t1 + 60)
            nights += 1
    if data.periods:
        for x in data.periods:                                  # a nap's stages aren't part of any night's stage view
            if x["kind"] == "nap":
                c.store.put_stages(p.id, stage_source, stage_label, [], x["start"] - 60, x["end"] + 60)
        from ..engine.sleeps import save as save_sleeps
        stored["sleeps"] = save_sleeps(c, p, data.periods, now)
    for d in sorted(touched)[-7:]:
        try:
            _recheck(c, p, d)
        except Exception:  # noqa: BLE001  (no rhythm that night, etc.)
            pass
    stored["nights"] = nights
    return stored


def retry_after(r: httpx.Response) -> float | None:
    try:
        return max(30.0, min(6 * 3600.0, float(r.headers.get("retry-after", ""))))
    except ValueError:
        return None


def ts(v) -> float | None:
    """Seconds from an ISO string (with or without Z), or from epoch seconds."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def merge_segments(segs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for g in sorted(segs, key=lambda x: x["start"]):
        if out and out[-1]["stage"] == g["stage"] and abs(out[-1]["end"] - g["start"]) < 1:
            out[-1]["end"] = g["end"]
        else:
            out.append(dict(g))
    return out
