"""Validated application configuration."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field, HttpUrl
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)


class DatabaseConfig(BaseModel):
    path: Path = Path("data/netmon.db")


class DiscoveryConfig(BaseModel):
    subnets: list[str] = Field(default_factory=list)
    interval_seconds: float = Field(default=300.0, gt=0)
    offline_after_misses: int = Field(default=3, ge=1)
    concurrency: int = Field(default=32, ge=1, le=512)


class LatencyConfig(BaseModel):
    interval_seconds: float = Field(default=10.0, gt=0)
    targets: list[str] = Field(default_factory=lambda: ["gateway", "1.1.1.1", "8.8.8.8"])
    probes_per_sample: int = Field(default=5, ge=1)
    timeout_ms: int = Field(default=1000, ge=1)


class DnsConfig(BaseModel):
    interval_seconds: float = Field(default=60.0, gt=0)
    resolvers: list[str] = Field(default_factory=lambda: ["1.1.1.1", "8.8.8.8"])
    query_names: list[str] = Field(default_factory=lambda: ["example.com", "cloudflare.com"])


class HttpConfig(BaseModel):
    interval_seconds: float = Field(default=60.0, gt=0)
    urls: list[HttpUrl] = Field(
        default_factory=lambda: [HttpUrl("https://www.google.com/generate_204")]
    )
    timeout_seconds: float = Field(default=5.0, gt=0)


class OutageConfig(BaseModel):
    failures_to_open: int = Field(default=3, ge=1)
    successes_to_close: int = Field(default=2, ge=1)
    traceroute_on_open: bool = True


class WanConfig(BaseModel):
    enabled: bool = True
    interval_seconds: float = Field(default=30.0, gt=0)


class SpeedtestConfig(BaseModel):
    enabled: bool = False
    interval_seconds: float = Field(default=21600.0, gt=0)


class RetentionConfig(BaseModel):
    raw_days: int = Field(default=7, ge=1)
    rollup_1m_days: int = Field(default=30, ge=1)
    rollup_5m_days: int = Field(default=365, ge=1)


class AlertsConfig(BaseModel):
    enabled: bool = True
    webhook_url: HttpUrl | None = None
    windows_toast: bool = True


class ThrottleConfig(BaseModel):
    backend: str = "null"


class TomlSettingsSource:
    """A tiny settings source so TOML remains optional during local startup."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        with self.path.open("rb") as file:
            return tomllib.load(file)


class NetmonSettings(BaseSettings):
    """Application settings, loaded from TOML and then environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="NETMON_", env_nested_delimiter="__", extra="ignore"
    )
    config_path: ClassVar[Path] = Path("netmon.toml")

    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    latency: LatencyConfig = Field(default_factory=LatencyConfig)
    dns: DnsConfig = Field(default_factory=DnsConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    outage: OutageConfig = Field(default_factory=OutageConfig)
    wan: WanConfig = Field(default_factory=WanConfig)
    speedtest: SpeedtestConfig = Field(default_factory=SpeedtestConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    throttle: ThrottleConfig = Field(default_factory=ThrottleConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[Any, ...]:
        del settings_cls, dotenv_settings, file_secret_settings
        path = Path(os.environ.get("NETMON_CONFIG", str(cls.config_path)))
        return init_settings, env_settings, TomlSettingsSource(path)
