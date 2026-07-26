"""Helpers for the Windows Task Scheduler launcher."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DEFAULT_LOG_MAX_BYTES = 10_000_000
DEFAULT_LOG_BACKUPS = 5


def build_service_command(
    python_executable: Path | str,
    working_directory: Path | str,
    log_directory: Path | str,
) -> list[str]:
    """Build the argument vector used by the scheduled task action."""

    return [
        str(python_executable),
        "-m",
        "netmon",
        "service-run",
        "--working-dir",
        str(Path(working_directory)),
        "--log-dir",
        str(Path(log_directory)),
    ]


def rotate_log(
    path: Path,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backups: int = DEFAULT_LOG_BACKUPS,
) -> None:
    """Move a full log through numbered backups, retaining the configured history."""

    if not path.is_file() or path.stat().st_size < max_bytes:
        return
    for index in range(backups, 0, -1):
        source = path if index == 1 else Path(f"{path}.{index - 1}")
        target = Path(f"{path}.{index}")
        if source.exists():
            os.replace(source, target)


def run_service(
    working_directory: Path | str,
    log_directory: Path | str,
    python_executable: Path | str | None = None,
) -> int:
    """Run netmon with rotating stdout/stderr files and return its exit code."""

    working = Path(working_directory)
    logs = Path(log_directory)
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / "netmon.out.log"
    stderr_path = logs / "netmon.err.log"
    rotate_log(stdout_path)
    rotate_log(stderr_path)
    executable = python_executable or sys.executable
    command = [str(executable), "-m", "netmon"]
    with (
        stdout_path.open("a", encoding="utf-8") as stdout,
        stderr_path.open("a", encoding="utf-8") as stderr,
    ):
        result = subprocess.run(command, cwd=working, stdout=stdout, stderr=stderr, check=False)
    return result.returncode
