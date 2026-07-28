"""FastAPI application and lifespan wiring."""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.base import RequestResponseEndpoint

from netmon.alerts.notifiers import (
    LogNotifier,
    NotificationDispatcher,
    Notifier,
    WebhookNotifier,
    WindowsToastNotifier,
)
from netmon.alerts.rules import AlertEvaluator
from netmon.collectors.alerts import AlertCollector
from netmon.collectors.base import Clock, Collector, RealClock
from netmon.collectors.discovery import DiscoveryCollector
from netmon.collectors.dns_probe import DnsCollector
from netmon.collectors.eero_usage import EeroUsageCollector
from netmon.collectors.host_nic import HostNicCollector
from netmon.collectors.http_probe import HttpCollector
from netmon.collectors.latency import LatencyCollector
from netmon.collectors.retention import RetentionCollector
from netmon.collectors.speedtest import SpeedtestCollector
from netmon.collectors.wan_status import WanStatusCollector
from netmon.config import NetmonSettings
from netmon.db import DatabaseRuntimeLock, Store
from netmon.outage import OutageCoordinator, OutageStateMachine
from netmon.probes.arp import create_arp_probe, create_interface_probe
from netmon.probes.dns import ResolverDnsProbe
from netmon.probes.eero_cloud import EeroCloudProbe, HttpxEeroTransport
from netmon.probes.gateway import create_gateway_probe
from netmon.probes.host_nic import PsutilHostNicProbe
from netmon.probes.hostname import create_hostname_probe
from netmon.probes.http import HttpxProbe
from netmon.probes.icmp import create_icmp_probe
from netmon.probes.oui import BundledOuiLookup
from netmon.probes.speedtest import HttpxSpeedtestProbe
from netmon.probes.traceroute import SystemTracerouteProbe
from netmon.probes.upnp import UpnpProbe
from netmon.probes.webhook import HttpxWebhookProbe
from netmon.scheduler import Scheduler


class DeviceLabelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(max_length=120)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


def _default_scheduler(settings: NetmonSettings, store: Store, clock: Clock) -> Scheduler:
    gateway = create_gateway_probe()
    machine = OutageStateMachine(
        failures_to_open=settings.outage.failures_to_open,
        successes_to_close=settings.outage.successes_to_close,
        wan_interval_seconds=settings.wan.interval_seconds,
    )
    coordinator = OutageCoordinator(
        machine,
        clock,
        store.record_event,
        settings.outage,
        traceroute=SystemTracerouteProbe(),
        event_update=store.update_event_details,
    )
    discovery = DiscoveryCollector(
        arp=create_arp_probe(),
        icmp=create_icmp_probe(),
        oui=BundledOuiLookup(),
        hostname=create_hostname_probe(),
        interfaces=create_interface_probe(),
        clock=clock,
        config=settings.discovery,
        observation_sink=store.record_discovery,
    )
    latency = LatencyCollector(
        create_icmp_probe(), gateway, clock, settings.latency, coordinator.observe
    )
    dns = DnsCollector(ResolverDnsProbe(clock), clock, settings.dns, coordinator.observe)
    http = HttpCollector(
        HttpxProbe(clock, settings.http.timeout_seconds), clock, settings.http, coordinator.observe
    )
    wan = WanStatusCollector(
        UpnpProbe(
            lan_ip=settings.wan.lan_ip,
            gateway=gateway,
            gateway_ip=settings.wan.gateway,
        ),
        clock,
        settings.wan,
        event_sink=store.record_event,
        wan_sink=coordinator.wan,
    )
    collectors: list[Collector] = [discovery, latency, dns, http]
    if settings.wan.enabled:
        collectors.append(wan)
    if settings.host_nic.enabled:
        collectors.append(HostNicCollector(PsutilHostNicProbe(), clock, settings.host_nic))
    if settings.speedtest.enabled:
        collectors.append(SpeedtestCollector(HttpxSpeedtestProbe(clock), clock, settings.speedtest))
    if settings.eero.enabled:
        token = _read_token(settings.eero.token_path)
        collectors.append(
            EeroUsageCollector(
                EeroCloudProbe(HttpxEeroTransport(str(settings.eero.base_url))),
                token,
                clock,
                settings.eero,
            )
        )
    collectors.append(RetentionCollector(store, clock, settings.retention))
    if settings.alerts.enabled:
        notifiers: list[Notifier] = [LogNotifier()]
        if settings.alerts.webhook_url is not None:
            notifiers.append(WebhookNotifier(HttpxWebhookProbe(), str(settings.alerts.webhook_url)))
        if settings.alerts.windows_toast:
            notifiers.append(WindowsToastNotifier())
        collectors.append(
            AlertCollector(
                store,
                clock,
                settings.alerts,
                AlertEvaluator(settings.alerts.rules, settings.alerts.cooldown_seconds),
                NotificationDispatcher(notifiers),
            )
        )
    return Scheduler(
        collectors,
        sink=store.persist_samples,
        clock=clock,
        status_provider=coordinator.status,
    )


