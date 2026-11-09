"""Studio endpoints: data sources & calibration, the multi-source explorer, sensor agreement, insights, AI."""

from __future__ import annotations

from datetime import date as _date
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import ai
from .engine.guide import apply_schedule, tuning_guide
from .engine.insights import apply_proposal, insights
from .engine.rhythm import night_date, pick_rhythm, resolve
from .engine.summary import _moments, household_pets, night_window, recent
from typing import Literal

from .sources import DEVICE_TYPES, ROLE_INFO, TYPES, WEARABLE_ROLES, Calibration, SourceConfig, votes

# Explorer series colours: theme variables, so every line follows light and dark mode and keeps >= 4.5:1 contrast
PALETTE = {"target": "var(--acc)", "setpoint": "var(--faint)", "measured": "var(--text)", "hr": "var(--warm)", "br": "var(--pc-violet)",
           "room_temp": "var(--pc-blue)", "room_humidity": "var(--pc-teal)", "wearable_hr": "var(--pc-gold)", "presence": "var(--pc-green)",
           "weight": "var(--pc-violet)", "skin_temp": "var(--pc-rose)", "wearable_hrv": "var(--pc-green)", "home": "var(--good)",
           "location": "var(--pc-blue)", "calendar": "var(--pc-violet)", "light": "var(--warn)", "other": "var(--mute)"}


class HaTest(BaseModel):
    url: str
    token: str
    entity_id: str


class GroupPart(BaseModel):
    zone: Literal["left", "right"]
    params: dict = Field(default_factory=dict)
    id: str | None = Field(None, max_length=40)     # an existing source becomes this side (keeps its history)


class GroupBody(BaseModel):
    """One device that watches both sides: a radar's left and right zones, a scale's load cells per side."""
    name: str = Field(min_length=1, max_length=80)
    kind: str
    role: str | None = None
    driver: Literal["ha-entity", "mqtt-topic", "sim"]
    device: str | None = Field(None, max_length=80)
    tuning: dict = Field(default_factory=dict)
    enabled: bool = True
    # how the sides are watched: "zones" = one radar with a zone per side; "two_sensors" = two single-zone sensors,
    # one by each nightstand (fusion then allows for each seeing a little of the partner). None = keep what it was.
    layout: Literal["zones", "two_sensors"] | None = None
    parts: list[GroupPart] = Field(min_length=1, max_length=2)


class ApplyBody(BaseModel):
    proposal: dict
    source: str = "studio"
    insight_id: str | None = None


class StageSeg(BaseModel):
    start: float | str          # seconds, milliseconds, or ISO time
    end: float | str
    stage: str | int


class StagesBody(BaseModel):
    source: str = Field(max_length=60)                 # e.g. "health-connect", "oura", "pixel-watch"
    label: str | None = Field(None, max_length=120)    # shown in Studio, e.g. "Pixel Watch · Health Connect"
    segments: list[StageSeg] = Field(max_length=5000)  # a week of 30-second stages fits; a flood doesn't


class AskBody(BaseModel):
    person_id: str = Field(max_length=40)
    question: str = Field(min_length=1, max_length=1000)


