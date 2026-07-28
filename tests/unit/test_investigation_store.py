from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from netmon.db import Store
from netmon.db.migrations import migrate
from netmon.db.store import ActiveInvestigationError
from netmon.models import DeviceObservation, Event, Sample


def _times() -> tuple[datetime, datetime, datetime]:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    return started, started + timedelta(minutes=15), started + timedelta(minutes=5)


def test_investigation_round_trip(tmp_path):
    store = Store(tmp_path / "investigation.db")
    started, ends, _ = _times()

    investigation = store.create_investigation(started, ends, "upload", "watch it", "manual")

    assert investigation.id == 1
    assert investigation.started_at == started
    assert investigation.ends_at == ends
    assert investigation.ended_at is None
    assert investigation.label == "upload"
    assert investigation.note == "watch it"
    assert investigation.trigger == "manual"
    assert investigation.status == "running"
    assert investigation.verdict is None
    assert store.get_investigation(investigation.id) == investigation
    assert store.active_investigation(ends + timedelta(days=1)) == investigation


def test_only_one_running_investigation_and_completion_frees_slot(tmp_path):
    store = Store(tmp_path / "investigation.db")
    started, ends, ended = _times()
    first = store.create_investigation(started, ends, None, None, "auto_arm")

    with pytest.raises(ActiveInvestigationError):
        store.create_investigation(started + timedelta(seconds=1), ends, None, None, "manual")

    completed = store.complete_investigation(first.id, ended, {"verdict": "healthy"})
    assert completed is not None
    second = store.create_investigation(ended, ends + timedelta(minutes=15), None, None, "manual")
    assert second.status == "running"


def test_complete_preserves_verdict_and_missing_ids_return_none(tmp_path):
    store = Store(tmp_path / "investigation.db")
    started, ends, ended = _times()
    investigation = store.create_investigation(started, ends, None, None, "manual")
    verdict = {"confidence": "high", "reasons": ["gateway clean"], "coverage": {"x": 1}}

    completed = store.complete_investigation(investigation.id, ended, verdict)

    assert completed is not None
    assert completed.status == "complete"
    assert completed.ended_at == ended
    assert completed.verdict == verdict
    assert store.active_investigation(ended) is None
    assert store.get_investigation(999) is None
    assert store.complete_investigation(999, ended, verdict) is None
    assert store.cancel_investigation(999, ended) is None


def test_cancel_sets_end_and_leaves_verdict_empty(tmp_path):
    store = Store(tmp_path / "investigation.db")
    started, ends, ended = _times()
    investigation = store.create_investigation(started, ends, "test", None, "manual")

    cancelled = store.cancel_investigation(investigation.id, ended)

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.ended_at == ended
    assert cancelled.verdict is None


def test_list_is_newest_first_and_respects_limit(tmp_path):
    store = Store(tmp_path / "investigation.db")
    first, first_end, _ = _times()
    store.create_investigation(first, first_end, "first", None, "manual")
    store.cancel_investigation(1, first + timedelta(minutes=1))
    second = first + timedelta(minutes=1)
    store.create_investigation(second, second + timedelta(minutes=15), "second", None, "manual")
    store.cancel_investigation(2, second + timedelta(minutes=1))

    rows = store.list_investigations(limit=1)

    assert [row.label for row in rows] == ["second"]
    assert [row.label for row in store.list_investigations()] == ["second", "first"]


def test_v3_migration_preserves_existing_rows(tmp_path):
    path = tmp_path / "history.db"
    store = Store(path)
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    store.persist_samples([Sample("latency", "gateway", "ms", timestamp, 4.5)])
    store.record_event(Event(timestamp, "outage_start", "error", "WAN outage", {}))
    store.record_discovery(
        [DeviceObservation("AA:BB:CC:00:00:01", "192.168.1.10", "Vendor", "lamp")],
        timestamp,
        offline_after_misses=2,
    )
    with store.connection() as connection:
        sample_count = connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
        event_count = connection.execute("SELECT COUNT(*) FROM event").fetchone()[0]
        device_count = connection.execute("SELECT COUNT(*) FROM device").fetchone()[0]

    with sqlite3.connect(path) as connection:
        migrate(connection)
        assert connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == sample_count
        assert connection.execute("SELECT COUNT(*) FROM event").fetchone()[0] == event_count
        assert connection.execute("SELECT COUNT(*) FROM device").fetchone()[0] == device_count
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='investigation'"
        ).fetchone() == ("investigation",)
