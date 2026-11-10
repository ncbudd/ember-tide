"""Bedside dials: which ones Ember & Tide can see, whose side each is by, and whether it's online.

Two ways a dial shows up:
- Home Assistant: an ESPHome device with "dial" in its name (the dial firmware talks to HA, and HA to the bridge).
  Found with one template call (device name, model, firmware version, entity states); older HA without the device
  template functions falls back to entity names.
- Bluetooth: the dials' beacons that the Android app hears at night (dial_beacons: beacon number → side).
Which side a dial is by: what Studio saved (kv `dial_sides`), else what the dial itself reports. Dial firmware from
2026-11 publishes a "Home side" text sensor naming the person it starts on (their HA object id, e.g. embertide_alex);
that person's side is the dial's side. Older firmware doesn't say, so it stays "not set" until picked.
A dial that isn't yours any more (an old setup still in Home Assistant) can be hidden (kv `dial_hidden`, owner only);
it stays listed with hidden: true so it can be shown again. The HA token is the shared connection's
(Studio → Integrations); nothing here asks for it again.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .engine import phone_context as PC

# one line per ESPHome entity: device id | device name | firmware | model | entity id | state | last changed (unix s)
TEMPLATE = ("{% for e in integration_entities('esphome') %}{% set d = device_id(e) %}{% if d %}"
            "{{ d }}|{{ device_attr(d, 'name_by_user') or device_attr(d, 'name') }}|{{ device_attr(d, 'sw_version') }}|"
            "{{ device_attr(d, 'model') }}|{{ e }}|{{ states(e) }}|"
            "{{ (as_timestamp(states[e].last_changed) | int(0)) if states[e] else 0 }}\n{% endif %}{% endfor %}")
OFFLINE = ("unavailable", "unknown", "none", "")
# the shared dial firmware package (no secrets: Wi-Fi comes from the ESPHome secrets file), served for the setup wizard
FILES = {"embertide-dial-common.yaml": ".embertide-dial-common.yaml", "et_dial.h": "et_dial.h"}
DIAL_DIR = Path(__file__).parent / "dial"


class DialSide(BaseModel):
    side: str | None = Field(None, pattern="^(left|right)$")
    beacon: int | None = Field(None, ge=1, le=65535)     # this dial's Bluetooth beacon number (when the dial doesn't say)


class DialHidden(BaseModel):
    hidden: bool


def _ts(v) -> float:
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return 0.0


def _note(d: dict, ent: str, st: str, seen: float) -> None:
    """Fold one entity into its dial: count, online, last seen, and the home side the firmware reports."""
    d["entities"] += 1
    up = st.lower() not in OFFLINE
    d["online"] = d["online"] or up
    d["last_seen"] = max(d.get("last_seen") or 0, seen) or None
    if up and ent.endswith("_home_side"):
        d["home"] = st[:60]
    if up and ent.endswith("_beacon") and st.strip().isdigit() and 1 <= int(st) <= 65535:
        d["beacon"] = int(st)


def parse_template(text: str) -> list[dict]:
    """Group the template's lines into dials (ESPHome devices whose name says dial)."""
    devs: dict[str, dict] = {}
    for line in (text or "").splitlines():
        parts = line.split("|")
        if len(parts) < 6:
            continue
        did, name, sw, model, ent, st = (p.strip() for p in parts[:6])
        if "dial" not in name.lower():
            continue
        d = devs.setdefault(did, {"id": re.sub(r"[^a-z0-9]", "", did.lower())[:40] or "dial", "name": name,
                                  "firmware": None if sw in ("None", "") else sw, "model": None if model in ("None", "") else model,
                                  "entities": 0, "online": False, "last_seen": None, "home": None})
        _note(d, ent, st, _ts(parts[6].strip()) if len(parts) > 6 else 0.0)
    return list(devs.values())


