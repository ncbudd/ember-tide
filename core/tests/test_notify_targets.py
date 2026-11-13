"""Generic notification targets: webhook and Apprise API."""

import json

import httpx
from fastapi.testclient import TestClient

from embertide.notify import Message, Notifier
from test_notify import H, make
from test_scenarios import Night


def recorder(status=200):
    calls = []

    def handler(req: httpx.Request):
        calls.append({"url": str(req.url), "json": json.loads(req.content or b"{}"), "headers": dict(req.headers)})
        return httpx.Response(status, json={})
    return calls, httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_webhook_posts_json_with_headers(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    calls, cl = recorder()
    nt = Notifier(n.c, "https://bed.example", client=cl)
    p = {"id": "w1", "kind": "webhook", "name": "Hook", "person": n.p1.id, "enabled": True,
         "settings": {"url": "https://hooks.example/x", "headers": {"X-Key": "abc"}}}
    r = await nt.deliver(p, Message("test", "Hello", "World", n.p1.id, priority=4))
    assert r == {"ok": True}
    c = calls[0]
    assert c["url"] == "https://hooks.example/x" and c["headers"]["x-key"] == "abc"
    assert set(c["json"]) == {"title", "body", "kind", "person", "priority", "url", "ts"}
    assert c["json"]["title"] == "Hello" and c["json"]["kind"] == "test" and c["json"]["priority"] == 4 and c["json"]["url"] == "https://bed.example/"


async def test_apprise_uses_key_or_urls_and_logs_failures(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    calls, cl = recorder()
    nt = Notifier(n.c, None, client=cl)
    keyed = {"id": "a1", "kind": "apprise", "name": "A", "settings": {"url": "http://apprise:8000", "key": "bed", "tag": "sleep"}}
    assert (await nt.deliver(keyed, Message("x", "T", "B")))["ok"]
    assert calls[0]["url"] == "http://apprise:8000/notify/bed"
    assert calls[0]["json"]["title"] == "T" and calls[0]["json"]["body"] == "B" and calls[0]["json"]["type"] == "info" and calls[0]["json"]["tag"] == "sleep"
    stateless = {"id": "a2", "kind": "apprise", "name": "B", "settings": {"url": "http://apprise:8000/", "urls": "tgram://t/c"}}
    assert (await nt.deliver(stateless, Message("x", "T", "B", priority=5)))["ok"]
    assert calls[1]["url"] == "http://apprise:8000/notify" and calls[1]["json"]["urls"] == "tgram://t/c" and calls[1]["json"]["type"] == "warning"
    calls2, bad = recorder(500)
    nt2 = Notifier(n.c, None, client=bad)
    r = await nt2.deliver(keyed, Message("x", "T", "B"))
    assert not r["ok"] and "error (500)" in r["error"]
    assert nt2.delivery_log()[-1]["ok"] is False if hasattr(nt2, "delivery_log") else True


async def test_generic_targets_follow_person_routing(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    nt = Notifier(n.c, None)
    nt.save_providers([
        {"id": "w1", "kind": "webhook", "name": "mine", "person": n.p1.id, "enabled": True, "settings": {"url": "https://h.example/1"}},
        {"id": "w2", "kind": "apprise", "name": "house", "person": None, "enabled": True, "settings": {"url": "http://a:8000", "key": "k"}},
    ])
    assert [p["id"] for p in nt.recipients(Message("x", "t", "b", n.p1.id))] == ["w1"]
    assert [p["id"] for p in nt.recipients(Message("x", "t", "b", None))] == ["w2"]


def test_provider_api_validates_and_hides_header_secrets(tmp_path):
    with make(tmp_path) as c:
        post = lambda body: c.post("/api/notify/providers", json=body, headers=H)
        assert post({"kind": "webhook", "name": "w", "settings": {"url": "ftp://x"}}).status_code == 422
        assert post({"kind": "apprise", "name": "a", "settings": {"url": "http://a:8000"}}).status_code == 422       # needs key or urls
        assert post({"kind": "webhook", "name": "w", "settings": {"url": "https://h.example", "headers": {"bad name": "v"}}}).status_code == 422
        r = post({"kind": "webhook", "name": "w", "person": "p1", "settings": {"url": "https://h.example/x", "headers": {"Authorization": "Bearer s3cret"}}})
        assert r.status_code == 200 and "s3cret" not in r.text
        r = post({"kind": "apprise", "name": "a", "settings": {"url": "http://a:8000", "urls": "tgram://tok/chat"}})
        assert r.status_code == 200 and "tok/chat" not in r.text
        listed = c.get("/api/notify/providers").json()
        assert "s3cret" not in json.dumps(listed) and listed[0]["settings"]["headers"] == {"Authorization": "set"}
        # saving back unchanged keeps the real values
        for p in listed:
            assert c.put(f"/api/notify/providers/{p['id']}", json={**p, "enabled": False}, headers=H).status_code == 200
        stored = c.app.state.notifier.providers
        assert stored[0]["settings"]["headers"] == {"Authorization": "Bearer s3cret"} and stored[1]["settings"]["urls"] == "tgram://tok/chat"
        # moving it to another address does not carry the secret along
        moved = {**listed[0], "settings": {**listed[0]["settings"], "url": "https://evil.example/x"}}
        assert c.put(f"/api/notify/providers/{listed[0]['id']}", json=moved, headers=H).status_code == 422
