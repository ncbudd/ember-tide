"""One Home Assistant connection for all HA devices (Studio → Integrations), and the entity picker.

The owner enters the HA address and one long-lived token once. Devices then say {"conn": "ha"} instead of carrying
their own address and token, so changing the token updates every device. The token is stored on this server only,
never returned by the API (shown as •••) and never logged.
"""

from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

MASK = "•••"


class HaConnBody(BaseModel):
    url: str = Field(min_length=4, max_length=300)
    token: str | None = Field(None, max_length=2000)      # empty / "•••" keeps the stored token


def _clean_url(u: str) -> str:
    u = u.strip().rstrip("/")
    if not re.match(r"^https?://[A-Za-z0-9._\-\[\]:]+(:\d+)?(/.*)?$", u):
        raise HTTPException(422, "Enter the Home Assistant address, e.g. http://homeassistant.local:8123")
    return u


# what fits each device type, best first: (domain, device classes, entity-id words, units)
FITS: dict[str, list[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]]] = {
    "light": [("light", (), (), ()), ("switch", (), (), ())],
    "radar": [("binary_sensor", ("occupancy", "presence"), (), ()), ("binary_sensor", ("motion",), (), ()),
              ("sensor", (), ("presence", "occupancy"), ()),
              # LD2450 / Everything Presence zones and targets, FP2 zone counts: one entity per area of the bed
              ("sensor", (), ("zone", "target_count", "targets"), ()), ("binary_sensor", (), ("zone", "target"), ())],
    # one single-zone sensor per side (e.g. two Aqara FP1E): only occupancy / presence on-off sensors
    "radar_single": [("binary_sensor", ("occupancy", "presence"), (), ()), ("binary_sensor", (), ("occupancy", "presence"), ())],
    "motion": [("binary_sensor", ("motion",), (), ()), ("binary_sensor", ("occupancy", "presence"), (), ())],
    "pad": [("binary_sensor", ("occupancy", "presence"), (), ()), ("binary_sensor", (), ("bed", "pad"), ())],
    "scale": [("sensor", ("weight",), (), ()), ("sensor", (), (), ("kg", "lb", "lbs"))],
    "room": [("sensor", ("temperature", "humidity"), (), ())],
    "wearable": [("sensor", (), ("heart_rate", "heartrate", "_hr", "skin", "hrv"), ())],
    "phone": [("device_tracker", (), (), ()), ("person", (), (), ()), ("binary_sensor", (), ("wifi",), ())],
    "location": [("sensor", (), ("proximity", "distance"), ("km", "mi", "m", "ft")), ("device_tracker", (), (), ()),
                 ("person", (), (), ())],
    "calendar": [("calendar", (), (), ())],
    "leak": [("binary_sensor", ("moisture",), (), ()), ("binary_sensor", (), ("leak", "water", "moisture", "flood", "wet"), ())],
}


# entities that fit by name but are never this kind: the bed's own sensors (and Ember & Tide's published entities) are
# not a watch or ring; a person's own wearable never comes from the bed
NOT_FIT: dict[str, tuple[str, ...]] = {
    "wearable": ("embertide", "orion", "bed", "topper", "mattress", "eight_sleep", "sleep_number", "withings_sleep"),
    "radar": ("embertide",), "radar_single": ("embertide",), "motion": ("embertide",), "pad": ("embertide",), "scale": ("embertide",),
    "room": ("embertide",), "light": ("embertide",), "leak": ("embertide",),
}


def fit_rank(kind: str, e: dict) -> int | None:
    """0 = best fit for this device type, higher = weaker; None = doesn't fit. 'custom' takes everything."""
    if kind == "custom":
        return 0
    dom = e["entity_id"].split(".", 1)[0]
    dc = (e.get("device_class") or "").lower()
    eid = e["entity_id"].lower()
    if any(w in eid or w in str(e.get("name") or "").lower().replace(" ", "_") for w in NOT_FIT.get(kind, ())):
        return None
    unit = (e.get("unit") or "").lower().replace("°", "")
    for i, (d, classes, words, units) in enumerate(FITS.get(kind, [])):
        if dom != d:
            continue
        if classes and dc not in classes:
            continue
        if words and not any(w in eid for w in words):
            continue
        if units and unit not in units:
            continue
        return i
    return None


