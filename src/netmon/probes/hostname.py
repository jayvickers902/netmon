"""Best-effort hostname resolution isolated behind a probe."""

from __future__ import annotations

import asyncio
import socket
from typing import Protocol


class HostnameProbe(Protocol):
    async def resolve(self, ip: str) -> str | None: ...


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
        try:
            process = await asyncio.create_subprocess_exec(
                "nbtstat", "-A", ip, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except OSError:
            return None
        stdout, _ = await process.communicate()
        for line in stdout.decode(errors="replace").splitlines():
            if "<00>" in line and "UNIQUE" in line:
                return line.split()[0]
        return None

    @staticmethod
    async def _mdns(ip: str) -> str | None:
        try:
            process = await asyncio.create_subprocess_exec(
                "avahi-resolve-address",
                ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            return None
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            parts = stdout.decode(errors="replace").split()
            if parts:
                return parts[-1].rstrip(".")
        return None


def create_hostname_probe() -> HostnameProbe:
    return SystemHostnameProbe()
