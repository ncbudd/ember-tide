"""Studio → Integrations: the Home Assistant bridge (owner only)."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


class HaBody(BaseModel):
    host: str = Field("", max_length=200)          # "" turns the bridge off
    port: int = Field(1883, ge=1, le=65535)
    username: str = Field("", max_length=100)
    password: str = Field("", max_length=200)
    keep_password: bool = False                    # leave the saved password as it is
    prefix: str = Field("homeassistant", min_length=1, max_length=60)


def build_integrations_router(state) -> APIRouter:
    r = APIRouter(prefix="/api/integrations")

    def masked() -> dict:
        b = state.ha
        u = urlparse(b.url) if b.url else None
        return {**b.status(), "host": u.hostname if u else "", "port": (u.port or 1883) if u else 1883,
                "username": unquote(u.username or "") if u else "", "password_saved": bool(u and u.password),
                "from_env": bool(state.cfg.ha_mqtt_url)}

    @r.get("/ha")
    async def get_ha():
        return masked()

    @r.put("/ha")
    async def put_ha(b: HaBody):
        if state.cfg.ha_mqtt_url:
            raise HTTPException(409, "Set by ET_HA_MQTT_URL on the server. Change it there.")
        host = b.host.strip().removeprefix("mqtt://").split("/")[0]
        if host and (":" in host or " " in host):
            raise HTTPException(422, "Put just the host name or address here (the port has its own box).")
        import re as _re
        if host and not _re.fullmatch(r"[A-Za-z0-9._-]{1,200}", host):
            raise HTTPException(422, "That doesn't look like a host name or address (e.g. 192.168.1.20 or homeassistant.local).")
        pw = b.password
        if b.keep_password and state.ha.url:
            from urllib.parse import unquote
            pw = unquote(urlparse(state.ha.url).password or "")
        from urllib.parse import quote
        cred = f"{quote(b.username, safe='')}:{quote(pw, safe='')}@" if b.username else ""
        url = f"mqtt://{cred}{host}:{b.port}" if host else None
        state.controller.store.put("ha_bridge", {"url": url, "prefix": b.prefix.strip()})
        await state.ha.restart(url, b.prefix.strip())
        return masked()

    @r.get("/ha/entities")
    async def entities():
        """What Home Assistant will see, for the Studio list and for writing dial substitutions."""
        out = []
        for topic, cfg in state.ha.discovery():
            out.append({"entity_id": cfg["default_entity_id"], "name": cfg.get("name") or "Climate",
                        "device": cfg["device"]["name"]})
        return out

    @r.post("/ha/remove-entities")
    async def remove_entities():
        if state.ha.state != "connected":
            raise HTTPException(409, "Connect to the broker first.")
        n = await state.ha.unannounce()
        return {"removed": n, "note": "They come back the next time the bridge connects, unless you turn it off."}

    @r.post("/ha/announce")
    async def announce():
        if state.ha.state != "connected":
            raise HTTPException(409, "Connect to the broker first.")
        await state.ha.announce()
        state.ha._last.clear()
        return {"ok": True}

    return r
