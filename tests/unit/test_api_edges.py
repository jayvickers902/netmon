from __future__ import annotations

from fastapi.testclient import TestClient

from netmon.api.app import create_app
from netmon.db import Store
from netmon.scheduler import Scheduler


def test_api_not_found_and_event_limit_validation(tmp_path):
    app = create_app(store=Store(tmp_path / "api.db"), scheduler=Scheduler([]))
    with TestClient(app) as client:
        assert client.get("/devices/999").status_code == 404
        assert client.get("/events?limit=0").status_code == 422
