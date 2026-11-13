"""The push relay client: phone pushes for servers without their own Firebase key."""

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest

from embertide import fcm as fcm_mod
from embertide.notify import Message, Notifier
from test_notify import H, _fake_service_account, _phone_keys, make
from test_scenarios import Night

TOKEN = "r" * 160


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("ET_FCM_CREDENTIALS", "ET_PUSH_RELAY", "ET_PUSH_RELAY_URL"):
        monkeypatch.delenv(k, raising=False)


class Relay:
    """A stand-in relay: records requests, answers register and send."""

    def __init__(self, status="ok"):
        self.calls: list[httpx.Request] = []
        self.status = status
        self.registrations = 0

    def client(self):
        def handler(req: httpx.Request):
            self.calls.append(req)
            if req.url.path == "/v1/register":
                self.registrations += 1
                return httpx.Response(200, json={"install_id": "inst-abcdefghij", "install_key": "key-secret-value"})
            body = json.loads(req.content)
            return httpx.Response(200, json={"results": [{"token_suffix": t[-8:], "status": self.status} for t in body["tokens"]]})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def sends(self):
        return [c for c in self.calls if c.url.path == "/v1/send"]


def _night(tmp_path, relay: Relay, with_key=False):
    n = Night(tmp_path, "2026-11-05T21:00")
    n.c.notifier = Notifier(n.c, None, client=relay.client())
    n.c.notifier.fcm.data_dir = tmp_path
    if with_key:
        _fake_service_account(tmp_path)
    _, pub, auth = _phone_keys()
    n.c.notifier.add_device(n.p1.id, TOKEN, pub, auth, "Pixel", kind="fcm")
    return n


async def _push(n):
    return await n.c.notifier.push(n.p1.id, n.c.notifier.devices(n.p1.id)[0], Message("test", "Hello", "body", n.p1.id))


async def test_registration_is_stored_and_reused(tmp_path):
    relay = Relay()
    n = _night(tmp_path, relay)
    assert await _push(n) and await _push(n)
    assert relay.registrations == 1 and len(relay.sends()) == 2
    saved = n.c.store.get("push_relay")
    assert saved["install_id"] == "inst-abcdefghij" and saved["install_key"] == "key-secret-value"


async def test_request_is_signed_and_carries_only_ciphertext(tmp_path):
    relay = Relay()
    n = _night(tmp_path, relay)
    assert await _push(n)
    req = relay.sends()[0]
    assert str(req.url) == "https://embertide.homepush.dev/v1/send"
    assert req.headers["x-signature"] == hmac.new(b"key-secret-value", req.content, hashlib.sha256).hexdigest()
    body = json.loads(req.content)
    assert body["install_id"] == "inst-abcdefghij" and body["tokens"] == [TOKEN] and body["urgent"] is True and body["ios"] is False
    assert abs(body["ts"] - __import__("time").time()) < 60 and set(body["data"]) == {"e"}
    assert "Hello" not in req.content.decode()                       # the title is inside the encrypted payload
    assert "key-secret-value" not in req.content.decode()            # the key signs, it is never sent


async def test_own_firebase_key_is_preferred(tmp_path):
    relay = Relay()
    n = _night(tmp_path, relay, with_key=True)
    assert n.c.notifier.push_path() == "direct"
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if "oauth2" in str(req.url):
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600})
        return httpx.Response(200, json={})
    n.c.notifier.fcm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await _push(n)
    assert relay.calls == [] and any("fcm.googleapis.com" in u for u in seen)


async def test_relay_used_without_a_key_and_off_switch(tmp_path, monkeypatch):
    relay = Relay()
    n = _night(tmp_path, relay)
    assert n.c.notifier.push_path() == "relay" and n.c.notifier.fcm_ready
    monkeypatch.setenv("ET_PUSH_RELAY", "off")
    assert n.c.notifier.push_path() == "off" and not n.c.notifier.fcm_ready
    assert not await _push(n)
    assert relay.calls == []                                         # off means nothing is sent anywhere
    monkeypatch.setenv("ET_PUSH_RELAY", "on")
    monkeypatch.setenv("ET_PUSH_RELAY_URL", "https://my-relay.example/")
    assert await _push(n)
    assert relay.registrations == 1 and str(relay.sends()[0].url) == "https://my-relay.example/v1/send"


async def test_gone_result_drops_the_device(tmp_path):
    relay = Relay(status="gone")
    n = _night(tmp_path, relay)
    assert not await _push(n)
    assert n.c.notifier.devices(n.p1.id) == []


async def test_relay_error_keeps_the_device(tmp_path):
    relay = Relay(status="error")
    n = _night(tmp_path, relay)
    assert not await _push(n)
    assert len(n.c.notifier.devices(n.p1.id)) == 1


async def test_forgotten_install_registers_again(tmp_path):
    n = _night(tmp_path, Relay())
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        if req.url.path == "/v1/register":
            return httpx.Response(200, json={"install_id": f"inst-{len(calls):06d}xx", "install_key": "k"})
        return httpx.Response(401, json={"error": "unauthorized"}) if calls.count("/v1/send") == 1 else httpx.Response(200, json={"results": [{"status": "ok"}]})
    n.c.notifier.relay.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert not await _push(n)                                        # 401: the saved install is dropped
    assert n.c.store.get("push_relay") is None
    assert await _push(n) and calls.count("/v1/register") == 2


def test_message_builder_matches_the_shared_fixture():
    """relay/test/fixtures/message.json is read by the Worker's tests too, so the two builders cannot drift apart."""
    fixture = Path(__file__).resolve().parents[2] / "relay" / "test" / "fixtures" / "message.json"
    for case in json.loads(fixture.read_text(encoding="utf-8")):
        i = case["input"]
        assert fcm_mod.build_message(i["token"], i["e"], i["urgent"], i["ios"], now=i["now"]) == case["expected"], case["name"]


def test_install_key_never_appears_in_any_api_response(tmp_path):
    secret = "key-secret-value"
    with make(tmp_path) as c:
        c.app.state.controller.store.put("push_relay", {"install_id": "inst-abcdefghij", "install_key": secret, "url": "x"}) \
            if hasattr(c.app.state, "controller") else None
        _, pub, auth = _phone_keys()
        c.post("/api/notify/me/push", json={"kind": "fcm", "endpoint": "a" * 160, "p256dh": pub, "auth": auth, "device": "Pixel", "person": "p1"}, headers=H)
        for path, params in (("/api/notify/settings", None), ("/api/notify/me/push", {"person": "p1"}), ("/api/notify/providers", None), ("/api/notify/log", None)):
            r = c.get(path, params=params)
            assert secret not in r.text and "install_key" not in r.text and "inst-abcdefghij" not in r.text, path
        s = c.get("/api/notify/settings").json()
        assert s["fcm"] is True and s["push_path"] == "relay"        # the app turns Google push on by itself


def test_settings_say_which_path_is_used(tmp_path, monkeypatch):
    with make(tmp_path) as c:
        assert c.get("/api/notify/settings").json()["push_path"] == "relay"
        monkeypatch.setenv("ET_PUSH_RELAY", "off")
        s = c.get("/api/notify/settings").json()
        assert s["push_path"] == "off" and s["fcm"] is False
        _, pub, auth = _phone_keys()
        r = c.post("/api/notify/me/push", json={"kind": "fcm", "endpoint": "a" * 160, "p256dh": pub, "auth": auth, "person": "p1"}, headers=H)
        assert r.status_code == 422
        _fake_service_account(tmp_path)
        assert c.get("/api/notify/settings").json()["push_path"] == "direct"
