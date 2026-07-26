from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from netmon.db import (
    DatabaseBackupError,
    DatabaseBusyError,
    DatabaseRuntimeLock,
    Store,
    backup_database,
    restore_database,
)
from netmon.db.backup import process_is_alive
from netmon.models import Event, Sample


@pytest.fixture
def dead_pid():
    """A PID that is definitely not running.

    The Popen object is kept alive deliberately: while it holds the process
    handle open Windows cannot recycle the PID, so the answer stays stable.
    """
    process = subprocess.Popen([sys.executable, "-c", ""])
    process.wait()
    yield process.pid


def test_liveness_probe_answers_without_signalling_the_process(dead_pid):
    # os.kill(pid, 0) would be the POSIX idiom here. On Windows signal 0 is
    # CTRL_C_EVENT, so it delivers a Ctrl-C to our own console process group and
    # never reports a dead PID. Both halves have to be right.
    assert process_is_alive(os.getpid()) is True
    assert process_is_alive(dead_pid) is False


def test_a_lock_left_by_a_crashed_run_does_not_block_restore(tmp_path, dead_pid):
    database = tmp_path / "netmon.db"
    backup = tmp_path / "backup.db"
    Store(database)
    backup_database(database, backup)
    stale = DatabaseRuntimeLock(database)
    stale.lock_path.write_text(str(dead_pid), encoding="ascii")

    # Before the liveness fix this raised DatabaseBusyError: a dead PID read as
    # alive on Windows, so a lock left behind by a crash blocked restore forever.
    restore_database(database, backup)

    # And the stale file is reclaimed rather than fenced around.
    stale.acquire()
    try:
        assert stale.lock_path.read_text(encoding="ascii") == str(os.getpid())
    finally:
        stale.release()


def test_backup_and_restore_round_trip_retains_samples_events_and_old_database(tmp_path):
    database = tmp_path / "netmon.db"
    backup = tmp_path / "backup.db"
    original_time = datetime(2026, 1, 1, tzinfo=UTC)
    changed_time = datetime(2026, 1, 2, tzinfo=UTC)
    store = Store(database)
    store.persist_samples([Sample("latency", "gateway", "ms", original_time, 4.5)])
    store.record_event(Event(original_time, "outage_end", "info", "Recovered", {}))

    backup_database(database, backup)
    store.persist_samples([Sample("latency", "gateway", "ms", changed_time, 9.5)])
    displaced = restore_database(database, backup)

    assert displaced == tmp_path / "netmon.db.pre-restore"
    restored = Store(database)
    assert [item["value"] for item in restored.list_metrics()] == [4.5]
    assert restored.list_events()[0]["message"] == "Recovered"
    retained = Store(displaced)
    assert [item["value"] for item in retained.list_metrics()] == [4.5, 9.5]


def test_corrupt_restore_source_is_refused_without_displacing_live_database(tmp_path):
    database = tmp_path / "netmon.db"
    corrupt = tmp_path / "corrupt.db"
    Store(database).persist_samples(
        [Sample("latency", "gateway", "ms", datetime(2026, 1, 1, tzinfo=UTC), 4.5)]
    )
    corrupt.write_bytes(b"not sqlite")

    with pytest.raises(DatabaseBackupError, match="valid SQLite"):
        restore_database(database, corrupt)
    assert Store(database).list_metrics()[0]["value"] == 4.5
    assert not (tmp_path / "netmon.db.pre-restore").exists()


def test_newer_schema_restore_is_refused(tmp_path):
    database = tmp_path / "netmon.db"
    newer = tmp_path / "newer.db"
    Store(database)
    connection = sqlite3.connect(newer)
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY NOT NULL)")
    connection.execute("INSERT INTO schema_migrations VALUES (99)")
    connection.commit()
    connection.close()

    with pytest.raises(DatabaseBackupError, match="not supported"):
        restore_database(database, newer)
    assert not (tmp_path / "netmon.db.pre-restore").exists()


def test_restore_refuses_a_source_without_an_understood_schema(tmp_path):
    database = tmp_path / "netmon.db"
    empty = tmp_path / "empty.db"
    Store(database)
    connection = sqlite3.connect(empty)
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY NOT NULL)")
    connection.commit()
    connection.close()

    with pytest.raises(DatabaseBackupError, match="no understood schema version"):
        restore_database(database, empty)
    assert not (tmp_path / "netmon.db.pre-restore").exists()


def test_missing_and_overlapping_paths_are_refused(tmp_path):
    database = tmp_path / "netmon.db"
    backup = tmp_path / "backup.db"
    Store(database)

    with pytest.raises(DatabaseBackupError, match="does not exist"):
        restore_database(database, tmp_path / "absent.db")
    with pytest.raises(DatabaseBackupError, match="must be different"):
        backup_database(database, database)
    with pytest.raises(DatabaseBackupError, match="database does not exist"):
        backup_database(tmp_path / "absent.db", backup)

    backup_database(database, backup)
    with pytest.raises(DatabaseBackupError, match="already exists"):
        backup_database(database, backup)


def test_restore_refuses_a_running_netmon_database(tmp_path):
    database = tmp_path / "netmon.db"
    backup = tmp_path / "backup.db"
    Store(database)
    backup_database(database, backup)
    lock = DatabaseRuntimeLock(database)
    lock.acquire()
    try:
        with pytest.raises(DatabaseBusyError, match="running netmon"):
            restore_database(database, backup)
    finally:
        lock.release()
