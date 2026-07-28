from __future__ import annotations

from fastapi.testclient import TestClient

from netmon.api.app import create_app
from netmon.config import InvestigationConfig, NetmonSettings
from netmon.db import Store
from netmon.scheduler import Scheduler


def test_investigation_lifecycle_and_limits(tmp_path, frozen_clock) -> None:
    settings = NetmonSettings(
        investigation=InvestigationConfig(default_duration_seconds=30, max_duration_seconds=60)
    )
    app = create_app(
        settings=settings,
        store=Store(tmp_path / "investigation.db"),
        scheduler=Scheduler([]),
        clock=frozen_clock,
    )
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        started = client.post("/investigations", json={"label": "test"})
        assert started.status_code == 201
        investigation_id = started.json()["id"]
        assert client.get("/investigations/active").json()["id"] == investigation_id
        assert client.post("/investigations", json={}).status_code == 409
        assert client.post("/investigations", json={"duration_seconds": 61}).status_code == 422
        stopped = client.post(f"/investigations/{investigation_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "complete"
        assert client.get(f"/investigations/{investigation_id}/report").status_code == 200
        assert client.get("/investigations/9999").status_code == 404
