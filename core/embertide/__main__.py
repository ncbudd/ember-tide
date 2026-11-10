import logging

import uvicorn

from .app import create_app
from .config import Config


def reset_password(username: str) -> None:
    """Locked out? Run `python -m embertide reset-password <username>` on the server (or `docker exec … `)
    to get a one-time link that sets a new password."""
    from .auth import Auth
    from .store import Store
    cfg = Config()
    a = Auth(Store(cfg.db_path))
    u = next((x for x in a.users() if x["username"].lower() == username.lower()), None)
    if not u:
        names = ", ".join(x["username"] for x in a.users()) or "none yet"
        raise SystemExit(f"No account called {username!r}. Accounts: {names}")
    code = a.new_reset(u["id"], "cli")
    print(f"Open this within 24 hours to set a new password for {u['username']}:\n  http://<your-server>:{cfg.port}/join#{code}")


class RedactTokens(logging.Filter):
    """The phone app opens the live connection as /api/live?ticket=… (older builds: ?token=…); uvicorn logs that path on
    every connect. Never let a working sign-in token or live ticket reach the logs."""
    import re as _re
    RX = _re.compile(r"((?:token|ticket)=)[^&\s\"']+", _re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        low = msg.lower()
        if "token=" in low or "ticket=" in low:
            record.msg, record.args = self.RX.sub(lambda m: m.group(1) + "***", msg), ()
        return True


def redact_uvicorn_logs() -> None:
    f = RedactTokens()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.addFilter(f)
        for h in lg.handlers:
            h.addFilter(f)


def main() -> None:
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "reset-password":
        return reset_password(sys.argv[2])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx/httpcore log every request URL at INFO; those URLs can carry secrets (calendar feed links, HA addresses
    # with tokens in query strings), so keep them to warnings
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = Config()
    loop = "auto"
    if sys.platform == "win32":
        # MQTT (paho via aiomqtt) needs add_reader(), which Windows' default Proactor loop lacks
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = "none"
    app = create_app(cfg)
    config = uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="info", loop=loop)
    server = uvicorn.Server(config)                              # (Config sets up uvicorn's loggers; filter after it)
    redact_uvicorn_logs()
    server.run()


if __name__ == "__main__":
    main()
