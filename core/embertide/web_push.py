"""Web Push for the web app (an iPhone Home Screen web app, or a desktop/Android browser).

The server has one VAPID key pair, made on first use and kept in the data directory (`vapid-private.pem`, owner
read/write only, never in the repo). Browsers subscribe with the public half; each message is encrypted for that
browser (RFC 8291 aes128gcm, via pywebpush) and signed with the private half (RFC 8292), then posted to the browser
maker's push service (Apple, Google, Mozilla, Microsoft). Only those push services are accepted as endpoints, so a
signed-in account can't make this server post anywhere else.

iPhone: Web Push needs iOS 16.4+ and the site added to the Home Screen and opened from there.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

KEY_FILE = "vapid-private.pem"
# push services of the browsers people actually use (host suffixes)
PUSH_HOSTS = ("push.apple.com", "fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com",
              "notify.windows.com")
MAX_PAYLOAD = 3000          # push services take ~4 KB of ciphertext


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class WebPushKeys:
    """The server's VAPID key, created on first use in `data_dir`."""

    def __init__(self, data_dir: Path | None) -> None:
        self.dir = data_dir
        self._vapid = None

    @property
    def available(self) -> bool:
        return self.dir is not None

    def vapid(self):
        if self._vapid is not None:
            return self._vapid
        if self.dir is None:
            raise RuntimeError("no data directory for the Web Push key")
        from py_vapid import Vapid02
        f = self.dir / KEY_FILE
        if f.exists():
            v = Vapid02.from_file(str(f))
        else:
            v = Vapid02()
            v.generate_keys()
            self.dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(v.private_pem())
            log.info("made a new Web Push (VAPID) key in the data directory")
        self._vapid = v
        return v

    def public_key(self) -> str:
        """The applicationServerKey a browser subscribes with (uncompressed P-256 point, base64url)."""
        from cryptography.hazmat.primitives import serialization
        pub = self.vapid().public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return _b64u(pub)


def check_endpoint(endpoint: str) -> str | None:
    """None if a browser push endpoint is acceptable, else why not."""
    try:
        u = urlsplit(endpoint)
    except ValueError:
        return "That push address isn't valid."
    host = (u.hostname or "").lower()
    if u.scheme != "https" or u.port not in (None, 443) or u.username or u.password:
        return "Push addresses must be https."
    if not any(host == h or host.endswith("." + h) for h in PUSH_HOSTS):
        return "That push address isn't from a browser's push service."
    if len(endpoint) > 1000:
        return "That push address is too long."
    return None


def payload(m, url: str = "/", actions: list[dict] | None = None) -> bytes:
    """What the service worker shows: title, body, where a tap goes and (where the browser supports them) buttons."""
    d = {"t": "et", "v": 1, "kind": m.kind, "title": m.title[:120], "body": m.body[:600], "person": m.person,
         "tag": f"et-{m.kind}-{m.person or 'house'}", "url": url, "ts": int(time.time()),
         "urgent": m.priority >= 4, "actions": (actions or [])[:2]}
    raw = json.dumps(d, separators=(",", ":")).encode()
    if len(raw) > MAX_PAYLOAD:
        d["body"], d["actions"] = d["body"][:200], []
        raw = json.dumps(d, separators=(",", ":")).encode()
    return raw


def build(keys: WebPushKeys, sub: dict, data: bytes, contact: str, urgent: bool = False, ttl: int = 6 * 3600) -> tuple[str, dict, bytes]:
    """(url, headers, body) for one push: encrypted for that browser, signed with our VAPID key."""
    from pywebpush import WebPusher
    enc = WebPusher({"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}).encode(data, "aes128gcm")
    u = urlsplit(sub["endpoint"])
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": contact}
    headers = {**keys.vapid().sign(claims), "TTL": str(ttl), "Content-Encoding": "aes128gcm",
               "Content-Type": "application/octet-stream", "Urgency": "high" if urgent else "normal"}
    headers.pop("Crypto-Key", None)            # vapid (RFC 8292) puts the key in Authorization; Apple rejects extras
    return sub["endpoint"], headers, enc["body"]


def contact_for(public_url: str | None) -> str:
    """The VAPID 'sub' claim: Apple insists on a mailto: or https: URL."""
    if public_url and public_url.startswith("https://"):
        return public_url
    return "mailto:push@embertide.invalid"
