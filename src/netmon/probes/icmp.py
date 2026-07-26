"""Unelevated ICMP probes for Windows and POSIX systems."""

from __future__ import annotations

import asyncio
import ctypes
import ipaddress
import re
import sys
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IcmpResult:
    reachable: bool
    latency_ms: float | None


class IcmpProbe(Protocol):
    async def ping(self, ip: str, timeout_ms: int) -> IcmpResult: ...


class WindowsIcmpProbe:
    """Call iphlpapi instead of raw sockets, so normal users can run netmon."""

    async def ping(self, ip: str, timeout_ms: int) -> IcmpResult:
        return await asyncio.to_thread(self._ping_sync, ip, timeout_ms)

    @staticmethod
    def _ping_sync(ip: str, timeout_ms: int) -> IcmpResult:
        class EchoReply(ctypes.Structure):
            _fields_ = [
                ("address", ctypes.c_uint32),
                ("status", ctypes.c_uint32),
                ("round_trip_time", ctypes.c_uint32),
                ("data_size", ctypes.c_uint16),
                ("reserved", ctypes.c_uint16),
                ("data", ctypes.c_void_p),
                ("options", ctypes.c_void_p),
            ]

        try:
            address = ipaddress.IPv4Address(ip)
            iphlpapi = ctypes.WinDLL("iphlpapi.dll")
            handle = iphlpapi.IcmpCreateFile()
            if handle == ctypes.c_void_p(-1).value:
                return IcmpResult(False, None)
            try:
                payload = b"netmon"
                reply = EchoReply()
                destination = int.from_bytes(address.packed, "little")
                result = iphlpapi.IcmpSendEcho(
                    handle,
                    destination,
                    payload,
                    len(payload),
                    None,
                    ctypes.byref(reply),
                    ctypes.sizeof(reply),
                    timeout_ms,
                )
                if result == 0 or reply.status != 0:
                    return IcmpResult(False, None)
                return IcmpResult(True, float(reply.round_trip_time))
            finally:
                iphlpapi.IcmpCloseHandle(handle)
        except (OSError, ValueError, ctypes.ArgumentError):
            return IcmpResult(False, None)


class PosixIcmpProbe:
    """Use the platform ping executable; it requires no special privileges."""

    async def ping(self, ip: str, timeout_ms: int) -> IcmpResult:
        timeout_seconds = max(1, (timeout_ms + 999) // 1000)
        process = await asyncio.create_subprocess_exec(
            "ping",
            "-c",
            "1",
            "-W",
            str(timeout_seconds),
            ip,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return IcmpResult(False, None)
        match = re.search(rb"time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms", stdout)
        return IcmpResult(True, float(match.group(1)) if match else None)


def create_icmp_probe() -> IcmpProbe:
    return WindowsIcmpProbe() if sys.platform == "win32" else PosixIcmpProbe()
