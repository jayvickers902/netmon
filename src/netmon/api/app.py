"""FastAPI application and lifespan wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

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
from netmon.db import Store
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


def _default_scheduler(settings: NetmonSettings, store: Store, clock: Clock) -> Scheduler:
    gateway = create_gateway_probe()
    machine = OutageStateMachine(
        failures_to_open=settings.outage.failures_to_open,
        successes_to_close=settings.outage.successes_to_close,
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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await application_scheduler.start()
        try:
            yield
        finally:
            await application_scheduler.stop()

    app = FastAPI(title="netmon", version="0.5.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "collectors": application_scheduler.health()}

    @app.get("/devices")
    async def devices() -> list[dict[str, Any]]:
        return application_store.list_devices()

    @app.get("/devices/{device_id}")
    async def device(device_id: int) -> dict[str, Any]:
        result = application_store.get_device(device_id)
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

    dashboard_path = Path(__file__).parents[1] / "web" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return dashboard_path.read_text(encoding="utf-8")

    return app


def _read_token(path: Path) -> str | None:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None
