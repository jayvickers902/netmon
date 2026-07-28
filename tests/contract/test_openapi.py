from __future__ import annotations

import json
from pathlib import Path

from netmon.api.app import create_app
from netmon.db import Store
from netmon.scheduler import Scheduler


def test_openapi_snapshot(tmp_path):
    app = create_app(store=Store(tmp_path / "openapi.db"), scheduler=Scheduler([]))
    schema = app.openapi()
    actual = {
        "openapi": schema["openapi"],
        "title": schema["info"]["title"],
        "paths": {
            path: sorted(method for method in definition if method != "parameters")
            for path, definition in sorted(schema["paths"].items())
        },
        "schemas": {
            "DeviceLabelUpdate": schema["components"]["schemas"]["DeviceLabelUpdate"],
        },
    }
    expected = json.loads((Path(__file__).parent / "openapi_snapshot.json").read_text())
    assert actual == expected