def thin(pts: list, kind: str, budget: int, keep_every_s: float = 300) -> list:
    """Fewer points, same picture. Step series keep every change plus one point at least every `keep_every_s` (so the
    explorer still sees how often the device reports, and real gaps stay gaps). Line series drop the inside of flat
    runs, then, if still over `budget`, keep the first, lowest and highest point of each time bucket (peaks survive)."""
    if len(pts) <= 2:
        return pts
    if kind == "step":
        out = [pts[0]]
        for i in range(1, len(pts) - 1):
            if pts[i][1] != out[-1][1] or pts[i][0] - out[-1][0] >= keep_every_s or pts[i + 1][0] - pts[i][0] > keep_every_s:
                out.append(pts[i])
        out.append(pts[-1])
        return out
    flat = [pts[0]]
    for i in range(1, len(pts) - 1):
        if not (pts[i][1] == pts[i - 1][1] == pts[i + 1][1] and pts[i + 1][0] - pts[i - 1][0] <= keep_every_s * 2):
            flat.append(pts[i])
    flat.append(pts[-1])
    if len(flat) <= budget:
        return flat
    span = flat[-1][0] - flat[0][0] or 1.0
    nb = max(1, budget // 3)
    buckets: dict[int, list] = {}
    for p in flat:
        buckets.setdefault(min(nb - 1, int((p[0] - flat[0][0]) / span * nb)), []).append(p)
    out = []
    for b in sorted(buckets):
        grp = buckets[b]
        keep = {id(grp[0]): grp[0], id(min(grp, key=lambda q: q[1])): min(grp, key=lambda q: q[1]),
                id(max(grp, key=lambda q: q[1])): max(grp, key=lambda q: q[1])}
        out += sorted(keep.values(), key=lambda q: q[0])
    if out[-1] is not flat[-1]:
        out.append(flat[-1])
    return out


def build_studio_router(state) -> APIRouter:
    r = APIRouter(prefix="/api")
    ctl = lambda: state.controller          # noqa: E731
    src = lambda: state.sources             # noqa: E731

    def person(pid: str):
        try:
            return ctl().person(pid)
        except KeyError:
            raise HTTPException(404, "unknown person")

    # ---------- sources ----------
    @r.get("/sources")
    async def list_sources():
        return {"sources": [src().describe(c) for c in src().configs], "roles": ROLE_INFO, "types": DEVICE_TYPES,
                "calibration": src().calibration.model_dump()}

    def _clean(c: SourceConfig) -> SourceConfig:
        """Check a device against its type: a sensible role, a side when it needs one, tuning within range."""
        t = TYPES.get(c.kind)
        if not t:
            raise HTTPException(422, "Unknown device type.")
        roles = t.get("roles") or [t["role"]]
        if c.role not in roles:
            c.role = roles[0]
        if c.driver == "ha-entity":
            if not c.params.get("entity_id"):
                raise HTTPException(422, "Pick the Home Assistant entity this device reads.")
            if c.params.get("conn") == "ha":
                if not src().ha_conn():
                    raise HTTPException(422, "Set up the Home Assistant connection first (Studio → Integrations).")
                c.params = {k: v for k, v in c.params.items() if k not in ("url", "token")}   # never a second copy
            elif not c.params.get("url"):
                raise HTTPException(422, "A Home Assistant device needs the HA address and an entity ID.")
        if c.driver == "ics":
            if c.role != "calendar":
                raise HTTPException(422, "a calendar feed can only be a calendar device")
            from .sources import ics_url
            try:
                ics_url(c.params.get("url", ""))
            except ValueError as e:
                raise HTTPException(422, str(e))
        if c.driver == "mqtt-topic" and not (c.params.get("url") and c.params.get("topic")):
            raise HTTPException(422, "An MQTT device needs the broker address and a topic.")
        votes_now = t["votes"] or (t["votes"] is None and c.role in ("presence", "weight"))
        if votes_now and c.zone not in ("left", "right"):
            raise HTTPException(422, "Which side of the bed is it watching? Pick left or right.")
        clean = {}
        for f in t["tuning"]:
            if f["key"] not in c.tuning:
                continue
            v = c.tuning[f["key"]]
            if f["type"] == "number":
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    raise HTTPException(422, f"{f['label']}: enter a number.")
                v = max(f.get("min", v), min(f.get("max", v), v))
            elif f["type"] == "bool":
                v = bool(v)
            elif f["type"] == "map":
                if not isinstance(v, dict) or len(v) > 12:
                    raise HTTPException(422, f"{f['label']}: up to 12 values.")
                clean_map = {}
                for k, m in v.items():
                    if m not in ("yes", "no", "ignore"):
                        raise HTTPException(422, f"{f['label']}: each value means yes, no or ignore.")
                    clean_map[str(k).strip().lower()[:30]] = m
                v = clean_map
            elif f["type"] == "select":
                if v not in f.get("options", []):
                    raise HTTPException(422, f"{f['label']}: pick one of {', '.join(f['options'])}.")
            else:
                v = "" if v is None else str(v)[:20]
            clean[f["key"]] = v
        if clean.get("value_map"):
            clean["invert"] = False          # the map says what each value means, already the right way round
        c.tuning = clean
        if not t.get("side"):
            c.zone = None
        if not t.get("person") and c.kind != "custom":
            c.person = None
        # who it's for: a person's own device names that person; a calendar may be shared (no person = everyone)
        people = {p.id: p for p in ctl().household.people}
        if c.person is not None and c.person not in people:
            raise HTTPException(422, "That person isn't in this household.")
        personal = c.kind in ("wearable", "phone", "location") or (c.kind == "custom" and c.role in WEARABLE_ROLES)
        if personal and c.person is None:
            raise HTTPException(422, "Whose is it? Pick the person it belongs to.")
        if personal and t.get("side") and c.person:
            c.zone = people[c.person].zone          # a watch or ring follows its owner's side
        return c

    def _same_input(a: SourceConfig, b: SourceConfig) -> bool:
        """Do two devices read exactly the same thing (same entity + attribute, same feed, same topic + path)?"""
        if a.driver != b.driver or a.role != b.role:
            return False
        pa, pb = a.params, b.params
        if a.driver == "ha-entity":
            return pa.get("entity_id") == pb.get("entity_id") and (pa.get("attribute") or "") == (pb.get("attribute") or "")
        if a.driver == "ics":
            def feed(u: str) -> str:            # webcal:// = https://, and http/https, host case, a trailing / don't matter
                u = re.sub(r"^(webcal|https?)://", "", (u or "").strip(), flags=re.I).rstrip("/")
                host, _, rest = u.partition("/")
                return host.lower() + "/" + rest
            return feed(pa.get("url")) == feed(pb.get("url"))
        if a.driver == "mqtt-topic":
            return pa.get("topic") == pb.get("topic") and (pa.get("json_path") or "") == (pb.get("json_path") or "")
        return False

    @r.post("/sources")
    async def add_source(c: SourceConfig):
        c = _clean(c)
        dup = next((x for x in src().configs if x.id != c.id and _same_input(x, c)), None)
        if dup:
            raise HTTPException(409, f"That's already added as \"{dup.name}\". Use Edit on it to change how it's used.")
        await src().add(c)
        return src().describe(c)

    def _host(u) -> tuple | None:
        from urllib.parse import urlsplit
        try:
            x = urlsplit(u or "")
            return (x.scheme, x.hostname, x.port) if x.hostname else None
        except ValueError:
            return None

    @r.put("/sources/{sid}")
    async def update_source(sid: str, c: SourceConfig):
        old = src().get(sid)
        if not old:
            raise HTTPException(404, "unknown source")
        # keep secrets if the client sent the masked value back (token, password, a calendar link shown as host/•••)
        for k, v in list(c.params.items()):
            if isinstance(v, str) and "•••" in v and k in old.params:
                c.params[k] = old.params[k]
        for k in ("token", "password", "url"):
            if k in old.params and k not in c.params and c.driver == old.driver and c.params.get("conn") != "ha":
                c.params[k] = old.params[k]                  # left out entirely: unchanged
        # a saved token or password only ever goes to the address it was saved for
        if _host(c.params.get("url")) != _host(old.params.get("url")):
            for k in ("token", "password"):
                if k in old.params and c.params.get(k) == old.params[k]:
                    raise HTTPException(422, "The address changed: enter the token or password again.")
        c.id = sid
        c = _clean(c)
        dup = next((x for x in src().configs if x.id != sid and _same_input(x, c)), None)    # never itself
        if dup:
            raise HTTPException(409, f"That's already added as \"{dup.name}\". Edit that one instead.")
        await src().add(c)                       # same id: its stored readings (history) stay attached
        return src().describe(c)

    @r.delete("/sources/{sid}")
    async def delete_source(sid: str):
        await src().remove(sid)
        return {"ok": True}

    async def _put_group(gid: str, b: GroupBody) -> dict:
        if not re.fullmatch(r"[a-z0-9-]{1,40}", gid):
            raise HTTPException(422, "bad group id")
        t = TYPES.get(b.kind)
        if not t:
            raise HTTPException(422, "Unknown device type.")
        if len({p.zone for p in b.parts}) != len(b.parts):
            raise HTTPException(422, "Each side can only be mapped once.")
        members = {c.id for c in src().configs if c.group == gid}
        olds = [src().get(p.id) for p in b.parts if p.id]
        donor = next((o for o in olds if o), None) or next((c for c in src().configs if c.group == gid), None)
        layout = b.layout or (donor.params.get("layout") if donor and donor.group == gid else None) or "zones"
        if layout == "two_sensors" and (b.role or t["role"]) != "presence":
            raise HTTPException(422, "Two sensors, one per side, is for presence sensors (radar, pad or motion).")
        built: list[SourceConfig] = []
        for part in b.parts:
            old = src().get(part.id) if part.id else None
            if part.id and (not old or (old.group not in (None, gid))):
                raise HTTPException(422, "That device belongs to something else.")
            params = dict(part.params)
            keep = old or donor                                 # masked secrets sent back: keep the stored ones
            if keep:                                            # (a new side borrows them from the other side)
                for k, v in list(params.items()):
                    if isinstance(v, str) and "•••" in v and k in keep.params:
                        params[k] = keep.params[k]
            params.pop("layout", None)
            if layout == "two_sensors":
                params["layout"] = "two_sensors"                # read by the UI and by fusion (crosstalk)
            c = SourceConfig(**({"id": old.id} if old else {}), name=b.name.strip(), driver=b.driver, role=b.role or t["role"],
                             kind=b.kind, zone=part.zone, device=(b.device or "").strip() or None, tuning=dict(b.tuning),
                             params=params, enabled=b.enabled, group=gid)
            c = _clean(c)
            if not votes(c):
                raise HTTPException(422, "Only a device that watches the bed (radar, scale, pad, motion) can be split into sides.")
            built.append(c)
        if len(built) == 2 and _same_input(built[0], built[1]):
            raise HTTPException(422, "The left and right side can't read the same sensor: pick a different one for each side."
                                if layout == "two_sensors" else
                                "The left and right side can't read the same thing: pick a different zone or entity for each.")
        mine = members | {c.id for c in built}
        for c in built:
            dup = next((x for x in src().configs if x.id not in mine and _same_input(x, c)), None)
            if dup:
                raise HTTPException(409, f"That's already added as \"{dup.name}\". Edit that one instead.")
        for sid in members - {c.id for c in built}:
            await src().remove(sid)                             # a side no longer mapped
        for c in built:
            await src().add(c)
        return {"group": gid, "layout": layout, "sources": [src().describe(c) for c in built]}

    @r.post("/sources/group")
    async def add_group(b: GroupBody):
        import uuid
        return await _put_group(uuid.uuid4().hex[:8], b)

    @r.put("/sources/group/{gid}")
    async def update_group(gid: str, b: GroupBody):
        return await _put_group(gid, b)

    @r.delete("/sources/group/{gid}")
    async def delete_group(gid: str):
        ids = [c.id for c in src().configs if c.group == gid]
        for sid in ids:
            await src().remove(sid)
        return {"ok": True, "removed": len(ids)}


    @r.post("/sources/test-ha")
    async def test_ha(b: HaTest):
        try:
            async with httpx.AsyncClient(timeout=8, headers={"Authorization": f"Bearer {b.token}"}) as http:
                x = await http.get(b.url.rstrip("/") + f"/api/states/{b.entity_id}")
            if x.status_code == 401:
                return {"ok": False, "error": "Home Assistant rejected the token. Create a long-lived token under your HA profile → Security."}
            if x.status_code == 404:
                return {"ok": False, "error": f"Home Assistant has no entity called {b.entity_id}. Check the ID under Settings → Entities."}
            if x.status_code != 200:
                return {"ok": False, "error": f"Home Assistant answered with an error ({x.status_code})."}
            d = x.json()
            return {"ok": True, "state": d.get("state"), "unit": d.get("attributes", {}).get("unit_of_measurement"),
                    "name": d.get("attributes", {}).get("friendly_name")}
        except httpx.ConnectError:
            return {"ok": False, "error": "Couldn't reach Home Assistant at that address. Check the URL (including :8123) and that HA is running."}
        except httpx.TimeoutException:
            return {"ok": False, "error": "Home Assistant didn't answer in time. Is the address right?"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"That didn't work: {e}"}

    # ---------- calibration ----------
    @r.get("/calibration")
    async def get_cal():
        return src().calibration.model_dump()

    @r.put("/calibration")
    async def put_cal(c: Calibration):
        import math

        def ok(v, lo, hi):
            return isinstance(v, (int, float)) and math.isfinite(v) and lo <= v <= hi
        bad = ([k for k, v in c.weights.items() if not ok(v, 0, 5)] + ([] if ok(c.weight_threshold_kg, 1, 300) else ["weight_threshold_kg"])
               + [k for k, v in c.tare_kg.items() if not ok(v, -500, 500)] + [k for k, v in c.person_kg.items() if not ok(v, 20, 400)])
        if bad or len(c.weights) + len(c.tare_kg) + len(c.person_kg) > 200:
            raise HTTPException(422, f"calibration values out of range: {', '.join(map(str, bad[:5])) or 'too many entries'}")
        c.tare_kg = c.tare_kg or src().calibration.tare_kg
        c.empty_kg = {k: v for k, v in (c.empty_kg or src().calibration.empty_kg).items() if ok(v, -100, 200)}
        c.side_kg = {k: v for k, v in (c.side_kg or src().calibration.side_kg).items() if ok(v, 10, 400)}
        src().scales.clear()
        src().calibration = c
        src().save()
        return c.model_dump()

    @r.post("/calibration/tare/{sid}")
    async def tare(sid: str):
        c = src().get(sid)
        if not c or c.role != "weight":
            raise HTTPException(400, "only load-cell (weight) sources can be zeroed")
        l = src().latest.get(sid, {})
        if l.get("value") is None:
            raise HTTPException(409, "no reading yet")
        from .sources import tuning_for
        thr = float(tuning_for(c.kind, c.tuning).get("threshold_kg") or src().calibration.weight_threshold_kg)
        if l["value"] >= thr:
            raise HTTPException(409, f"it reads {l['value']:.0f} kg, so someone may be on that side. Zero it with the bed empty.")
        cal = src().calibration
        cal.tare_kg[sid] = round(cal.tare_kg.get(sid, 0.0) + l["value"], 2)
        cal.empty_kg.pop(sid, None)                      # zeroed by hand: the learned empty-bed zero starts again from here
        src().scales.pop(sid, None)
        src().save()
        return cal.model_dump()

    # ---------- explorer ----------
    @r.get("/explore")
    async def explore(request: Request, person_id: str, date: str | None = None, points: int = 2000):
        c = ctl()
        p = person(person_id)
        who = request.scope.get("state", {}).get("principal")
        if who is not None and who.role != "owner" and who.kind != "open" and who.person != p.id:
            raise HTTPException(403, "That's their own sleep data.")          # (auth.PRIVATE_READS, as a query)
        points = max(50, min(points, 5000))
        tz, now = c.clock.tz, c.clock.now_ts()
        d = _date.fromisoformat(date) if date else night_date(c.clock.now())
        win = night_window(p, d, tz, now)
        if not win:
            raise HTTPException(404, "no rhythm that night")
        start, end = win
        rows = c.store.history(p.zone, start, end, person=p.id)
        bed_name = c.driver.name
        # left/right as you lie: some beds name their sides from the foot (household setting, bed_api.py)
        flip = bool(c.store.get("bed_from_foot") or False)
        zone_label = f"{({'left': 'right', 'right': 'left'}[p.zone] if flip else p.zone)} side"

        budget = max(200, min(20000, points))

        def series(id_, label, source, device, driver, unit, lane, key, kind="line", conv=None):
            pts = thin([[x["ts"], (conv(x[key]) if conv else x[key])] for x in rows if x[key] is not None], kind, budget)
            return {"id": id_, "label": label, "source": source, "device": device, "driver": driver, "unit": unit,
                    "lane": lane, "kind": kind, "color": PALETTE.get(id_, "var(--mute)"), "points": pts}

        out = [
            series("target", "Planned temperature", "What the rhythm asks for at this time", f"Rhythm · {p.name}", "engine", "°C", "temp", "target"),
            series("setpoint", "Temperature sent to the bed", f"{bed_name} · {zone_label}", "Topper controller", c.driver.id, "°C", "temp", "setpoint"),
            series("measured", "Bed temperature (measured)", f"{bed_name} · {zone_label}, sensor in the pad", "Topper controller", c.driver.id, "°C", "temp", "measured"),
            series("hr", "Heart rate (from the bed)", f"{bed_name} · sensor strip under the cover, {zone_label}", "Under-cover sensor strip", c.driver.id, "bpm", "body", "hr"),
            series("br", "Breathing rate (from the bed)", f"{bed_name} · sensor strip under the cover, {zone_label}", "Under-cover sensor strip", c.driver.id, "/min", "body", "br"),
            series("bed_occ", "In bed: bed's own sensor", f"{bed_name} · sensor strip, cleaned up (must hold 3 s; repeats and stale readings ignored)", "Under-cover sensor strip", c.driver.id, "", "presence", "bed_occ", "step"),
            series("occupied", "In bed: final answer", "All in-bed sensors together (each one's vote weighted by how much it counts)", "In-bed decision", "engine", "", "presence", "occupied", "step"),
            series("status", "Bed sensor reading (unfiltered)", f"{bed_name} · sensor strip as it reports it: 0 in bed, 4 moving, 5 empty, 6 turned over", "Under-cover sensor strip", c.driver.id, "code", "raw", "status", "step"),
        ]
        for sc in src().configs:
            if sc.zone not in (None, p.zone) or (sc.person not in (None, p.id)):
                continue
            info = ROLE_INFO[sc.role]
            pts = thin([[t, v] for t, v in c.store.samples(sc.id, start, end)], "step" if sc.role in ("presence", "light") else "line", budget)
            out.append({"id": f"src:{sc.id}", "label": sc.name, "source": f"{sc.device or sc.driver} · {sc.driver}" + (f" · {sc.params.get('entity_id')}" if sc.params.get("entity_id") else "") + (f" · {sc.params.get('topic')}" if sc.params.get("topic") else ""),
                        "device": sc.device or sc.name, "driver": sc.driver, "unit": sc.unit or info["unit"], "lane": info["lane"],
                        "kind": "step" if sc.role in ("presence", "light") else "line", "color": PALETTE.get(sc.role, "var(--mute)"), "points": pts, "role": sc.role})
        events = [e for e in c.store.events(since=start, limit=3000) if e["ts"] <= end and e.get("person") in (p.id, None)]
        rh = pick_rhythm(p.rhythms, d)
        n = resolve(rh, d, tz) if rh else None
        from .engine.stages import collect
        await _maybe_fetch_orion(p, d, win)
        stages = collect(c.store, p, start, end)
        return {"date": d.isoformat(), "start": start, "end": end, "now": now, "tz": str(tz), "person": p.id, "zone": p.zone, "stages": stages,
                "series": out, "moments": _moments(events, tz, zone=p.zone, pets=household_pets(c.store), until=end),
                "phases": [{"id": ph.id, "name": ph.name, "start": ph.start, "temp_c": ph.temp_c} for ph in (n.phases if n else [])],
                "schedule": {"bedtime": n.bedtime, "wake": n.wake, "window_start": n.window_start, "window_end": n.window_end} if n else None}

    @r.get("/people/{pid}/stages")
    async def get_stages(pid: str, date: str | None = None):
        from .engine.stages import collect
        c = ctl()
        p = person(pid)
        d = _date.fromisoformat(date) if date else night_date(c.clock.now())
        win = night_window(p, d, c.clock.tz, c.clock.now_ts())
        if not win:
            return []
        await _maybe_fetch_orion(p, d, win)
        return collect(c.store, p, *win)

    @r.post("/people/{pid}/stages")
    async def import_stages(pid: str, b: StagesBody):
        """For the Android app (Health Connect), Oura sync, Shortcuts or scripts: push a night of stages."""
        from . import align as A
        from .engine.stages import normalize_stage
        p = person(pid)
        c = ctl()
        tz, now = c.clock.tz, c.clock.now_ts()
        bad = 0
        segs = []
        for g in b.segments:
            st = normalize_stage(g.stage)
            a0, a1 = A.parse_time(g.start, tz), A.parse_time(g.end, tz)
            if not st or a0 is None or a1 is None or a1 <= a0 or a1 - a0 > 12 * 3600 or a1 > now + 300:
                bad += 1
                continue
            segs.append({"start": a0, "end": a1, "stage": st})
        if not segs:
            raise HTTPException(400, "no usable segments (stages must be awake / light / deep / rem or Health Connect codes, with valid times)")
        segs.sort(key=lambda x: x["start"])
        overlaps = sum(1 for x, y in zip(segs, segs[1:]) if y["start"] < x["end"] - 60)
        # line the night up with the bed: how much of the time in bed it covers, and whether it sits an hour off
        rows = c.store.history(p.zone, segs[0]["start"] - 6 * 3600, segs[-1]["end"] + 6 * 3600, person=p.id)
        spans = A.in_bed_spans(rows)
        warnings, cov = [], None
        if spans:
            def covered(shift: float) -> float:
                tot = sum(y - x for x, y in spans)
                hit = 0.0
                for x, y in spans:
                    for g in segs:
                        lo, hi = max(x, g["start"] + shift), min(y, g["end"] + shift)
                        if hi > lo:
                            hit += hi - lo
                return hit / tot if tot else 0.0
            cov = round(covered(0), 2)
            best = max(range(-12, 13), key=lambda h: covered(h * 3600))
            if best and covered(best * 3600) >= cov + 0.25:
                warnings.append(f"These stages line up much better with your time in bed if moved {best:+d} h: probably a time-zone mistake in the export. Nothing was moved.")
            if cov < 0.5:
                warnings.append(f"They cover only {round(cov * 100)}% of your time in bed.")
        if overlaps:
            warnings.append(f"{overlaps} segment(s) overlap the one before.")
        c.store.put_stages(p.id, b.source, b.label or b.source, segs, segs[0]["start"], segs[-1]["end"])
        compared = _after_stages(c, p, segs)
        return {"ok": True, "stored": len(segs), "rejected": bad, "coverage_in_bed": cov, "warnings": warnings,
                "compared": compared}

    _orion_tried: set[str] = set()

    async def _maybe_fetch_orion(p, d, win):
        """With the cloud driver, pull Orion's own staging for that night once (needs Orion Intelligence)."""
        drv = ctl().driver
        key = f"{p.id}:{d.isoformat()}"
        if getattr(drv, "id", "") != "orion-cloud" or key in _orion_tried:
            return
        _orion_tried.add(key)
        from .engine.stages import parse_orion_hypnogram
        try:
            r2 = await drv._http.get("/v2/insights", params={"from": d.isoformat(), "to": d.isoformat()})
            data = r2.json().get("data", {})
            segs = []
            for day in data.values():
                for sess in day.get("sessions", []) or []:
                    segs += parse_orion_hypnogram((sess.get("sleep_summary") or {}).get("hypnogram") or sess.get("hypnogram"))
            if segs:
                ctl().store.put_stages(p.id, "orion", "Orion · cloud sleep staging", segs, *win)
        except Exception:  # noqa: BLE001
            pass

    @r.get("/presence/agreement")
    async def agreement(request: Request, person_id: str, date: str | None = None):
        """How often each presence signal agreed with the combined answer, for one night."""
        c = ctl()
        p = person(person_id)
        who = request.scope.get("state", {}).get("principal")
        if who is not None and who.role != "owner" and who.kind != "open" and who.person != p.id:
            raise HTTPException(403, "That's their own sleep data.")          # (auth.PRIVATE_READS, as a query)
        d = _date.fromisoformat(date) if date else night_date(c.clock.now())
        win = night_window(p, d, c.clock.tz, c.clock.now_ts())
        if not win:
            return []
        rows = [x for x in c.store.history(p.zone, *win, person=p.id) if x["occupied"] is not None]
        if not rows:
            return []
        out = []

        def score(name, values):
            pairs = [(v, rows[i]["occupied"]) for i, v in enumerate(values) if v is not None]
            if pairs:
                out.append({"signal": name, "agree": round(sum(1 for a, b in pairs if a == b) / len(pairs), 3), "samples": len(pairs)})
        score("Bed sensor", [x["bed_occ"] for x in rows])
        thr = src().calibration.weight_threshold_kg
        for sc in src().configs:
            if sc.zone != p.zone or sc.role not in ("presence", "weight"):
                continue
            samp = c.store.samples(sc.id, *win)
            if not samp:
                continue
            vals, j = [], 0
            for x in rows:
                while j + 1 < len(samp) and samp[j + 1][0] <= x["ts"]:
                    j += 1
                v = samp[j][1] if abs(samp[j][0] - x["ts"]) < 60 else None
                vals.append(None if v is None else int(v >= (thr if sc.role == "weight" else 0.5)))
            score(sc.name, vals)
        return out

    # ---------- insights & AI ----------
    def context(p):
        c = ctl()
        nights = recent(c.store, p, c.clock.tz, c.clock.now_ts(), 7)
        return nights, {"person": {"id": p.id, "name": p.name, "persona": p.persona, "units": p.settings.units},
                        "rhythms": [rh.model_dump() for rh in p.rhythms],
                        "nights": [{k: v for k, v in n.items() if k not in ("series",)} for n in nights],
                        "insights": insights(p, nights, c.clock.tz, c.clock.now_ts())}

    @r.get("/people/{pid}/insights")
    async def get_insights(pid: str):
        p = person(pid)
        nights, _ = context(p)
        out = insights(p, nights, ctl().clock.tz, ctl().clock.now_ts())
        done = [n for n in nights if n.get("has_data") and not n.get("in_progress")]
        if len(out) == 1 and out[0]["id"] == "all_good" and len(done) < 5:
            # "steady" after two nights would overclaim: say it's early instead (and never leave this group empty)
            out = [{"id": "early_days", "severity": "info", "title": f"Early days: {len(done)} night{'s' if len(done) != 1 else ''} so far",
                    "detail": "Nothing stands out yet. Patterns get clearer after about a week of nights.", "evidence": [], "proposal": None}]
        out = wearable_cards(ctl(), p, ctl().clock.now_ts()) + out + tuning_guide(ctl(), p, ctl().clock.now_ts())
        applied = ctl().store.get(f"applied_insights:{pid}", {})
        newest = max((n["date"] for n in nights if n.get("has_data") and not n.get("in_progress")), default="")
        for i in out:
            a = applied.get(i["id"])
            if a and newest <= a["night"]:
                i["applied"] = a                     # no new nights since it was applied: don't suggest it again yet
                i["proposal"] = None
        return out


    @r.post("/people/{pid}/insights/apply")
    async def apply(pid: str, b: ApplyBody):
        p = person(pid)
        try:
            what = apply_schedule(p, b.proposal) or apply_proposal(p, b.proposal)
        except KeyError as e:
            raise HTTPException(400, str(e))
        ctl().reload_household(ctl().household)
        ctl().store.event(ctl().clock.now_ts(), "rhythm_changed", pid, b.source, change=what)
        if b.insight_id:
            nights = [n for n in recent(ctl().store, p, ctl().clock.tz, ctl().clock.now_ts(), 7) if n.get("has_data") and not n.get("in_progress")]
            applied = ctl().store.get(f"applied_insights:{pid}", {})
            applied[b.insight_id] = {"ts": ctl().clock.now_ts(), "night": max((n["date"] for n in nights), default=""), "change": what, "proposal": b.proposal}
            ctl().store.put(f"applied_insights:{pid}", applied)
        return {"ok": True, "applied": what}

    @r.get("/ai/status")
    async def ai_status():
        cfg = state.cfg
        return {"configured": ai.configured(cfg), "model": ai.model_name(cfg) if ai.configured(cfg) else None,
                "provider": ai.provider(cfg), "url": cfg.llm_url if ai.provider(cfg) == "openai" else None}


    ask_hits: dict[str, list[float]] = {}

    @r.post("/ai/ask")
    async def ask(b: AskBody, request: Request):
        import time as _t
        who = request.scope.get("state", {}).get("principal")
        key = getattr(who, "id", None) or "anon"
        now = _t.time()
        recent = [t for t in ask_hits.get(key, []) if now - t < 3600]
        if len(recent) >= 30:                                 # the model is shared (and slow): 30 questions an hour each
            raise HTTPException(429, "That's a lot of questions for one hour. Try again a little later.")
        ask_hits[key] = recent + [now]
        p = person(b.person_id)
        if who is not None and who.role != "owner" and who.kind != "open" and who.person != p.id:
            raise HTTPException(403, "That's their own sleep data.")
        nights, ctxt = context(p)
        if not ai.configured(state.cfg):
            ins = ctxt["insights"]
            return {"answer": "No AI model is connected, so here is what the built-in analysis found:\n\n" +
                    "\n".join(f"• {i['title']}: {i['detail']}" for i in ins) +
                    "\n\nConnect a local model (for example Ollama) with ET_LLM_URL to ask open questions.",
                    "proposal": next((i["proposal"] for i in ins if i.get("proposal")), None), "model": None}
        try:
            out = await ai.ask(state.cfg, b.question, ctxt)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"AI model error: {type(e).__name__}")
        out["proposal"] = ai.check_rhythm_proposal(p, out.get("proposal"))     # a real phase, within ±1.5 °C, or nothing
        return out

    return r


