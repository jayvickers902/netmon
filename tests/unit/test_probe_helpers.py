from __future__ import annotations

import asyncio
import ipaddress
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from netmon.probes.arp import (
    PosixArpProbe,
    SystemInterfaceProbe,
    create_arp_probe,
    create_interface_probe,
    parse_get_ip_net_table,
)
from netmon.probes.hostname import SystemHostnameProbe


class Process:
    def __init__(self, returncode: int, output: bytes = b"") -> None:
        self.returncode = returncode
        self.output = output

    async def communicate(self):
        return self.output, b""


def test_empty_ip_net_table_and_interface_subnets():
    assert parse_get_ip_net_table(b"\x00\x00\x00") == []
    addresses = {
        "eth0": [SimpleNamespace(address="192.168.1.4", netmask="255.255.255.0")],
        "lo": [SimpleNamespace(address="127.0.0.1", netmask="255.0.0.0")],
        "bad": [SimpleNamespace(address="not-an-ip", netmask="255.255.255.0")],
    }
    assert SystemInterfaceProbe._read_subnets(addresses) == [ipaddress.ip_network("192.168.1.0/24")]
    assert isinstance(create_arp_probe(), PosixArpProbe) is (sys.platform != "win32")
    assert isinstance(create_interface_probe(), SystemInterfaceProbe)


@pytest.mark.asyncio
async def test_posix_arp_falls_back_to_arp_command(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return (
            Process(1) if args[0] == "ip" else Process(0, b"? (192.168.1.1) at aa:bb:cc:dd:ee:ff")
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    entries = await PosixArpProbe().entries()
    assert entries[0].ip == "192.168.1.1"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_windows_hostname_probe_releases_subprocess_pipes(monkeypatch):
    def no_dns(*args):
        raise OSError

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, b"DEVICE <00> UNIQUE", b"")

    async def fail_async_exec(*args, **kwargs):
        raise AssertionError("Windows hostname lookup must not allocate Proactor pipe transports")

    monkeypatch.setattr(socket, "gethostbyaddr", no_dns)
    monkeypatch.setattr("netmon.probes.hostname.sys.platform", "win32")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_async_exec)
    assert await SystemHostnameProbe().resolve("192.168.1.2") == "DEVICE"


@pytest.mark.asyncio
async def test_posix_hostname_probe_keeps_async_command_path(monkeypatch):
    def no_dns(*args):
        raise OSError

    async def fake_exec(*args, **kwargs):
        return Process(0, b"DEVICE <00> UNIQUE")

    monkeypatch.setattr(socket, "gethostbyaddr", no_dns)
    monkeypatch.setattr("netmon.probes.hostname._is_windows", lambda: False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await SystemHostnameProbe().resolve("192.168.1.2") == "DEVICE"