GUESS_ORDER = ["calendar", "leak", "light", "scale", "room", "location", "phone", "radar", "motion", "pad", "wearable"]


def guess(e: dict) -> tuple[str, str]:
    """Best device type and role for an entity (the person can change it): the type it fits best, best rank first."""
    best = None
    for k in GUESS_ORDER:
        r = fit_rank(k, e)
        if r is not None and (best is None or r < best[1]):
            best = (k, r)
    kind = best[0] if best else "custom"
    dc = (e.get("device_class") or "").lower()
    role = {"light": "light", "scale": "weight", "location": "location", "phone": "home", "radar": "presence", "motion": "presence",
            "pad": "presence", "calendar": "calendar", "leak": "leak"}.get(kind)
    if kind == "room":
        role = "room_humidity" if dc == "humidity" or (e.get("unit") or "") == "%" else "room_temp"
    if kind == "wearable":
        eid = e["entity_id"].lower()
        role = "skin_temp" if "skin" in eid else "wearable_hrv" if "hrv" in eid else "wearable_hr"
    if kind == "location" and e["entity_id"].split(".", 1)[0] in ("device_tracker", "person"):
        kind, role = "phone", "home"                          # home / away states: a phone, not a distance
    return kind, role or "other"


def entity_row(s: dict) -> dict:
    a = s.get("attributes") or {}
    return {"entity_id": s.get("entity_id", ""), "name": a.get("friendly_name") or s.get("entity_id", ""),
            "domain": s.get("entity_id", "").split(".", 1)[0], "device_class": a.get("device_class"),
            "state": s.get("state"), "unit": a.get("unit_of_measurement"), "area": a.get("area") or a.get("area_id")}