def wearable_cards(c, p, now: float) -> list[dict]:
    """Insight cards for a watch or ring that's new (being learned) or has gone quiet."""
    out = []
    for d in c.autopilot.wearable_status(p, now):
        if d["learning"]:
            out.append({"id": "wearable_learning", "severity": "info", "title": d["text"],
                        "detail": f"Different devices read a little differently. Until the {d['kind']} has a few nights, its "
                                  "readings only count against the rest of the same night, so nothing changes because of "
                                  "the switch.", "evidence": [], "proposal": None})
        elif d["quiet"] and now - d["last"] <= 7 * 86400:
            out.append({"id": "wearable_quiet", "severity": "info", "title": d["text"],
                        "detail": "Autopilot only uses fresh readings, so it carries on with the bed's own sensors.",
                        "evidence": [], "proposal": None})
    return out


def _after_stages(c, p, segs: list[dict]) -> list[dict]:
    """New sleep stages from a watch or ring (the slow batch path): line each night they cover up with the bed's
    (engine/compare.py, which also teaches the in-bed offset) and re-run Autopilot's hindsight check for it."""
    from datetime import datetime, timedelta
    from .autopilot_api import _recheck
    from .engine.compare import compare_night
    out = []
    nights = sorted({(datetime.fromtimestamp(g["start"], c.clock.tz) - timedelta(hours=12)).date().isoformat() for g in segs})
    for d in nights[-7:]:
        try:
            cmp = compare_night(c, p, d)
        except Exception:  # noqa: BLE001
            continue
        if not cmp.get("available"):
            continue
        item = {"date": d, "summary": cmp.get("summary"), "disagree": cmp.get("disagree"), "stage_agreement": cmp.get("stage_agreement")}
        try:
            item["recheck"] = _recheck(c, p, d).get("text")
        except Exception:  # noqa: BLE001
            pass
        out.append(item)
    for k in ("_nights_cache", "_base_cache"):
        c.autopilot.__dict__.get(k, {}).pop(p.id, None)
    return out
