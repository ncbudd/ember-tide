"""Optional AI model. Two ways to connect one (config.py):

- ET_LLM_PROVIDER=openai (default): any OpenAI-compatible chat endpoint at ET_LLM_URL — a local Ollama
  (http://localhost:11434/v1), LM Studio, or a hosted API — with ET_LLM_MODEL and an optional ET_LLM_KEY.
- ET_LLM_PROVIDER=anthropic: Claude through the Anthropic SDK (`pip install embertide[claude]`). The key comes from
  ET_LLM_KEY or the SDK's own credentials (ANTHROPIC_API_KEY, `ant auth login`); the model defaults to claude-opus-5-5.

Without one, Ask falls back to the rule-based insights. The model never changes anything itself: the assistant
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
(estimated from bed sensors, not medical), morning feedback, and rule-based insights. Answer briefly and concretely, in °F
unless the person uses °C. Never give medical advice. If you recommend a rhythm change, add ONE fenced block:
```proposal
{"rhythm_id": "...", "phase_id": "...", "field": "temp_c" | "ramp_min", "to": <number>}
```
Use only rhythm and phase ids from the context. Keep temperature changes within ±1.5 °C per suggestion."""


def provider(cfg) -> str:
    return "anthropic" if (getattr(cfg, "llm_provider", "openai") == "anthropic") else "openai"


def configured(cfg) -> bool:
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


class OpenAICompatModel:
    """Any OpenAI-compatible /chat/completions endpoint, with function tools (Ollama, LM Studio, hosted APIs)."""

    ROUNDS = 3

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    async def run(self, system: str, user: str, tools: list[dict], on_call: OnCall, max_tokens: int = 1200,
                  timeout: float = 120) -> Reply:
        cfg = self.cfg
        msgs: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        out = Reply("", model=model_name(cfg))
        headers = {"Authorization": f"Bearer {cfg.llm_key}"} if cfg.llm_key else {}
        texts: list[str] = []
        async with httpx.AsyncClient(timeout=timeout) as http:
            for _ in range(self.ROUNDS):
                body = {"model": model_name(cfg), "temperature": 0.3, "max_tokens": max_tokens, "reasoning_effort": "none",
                        "messages": msgs}
                if tools:
                    body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                                       "parameters": t["input_schema"]}} for t in tools]
                r = await http.post(cfg.llm_url.rstrip("/") + "/chat/completions", json=body, headers=headers)
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
                raise RuntimeError("ET_LLM_PROVIDER=anthropic needs the Anthropic SDK: pip install 'embertide[claude]'") from e
            self._client = anthropic.AsyncAnthropic(api_key=self.cfg.llm_key) if self.cfg.llm_key else anthropic.AsyncAnthropic()
        return self._client

    async def run(self, system: str, user: str, tools: list[dict], on_call: OnCall, max_tokens: int = 16000,
                  timeout: float = 240) -> Reply:
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
