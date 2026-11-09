"""Web Push for the web app (iPhone Home Screen app, browsers): VAPID key, subscribe endpoint, payload, delivery."""

import base64
import json
import os
import stat

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient

from embertide import web_push, webpush
from embertide.app import create_app
from embertide.config import Config
from embertide.notify import Action, Message

H = {"X-Embertide": "1"}
APPLE = "https://web.push.apple.com/QGxPdxd0kRt3f2xZ0Pb7aQ0x_example-token"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def browser():
    """A browser's subscription keys (what PushManager.subscribe hands the page)."""
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return k, b64u(pub), b64u(os.urandom(16))


def client(tmp_path, auth=False):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, auth
    cfg.public_url = "https://bed.example.org"
    return TestClient(create_app(cfg))


class PushService:
    def __init__(self, status=201, headers=None):
        self.calls: list[httpx.Request] = []
        self.status, self.headers = status, headers or {}

    def client(self):
        def handler(req: httpx.Request):
            self.calls.append(req)
            return httpx.Response(self.status, headers=self.headers)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_vapid_key_made_once_in_the_data_dir(tmp_path):
    k1 = web_push.WebPushKeys(tmp_path).public_key()
    f = tmp_path / web_push.KEY_FILE
    assert f.exists() and b"PRIVATE KEY" in f.read_bytes()
    if os.name == "posix":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert web_push.WebPushKeys(tmp_path).public_key() == k1          # reused, not remade
    assert len(unb64u(k1)) == 65 and unb64u(k1)[0] == 4
    assert not web_push.WebPushKeys(None).available


def test_endpoint_must_be_a_browser_push_service():
    ok = ["https://web.push.apple.com/abc", "https://fcm.googleapis.com/fcm/send/abc",
          "https://updates.push.services.mozilla.com/wpush/v2/abc", "https://wns2-par02p.notify.windows.com/w/?token=x"]
    bad = ["http://web.push.apple.com/abc", "https://evil.example/push", "https://push.apple.com.evil.example/x",
           "https://user:pw@web.push.apple.com/x", "https://web.push.apple.com:8443/x", "https://192.168.1.10/x"]
    assert all(web_push.check_endpoint(u) is None for u in ok)
    assert all(web_push.check_endpoint(u) for u in bad)


def test_subscribe_endpoint(tmp_path):
    _, pub, auth = browser()
    with client(tmp_path) as c:
        info = c.get("/api/notify/me/webpush?person=p2").json()
        assert info["available"] and len(unb64u(info["key"])) == 65 and info["devices"] == []
        bad = c.post("/api/notify/me/webpush", json={"endpoint": "https://evil.example/x", "p256dh": pub, "auth": auth, "person": "p2"}, headers=H)
        assert bad.status_code == 422
        bad = c.post("/api/notify/me/webpush", json={"endpoint": APPLE, "p256dh": pub[:-4] + "AAAA", "auth": auth, "person": "p2"}, headers=H)
        assert bad.status_code == 422
        assert c.post("/api/notify/me/webpush", json={"endpoint": APPLE, "p256dh": pub, "auth": auth, "person": "nobody"}, headers=H).status_code == 403
        r = c.post("/api/notify/me/webpush", json={"endpoint": APPLE, "p256dh": pub, "auth": auth, "platform": "ios", "person": "p2"}, headers=H)
        assert r.status_code == 200, r.text
        # subscribing again (iOS hands out the same endpoint) replaces, never duplicates
        c.post("/api/notify/me/webpush", json={"endpoint": APPLE, "p256dh": pub, "auth": auth, "platform": "ios", "person": "p2"}, headers=H)
        devs = c.get("/api/notify/me/webpush?person=p2").json()["devices"]
        assert len(devs) == 1 and devs[0]["device"] == "iPhone" and devs[0]["platform"] == "ios"
        assert c.get("/api/notify/me/webpush?person=p1").json()["devices"] == []
        assert c.post("/api/notify/me/push/remove", json={"id": devs[0]["id"], "person": "p2"}, headers=H).json()["ok"]
        assert c.get("/api/notify/me/webpush?person=p2").json()["devices"] == []


def test_subscribe_is_per_signed_in_person(tmp_path):
    _, pub, auth = browser()
    with client(tmp_path, auth=True) as c:
        assert c.post("/api/auth/setup", json={"username": "alex", "password": "correct horse battery", "person": "p1"}).status_code == 200
        # the body's person is ignored when signed in: it's always your own side
        r = c.post("/api/notify/me/webpush", json={"endpoint": APPLE, "p256dh": pub, "auth": auth, "person": "p2"}, headers=H)
        assert r.status_code == 200
        n = c.app.state.notifier
        assert [d["kind"] for d in n.devices("p1")] == ["web"] and n.devices("p2") == []
        anon = TestClient(c.app)
        assert anon.post("/api/notify/me/webpush", json={"endpoint": APPLE, "p256dh": pub, "auth": auth}, headers=H).status_code == 401


