"""Forward-only database migrations."""

from __future__ import annotations

import sqlite3
from importlib.resources import files

CURRENT_SCHEMA_VERSION = 3


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY NOT NULL)"
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    newest = max(applied, default=0)
    if newest > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {newest} is newer than supported version "
            f"{CURRENT_SCHEMA_VERSION}"
        )
    if 1 not in applied:
        schema = files("netmon.db").joinpath("schema.sql").read_text(encoding="utf-8")
        connection.executescript(schema)
        connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
    if 2 not in applied:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(metric)").fetchall()}
        if "source" not in columns:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("ALTER TABLE metric RENAME TO metric_old")
            connection.execute("ALTER TABLE sample RENAME TO sample_old")
            connection.execute(
                "CREATE TABLE metric (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, "
                "target TEXT NOT NULL, unit TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'local', "
                "UNIQUE (kind, target, unit, source))"
            )
            connection.execute(
                "INSERT INTO metric(id, kind, target, unit, source) "
                "SELECT id, kind, target, unit, 'local' FROM metric_old"
            )
            connection.execute(
                "CREATE TABLE sample (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "metric_id INTEGER NOT NULL REFERENCES metric(id) ON DELETE CASCADE, "
                "ts REAL NOT NULL, value REAL NOT NULL)"
            )
            connection.execute("INSERT INTO sample SELECT id, metric_id, ts, value FROM sample_old")
            connection.execute("DROP TABLE sample_old")
            connection.execute("DROP TABLE metric_old")
            connection.execute("CREATE INDEX sample_metric_ts ON sample(metric_id, ts)")
            connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS sample_rollup ("
            "metric_id INTEGER NOT NULL REFERENCES metric(id) ON DELETE CASCADE,"
            "bucket_seconds INTEGER NOT NULL, ts REAL NOT NULL, value REAL NOT NULL,"
            "sample_count INTEGER NOT NULL,"
            "PRIMARY KEY (metric_id, bucket_seconds, ts))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS sample_rollup_metric_ts "
            "ON sample_rollup(metric_id, bucket_seconds, ts)"
        )
        connection.execute("INSERT INTO schema_migrations(version) VALUES (2)")
    if 3 not in applied:
        connection.executescript(
            "CREATE TABLE investigation ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "started_at REAL NOT NULL,"
            "ends_at REAL NOT NULL,"
            "ended_at REAL,"
            "label TEXT,"
            "note TEXT,"
            "trigger TEXT NOT NULL,"
            "status TEXT NOT NULL,"
            "verdict_json TEXT"
            ");"
            "CREATE INDEX investigation_started_at ON investigation (started_at DESC);"
            "CREATE UNIQUE INDEX investigation_single_running "
            "ON investigation (status) WHERE status = 'running';"
        )
        connection.execute("INSERT INTO schema_migrations(version) VALUES (3)")
    connection.commit()
