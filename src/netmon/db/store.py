"""SQLite store with one short-lived connection per operation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from netmon.config import RetentionConfig
from netmon.models import DeviceObservation, Event, Sample

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
                    "SELECT id FROM metric WHERE kind=? AND target=? AND unit=? AND source=?",
                    (sample.metric_kind, sample.target, sample.unit, sample.source),
                ).fetchone()
                if metric is None:
                    try:
                        cursor = connection.execute(
                            "INSERT INTO metric(kind, target, unit, source) VALUES (?, ?, ?, ?)",
                            (sample.metric_kind, sample.target, sample.unit, sample.source),
                        )
                        metric = (cursor.lastrowid,)
                    except sqlite3.IntegrityError:
                        metric = connection.execute(
                            "SELECT id FROM metric WHERE kind=? AND target=? AND unit=?",
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
        return self.list_filtered_events(limit=limit)

    def record_event(self, event: Event) -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO event(ts, kind, device_id, severity, message, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.ts.timestamp(),
                    event.kind,
                    event.device_id,
                    event.severity,
                    event.message,
                    json.dumps(event.details),
                ),
            )
            assert cursor.lastrowid is not None
            return int(cursor.lastrowid)

    def update_event_details(self, event_id: int, details: dict[str, Any]) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE event SET details_json=? WHERE id=?",
                (json.dumps(details), event_id),
            )

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT id, ts, kind, device_id, severity, message, details_json "
                "FROM event WHERE id=?",
                (event_id,),
            ).fetchone()
            return None if row is None else self._event_dict(row)

    def list_filtered_events(
        self,
        limit: int = 100,
        kind: str | tuple[str, ...] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        severity: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.connection() as connection:
            clauses: list[str] = []
            parameters: list[Any] = []
            if kind is not None:
                if isinstance(kind, tuple):
                    clauses.append(f"kind IN ({','.join('?' for _ in kind)})")
                    parameters.extend(kind)
                else:
                    clauses.append("kind=?")
                    parameters.append(kind)
            if severity is not None:
                clauses.append("severity=?")
                parameters.append(severity)
            if start is not None:
                clauses.append("ts>=?")
                parameters.append(start.timestamp())
            if end is not None:
                clauses.append("ts<=?")
                parameters.append(end.timestamp())
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            parameters.append(limit)
            rows = connection.execute(
                "SELECT id, ts, kind, device_id, severity, message, details_json "
                f"FROM event {where} ORDER BY ts DESC, id DESC LIMIT ?",
                parameters,
            ).fetchall()
            return [self._event_dict(row) for row in rows]

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "ts": row["ts"],
            "kind": row["kind"],
            "device_id": row["device_id"],
            "severity": row["severity"],
            "message": row["message"],
            "details": json.loads(row["details_json"]),
        }

    def list_metrics(
        self,
        kind: str | None = None,
        target: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 10000,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.connection() as connection:
            clauses: list[str] = []
            parameters: list[Any] = []
            if kind is not None:
                clauses.append("metric.kind=?")
                parameters.append(kind)
            if target is not None:
                clauses.append("metric.target=?")
                parameters.append(target)
            if source is not None:
                clauses.append("metric.source=?")
                parameters.append(source)
            if start is not None:
                clauses.append("sample.ts>=?")
                parameters.append(start.timestamp())
            if end is not None:
                clauses.append("sample.ts<=?")
                parameters.append(end.timestamp())
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            parameters.append(limit)
            rows = connection.execute(
                "SELECT metric.kind, metric.target, metric.unit, metric.source, "
                "sample.ts, sample.value "
                "FROM sample JOIN metric ON metric.id=sample.metric_id "
                f"{where} ORDER BY sample.ts ASC LIMIT ?",
                parameters,
            ).fetchall()
            return [
                {
                    "kind": row["kind"],
                    "target": row["target"],
                    "unit": row["unit"],
                    "source": row["source"],
                    "ts": row["ts"],
                    "value": row["value"],
                }
                for row in rows
            ]

    def latest_metric(
        self,
        kind: str,
        target: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any] | None:
        with self.connection() as connection:
            clauses = ["metric.kind=?"]
            parameters: list[Any] = [kind]
            if target is not None:
                clauses.append("metric.target=?")
                parameters.append(target)
            if source is not None:
                clauses.append("metric.source=?")
                parameters.append(source)
            row = connection.execute(
                "SELECT metric.kind, metric.target, metric.unit, metric.source, sample.ts, "
                "sample.value FROM sample JOIN metric ON metric.id=sample.metric_id "
                f"WHERE {' AND '.join(clauses)} ORDER BY sample.ts DESC, sample.id DESC LIMIT 1",
                parameters,
            ).fetchone()
            if row is None:
                return None
            return {
                "kind": row["kind"],
                "target": row["target"],
                "unit": row["unit"],
                "source": row["source"],
                "ts": row["ts"],
                "value": row["value"],
            }

    def list_alerts(
        self,
        limit: int = 100,
        severity: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return self.list_filtered_events(
            limit=limit,
            kind=("alert_triggered", "alert_resolved"),
            severity=severity,
            start=start,
            end=end,
        )

    def run_retention(self, now: datetime, config: RetentionConfig) -> None:
        """Roll up and verify each tier before deleting its source rows."""
        raw_cutoff = (now - timedelta(days=config.raw_days)).timestamp()
        one_minute_cutoff = (now - timedelta(days=config.rollup_1m_days)).timestamp()
        five_minute_cutoff = (now - timedelta(days=config.rollup_5m_days)).timestamp()
        with self.connection() as connection:
            self._rollup_raw(connection, raw_cutoff)
            self._rollup_one_minute(connection, one_minute_cutoff)
            connection.execute(
                "DELETE FROM sample_rollup WHERE bucket_seconds=300 AND ts<?",
                (five_minute_cutoff,),
            )

    @staticmethod
    def _rollup_raw(connection: sqlite3.Connection, cutoff: float) -> None:
        groups = connection.execute(
            "SELECT metric_id, CAST(ts / 60 AS INTEGER) * 60 AS bucket, "
            "AVG(value) AS value, COUNT(*) AS count FROM sample WHERE ts<? "
            "GROUP BY metric_id, bucket",
            (cutoff,),
        ).fetchall()
        for row in groups:
            connection.execute(
                "INSERT INTO sample_rollup(metric_id, bucket_seconds, ts, value, sample_count) "
                "VALUES (?, 60, ?, ?, ?) ON CONFLICT(metric_id, bucket_seconds, ts) DO UPDATE "
                "SET value=excluded.value, sample_count=excluded.sample_count",
                (row["metric_id"], row["bucket"], row["value"], row["count"]),
            )
        for row in groups:
            verified = connection.execute(
                "SELECT value, sample_count FROM sample_rollup "
                "WHERE metric_id=? AND bucket_seconds=60 AND ts=?",
                (row["metric_id"], row["bucket"]),
            ).fetchone()
            if verified is None or verified["sample_count"] != row["count"]:
                raise RuntimeError("raw sample rollup verification failed")
        connection.execute("DELETE FROM sample WHERE ts<?", (cutoff,))

    @staticmethod
    def _rollup_one_minute(connection: sqlite3.Connection, cutoff: float) -> None:
        groups = connection.execute(
            "SELECT metric_id, CAST(ts / 300 AS INTEGER) * 300 AS bucket, "
            "SUM(value * sample_count) / SUM(sample_count) AS value, "
            "SUM(sample_count) AS count FROM sample_rollup "
            "WHERE bucket_seconds=60 AND ts<? GROUP BY metric_id, bucket",
            (cutoff,),
        ).fetchall()
        for row in groups:
            connection.execute(
                "INSERT INTO sample_rollup(metric_id, bucket_seconds, ts, value, sample_count) "
                "VALUES (?, 300, ?, ?, ?) ON CONFLICT(metric_id, bucket_seconds, ts) DO UPDATE "
                "SET value=excluded.value, sample_count=excluded.sample_count",
                (row["metric_id"], row["bucket"], row["value"], row["count"]),
            )
        for row in groups:
            verified = connection.execute(
                "SELECT sample_count FROM sample_rollup "
                "WHERE metric_id=? AND bucket_seconds=300 AND ts=?",
                (row["metric_id"], row["bucket"]),
            ).fetchone()
            if verified is None or verified["sample_count"] != row["count"]:
                raise RuntimeError("one-minute rollup verification failed")
        connection.execute("DELETE FROM sample_rollup WHERE bucket_seconds=60 AND ts<?", (cutoff,))
