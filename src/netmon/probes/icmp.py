"""Unelevated ICMP probes for Windows and POSIX systems."""

from __future__ import annotations

import asyncio
import ctypes
import ipaddress
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

if sys.platform == "win32":
    from ctypes import wintypes


@dataclass(frozen=True, slots=True)
class IcmpResult:
    reachable: bool
    latency_ms: float | None


class IcmpProbe(Protocol):
    async def ping(self, ip: str, timeout_ms: int) -> IcmpResult: ...


class EchoReply(ctypes.Structure):
    """`ICMP_ECHO_REPLY` from ipexport.h."""

    _fields_ = (
        ("address", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("round_trip_time", ctypes.c_uint32),
        ("data_size", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16),
        ("data", ctypes.c_void_p),
        ("options", ctypes.c_void_p),
    )


_ICMP_PAYLOAD = b"netmon"
_INVALID_HANDLE = (0, None, 2**64 - 1, 2**32 - 1)


@lru_cache(maxsize=1)
def _iphlpapi() -> ctypes.WinDLL:
    """Load iphlpapi once with explicit signatures.

    Declaring these is not optional. `IcmpCreateFile` returns a 64-bit HANDLE, and
    ctypes defaults an undeclared restype to 32-bit `c_int`, which silently truncates
    it. The truncated value is not `INVALID_HANDLE_VALUE`, so it passes a naive
    validity check and every subsequent `IcmpSendEcho` fails against a garbage handle
    — making the whole host look unreachable while the network is perfectly healthy.
    """
    library = ctypes.WinDLL("iphlpapi.dll")
    library.IcmpCreateFile.argtypes = ()
    library.IcmpCreateFile.restype = wintypes.HANDLE
    library.IcmpCloseHandle.argtypes = (wintypes.HANDLE,)
    library.IcmpCloseHandle.restype = wintypes.BOOL
    library.IcmpSendEcho.argtypes = (
        wintypes.HANDLE,  # IcmpHandle
        ctypes.c_uint32,  # DestinationAddress (network byte order)
        ctypes.c_void_p,  # RequestData
        ctypes.c_uint16,  # RequestSize
        ctypes.c_void_p,  # RequestOptions
        ctypes.c_void_p,  # ReplyBuffer
        ctypes.c_ulong,  # ReplySize
        ctypes.c_ulong,  # Timeout
    )
    library.IcmpSendEcho.restype = ctypes.c_ulong
    return library


class WindowsIcmpProbe:
    """Call iphlpapi instead of raw sockets, so normal users can run netmon."""

    async def ping(self, ip: str, timeout_ms: int) -> IcmpResult:
        return await asyncio.to_thread(self._ping_sync, ip, timeout_ms)

    @staticmethod
    def _ping_sync(ip: str, timeout_ms: int) -> IcmpResult:
        try:
            address = ipaddress.IPv4Address(ip)
            iphlpapi = _iphlpapi()
            handle = iphlpapi.IcmpCreateFile()
            if handle in _INVALID_HANDLE:
                return IcmpResult(False, None)
            try:
                # The reply buffer must hold the struct *plus* the echoed payload;
                # the trailing 8 bytes leave room for an ICMP error message, per
                # the IcmpSendEcho contract. Sizing it to the struct alone fails
                # with ERROR_INSUFFICIENT_BUFFER.
                size = ctypes.sizeof(EchoReply) + len(_ICMP_PAYLOAD) + 8
                buffer = ctypes.create_string_buffer(size)
                destination = int.from_bytes(address.packed, "little")
                replies = iphlpapi.IcmpSendEcho(
                    handle,
                    destination,
                    _ICMP_PAYLOAD,
                    len(_ICMP_PAYLOAD),
                    None,
                    buffer,
                    size,
                    timeout_ms,
                )
                if replies == 0:
                    return IcmpResult(False, None)
                reply = ctypes.cast(buffer, ctypes.POINTER(EchoReply)).contents
                if reply.status != 0:
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
