"""Connecting an AI model from the app: settings round-trip, the key never comes back, owner-only, environment defaults
vs saved settings, per-provider request bodies, the one retry on a 400, test and model-list messages. No network."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from embertide import ai
from embertide.app import create_app
from embertide.config import Config

H = {"X-Embertide": "1"}
PW = "correct horse battery"
KEY = "sk-secret-1234567890abcd"


_open: list = []


@pytest.fixture(autouse=True)
def _close_apps():
    yield
    while _open:
        _open.pop().__exit__(None, None, None)


def make(tmp_path, url=None, model="qwen2.5:7b", key=None, provider="openai"):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.auth = tmp_path, "America/New_York", "sim", tmp_path / "noweb", True
    cfg.llm_url, cfg.llm_model, cfg.llm_key, cfg.llm_provider = url, model, key, provider
    c = TestClient(create_app(cfg))
    c.__enter__()                                     # starts the app (lifespan); closed by the fixture below
    _open.append(c)
    assert c.post("/api/auth/setup", json={"username": "alex", "password": PW, "person": "p1"}).status_code == 200
    return c


def mock(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    import embertide.ai_api as api_mod
    monkeypatch.setattr(api_mod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def ok_reply(req):
    return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})


def test_env_is_the_default_until_something_is_saved(tmp_path):
    c = make(tmp_path, url="http://ollama.test:11434/v1", model="llama3", key="envkey-abcd")
    g = c.get("/api/ai/settings").json()
    assert g["source"] == "env" and g["url"] == "http://ollama.test:11434/v1" and g["model"] == "llama3" and g["configured"]
    assert g["key_set"] and g["key_tail"] == "abcd" and "envkey" not in json.dumps(g)
    st = c.get("/api/ai/status").json()
    assert st["configured"] and st["model"] == "llama3"


def test_saved_settings_win_and_reset_returns_to_env(tmp_path):
    c = make(tmp_path, url="http://ollama.test:11434/v1")
    r = c.put("/api/ai/settings", json={"preset": "openai", "model": "gpt-4.1", "key": KEY}, headers=H)
    assert r.status_code == 200
    g = r.json()
    assert g["source"] == "app" and g["url"] == "https://api.openai.com/v1" and g["model"] == "gpt-4.1"
    assert g["key_set"] and g["key_tail"] == KEY[-4:]
    assert KEY not in json.dumps(g) and KEY not in c.get("/api/ai/settings").text and KEY not in c.get("/api/ai/status").text
    assert c.get("/api/ai/status").json()["model"] == "gpt-4.1"
    g = c.delete("/api/ai/settings", headers=H).json()
    assert g["source"] == "env" and g["url"] == "http://ollama.test:11434/v1"


def test_off_turns_the_model_off_even_with_env(tmp_path):
    c = make(tmp_path, url="http://ollama.test:11434/v1")
    g = c.put("/api/ai/settings", json={"preset": "off"}, headers=H).json()
    assert g["preset"] == "off" and not g["configured"]
    assert c.get("/api/ai/status").json()["configured"] is False


def test_blank_key_keeps_saved_only_for_the_same_place(tmp_path):
    c = make(tmp_path)
    c.put("/api/ai/settings", json={"preset": "openai", "model": "gpt-4.1", "key": KEY}, headers=H)
    g = c.put("/api/ai/settings", json={"preset": "openai", "model": "gpt-4.1-mini"}, headers=H).json()
    assert g["key_set"] and g["key_tail"] == KEY[-4:] and g["model"] == "gpt-4.1-mini"
    # another preset needing a key: the saved key is not carried over
    r = c.put("/api/ai/settings", json={"preset": "gemini"}, headers=H)
    assert r.status_code == 422


def test_validation(tmp_path):
    c = make(tmp_path)
    assert c.put("/api/ai/settings", json={"preset": "nope"}, headers=H).status_code == 422
    assert c.put("/api/ai/settings", json={"preset": "ollama", "url": "ollama.local", "model": "x"}, headers=H).status_code == 422
    assert c.put("/api/ai/settings", json={"preset": "ollama", "url": "http://h:11434/v1"}, headers=H).status_code == 200  # default model
    assert c.put("/api/ai/settings", json={"preset": "claude"}, headers=H).status_code == 422                            # key needed


def test_only_the_owner(tmp_path):
    c = make(tmp_path)
    code = c.post("/api/auth/invites", json={"person": "p2", "role": "sleeper"}, headers=H).json()["code"]
    g = TestClient(c.app)                 # same running app, a different cookie jar
    assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
    for method, path in (("get", ""), ("put", ""), ("delete", ""), ("post", "/test"), ("post", "/models")):
        r = getattr(g, method)("/api/ai/settings" + path, headers=H, **({"json": {"preset": "ollama", "url": "http://h/v1"}} if method in ("put", "post") else {}))
        assert r.status_code == 403, (method, path)
    assert g.get("/api/ai/status").status_code == 200


def cfg_for(preset, model, url="http://x.test/v1", key="k"):
    return ai.Effective("openai", url, model, key, preset=preset, source="app")


def test_bodies_depend_on_the_preset():
    msgs = [{"role": "user", "content": "hi"}]
    local = ai.chat_body(cfg_for("ollama", "qwen2.5:7b"), msgs, 500)
    assert local["reasoning_effort"] == "none" and local["max_tokens"] == 500 and local["temperature"] == 0.3
    env = ai.chat_body(ai.Effective("openai", "http://x/v1", "m", None), msgs, 500)       # env default = unchanged behaviour
    assert env["reasoning_effort"] == "none" and env["max_tokens"] == 500
    o = ai.chat_body(cfg_for("openai", "o4-mini"), msgs, 500)
    assert "temperature" not in o and "reasoning_effort" not in o and "max_tokens" not in o and o["max_completion_tokens"] >= 500
    g = ai.chat_body(cfg_for("openai", "gpt-4.1"), msgs, 500)
    assert g["max_completion_tokens"] == 500 and g["temperature"] == 0.3 and "reasoning_effort" not in g
    gh = ai.chat_body(cfg_for("github", "openai/gpt-4.1"), msgs, 500)
    assert gh["max_tokens"] == 500 and "reasoning_effort" not in gh
    assert "reasoning_effort" not in ai.chat_body(cfg_for("gemini", "gemini-2.5-flash"), msgs, 500)
    assert "reasoning_effort" not in ai.chat_body(cfg_for("openrouter", "a/b"), msgs, 500)


async def test_a_400_naming_a_parameter_is_retried_once_without_it(monkeypatch):
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature' does not support 0.3"}})
        return ok_reply(req)
    mock(monkeypatch, handler)
    rep = await ai.OpenAICompatModel(cfg_for("openai", "gpt-4.1")).run("s", "u", [], ai._no_tools)
    assert rep.text == "OK" and len(seen) == 2 and "temperature" not in seen[1]


async def test_max_tokens_is_swapped_when_the_provider_asks(monkeypatch):
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}})
        return ok_reply(req)
    mock(monkeypatch, handler)
    rep = await ai.OpenAICompatModel(cfg_for("github", "openai/gpt-4.1")).run("s", "u", [], ai._no_tools, max_tokens=300)
    assert rep.text == "OK" and seen[1]["max_completion_tokens"] == 300 and "max_tokens" not in seen[1]


async def test_a_400_about_something_else_is_not_retried(monkeypatch):
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(400, json={"error": {"message": "bad request"}})
    mock(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await ai.OpenAICompatModel(cfg_for("openai", "gpt-4.1")).run("s", "u", [], ai._no_tools)
    assert len(n) == 1


async def test_openrouter_headers_only_for_openrouter(monkeypatch):
    heads = []
    mock(monkeypatch, lambda req: (heads.append(dict(req.headers)), ok_reply(req))[1])
    await ai.OpenAICompatModel(cfg_for("openrouter", "a/b")).run("s", "u", [], ai._no_tools)
    await ai.OpenAICompatModel(cfg_for("openai", "gpt-4.1")).run("s", "u", [], ai._no_tools)
    assert heads[0]["x-title"] == "Ember & Tide" and "http-referer" in heads[0]
    assert "x-title" not in heads[1] and heads[1]["authorization"] == "Bearer k"


def test_test_endpoint_ok_uses_unsaved_settings(tmp_path, monkeypatch):
    c = make(tmp_path)
    seen = []
    mock(monkeypatch, lambda req: (seen.append(str(req.url)), ok_reply(req))[1])
    r = c.post("/api/ai/settings/test", json={"preset": "ollama", "url": "http://h.test:11434/v1", "model": "llama3"}, headers=H).json()
    assert r["ok"] and r["model"] == "llama3" and r["reply"] == "OK" and r["ms"] >= 0
    assert seen == ["http://h.test:11434/v1/chat/completions"]
    assert c.get("/api/ai/settings").json()["source"] == "env"          # nothing was saved


@pytest.mark.parametrize("make_resp,expect", [
    (lambda req: httpx.Response(401, json={}), "The key was rejected (401)"),
    (lambda req: httpx.Response(404, json={"error": {"message": "model 'x' not found"}}), "Model not found"),
    (lambda req: httpx.Response(404, text="nope"), "Nothing answered at that address"),
    (lambda req: httpx.Response(429, json={}), "429"),
])
def test_test_endpoint_explains_errors(tmp_path, monkeypatch, make_resp, expect):
    c = make(tmp_path)
    mock(monkeypatch, make_resp)
    r = c.post("/api/ai/settings/test", json={"preset": "openai", "model": "gpt-4.1", "key": KEY}, headers=H).json()
    assert not r["ok"] and expect in r["error"] and KEY not in json.dumps(r)


def test_test_endpoint_unreachable(tmp_path, monkeypatch):
    c = make(tmp_path)

    def boom(req):
        raise httpx.ConnectError("refused", request=req)
    mock(monkeypatch, boom)
    r = c.post("/api/ai/settings/test", json={"preset": "ollama", "url": "http://h.test:11434/v1", "model": "m"}, headers=H).json()
    assert not r["ok"] and r["error"].startswith("Couldn't reach http://h.test:11434/v1") and "Ollama" in r["error"]


def test_claude_without_the_sdk_says_so(tmp_path, monkeypatch):
    c = make(tmp_path)
    import builtins
    real = builtins.__import__

    def no_anthropic(name, *a, **k):
        if name == "anthropic":
            raise ImportError("no")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    r = c.post("/api/ai/settings/test", json={"preset": "claude", "key": KEY}, headers=H).json()
    assert not r["ok"] and "Anthropic package" in r["error"]


def test_models_listing_ids_only(tmp_path, monkeypatch):
    c = make(tmp_path)
    mock(monkeypatch, lambda req: httpx.Response(200, json={"data": [{"id": "b", "owned_by": "x"}, {"id": "models/a"}, {"id": "b"}]}))
    r = c.post("/api/ai/settings/models", json={"preset": "gemini", "key": KEY}, headers=H).json()
    assert r == {"ok": True, "models": ["a", "b"]}


def test_models_listing_github_catalog_and_error(tmp_path, monkeypatch):
    c = make(tmp_path)
    urls = []
    fail = []
    mock(monkeypatch, lambda req: httpx.Response(401, json={}) if fail else (urls.append(str(req.url)), httpx.Response(200, json=[{"id": "openai/gpt-4.1"}]))[1])
    assert c.post("/api/ai/settings/models", json={"preset": "github", "key": KEY}, headers=H).json()["models"] == ["openai/gpt-4.1"]
    assert urls == ["https://models.github.ai/catalog/models"]
    fail.append(1)
    r = c.post("/api/ai/settings/models", json={"preset": "openai", "key": KEY}, headers=H).json()
    assert not r["ok"] and "rejected" in r["error"]


async def test_claude_models_via_sdk(monkeypatch):
    class Page:
        data = [type("M", (), {"id": "claude-haiku-5-5"})(), type("M", (), {"id": "claude-opus-5-5"})()]

    class Models:
        async def list(self, **kw):
            return Page()

    class Client:
        models = Models()
    monkeypatch.setattr(ai.AnthropicModel, "client", lambda self: Client())
    from embertide.ai_api import fetch_models
    assert await fetch_models(ai.Effective("anthropic", None, "", "k", preset="claude")) == ["claude-haiku-5-5", "claude-opus-5-5"]


def test_redact():
    assert KEY not in ai.redact(f"failed with {KEY}", KEY)
    assert "abcdefghijk" not in ai.redact("Authorization: Bearer abcdefghijk", None)
