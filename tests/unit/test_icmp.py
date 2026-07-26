from __future__ import annotations

import asyncio

import pytest

from netmon.probes.icmp import IcmpResult, PosixIcmpProbe


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
