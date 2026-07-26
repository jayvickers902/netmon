"""Best-effort hostname resolution isolated behind a probe."""

from __future__ import annotations

import asyncio
import socket
import sys
from typing import Protocol


class HostnameProbe(Protocol):
    async def resolve(self, ip: str) -> str | None: ...


class SystemHostnameProbe:
    async def resolve(self, ip: str) -> str | None:
        try:
            name, _, _ = await asyncio.to_thread(socket.gethostbyaddr, ip)
            return name.rstrip(".")
        except OSError:
            pass
        if sys.platform == "win32":
            return await self._netbios(ip)
        return await self._mdns(ip)

    @staticmethod
    async def _netbios(ip: str) -> str | None:
        process = await asyncio.create_subprocess_exec(
            "nbtstat", "-A", ip, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        for line in stdout.decode(errors="replace").splitlines():
            if "<00>" in line and "UNIQUE" in line:
                return line.split()[0]
        return None

    @staticmethod
    async def _mdns(ip: str) -> str | None:
        del ip
        return None


def create_hostname_probe() -> HostnameProbe:
    return SystemHostnameProbe()
