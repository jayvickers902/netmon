"""Online SQLite backup and restore operations."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from contextlib import suppress
from pathlib import Path
from urllib.parse import quote

from .migrations import CURRENT_SCHEMA_VERSION, migrate


class DatabaseBackupError(RuntimeError):
    """The requested backup or restore could not be completed safely."""


class DatabaseBusyError(DatabaseBackupError):
    """A running netmon process owns the database lock."""


class DatabaseRuntimeLock:
    """Process marker used to refuse restore while the application is running."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.lock_path = Path(f"{self.database_path}.lock")
        self._owned = False

    def acquire(self) -> None:
        if str(self.database_path) == ":memory:":
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._lock_owner_is_alive():
                    raise DatabaseBusyError(
                        "database is held by a running netmon process; stop it before restoring"
                    ) from None
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    continue
                continue
            with os.fdopen(descriptor, "w", encoding="ascii") as file:
                file.write(str(os.getpid()))
            self._owned = True
            return

    def release(self) -> None:
        if not self._owned:
            return
        self._owned = False
        with suppress(FileNotFoundError):
            self.lock_path.unlink()

    def _lock_owner_is_alive(self) -> bool:
        try:
            pid = int(self.lock_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def backup_database(database_path: Path | str, destination: Path | str) -> None:
    """Create a consistent live snapshot with SQLite's online VACUUM operation."""

    source = Path(database_path)
    target = Path(destination)
    _require_distinct_paths(source, target)
    if not source.is_file():
        raise DatabaseBackupError(f"database does not exist: {source}")
    if target.exists():
        raise DatabaseBackupError(f"backup destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(source)
        with connection:
            connection.execute("VACUUM INTO ?", (str(target),))
    except sqlite3.DatabaseError as error:
        if connection is not None:
            connection.close()
            connection = None
        _remove_file_if_present(target)
        raise DatabaseBackupError(f"could not create SQLite backup: {error}") from None
    finally:
        if connection is not None:
            connection.close()


def restore_database(database_path: Path | str, source_path: Path | str) -> Path | None:
    """Validate and install a snapshot, returning the retained old database path."""

    live = Path(database_path)
    source = Path(source_path)
    _require_distinct_paths(live, source)
    _validate_database(source)
    lock = DatabaseRuntimeLock(live)
    if lock.lock_path.exists() and lock._lock_owner_is_alive():
        raise DatabaseBusyError(
            "database is held by a running netmon process; stop it before restoring"
        )

    prepared = _prepare_restore(source, live)
    displaced = _next_displaced_path(live) if live.exists() else None
    moved: list[tuple[Path, Path]] = []
    try:
        if displaced is not None:
            moved = _displace_database(live, displaced)
        os.replace(prepared, live)
    except OSError as error:
        _remove_file_if_present(prepared)
        _remove_file_if_present(live)
        for original, retained in reversed(moved):
            os.replace(retained, original)
        raise DatabaseBackupError(f"could not install SQLite restore: {error}") from None
    return displaced


def _validate_database(path: Path) -> int:
    if not path.is_file():
        raise DatabaseBackupError(f"restore source does not exist: {path}")
    uri = f"file:{quote(str(path.resolve()), safe='/:')}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        with connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise DatabaseBackupError(f"restore source failed SQLite integrity_check: {path}")
            rows = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
    except DatabaseBackupError:
        raise
    except (sqlite3.DatabaseError, OSError):
        raise DatabaseBackupError(
            f"restore source is not a valid SQLite database: {path}"
        ) from None
    finally:
        if connection is not None:
            connection.close()
    if not rows:
        raise DatabaseBackupError(f"restore source has no understood schema version: {path}")
    try:
        version = max(int(row[0]) for row in rows)
    except (TypeError, ValueError):
        raise DatabaseBackupError(f"restore source has an invalid schema version: {path}") from None
    if version < 1 or version > CURRENT_SCHEMA_VERSION:
        raise DatabaseBackupError(
            f"restore source schema version {version} is not supported by this build"
        )
    return version


def _prepare_restore(source: Path, live: Path) -> Path:
    live.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{live.name}.restore-", suffix=".db", dir=live.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    connection: sqlite3.Connection | None = None
    try:
        shutil.copy2(source, temporary)
        connection = sqlite3.connect(temporary)
        with connection:
            migrate(connection)
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise DatabaseBackupError("prepared restore failed SQLite integrity_check")
    except (DatabaseBackupError, OSError, sqlite3.DatabaseError) as error:
        if connection is not None:
            connection.close()
            connection = None
        _remove_file_if_present(temporary)
        if isinstance(error, DatabaseBackupError):
            raise
        raise DatabaseBackupError(f"could not prepare SQLite restore: {error}") from None
    finally:
        if connection is not None:
            connection.close()
    return temporary


def _displace_database(live: Path, displaced: Path) -> list[tuple[Path, Path]]:
    moved: list[tuple[Path, Path]] = []
    for suffix in ("", "-wal", "-shm"):
        original = Path(f"{live}{suffix}")
        if not original.exists():
            continue
        retained = Path(f"{displaced}{suffix}")
        os.replace(original, retained)
        moved.append((original, retained))
    return moved


def _next_displaced_path(live: Path) -> Path:
    candidate = Path(f"{live}.pre-restore")
    index = 1
    while candidate.exists() or Path(f"{candidate}-wal").exists():
        candidate = Path(f"{live}.pre-restore.{index}")
        index += 1
    return candidate


def _require_distinct_paths(first: Path, second: Path) -> None:
    if first.absolute() == second.absolute():
        raise DatabaseBackupError("database and backup paths must be different")


def _remove_file_if_present(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()
