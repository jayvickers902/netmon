from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from netmon.api.app import create_app
from netmon.db import Store
from netmon.models import Event, Sample
from netmon.scheduler import Scheduler


def test_health_store_to_api_includes_metrics_and_traceroute(tmp_path):
    store = Store(tmp_path / "health-api.db")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store.persist_samples([Sample("latency_rtt_ms", "gateway", "ms", now, 2.5)])
    store.record_event(
        Event(now, "outage_start", "error", "Connection degraded", {"traceroute": []})
    )
    scheduler = Scheduler(
        [], status_provider=lambda: {"verdict": "wan_isp_outage", "open": True, "targets": {}}
    )
    app = create_app(store=store, scheduler=scheduler)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/status").json()["verdict"] == "wan_isp_outage"
        metric = client.get("/metrics?kind=latency_rtt_ms").json()[0]
        assert metric["value"] == 2.5
        assert metric["source"] == "local"
        event = client.get("/events?kind=outage_start").json()[0]
        assert event["details"]["traceroute"] == []
        assert client.get(f"/events/{event['id']}").json()["kind"] == "outage_start"


def test_metrics_limit_returns_the_newest_dashboard_window(tmp_path):
    store = Store(tmp_path / "metric-window.db")
    first = datetime(2026, 1, 1, tzinfo=UTC)
    store.persist_samples(
        [
            Sample("device_rate", "pc", "bytes_per_second", first, 1.0),
            Sample("device_rate", "pc", "bytes_per_second", first.replace(second=1), 2.0),
            Sample("device_rate", "pc", "bytes_per_second", first.replace(second=2), 3.0),
        ]
    )
    app = create_app(store=store, scheduler=Scheduler([]))

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        rows = client.get("/metrics?limit=2").json()

    assert [row["value"] for row in rows] == [2.0, 3.0]
