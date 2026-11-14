"""Optional AI model, connected from Studio (AI model) or, as the defaults, from environment variables.

Where the settings come from (`effective`): what the owner saved in the app wins; when nothing has been saved, the
environment variables apply, so an install that already points ET_LLM_URL at a local Ollama keeps working unchanged.
Every caller goes through `effective(cfg, store)` and hands the result to `configured`, `model_name`, `provider` and
`model_client`; nobody reads `cfg.llm_*` directly.

Two kinds of connection (see PRESETS for the one-tap choices in the app):
- "openai": any OpenAI-compatible chat endpoint (Ollama, LM Studio, llama.cpp, vLLM, OpenAI, GitHub Models, Gemini,
  OpenRouter, ...) at a base URL with a model and an optional key. The request body is adjusted per preset, and a 400
  that names an optional parameter is retried once without it.
- "anthropic": Claude through the Anthropic SDK (installed in the Docker image; `pip install embertide[claude]`
  elsewhere). The key comes from the app, ET_LLM_KEY or the SDK's own credentials; the model defaults to claude-opus-5-5.

Environment defaults: ET_LLM_PROVIDER, ET_LLM_URL, ET_LLM_MODEL, ET_LLM_KEY (config.py).

Without a model, Ask falls back to the rule-based insights. The model never changes anything itself: the assistant
(assistant.py) turns its tool calls into proposals, validates them and applies them only when the person confirms (or,
if they allowed it, small reversible ones within limits).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable

import httpx

CLAUDE_DEFAULT = "claude-opus-5-5"
OPENAI_DEFAULT = "qwen2.5:7b"

SYSTEM = """You are the sleep assistant inside Ember & Tide, a controller for a dual-zone water-cooled mattress topper.
You get JSON context: the person's rhythm (phases with temperatures in °C, anchors and transitions), recent night summaries
(estimated from bed sensors, not medical), morning feedback, and rule-based insights. Answer briefly and concretely.
Show temperatures in the unit named by person.units ("F" = degrees Fahrenheit, "C" = degrees Celsius), converting the
*_c values (they are always °C) and never mentioning the other unit unless asked. Reply in the language named by
person.language when it is set; if it is "auto", reply in the language the question is written in. Dates in the
context are ISO (YYYY-MM-DD) and times are 24-hour; show times in the style named by person.clock ("12" = 1:30 PM,
"24" = 13:30). Assume nothing about where the person lives, their culture, work week or holidays; only what the context says.
Never give medical advice. If you recommend a rhythm change, add ONE fenced block:
```proposal
{"rhythm_id": "...", "phase_id": "...", "field": "temp_c" | "ramp_min", "to": <number>}
```
In the proposal "to" is always °C (or minutes). Use only rhythm and phase ids from the context. Keep temperature changes within ±1.5 °C per suggestion."""


# ---------- household settings: presets, saved settings, the one place that decides what is in use ----------
SETTINGS_KEY = "ai_settings"          # kv row: {"preset", "url", "model", "key", "enabled"}; the key is never sent to a client

# group: local (nothing leaves the house) | hosted. kind: openai (compatible endpoint) | anthropic (SDK).
PRESETS: dict[str, dict] = {
    "ollama": {"id": "ollama", "group": "local", "name": "Ollama", "kind": "openai", "url": "", "url_hint": "http://<host>:11434/v1",
               "model": "qwen2.5:7b", "models": [], "needs_key": False, "leaves_house": False,
               "help": "Ollama running on a computer at home. In Docker, use that computer's address on your network (or "
                       "host.docker.internal), not localhost."},
    "lmstudio": {"id": "lmstudio", "group": "local", "name": "LM Studio", "kind": "openai", "url": "", "url_hint": "http://<host>:1234/v1",
                 "model": "", "models": [], "needs_key": False, "leaves_house": False,
                 "help": "LM Studio's local server (turn it on under Developer). Use that computer's address on your network."},
    "custom": {"id": "custom", "group": "local", "name": "Other OpenAI-compatible server", "kind": "openai", "url": "",
               "url_hint": "http://<host>:8080/v1", "model": "", "models": [], "needs_key": False, "key_optional": True,
               "leaves_house": False,
               "help": "llama.cpp server, vLLM, LocalAI, Jan and similar. Give the address ending in /v1; add a key only if it asks for one."},
    "claude": {"id": "claude", "group": "hosted", "name": "Claude", "kind": "anthropic", "url": "", "url_hint": "",
               "model": CLAUDE_DEFAULT, "models": [CLAUDE_DEFAULT, "claude-sonnet-5-5", "claude-haiku-5-5"],
               "needs_key": True, "leaves_house": True, "key_label": "Anthropic API key",
               "help": "Use an API key from the Anthropic Console. A Claude Pro or Max chat subscription can't be signed into from other "
                       "apps. Sonnet and Haiku are cheaper picks."},
    "openai": {"id": "openai", "group": "hosted", "name": "OpenAI", "kind": "openai", "url": "https://api.openai.com/v1", "url_hint": "",
               "model": "gpt-4.1", "models": ["gpt-4.1", "gpt-4.1-mini"], "needs_key": True, "leaves_house": True,
               "key_label": "OpenAI API key",
               "help": "Use an API key from the OpenAI platform. A ChatGPT Plus subscription can't be signed into from other apps."},
    "github": {"id": "github", "group": "hosted", "name": "GitHub Models", "kind": "openai", "url": "https://models.github.ai/inference",
               "url_hint": "", "model": "openai/gpt-4.1", "models": ["openai/gpt-4.1", "openai/gpt-4.1-mini"], "needs_key": True,
               "leaves_house": True, "key_label": "GitHub token",
               "help": "For GitHub accounts, including Copilot plans. Create a fine-grained personal access token with the "
                       "\"Models: read\" permission and paste it as the key. Copilot Chat itself can't be signed into from other apps."},
    "gemini": {"id": "gemini", "group": "hosted", "name": "Google Gemini", "kind": "openai",
               "url": "https://generativelanguage.googleapis.com/v1beta/openai/", "url_hint": "", "model": "gemini-2.5-flash",
               "models": ["gemini-2.5-flash", "gemini-2.5-pro"], "needs_key": True, "leaves_house": True,
               "key_label": "Gemini API key", "help": "Use an API key from Google AI Studio."},
    "openrouter": {"id": "openrouter", "group": "hosted", "name": "OpenRouter", "kind": "openai", "url": "https://openrouter.ai/api/v1",
                   "url_hint": "", "model": "", "models": [], "needs_key": True, "leaves_house": True, "key_label": "OpenRouter API key",
                   "help": "One key for many models. Pick a model id such as anthropic/claude-sonnet-5-5."},
}
LEAVES_HOUSE_NOTE = "The night summaries sent for Ask and the morning review leave the house and go to this provider."
for _p in PRESETS.values():
    _p["note"] = LEAVES_HOUSE_NOTE if _p["leaves_house"] else "Nothing leaves the house."


@dataclass
class Effective:
    """What is really in use. It carries the same llm_* names as the config, so the helpers below take either."""
    llm_provider: str = "openai"
    llm_url: str | None = None
    llm_model: str = ""
    llm_key: str | None = None
    preset: str = "custom"
    off: bool = False
    source: str = "env"            # "app" (saved in Studio) | "env"
    demo: bool = False


def saved_settings(store) -> dict | None:
    s = store.get(SETTINGS_KEY) if store is not None else None
    return s if isinstance(s, dict) else None


def effective(cfg, store=None) -> Effective:
    """The one place that decides which model is in use. Saved app settings win; with nothing saved, the environment
    (ET_LLM_*) applies unchanged."""
    if getattr(cfg, "demo", False):
        return Effective("openai", None, "", None, preset="custom", off=True, source="demo", demo=True)
    s = saved_settings(store)
    if s is None:
        envp = "anthropic" if getattr(cfg, "llm_provider", "openai") == "anthropic" else "openai"
        return Effective(envp, getattr(cfg, "llm_url", None), getattr(cfg, "llm_model", "") or "", getattr(cfg, "llm_key", None),
                         preset="claude" if envp == "anthropic" else "custom", source="env")
    pid = s.get("preset") if s.get("preset") in PRESETS else "custom"
    pr = PRESETS[pid]
    off = (not s.get("enabled", True)) or s.get("preset") == "off"
    url = (s.get("url") or pr["url"] or "").strip() or None
    return Effective(pr["kind"], url if pr["kind"] == "openai" else None, (s.get("model") or pr["model"] or "").strip(),
                     s.get("key") or None, preset=pid, off=off, source="app")


def redact(text: object, key: str | None) -> str:
    """Never let a key into a log line or an error shown to anyone."""
    t = str(text)
    if key and len(key) >= 4:
        t = t.replace(key, "••••")
    return re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", r"\1••••", t)


def provider(cfg) -> str:
    return "anthropic" if (getattr(cfg, "llm_provider", "openai") == "anthropic") else "openai"


def configured(cfg) -> bool:
    if getattr(cfg, "off", False):
        return False
    return provider(cfg) == "anthropic" or bool(cfg.llm_url)


def model_name(cfg) -> str:
    """The model in use. For Claude, an unset or non-Claude ET_LLM_MODEL means the current default."""
    m = cfg.llm_model or ""
    if provider(cfg) == "anthropic":
        return m if m.startswith("claude-") else CLAUDE_DEFAULT
    return m or OPENAI_DEFAULT


async def ask(cfg, question: str, context: dict) -> dict:
    """The original single-answer Ask (kept for older app versions). The proposal is checked by the caller."""
    text = await complete(cfg, SYSTEM, f"Context:\n```json\n{json.dumps(context, default=str)[:24000]}\n```\n\nQuestion: {question}")
    text, proposal = split_proposal(text)
    return {"answer": text, "proposal": proposal, "model": model_name(cfg)}


async def complete(cfg, system: str, user: str, max_tokens: int = 1200, timeout: float = 240) -> str:
    """One plain answer, no tools."""
    m = model_client(cfg)
    reply = await m.run(system, user, [], _no_tools, max_tokens=max_tokens, timeout=timeout)
    return reply.text


async def _no_tools(name: str, args: dict) -> str:
    return "No tools are available here."


def split_proposal(text: str) -> tuple[str, dict | None]:
    """Pull the one fenced ```proposal``` block out of a model's answer (and drop any <think> section)."""
    proposal = None
    m = re.search(r"```proposal\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            proposal = json.loads(m.group(1))
        except ValueError:
            proposal = None
        if not isinstance(proposal, dict):
            proposal = None
        text = (text[:m.start()] + text[m.end():]).strip()
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip(), proposal


def check_rhythm_proposal(person, prop: dict | None, max_step_c: float = 1.5) -> dict | None:
    """A model's rhythm proposal, only if it names a real phase of this person's rhythm and stays within the step limit
    (temperature ±max_step_c, transition ±30 min). Anything else is dropped, never shown as a button."""
    if not isinstance(prop, dict):
        return None
    try:
        to = float(prop.get("to"))
    except (TypeError, ValueError):
        return None
    field_ = prop.get("field")
    for rh in person.rhythms:
        if rh.id != prop.get("rhythm_id"):
            continue
        for ph in rh.phases:
            if ph.id != prop.get("phase_id"):
                continue
            if field_ == "temp_c" and abs(to - ph.temp_c) <= max_step_c + 1e-6 and 10 <= to <= 45:
                return {"rhythm_id": rh.id, "phase_id": ph.id, "field": "temp_c", "from": ph.temp_c, "to": round(to, 2)}
            if field_ == "ramp_min" and abs(to - ph.ramp_min) <= 30 and 0 <= to <= 120:
                return {"rhythm_id": rh.id, "phase_id": ph.id, "field": "ramp_min", "from": ph.ramp_min, "to": int(to)}
    return None


# ---------- model clients: one conversation turn with tools, the tool loop inside ----------
OnCall = Callable[[str, dict], Awaitable[str]]


@dataclass
class Reply:
    text: str
    calls: list[dict] = field(default_factory=list)       # [{"name", "input"}] in the order the model made them
    model: str = ""
    refused: bool = False


_OPTIONAL = ("temperature", "reasoning_effort", "max_tokens", "max_completion_tokens")
_LOCAL_PRESETS = ("ollama", "lmstudio", "custom")


def _reasoning_model(name: str) -> bool:
    n = (name or "").rsplit("/", 1)[-1].lower()
    return bool(re.match(r"(o\d|gpt-5)", n))


def chat_body(cfg, msgs: list[dict], max_tokens: int) -> dict:
    """The request body for this provider. Local servers keep the original body (Ollama needs reasoning_effort none);
    hosted ones get only what they accept."""
    preset = getattr(cfg, "preset", "custom")
    model = model_name(cfg)
    body: dict = {"model": model, "messages": msgs}
    if preset in _LOCAL_PRESETS:
        body.update(temperature=0.3, max_tokens=max_tokens, reasoning_effort="none")
    elif preset in ("openai", "github") and _reasoning_model(model):
        body["max_completion_tokens"] = max(max_tokens, 4000)       # reasoning tokens count against the limit
    elif preset == "openai":
        body.update(temperature=0.3, max_completion_tokens=max_tokens)
    else:
        body.update(temperature=0.3, max_tokens=max_tokens)
    return body


def _apply_adjust(body: dict, adjust: dict) -> None:
    for k in adjust.get("drop", ()):
        body.pop(k, None)
    if adjust.get("swap") and "max_tokens" in body:
        body["max_completion_tokens"] = body.pop("max_tokens")
    elif adjust.get("swap_back") and "max_completion_tokens" in body:
        body["max_tokens"] = body.pop("max_completion_tokens")


def _adapt(text: str, body: dict, adjust: dict) -> bool:
    """A 400 that names an optional parameter: change the body in place (and remember it for later rounds).
    False when it names none, or when this was already tried."""
    if adjust.get("done"):
        return False
    named = [k for k in _OPTIONAL if k in text and k in body]
    if not named:
        return False
    adjust["done"] = True
    adjust["drop"] = [k for k in named if k in ("temperature", "reasoning_effort")]
    if "max_tokens" in named and "max_completion_tokens" in text:
        adjust["swap"] = True
    elif "max_completion_tokens" in named and "max_tokens" in text:
        adjust["swap_back"] = True
    elif "max_tokens" in named or "max_completion_tokens" in named:
        adjust["drop"] += [k for k in named if k.startswith("max_")]
    _apply_adjust(body, adjust)
    return True


class OpenAICompatModel:
    """Any OpenAI-compatible /chat/completions endpoint, with function tools (Ollama, LM Studio, hosted APIs)."""

    ROUNDS = 3

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    async def run(self, system: str, user: str, tools: list[dict], on_call: OnCall, max_tokens: int = 1200,
                  timeout: float = 120) -> Reply:
        cfg = self.cfg
        assert not getattr(cfg, "demo", False), "AI requests are disabled in demo mode"
        msgs: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        out = Reply("", model=model_name(cfg))
        headers = {"Authorization": f"Bearer {cfg.llm_key}"} if cfg.llm_key else {}
        if getattr(cfg, "preset", "") == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/Ncbudd/ember-tide"
            headers["X-Title"] = "Ember & Tide"
        texts: list[str] = []
        adjust: dict = {}                           # sticky changes learned from a 400 (see _adapt)
        async with httpx.AsyncClient(timeout=timeout) as http:
            for _ in range(self.ROUNDS):
                body = chat_body(cfg, msgs, max_tokens)
                _apply_adjust(body, adjust)
                if tools:
                    body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                                       "parameters": t["input_schema"]}} for t in tools]
                url = cfg.llm_url.rstrip("/") + "/chat/completions"
                r = await http.post(url, json=body, headers=headers)
                if r.status_code == 400 and _adapt(r.text, body, adjust):      # once: retry without what it objected to
                    r = await http.post(url, json=body, headers=headers)
                r.raise_for_status()
                msg = (r.json().get("choices") or [{}])[0].get("message") or {}
                raw = msg.get("content") or ""
                if tools:
                    text, legacy = split_proposal(raw)
                else:                                   # plain answer: the caller reads any proposal block itself
                    text, legacy = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip(), None
                if text:
                    texts.append(text)
                calls = msg.get("tool_calls") or []
                if not calls and legacy and tools:
                    # an older model that writes the fenced proposal block instead of calling a tool
                    calls = [{"id": "legacy", "function": {"name": "change_rhythm_phase", "arguments": _legacy_args(legacy)}}]
                if not calls:
                    break
                msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for c in calls:
                    fn = c.get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args or "{}")
                        except ValueError:
                            args = {"_invalid_json": True}
                    name = str(fn.get("name") or "")
                    out.calls.append({"name": name, "input": args})
                    res = await on_call(name, args if isinstance(args, dict) else {"_invalid": True})
                    msgs.append({"role": "tool", "tool_call_id": c.get("id") or name, "content": res})
                if c.get("id") == "legacy":
                    break
        out.text = "\n\n".join(texts).strip()
        return out


