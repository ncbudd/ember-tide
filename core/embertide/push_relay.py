"""Client for the Ember & Tide push relay (relay/): phone pushes for servers that have no Firebase key of their own.

The payload is already end-to-end encrypted for each phone before it gets here (see notify.py), so the relay only
forwards ciphertext. We register once, keep the install id and key in the store, and sign every request with the key.
The key never leaves the server: no API returns it and nothing logs it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

import httpx

from . import config

log = logging.getLogger(__name__)
KV_KEY = "push_relay"
MAX_TOKENS = 10   # the relay's own limit per request


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class PushRelay:
    def __init__(self, store, client: httpx.AsyncClient | None = None, demo: bool = False):
        self.store = store
        self.client = client
        self.demo = demo

    @property
    def enabled(self) -> bool:
        return not self.demo and config.push_relay_enabled()

    @property
    def url(self) -> str:
        return config.push_relay_url()

    def _http(self) -> httpx.AsyncClient:
        return self.client or httpx.AsyncClient(timeout=15)

    async def _install(self, http: httpx.AsyncClient) -> dict:
        """The saved {install_id, install_key, url}; registers on first use (or when the relay address changed)."""
        assert not self.demo, "push relay is disabled in demo mode"
        saved = self.store.get(KV_KEY)
        if saved and saved.get("install_id") and saved.get("install_key") and saved.get("url") == self.url:
            return saved
        r = await http.post(f"{self.url}/v1/register", json={})
        r.raise_for_status()
        j = r.json()
        saved = {"install_id": j["install_id"], "install_key": j["install_key"], "url": self.url}
        self.store.put(KV_KEY, saved)
        log.info("push relay: registered this server")
        return saved

    @staticmethod
    def sign(key: str, body: bytes) -> str:
        return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()

    async def send_many(self, tokens: list[str], encrypted: bytes, urgent: bool, ios: bool = False) -> dict[str, str]:
        """{token: "ok" | "gone" | "error"}. Raises if the relay itself can't be reached or refuses us."""
        assert not self.demo, "push relay is disabled in demo mode"
        out: dict[str, str] = {}
        http = self._http()
        try:
            for i in range(0, len(tokens), MAX_TOKENS):
                chunk = tokens[i:i + MAX_TOKENS]
                inst = await self._install(http)
                body = json.dumps({"install_id": inst["install_id"], "ts": int(time.time()), "tokens": chunk,
                                   "data": {"e": _b64u(encrypted)}, "urgent": urgent, "ios": ios},
                                  separators=(",", ":")).encode()
                r = await http.post(f"{self.url}/v1/send", content=body,
                                    headers={"Content-Type": "application/json", "X-Signature": self.sign(inst["install_key"], body)})
                if r.status_code in (401, 403):
                    self.store.put(KV_KEY, None)       # the relay forgot us (or the key rotated): register afresh next time
                r.raise_for_status()
                # the relay answers in the order we asked; it only ever shows the last 8 characters of a token
                for tok, res in zip(chunk, r.json().get("results", [])):
                    out[tok] = res.get("status", "error") if res.get("status") in ("ok", "gone") else "error"
        finally:
            if http is not self.client:
                await http.aclose()
        return out

    async def send(self, token: str, encrypted: bytes, urgent: bool, ios: bool = False) -> str:
        """Same contract as Fcm.send: "ok", "gone", or raises."""
        res = (await self.send_many([token], encrypted, urgent, ios)).get(token, "error")
        if res == "error":
            raise RuntimeError("the push relay couldn't deliver the message")
        return res
