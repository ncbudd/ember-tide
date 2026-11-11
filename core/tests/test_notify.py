"""Notifications: triggers fire once, go to the right phone, and buttons work exactly once."""

import json

import httpx
from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config
from embertide.notify import Action, Message, Notifier
from test_scenarios import Night

H = {"X-Embertide": "1"}


class Fake:
    """Records what would have gone to ntfy / Home Assistant."""

    def __init__(self, status=200):
        self.calls: list[dict] = []
        self.status = status

    def client(self):
        def handler(req: httpx.Request):
            self.calls.append({"url": str(req.url), "json": json.loads(req.content or b"{}"), "auth": req.headers.get("authorization")})
            return httpx.Response(self.status, json={})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def providers():
    return [
        {"id": "n1", "kind": "ntfy", "name": "Left phone", "person": "p1", "enabled": True,
         "settings": {"url": "https://ntfy.example", "topic": "alex-bed", "token": "tk_x"}},
        {"id": "n2", "kind": "ha", "name": "Right phone", "person": "p2", "enabled": True,
         "settings": {"url": "http://ha.local:8123", "token": "ha_tok", "service": "notify.mobile_app_sam"}},
        {"id": "n3", "kind": "ntfy", "name": "House", "person": None, "enabled": True,
         "settings": {"url": "https://ntfy.example", "topic": "house", "token": None}},
    ]


