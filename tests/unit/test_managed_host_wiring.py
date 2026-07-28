"""Dashboard-safe managed-host configuration contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from netmon.api.app import _dashboard_config, _default_scheduler
from netmon.collectors.base import FrozenClock
from netmon.collectors.managed_host import ManagedHostNicCollector
from netmon.config import NetmonSettings
from netmon.db import Store


def test_dashboard_config_redacts_managed_host_connection_details() -> None:
    settings = NetmonSettings.model_validate(
        {
            "managed_hosts": {
                "enabled": True,
                "interval_seconds": 45,
                "hosts": [
                    {
                        "name": "office-pc",
                        "address": "192.168.4.50",
                        "username": "netmon",
                        "identity_file": "C:/keys/netmon",
                    }
                ],
            }
        }
    )

    assert _dashboard_config(settings)["managed_hosts"] == {
        "enabled": True,
        "interval_seconds": 45.0,
        "hosts": ["office-pc"],
    }


def test_default_scheduler_builds_one_collector_per_managed_host(tmp_path) -> None:
    settings = NetmonSettings.model_validate(
        {
            "wan": {"enabled": False},
            "host_nic": {"enabled": False},
            "alerts": {"enabled": False},
            "managed_hosts": {
                "enabled": True,
                "hosts": [
                    {
                        "name": "office-pc",
                        "address": "192.168.4.50",
                        "username": "netmon",
                        "identity_file": "C:/keys/office",
                    },
                    {
                        "name": "media-pc",
                        "address": "192.168.4.51",
                        "username": "netmon",
                        "identity_file": "C:/keys/media",
                    },
                ],
            },
        }
    )

    scheduler = _default_scheduler(
        settings,
        Store(tmp_path / "wiring.db"),
        FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )
    managed = [
        collector
        for collector in scheduler.collectors
        if isinstance(collector, ManagedHostNicCollector)
    ]

    assert [collector.name for collector in managed] == [
        "managed_host_nic:office-pc",
        "managed_host_nic:media-pc",
    ]
