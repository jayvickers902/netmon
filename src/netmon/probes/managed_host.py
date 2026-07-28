"""SSH probe for NIC byte counters on a managed Windows host."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeGuard

from netmon.probes.arp import normalize_mac


class ManagedHostProbeError(Exception):
    """A managed-host probe failure whose message contains no remote output or secrets."""


@dataclass(frozen=True, slots=True)
class RemoteNicCounters:
    adapter_name: str
    mac: str
    bytes_sent: int
    bytes_recv: int


class ManagedHostNicProbe(Protocol):
    async def counters(self) -> list[RemoteNicCounters]: ...


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class CommandRunner(Protocol):
    async def run(self, args: tuple[str, ...], timeout_seconds: float) -> CommandResult: ...


class AsyncioCommandRunner:
    """The sole subprocess boundary for managed-host probes."""

    async def run(self, args: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        communicate = asyncio.create_task(process.communicate())
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.shield(communicate), timeout_seconds)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await asyncio.shield(communicate)
            raise
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
            await asyncio.shield(communicate)
            raise
        return CommandResult(process.returncode or 0, stdout, stderr)


_ENCODED_POWERSHELL = (
    "JABhAGQAYQBwAHQAZQByAHMAPQBHAGUAdAAtAE4AZQB0AEEAZABhAHAAdABlAHIAIAAtAFAAaAB5"
    "AHMAaQBjAGEAbAB8AFcAaABlAHIAZQAtAE8AYgBqAGUAYwB0ACAAewAkAF8ALgBTAHQAYQB0AHUAcw"
    "AgAC0AZQBxACAAJwBVAHAAJwB9ADsAQAAoACQAYQBkAGEAcAB0AGUAcgBzAHwARgBvAHIARQBhAGMA"
    "aAAtAE8AYgBqAGUAYwB0ACAAewAkAHMAdABhAHQAcwA9AEcAZQB0AC0ATgBlAHQAQQBkAGEAcAB0AG"
    "UAcgBTAHQAYQB0AGkAcwB0AGkAYwBzACAALQBOAGEAbQBlACAAJABfAC4ATgBhAG0AZQA7AFsAcABz"
    "AGMAdQBzAHQAbwBtAG8AYgBqAGUAYwB0AF0AQAB7AG4AYQBtAGUAPQAkAF8ALgBOAGEAbQBlADsAbQ"
    "BhAGMAPQAkAF8ALgBNAGEAYwBBAGQAZAByAGUAcwBzADsAYgB5AHQAZQBzAF8AcwBlAG4AdAA9ACQA"
    "cwB0AGEAdABzAC4AUwBlAG4AdABCAHkAdABlAHMAOwBiAHkAdABlAHMAXwByAGUAYwB2AD0AJABzAH"
    "QAYQB0AHMALgBSAGUAYwBlAGkAdgBlAGQAQgB5AHQAZQBzAH0AfQApAHwAQwBvAG4AdgBlAHIAdABU"
    "AG8ALQBKAHMAbwBuACAALQBDAG8AbQBwAHIAZQBzAHMA"
)
_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")


class SshWindowsNicProbe:
    def __init__(
        self,
        runner: CommandRunner,
        address: str,
        username: str,
        identity_file: Path,
        port: int = 22,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._runner = runner
        self._address = address
        self._username = username
        self._identity_file = identity_file
        self._port = port
        self._timeout_seconds = timeout_seconds

    async def counters(self) -> list[RemoteNicCounters]:
        try:
            result = await self._runner.run(self._args(), self._timeout_seconds)
        except (TimeoutError, OSError):
            raise ManagedHostProbeError("managed host SSH command unavailable") from None
        except Exception:
            raise ManagedHostProbeError("managed host SSH command failed") from None
        if result.returncode != 0:
            raise ManagedHostProbeError("managed host SSH command failed")
        return _parse_counters(result.stdout)

    def _args(self) -> tuple[str, ...]:
        return (
            "ssh.exe",
            "-4",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-i",
            str(self._identity_file),
            "-p",
            str(self._port),
            f"{self._username}@{self._address}",
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            _ENCODED_POWERSHELL,
        )


def _parse_counters(stdout: bytes) -> list[RemoteNicCounters]:
    try:
        value: object = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ManagedHostProbeError("managed host returned invalid NIC data") from None
    rows = [value] if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ManagedHostProbeError("managed host returned invalid NIC data")
    counters: list[RemoteNicCounters] = []
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ManagedHostProbeError("managed host returned invalid NIC data")
        name = row.get("name")
        mac = row.get("mac")
        sent = row.get("bytes_sent")
        received = row.get("bytes_recv")
        normalized_mac = normalize_mac(mac.strip()) if isinstance(mac, str) else ""
        if (
            not isinstance(name, str)
            or not name
            or name in names
            or not _MAC.fullmatch(normalized_mac)
            or not _counter(sent)
            or not _counter(received)
        ):
            raise ManagedHostProbeError("managed host returned invalid NIC data")
        names.add(name)
        counters.append(RemoteNicCounters(name, normalized_mac, sent, received))
    return counters


def _counter(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
