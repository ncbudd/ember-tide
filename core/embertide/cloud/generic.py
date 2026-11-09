"""Two generic ways in, for any service or device without its own connector:

1. **Inbound webhook**: the owner creates one per source and gets a unique secret URL
   (`POST /api/hooks/<id>/<secret>`, no sign-in: the secret is the key). The body is JSON; a small mapping says
   where the value and time are (`value_path`, `time_path`, optional `items_path` for a list of readings), the unit
   and a scale. Readings go into the wearable store under the chosen person and metric (same duplicate rule).
2. **Poll a JSON URL**: the server fetches a URL every N minutes (≥ 5) with optional header auth (kept on the
   server, masked when shown) and the same mapping.

Paths are dotted with optional list indexes: `data.0.value`, `$.items[2].bpm`, `readings` (empty = the body itself).
Times: epoch seconds or milliseconds, or ISO text; no time path = when it arrived.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)

METRICS = ("skin_temp", "hr", "hrv", "resp_rate", "spo2", "resting_hr", "body_temp", "steps", "active_kcal",
           "exercise_min", "caffeine_mg", "weight_kg", "temp_dev", "readiness")
PATH = r"^[A-Za-z0-9_$.\[\]\- ]{0,120}$"
MAX_BODY = 256 * 1024
MAX_POINTS = 5000


class Mapping(BaseModel):
    metric: Literal[METRICS]                            # type: ignore[valid-type]
    items_path: str = Field("", pattern=PATH)           # where the list of readings is ("" = the body is one reading, or a list)
    value_path: str = Field("value", pattern=PATH)
    time_path: str = Field("", pattern=PATH)
    unit: Literal["", "C", "F"] = ""                    # temperatures only
    scale: float = Field(1.0, gt=0, le=1e6)            # multiply (e.g. 0.001 for grams → kg)


class HookBody(Mapping):
    name: str = Field(min_length=1, max_length=60)
    person: str = Field(min_length=1, max_length=16)
    enabled: bool = True


class PollBody(Mapping):
    name: str = Field(min_length=1, max_length=60)
    person: str = Field(min_length=1, max_length=16)
    url: str = Field(min_length=8, max_length=500)
    headers: dict[str, str] = Field(default_factory=dict)
    interval_min: int = Field(60, ge=5, le=1440)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("the address should start with https:// or http://")
        return v

    @field_validator("headers")
    @classmethod
    def _headers(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 6:
            raise ValueError("at most 6 headers")
        for k, x in v.items():
            if not re.fullmatch(r"[A-Za-z0-9-]{1,60}", k) or len(x) > 1000 or "\n" in x or "\r" in x:
                raise ValueError("header names are letters, numbers and dashes; values one line")
        return v


def get_path(obj: Any, path: str) -> Any:
    """`a.b.0.c` or `$.a.b[0].c`; None when it isn't there."""
    p = (path or "").strip()
    if p.startswith("$"):
        p = p[1:]
    parts = [x for x in re.split(r"\.|\[|\]", p) if x != ""]
    cur = obj
    for k in parts:
        if isinstance(cur, list):
            try:
                cur = cur[int(k)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if k not in cur:
                return None
            cur = cur[k]
        else:
            return None
    return cur


def extract(body: Any, m: Mapping, now: float) -> list[list]:
    """Readings [[time, value], ...] from a JSON body by the mapping."""
    items = get_path(body, m.items_path) if m.items_path else body
    if not isinstance(items, list):
        items = [items]
    out = []
    for it in items[:MAX_POINTS]:
        v = get_path(it, m.value_path) if m.value_path else it
        try:
            v = float(v) * m.scale
        except (TypeError, ValueError):
            continue
        if m.metric in ("skin_temp", "body_temp") and m.unit == "F":
            v = (v - 32) * 5 / 9
        t = get_path(it, m.time_path) if m.time_path else None
        out.append([t if t is not None else now, v])
    return out


def ingest(c, pid: str, device: str, m: Mapping, body: Any, now: float, platform: str = "server") -> dict:
    from ..autopilot_api import WearableBody, _import_wearable
    p = c.person(pid)
    pts = extract(body, m, now)
    if not pts:
        return {"ok": False, "stored": 0, "error": "no readings found with that mapping"}
    unit = "C" if m.metric in ("skin_temp", "body_temp") else None
    rep = _import_wearable(c, p, WearableBody(device=device[:60], metric=m.metric, unit=unit, points=pts, align="off", platform=platform))
    return {"ok": True, "stored": rep["stored"], "received": rep["received"], "rejected": rep["rejected"]}


class Generic:
    """Owner-made webhooks and JSON pollers (kv `cloud_hooks`, `cloud_polls`)."""

    def __init__(self, controller, client: httpx.AsyncClient | None = None) -> None:
        self.c = controller
        self.store = controller.store
        self.client = client

    # ---------- webhooks ----------
    @property
    def hooks(self) -> list[dict]:
        return self.store.get("cloud_hooks", []) or []

    def save_hook(self, b: HookBody, hid: str | None = None) -> dict:
        items = self.hooks
        cur = next((h for h in items if h["id"] == hid), None) if hid else None
        if hid and not cur:
            raise KeyError("unknown webhook")
        h = {**(cur or {"id": "h_" + secrets.token_hex(4), "secret": secrets.token_urlsafe(24)}), **b.model_dump()}
        self.store.put("cloud_hooks", [x for x in items if x["id"] != h["id"]] + [h])
        return h

    def delete_hook(self, hid: str) -> None:
        self.store.put("cloud_hooks", [x for x in self.hooks if x["id"] != hid])

    def receive(self, hid: str, secret: str, body: Any) -> dict:
        h = next((x for x in self.hooks if x["id"] == hid), None)
        if not h or not secrets.compare_digest(str(h.get("secret", "")), str(secret)):
            raise PermissionError("unknown webhook")
        if not h.get("enabled", True):
            return {"ok": False, "stored": 0, "error": "this webhook is turned off"}
        now = self.c.clock.now_ts()
        res = ingest(self.c, h["person"], h["name"], Mapping(**{k: h[k] for k in Mapping.model_fields}), body, now)
        self._mark("cloud_hooks", hid, now, res)
        return res

    # ---------- pollers ----------
    @property
    def polls(self) -> list[dict]:
        return self.store.get("cloud_polls", []) or []

    def save_poll(self, b: PollBody, pid_: str | None = None) -> dict:
        items = self.polls
        cur = next((h for h in items if h["id"] == pid_), None) if pid_ else None
        if pid_ and not cur:
            raise KeyError("unknown source")
        d = b.model_dump()
        if cur:                                   # masked header values sent back unchanged keep the stored ones,
            from urllib.parse import urlsplit     # but only for the same address: a saved key never goes to a new host
            same = urlsplit(cur.get("url", "")).netloc.lower() == urlsplit(d["url"]).netloc.lower()
            d["headers"] = {k: v for k, v in ((k, (cur.get("headers", {}).get(k, "") if same else "") if v.startswith("••••") else v)
                                              for k, v in d["headers"].items()) if v}
        h = {**(cur or {"id": "j_" + secrets.token_hex(4)}), **d}
        self.store.put("cloud_polls", [x for x in items if x["id"] != h["id"]] + [h])
        return h

    def delete_poll(self, pid_: str) -> None:
        self.store.put("cloud_polls", [x for x in self.polls if x["id"] != pid_])

    async def poll(self, src: dict) -> dict:
        now = self.c.clock.now_ts()
        c = self.client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        try:
            r = await c.get(src["url"], headers={"User-Agent": "EmberTide/1", "Accept": "application/json", **(src.get("headers") or {})})
            if r.status_code == 429:
                res = {"ok": False, "stored": 0, "error": "the service is busy (429)", "backoff_until": now + 1800}
            elif r.status_code >= 400:
                res = {"ok": False, "stored": 0, "error": f"answered {r.status_code}"}
            elif len(r.content) > MAX_BODY:
                res = {"ok": False, "stored": 0, "error": "the answer is too big"}
            else:
                res = ingest(self.c, src["person"], src["name"], Mapping(**{k: src[k] for k in Mapping.model_fields}), r.json(), now)
        except (httpx.HTTPError, ValueError) as e:
            res = {"ok": False, "stored": 0, "error": f"couldn't fetch it ({e.__class__.__name__})"}
        finally:
            if c is not self.client:
                await c.aclose()
        self._mark("cloud_polls", src["id"], now, res)
        return res

    async def tick(self) -> None:
        now = self.c.clock.now_ts()
        for s in self.polls:
            if not s.get("enabled", True) or now < (s.get("backoff_until") or 0):
                continue
            if now - (s.get("last_ts") or 0) >= s.get("interval_min", 60) * 60:
                try:
                    await self.poll(s)
                except Exception:  # noqa: BLE001
                    log.exception("json poll %s failed", s.get("id"))

    def _mark(self, key: str, sid: str, now: float, res: dict) -> None:
        items = self.store.get(key, []) or []
        for x in items:
            if x["id"] == sid:
                x.update(last_ts=now, last_ok=res.get("ok"), last_error=res.get("error"), last_stored=res.get("stored", 0),
                         backoff_until=res.get("backoff_until"))
        self.store.put(key, items)


def public_hook(h: dict, base: str) -> dict:
    return {**{k: v for k, v in h.items() if k != "secret"}, "url": f"{base}/api/hooks/{h['id']}/{h['secret']}"}


def public_poll(s: dict) -> dict:
    return {**s, "headers": {k: ("••••" + v[-4:] if len(v) > 8 else "••••") for k, v in (s.get("headers") or {}).items()}}

