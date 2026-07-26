"""SQLite store with one short-lived connection per operation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from netmon.models import DeviceObservation, Sample

from .migrations import migrate


def _stamp(value: datetime) -> str:
    return value.isoformat()


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._memory_connection: sqlite3.Connection | None = None
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            migrate(connection)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        is_memory = str(self.path) == ":memory:"
        if is_memory:
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
                self._memory_connection.execute("PRAGMA foreign_keys=ON")
            connection = self._memory_connection
        else:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            if not is_memory:
                connection.close()

    def persist_samples(self, samples: Iterable[Sample]) -> None:
        with self.connection() as connection:
            for sample in samples:
                metric = connection.execute(
                    "INSERT INTO metric(kind, target, unit) VALUES (?, ?, ?) "
                    "ON CONFLICT(kind, target, unit) DO UPDATE SET kind=excluded.kind "
                    "RETURNING id",
                    (sample.metric_kind, sample.target, sample.unit),
                ).fetchone()
                assert metric is not None
                connection.execute(
                    "INSERT INTO sample(metric_id, ts, value) VALUES (?, ?, ?)",
                    (metric[0], sample.ts.timestamp(), sample.value),
                )

    def record_discovery(
        self,
        observations: Iterable[DeviceObservation],
        now: datetime,
        offline_after_misses: int,
    ) -> None:
        observations_list = list(observations)
        seen_macs = {item.mac.lower() for item in observations_list}
        stamp = _stamp(now)
        with self.connection() as connection:
            for observation in observations_list:
                mac = observation.mac.lower()
                row = connection.execute("SELECT * FROM device WHERE mac = ?", (mac,)).fetchone()
                if row is None:
                    cursor = connection.execute(
                        "INSERT INTO device(mac, first_seen, last_seen, vendor, hostname) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (mac, stamp, stamp, observation.vendor, observation.hostname),
                    )
                    assert cursor.lastrowid is not None
                    device_id = cursor.lastrowid
                    self._event(connection, now, "device_join", device_id, "info", "Device joined")
                else:
                    device_id = int(row["id"])
                    if not bool(row["is_online"]):
                        self._event(
                            connection, now, "device_join", device_id, "info", "Device returned"
                        )
                    connection.execute(
                        "UPDATE device SET last_seen=?, vendor=COALESCE(?, vendor), "
                        "hostname=COALESCE(?, hostname), missed_sweeps=0, is_online=1 WHERE id=?",
                        (stamp, observation.vendor, observation.hostname, device_id),
                    )
                connection.execute(
                    "INSERT INTO device_ip(device_id, ip, last_seen) VALUES (?, ?, ?) "
                    "ON CONFLICT(device_id, ip) DO UPDATE SET last_seen=excluded.last_seen",
                    (device_id, observation.ip, stamp),
                )

            tracked = connection.execute(
                "SELECT id, mac, missed_sweeps, is_online FROM device"
            ).fetchall()
            for row in tracked:
                if row["mac"] in seen_macs or not bool(row["is_online"]):
                    continue
                misses = int(row["missed_sweeps"]) + 1
                now_offline = misses >= offline_after_misses
                connection.execute(
                    "UPDATE device SET missed_sweeps=?, is_online=? WHERE id=?",
                    (misses, int(not now_offline), row["id"]),
                )
                if now_offline:
                    self._event(
                        connection,
                        now,
                        "device_depart",
                        int(row["id"]),
                        "info",
                        "Device went offline",
                        {"missed_sweeps": misses},
                    )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        now: datetime,
        kind: str,
        device_id: int,
        severity: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO event(ts, kind, device_id, severity, message, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now.timestamp(), kind, device_id, severity, message, json.dumps(details or {})),
        )

    def list_devices(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM device ORDER BY last_seen DESC").fetchall()
            result = []
            for row in rows:
                result.append(self._device_dict(connection, row))
            return result

    def get_device(self, device_id: int) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM device WHERE id=?", (device_id,)).fetchone()
            return None if row is None else self._device_dict(connection, row)

    @staticmethod
    def _device_dict(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        ips = connection.execute(
            "SELECT ip, last_seen FROM device_ip WHERE device_id=? ORDER BY last_seen DESC",
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "mac": row["mac"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "vendor": row["vendor"],
            "hostname": row["hostname"],
            "label": row["label"],
            "is_tracked": bool(row["is_tracked"]),
            "online": bool(row["is_online"]),
            "missed_sweeps": row["missed_sweeps"],
            "ips": [dict(item) for item in ips],
        }

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT id, ts, kind, device_id, severity, message, details_json "
                "FROM event ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "ts": row["ts"],
                    "kind": row["kind"],
                    "device_id": row["device_id"],
                    "severity": row["severity"],
                    "message": row["message"],
                    "details": json.loads(row["details_json"]),
                }
                for row in rows
            ]