def from_states(states: list) -> list[dict]:
    """Fallback without device templates: entities whose name says dial, grouped by the name up to "dial"."""
    devs: dict[str, dict] = {}
    for s in states if isinstance(states, list) else []:
        if not isinstance(s, dict):
            continue
        eid = str(s.get("entity_id", ""))
        name = str((s.get("attributes") or {}).get("friendly_name") or "")
        if "embertide" in eid or "dial" not in name.lower():
            continue
        m = re.match(r"(.*?\bdial\b(?:\s+main)?)", name, re.I)
        base = (m.group(1) if m else name).strip()
        key = re.sub(r"[^a-z0-9]", "", base.lower())[:40] or "dial"
        d = devs.setdefault(key, {"id": key, "name": base, "firmware": None, "model": None, "entities": 0, "online": False,
                                  "last_seen": None, "home": None})
        try:
            seen = datetime.fromisoformat(str(s.get("last_changed") or "").replace("Z", "+00:00")).timestamp()
        except ValueError:
            seen = 0.0
        _note(d, eid, str(s.get("state", "")), seen)
    return list(devs.values())


def build_dials_router(state) -> APIRouter:
    r = APIRouter(prefix="/api")

    def store():
        return state.controller.store

    async def ha_dials() -> tuple[list[dict], str | None]:
        c = store().get("ha_connection") or {}
        if not (c.get("url") and c.get("token")):
            return [], None
        transport = getattr(state.sources, "http_transport", None)
        try:
            async with httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bearer {c['token']}"}, transport=transport) as http:
                t = await http.post(c["url"] + "/api/template", json={"template": TEMPLATE})
                if t.status_code == 200:
                    return parse_template(t.text), None
                if t.status_code in (401, 403):
                    return [], "Home Assistant refused the saved token. Update it in Studio → Integrations → Home Assistant."
                st = await http.get(c["url"] + "/api/states")
                return (from_states(st.json()), None) if st.status_code == 200 else ([], f"Home Assistant answered {st.status_code}.")
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):         # unreachable, bad address, or a reply that isn't JSON
            return [], "Couldn't reach Home Assistant right now."

    def reported_side(home: str | None) -> str | None:
        """The side of the person a dial says it starts on (its "Home side" sensor names their HA object id)."""
        if not home:
            return None
        people = {p.id: p for p in state.controller.household.people}
        oids = store().get("ha_object_ids") or {}
        pid = next((k for k, v in oids.items() if v == home), None)
        if pid in people:
            return people[pid].zone
        m = re.fullmatch(r"embertide_(left|right)", home)              # the package's defaults, before names are set
        return m.group(1) if m else None

    @r.get("/dials")
    async def dials():
        sides = store().get("dial_sides") or {}
        hidden = set(store().get("dial_hidden") or [])
        found, err = await ha_dials()
        links = dict(store().get("dial_beacon_of") or {})
        beacons = [dict(b) for b in PC.dial_beacons(store())]
        changed = False
        for d in found:
            d["reported_side"] = reported_side(d.pop("home", None))
            d["side"] = sides.get(d["id"]) or d["reported_side"]
            d["side_from"] = "set" if d["id"] in sides else "dial" if d["reported_side"] else None
            d["hidden"] = d["id"] in hidden
            # which beacon is this dial's: what its firmware says, else what was stored (wizard / picked in Studio),
            # else a beacon already named after it
            minor, frm = d.pop("beacon", None), "dial"
            if minor is None and d["id"] in links:
                minor, frm = links[d["id"]].get("minor"), "set"
            # a beacon named after a dial counts only for a shown dial whose name is unique (old setups often share
            # a name with the current dial, and must never take its beacon)
            same_name = [x for x in found if x["name"] == d["name"] and x["id"] not in hidden]
            if minor is None and d["id"] not in hidden and len(same_name) == 1:
                minor, frm = next((b["minor"] for b in beacons if b.get("name") == d["name"]), None), "set"
            d["beacon"], d["beacon_from"] = minor, (frm if minor else None)
            if d["id"] in hidden:                    # a hidden dial keeps whatever it reports but claims nothing
                d["beacon"], d["beacon_from"] = (minor, frm) if frm == "dial" else (None, None)
                continue
            if minor and links.get(d["id"]) != {"minor": minor, "name": d["name"]}:
                links = {k: v for k, v in links.items() if v.get("minor") != minor}
                links[d["id"]], changed = {"minor": minor, "name": d["name"]}, True
            b = next((x for x in beacons if x["minor"] == minor), None) if minor else None
            if not d["side"] and b and b.get("side") in ("left", "right"):
                d["side"], d["side_from"] = b["side"], "beacon"           # its beacon's side, set by hand earlier
            if minor and d["side"] and (b is None or b.get("side") != d["side"] or b.get("name") != d["name"]):
                beacons = [x for x in beacons if x["minor"] != minor] + [{"minor": minor, "side": d["side"], "name": d["name"]}]
                changed = True                                 # one thing: the beacon is on the side its dial is by
        if changed:
            store().put("dial_beacon_of", links)
            store().put("dial_beacons", sorted(beacons, key=lambda x: x["minor"])[:8])
        heard: dict[int, dict] = {}
        for p in state.controller.household.people:
            cur = PC.latest(store(), p.id) or {}
            for s in ((cur.get("data") or {}).get("dials") or []):
                m = int(s.get("minor", 0))
                if m and (m not in heard or (s.get("seen_at") or 0) > heard[m]["seen_at"]):
                    heard[m] = {"person": p.id, "rssi": s.get("rssi"), "seen_at": s.get("seen_at") or 0}
        for d in found:
            d["heard"] = heard.get(d["beacon"]) if d["beacon"] else None
        dial_of = {v.get("minor"): {"id": k, "name": v.get("name")} for k, v in links.items()}
        beacons = [{**b, "heard": heard.get(int(b["minor"])), "dial": dial_of.get(int(b["minor"]))} for b in PC.dial_beacons(store())]
        bridge = getattr(state, "ha", None)
        return {"ha_configured": bool((store().get("ha_connection") or {}).get("token")), "ha_error": err,
                "bridge": getattr(bridge, "state", "off") if bridge else "off", "dials": found, "beacons": beacons}

    @r.get("/dials/files/{name}")
    async def dial_file(name: str):
        """The shared package for a new dial: save it next to the dial's own file in ESPHome (with the name given in
        the download, starting with a dot so the ESPHome dashboard doesn't list it as a device)."""
        if name not in FILES:
            raise HTTPException(404, "unknown file")
        return PlainTextResponse((DIAL_DIR / name).read_text(encoding="utf-8"),
                                 headers={"Content-Disposition": f'attachment; filename="{FILES[name]}"'})

    @r.put("/dials/{did}")
    async def set_side(did: str, b: DialSide):
        if not re.fullmatch(r"[a-z0-9]{1,40}", did):
            raise HTTPException(422, "bad dial id")
        sides = dict(store().get("dial_sides") or {})
        if b.side:
            sides[did] = b.side
        else:
            sides.pop(did, None)
        if len(sides) > 16:
            raise HTTPException(422, "too many dials")
        store().put("dial_sides", sides)
        links = dict(store().get("dial_beacon_of") or {})
        if b.beacon:                                           # "this dial's beacon number", from the wizard or Studio
            name = (links.get(did) or {}).get("name") or did
            links = {k: v for k, v in links.items() if v.get("minor") != b.beacon and k != did}
            links[did] = {"minor": b.beacon, "name": name}
            store().put("dial_beacon_of", links)
        link = links.get(did)
        if link and b.side:                                    # its beacon is on the same side
            rest = [x for x in PC.dial_beacons(store()) if x["minor"] != link["minor"]]
            store().put("dial_beacons", sorted(rest + [{"minor": link["minor"], "side": b.side, "name": (link.get("name") or "")[:40]}],
                                               key=lambda x: x["minor"])[:8])
        return {"id": did, "side": b.side, "beacon": link["minor"] if link else None}

    @r.put("/dials/{did}/hidden")
    async def set_hidden(did: str, b: DialHidden):
        """Hide a dial that isn't yours any more (an old ESPHome setup still in Home Assistant), or show it again.
        Only Ember & Tide's list changes; Home Assistant keeps the device until it's deleted there. Owner only."""
        if not re.fullmatch(r"[a-z0-9]{1,40}", did):
            raise HTTPException(422, "bad dial id")
        hidden = [x for x in (store().get("dial_hidden") or []) if x != did]
        if b.hidden:
            hidden.append(did)
        if len(hidden) > 32:
            raise HTTPException(422, "too many hidden dials")
        store().put("dial_hidden", hidden)
        return {"id": did, "hidden": b.hidden}

    return r
