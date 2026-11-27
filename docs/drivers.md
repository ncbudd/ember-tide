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
| `ha-climate` | A bed controlled through Home Assistant `climate` entities, such as a BedJet or an OOLER (`ET_HA_URL`, `ET_HA_TOKEN`, `ET_BED_ENTITIES`). |
| `sleepme` | A Sleepme Dock Pro (ChiliPad Dock Pro) through Sleepme's official developer API (`ET_SLEEPME_TOKEN`). |
| `free-sleep` | An Eight Sleep Pod running [free-sleep](https://github.com/throwaway31265/free-sleep), fully local (`ET_FREE_SLEEP_URL`). |
| `python:package.module:ClassName` | Your own driver, loaded from any installed Python package (below). |

## Home Assistant climate (BedJet and others)

> **New driver:** `ha-climate` has not yet been tried on a real BedJet. If you run it on real hardware, please open an issue and report how it goes.

This driver lets Ember & Tide control any bed exposed in Home Assistant as one or two `climate` entities, starting with the BedJet (connected over Bluetooth through ESPHome or a community Home Assistant integration).

### What it needs

- A reachable Home Assistant instance (`ET_HA_URL`)
- A Home Assistant long-lived access token (`ET_HA_TOKEN`, kept secret in `.env`)
- One or two `climate.*` entities in Home Assistant (`ET_BED_ENTITIES`)

```yaml
ET_BED: ha-climate
ET_HA_URL: http://homeassistant.local:8123
ET_HA_TOKEN: your-long-lived-access-token
ET_BED_ENTITIES: left=climate.bedjet_left,right=climate.bedjet_right
ET_BED_KIND: auto
ET_HA_NEUTRAL_C: "26.0"
```

### Mapping sides to entities

- Set `ET_BED_ENTITIES` to `left=climate.a,right=climate.b` for a two-sided setup, or a single `climate.a` (which maps to `left` only). Only the configured sides appear as zones.
- Ember & Tide reads Home Assistant's unit system (`°C` or `°F`) from `/api/config` and converts temperatures automatically (rounding sent temperatures to `0.5` °C or `1` °F and clamping to the entity's `min_temp` / `max_temp`).
- This driver provides temperature control and measured temperature only (no bed occupancy or bio sensors). Add presence sensors in Studio, Devices if you want occupancy detection.

### BedJet notes

- **`ET_BED_KIND=auto`** (default) uses `bedjet` whenever `bedjet` appears in the entity ID or `friendly_name`, and `thermostat` otherwise. Set `bedjet` or `thermostat` explicitly if needed.
- **Warm vs cool (`ET_HA_NEUTRAL_C`, default `26.0`)**: Requests warmer than `ET_HA_NEUTRAL_C` set `heat` with the target temperature. Requests at or below `ET_HA_NEUTRAL_C` set `cool` (room-air fan on a BedJet) and scale the fan speed linearly from `ET_HA_NEUTRAL_C` (~30% of `fan_modes`) up to the fastest fan speed at `16.0` °C or below, without sending a target temperature.
- **Heat timer**: a BedJet ends heat by itself after about 4 hours. Ember & Tide sees that side switch off while the plan still wants it on and turns it back on, the same way it does for any bed. To stop it for the night, use Off tonight in the app rather than the BedJet's remote, or it will be switched back on.
- **What it reports**: the temperature a BedJet reports is the air coming out of it, not the bed surface. There's no in-bed sensor, so the night starts from your other presence sensors (Studio, Devices) or the schedule.
- **One Bluetooth connection**: a BedJet accepts one Bluetooth connection at a time, so close its phone app while Home Assistant is connected.

### OOLER (ChiliPad) through Home Assistant

The [OOLER integration](https://github.com/PostLogical/ooler) (HACS, local Bluetooth, works with ESPHome Bluetooth
proxies) shows each OOLER as a `climate` entity, so `ha-climate` can run it: one OOLER per side, `ET_BED_KIND=thermostat`.
An OOLER takes its setpoint in whole °F (55 to 115) and ignores changes while it's off, which the driver handles by
turning it on before setting the temperature. Like a BedJet, it accepts one Bluetooth connection at a time.

## Sleepme Dock Pro

> **New driver:** `sleepme` has not yet been tried on a real Dock Pro. If you run it, please open an issue and say how it goes.

Uses Sleepme's own [developer API](https://docs.developer.sleep.me), so it needs the internet but no workarounds.

1. In the Sleepme app or at sleep.me, open **Account details → Developer API → Create new token** (the dock must already
   be set up in the Sleepme app). Copy the token.
2. Set it in `.env`:

```yaml
ET_BED: sleepme
ET_SLEEPME_TOKEN: your-sleepme-token
# ET_SLEEPME_DEVICES: left=<device id>,right=<device id>   # only if the sides come out swapped
```

- One Dock Pro is one side. With one dock it becomes the left side; with two, the first two docks on the account become
  left and right. If they come out swapped, set `ET_SLEEPME_DEVICES`; the server log lists each dock's name and id when it starts.
- Sleepme allows 10 requests a minute per account, so Ember & Tide reads each dock once a minute and sends one request
  per change. Changes made in the Sleepme app show up within a minute.
- It reports the water temperature and low water (Care reminds you). There's no in-bed sensor on the dock, so the night
  starts from your other presence sensors (a Sleepme Sleep Tracker isn't read yet) or the schedule.

## Eight Sleep Pod (free-sleep)

> **New driver:** `free-sleep` has not yet been tried on a real Pod. If you run it, please open an issue and say how it goes.

[free-sleep](https://github.com/throwaway31265/free-sleep) is a community project that replaces the Pod's software so it
runs without Eight Sleep's cloud. It works on the Pod 3 (without SD card), Pod 4 and Pod 5, and installing it means
opening the Pod and connecting to its board: follow its README, and know that it may affect your warranty. Once it runs,
it serves a small web page and API on your home network, and Ember & Tide talks to that directly:

```yaml
ET_BED: free-sleep
ET_FREE_SLEEP_URL: http://192.168.1.60:3000     # the Pod's address
```

- Both sides are controlled; temperatures go in whole °F, 55 to 110 (about 13 to 43 °C).
- free-sleep has no sign-in: anyone on your network can control the Pod. Keep it on a trusted network (free-sleep also
  has a script to block the Pod's internet access).
- If away mode is on for one side in free-sleep's own settings, free-sleep applies changes to both sides. Leave away mode
  off and use Ember & Tide's away instead.
- Heart rate and presence from the Pod aren't read yet; add your other presence sensors in Studio, Devices.
- Eight Sleep's cloud isn't supported: it has no public API, and the unofficial one needs the app's built-in key and your password.

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
