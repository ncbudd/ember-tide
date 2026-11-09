import json

import pytest

from embertide.drivers.base import CommandError
from embertide.drivers.orion_mqtt import parse_status, validate


def payload(**over):
    s1 = [4, 5, 20, 19, 0, 0, 0, 14, 1, 1, 0, 10, 20, 30, 40, 58, 14, 0, 0, 0, 0, 0, 0, 0]
    s2 = [4, 6, 20, 19, 0, 0, 0, 14, 1, 1, 0, 11, 21, 31, 41, 0, 0, 5, 0, 0, 0, 0, 0, 0]
    d = {"type": "status", "serial": "OSCT123", "system_state": "SYSTEM_ON", "fw": {"version": "2.6.2"},
         "tec": {"left": {"setpoint": 17.22, "control_enabled": True}, "right": {"setpoint": 22.0, "control_enabled": False}},
         "temps": {"ib_left": 18.1, "ib_right": 21.4}, "fans": {"quiet_mode": True},
         "led": {"led_brightness": 20, "idle_r": 226, "idle_g": 168, "idle_b": 120},
         "sensors": {"sensor1": s1, "sensor2": s2}, "errors": {"e1": 0}}
    d.update(over)
    return json.dumps(d).encode()


def test_parse_status():
    serial, f = parse_status("orion/OSCT123/out", payload(), 1.0)
    assert serial == "OSCT123" and f.firmware == "2.6.2" and f.quiet_mode is True
    assert f.zones["left"].on is True and f.zones["left"].setpoint_c == 17.22 and f.zones["left"].measured_c == 18.1
    assert f.zones["left"].sensor.status == 0 and f.zones["left"].sensor.hr == 58 and f.zones["left"].sensor.br == 14
    assert f.zones["right"].sensor.status == 5 and f.zones["right"].sensor.hr is None
    assert f.rgb == (226, 168, 120) and f.fault is False


def test_parse_rejects_wrong_topic_or_serial():
    assert parse_status("orion/OTHER/out", payload(), 1.0) is None
    assert parse_status("orion/OSCT123/in", payload(), 1.0) is None
    assert parse_status("orion/OSCT123/out", b"not json", 1.0) is None


def test_validate_allowlist():
    assert validate("set left 17.2222") == "SET LEFT 17.22"
    assert validate("SET BOTH 18 22") == "SET BOTH 18 22"
    assert validate("clear right") == "CLEAR RIGHT"
    for bad in ("OTA http://x/fw.bin", "ERASE WIFI", "IB UPDATE", "SET LEFT 60", "LPUMP OFF", "SET LEFT 18 19"):
        with pytest.raises(CommandError):
            validate(bad)


# ---- real frames captured from firmware 2.6.2 (serial, Wi-Fi name and IP replaced) ----
import json as _json
import pathlib as _pl

REAL = _json.loads((_pl.Path(__file__).parent / "fixtures" / "orion_fw262_frames.json").read_text(encoding="utf-8"))


def test_real_frames_all_parse():
    from embertide.drivers.orion_mqtt import parse_status
    assert parse_status("orion/AABBCCDDEEFF/out", REAL["online"].encode(), 0) is None   # the bed's hello message
    for kind, payload in REAL.items():
        if kind == "online":
            continue
        r = parse_status("orion/AABBCCDDEEFF/out", payload.encode("utf-8"), 0)
        assert r is not None, kind                      # nan and garbled version strings no longer lose the frame
        serial, f = r
        assert serial == "AABBCCDDEEFF" and f.firmware == "2.6.2"


def test_empty_sensor_block_is_not_someone_in_bed():
    from embertide.drivers.orion_mqtt import parse_status
    _, f = parse_status("orion/AABBCCDDEEFF/out", REAL["valid"].encode(), 0)
    assert f.zones["left"].sensor.status is None        # sensor1 = all zeros: no report, NOT status 0 ("in bed")
    assert f.zones["right"].sensor.status in (None, 5)  # sensor2 real report: not in bed


def test_booting_zero_temperature_is_ignored():
    from embertide.drivers.orion_mqtt import parse_status
    _, f = parse_status("orion/AABBCCDDEEFF/out", REAL["nan"].encode(), 0)
    assert f.zones["left"].measured_c is None or f.zones["left"].measured_c > 1


def test_driver_holds_last_report_across_empty_blocks():
    from embertide.drivers.base import BedFrame, SensorFrame, ZoneFrame
    from embertide.drivers.orion_mqtt import OrionMqtt
    d = OrionMqtt("mqtt://x")
    real = SensorFrame(status=5, clock=(1, 2, 3, 4))
    f1 = d._hold_sensors(BedFrame(ts=100, zones={"right": ZoneFrame(sensor=real)}))
    f2 = d._hold_sensors(BedFrame(ts=105, zones={"right": ZoneFrame(sensor=SensorFrame())}))
    assert f2.zones["right"].sensor.status == 5         # gap bridged
    f3 = d._hold_sensors(BedFrame(ts=120, zones={"right": ZoneFrame(sensor=SensorFrame())}))
    assert f3.zones["right"].sensor.status is None       # quiet for too long: unknown
