from __future__ import annotations

from fastapi.testclient import TestClient

from netmon.api.app import create_app
from netmon.config import NetmonSettings
from netmon.db import Store
from netmon.scheduler import Scheduler


def test_api_not_found_and_event_limit_validation(tmp_path):
    app = create_app(store=Store(tmp_path / "api.db"), scheduler=Scheduler([]))
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/devices/999").status_code == 404
        assert client.patch("/devices/999", json={"label": "missing"}).status_code == 404
        assert client.patch("/devices/999", json={}).status_code == 422
        assert client.get("/events/999").status_code == 404
        assert client.get("/events?limit=0").status_code == 422
        assert client.get("/metrics?limit=0").status_code == 422
        assert client.get("/status").json()["verdict"] == "unknown"


def test_default_runtime_wires_v03_collectors(tmp_path):
    app = create_app(
        settings=NetmonSettings(),
        store=Store(tmp_path / "default.db"),
    )
    assert "/status" in app.openapi()["paths"]


def test_dashboard_routes_and_assets_do_not_expose_secrets(tmp_path):
    settings = NetmonSettings(
        database={"path": tmp_path / "dashboard.db"},
        api={"token": "never-render-this-token"},
        alerts={"webhook_url": "https://secret.example/hook"},
    )
    app = create_app(settings=settings, scheduler=Scheduler([]))

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "network evidence console" in dashboard.text
        assert "never-render-this-token" not in dashboard.text
        assert "secret.example" not in dashboard.text
        assert client.get("/timeline").status_code == 200
        assert client.get("/assets/dashboard.css").status_code == 200
        script = client.get("/assets/dashboard.js")
        assert script.status_code == 200
        assert 'method:"PATCH"' in script.text
        assert "data-label-cancel" in script.text
        assert "window.prompt" not in script.text