async def test_away_at_precool_notifies_once_with_buttons(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    fake = Fake()
    n.c.notifier = Notifier(n.c, "https://bed.example", client=fake.client())
    n.c.notifier.save_providers(providers())
    n.c.home[n.p1.id] = False
    await n.until("2026-11-05T21:50")
    for t in list(n.c.notifier._tasks):
        await t
    to_alex = [c for c in fake.calls if c["json"].get("topic") == "alex-bed"]
    assert len(to_alex) == 1                                     # once, not every tick
    body = to_alex[0]["json"]
    assert "not home" in body["title"] and to_alex[0]["auth"] == "Bearer tk_x"
    labels = [a["label"] for a in body["actions"]]
    assert labels == ["Wait an hour", "Off tonight", "Start anyway"]
    assert all(a["url"].startswith("https://bed.example/api/notify/act/") and "tk_" not in a["url"] for a in body["actions"])
    assert not [c for c in fake.calls if "mobile_app_sam" in c["url"]]   # Sam is home: nothing for Sam


async def test_household_problem_goes_to_household_target(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    fake = Fake()
    n.c.notifier = Notifier(n.c, None, client=fake.client())
    n.c.notifier.save_providers(providers())
    snap = n.c.snapshot()
    snap["system"]["online"] = False
    snap["system"]["detail"] = "no broker"
    now = n.clock.now_ts()
    assert n.c.notifier.observe(now, snap) == []
    out = n.c.notifier.observe(now + 301, snap)
    assert [m.kind for m in out] == ["problem"]
    for t in list(n.c.notifier._tasks):
        await t
    assert [c["json"]["topic"] for c in fake.calls] == ["house"]
    assert n.c.notifier.observe(now + 900, snap) == []          # not again while it stays offline


async def test_ha_message_shape_and_link_buttons(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    fake = Fake()
    nt = Notifier(n.c, "https://bed.example", client=fake.client())
    p = providers()[1]
    r = await nt.deliver(p, Message("empty_side", "t", "b", "p2", [Action("Off tonight", "skip")]))
    assert r["ok"]
    call = fake.calls[0]
    assert call["url"] == "http://ha.local:8123/api/services/notify/mobile_app_sam" and call["auth"] == "Bearer ha_tok"
    act = call["json"]["data"]["actions"][0]
    assert act["action"] == "URI" and act["uri"].startswith("https://bed.example/act#")


async def test_plain_language_delivery_errors(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    nt = Notifier(n.c, None, client=Fake(status=401).client())
    r = await nt.deliver(providers()[0], Message("test", "t", "b", "p1"))
    assert not r["ok"] and "token" in r["error"]


def make(tmp_path, auth=False):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.llm_url, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", None, auth
    return TestClient(create_app(cfg))


def test_buttons_work_once_and_without_sign_in(tmp_path):
    with make(tmp_path, auth=True) as c:
        c.post("/api/auth/setup", json={"username": "alex", "password": "correct horse battery", "person": "p1"})
        from embertide.notify import Action as A
        stranger = TestClient(c.app)
        code = c.app.state.notifier.new_action("p1", A("Off tonight", "skip"))
        assert stranger.get(f"/api/notify/act/{code}").json()["person_name"] == "Left"
        assert stranger.post(f"/api/notify/act/{code}").status_code == 200          # no cookie, no header: the code is the key
        # "off tonight": the night now, or (in the morning, after it ended) the next one; both count
        e = c.app.state.notifier.c.engine_for("p1")
        snap_skip = c.get("/api/snapshot").json()["people"][0]["night"]["skip"]
        assert snap_skip is True or e.s.skip_next is True
        assert stranger.post(f"/api/notify/act/{code}").status_code == 404          # once
        assert stranger.post("/api/notify/act/made-up-code").status_code == 404
        assert stranger.get("/api/notify/providers").status_code == 401             # the rest stays locked


def test_provider_api_hides_tokens_and_validates(tmp_path):
    with make(tmp_path) as c:
        assert c.post("/api/notify/providers", json={"kind": "ntfy", "name": "x", "settings": {"topic": ""}}, headers=H).status_code == 422
        assert c.post("/api/notify/providers", json={"kind": "ha", "name": "x", "settings": {"url": "ha.local", "token": "t", "service": "notify.x"}}, headers=H).status_code == 422
        r = c.post("/api/notify/providers", json={"kind": "ntfy", "name": "Pixel", "person": "p1", "settings": {"topic": "abc", "token": "tk_secret123"}}, headers=H)
        assert r.status_code == 200 and "tk_secret123" not in r.text
        p = c.get("/api/notify/providers").json()[0]
        assert p["settings"]["token"].startswith("••••")
        # saving it back unchanged keeps the real token
        assert c.put(f"/api/notify/providers/{p['id']}", json={**p, "enabled": False}, headers=H).status_code == 200
        assert c.app.state.notifier.providers[0]["settings"]["token"] == "tk_secret123"
        assert c.put("/api/notify/settings", json={"public_url": "bed.example"}, headers=H).status_code == 422
        assert c.put("/api/notify/settings", json={"public_url": "https://bed.example/"}, headers=H).json()["public_url"] == "https://bed.example"


async def test_home_assistant_one_tap_buttons(tmp_path):
    """A fake Home Assistant websocket: auth, subscribe, then a button press event comes in and is applied."""
    import asyncio

    import websockets
    from embertide.notify import HaActionListener  # noqa: F401
    n = Night(tmp_path, "2026-11-05T21:00")
    nt = Notifier(n.c, None, client=Fake().client())
    pressed = asyncio.Event()
    code_box = {}

    async def fake_ha(ws):
        await ws.send(json.dumps({"type": "auth_required"}))
        auth = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "auth_ok" if auth["access_token"] == "good" else "auth_invalid"}))
        sub = json.loads(await ws.recv())
        assert sub["event_type"] == "mobile_app_notification_action"
        await pressed.wait()
        await ws.send(json.dumps({"type": "event", "event": {"data": {"action": f"ET_{code_box['code']}"}}}))
        await asyncio.sleep(0.5)

    async with websockets.serve(fake_ha, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        nt.save_providers([{"id": "h", "kind": "ha", "name": "HA", "person": "p1", "enabled": True,
                            "settings": {"url": f"http://127.0.0.1:{port}", "token": "good", "service": "notify.x"}}])
        nt.sync_ha_listeners()
        lis = nt.ha_listeners[f"http://127.0.0.1:{port}"]
        for _ in range(50):
            if lis.connected:
                break
            await asyncio.sleep(0.05)
        assert lis.connected
        fake = Fake()
        nt.client = fake.client()
        await nt.deliver(nt.providers[0], Message("away_precool", "t", "b", "p1", [Action("Off tonight", "skip")]))
        act = fake.calls[0]["json"]["data"]["actions"][0]
        assert act["action"].startswith("ET_") and "uri" not in act          # a real one-tap button
        code_box["code"] = act["action"][3:]
        pressed.set()
        for _ in range(50):
            if n.e(n.p1).s.skip:
                break
            await asyncio.sleep(0.05)
        assert n.e(n.p1).s.skip
        nt.stop()


async def test_left_on_by_hand_and_empty_asks_to_turn_it_off(tmp_path):
    n = Night(tmp_path, "2026-11-06T14:00")                     # afternoon: the rhythm has the side off
    fake = Fake()
    n.c.notifier = Notifier(n.c, "https://bed.example", client=fake.client())
    n.c.notifier.save_providers(providers())
    await n.until("2026-11-06T14:01")
    e = n.c.engine_for(n.p1.id)
    now = n.clock.now_ts()
    e.adjust(now, temp_c=20.0, minutes=90)                      # a nap
    await n.until("2026-11-06T14:02")
    snap = n.c.snapshot()
    me = next(p for p in snap["people"] if p["id"] == n.p1.id)
    assert me["night"]["override"]["manual"] and me["night"]["target_c"] is not None
    me["presence"]["present"] = False
    n.c.notifier._empty_since.clear()                           # (the live ticks above already started the clock)
    t = n.clock.now_ts()
    assert n.c.notifier.observe(t, snap) == []
    assert n.c.notifier.observe(t + 19 * 60, snap) == []        # not yet
    out = n.c.notifier.observe(t + 21 * 60, snap)
    assert [m.kind for m in out] == ["left_on"]
    assert n.c.notifier.observe(t + 30 * 60, snap) == []        # once per session
    me["presence"]["present"] = None                            # unknown presence never counts as empty
    n.c.notifier._empty_since.clear()
    assert n.c.notifier.observe(t + 90 * 60, snap) == []
    for task in list(n.c.notifier._tasks):
        await task
    call = next(c for c in fake.calls if c["json"].get("topic") == "alex-bed")
    assert [a["label"] for a in call["json"]["actions"]] == ["Turn it off"]
    code = call["json"]["actions"][0]["url"].rsplit("/", 1)[-1]
    used = n.c.notifier.use_action(code)
    assert used and used["done"].endswith("side is off")
    assert e.s.override is None


# ---------- app notifications (UnifiedPush + Web Push encryption) ----------
def _phone_keys():
    import base64, os
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()   # noqa: E731
    return k, b(pub), b(os.urandom(16))


class RawFake:
    """Records raw POSTs (encrypted push bodies) and answers with a fixed status."""
    def __init__(self, status=200):
        self.calls, self.status = [], status

    def client(self):
        def handler(req: httpx.Request):
            self.calls.append({"url": str(req.url), "body": req.content, "headers": dict(req.headers)})
            return httpx.Response(self.status, json={})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _push_setup(tmp_path, status=200, token=None):
    n = Night(tmp_path, "2026-11-05T21:00")
    fake = RawFake(status)
    n.c.notifier = Notifier(n.c, "https://bed.example", client=fake.client())
    n.c.store.put("notify_settings", {"push_server": "https://ntfy.example", "push_token": token})
    return n, fake


def test_push_address_must_be_on_the_owners_push_server(tmp_path):
    n, _ = _push_setup(tmp_path)
    ok = n.c.notifier.check_endpoint
    assert ok("https://ntfy.example/upAbC123xyz?up=1") is None
    assert ok("https://evil.example/upAbC123xyz") is not None            # someone else's server
    assert ok("http://ntfy.example/upAbC123xyz") is not None             # downgrade to http
    assert ok("https://ntfy.example:8443/upAbC123xyz") is not None       # other port
    assert ok("https://ntfy.example/v1/account") is not None             # not a topic
    assert ok("https://ntfy.example/up123456?x=1") is not None
    n.c.store.put("notify_settings", {})
    assert "aren't set up" in ok("https://ntfy.example/upAbC123xyz")


async def test_push_is_encrypted_and_only_the_phone_can_read_it(tmp_path):
    from embertide import webpush
    n, fake = _push_setup(tmp_path, token="tk_push")
    k, pub, auth = _phone_keys()
    n.c.notifier.add_device(n.p1.id, "https://ntfy.example/upAbC123xyz?up=1", pub, auth, "Pixel 9")
    m = Message("left_on", "Left's side is on", "Nobody in it", n.p1.id,
                [Action("Turn it off", "manual_off"), Action("Delete everything", "rm_rf")], priority=3)
    assert await n.c.notifier.push(n.p1.id, n.c.notifier.devices(n.p1.id)[0], m)
    call = fake.calls[0]
    assert call["headers"]["content-encoding"] == "aes128gcm" and call["headers"]["authorization"] == "Bearer tk_push"
    assert b"Left" not in call["body"]                                    # ciphertext on the wire
    got = json.loads(webpush.decrypt(call["body"], k, auth))
    assert got["title"] == "Left's side is on" and got["t"] == "et"
    assert [a["act"] for a in got["actions"]] == ["manual_off"]           # only actions the app knows survive


async def test_gone_phone_is_removed(tmp_path):
    n, _ = _push_setup(tmp_path, status=410)
    _, pub, auth = _phone_keys()
    n.c.notifier.add_device(n.p1.id, "https://ntfy.example/upAbC123xyz", pub, auth, "Old phone")
    assert not await n.c.notifier.push(n.p1.id, n.c.notifier.devices(n.p1.id)[0], Message("test", "t", "b", n.p1.id))
    assert n.c.notifier.devices(n.p1.id) == []


async def test_app_first_then_ntfy_when_the_phone_push_fails(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        return httpx.Response(403 if "/upAbC" in str(req.url) else 200, json={})
    n.c.notifier = Notifier(n.c, "https://bed.example", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    n.c.notifier.save_providers(providers())
    n.c.store.put("notify_settings", {"push_server": "https://ntfy.example"})
    _, pub, auth = _phone_keys()
    n.c.notifier.add_device(n.p1.id, "https://ntfy.example/upAbC123xyz", pub, auth, "Pixel")
    n.c.notifier.send(Message("left_on", "On", "empty", n.p1.id))
    for t in list(n.c.notifier._tasks):
        await t
    assert seen[0].startswith("https://ntfy.example/upAbC") and any(u.rstrip("/") == "https://ntfy.example" for u in seen[1:])


def test_push_routes(tmp_path):
    with make(tmp_path) as c:
        c.put("/api/notify/settings", json={"push_server": "https://ntfy.example"}, headers=H)
        _, pub, auth = _phone_keys()
        bad = c.post("/api/notify/me/push", json={"endpoint": "https://evil.example/upAbC123xyz", "p256dh": pub, "auth": auth, "person": "p1"}, headers=H)
        assert bad.status_code == 422
        good = c.post("/api/notify/me/push", json={"endpoint": "https://ntfy.example/upAbC123xyz", "p256dh": pub, "auth": auth, "device": "Pixel", "person": "p1"}, headers=H)
        assert good.status_code == 200
        lst = c.get("/api/notify/me/push", params={"person": "p1"}).json()
        assert lst["server"] == "https://ntfy.example" and [d["device"] for d in lst["devices"]] == ["Pixel"]
        assert "p256dh" not in lst["devices"][0] and "endpoint" not in lst["devices"][0]
        assert c.post("/api/notify/me/push/remove", json={"id": lst["devices"][0]["id"], "person": "p1"}, headers=H).json()["ok"]


# ---------- Google push (FCM) ----------
def _fake_service_account(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    (tmp_path / "fcm-service-account.json").write_text(json.dumps({
        "type": "service_account", "project_id": "ember-test", "private_key": pem,
        "client_email": "push@ember-test.iam.gserviceaccount.com", "token_uri": "https://oauth2.googleapis.com/token"}))
    return k


async def test_fcm_send_is_signed_and_encrypted(tmp_path):
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from embertide import webpush
    rsa_key = _fake_service_account(tmp_path)
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if "oauth2" in str(req.url):
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600})
        return httpx.Response(200, json={"name": "projects/ember-test/messages/1"})
    n = Night(tmp_path, "2026-11-05T21:00")
    n.c.notifier = Notifier(n.c, None, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    n.c.notifier.fcm.data_dir = tmp_path
    assert n.c.notifier.fcm.configured and n.c.notifier.fcm.project == "ember-test"
    k, pub, auth = _phone_keys()
    n.c.notifier.add_device(n.p1.id, "f" * 160, pub, auth, "Pixel", kind="fcm")
    ok = await n.c.notifier.push(n.p1.id, n.c.notifier.devices(n.p1.id)[0], Message("test", "Hello", "body", n.p1.id))
    assert ok
    # the token request carries a JWT signed with the service-account key
    from urllib.parse import parse_qs
    assertion = parse_qs(seen[0].content.decode())["assertion"][0]
    head, claim, sig = assertion.split(".")
    pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))   # noqa: E731
    rsa_key.public_key().verify(pad(sig), f"{head}.{claim}".encode(), padding.PKCS1v15(), hashes.SHA256())
    assert json.loads(pad(claim))["scope"].endswith("firebase.messaging")
    send = seen[1]
    assert str(send.url).endswith("/v1/projects/ember-test/messages:send") and send.headers["authorization"] == "Bearer ya29.test"
    msg = json.loads(send.content)["message"]
    assert msg["token"] == "f" * 160 and msg["android"]["priority"] == "high"
    assert json.loads(webpush.decrypt(pad(msg["data"]["e"]), k, auth))["title"] == "Hello"   # Google only sees ciphertext


async def test_fcm_unregistered_phone_is_removed(tmp_path):
    _fake_service_account(tmp_path)

    def handler(req: httpx.Request):
        if "oauth2" in str(req.url):
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600})
        return httpx.Response(404, json={"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}})
    n = Night(tmp_path, "2026-11-05T21:00")
    n.c.notifier = Notifier(n.c, None, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    n.c.notifier.fcm.data_dir = tmp_path
    _, pub, auth = _phone_keys()
    n.c.notifier.add_device(n.p1.id, "g" * 160, pub, auth, "Old", kind="fcm")
    assert not await n.c.notifier.push(n.p1.id, n.c.notifier.devices(n.p1.id)[0], Message("test", "t", "b", n.p1.id))
    assert n.c.notifier.devices(n.p1.id) == []


def test_fcm_registration_needs_the_server_set_up(tmp_path):
    with make(tmp_path) as c:
        _, pub, auth = _phone_keys()
        r = c.post("/api/notify/me/push", json={"kind": "fcm", "endpoint": "a" * 160, "p256dh": pub, "auth": auth, "person": "p1"}, headers=H)
        assert r.status_code == 422 and "Firebase" in r.json()["detail"]
        _fake_service_account(tmp_path)
        assert c.get("/api/notify/me/push", params={"person": "p1"}).json()["fcm"] is True
        assert c.post("/api/notify/me/push", json={"kind": "fcm", "endpoint": "bad token!", "p256dh": pub, "auth": auth, "person": "p1"}, headers=H).status_code == 422
        ok = c.post("/api/notify/me/push", json={"kind": "fcm", "endpoint": "a" * 160, "p256dh": pub, "auth": auth, "device": "Pixel", "person": "p1"}, headers=H)
        assert ok.status_code == 200
        assert c.get("/api/notify/me/push", params={"person": "p1"}).json()["devices"][0]["kind"] == "fcm"


def test_fcm_message_android_vs_ios():
    from embertide.fcm import Fcm
    a = Fcm.message("tok", b"secret", urgent=True)["message"]
    assert "apns" not in a and a["android"]["priority"] == "high" and a["data"]["e"]
    i = Fcm.message("tok", b"secret", urgent=True, ios=True)["message"]
    aps = i["apns"]["payload"]["aps"]
    assert aps["mutable-content"] == 1 and i["apns"]["headers"]["apns-priority"] == "10"
    assert "secret" not in str(i["apns"])                     # only ciphertext travels; the placeholder text is generic


async def test_a_side_that_ignores_commands_is_reported_once(tmp_path):
    """The bed is online but one side keeps ignoring changes: after 20 minutes, one plain alert for that night."""
    n = Night(tmp_path, "2026-11-05T21:00")
    fake = Fake()
    n.c.notifier = Notifier(n.c, None, client=fake.client())
    n.c.notifier.save_providers(providers())
    n.present("left", True)
    await n.until("2026-11-05T22:40")
    now = n.clock.now_ts()
    zone = n.p1.zone
    n.c.unconfirmed_since = {zone: now - 60}
    assert not [m for m in n.c.notifier.observe(now, n.c.snapshot()) if m.kind == "problem"]
    n.c.unconfirmed_since = {zone: now - 21 * 60}
    out = [m for m in n.c.notifier.observe(now, n.c.snapshot()) if m.kind == "problem"]
    assert len(out) == 1 and "isn't taking changes" in out[0].title
    assert not [m for m in n.c.notifier.observe(now + 600, n.c.snapshot()) if m.kind == "problem"]


async def test_calibration_morning_has_one_tap_check_in(tmp_path):
    """During the calibration week the morning message carries Too cold / Just right / Too warm; a tap stores it for
    the night that just ended (tunes Autopilot), from the app or a link button alike."""
    from embertide.notify import Action
    n = Night(tmp_path, "2026-11-05T22:00")
    n.c.notifier = Notifier(n.c, "https://bed.example", client=Fake().client())
    n.present("left", True)
    await n.until("2026-11-06T07:00", dt=120)
    n.present("left", False)
    await n.until("2026-11-06T08:30", dt=120)
    m = n.c.notifier._morning(n.p1, "2026-11-05", n.clock.now_ts())
    assert m and [a.act for a in m.actions] == ["feel_cold", "feel_right", "feel_warm"]
    code = n.c.notifier.new_action(n.p1.id, Action("Too warm", "feel_warm"))
    done = n.c.notifier.use_action(code)
    assert done and done["done"] == "Noted: too warm"
    assert n.store.get(f"feedback:{n.p1.id}:2026-11-05")["temp"] == "warm"
    n.p1.settings.autopilot.learn_skip = True                          # calibration over: the plain message
    m2 = n.c.notifier._morning(n.p1, "2026-11-05", n.clock.now_ts())
    assert m2 and not m2.actions


def test_feedback_without_a_date_is_for_the_night_that_just_ended(tmp_path):
    from test_autopilot_suite import make
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T09:00"}, headers=H)
        assert c.post("/api/people/p1/feedback", json={"temp": "cold"}, headers=H).json()["ok"]
        assert c.app.state.oura.c.store.get("feedback:p1:2026-11-05")["temp"] == "cold"