def _verify_vapid(req: httpx.Request, key_b64: str):
    a = req.headers["authorization"]
    assert a.startswith("vapid t=")
    t, k = a[len("vapid t="):].split(",k=")
    assert k == key_b64
    head, body, sig = t.split(".")
    claims = json.loads(unb64u(body))
    assert claims["aud"] == "https://web.push.apple.com" and claims["sub"] == "https://bed.example.org"
    raw = unb64u(sig)
    pk = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(key_b64))
    pk.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
              f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))           # raises if not signed by our key


def test_payload_is_encrypted_for_the_browser_and_signed(tmp_path):
    priv, pub, auth = browser()
    svc = PushService()
    with client(tmp_path) as c:
        key = c.get("/api/notify/me/webpush?person=p2").json()["key"]
        c.post("/api/notify/me/webpush", json={"endpoint": APPLE, "p256dh": pub, "auth": auth, "platform": "ios", "person": "p2"}, headers=H)
        c.app.state.notifier.client = svc.client()
        assert c.post("/api/notify/me/push/test?person=p2", headers=H).status_code == 200
    req = svc.calls[0]
    assert str(req.url) == APPLE
    assert req.headers["content-encoding"] == "aes128gcm" and int(req.headers["ttl"]) > 0
    assert "crypto-key" not in req.headers
    _verify_vapid(req, key)
    msg = json.loads(webpush.decrypt(req.content, priv, auth))
    assert msg["title"] == "Notifications work" and msg["person"] == "p2" and msg["url"] == "/"
    assert b"Notifications work" not in req.content                    # the push service only sees ciphertext


def test_payload_shape_and_buttons(tmp_path):
    m = Message("left_on", "Your side is still on", "Nobody's been in it for 20 minutes.", "p1",
                [Action("Turn off", "manual_off"), Action("Keep on", "noop")], priority=4)
    d = json.loads(web_push.payload(m, "/", [{"action": "a0", "title": "Turn off", "url": "https://x/act#c"}]))
    assert d["tag"] == "et-left_on-p1" and d["urgent"] and d["actions"][0]["url"].endswith("#c")
    big = Message("x", "t", "b" * 5000, None)
    raw = web_push.payload(big, "/", [{"action": "a0", "title": "x" * 50, "url": "u"}])
    assert len(raw) <= web_push.MAX_PAYLOAD and len(json.loads(raw)["body"]) <= 600


def test_real_events_reach_web_push_alongside_app_push(tmp_path):
    """The same notify path: a message for p2 goes to her iPhone web app and to any native app phone."""
    priv, pub, auth = browser()
    svc = PushService()
    with client(tmp_path) as c:
        n = c.app.state.notifier
        c.post("/api/notify/me/webpush", json={"endpoint": APPLE, "p256dh": pub, "auth": auth, "platform": "ios", "person": "p2"}, headers=H)
        n.client = svc.client()
        import asyncio
        m = Message("left_on", "Right's side is still on", "Nobody's in it.", "p2", [Action("Turn off", "manual_off")], priority=4)
        async def go():
            return await asyncio.gather(*(n.push(pid, d, m) for pid, d in n.push_targets(m)))
        results = asyncio.run(go())
        assert results == [True]
    body = json.loads(webpush.decrypt(svc.calls[0].content, priv, auth))
    assert body["kind"] == "left_on" and body["actions"][0]["title"] == "Turn off"
    assert body["actions"][0]["url"].startswith("https://bed.example.org/act#")
    assert svc.calls[0].headers["urgency"] == "high"


def test_gone_subscription_removed_and_rate_limit_logged(tmp_path):
    _, pub, auth = browser()
    with client(tmp_path) as c:
        n = c.app.state.notifier
        c.post("/api/notify/me/webpush", json={"endpoint": APPLE, "p256dh": pub, "auth": auth, "person": "p2"}, headers=H)
        n.client = PushService(429, {"retry-after": "30"}).client()
        assert c.post("/api/notify/me/push/test?person=p2", headers=H).status_code == 502
        assert "busy" in n.sent_log[-1]["error"] and n.devices("p2")              # kept: try again later
        n.client = PushService(410).client()
        c.post("/api/notify/me/push/test?person=p2", headers=H)
        assert n.devices("p2") == []                                              # unsubscribed in the browser
