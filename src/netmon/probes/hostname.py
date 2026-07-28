"""Best-effort hostname resolution isolated behind a probe."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
from typing import Protocol


class HostnameProbe(Protocol):
    async def resolve(self, ip: str) -> str | None: ...


def _is_windows() -> bool:
    return sys.platform == "win32"


class SystemHostnameProbe:
    async def resolve(self, ip: str) -> str | None:
        try:
            resolved_name, _, _ = await asyncio.to_thread(socket.gethostbyaddr, ip)
            return resolved_name.rstrip(".")
        except OSError:
            pass
        name = await self._netbios(ip)
        return name if name is not None else await self._mdns(ip)

    @staticmethod
    async def _netbios(ip: str) -> str | None:
        _, stdout = await SystemHostnameProbe._run_command("nbtstat", "-A", ip)
        for line in stdout.decode(errors="replace").splitlines():
            if "<00>" in line and "UNIQUE" in line:
                return line.split()[0]
        return None

    @staticmethod
    async def _mdns(ip: str) -> str | None:
        returncode, stdout = await SystemHostnameProbe._run_command("avahi-resolve-address", ip)
        if returncode == 0:
            parts = stdout.decode(errors="replace").split()
            if parts:
                return parts[-1].rstrip(".")
        return None

    @staticmethod
    async def _run_command(*command: str) -> tuple[int, bytes]:
        if _is_windows():
            # Proactor pipe transports retain two 64 KiB read buffers per process
            # until cyclic GC. A blocking child inside the worker thread releases
            # its pipes immediately without blocking the event loop.
            try:
                completed = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except OSError:
                return -1, b""
            return completed.returncode, completed.stdout
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            return -1, b""
        stdout, _ = await process.communicate()
        return process.returncode if process.returncode is not None else -1, stdout


def create_hostname_probe() -> HostnameProbe:
    return SystemHostnameProbe()
