from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from netmon.db import Store
from netmon.models import DeviceObservation, Event, Sample
from netmon.probes.eero_cloud import EeroDevice


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


def test_health_events_metrics_and_filters(tmp_path):
    store = Store(tmp_path / "health.db")
    first = datetime(2026, 1, 1, tzinfo=UTC)
    event_id = store.record_event(
        Event(first, "outage_start", "error", "WAN outage", {"traceroute": [{"index": 1}]})
    )
    store.persist_samples([Sample("latency_rtt_ms", "1.1.1.1", "ms", first, 12.0)])
    store.update_event_details(event_id, {"traceroute": [{"index": 2}]})
    assert store.get_event(event_id)["details"]["traceroute"][0]["index"] == 2
    assert store.list_filtered_events(kind="outage_start", start=first)[0]["id"] == event_id
    assert store.list_metrics(kind="latency_rtt_ms", target="1.1.1.1")[0]["value"] == 12.0


def test_metric_limit_returns_newest_window_in_chronological_order(tmp_path):
    store = Store(tmp_path / "metrics.db")
    first = datetime(2026, 1, 1, tzinfo=UTC)
    store.persist_samples(
        [
            Sample("x", "target", "count", first + timedelta(seconds=index), float(index))
            for index in range(5)
        ]
    )

    assert [row["value"] for row in store.list_metrics(limit=3)] == [2.0, 3.0, 4.0]


def test_metric_limit_is_applied_after_filters(tmp_path):
    store = Store(tmp_path / "filtered-metrics.db")
    first = datetime(2026, 1, 1, tzinfo=UTC)
    store.persist_samples(
        [
            Sample("x", "wanted", "count", first, 1.0, "remote"),
            Sample("x", "other", "count", first + timedelta(seconds=1), 2.0, "remote"),
            Sample("x", "wanted", "count", first + timedelta(seconds=2), 3.0, "remote"),
            Sample("x", "wanted", "count", first + timedelta(seconds=3), 4.0, "remote"),
        ]
    )

    rows = store.list_metrics(kind="x", target="wanted", source="remote", limit=2)
    assert [row["value"] for row in rows] == [3.0, 4.0]


def test_record_eero_devices_updates_known_macs_and_leaves_unknown_ones_alone(tmp_path):
    store = Store(tmp_path / "eero.db")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store.record_discovery(
        [DeviceObservation("AA:BB:CC:00:00:01", "192.168.1.10", "Vendor", "lamp")],
        now,
        offline_after_misses=2,
    )

    store.record_eero_devices(
        [
            EeroDevice(
                "aa:bb:cc:00:00:01",
                name="Living Room Lamp",
                connection_type="wireless",
                node="Living Room eero",
            ),
            EeroDevice("aa:bb:cc:99:99:99", name="Ghost Device"),
        ]
    )

    devices = store.list_devices()
    assert len(devices) == 1  # the unknown MAC did not create a row
    known = devices[0]
    assert known["eero_name"] == "Living Room Lamp"
    assert known["connection_type"] == "wireless"
    assert known["eero_node"] == "Living Room eero"


def test_record_eero_devices_coalesces_missing_fields_instead_of_erasing_them(tmp_path):
    store = Store(tmp_path / "eero-coalesce.db")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store.record_discovery(
        [DeviceObservation("AA:BB:CC:00:00:02", "192.168.1.11", "Vendor", "desk")],
        now,
        offline_after_misses=2,
    )
    store.record_eero_devices([EeroDevice("aa:bb:cc:00:00:02", name="Desk PC")])

    # A later cycle that saw the device but not its name must not blank the stored name.
    store.record_eero_devices([EeroDevice("aa:bb:cc:00:00:02", connection_type="wired")])

    device = store.list_devices()[0]
    assert device["eero_name"] == "Desk PC"
    assert device["connection_type"] == "wired"


def test_forward_migration_adds_eero_columns_without_losing_devices(tmp_path):
    path = tmp_path / "pre-eero.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY NOT NULL);"
        "INSERT INTO schema_migrations VALUES (1), (2), (3);"
        "CREATE TABLE device(id INTEGER PRIMARY KEY AUTOINCREMENT, mac TEXT NOT NULL UNIQUE, "
        "first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, vendor TEXT, hostname TEXT, "
        "label TEXT, is_tracked INTEGER NOT NULL DEFAULT 1, missed_sweeps INTEGER NOT NULL "
        "DEFAULT 0, is_online INTEGER NOT NULL DEFAULT 1);"
        "CREATE TABLE device_ip(device_id INTEGER NOT NULL, ip TEXT NOT NULL, "
        "last_seen TEXT NOT NULL, PRIMARY KEY (device_id, ip));"
        "INSERT INTO device(mac, first_seen, last_seen) VALUES ('aa:bb:cc:00:00:03', 'x', 'x');"
    )
    connection.commit()
    connection.close()

    store = Store(path)

    columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(device)")}
    assert {"eero_name", "connection_type", "eero_node"} <= columns
    devices = store.list_devices()
    assert len(devices) == 1
    assert devices[0]["mac"] == "aa:bb:cc:00:00:03"
    assert devices[0]["eero_name"] is None


def test_forward_migration_adds_metric_source_without_losing_samples(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY NOT NULL);"
        "INSERT INTO schema_migrations VALUES (1);"
        "CREATE TABLE metric(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, "
        "target TEXT NOT NULL, unit TEXT NOT NULL, UNIQUE(kind,target,unit));"
        "CREATE TABLE sample(id INTEGER PRIMARY KEY AUTOINCREMENT, metric_id INTEGER NOT NULL, "
        "ts REAL NOT NULL, value REAL NOT NULL);"
        "INSERT INTO metric VALUES (1, 'x', 'target', 'count');"
        "INSERT INTO sample VALUES (1, 1, 1, 2);"
    )
    connection.commit()
    connection.close()
    store = Store(path)
    assert store.list_metrics(kind="x")[0]["source"] == "local"
    store.persist_samples([Sample("x", "target", "count", datetime.now(UTC), 3, "cloud")])
    assert len(store.list_metrics(kind="x")) == 2