def create_app(
    settings: NetmonSettings | None = None,
    store: Store | None = None,
    scheduler: Scheduler | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    application_settings = settings or NetmonSettings()
    application_store = store or Store(application_settings.database.path)
    application_clock = clock or RealClock()
    application_scheduler = scheduler or _default_scheduler(
        application_settings, application_store, application_clock
    )
    expected_token = application_settings.api.token
    database_lock = DatabaseRuntimeLock(application_store.path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database_lock.acquire()
        try:
            await application_scheduler.start()
            try:
                yield
            finally:
                await application_scheduler.stop()
        finally:
            database_lock.release()

    app = FastAPI(title="netmon", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def authenticate(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path == "/health" or _loopback_request_allowed(
            request, application_settings.api.allow_loopback_unauthenticated
        ):
            return await call_next(request)

        supplied_token = _bearer_token(request)
        if expected_token is None or supplied_token is None:
            return _unauthorized()
        if not secrets.compare_digest(
            supplied_token.encode("utf-8"), expected_token.encode("utf-8")
        ):
            return _unauthorized()
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/devices")
    async def devices() -> list[dict[str, Any]]:
        return application_store.list_devices()

    @app.get("/devices/{device_id}")
    async def device(device_id: int) -> dict[str, Any]:
        result = application_store.get_device(device_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Device not found")
        return result

    @app.patch("/devices/{device_id}")
    async def update_device_label(device_id: int, update: DeviceLabelUpdate) -> dict[str, Any]:
        result = application_store.set_device_label(device_id, update.label)
        if result is None:
            raise HTTPException(status_code=404, detail="Device not found")
        return result

    @app.get("/events")
    async def events(
        limit: int = Query(default=100, ge=1, le=1000),
        kind: str | None = None,
        severity: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return application_store.list_filtered_events(limit, kind, start, end, severity=severity)

    @app.get("/events/{event_id}")
    async def event(event_id: int) -> dict[str, Any]:
        result = application_store.get_event(event_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return result

    @app.get("/metrics")
    async def metrics(
        kind: str | None = None,
        target: str | None = None,
        source: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = Query(default=10000, ge=1, le=100000),
    ) -> list[dict[str, Any]]:
        return application_store.list_metrics(kind, target, start, end, limit, source)

    @app.get("/alerts")
    async def alerts(
        limit: int = Query(default=100, ge=1, le=1000),
        severity: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return application_store.list_alerts(limit, severity, start, end)

    @app.get("/status")
    async def status() -> dict[str, object]:
        return application_scheduler.current_status()

    web_path = Path(__file__).parents[1] / "web"
    dashboard_path = web_path / "index.html"

    @app.get("/", response_class=HTMLResponse)
    @app.get("/timeline", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/devices-view", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/diagnostics", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/bandwidth", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/alerts-view", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> str:
        config = _dashboard_config(application_settings)
        return dashboard_path.read_text(encoding="utf-8").replace(
            "__NETMON_CONFIG__", _json_for_script(config)
        )

    @app.get("/assets/dashboard.css", include_in_schema=False)
    async def dashboard_css() -> FileResponse:
        return FileResponse(web_path / "dashboard.css", media_type="text/css")

    @app.get("/assets/dashboard.js", include_in_schema=False)
    async def dashboard_js() -> FileResponse:
        return FileResponse(web_path / "dashboard.js", media_type="text/javascript")

    return app


def _dashboard_config(settings: NetmonSettings) -> dict[str, Any]:
    """Expose the read-only, non-secret settings mirror used by the dashboard."""

    return {
        "version": "1.0.0",
        "server": {"host": settings.server.host, "port": settings.server.port},
        "api": {
            "allow_loopback_unauthenticated": settings.api.allow_loopback_unauthenticated,
            "token_configured": settings.api.token is not None,
        },
        "database": {"path": str(settings.database.path)},
        "latency": settings.latency.model_dump(mode="json"),
        "dns": settings.dns.model_dump(mode="json"),
        "http": settings.http.model_dump(mode="json"),
        "outage": settings.outage.model_dump(mode="json"),
        "discovery": settings.discovery.model_dump(mode="json"),
        "wan": settings.wan.model_dump(mode="json"),
        "host_nic": settings.host_nic.model_dump(mode="json"),
        "retention": settings.retention.model_dump(mode="json"),
        "alerts": {
            "enabled": settings.alerts.enabled,
            "interval_seconds": settings.alerts.interval_seconds,
            "cooldown_seconds": settings.alerts.cooldown_seconds,
            "rules": [rule.model_dump(mode="json") for rule in settings.alerts.rules],
            "webhook_configured": settings.alerts.webhook_url is not None,
            "windows_toast": settings.alerts.windows_toast,
        },
        "speedtest": {
            key: value
            for key, value in settings.speedtest.model_dump(mode="json").items()
            if key != "endpoint"
        },
        "eero": {
            "enabled": settings.eero.enabled,
            "interval_seconds": settings.eero.interval_seconds,
            "token_configured": _read_token(settings.eero.token_path) is not None,
        },
    }


def _json_for_script(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":")).replace("<", "\\u003c")


def _loopback_request_allowed(request: Request, enabled: bool) -> bool:
    if not enabled:
        return False
    client = request.client
    if client is None:
        return False
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return client.host.lower() == "localhost"


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if authorization is None:
        return None
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    token = value.strip()
    return token or None


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"detail": "Not authenticated"},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _read_token(path: Path) -> str | None:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None
