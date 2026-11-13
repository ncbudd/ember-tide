"""MQTT broker addresses: mqtt:// (plain, port 1883) and mqtts:// (TLS, port 8883), for the bed driver and the Home Assistant bridge."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

SCHEMES = ("mqtt", "mqtts")


@dataclass(frozen=True)
class MqttTarget:
    host: str
    port: int
    tls: bool
    username: str | None
    password: str | None


def parse_mqtt_url(url: str) -> MqttTarget:
    """mqtt://user:pass@host[:port] or mqtts://... (credentials may be percent-encoded). Raises ValueError for anything else."""
    u = urlparse(url)
    if u.scheme not in SCHEMES:
        raise ValueError(f"The MQTT address {url.split('@')[-1]!r} should start with mqtt:// or mqtts://")
    tls = u.scheme == "mqtts"
    try:
        port = u.port
    except ValueError:
        raise ValueError("The MQTT address has a port that isn't a number") from None
    return MqttTarget(u.hostname or "localhost", port or (8883 if tls else 1883), tls,
                      unquote(u.username) if u.username else None, unquote(u.password) if u.password else None)


def tls_context(ca_file: str | None = None, insecure: bool = False) -> ssl.SSLContext:
    """Verify the broker against the system's CAs, or against ca_file (a private CA or the broker's own certificate).
    insecure=True skips verification entirely: for a self-signed broker on a trusted network only."""
    ctx = ssl.create_default_context(cafile=ca_file or None)
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def client_kwargs(t: MqttTarget, ca_file: str | None = None, insecure: bool = False) -> dict:
    """The aiomqtt.Client arguments for this target (TLS only for mqtts://)."""
    kw: dict = {"username": t.username, "password": t.password}
    if t.tls:
        kw["tls_context"] = tls_context(ca_file, insecure)
    return kw