def build_ha_router(state) -> APIRouter:
    r = APIRouter(prefix="/api/ha")
    src = lambda: state.sources            # noqa: E731

    def stored() -> dict:
        return state.controller.store.get("ha_connection") or {}

    def client(transport=None, token=None):
        return httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bearer {token}"},
                                 transport=transport if transport is not None else getattr(src(), "http_transport", None))

    async def probe(url: str, token: str) -> dict:
        """Check an address + token: HA's version and how many entities it has, or a plain error."""
        try:
            async with client(token=token) as http:
                a = await http.get(url + "/api/config")
                if a.status_code in (401, 403):
                    return {"ok": False, "error": "Home Assistant refused the token. Make a new long-lived token in your HA profile → Security."}
                if a.status_code != 200:
                    return {"ok": False, "error": f"That address answered {a.status_code}. Is it the Home Assistant address (with :8123)?"}
                cfg = a.json() if a.headers.get("content-type", "").startswith("application/json") else {}
                st = await http.get(url + "/api/states")
                n = len(st.json()) if st.status_code == 200 else None
            return {"ok": True, "version": cfg.get("version"), "location": cfg.get("location_name"), "entities": n}
        except httpx.ConnectError:
            return {"ok": False, "error": "Couldn't reach Home Assistant at that address. Check it (including :8123) and that HA is running."}
        except httpx.TimeoutException:
            return {"ok": False, "error": "Home Assistant didn't answer in time."}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"That didn't work ({type(e).__name__})."}

    def public(c: dict) -> dict:
        users = sum(1 for s in src().configs if s.driver == "ha-entity" and s.params.get("conn") == "ha")
        legacy = [s.id for s in src().configs if s.driver == "ha-entity" and s.params.get("conn") != "ha"
                  and c.get("url") and s.params.get("url", "").rstrip("/") == c["url"]]
        return {"configured": bool(c.get("url") and c.get("token")), "url": c.get("url") or "",
                "token": MASK if c.get("token") else "", "devices": users, "can_migrate": legacy}

    @r.get("/connection")
    async def get_conn():
        return public(stored())

    def _same_place(a: str | None, b: str | None) -> bool:
        """Same scheme, host and port: only then may a saved token be reused (a changed address never receives it)."""
        from urllib.parse import urlsplit
        try:
            x, y = urlsplit(a or ""), urlsplit(b or "")
            return bool(x.hostname) and (x.scheme, x.hostname, x.port) == (y.scheme, y.hostname, y.port)
        except ValueError:
            return False

    @r.put("/connection")
    async def put_conn(b: HaConnBody):
        url = _clean_url(b.url)
        old = stored()
        reuse = b.token in (None, "", MASK)
        if reuse and not _same_place(url, old.get("url")):
            raise HTTPException(422, "The address changed: paste the token again (a saved token is only sent to the address it was saved for).")
        token = old.get("token") if reuse else b.token.strip()
        if not token:
            raise HTTPException(422, "Paste a long-lived token from your Home Assistant profile → Security.")
        test = await probe(url, token)
        if not test["ok"]:
            raise HTTPException(422, test["error"])
        state.controller.store.put("ha_connection", {"url": url, "token": token})
        src().relaunch_shared()
        return {**public({"url": url, "token": token}), "test": test}

    @r.post("/connection/test")
    async def test_conn(b: HaConnBody | None = None):
        old = stored()
        url = _clean_url(b.url) if b and b.url else old.get("url")
        given = b.token if b and b.token not in (None, "", MASK) else None
        if not given and not _same_place(url, old.get("url")):
            raise HTTPException(422, "The address changed: paste the token again to test it.")
        token = given or old.get("token")
        if not url or not token:
            raise HTTPException(422, "Enter the address and a token first.")
        return await probe(url, token)

    @r.delete("/connection")
    async def del_conn():
        state.controller.store.put("ha_connection", None)
        src().relaunch_shared()                  # their next read reports "no connection" in plain words
        return public({})

    @r.post("/connection/migrate")
    async def migrate():
        """Move devices that carry their own copy of the same address + token onto the shared connection."""
        c = stored()
        if not c.get("url"):
            raise HTTPException(409, "Set up the shared connection first.")
        moved = []
        for s in list(src().configs):
            if s.driver == "ha-entity" and s.params.get("conn") != "ha" and s.params.get("url", "").rstrip("/") == c["url"]:
                s.params = {k: v for k, v in s.params.items() if k not in ("url", "token")} | {"conn": "ha"}
                await src().add(s)
                moved.append(s.id)
        return {"moved": moved, **public(c)}

    @r.get("/entities")
    async def entities(kind: str = "custom", q: str = ""):
        c = stored()
        if not c.get("url") or not c.get("token"):
            raise HTTPException(409, "Set up the Home Assistant connection first (Studio → Integrations).")
        try:
            async with client(token=c["token"]) as http:
                x = await http.get(c["url"] + "/api/states")
        except (httpx.ConnectError, httpx.TimeoutException):
            raise HTTPException(502, "Couldn't reach Home Assistant right now.")
        if x.status_code in (401, 403):
            raise HTTPException(502, "Home Assistant refused the token. Update it in Studio → Integrations.")
        if x.status_code != 200:
            raise HTTPException(502, f"Home Assistant answered {x.status_code}.")
        rows = []
        used = {s.params.get("entity_id") for s in src().configs if s.driver == "ha-entity"}
        ql = q.strip().lower()
        for s in x.json() if isinstance(x.json(), list) else []:
            if not isinstance(s, dict) or not s.get("entity_id"):
                continue
            e = entity_row(s)
            rank = fit_rank(kind, e)
            if rank is None:
                continue
            if ql and ql not in e["entity_id"].lower() and ql not in str(e["name"]).lower():
                continue
            gk, gr = guess(e)
            rows.append({**e, "fit": rank, "added": e["entity_id"] in used, "guess_kind": gk, "guess_role": gr})
        rows.sort(key=lambda e: (e["fit"], str(e["name"]).lower()))
        return {"kind": kind, "entities": rows[:300], "total": len(rows)}

    return r
