from __future__ import annotations

from datetime import UTC, datetime

from netmon.api.app import _default_scheduler
from netmon.collectors.base import FrozenClock
from netmon.config import NetmonSettings
from netmon.db import Store


def _collector_names(tmp_path, enabled: bool) -> list[str]:
    settings = NetmonSettings.model_validate(
        {
            "wan": {"enabled": False},
            "hitron": {"enabled": enabled},
            "host_nic": {"enabled": False},
            "speedtest": {"enabled": False},
            "eero": {"enabled": False},
            "managed_hosts": {"enabled": False},
            "alerts": {"enabled": False},
        }
    )
    scheduler = _default_scheduler(
        settings,
        Store(tmp_path / "wiring.db"),
        FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )
    return [collector.name for collector in scheduler.collectors]


def test_default_scheduler_includes_docsis_when_hitron_enabled(tmp_path) -> None:
    assert "docsis" in _collector_names(tmp_path, True)


def test_default_scheduler_omits_docsis_when_hitron_disabled(tmp_path) -> None:
    assert "docsis" not in _collector_names(tmp_path, False)
