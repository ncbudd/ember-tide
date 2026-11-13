"""AI model settings (owner only, auth.OWNER_ONLY): pick a preset, save the address / model / key, test, list models.

Saved settings live in the kv row `ai_settings` (like the Home Assistant token: on this server only, not in any export).
The key is never returned: reads say `key_set` and the last four characters. With nothing saved the environment
(ET_LLM_*) applies; DELETE returns to that.
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import ai


class AiBody(BaseModel):
    preset: str = Field(..., max_length=20)                   # a preset id, or "off"
    url: str = Field("", max_length=300)
    model: str = Field("", max_length=200)
    key: str | None = Field(None, max_length=600)            # empty / None keeps the saved one (same preset and address only)
    clear_key: bool = False


def _norm(u: str | None) -> str:
    return (u or "").strip().rstrip("/")


def build_ai_router(state) -> APIRouter:
    r = APIRouter(prefix="/api/ai/settings")

    def store():
        return state.controller.store

    def view() -> dict:
        eff = ai.effective(state.cfg, store())
        pr = ai.PRESETS.get(eff.preset, ai.PRESETS["custom"])
        return {"source": eff.source, "preset": "off" if eff.off else eff.preset, "enabled": not eff.off,
                "url": eff.llm_url or "", "model": eff.llm_model, "key_set": bool(eff.llm_key),
                "key_tail": (eff.llm_key or "")[-4:], "configured": ai.configured(eff),
                "needs_key": pr["needs_key"], "presets": list(ai.PRESETS.values())}

    def resolve(b: AiBody) -> tuple[ai.Effective, str]:
        """The settings in the body as a model config; a blank key reuses the saved one only for the same preset and
        address (so a key is never sent somewhere new by accident)."""
        if b.preset not in ai.PRESETS:
            raise HTTPException(422, "Pick one of the choices.")
        pr = ai.PRESETS[b.preset]
        url = _norm(b.url) or _norm(pr["url"])
        if pr["kind"] == "openai":
            if not url.startswith(("http://", "https://")):
                raise HTTPException(422, "Give the server's address, starting with http:// or https://.")
        cur = ai.effective(state.cfg, store())
        key = (b.key or "").strip()
        if not key and not b.clear_key and cur.preset == b.preset and _norm(cur.llm_url) == (url if pr["kind"] == "openai" else _norm(cur.llm_url)):
            key = cur.llm_key or ""
        model = b.model.strip() or pr["model"]
        eff = ai.Effective(pr["kind"], url if pr["kind"] == "openai" else None, model, key or None, preset=b.preset, source="app")
        return eff, url

    @r.get("")
    async def get_settings():
        return view()

    @r.put("")
    async def put_settings(b: AiBody):
        if b.preset == "off":
            old = ai.saved_settings(store()) or {}
            store().put(ai.SETTINGS_KEY, {"preset": "off", "enabled": False, "url": "", "model": "", "key": old.get("key") or ""})
            return view()
        eff, url = resolve(b)
        pr = ai.PRESETS[b.preset]
        if pr["needs_key"] and not eff.llm_key:
            raise HTTPException(422, f"Add the {pr.get('key_label', 'key')} too.")
        if pr["kind"] == "openai" and not eff.llm_model:
            raise HTTPException(422, "Pick or type a model name.")
        store().put(ai.SETTINGS_KEY, {"preset": b.preset, "enabled": True, "url": url if pr["kind"] == "openai" else "",
                                      "model": eff.llm_model, "key": eff.llm_key or ""})
        return view()

    @r.delete("")
    async def reset_settings():
        """Back to the server defaults (the ET_LLM_* environment variables)."""
        store().put(ai.SETTINGS_KEY, None)
        return view()

    @r.post("/test")
    async def test_settings(b: AiBody):
        eff, _ = resolve(b)
        t0 = time.monotonic()
        try:
            reply = await ai.model_client(eff).run("Reply with one short word.", "Say OK.", [], ai._no_tools,
                                                   max_tokens=60, timeout=40)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": explain(e, eff)}
        return {"ok": True, "model": reply.model or ai.model_name(eff), "ms": int((time.monotonic() - t0) * 1000),
                "reply": ai.redact(reply.text, eff.llm_key)[:160]}

    @r.post("/models")
    async def list_models(b: AiBody):
        """POST, not GET: the unsaved key travels in the body, never in an address that could be logged."""
        eff, _ = resolve(b)
        try:
            ids = await fetch_models(eff)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "models": [], "error": explain(e, eff)}
        return {"ok": True, "models": ids}

    return r


async def fetch_models(eff: ai.Effective) -> list[str]:
    """Model ids only."""
    if eff.llm_provider == "anthropic":
        client = ai.AnthropicModel(eff).client()
        page = await client.models.list(limit=100)
        return sorted(m.id for m in page.data)
    headers = {"Authorization": f"Bearer {eff.llm_key}"} if eff.llm_key else {}
    base = (eff.llm_url or "").rstrip("/")
    url = "https://models.github.ai/catalog/models" if eff.preset == "github" else base + "/models"
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.get(url, headers=headers)
        r.raise_for_status()
        j = r.json()
    rows = j if isinstance(j, list) else (j.get("data") or j.get("models") or [])
    ids = []
    for m in rows:
        i = m.get("id") if isinstance(m, dict) else m
        if isinstance(i, str) and i:
            ids.append(i[7:] if i.startswith("models/") else i)
    return sorted(set(ids))


def explain(e: Exception, eff: ai.Effective) -> str:
    """A plain-language reason, with no key and no raw server text in it."""
    pname = ai.PRESETS.get(eff.preset, {}).get("name", "the model")
    where = eff.llm_url or pname
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        tip = " is Ollama running and reachable from the server?" if eff.preset == "ollama" else " is it running and reachable from the server?"
        return f"Couldn't reach {where} —{tip}"
    if isinstance(e, httpx.TimeoutException):
        return f"{where} took too long to answer. A big local model may need a minute to load: try again."
    status = getattr(e, "status_code", None)
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        body = e.response.text.lower()
    else:
        body = str(e).lower()
    name = type(e).__name__
    if isinstance(e, ImportError) or "anthropic sdk" in body:
        return "Claude needs the Anthropic package, which isn't installed on this server (pip install 'embertide[claude]')."
    if status in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError"):
        return f"The key was rejected ({status or 401}). Check it is copied whole and has the right permission."
    if status == 404 or name == "NotFoundError":
        if "model" in body:
            return "Model not found — pick one from the list."
        return f"Nothing answered at that address (404). Check it, usually it ends in /v1."
    if status == 429 or name == "RateLimitError":
        return "The provider says too many requests or no credit left (429). Check your plan and try again."
    if status == 400 and "model" in body:
        return "The provider didn't accept that model — pick one from the list."
    if status:
        return f"The model answered with an error ({status})."
    if "api_key" in body or "credentials" in body or "auth" in body:
        return "No key was found. Add the key and try again."
    return f"That didn't work ({ai.redact(name, eff.llm_key)})."
