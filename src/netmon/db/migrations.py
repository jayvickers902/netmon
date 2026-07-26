"""Forward-only database migrations."""

from __future__ import annotations

import sqlite3
from importlib.resources import files


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY NOT NULL)"
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    if 1 not in applied:
        schema = files("netmon.db").joinpath("schema.sql").read_text(encoding="utf-8")
        connection.executescript(schema)
        connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
    connection.commit()
