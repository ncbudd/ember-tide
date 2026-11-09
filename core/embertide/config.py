"""Configuration from environment variables (Docker-friendly). Everything has a safe default: with no
configuration at all you get the simulator, so the UI can be tried before touching a real bed."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(_env("ET_DATA_DIR", "./data")))
    tz: str = field(default_factory=lambda: _env("ET_TZ", _env("TZ", "UTC")))
    bed: str = field(default_factory=lambda: _env("ET_BED", "sim"))               # sim | orion-mqtt | orion-cloud
    mqtt_url: str = field(default_factory=lambda: _env("ET_MQTT_URL", "mqtt://localhost:1883"))
    orion_serial: str | None = field(default_factory=lambda: _env("ET_ORION_SERIAL"))
    orion_api_key: str | None = field(default_factory=lambda: _env("ET_ORION_API_KEY"))
    sim_speed: float = field(default_factory=lambda: float(_env("ET_SIM_SPEED", "1")))
    host: str = field(default_factory=lambda: _env("ET_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("ET_PORT", "8790")))
    llm_url: str | None = field(default_factory=lambda: _env("ET_LLM_URL"))          # e.g. http://localhost:11434/v1
    llm_model: str = field(default_factory=lambda: _env("ET_LLM_MODEL", "qwen2.5:7b"))
    llm_key: str | None = field(default_factory=lambda: _env("ET_LLM_KEY"))
    # "openai" = any OpenAI-compatible endpoint at ET_LLM_URL (Ollama, LM Studio, a hosted API); "anthropic" = Claude
    # through the Anthropic SDK (key from ET_LLM_KEY or the SDK's own credentials; model defaults to claude-opus-5-5)
    llm_provider: str = field(default_factory=lambda: (_env("ET_LLM_PROVIDER", "openai") or "openai").lower())
    public_url: str | None = field(default_factory=lambda: _env("ET_PUBLIC_URL"))   # how phones reach this server, for notification buttons
    ha_mqtt_url: str | None = field(default_factory=lambda: _env("ET_HA_MQTT_URL"))           # mqtt://user:pass@homeassistant.local:1883
    ha_prefix: str = field(default_factory=lambda: _env("ET_HA_DISCOVERY_PREFIX", "homeassistant"))
    auth: bool = field(default_factory=lambda: (_env("ET_AUTH", "on") or "on").lower() not in ("off", "0", "false", "no"))
    trust_proxy: bool = field(default_factory=lambda: (_env("ET_TRUST_PROXY", "off") or "off").lower() in ("on", "1", "true", "yes"))
    web_dir: Path | None = field(default_factory=lambda: Path(p) if (p := _env("ET_WEB_DIR")) else None)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "embertide.db"
