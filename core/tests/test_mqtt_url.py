"""mqtt:// and mqtts:// broker addresses."""

import ssl

import pytest

from embertide.drivers.orion_mqtt import OrionMqtt
from embertide.ha_bridge import HaBridge
from embertide.mqtt_url import client_kwargs, parse_mqtt_url, tls_context


def test_plain_and_tls_defaults():
    p = parse_mqtt_url("mqtt://broker.local")
    assert (p.host, p.port, p.tls) == ("broker.local", 1883, False)
    t = parse_mqtt_url("mqtts://u%40x:pass@broker.example")
    assert (t.host, t.port, t.tls, t.username, t.password) == ("broker.example", 8883, True, "u@x", "pass")
    assert parse_mqtt_url("mqtts://b:8884").port == 8884 and parse_mqtt_url("mqtt://b:1884").port == 1884


def test_bad_addresses():
    for bad in ("http://broker", "broker:1883", "", "mqtt://b:notaport"):
        with pytest.raises(ValueError):
            parse_mqtt_url(bad)


def test_tls_context_modes(tmp_path):
    strict = tls_context()
    assert strict.verify_mode == ssl.CERT_REQUIRED and strict.check_hostname
    loose = tls_context(insecure=True)
    assert loose.verify_mode == ssl.CERT_NONE and not loose.check_hostname
    with pytest.raises(FileNotFoundError):
        tls_context(str(tmp_path / "missing-ca.pem"))


def test_client_kwargs_only_use_tls_for_mqtts():
    assert "tls_context" not in client_kwargs(parse_mqtt_url("mqtt://b"))
    kw = client_kwargs(parse_mqtt_url("mqtts://u:p@b"), None, True)
    assert kw["username"] == "u" and kw["tls_context"].verify_mode == ssl.CERT_NONE


async def test_orion_driver_and_bridge_take_mqtts():
    d = OrionMqtt("mqtts://u:p@bed.example", "SER", insecure=True)
    assert (d.host, d.port, d.tls, d.username, d.insecure) == ("bed.example", 8883, True, "u", True)
    assert OrionMqtt("mqtt://bed.example").port == 1883
    h = HaBridge(None, "mqtts://ha.example", ca_file="/certs/ca.pem", insecure=False)
    assert h.ca_file == "/certs/ca.pem" and h.insecure is False
    assert parse_mqtt_url(h.url).port == 8883
