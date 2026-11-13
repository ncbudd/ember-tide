"""FastAPI application: wires config → driver → controller, serves the API and the built web app."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import build_router
from .clock import Clock
from .config import Config
from .engine.controller import Controller
from .models import Household
from .seed import default_household
from .store import Store

log = logging.getLogger("embertide")
for _noisy in ("httpx", "httpcore"):                  # request URLs can carry secrets (calendar feed links): never log them
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class AppState:
    controller: Controller


def make_driver(cfg: Config, clock: Clock):
    if cfg.bed == "orion-mqtt":
        from .drivers.orion_mqtt import OrionMqtt
        return OrionMqtt(cfg.mqtt_url, cfg.orion_serial)
    if cfg.bed == "orion-cloud":
        if not cfg.orion_api_key:
            raise SystemExit("ET_ORION_API_KEY is required for the orion-cloud driver")
        from .drivers.orion_cloud import OrionCloud
        return OrionCloud(cfg.orion_api_key, cfg.orion_serial)
    from .drivers.sim import SimBed
    return SimBed(clock)


class BodyLimit:
    """No request body over 512 KB (wearable imports: 8 MB), whether it says so up front or streams in chunks: a few
    huge uploads through the tunnel can't fill the server's memory."""
    SMALL, BIG = 512 * 1024, 8 * 1024 * 1024
    BIG_PATHS = ("/wearable", "/watch-hr", "/import")

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        limit = self.BIG if any(path.endswith(x) for x in self.BIG_PATHS) else self.SMALL
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > limit:
                        return await self._too_big(send)
                except ValueError:
                    return await self._too_big(send)
        seen = 0

        async def counted():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise _TooBig()
            return msg
        try:
            await self.app(scope, counted, send)
        except _TooBig:
            await self._too_big(send)

    @staticmethod
    async def _too_big(send):
        body = b'{"detail":"That request is too big."}'
        try:
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
        except Exception:  # noqa: BLE001  (a response had already started)
            pass


class _TooBig(Exception):
    pass


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    state = AppState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        clock = Clock(cfg.tz, speed=cfg.sim_speed if cfg.bed == "sim" else 1.0)
        store = Store(cfg.db_path)
        saved = store.get("household")
        household = Household.model_validate(saved) if saved else default_household()
        if not saved:
            store.put("household", household.model_dump())
        driver = make_driver(cfg, clock)
        await driver.start()
        from .sources import SourceManager, default_sim_sources
        if cfg.bed == "sim" and store.get("sources") is None:
            store.put("sources", [c.model_dump() for c in default_sim_sources()])
        sources = SourceManager(store, clock, lambda: driver)
        await sources.start()
        state.sources = sources
        state.cfg = cfg
        state.controller = Controller(household, driver, store, clock, sources)
        if sources.migrate(household.people):              # older setups → "who is it for" (sources.migrate)
            sources.save()
        from .auth import Auth
        from .notify import Notifier
        state.auth = Auth(store, enabled=cfg.auth)
        state.controller.notifier = state.notifier = Notifier(state.controller, cfg.public_url)
        app.state.notifier = state.notifier
        state.notifier.owner_persons = lambda: {u["person"] for u in state.auth.users() if u["role"] == "owner" and u["person"]}
        state.notifier.care_persons = lambda: {u["person"] for u in state.auth.users() if u["role"] in ("owner", "sleeper") and u["person"]}
        def _accounts():
            out = []
            for prov in state.cloud.providers.values():
                for p in state.controller.household.people:
                    st = prov.status(p.id)
                    if st["connected"]:
                        out.append((p.id, prov.name, bool(st["needs_reconnect"])))
            return out
        state.notifier.account_states = _accounts
        state.notifier.bridge_status = lambda: (bool(state.ha.url), state.ha.state)
        state.notifier.sync_ha_listeners()
        from .ha_bridge import HaBridge
        saved_ha = store.get("ha_bridge", {}) or {}
        import asyncio as _asyncio

        def _review_hook(person, d):
            from .autopilot_api import _review
            async def run():
                try:
                    out = await _review(state, person, d, None)
                    state.notifier.send_review(person, d, out)
                except Exception:  # noqa: BLE001
                    log.warning("morning AI review for %s failed", person.id)
            _asyncio.get_running_loop().create_task(run())
        state.controller.review_hook = _review_hook
        from .cloud import CloudAccounts             # cloud accounts (Oura, Withings, WHOOP, Polar) + webhooks / JSON polls
        state.cloud = CloudAccounts(state.controller)
        app.state.cloud = state.cloud
        state.oura = app.state.oura = state.cloud.get("oura")
        state.cloud.start()
        state.ha = HaBridge(state.controller, cfg.ha_mqtt_url or saved_ha.get("url"), cfg.ha_prefix if cfg.ha_mqtt_url else saved_ha.get("prefix", cfg.ha_prefix))
        app.state.ha = state.ha
        await state.ha.start()
        if not cfg.auth:
            log.warning("ET_AUTH=off: anyone who can reach this server can control the bed")
        await state.controller.tick()
        await state.controller.start()

        async def housekeeping():
            while True:
                try:
                    r = await _asyncio.to_thread(store.compact, clock.now_ts())
                    if r["samples_before"] or r["telemetry_removed"]:
                        log.info("database tidied: %s", r)
                except Exception:  # noqa: BLE001
                    log.exception("database housekeeping failed")
                await _asyncio.sleep(6 * 3600)
        housekeeping_task = _asyncio.get_running_loop().create_task(housekeeping())
        from . import trace as _trace                 # night trace for tuning (data dir/traces, ET_TRACE=off to stop)
        trace_task = _asyncio.get_running_loop().create_task(_trace.run(state.controller, cfg.data_dir))
        log.info("Ember & Tide %s · bed driver %s · tz %s", __version__, driver.id, cfg.tz)
        yield
        housekeeping_task.cancel()
        trace_task.cancel()
        await state.ha.stop()
        state.cloud.stop()
        state.notifier.stop()
        await state.controller.stop()
        await sources.stop()
        await driver.stop()

    app = FastAPI(title="Ember & Tide", version=__version__, lifespan=lifespan)

    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    @app.exception_handler(RequestValidationError)
    async def _invalid(_request, exc: RequestValidationError):
        """422 with the problems listed, but never echoing the input back: a NaN or a huge value in it would make the
        error itself unencodable (a 500), and echoing secrets is pointless anyway."""
        errs = [{"loc": [str(x) for x in e.get("loc", ())][:8], "msg": str(e.get("msg", ""))[:200], "type": str(e.get("type", ""))[:60]}
                for e in exc.errors()[:20]]
        return JSONResponse(status_code=422, content={"detail": errs})
    from .auth_api import AuthMiddleware, build_auth_router
    app.add_middleware(AuthMiddleware, state=state)
    app.add_middleware(BodyLimit)                         # outermost: refuse a huge body before anyone reads it
    from fastapi.middleware.cors import CORSMiddleware
    # The Android app's WebView origin. No cookies are used across origins: the app sends a bearer token.
    app.add_middleware(CORSMiddleware, allow_origins=["https://localhost", "http://localhost", "capacitor://localhost"],
                       allow_methods=["*"], allow_headers=["authorization", "content-type", "x-embertide", "x-embertide-client"],
                       allow_credentials=False, max_age=3600)
    app.include_router(build_auth_router(state))
    from .notify_api import build_notify_router
    app.include_router(build_notify_router(state))
    from .integrations_api import build_integrations_router
    app.include_router(build_integrations_router(state))
    from .autopilot_api import build_autopilot_router
    app.include_router(build_autopilot_router(state))
    app.include_router(build_router(state))
    from .studio_api import build_studio_router
    app.include_router(build_studio_router(state))
    from .ha_connection_api import build_ha_router
    app.include_router(build_ha_router(state))
    from .pets_api import build_pets_router
    app.include_router(build_pets_router(state))
    from .client_errors_api import build_client_errors_router
    app.include_router(build_client_errors_router(state))
    from .dials_api import build_dials_router                 # Studio: bedside dials seen through HA / Bluetooth
    app.include_router(build_dials_router(state))
    from .phone_api import build_phone_router                 # the Android app's "From this phone" reports
    app.include_router(build_phone_router(state))
    from .bed_api import build_bed_router                     # which way the bed names its sides (display only)
    app.include_router(build_bed_router(state))
    from .hub_api import build_hub_router                 # the control unit: health and maintenance (owner)
    app.include_router(build_hub_router(state))
    from .care_api import build_care_router               # maintenance reminders: purification tablets, water, checks
    app.include_router(build_care_router(state))
    from .identity_api import build_identity_router          # watch heart rate, pet questions, night corrections
    app.include_router(build_identity_router(state))
    from .oura_api import build_oura_router                   # Oura ring via its cloud (OAuth per person)
    app.include_router(build_oura_router(state))
    from .cloud_api import build_cloud_router
    app.include_router(build_cloud_router(state))
    from .assistant_api import build_assistant_router         # Ask: answers + proposed changes with confirm / undo
    app.include_router(build_assistant_router(state))

    @app.middleware("http")
    async def security_headers(request, call_next):
        """No framing by other sites (clickjacking), no MIME sniffing, no referrer leaks, HTTPS remembered."""
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Frame-Options", "DENY")
        if h.get("content-type", "").startswith("text/html"):
            host = request.headers.get("host", "")
            if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]{1,255}", host):    # only a plain host[:port] goes into the policy
                host = "localhost"
            # the app's own scripts only; Google Fonts for type; live updates over this server's own WebSocket
            h.setdefault("Content-Security-Policy", "; ".join((
                "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
                "font-src 'self' https://fonts.gstatic.com data:", "img-src 'self' data: blob:",
                # nosemgrep: python.django.security.injection.tainted-url-host.tainted-url-host, javascript.lang.security.detect-insecure-websocket.detect-insecure-websocket -- host is the sanitised request Host (ws:// is needed for plain-HTTP LAN installs), used only inside a CSP source list
                f"connect-src 'self' wss://{host} ws://{host}", "worker-src 'self'", "manifest-src 'self'",
                "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'")))
        else:
            h.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "same-origin")
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    @app.get("/healthz")
    async def healthz():
        out = {"ok": True, "version": __version__}
        # the app reached over plain http at home moves to the encrypted address (so the session isn't sent in the
        # clear on the home network), after checking that address answers with this same server's id
        try:
            store = state.controller.store
            sid = store.get("server_id")
            if not sid:
                import secrets
                sid = secrets.token_hex(8)
                store.put("server_id", sid)
            out["id"] = sid
            pub = state.notifier.public_url if getattr(state, "notifier", None) else None
            if pub and pub.startswith("https://"):
                out["secure_url"] = pub.rstrip("/")
        except Exception:  # noqa: BLE001  (still up even before the controller is ready)
            pass
        return out

    web = cfg.web_dir or Path(__file__).resolve().parents[2] / "web" / "dist"
    if web.exists():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        root = web.resolve()

        @app.get("/{path:path}")
        async def spa(path: str):
            # only files inside the built web app; "../" (also sent encoded as %2e%2e / %2F) never leaves it
            f = (root / path).resolve()
            if path and f.is_relative_to(root) and f.is_file():
                return FileResponse(f)
            return FileResponse(root / "index.html")
    return app
