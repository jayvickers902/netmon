"""FastAPI application and lifespan wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from netmon.collectors.base import Clock, RealClock
from netmon.collectors.discovery import DiscoveryCollector
from netmon.config import NetmonSettings
from netmon.db import Store
from netmon.probes.arp import create_arp_probe, create_interface_probe
from netmon.probes.hostname import create_hostname_probe
from netmon.probes.icmp import create_icmp_probe
from netmon.probes.oui import BundledOuiLookup
from netmon.scheduler import Scheduler


def _default_scheduler(settings: NetmonSettings, store: Store, clock: Clock) -> Scheduler:
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
    return Scheduler([discovery], sink=store.persist_samples, clock=clock)


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
    async def lifespan(_: FastAPI):
        await application_scheduler.start()
        try:
            yield
        finally:
            await application_scheduler.stop()

    app = FastAPI(title="netmon", version="0.1.0", lifespan=lifespan)

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
    async def events(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return application_store.list_events(limit)

    dashboard_path = Path(__file__).parents[1] / "web" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return dashboard_path.read_text(encoding="utf-8")

    return app
