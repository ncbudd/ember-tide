"""Orion topper over its own local MQTT protocol.

The bed connects to whatever `dev.orionbed.com` resolves to on port 1883. Point that name at your broker
(router DNS record) and restart the bed. Telemetry arrives on `orion/<serial>/out` (~1–2 Hz) and commands go
to `orion/<serial>/in`. Protocol knowledge comes from the community: tdickman's local integration
(github.com/tdickman/orion-sleep-local-mqtt-home-assistant) and firmware notes shared on r/Orion_Sleep.
Only commands verified on firmware 2.6.2 are allowed. Firmware/OTA/Wi-Fi commands are refused on purpose.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from urllib.parse import urlparse

from . import profiles
from .base import BedFrame, Cap, CommandError, SensorFrame, HubHealth, ZoneFrame

log = logging.getLogger(__name__)
SERIAL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SIDE = {"left": "LEFT", "right": "RIGHT"}
BLOCKED = ("OTA", "ERASE", "IB UPDATE", "FILLVALVE", "AIRVALVE", "RPUMP", "LPUMP", "RTEC", "LTEC", "FAN PWM")


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _bool(v):
    if isinstance(v, bool):
        return v
    if v in ("true", "false"):
        return v == "true"
    return None


REALTIME = 0x13          # sensor array byte 3: a real-time report. Anything else is an empty/idle block.
_NAN = re.compile(r'(?<=[:\[,])\s*-?(nan|inf)\b')
# Firmware 2.6.2 sometimes writes raw garbage (including stray quotes) into the sensor version strings.
_BAD_VERSION = re.compile(r'"(\w*Version)":".*?"(?=,"[A-Za-z_]\w*":)')


def _loads_lenient(payload: bytes):
    """The bed's JSON isn't always valid: `nan` for missing readings, and corrupted version strings.
    Seen on real firmware 2.6.2 (roughly 7 in 10 messages). Repair those two things, nothing else."""
    s = payload.decode("utf-8", errors="replace")
    try:
        return json.loads(s)
    except ValueError:
        pass
    s = _BAD_VERSION.sub(r'"\1":""', _NAN.sub("null", s))
    return json.loads(s)


def parse_status(topic: str, payload: bytes, ts: float) -> tuple[str, BedFrame] | None:
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "orion" or parts[2] != "out" or not SERIAL_RE.fullmatch(parts[1]):
        return None
    if len(payload) > 65536:
        return None
    try:
        raw = _loads_lenient(payload)
    except (ValueError, RecursionError):
        return None
    if not isinstance(raw, dict) or raw.get("type") != "status" or raw.get("serial") != parts[1]:
        return None

    def get(*path):
        v = raw
        for k in path:
            if not isinstance(v, dict):
                return None
            v = v.get(k)
        return v

    zones = {}
    for side, sensor in (("left", "sensor1"), ("right", "sensor2")):
        arr = get("sensors", sensor)
        sf = SensorFrame()
        if (isinstance(arr, list) and len(arr) > 17 and arr[3] == REALTIME
                and all(type(x) is int and 0 <= x <= 255 for x in arr[11:18])):
            sf.status = arr[17]
            sf.clock = tuple(arr[11:15])
            hr, br = arr[15], arr[16]
            sf.hr = hr if 0 < hr < 255 and sf.status not in (3, 5, 1, 255) else None
            sf.br = br if 0 < br < 255 and sf.status not in (2, 5, 1, 255) else None
        m = _num(get("temps", f"ib_{side}"))
        zones[side] = ZoneFrame(on=_bool(get("tec", side, "control_enabled")), setpoint_c=_num(get("tec", side, "setpoint")),
                                measured_c=m if m is not None and m > 1 else None,     # 0.00 while booting
                                sensor=sf)
    rgb = tuple(_num(get("led", f"idle_{c}")) for c in "rgb")
    errors = get("errors")
    frame = BedFrame(
        ts=ts, zones=zones,
        system_state=get("system_state") if isinstance(get("system_state"), str) else None,
        firmware=get("fw", "version") if isinstance(get("fw", "version"), str) else None,
        quiet_mode=_bool(get("fans", "quiet_mode")),
        brightness=_num(get("led", "led_brightness")),
        rgb=rgb if all(v is not None for v in rgb) else None,  # type: ignore[arg-type]
        fault=any(bool(v) for v in errors.values()) if isinstance(errors, dict) and errors else None,
        hub=parse_hub(raw),
    )
    return parts[1], frame


# the unit's own fault flags -> generic fault codes (hub.FAULTS has the wording)
FAULT_CODES = {
    "PUMP_OFF_TEC_ON": "water_flow", "FAN_OFF_TEC_ON": "fan_stopped", "PUMP_CURRENT": "pump",
    "FAN_RPM_OUT_OF_RANGE": "fan_speed", "NTC_MIN_TEMP_CUTOFF": "pad_cold_limit", "NTC_MAX_TEMP_CUTOFF": "pad_hot_limit",
    "CB_MAX_TEMP_CUTOFF": "hub_hot", "ANGLE_Z_OUT_OF_RANGE": "not_level", "NO_IB_VERSION": "panel_link",
    "IB_COMM_NOT_OK": "panel_link", "FLASH_NOT_OK": "memory", "ACCEL_NOT_OK": "tilt_sensor", "ADC_NOT_OK": "temp_readings",
    "AMBIENT_SENSOR_NOT_OK": "room_sensor", "MANUAL_ERROR_COMMAND": "manual_error", "PTC_FAULT_DETECTED": "power",
}
ZONE_FAULTS = {"RTEC_FAULT_DETECTED": "right", "LTEC_FAULT_DETECTED": "left"}
# The only water value seen on a healthy unit is 1. What other values mean is NOT confirmed (0 is presumably "low",
# but that has never been observed), so anything other than 1 is reported as "check the water", never as a specific fault.
WATER_NORMAL = 1


def parse_hub(raw: dict) -> HubHealth:
    def get(*path):
        v = raw
        for k in path:
            if not isinstance(v, dict):
                return None
            v = v.get(k)
        return v

    def txt(v, n=64):
        return v[:n] if isinstance(v, str) and v else None

    t = HubHealth()
    water = get("water")
    if isinstance(water, dict):
        vals = {k: _num(v) for k, v in water.items()}
        t.water_raw = {k: v for k, v in vals.items() if v is not None}
        if t.water_raw:
            t.water_ok = all(v == WATER_NORMAL for v in t.water_raw.values())
    errors = get("errors")
    if isinstance(errors, dict):
        for name, v in errors.items():
            if not v:
                continue
            if name in FAULT_CODES:
                t.faults.append((FAULT_CODES[name], None))
            elif name in ZONE_FAULTS:
                t.faults.append(("zone_fault", ZONE_FAULTS[name]))
            else:
                t.faults.append(("unknown", None))
    for key, code, zone in (("ptc_ok", "power", None), ("rtec_ok", "zone_fault", "right"), ("ltec_ok", "zone_fault", "left")):
        if _bool(raw.get(key)) is False and (code, zone) not in t.faults:
            t.faults.append((code, zone))
    sf = raw.get("safetyOk")
    t.safety_ok = sf if isinstance(sf, bool) else _bool(sf)
    rssi = _num(get("wifi", "rssi"))
    t.wifi_dbm = int(rssi) if rssi is not None and rssi < 0 else None
    t.wifi_name, t.wifi_address = txt(get("wifi", "ssid")), txt(get("wifi", "ip"))
    up = _num(raw.get("uptime_ms"))
    t.uptime_s = int(up // 1000) if up is not None and up >= 0 else None
    t.fw_version, t.fw_build, t.fw_panel = txt(get("fw", "version")), txt(get("fw", "build")), txt(get("fw", "ib_version"))
    heap = _num(get("fw", "free_heap"))
    t.free_memory_kb = int(heap // 1024) if heap is not None else None
    t.last_command = txt(raw.get("previous_command_received"), 80)
    for side in ("left", "right"):
        rpm = _num(get("fans", f"{side}_rpm"))
        if rpm is not None:
            t.fan_rpm[side] = int(rpm)
    pwm = _num(get("fans", "pwm"))
    t.fan_pwm = int(pwm) if pwm is not None else None
    hum = _num(get("ambient", "humidity_filtered"))
    t.humidity = round(hum, 1) if hum is not None else None
    return t


def validate(raw: str) -> str:
    cmd = " ".join(raw.strip().upper().split())
    if any(cmd.startswith(b) for b in BLOCKED):
        raise CommandError("blocked: firmware, Wi-Fi and low-level hardware commands are not allowed")
    p = cmd.split()
    if cmd in {"SYSTEM ON", "REBOOT", "CLEAR LEFT", "CLEAR RIGHT", "CLEAR BOTH", "FAN QUIET ON", "FAN QUIET OFF",
               "FILL START"}:      # FILL START: listed by the community, never run on this setup
        return cmd
    if p and p[0] == "SET" and len(p) in (3, 4) and p[1] in ("LEFT", "RIGHT", "BOTH"):
        if len(p) == 4 and p[1] != "BOTH":
            raise CommandError("two temperatures need BOTH")
        temps = [float(v) for v in p[2:]]
        if not all(10 <= t <= 45 for t in temps):
            raise CommandError("temperature must be 10–45 °C")
        return " ".join(p[:2] + [f"{t:.2f}".rstrip("0").rstrip(".") for t in temps])
    if p[:2] == ["IB", "RGB"] and len(p) == 5 and all(v.isdigit() and int(v) <= 255 for v in p[2:]):
        return cmd
    if p[:2] == ["IB", "DIMMER"] and len(p) == 3 and p[2].isdigit() and int(p[2]) <= 100:
        return cmd
    raise CommandError("unsupported command")


class OrionMqtt:
    id = "orion-mqtt"
    name = "Orion topper (local MQTT)"
    capabilities = {Cap.ZONE_TEMP, Cap.ZONE_POWER, Cap.MEASURED_TEMP, Cap.OCCUPANCY, Cap.BIO_HR, Cap.BIO_BR,
                    Cap.LED_DIM, Cap.LED_RGB, Cap.FAN_QUIET, Cap.REBOOT, Cap.RAW_CONSOLE, Cap.HUB_HEALTH, Cap.WATER_STATUS,
                    Cap.FAULTS, Cap.WATER_FILL}
    untried = {Cap.WATER_FILL}      # offered, but never run on a real unit here
    profile = profiles.ORION      # care tasks and the hub light guide (drivers/profiles.py)
    zones = ["left", "right"]

    def __init__(self, url: str, serial: str | None = None, username: str | None = None, password: str | None = None) -> None:
        u = urlparse(url)
        self.host, self.port = u.hostname or "localhost", u.port or 1883
        self.username = username or u.username
        self.password = password or u.password
        self.serial = serial
        self._frame: BedFrame | None = None
        self._client = None
        self._task: asyncio.Task | None = None
        self._changed = asyncio.Event()
        self._state = "starting"
        self._lock = asyncio.Lock()
        self._fails: dict[str, tuple[str, float, int]] = {}      # zone -> (command, don't resend before, attempts)
        self.sent: tuple[str, float] | None = None               # last command we published, and when
        self.acked: bool | None = None                           # did the unit report receiving it (None = no command yet)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def latest(self) -> BedFrame | None:
        f = self._frame
        if f and time.time() - f.ts > 20:
            f.online = False
        return f

    def status(self) -> dict:
        f = self._frame
        return {"driver": self.id, "name": self.name, "online": bool(f and f.online and time.time() - f.ts < 20),
                "detail": self._state, "serial": self.serial, "broker": f"{self.host}:{self.port}",
                "firmware": f.firmware if f else None, "age_s": round(time.time() - f.ts, 1) if f else None}

    async def _run(self) -> None:
        import aiomqtt
        backoff = 1
        while True:
            try:
                async with aiomqtt.Client(self.host, self.port, username=self.username, password=self.password,
                                          identifier=f"embertide-{int(time.time())}") as client:
                    self._client = client
                    self._state = "connected"
                    backoff = 1
                    await client.subscribe(f"orion/{self.serial or '+'}/out")
                    async for msg in client.messages:
                        parsed = parse_status(str(msg.topic), bytes(msg.payload), time.time())
                        if not parsed:
                            continue
                        serial, frame = parsed
                        if self.serial is None:
                            self.serial = serial
                        if serial == self.serial:
                            if self._frame is None or frame.ts - self._frame.ts > 20:
                                self._fails.clear()      # back after a silence (power cut, reboot): no held-back retries
                            self._frame = self._hold_sensors(frame)
                            self._check_ack(frame)
                            self._changed.set()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._client = None
                self._state = f"reconnecting ({type(e).__name__})"
                log.warning("mqtt: %s; retry in %ss", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(10, backoff * 2)        # local broker: get back quickly (the bed itself retries in ~15 s)

    def _check_ack(self, frame: BedFrame) -> None:
        """The unit repeats the last command it received in every report: that confirms delivery (not that it took effect)."""
        from ..hub import same_command
        if self.sent and frame.hub and same_command(frame.hub.last_command, self.sent[0]):
            self.acked = True

    def _hold_sensors(self, frame: BedFrame) -> BedFrame:
        """The bed interleaves empty sensor blocks between real reports. Keep the last real report for up to 10 s,
        so presence doesn't flicker to "unknown"; after that the side really has gone quiet."""
        held = getattr(self, "_last_sensor", {})
        for side, z in frame.zones.items():
            if z.sensor.status is not None:
                held[side] = (frame.ts, z.sensor)
            elif side in held and frame.ts - held[side][0] <= 10:
                z.sensor = held[side][1]
        self._last_sensor = held
        return frame

    async def _publish(self, cmd: str) -> None:
        if not self._client or not self.serial:
            raise CommandError("bed not connected")
        await self._client.publish(f"orion/{self.serial}/in", cmd.encode(), qos=1, retain=False)
        self.sent, self.acked = (cmd, time.time()), False

    async def _wait_for(self, pred, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            self._changed.clear()
            f = self._frame
            if f and pred(f):
                return True
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=max(0.1, end - time.time()))
            except TimeoutError:
                break
        return bool(self._frame and pred(self._frame))

    async def set_zone(self, zone: str, *, on: bool, temp_c: float | None = None) -> bool:
        if temp_c is None and on:
            raise CommandError("temperature required")
        cmd = f"CLEAR {SIDE[zone]}" if not on else validate(f"SET {SIDE[zone]} {round(min(45.0, max(10.0, temp_c)), 2)}")
        # the same command the bed just ignored: wait before sending it again (15 s, 30 s, ... up to 5 min), so a bed
        # that's rebooting or offline isn't flooded; a different command (someone pressed +) goes straight out
        fail = self._fails.get(zone)
        if fail and fail[0] == cmd and time.monotonic() < fail[1]:
            return False
        async with self._lock:
            if not on:
                await self._publish(cmd)
                ok = await self._wait_for(lambda f: f.zones[zone].on is False, 15)
            else:
                t = round(min(45.0, max(10.0, temp_c)), 2)
                await self._publish(cmd)
                ok = await self._wait_for(lambda f: f.zones[zone].on is True and f.zones[zone].setpoint_c is not None
                                          and abs(f.zones[zone].setpoint_c - t) <= 0.006, 15)
                if ok and self._frame and self._frame.system_state not in ("SYSTEM_ON", None):
                    await self._publish("SYSTEM ON")
        if ok:
            self._fails.pop(zone, None)
        else:
            n = (fail[2] + 1) if fail and fail[0] == cmd else 1
            self._fails[zone] = (cmd, time.monotonic() + min(300.0, 15.0 * 2 ** (n - 1)), n)
        return ok

    async def command(self, raw: str) -> str:
        cmd = validate(raw)
        await self._publish(cmd)
        return f"sent: {cmd}"

    async def set_light(self, *, brightness: int | None = None, rgb: tuple[int, int, int] | None = None) -> str:
        out = []
        if rgb is not None:
            out.append(await self.command("IB RGB " + " ".join(str(int(v)) for v in rgb)))
        if brightness is not None:
            out.append(await self.command(f"IB DIMMER {int(brightness)}"))
        return "; ".join(out)

    async def set_quiet_fan(self, on: bool) -> str:
        return await self.command("FAN QUIET ON" if on else "FAN QUIET OFF")

    async def restart(self) -> str:
        return await self.command("REBOOT")

    async def start_water_fill(self) -> str:
        return await self.command("FILL START")
