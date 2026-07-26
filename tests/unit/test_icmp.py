from __future__ import annotations

import asyncio
import ctypes
import sys

import pytest

from netmon.probes.icmp import EchoReply, IcmpResult, PosixIcmpProbe, WindowsIcmpProbe

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="iphlpapi is Windows-only")


class FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"64 bytes from 192.0.2.1: time=2.34 ms\n", b""


@pytest.mark.asyncio
async def test_posix_icmp_parser(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await PosixIcmpProbe().ping("192.0.2.1", 1000)
    assert result == IcmpResult(True, 2.34)


@windows_only
def test_icmp_handle_types_are_pointer_sized():
    """Regression: an undeclared restype truncates the 64-bit HANDLE to 32 bits.

    The truncated value is not INVALID_HANDLE_VALUE, so it survives validation and
    every send then fails against a garbage handle — every host looks unreachable
    while the network is fine. Assert the declarations rather than the symptom.
    """
    from ctypes import wintypes

    from netmon.probes.icmp import _iphlpapi

    library = _iphlpapi()
    assert library.IcmpCreateFile.restype is wintypes.HANDLE
    assert ctypes.sizeof(library.IcmpCreateFile.restype) == ctypes.sizeof(ctypes.c_void_p)
    assert library.IcmpSendEcho.argtypes[0] is wintypes.HANDLE
    assert library.IcmpCloseHandle.argtypes == (wintypes.HANDLE,)


class FakeIphlpapi:
    """Records the ReplySize it was handed and writes a successful echo reply."""

    def __init__(self, status: int = 0, rtt: int = 7) -> None:
        self.status = status
        self.rtt = rtt
        self.reply_size: int | None = None
        self.closed = False

    def IcmpCreateFile(self) -> int:  # noqa: N802 - mirrors the Win32 name
        return 0x22DAD706F60  # a realistic 64-bit handle

    def IcmpSendEcho(  # noqa: N802 - mirrors the Win32 name
        self, handle, destination, data, data_size, options, buffer, size, timeout
    ) -> int:
        self.reply_size = size
        reply = EchoReply(status=self.status, round_trip_time=self.rtt)
        ctypes.memmove(buffer, ctypes.byref(reply), ctypes.sizeof(EchoReply))
        return 1

    def IcmpCloseHandle(self, handle) -> int:  # noqa: N802 - mirrors the Win32 name
        self.closed = True
        return 1


@windows_only
def test_reply_buffer_leaves_room_for_payload_and_error(monkeypatch):
    """Regression: sizing the buffer to the struct alone fails with
    ERROR_INSUFFICIENT_BUFFER. It must also hold the echoed payload."""
    fake = FakeIphlpapi()
    monkeypatch.setattr("netmon.probes.icmp._iphlpapi", lambda: fake)

    result = WindowsIcmpProbe._ping_sync("1.1.1.1", 1000)

    assert result == IcmpResult(True, 7.0)
    assert fake.reply_size is not None
    assert fake.reply_size > ctypes.sizeof(EchoReply)
    assert fake.closed


@windows_only
def test_nonzero_reply_status_is_unreachable(monkeypatch):
    fake = FakeIphlpapi(status=11010)  # IP_REQ_TIMED_OUT
    monkeypatch.setattr("netmon.probes.icmp._iphlpapi", lambda: fake)

    assert WindowsIcmpProbe._ping_sync("1.1.1.1", 1000) == IcmpResult(False, None)
    assert fake.closed


@windows_only
def test_invalid_address_is_unreachable():
    assert WindowsIcmpProbe._ping_sync("not-an-ip", 1000) == IcmpResult(False, None)
