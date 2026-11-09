"""Simulated dual-zone topper. Reproduces the quirks that matter: thermal lag, cross-side sensor
pickup ("echo"), half-second false-occupancy blips, and a sensor clock that can freeze."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

from ..clock import Clock
from .base import BedFrame, Cap, CommandError, SensorFrame, ZoneFrame


class SimZone:
    def __init__(self, measured: float) -> None:
        self.on = False
        self.setpoint = 21.0
        self.measured = measured
        self.occupied = False
        self.in_since: float | None = None
        self.out_since: float | None = None   # when the sleeper got up (the pad is briefly really empty before it echoes)
        self.clock = 0
        self.frozen = False


class SimBed:
    id = "sim"
    name = "Simulated topper"
    capabilities = {Cap.ZONE_TEMP, Cap.ZONE_POWER, Cap.MEASURED_TEMP, Cap.OCCUPANCY, Cap.BIO_HR, Cap.BIO_BR,
                    Cap.LED_DIM, Cap.LED_RGB, Cap.FAN_QUIET, Cap.REBOOT, Cap.RAW_CONSOLE}
    zones = ["left", "right"]

    def __init__(self, clock: Clock, room_c: float = 21.5, seed: int = 7) -> None:
        self.clock = clock
        self.room_c = room_c
        self.z = {"left": SimZone(room_c), "right": SimZone(room_c)}
        self.echo = True          # one sleeper shows up on both pads
        self.blips = True         # empty pads occasionally report "occupied" for one frame
        self.scripted = True      # follow a realistic night script unless set manually
        self.pets: dict[str, int] = {}   # zone -> a pet's heart rate while it lies on that (empty) side
        self.tau_on_min = 8.0
        self.tau_off_min = 40.0
        self.quiet = True
        self.brightness = 20
        self.rgb = (226, 168, 120)
        self.system_on = True
        self.firmware = "sim-2.6.2"
        self._rand = random.Random(seed)
        self._last_ts = clock.now_ts()
        self._frame: BedFrame | None = None
        self._task: asyncio.Task | None = None
        # script: (zone, get in HH:MM, get out HH:MM, list of (up HH:MM, minutes))
        self.script = {"left": ("22:24", "06:40", []), "right": ("22:29", "07:05", [("02:12", 6)])}

    # ---------- lifecycle ----------
    async def start(self) -> None:
        self._tick()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            self._tick()

    def latest(self) -> BedFrame | None:
        return self._frame

    def status(self) -> dict:
        return {"driver": self.id, "name": self.name, "online": True, "detail": "simulator",
                "echo": self.echo, "blips": self.blips, "scripted": self.scripted}

    # ---------- control ----------
    async def set_zone(self, zone: str, *, on: bool, temp_c: float | None = None) -> bool:
        z = self.z[zone]
        z.on = on
        if temp_c is not None:
            z.setpoint = round(float(temp_c), 2)
        if on:
            self.system_on = True
        self._tick()
        return True

    async def command(self, raw: str) -> str:
        from .orion_mqtt import validate
        raw = validate(raw)          # identical allowlist and messages to the real local driver
        p = raw.strip().upper().split()
        if not p:
            raise CommandError("empty command")
        if p[0] == "SET" and len(p) >= 3:
            sides = ["left", "right"] if p[1] == "BOTH" else [p[1].lower()]
            temps = [float(v) for v in p[2:]] * (2 if len(p) == 3 and p[1] == "BOTH" else 1)
            for s, t in zip(sides, temps):
                await self.set_zone(s, on=True, temp_c=t)
            return "ok"
        if p[0] == "CLEAR" and len(p) == 2:
            for s in (["left", "right"] if p[1] == "BOTH" else [p[1].lower()]):
                await self.set_zone(s, on=False)
            return "ok"
        if p[:2] == ["SYSTEM", "ON"]:
            self.system_on = True
            return "ok"
        if p[:2] == ["FAN", "QUIET"] and len(p) == 3:
            self.quiet = p[2] == "ON"
            return "ok"
        if p[:2] == ["IB", "DIMMER"] and len(p) == 3:
            self.brightness = max(0, min(100, int(p[2])))
            return "ok"
        if p[:2] == ["IB", "RGB"] and len(p) == 5:
            self.rgb = tuple(max(0, min(255, int(v))) for v in p[2:])  # type: ignore[assignment]
            return "ok"
        if p == ["REBOOT"]:
            return "ok (simulated)"
        raise CommandError(f"unsupported command: {raw}")

    # simulator-only controls
    def set_presence(self, zone: str, occupied: bool) -> None:
        self.scripted = False
        z = self.z[zone]
        if occupied and not z.occupied:
            z.in_since = self.clock.now_ts()
        if not occupied and z.occupied:
            z.out_since = self.clock.now_ts()
        z.occupied = occupied

    def true_stage(self, zone: str, ts: float) -> str | None:
        """The simulated sleeper's actual stage: ~15 min to fall asleep, then 90-minute cycles with deep sleep
        front-loaded and REM growing toward morning, plus a short wake between some cycles."""
        z = self.z[zone]
        if not z.occupied or z.in_since is None:
            return None
        m = (ts - z.in_since) / 60 - (14 if zone == "left" else 19)
        if m < 0:
            return "awake"
        cycle, pos = int(m // 90), (m % 90) / 90
        deep_len = max(0.05, 0.40 - 0.09 * cycle)
        rem_len = min(0.40, 0.10 + 0.07 * cycle)
        if cycle >= 1 and pos < 0.03 and cycle % 2 == 1:
            return "awake"
        if pos < 0.12:
            return "light"
        if pos < 0.12 + deep_len:
            return "deep"
        if pos > 1 - rem_len:
            return "rem"
        return "light"

    # ---------- model ----------
    def _scripted_presence(self, zone: str, now: datetime) -> bool:
        get_in, get_out, ups = self.script[zone]
        def at(hm: str, base: datetime) -> datetime:
            h, m = map(int, hm.split(":"))
            return base.replace(hour=h, minute=m, second=0, microsecond=0)
        noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
        night = noon if now >= noon else noon - timedelta(days=1)
        t_in = at(get_in, night)
        t_out = at(get_out, night + timedelta(days=1))
        if not (t_in <= now < t_out):
            return False
        for hm, minutes in ups:
            u = at(hm, night + timedelta(days=1) if int(hm[:2]) < 12 else night)
            if u <= now < u + timedelta(minutes=minutes):
                return False
        return True

    def _tick(self) -> None:
        ts = self.clock.now_ts()
        dt_min = max(0.0, min(30.0, (ts - self._last_ts) / 60))
        self._last_ts = ts
        now = self.clock.now()
        for name, z in self.z.items():
            if self.scripted:
                occ = self._scripted_presence(name, now)
                if occ and not z.occupied:
                    z.in_since = ts
                if not occ and z.occupied:
                    z.out_since = ts
                z.occupied = occ
            target, tau = self._drive(z)
            # body heat nudges the pad toward skin temperature a little
            if z.occupied:
                target += 0.4
            if dt_min > 0:
                z.measured += (target - z.measured) * (1 - pow(2.718281828, -dt_min / tau))
            if not z.frozen:
                z.clock = (z.clock + max(1, int(dt_min * 60))) % (1 << 32)
        sensors: dict[str, SensorFrame] = {}
        for name in sorted(self.z, key=lambda n: not self.z[n].occupied):  # occupied pads first, echoes copy them
            sensors[name] = self._sensor(name, ts, sensors)
        zones = {name: ZoneFrame(on=z.on and self.system_on, setpoint_c=z.setpoint, measured_c=round(z.measured, 2),
                                 sensor=sensors[name]) for name, z in self.z.items()}
        self._frame = BedFrame(ts=ts, zones=zones, system_state="SYSTEM_ON" if self.system_on else "SYSTEM_OFF",
                               firmware=self.firmware, quiet_mode=self.quiet, brightness=self.brightness, rgb=self.rgb,
                               fault=False, online=True)

    def _drive(self, z: SimZone) -> tuple[float, float]:
        """Where the pad is heading and how slowly (time constant, minutes). Tests override it (a cold room)."""
        return (z.setpoint, self.tau_on_min) if (z.on and self.system_on) else (self.room_c, self.tau_off_min)

    def _sensor(self, name: str, ts: float, done: dict[str, SensorFrame]) -> SensorFrame:
        z = self.z[name]
        oname = "right" if name == "left" else "left"
        other = self.z[oname]
        clock = (z.clock & 255, (z.clock >> 8) & 255, (z.clock >> 16) & 255, (z.clock >> 24) & 255)
        if z.occupied:
            mins = (ts - (z.in_since or ts)) / 60
            st = self.true_stage(name, ts) or "awake"
            base = 64 - min(8, mins / 10)
            hr_off, hr_jit, br_off, move = {"awake": (7, 2.5, 1.5, 0.25), "light": (0, 1.2, 0, 0.04),
                                            "deep": (-5, 0.6, -1.5, 0.0), "rem": (3, 3.0, 1.0, 0.02)}[st]
            hr = round(base + hr_off + self._rand.uniform(-hr_jit, hr_jit))
            br = round(15 - min(2, mins / 30) + br_off + self._rand.uniform(-0.8, 0.8))
            status = self._rand.choices([0, 4, 6], weights=[1 - move, move * 0.7, move * 0.3])[0] if move else 0
            return SensorFrame(status=status, hr=hr, br=br, clock=clock)
        if name in self.pets:
            # a pet on this side (tests / simulator): the strip feels a fast heart and lots of movement
            hr = round(self.pets[name] + self._rand.uniform(-6, 6))
            return SensorFrame(status=self._rand.choice([0, 0, 4, 6]), hr=hr, br=self._rand.randint(20, 32), clock=clock)
        # the other pad starts picking the sleeper up a few seconds after they lie down, and a pad someone just left
        # reads empty for a few seconds before it picks up the partner (on the real bed the two pads never switch in
        # the very same frame), then it copies their readings
        if (self.echo and other.occupied and oname in done and ts - (other.in_since or ts - 60) >= 5
                and ts - (z.out_since or ts - 60) >= 5):
            o = done[oname]
            return SensorFrame(status=o.status, hr=o.hr, br=o.br, clock=clock)
        if self.blips and self._rand.random() < 0.004:
            return SensorFrame(status=0, hr=self._rand.randint(55, 90), br=self._rand.randint(10, 22), clock=clock)
        return SensorFrame(status=5, hr=None, br=None, clock=clock)
