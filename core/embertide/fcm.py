"""Firebase Cloud Messaging (HTTP v1) for the phone apps: Android now, iPhone later (FCM hands those to Apple's push).

The owner creates a free Firebase project and puts its service-account file on the server (ET_FCM_CREDENTIALS, or
fcm-service-account.json in the data folder). We sign a short-lived token with it, swap it for an access token, and
send data messages. The payload is Web Push-encrypted for each phone first, so Google only ever carries ciphertext.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

log = logging.getLogger(__name__)
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Fcm:
    def __init__(self, data_dir: Path, client: httpx.AsyncClient | None = None):
        self.data_dir = Path(data_dir)
        self.client = client
        self._creds: dict | None = None
        self._creds_mtime = 0.0
        self._token: tuple[str, float] | None = None

    def _path(self) -> Path:
        return Path(os.environ.get("ET_FCM_CREDENTIALS") or self.data_dir / "fcm-service-account.json")

    def creds(self) -> dict | None:
        p = self._path()
        try:
            m = p.stat().st_mtime
        except OSError:
            self._creds = None
            return None
        if self._creds is None or m != self._creds_mtime:
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
                if c.get("type") != "service_account" or not c.get("private_key") or not c.get("client_email") or not c.get("project_id"):
                    log.warning("fcm: %s isn't a Firebase service-account file", p)
                    return None
                self._creds, self._creds_mtime, self._token = c, m, None
            except Exception as e:  # noqa: BLE001
                log.warning("fcm: can't read %s: %s", p, e)
                return None
        return self._creds

    @property
    def configured(self) -> bool:
        return self.creds() is not None

    @property
    def project(self) -> str | None:
        c = self.creds()
        return c["project_id"] if c else None

    def _assertion(self, c: dict, now: int) -> str:
        head = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claim = _b64u(json.dumps({"iss": c["client_email"], "scope": SCOPE, "aud": c.get("token_uri", "https://oauth2.googleapis.com/token"),
                                  "iat": now, "exp": now + 3600}).encode())
        key = serialization.load_pem_private_key(c["private_key"].encode(), password=None)
        sig = key.sign(f"{head}.{claim}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{claim}.{_b64u(sig)}"

    async def _access_token(self, http: httpx.AsyncClient) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        c = self.creds()
        if not c:
            raise RuntimeError("FCM isn't set up on this server")
        r = await http.post(c.get("token_uri", "https://oauth2.googleapis.com/token"),
                            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": self._assertion(c, int(time.time()))})
        r.raise_for_status()
        j = r.json()
        self._token = (j["access_token"], time.time() + int(j.get("expires_in", 3600)))
        return self._token[0]

    @staticmethod
    def message(token: str, encrypted: bytes, urgent: bool, ios: bool = False) -> dict:
        """The FCM v1 message. Android gets a data message (the app decrypts and shows it). iPhones don't reliably wake
        for data-only pushes, so they get a placeholder alert with mutable-content: the app's notification service
        extension decrypts the payload and replaces the text and buttons before anything is shown."""
        msg = {"token": token, "data": {"e": _b64u(encrypted)},
               "android": {"priority": "high" if urgent else "normal", "ttl": "21600s"}}
        if ios:
            msg["apns"] = {"headers": {"apns-priority": "10" if urgent else "5", "apns-push-type": "alert",
                                       "apns-expiration": str(int(time.time()) + 21600)},
                           "payload": {"aps": {"alert": {"title": "Ember & Tide", "body": "New message"},
                                               "mutable-content": 1, "sound": "default"}}}
        return {"message": msg}

    async def send(self, token: str, encrypted: bytes, urgent: bool, ios: bool = False) -> str:
        """Returns "ok", "gone" (the phone's registration no longer exists) or raises."""
        http = self.client or httpx.AsyncClient(timeout=10)
        try:
            at = await self._access_token(http)
            msg = self.message(token, encrypted, urgent, ios)
            r = await http.post(f"https://fcm.googleapis.com/v1/projects/{self.project}/messages:send", json=msg,
                                headers={"Authorization": f"Bearer {at}"})
            if r.status_code == 404 or (r.status_code == 400 and "UNREGISTERED" in r.text) or "registration-token-not-registered" in r.text:
                return "gone"
            # a token Google calls invalid, or one that belongs to another Firebase project, never works again: drop it
            # (a bad payload is also a 400, so that needs the word "token" in Google's own message)
            if (r.status_code == 400 and "INVALID_ARGUMENT" in r.text and "registration token" in r.text) or \
               (r.status_code == 403 and "SENDER_ID_MISMATCH" in r.text):
                return "gone"
            if r.status_code == 401:
                self._token = None
            r.raise_for_status()
            return "ok"
        finally:
            if http is not self.client:
                await http.aclose()
