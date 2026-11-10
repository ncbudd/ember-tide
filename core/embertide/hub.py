"""The bed's control unit (the "hub"): plain-language health, for every driver.

A driver fills `drivers.base.HubHealth` with generic fault codes; this module turns that into words.
- `summary()` is small and safe for everyone (it rides in every snapshot): no network name, no address.
- `detail()` is the owner's page: advice, signal, restart time, versions, last command received.
Advice is generic and safe on purpose: it never tells anyone to open the unit.
"""

from __future__ import annotations

from .drivers.base import HubHealth

# generic fault code -> (what it is, what to do)
FAULTS: dict[str, tuple[str, str]] = {
    "water_flow": ("Water isn't flowing", "Check the water level and that the hoses aren't kinked."),
    "pump": ("Water pump is straining", "Check the water level and that the hoses aren't kinked."),
    "fan_stopped": ("Fan isn't running", "Check the hub's vents aren't blocked."),
    "fan_speed": ("Fan speed out of range", "Check the hub's vents aren't blocked."),
    "pad_cold_limit": ("Pad reached its low limit", "It protects itself and should carry on by itself. If it keeps happening, restart the hub."),
    "pad_hot_limit": ("Pad reached its high limit", "It cooled itself down. If it doesn't carry on, restart the hub."),
    "hub_hot": ("Hub is running hot", "Check the vents are clear and there's some space around the hub."),
    "not_level": ("Hub isn't level", "Check the hub is upright and on a flat surface."),
    "panel_link": ("Light panel isn't answering", "Restarting the hub may clear it."),
    "memory": ("Hub memory check failed", "Restart the hub. If it comes back, contact the maker."),
    "tilt_sensor": ("Tilt sensor isn't answering", "Restarting the hub may clear it."),
    "temp_readings": ("Temperature readings look unreliable", "Restarting the hub may clear it."),
    "room_sensor": ("Room sensor isn't answering", "Room humidity may be missing. Restarting the hub may clear it."),
    "manual_error": ("Hub was put in an error state", "Restart the hub to clear it."),
    "power": ("Power protection reported a fault", "Switch the bed off and on at the wall. If it comes back, contact the maker."),
    "zone_fault": ("Heating and cooling fault", "Restart the hub. If it comes back, contact the maker."),
}
UNKNOWN_FAULT = ("The hub reported a problem", "Restart the hub. If it comes back, contact the maker.")

STATUS_OK = "All good"
STATUS_WATER = "Check the water"


def fault_name(code: str) -> str:
    return FAULTS.get(code, UNKNOWN_FAULT)[0]


def fault_advice(code: str) -> str:
    return FAULTS.get(code, UNKNOWN_FAULT)[1]


def wifi_words(dbm: int | None) -> str | None:
    if dbm is None:
        return None
    return "Strong" if dbm >= -60 else "Good" if dbm >= -70 else "Fair" if dbm >= -80 else "Weak"


def _faults(h: HubHealth) -> list[tuple[str, str | None]]:
    seen, out = set(), []
    for f in h.faults:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def status_line(h: HubHealth | None) -> str | None:
    """One calm line: "All good", "Check the water", or the first fault in plain words. None = nothing known."""
    if h is None:
        return None
    fs = _faults(h)
    if fs:
        return fault_name(fs[0][0])
    if h.water_ok is False:
        return STATUS_WATER
    if h.safety_ok is False:
        return "The hub's safety check isn't passing"
    return STATUS_OK


def needs_attention(h: HubHealth | None) -> bool:
    return h is not None and status_line(h) != STATUS_OK


def summary(h: HubHealth | None) -> dict | None:
    """What every signed-in person may see (and what rides in each snapshot)."""
    if h is None:
        return None
    return {"line": status_line(h), "ok": not needs_attention(h), "water_ok": h.water_ok, "safety_ok": h.safety_ok,
            "faults": [{"code": c, "name": fault_name(c), "zone": z} for c, z in _faults(h)]}


def detail(h: HubHealth | None, *, untried: list[str] | None = None) -> dict | None:
    """The owner's view. Includes the network name and address, so it must never reach a sleeper, a viewer or a log."""
    if h is None:
        return None
    return {
        **summary(h),
        "faults": [{"code": c, "name": fault_name(c), "advice": fault_advice(c), "zone": z} for c, z in _faults(h)],
        "water_raw": dict(h.water_raw),
        "wifi": {"dbm": h.wifi_dbm, "words": wifi_words(h.wifi_dbm), "name": h.wifi_name, "address": h.wifi_address},
        "uptime_s": h.uptime_s,
        "firmware": {"version": h.fw_version, "build": h.fw_build, "panel": h.fw_panel},
        "last_command": h.last_command,
        "fan": {"rpm": dict(h.fan_rpm), "pwm": h.fan_pwm},
        "humidity": h.humidity, "room_c": h.room_c, "free_memory_kb": h.free_memory_kb,
        "untried": untried or [],
    }


def same_command(a: str | None, b: str | None) -> bool:
    """Is the unit's "last command received" the command we sent? Case, spacing and number formatting don't matter."""
    def norm(s: str | None) -> list:
        out: list = []
        for t in (s or "").upper().split():
            try:
                out.append(round(float(t), 2))
            except ValueError:
                out.append(t)
        return out
    return bool(a and b) and norm(a) == norm(b)
