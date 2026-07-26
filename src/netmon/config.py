"""Validated application configuration."""

from __future__ import annotations

import ipaddress
import os
import tomllib
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)


class ApiConfig(BaseModel):
    token: str | None = None
    allow_loopback_unauthenticated: bool = True

    @model_validator(mode="after")
    def normalize_token(self) -> ApiConfig:
        if self.token is not None:
            self.token = self.token.strip() or None
        return self


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
    traceroute_timeout_seconds: float = Field(default=30.0, gt=0)
    traceroute_max_hops: int = Field(default=30, ge=1, le=128)


class WanConfig(BaseModel):
    enabled: bool = True
    interval_seconds: float = Field(default=30.0, gt=0)
    gateway: str | None = None
    lan_ip: str | None = None


class SpeedtestConfig(BaseModel):
    enabled: bool = False
    interval_seconds: float = Field(default=21600.0, gt=0)
    endpoint: HttpUrl = HttpUrl("https://speed.cloudflare.com")
    download_bytes: int = Field(default=10_000_000, gt=0)
    upload_bytes: int = Field(default=10_000_000, gt=0)
    max_bytes: int = Field(default=50_000_000, gt=0)
    total_time_budget_seconds: float = Field(default=120.0, gt=0)

    @model_validator(mode="after")
    def safe_interval(self) -> SpeedtestConfig:
        if self.interval_seconds < 900:
            raise ValueError("speedtest.interval_seconds must be at least 900 seconds")
        if self.download_bytes > self.max_bytes or self.upload_bytes > self.max_bytes:
            raise ValueError("speedtest byte counts cannot exceed speedtest.max_bytes")
        return self


class HostNicConfig(BaseModel):
    enabled: bool = True
    interval_seconds: float = Field(default=10.0, gt=0)
    top_processes: int = Field(default=0, ge=0, le=100)


class EeroConfig(BaseModel):
    enabled: bool = False
    interval_seconds: float = Field(default=60.0, ge=30.0)
    token_path: Path = Path("data/eero.token")
    base_url: HttpUrl = HttpUrl("https://api-user.e2ro.com")


class RetentionConfig(BaseModel):
    raw_days: int = Field(default=7, ge=1)
    rollup_1m_days: int = Field(default=30, ge=1)
    rollup_5m_days: int = Field(default=365, ge=1)

    @model_validator(mode="after")
    def tiers_are_ordered(self) -> RetentionConfig:
        if not self.raw_days < self.rollup_1m_days < self.rollup_5m_days:
            raise ValueError(
                "retention tiers must be ordered raw_days < rollup_1m_days < rollup_5m_days"
            )
        return self


class AlertRuleConfig(BaseModel):
    name: str
    metric_kind: str
    target: str | None = None
    source: str | None = None
    operator: str = ">"
    threshold: float
    failures_to_open: int = Field(default=3, ge=1)
    successes_to_close: int = Field(default=2, ge=1)
    severity: str = "warning"

    @model_validator(mode="after")
    def valid_operator(self) -> AlertRuleConfig:
        if self.operator not in {">", ">=", "<", "<=", "==", "!="}:
            raise ValueError("alerts rule operator must be one of >, >=, <, <=, ==, !=")
        if self.severity not in {"info", "warning", "error", "critical"}:
            raise ValueError("alerts rule severity must be info, warning, error, or critical")
        return self


class AlertsConfig(BaseModel):
    enabled: bool = True
    interval_seconds: float = Field(default=5.0, gt=0)
    cooldown_seconds: float = Field(default=3600.0, ge=0)
    rules: list[AlertRuleConfig] = Field(default_factory=list)
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
    api: ApiConfig = Field(default_factory=ApiConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    latency: LatencyConfig = Field(default_factory=LatencyConfig)
    dns: DnsConfig = Field(default_factory=DnsConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    outage: OutageConfig = Field(default_factory=OutageConfig)
    wan: WanConfig = Field(default_factory=WanConfig)
    host_nic: HostNicConfig = Field(default_factory=HostNicConfig)
    speedtest: SpeedtestConfig = Field(default_factory=SpeedtestConfig)
    eero: EeroConfig = Field(default_factory=EeroConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    throttle: ThrottleConfig = Field(default_factory=ThrottleConfig)

    @model_validator(mode="after")
    def safe_api_binding(self) -> NetmonSettings:
        if self.api.token is None and not _is_loopback_host(self.server.host):
            raise ValueError(
                "api.token (NETMON_API__TOKEN) is required when server.host is not loopback"
            )
        return self

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


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower().removesuffix(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
