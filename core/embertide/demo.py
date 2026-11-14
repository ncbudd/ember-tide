"""Live public demo mode (ET_DEMO=on):

- Forces the bed simulator and disables every outbound network channel.
- Seeds a realistic two-sleeper household with a pet and ~7 past nights at start and every hour.
- Enforces an allow/deny list in middleware so visitors can try bed controls and rhythms freely
  while external, account, security, export/import, and console endpoints return 403.
- Rate-limits visitor mutations using the existing Auth throttle table.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .clock import FixedClock
from .drivers.sim import SimBed
from .engine.controller import Controller
from .models import Household, Pet
from .seed import apply_persona_temps, default_household
from .sources import SourceManager, default_sim_sources
from .store import Store

log = logging.getLogger("embertide.demo")

DEMO_BANNER = "Demo: changes reset every hour; nothing here controls a real bed"
DEMO_DENY_MSG = "Not available in the demo."
DEMO_RESET_S = 3600
DEMO_MUTATE_LIMIT = 20
DEMO_MUTATE_WINDOW_S = 60
DEMO_MAX_BODY = 16 * 1024        # visitors only send small JSON; refuse anything bigger before it is read into memory
DEMO_WS_PER_IP = 4               # live-update sockets one visitor may hold open
DEMO_WS_TOTAL = 100
DEMO_DB_MAX_BYTES = 50 * 1024 * 1024   # reset early if visitors grow the database past this
DEMO_CHECK_S = 60


def apply_demo_config(cfg) -> None:
    """Force safe simulated-only defaults whenever demo mode is enabled."""
    if not getattr(cfg, "demo", False):
        return
    cfg.bed = "sim"
    cfg.push_relay = False
    cfg.ha_mqtt_url = None
    cfg.llm_url = None
    cfg.auth = False


# ---------- centralized demo allow / deny policy ----------
# Anything matching a blocked prefix or blocked route returns 403 "Not available in the demo.",
# regardless of query params or cookies. Reads (GET/HEAD/OPTIONS) outside those blocked areas are
# permitted; mutations are allowed only if explicitly matched below.
DEMO_BLOCKED_PREFIXES = (
    "/mcp",
    "/api/notify",
    "/api/ai/settings",
    "/api/integrations",
    "/api/hooks",
    "/api/cloud",
    "/api/oura",
    "/api/ha",
    "/api/dials",
    "/api/dial-beacons",
)

DEMO_BLOCKED_ROUTES: list[tuple[str | None, re.Pattern[str]]] = [
    (None, re.compile(r"^/api/auth/(?!state$).+")),        # logins, users, invites, tokens, MCP keys, passwords
    ("GET", re.compile(r"^/api/export$")),                 # data export
    ("POST", re.compile(r"^/api/console$")),               # raw bed console
    ("POST", re.compile(r"^/api/sim$")),                   # clock jumping / fault injection
    ("POST", re.compile(r"^/api/sources(/.*)?$")),         # source creation & HA connection test
    ("PUT", re.compile(r"^/api/sources(/.*)?$")),
    ("DELETE", re.compile(r"^/api/sources(/.*)?$")),
    ("POST", re.compile(r"^/api/people/[^/]+/(wearable(/repair)?|watch-hr|stages)$")),  # wearable imports
    ("POST", re.compile(r"^/api/hub(/.*)?$")),
    ("PUT", re.compile(r"^/api/hub(/.*)?$")),
    ("POST", re.compile(r"^/api/care(/.*)?$")),
    ("PUT", re.compile(r"^/api/care(/.*)?$")),
    ("DELETE", re.compile(r"^/api/care(/.*)?$")),
    ("PUT", re.compile(r"^/api/calibration$")),
    ("POST", re.compile(r"^/api/calibration/tare/[^/]+$")),
    ("POST", re.compile(r"^/api/people/[^/]+/phone-context$")),
    ("DELETE", re.compile(r"^/api/people/[^/]+/phone-context$")),
    ("DELETE", re.compile(r"^/api/client-errors$")),
    ("GET", re.compile(r"^/api/client-errors$")),          # one visitor's text is not shown to the next
    ("POST", re.compile(r"^/api/client-error$")),
]

DEMO_ALLOWED_MUTATIONS: list[tuple[str, re.Pattern[str]]] = [
    ("POST", re.compile(r"^/api/live/ticket$")),
    ("POST", re.compile(r"^/api/people/[^/]+/adjust(/clear)?$")),
    ("POST", re.compile(r"^/api/people/[^/]+/flash(/extend|/stop)?$")),
    ("POST", re.compile(r"^/api/people/[^/]+/tonight$")),
    ("POST", re.compile(r"^/api/people/[^/]+/away(-check)?$")),
    ("POST", re.compile(r"^/api/people/[^/]+/(feedback|feel)$")),
    ("POST", re.compile(r"^/api/people/[^/]+/pet-question/[^/]+$")),
    ("PUT", re.compile(r"^/api/people/[^/]+/rhythms/[^/]+$")),
    ("PUT", re.compile(r"^/api/people/[^/]+/(settings|profile|night-fix)$")),
    ("PATCH", re.compile(r"^/api/people/[^/]+/settings$")),
    ("DELETE", re.compile(r"^/api/people/[^/]+/night-fix$")),
    ("POST", re.compile(r"^/api/household/swap-sides$")),
    ("PUT", re.compile(r"^/api/household/region$")),
    ("PUT", re.compile(r"^/api/bed-orientation$")),
    ("POST", re.compile(r"^/api/guest/(left|right|both)/(start|stop|set)$")),
    ("PUT", re.compile(r"^/api/pets$")),
    ("DELETE", re.compile(r"^/api/pets/[^/]+/learned$")),
    ("POST", re.compile(r"^/api/people/[^/]+/autopilot/(replay|recheck|recalibrate|ai-review/dismiss)$")),
    ("POST", re.compile(r"^/api/people/[^/]+/autopilot/suggestions/[^/]+/dismiss$")),
    ("POST", re.compile(r"^/api/people/[^/]+/insights/(dismiss|apply)$")),
    ("POST", re.compile(r"^/api/assistant/(ask|proposals/[^/]+/(apply|dismiss)|changes/[^/]+/undo)$")),
    ("POST", re.compile(r"^/api/ai/ask$")),
]


def demo_allowed(method: str, path: str) -> bool:
    """Single source of truth for demo access: True if the route may run, False for 403."""
    p = path.rstrip("/") or "/"
    m = method.upper()
    if any(p == pref or p.startswith(pref + "/") for pref in DEMO_BLOCKED_PREFIXES):
        return False
    for bm, rx in DEMO_BLOCKED_ROUTES:
        if (bm is None or bm == m) and rx.match(p):
            return False
    if not (p.startswith("/api/") or p == "/api"):
        return True
    if m in ("GET", "HEAD", "OPTIONS"):
        return True
    return any(am == m and rx.match(p) for am, rx in DEMO_ALLOWED_MUTATIONS)


class _BodyTooLarge(Exception):
    pass


def _capped_receive(receive, limit: int):
    """Wrap an ASGI receive so a body without (or lying about) Content-Length still stops at `limit` bytes."""
    seen = 0

    async def wrapped():
        nonlocal seen
        msg = await receive()
        if msg.get("type") == "http.request":
            seen += len(msg.get("body", b""))
            if seen > limit:
                raise _BodyTooLarge()
        return msg
    return wrapped


class DemoMiddleware:
    """Enforces the demo route policy and polite visitor mutation throttles."""

    def __init__(self, app, state) -> None:
        self.app = app
        self.state = state
        self.ws_open: dict[str, int] = {}

    async def __call__(self, scope, receive, send):
        cfg = getattr(self.state, "cfg", None)
        if not cfg or not getattr(cfg, "demo", False) or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = (scope.get("path") or "/").rstrip("/") or "/"
        method = scope.get("method", "GET") if scope["type"] == "http" else "GET"
        if not demo_allowed(method, path):
            return await self._deny(scope, receive, send, 403, DEMO_DENY_MSG)
        if scope["type"] == "http":
            headers_raw = dict(scope.get("headers", []))
            try:
                declared = int(headers_raw.get(b"content-length", b"0") or 0)
            except ValueError:
                declared = DEMO_MAX_BODY + 1
            if declared > DEMO_MAX_BODY:
                return await self._deny(scope, receive, send, 413, "That request is too large for the demo.")
            receive = _capped_receive(receive, DEMO_MAX_BODY)
        if scope["type"] == "http" and path.startswith("/api") and method not in ("GET", "HEAD", "OPTIONS"):
            auth = getattr(self.state, "auth", None)
            if auth is not None:
                from .auth_api import peer_ip
                headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
                peer = (scope.get("client") or (None,))[0]
                key = f"demo:{peer_ip(headers, peer, cfg)}"
                now = time.time()
                if auth.throttled(key, now, limit=DEMO_MUTATE_LIMIT, window=DEMO_MUTATE_WINDOW_S):
                    return await self._deny(scope, receive, send, 429, "Slow down a little — try again in a minute.")
                auth.failed(key, now)
        if scope["type"] != "http":
            from .auth_api import peer_ip
            headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            ip = peer_ip(headers, (scope.get("client") or (None,))[0], cfg)
            if self.ws_open.get(ip, 0) >= DEMO_WS_PER_IP or sum(self.ws_open.values()) >= DEMO_WS_TOTAL:
                return await self._deny(scope, receive, send, 429, "Too many open connections.")
            self.ws_open[ip] = self.ws_open.get(ip, 0) + 1
            try:
                return await self.app(scope, receive, send)
            finally:
                left = self.ws_open.get(ip, 1) - 1
                if left > 0:
                    self.ws_open[ip] = left
                else:
                    self.ws_open.pop(ip, None)
        started = False

        async def send_tracked(msg):
            nonlocal started
            if msg.get("type") == "http.response.start":
                started = True
            await send(msg)
        try:
            return await self.app(scope, receive, send_tracked)
        except _BodyTooLarge:
            if not started:
                await self._deny(scope, receive, send, 413, "That request is too large for the demo.")

    @staticmethod
    async def _deny(scope, receive, send, code: int, msg: str):
        if scope["type"] == "websocket":
            try:
                await receive()
            except Exception:  # noqa: BLE001
                pass
            await send({"type": "websocket.close", "code": 4403})
            return
        body = json.dumps({"detail": msg}).encode()
        await send({
            "type": "http.response.start",
            "status": code,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})


# ---------- household & 7-night history seeding ----------
def build_demo_household() -> Household:
    """Two sleepers with distinct thermal preferences and rhythms, plus one bed-sharing cat."""
    hh = default_household()
    p1, p2 = hh.people
    p1.name, p1.persona = "Alex", "ember"
    p1.settings.welcomed = True
    p1.settings.units = "F"
    apply_persona_temps(p1)

    p2.name, p2.persona = "Sam", "tide"
    p2.settings.welcomed = True
    p2.settings.units = "F"
    p2.settings.step_f = 0.5
    apply_persona_temps(p2)

    hh.pets = [Pet(id="demo_pet", name="Milo", kind="cat", weight_kg=4.5, sleeps_on_bed="yes")]
    return hh


async def seed_demo_store(store: Store, tz_name: str, now_ts: float, household: Household | None = None, nights: int = 7) -> Household:
    """Populate `store` with the demo household, sim sources, and `nights` past nights ending at `now_ts`."""
    store.clear_all()
    hh = household or build_demo_household()
    tz = ZoneInfo(tz_name)
    now_dt = datetime.fromtimestamp(now_ts, tz)
    # Start `nights` evenings ago at 20:00 local time so we finish cleanly at `now_ts`.
    start_day = (now_dt - timedelta(days=nights)).replace(hour=20, minute=0, second=0, microsecond=0)
    if start_day.timestamp() >= now_ts - 3600:
        start_day -= timedelta(days=1)

    clock = FixedClock(start_day.timestamp(), tz_name)
    bed = SimBed(clock, room_c=20.6, seed=7)
    bed.scripted, bed.echo, bed.blips = True, True, False
    store.put("household", hh.model_dump())
    store.put("sources", [c.model_dump() for c in default_sim_sources()])
    sources = SourceManager(store, clock, lambda: bed, demo=True)
    ctl = Controller(hh, bed, store, clock, sources=sources, demo=True)

    # Bulk-seed SQLite without waiting on per-statement fsync, restoring durability afterwards.
    with store._lock:
        store._db.execute("PRAGMA synchronous=OFF")
    try:
        rnd = random.Random(19)
        t = clock.now_ts()
        while t <= now_ts:
            bed._tick()
            for c in sources.configs:
                if c.enabled and c.driver == "sim":
                    sources._sample(c, sources._sim_value(c, t), t)
            await ctl.tick()
            for task in list(ctl._inflight.values()):
                await task
            hour = clock.now().hour
            dt = 300.0 if (hour >= 20 or hour < 9) else 3600.0
            step = min(dt, max(1.0, now_ts - t)) if t < now_ts else 0.0
            if step <= 0:
                break
            clock.advance(step)
            t = clock.now_ts()

        # Fill realistic sleep stages and morning check-in feedback for completed nights.
        first_night = start_day.date()
        completed_nights = max(1, min(nights, (now_dt.date() - first_night).days))
        for i in range(completed_nights):
            nd = first_night + timedelta(days=i)
            night_str = nd.isoformat()
            for pid, bed_hm, label, src in (
                ("p1", "22:30", "Simulated Watch", "demo_watch"),
                ("p2", "23:15", "Simulated Ring", "demo_ring"),
            ):
                bh, bm = map(int, bed_hm.split(":"))
                base_dt = datetime(nd.year, nd.month, nd.day, bh, bm, tzinfo=tz)
                st_ts = base_dt.timestamp()
                if st_ts + 7 * 3600 > now_ts:
                    continue
                segs = []
                cur = st_ts
                for stage in ("light", "deep", "deep", "light", "rem", "light", "deep", "light", "rem", "light", "awake", "light", "rem", "light"):
                    mins = {"deep": rnd.randint(20, 34), "rem": rnd.randint(14, 26), "light": rnd.randint(18, 36), "awake": rnd.randint(4, 8)}[stage]
                    segs.append({"start": cur, "end": cur + mins * 60, "stage": stage})
                    cur += mins * 60
                store.put_stages(pid, src, label, segs, st_ts - 1800, cur + 1800)
                if i < completed_nights - 1 or rnd.random() < 0.7:
                    store.put(f"feedback:{pid}:{night_str}", {"temp": "right" if i != 2 else ("cold" if pid == "p1" else "warm"), "rested": 4 if i % 2 else 5})

        ctl.reload_household(hh)
    finally:
        with store._lock:
            store._db.execute("PRAGMA synchronous=NORMAL")
    return hh


async def reset_demo_state(state) -> None:
    """Restore the live running demo server back to its freshly seeded household and 7 nights."""
    ctl: Controller = state.controller
    clock = ctl.clock
    now_ts = clock.now_ts()

    # Cancel any queued hardware tasks from visitors before clearing the database.
    for task in list(ctl._inflight.values()):
        task.cancel()
    ctl._inflight.clear()
    ctl._pending_force.clear()
    ctl._pending_desired.clear()

    hh = await seed_demo_store(ctl.store, getattr(clock.tz, "key", str(clock.tz)), now_ts)

    # Reset simulated bed hardware and in-memory engine/presence state.
    drv = ctl.driver
    if hasattr(drv, "z"):
        drv.scripted = True
        drv.echo = True
        drv.blips = False
        drv.low_water = False
        if hasattr(drv, "faults"):
            drv.faults.clear()
        for z in drv.z.values():
            z.on = False
            z.occupied = False
            z.in_since = None
            z.out_since = None
        if hasattr(drv, "_tick"):
            drv._tick()

    from .engine.night import NightState
    ctl.reload_household(hh)
    for pid in list(ctl.engines):
        ctl.engines[pid].s = NightState()
    ctl.presence.sides.clear()
    ctl.fused.clear()
    ctl._restore()

    if state.sources is not None:
        from .sources import Calibration, SourceConfig
        state.sources.configs = [SourceConfig(**c) for c in ctl.store.get("sources", [])]
        state.sources.calibration = Calibration(**(ctl.store.get("calibration") or {}))
        state.sources.latest.clear()
        state.sources.paused_zones.clear()

    await ctl.tick()
    log.info("demo state reset cleanly (household %s + seeded nights)", ", ".join(p.name for p in hh.people))


def _db_bytes(state) -> int:
    path = getattr(getattr(state.controller, "store", None), "path", None)
    if not path or path == ":memory:":
        return 0
    from pathlib import Path
    total = 0
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(path) + suffix)
        if f.exists():
            total += f.stat().st_size
    return total


async def demo_reset_loop(state, every_s: float = DEMO_RESET_S, check_s: float = DEMO_CHECK_S,
                          max_bytes: int = DEMO_DB_MAX_BYTES) -> None:
    """Background task resetting all visitor changes every hour, or sooner if the database grows too large."""
    last = time.monotonic()
    while True:
        await asyncio.sleep(min(check_s, every_s))
        if time.monotonic() - last < every_s and _db_bytes(state) < max_bytes:
            continue
        last = time.monotonic()
        try:
            await reset_demo_state(state)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("scheduled demo reset failed")
