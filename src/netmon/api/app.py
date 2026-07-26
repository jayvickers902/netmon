"""FastAPI application and lifespan wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from netmon.collectors.base import Clock, RealClock
from netmon.collectors.discovery import DiscoveryCollector
from netmon.collectors.dns_probe import DnsCollector
from netmon.collectors.http_probe import HttpCollector
from netmon.collectors.latency import LatencyCollector
from netmon.collectors.wan_status import WanStatusCollector
from netmon.config import NetmonSettings
from netmon.db import Store
from netmon.outage import OutageCoordinator, OutageStateMachine
from netmon.probes.arp import create_arp_probe, create_interface_probe
from netmon.probes.dns import ResolverDnsProbe
from netmon.probes.gateway import create_gateway_probe
from netmon.probes.hostname import create_hostname_probe
from netmon.probes.http import HttpxProbe
from netmon.probes.icmp import create_icmp_probe
from netmon.probes.oui import BundledOuiLookup
from netmon.probes.traceroute import SystemTracerouteProbe
from netmon.probes.upnp import UpnpProbe
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
    return Scheduler(
        [discovery, latency, dns, http, wan]
        if settings.wan.enabled
        else [discovery, latency, dns, http],
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

    app = FastAPI(title="netmon", version="0.3.0", lifespan=lifespan)

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
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return application_store.list_filtered_events(limit, kind, start, end)

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
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = Query(default=10000, ge=1, le=100000),
    ) -> list[dict[str, Any]]:
        return application_store.list_metrics(kind, target, start, end, limit)

    @app.get("/status")
    async def status() -> dict[str, object]:
        return application_scheduler.current_status()

    dashboard_path = Path(__file__).parents[1] / "web" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return dashboard_path.read_text(encoding="utf-8")

    return app
