# Bed drivers

A **driver** is the only part of Ember & Tide that knows how to talk to a specific bed. The engine, the app and Studio
only use the contract in `core/embertide/drivers/base.py`, so supporting another heated or cooled mattress pad means
writing one class. Nothing else changes.

Built in:

| `ET_BED` | What it is |
|---|---|
| `sim` | The built-in simulator (the default). A pretend two-person bed with a scripted night, thermal lag and sensor noise. |
| `orion-mqtt` | The Orion topper through your own MQTT broker (see [bed-cutover.md](bed-cutover.md)). |
| `orion-cloud` | The maker's cloud API (`ET_ORION_API_KEY`). |
| `python:package.module:ClassName` | Your own driver, loaded from any installed Python package (below). |

## No bed yet? Run it on the simulator

`ET_BED=sim` is a supported way to run Ember & Tide, not only a test mode. You get the whole app: rhythms, the phone app and
Studio, notifications, Home Assistant entities, Autopilot. Studio, Simulator moves the clock, puts people in bed and raises
faults. Add presence sensors and a calendar from your own stack in Studio, Devices, and switch to a real driver later by
changing `ET_BED`; your people, rhythms and settings stay.

## The contract

Your class is created as `YourClass(cfg, clock)` (`cfg` is the server's `Config`, `clock` is its `Clock`; ignore what you
do not need) and must provide:

| Member | Meaning |
|---|---|
| `id: str`, `name: str` | Short id (`"my-bed"`) and a name people see. |
| `zones: list[str]` | Normally `["left", "right"]`. |
| `capabilities: set[Cap]` | What the hardware really does (below). The app hides controls for anything missing. |
| `async start()` / `async stop()` | Open and close connections, start and cancel background tasks. |
| `latest() -> BedFrame or None` | The newest reading, instantly (never block or do I/O here). `None` until the first one. |
| `async set_zone(zone, *, on, temp_c=None) -> bool` | Turn a side on or off and/or set its temperature in Celsius. Return `True` once the bed confirmed it (or if it cannot tell). Raise `CommandError` with a plain reason when refused. |
| `async command(raw) -> str` | A raw console command for Studio's console; raise `CommandError("this bed has no console")` if there is none. |
| `status() -> dict` | Small dict for diagnostics: `driver`, `name`, `online`, `detail`. No secrets. |

Optional (offer one only if you also list its capability; raise `CommandError` when refused): `set_light(brightness=, rgb=)`
(`LED_DIM`, `LED_RGB`), `set_quiet_fan(on)` (`FAN_QUIET`), `restart()` (`REBOOT`), `start_water_fill()` (`WATER_FILL`).
`profile` (see `drivers/profiles.py`) supplies the care-list tasks and hub light guide; use `profiles.GENERIC` if unsure.
`untried: set[Cap]` marks capabilities you offer but have never run on a real unit; the app says so.

### Capabilities (`Cap`)

`ZONE_TEMP` (set a target), `ZONE_POWER` (on/off), `MEASURED_TEMP` (report the pad's temperature), `OCCUPANCY` (a presence
signal from the bed), `BIO_HR` and `BIO_BR` (heart and breathing rate), `LED_RGB`, `LED_DIM`, `FAN_QUIET`, `WATER_FILL`,
`REBOOT`, `RAW_CONSOLE`, `HUB_HEALTH`, `WATER_STATUS`, `FAULTS`. A bed with only temperature control is fine: list
`ZONE_TEMP`, `ZONE_POWER`, `MEASURED_TEMP`, and add presence sensors in Studio, Devices.

### Frames

`latest()` returns a `BedFrame`:

- `ts` (Unix seconds), `online` (False when the bed stopped talking), optional `system_state`, `firmware`, `quiet_mode`,
  `brightness`, `rgb`, `fault`.
- `zones`: `{zone: ZoneFrame(on, setpoint_c, measured_c, sensor=SensorFrame(status, hr, br, clock))}`. Temperatures are Celsius.
- `SensorFrame.status` follows the Sleepace convention (0 in bed, 5 empty, 255 invalid); leave the sensor empty if the bed has
  none. `SensorFrame.clock` is an opaque token that must change while the sensor is alive (a frozen sensor is detected by it).
- `hub`: optional `HubHealth` (water, faults, Wi-Fi, firmware) for beds with a control unit. Translate vendor flags into the
  generic fault codes in `hub.py`.

Safety limits live in the engine (every write passes them), so a driver just does what it is asked, but it should still
reject values the hardware cannot take.

## A minimal driver

This is `core/embertide/drivers/example_template.py`, which is tested; copy it:

```python
class ExampleBed:
    id = "example"
    name = "Example topper"
    capabilities = {Cap.ZONE_TEMP, Cap.ZONE_POWER, Cap.MEASURED_TEMP}
    profile = profiles.GENERIC
    zones = ["left", "right"]

    def __init__(self, cfg=None, clock=None):
        self._z = {z: {"on": False, "set": 24.0, "now": 24.0} for z in self.zones}

    async def start(self): ...
    async def stop(self): ...

    def latest(self):
        return BedFrame(ts=time.time(), online=True, zones={
            z: ZoneFrame(on=v["on"], setpoint_c=v["set"], measured_c=v["now"]) for z, v in self._z.items()})

    async def set_zone(self, zone, *, on, temp_c=None):
        v = self._z[zone]
        v["on"] = on
        if temp_c is not None:
            v["set"] = v["now"] = float(temp_c)
        return True

    async def command(self, raw):
        raise CommandError("this bed has no console")

    def status(self):
        return {"driver": self.id, "name": self.name, "online": True}
```

## Using your own driver

Put your package in the same Python as Ember & Tide (`pip install ./my-driver`, or mount it into the container and add it to
`PYTHONPATH`) and set:

```yaml
ET_BED: python:my_bed.driver:MyBed
```

If the module or class cannot be found, or the class lacks required members, the server stops at start with a one-line
reason. Only set this to code you trust: it runs inside the server.

## Testing a driver

1. Try the class by itself: build it, `await start()`, check `latest()` returns frames and `set_zone` changes them.
2. Run the whole app on it: `ET_BED=python:... ET_AUTH=off ET_DATA_DIR=./data-dev python -m embertide` and look at
   Tonight and Studio. Compare with `ET_BED=sim` for how a healthy bed looks.
3. Add tests next to `core/tests/test_drivers_registry.py`: create the app with `cfg.bed = "python:..."` and `TestClient`
   (see that file), and unit-test parsing of your bed's messages like `tests/test_orion_mqtt.py` does.
4. Never test against a real bed without limits you trust; start with low temperatures and watch it.

## Contributing a driver

Open an issue first to describe the bed and how it talks (local MQTT, HTTP, Bluetooth, cloud). A pull request adds
`core/embertide/drivers/<name>.py`, one line in the `DRIVERS` registry in `core/embertide/app.py`, tests with recorded sample
messages (no real serial numbers, addresses or accounts), a setup section in the docs and a line in `configuration.md`. Mark
anything you could not run on real hardware in `untried`. See [CONTRIBUTING.md](../CONTRIBUTING.md).