def _legacy_args(p: dict) -> dict:
    a = {"rhythm_id": p.get("rhythm_id"), "phase_id": p.get("phase_id")}
    if p.get("field") == "ramp_min":
        a["ramp_min"] = p.get("to")
    else:
        a["temp_c"] = p.get("to")
    return a


class AnthropicModel:
    """Claude through the official Anthropic SDK (Messages API with client tools; we run the short tool loop)."""

    ROUNDS = 3

    def __init__(self, cfg, client=None) -> None:
        self.cfg = cfg
        self._client = client

    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:                        # pragma: no cover - depends on the install
                raise RuntimeError("Claude needs the Anthropic SDK, which isn't installed here: pip install 'embertide[claude]'") from e
            self._client = anthropic.AsyncAnthropic(api_key=self.cfg.llm_key) if self.cfg.llm_key else anthropic.AsyncAnthropic()
        return self._client

    async def run(self, system: str, user: str, tools: list[dict], on_call: OnCall, max_tokens: int = 16000,
                  timeout: float = 240) -> Reply:
        assert not getattr(self.cfg, "demo", False), "AI requests are disabled in demo mode"
        model = model_name(self.cfg)
        out = Reply("", model=model)
        messages: list[dict] = [{"role": "user", "content": user}]
        texts: list[str] = []
        cl = self.client()
        for _ in range(self.ROUNDS):
            kw = dict(model=model, max_tokens=max(max_tokens, 4000), system=system, messages=messages,
                      thinking={"type": "adaptive"}, output_config={"effort": "medium"},
                      betas=["server-side-fallback-2026-07-01"], fallbacks="default", timeout=timeout)
            if tools:
                kw["tools"] = tools
            resp = await cl.beta.messages.create(**kw)
            if resp.stop_reason == "refusal":
                out.refused = True
                texts.append("I can't help with that one.")
                break
            texts += [b.text for b in resp.content if getattr(b, "type", "") == "text" and b.text.strip()]
            uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            if resp.stop_reason != "tool_use" or not uses:
                break
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for u in uses:
                args = u.input if isinstance(u.input, dict) else {"_invalid": True}
                out.calls.append({"name": u.name, "input": args})
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": await on_call(u.name, args)})
            messages.append({"role": "user", "content": results})
        out.text = "\n\n".join(texts).strip()
        return out


def model_client(cfg):
    return AnthropicModel(cfg) if provider(cfg) == "anthropic" else OpenAICompatModel(cfg)
