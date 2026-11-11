"""Home Assistant bridge over MQTT discovery.

Publishes each person's side as Home Assistant entities, so dashboards, voice, automations and existing bedside dials
keep working without any custom integration:

  climate.embertide_<name>               target = "change until the next phase"; mode off / heat_cool = side off / on
  button.embertide_<name>_hot_flash      (and _cold_flash, _stop_flash)
  sensor.embertide_<name>_phase          current phase name, or what the side is doing
  sensor.embertide_<name>_next_change    when the next phase starts
  binary_sensor.embertide_<name>_in_bed
  number.embertide_<name>_<phase>_temp   each phase's temperature in tonight's rhythm (writes to the rhythm)

Works with any MQTT broker Home Assistant uses (e.g. the Mosquitto add-on). Configure in Studio → Integrations or with
ET_HA_MQTT_URL=mqtt://user:pass@homeassistant.local:1883.

The message building and command handling are plain functions (no network), so they're tested directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from urllib.parse import unquote, urlparse

from . import __version__
from .engine.rhythm import night_date, pick_rhythm
from .models import PALETTE, person_color
from .units import c_to_f, f_to_c

log = logging.getLogger(__name__)
BASE = "embertide"


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "person"


class HaBridge:
    def __init__(self, controller, url: str | None = None, prefix: str = "homeassistant") -> None:
        self.c = controller
        self.url = url
        self.prefix = prefix
        self.state = "off" if not url else "starting"
        self._task: asyncio.Task | None = None
        self._client = None
        self._last: dict[str, str] = {}
        self._announced: set[str] = set()
        self.handled = 0
        self.ignored_retained = 0

    # ---------- naming ----------
    def oid(self, p) -> str:
        """Stable object id: fixed the first time a person is announced, so renaming them doesn't break dials."""
        ids = self.c.store.get("ha_object_ids", {})
        if p.id not in ids:
            taken = set(ids.values())
            base = f"embertide_{slug(p.name)}"
            o, k = base, 2
            while o in taken:
                o, k = f"{base}_{k}", k + 1
            ids[p.id] = o
            self.c.store.put("ha_object_ids", ids)
        return ids[p.id]

    def t(self, p, *parts: str) -> str:
        return "/".join([BASE, p.id, *parts])

    # ---------- discovery ----------
    def discovery(self) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        avail = {"availability_topic": f"{BASE}/status"}
        bed = {"identifiers": ["embertide_bed"], "name": "Ember & Tide · Bed", "manufacturer": "Ember & Tide", "sw_version": __version__}
        for zone in ("left", "right"):
            out.append((f"{self.prefix}/sensor/embertide_{zone}_guest/config", {
                **avail, "device": bed, "name": f"{zone.title()} side guest", "unique_id": f"embertide_{zone}_guest",
                "object_id": f"embertide_{zone}_guest", "state_topic": f"{BASE}/bed/{zone}/guest", "icon": "mdi:account-clock-outline"}))
        from . import care as carelib
        for t in carelib.of(self.c).tasks():      # care jobs: days until due, and a button to mark one done
            tid = t["id"]
            out.append((f"{self.prefix}/sensor/embertide_care_{tid}_days/config", {
                **avail, "device": bed, "name": f"Care: {t['title']} (days until due)", "unique_id": f"embertide_care_{tid}_days",
                "object_id": f"embertide_care_{tid}_days", "state_topic": f"{BASE}/care/{tid}/days", "unit_of_measurement": "d",
                "icon": "mdi:wrench-clock"}))
            out.append((f"{self.prefix}/button/embertide_care_{tid}_done/config", {
                **avail, "device": bed, "name": f"Care: {t['title']} done", "unique_id": f"embertide_care_{tid}_done",
                "object_id": f"embertide_care_{tid}_done", "command_topic": f"{BASE}/care/set/{tid}", "payload_press": "done",
                "icon": "mdi:check-circle-outline"}))
        for p in self.c.household.people:
            o = self.oid(p)
            f = p.settings.units == "F"
            dev = {"identifiers": [f"embertide_{p.id}"], "name": f"Ember & Tide · {p.name}", "manufacturer": "Ember & Tide",
                   "model": f"{p.zone.title()} side", "sw_version": __version__}
            common = {**avail, "device": dev}
            out.append((f"{self.prefix}/climate/{o}/config", {
                **common, "name": None, "unique_id": f"embertide_{p.id}_climate", "object_id": o,
                "modes": ["off", "heat_cool"], "mode_state_topic": self.t(p, "mode"), "mode_command_topic": self.t(p, "set", "mode"),
                "temperature_state_topic": self.t(p, "target"), "temperature_command_topic": self.t(p, "set", "target"),
                "current_temperature_topic": self.t(p, "measured"), "action_topic": self.t(p, "action"),
                "temperature_unit": "F" if f else "C", "temp_step": 1 if f else 0.5, "precision": 1.0 if f else 0.5,
                "min_temp": 55 if f else 13, "max_temp": 110 if f else 43, "icon": "mdi:bed-king-outline"}))
            for kind, label, icon in (("hot", "Hot flash", "mdi:snowflake"), ("cold", "Cold flash", "mdi:fire"), ("stop", "Stop flash", "mdi:stop-circle-outline"),
                                      ("extend", "Flash +5 min", "mdi:timer-plus-outline")):
                out.append((f"{self.prefix}/button/{o}_{kind}_flash/config", {
                    **common, "name": label, "unique_id": f"embertide_{p.id}_{kind}_flash", "object_id": f"{o}_{kind}_flash",
                    "command_topic": self.t(p, "set", "flash"), "payload_press": kind, "icon": icon}))
            for kind, label, icon in (("too_hot", "Feels too hot", "mdi:thermometer-chevron-up"), ("too_cold", "Feels too cold", "mdi:thermometer-chevron-down"),
                                      ("just_right", "Feels just right", "mdi:thumb-up-outline")):
                out.append((f"{self.prefix}/button/{o}_feel_{kind}/config", {
                    **common, "name": label, "unique_id": f"embertide_{p.id}_feel_{kind}", "object_id": f"{o}_feel_{kind}",
                    "command_topic": self.t(p, "set", "feel"), "payload_press": kind, "icon": icon}))
            out.append((f"{self.prefix}/sensor/{o}_phase/config", {
                **common, "name": "Phase", "unique_id": f"embertide_{p.id}_phase", "object_id": f"{o}_phase",
                "state_topic": self.t(p, "phase"), "icon": "mdi:weather-night"}))
            out.append((f"{self.prefix}/sensor/{o}_next_change/config", {
                **common, "name": "Next change", "unique_id": f"embertide_{p.id}_next_change", "object_id": f"{o}_next_change",
                "state_topic": self.t(p, "next"), "device_class": "timestamp"}))
            out.append((f"{self.prefix}/sensor/{o}_name/config", {
                **common, "name": "Name", "unique_id": f"embertide_{p.id}_name", "object_id": f"{o}_name",
                "state_topic": self.t(p, "name"), "icon": "mdi:account", "entity_category": "diagnostic"}))
            out.append((f"{self.prefix}/sensor/{o}_checkin/config", {
                **common, "name": "Morning check-in", "unique_id": f"embertide_{p.id}_checkin", "object_id": f"{o}_checkin",
                "state_topic": self.t(p, "checkin"), "icon": "mdi:weather-sunset-up"}))
            out.append((f"{self.prefix}/sensor/{o}_flash_end/config", {
                **common, "name": "Flash ends", "unique_id": f"embertide_{p.id}_flash_end", "object_id": f"{o}_flash_end",
                "state_topic": self.t(p, "flash_end"), "device_class": "timestamp", "icon": "mdi:timer-sand"}))
            out.append((f"{self.prefix}/sensor/{o}_plan/config", {
                **common, "name": "Rhythm temperature now", "unique_id": f"embertide_{p.id}_plan", "object_id": f"{o}_plan",
                "state_topic": self.t(p, "plan"), "unit_of_measurement": "°F" if f else "°C", "device_class": "temperature"}))
            out.append((f"{self.prefix}/sensor/{o}_tonight/config", {
                **common, "name": "Tonight", "unique_id": f"embertide_{p.id}_tonight", "object_id": f"{o}_tonight",
                "state_topic": self.t(p, "tonight"), "icon": "mdi:format-list-bulleted", "entity_category": "diagnostic"}))
            out.append((f"{self.prefix}/sensor/{o}_guest/config", {
                **common, "name": "Guest on this side", "unique_id": f"embertide_{p.id}_guest", "object_id": f"{o}_guest",
                "state_topic": self.t(p, "guest"), "icon": "mdi:account-clock-outline"}))
            out.append((f"{self.prefix}/sensor/{o}_sharing/config", {
                **common, "name": "Shared with partner", "unique_id": f"embertide_{p.id}_sharing", "object_id": f"{o}_sharing",
                "state_topic": self.t(p, "sharing"), "icon": "mdi:account-multiple", "entity_category": "diagnostic"}))
            out.append((f"{self.prefix}/sensor/{o}_color/config", {
                **common, "name": "Colour", "unique_id": f"embertide_{p.id}_color", "object_id": f"{o}_color",
                "state_topic": self.t(p, "color"), "icon": "mdi:palette", "entity_category": "diagnostic"}))
            out.append((f"{self.prefix}/button/{o}_pause_hour/config", {
                **common, "name": "Pause for an hour", "unique_id": f"embertide_{p.id}_pause_hour", "object_id": f"{o}_pause_hour",
                "command_topic": self.t(p, "set", "tonight"), "payload_press": "pause", "icon": "mdi:pause-circle-outline"}))
            out.append((f"{self.prefix}/binary_sensor/{o}_in_bed/config", {
                **common, "name": "In bed", "unique_id": f"embertide_{p.id}_in_bed", "object_id": f"{o}_in_bed",
                "state_topic": self.t(p, "in_bed"), "device_class": "occupancy", "payload_on": "ON", "payload_off": "OFF"}))
            rh = self._rhythm(p)
            for ph in (rh.phases if rh else []):
                out.append((f"{self.prefix}/number/{o}_{ph.id}_temp/config", {
                    **common, "name": f"{ph.name} temperature", "unique_id": f"embertide_{p.id}_{ph.id}_temp", "object_id": f"{o}_{ph.id}_temp",
                    "state_topic": self.t(p, "phase_temp", ph.id), "command_topic": self.t(p, "set", "phase_temp", ph.id),
                    "min": 55 if f else 13, "max": 95 if f else 35, "step": 1 if f else 0.5, "mode": "box",
                    "unit_of_measurement": "°F" if f else "°C", "device_class": "temperature", "entity_category": "config"}))
        return [(t, {k: v for k, v in c.items() if k != "_oid"}) for t, c in self._ids(out)]

    @staticmethod
    def _ids(msgs: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
        """Home Assistant 2025.10+ takes the entity ID from `default_entity_id` (domain included) and ignores the old
        `object_id`, naming entities after the device instead. Send the new key so the IDs are the ones we promise."""
        out = []
        for topic, cfg in msgs:
            domain = topic.split("/")[1]
            cfg = {**cfg, "default_entity_id": f"{domain}.{cfg['object_id']}"}
            cfg.pop("object_id")
            out.append((topic, {**cfg, "_oid": cfg["default_entity_id"].split(".", 1)[1]}))
        return out

    def _rhythm(self, p):
        now = datetime.fromtimestamp(self.c.clock.now_ts(), self.c.clock.tz)
        return pick_rhythm(p.rhythms, night_date(now))

    # ---------- state ----------
    def states(self, snap: dict) -> dict[str, str]:
        out: dict[str, str] = {}
        for zone in ("left", "right"):
            out[f"{BASE}/bed/{zone}/guest"] = "on" if zone in self.c.household.guest else "off"
        from . import care as carelib
        for t in carelib.of(self.c).tasks():
            # days until due (negative = overdue); a water job has no schedule: 0 while the hub says it needs water
            out[f"{BASE}/care/{t['id']}/days"] = str(t["days_until"]) if t["days_until"] is not None else ("0" if t["due"] else "None")
        for pv in snap.get("people", []):
            try:
                p = self.c.person(pv["id"])
            except KeyError:
                continue
            f = p.settings.units == "F"
            conv = (lambda c: round(c_to_f(c))) if f else (lambda c: round(c * 2) / 2)
            n = pv["night"]
            on = n["target_c"] is not None
            out[self.t(p, "mode")] = "heat_cool" if on else "off"
            shown = n["target_c"] if on else (pv["bed"].get("setpoint_c") or None)
            out[self.t(p, "target")] = str(conv(shown)) if shown is not None else "None"
            m = pv["bed"].get("measured_c")
            out[self.t(p, "measured")] = str(round(c_to_f(m), 1) if f else round(m, 1)) if m is not None else "None"
            if not on:
                act = "off"
            elif m is None:
                act = "idle"
            else:
                act = "cooling" if n["target_c"] < m - 0.3 else "heating" if n["target_c"] > m + 0.3 else "idle"
            out[self.t(p, "action")] = act
            if n.get("flash") and snap["now"] < n["flash"]["end"]:
                phase = "Hot flash" if n["flash"]["kind"] == "hot" else "Cold flash"
            elif on and not n.get("phase_name"):
                phase = "On"                             # turned on outside the rhythm: never say "Off" while it's running
            else:
                phase = n.get("phase_name") or {"waiting": "Pre-conditioning", "away": "Away", "paused": "Paused", "skipped": "Off tonight",
                                                "out_of_bed": "Out of bed", "out_of_bed_hold": "Out of bed", "done": "Off",
                                                "day": "Off", "missed": "Off", "no_rhythm": "No rhythm"}.get(n["state"], n["state"])
            out[self.t(p, "phase")] = phase
            nx = pv.get("next")
            out[self.t(p, "next")] = datetime.fromtimestamp(nx["at"], self.c.clock.tz).isoformat() if nx else "None"
            out[self.t(p, "name")] = p.name
            out[self.t(p, "checkin")] = "due" if self._checkin_due(p, pv, snap["now"]) else "none"
            fl = n.get("flash")
            out[self.t(p, "flash_end")] = (datetime.fromtimestamp(fl["end"], self.c.clock.tz).isoformat()
                                           if fl and snap["now"] < fl["end"] else "None")
            base = n.get("base_c")
            out[self.t(p, "plan")] = str(conv(base)) if base is not None else "None"
            rows = []
            for ph in pv.get("phases", []):
                cur = ph["id"] == n.get("phase_id")
                if cur or ph["start"] > snap["now"]:
                    t = "now" if cur else datetime.fromtimestamp(ph["start"], self.c.clock.tz).strftime("%I:%M").lstrip("0")
                    rows.append(f"{t}|{ph['name']}|{conv(ph.get('adjusted_c') or ph['temp_c'])}")
            sch = pv.get("schedule")
            if sch:
                rows.append(datetime.fromtimestamp(sch["end"], self.c.clock.tz).strftime("%I:%M").lstrip("0") + "|Side off|")
            out[self.t(p, "tonight")] = ";".join(rows[:5])[:250] or "None"
            st = p.settings
            g = self.c.household.guest.get(p.zone)        # the dial shows its guest screen unless asked not to
            out[self.t(p, "guest")] = "on" if g is not None and g.dial else "off"
            out[self.t(p, "sharing")] = ",".join(k for k, v in (("flash", st.partner_can_flash), ("adjust", st.partner_can_adjust)) if v) or "none"
            out[self.t(p, "color")] = PALETTE[person_color(p)][0]   # dark-theme hex, for dials and dashboards
            pres = pv["presence"].get("present")
            out[self.t(p, "in_bed")] = "ON" if pres else "OFF"
            rh = self._rhythm(p)
            for ph in (rh.phases if rh else []):
                out[self.t(p, "phase_temp", ph.id)] = str(conv(ph.temp_c))
        return out

    def _checkin_night(self, p, pv, now: float) -> str | None:
        """The night a morning check-in is for: within 5 hours after this morning's night ended."""
        sch = pv.get("schedule")
        if not sch or pv["night"]["state"] != "done" or not (sch["end"] <= now < sch["end"] + 5 * 3600):
            return None
        return datetime.fromtimestamp(sch["bedtime"], self.c.clock.tz).date().isoformat()

    def _checkin_due(self, p, pv, now: float) -> bool:
        d = self._checkin_night(p, pv, now)
        return bool(d) and not (self.c.store.get(f"feedback:{p.id}:{d}") or {}).get("temp")

    # ---------- commands from Home Assistant ----------
    async def handle(self, topic: str, payload: str) -> str | None:
        """Apply one command. Returns a short description (for logs/tests), or None if it wasn't ours/was invalid."""
        parts = topic.split("/")
        if len(parts) < 4 or parts[0] != BASE or parts[2] != "set" or len(payload) > 200:
            return None
        if parts[1] == "care":                       # a care job's "done" button: embertide/care/set/<task id>
            if len(parts) != 4 or payload.strip() != "done":
                return None
            from . import care as carelib
            try:
                carelib.of(self.c).done(parts[3])
            except (KeyError, carelib.CareError):
                return None
            self.handled += 1
            return f"care {parts[3]} done"
        try:
            p = self.c.person(parts[1])
        except KeyError:
            return None
        e = self.c.engine_for(p.id)
        now = self.c.clock.now_ts()
        f = p.settings.units == "F"
        to_c = (lambda v: f_to_c(float(v))) if f else (lambda v: float(v))
        what = parts[3]
        payload = payload.strip()
        if p.zone in self.c.household.guest:
            # a guest's side: the climate entity (and the dial) is a plain on/off and temperature control
            try:
                if what == "mode" and payload == "off":
                    self.c.guest_set(p.zone, False, None)
                    return "guest side off"
                if what == "mode" and payload in ("heat_cool", "auto", "cool", "heat"):
                    self.c.guest_set(p.zone, True, None)
                    return "guest side on"
                if what == "target":
                    c = to_c(payload)
                    if not 10 <= c <= 45:
                        return None
                    self.c.guest_set(p.zone, None, round(c, 2))
                    return f"guest target {payload}"
            except (ValueError, TypeError, KeyError):
                return None
            return None                                   # flashes, tonight, check-ins: not for a guest
        try:
            if what == "mode":
                if payload == "off":
                    e.tonight(now, "skip")
                    done = "side off tonight"
                elif payload in ("heat_cool", "auto", "cool", "heat"):
                    e.tonight(now, "resume")
                    done = "side on"
                    if e.tick(now, self.c.fuse(p.zone), self.c.home.get(p.id)) is None:
                        # nothing scheduled right now: turning it on means "on, at the last temperature"
                        z = (self.c.driver.latest().zones.get(p.zone) if self.c.driver.latest() else None)
                        e.adjust(now, temp_c=(z.setpoint_c if z and z.setpoint_c else f_to_c(70)), until="next_phase", source="ha")
                        done = "side on by hand"
                else:
                    return None
            elif what == "target":
                c = to_c(payload)
                if not 10 <= c <= 45:
                    return None
                e.adjust(now, temp_c=round(c, 2), until="next_phase", source="ha")
                done = f"target {payload}"
            elif what == "flash":
                if payload in ("hot", "cold"):
                    e.flash(payload, now, source="ha")
                elif payload == "stop":
                    e.stop_flash(now)
                elif payload == "extend":
                    if not e.s.flash:
                        return None
                    e.extend_flash(now)
                else:
                    return None
                done = f"flash {payload}"
            elif what == "tonight":
                if payload == "pause":
                    e.tonight(now, "pause", 60)
                elif payload in ("skip", "resume"):
                    e.tonight(now, payload)
                else:
                    return None
                done = f"tonight {payload}"
            elif what == "feel":
                if payload not in ("too_hot", "too_cold", "just_right"):
                    return None
                z = self.c.driver.latest().zones.get(p.zone) if self.c.driver.latest() else None
                pv = next((x for x in (self.c.last_snapshot or {}).get("people", []) if x["id"] == p.id), None)
                night = self._checkin_night(p, pv, now) if pv else None
                if night:
                    # the morning prompt: this answers "how was the temperature?" for last night
                    temp = {"too_hot": "warm", "too_cold": "cold", "just_right": "right"}[payload]
                    fb = self.c.store.get(f"feedback:{p.id}:{night}", {}) or {}
                    self.c.store.put(f"feedback:{p.id}:{night}", {**fb, "temp": temp, "ts": now})
                    self.c.store.event(now, "feedback", p.id, "ha", night=night, temp=temp)
                    done = f"morning check-in: {temp}"
                else:
                    self.c.store.event(now, "feel", p.id, "ha", feel=payload, phase=e.s.phase_id, target_c=e.s.target_c,
                                       measured_c=z.measured_c if z else None)
                    done = f"feels {payload.replace('_', ' ')}"
            elif what == "phase_temp" and len(parts) == 5:
                c = to_c(payload)
                if not 10 <= c <= 45:
                    return None
                rh = self._rhythm(p)
                ph = next((x for x in (rh.phases if rh else []) if x.id == parts[4]), None)
                if not ph:
                    return None
                ph.temp_c = round(c, 2)
                self.c.reload_household(self.c.household)
                self.c.store.event(now, "rhythm_changed", p.id, "ha", rhythm=rh.id, phase=ph.id)
                done = f"{ph.name} = {payload}"
            else:
                return None
        except (ValueError, TypeError):
            return None
        self.handled += 1
        await self.c.tick(force={p.id})
        return done

    # ---------- connection ----------
    async def start(self) -> None:
        if self.url:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._client:
            try:
                await self._client.publish(f"{BASE}/status", b"offline", retain=True)
            except Exception:  # noqa: BLE001
                pass

    async def restart(self, url: str | None, prefix: str) -> None:
        await self.stop()
        self.url, self.prefix = url, prefix
        self._last.clear()
        self.state = "off" if not url else "starting"
        await self.start()

    def status(self) -> dict:
        u = urlparse(self.url) if self.url else None
        return {"state": self.state, "broker": f"{u.hostname}:{u.port or 1883}" if u else None, "prefix": self.prefix,
                "entities": len(self.discovery()), "commands_handled": self.handled, "ignored_retained": getattr(self, "ignored_retained", 0),
                "object_ids": {p.id: self.oid(p) for p in self.c.household.people}}

    async def publish_state(self, snap: dict) -> None:
        if not self._client:
            return
        for topic, value in self.states(snap).items():
            if self._last.get(topic) != value:
                self._last[topic] = value
                await self._client.publish(topic, value.encode(), retain=True)

    async def announce(self) -> None:
        if not self._client:
            return
        msgs = self.discovery()
        keep = {t for t, _ in msgs}
        for topic, cfg in msgs:
            await self._client.publish(topic, json.dumps(cfg).encode(), retain=True)
        for gone in self._announced - keep:                # e.g. a phase was removed: take its entity away in HA
            await self._client.publish(gone, b"", retain=True)
        self._announced = keep

    async def unannounce(self) -> int:
        """Remove every Ember & Tide entity from Home Assistant (empty retained configs)."""
        if not self._client:
            return 0
        topics = {t for t, _ in self.discovery()} | self._announced
        for t in topics:
            await self._client.publish(t, b"", retain=True)
        self._announced = set()
        return len(topics)

    async def _run(self) -> None:
        import aiomqtt
        u = urlparse(self.url)
        backoff = 1
        while True:
            try:
                will = aiomqtt.Will(f"{BASE}/status", b"offline", retain=True)
                async with aiomqtt.Client(u.hostname or "localhost", u.port or 1883,
                                          username=unquote(u.username) if u.username else None,
                                          password=unquote(u.password) if u.password else None,
                                          identifier=f"embertide-ha-{int(time.time())}", will=will) as client:
                    self._client = client
                    self.state = "connected"
                    backoff = 1
                    await client.publish(f"{BASE}/status", b"online", retain=True)
                    await self.announce()
                    self._last.clear()
                    await client.subscribe(f"{BASE}/+/set/#")
                    await client.subscribe(f"{self.prefix}/status")          # HA restarted: announce again

                    def on_snap(s: dict) -> None:
                        asyncio.get_running_loop().create_task(self.publish_state(s))
                    self.c.listeners.append(on_snap)
                    try:
                        if self.c.last_snapshot:
                            await self.publish_state(self.c.last_snapshot)
                        async for msg in client.messages:
                            topic, payload = str(msg.topic), bytes(msg.payload).decode(errors="replace")
                            if topic == f"{self.prefix}/status":
                                if payload == "online":
                                    await self.announce()
                                    self._last.clear()
                                continue
                            if getattr(msg, "retain", False):
                                # a retained command is the broker replaying an old button press after a reconnect:
                                # never apply it again (HA itself never retains commands)
                                self.ignored_retained += 1
                                continue
                            r = await self.handle(topic, payload)
                            log.info("home assistant: %s → %s", topic, r)
                    finally:
                        if on_snap in self.c.listeners:
                            self.c.listeners.remove(on_snap)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._client = None
                msg = str(e)
                self.state = ("not authorised: check the MQTT username and password" if "authori" in msg.lower()
                              else f"can't reach the broker, retrying ({msg[:80]})")
                log.warning("home assistant mqtt: %s; retry in %ss", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)
