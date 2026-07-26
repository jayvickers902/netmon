from __future__ import annotations

from datetime import UTC, datetime, timedelta

from netmon.db import Store
from netmon.models import DeviceObservation, Sample


def test_store_migrates_wal_and_persists_samples(tmp_path):
    store = Store(tmp_path / "netmon.db")
    with store.connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("SELECT version FROM schema_migrations").fetchone()[0] == 1
    store.persist_samples(
        [Sample("latency", "gateway", "ms", datetime(2026, 1, 1, tzinfo=UTC), 4.5)]
    )
    with store.connection() as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == 4.5


def test_identity_follows_mac_and_offline_transition(tmp_path):
    store = Store(tmp_path / "netmon.db")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    observation = DeviceObservation("AA:BB:CC:00:00:01", "192.168.1.10", "Vendor", "lamp")
    store.record_discovery([observation], now, offline_after_misses=2)
    store.record_discovery(
        [DeviceObservation(observation.mac, "192.168.1.11", "Vendor", "lamp")],
        now + timedelta(seconds=1),
        offline_after_misses=2,
    )
    devices = store.list_devices()
    assert len(devices) == 1
    assert {item["ip"] for item in devices[0]["ips"]} == {"192.168.1.10", "192.168.1.11"}
    store.record_discovery([], now + timedelta(seconds=2), offline_after_misses=2)
    assert store.list_devices()[0]["online"] is True
    store.record_discovery([], now + timedelta(seconds=3), offline_after_misses=2)
    assert store.list_devices()[0]["online"] is False
    assert store.list_events(limit=10)[0]["kind"] == "device_depart"
