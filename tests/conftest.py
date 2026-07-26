from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from netmon.collectors.base import FrozenClock
from netmon.probes.arp import ArpEntry
from netmon.probes.icmp import IcmpResult


class FakeIcmpProbe:
    def __init__(self, reachable: set[str] | None = None) -> None:
        self.reachable = reachable or set()
        self.calls: list[str] = []

    async def ping(self, ip: str, timeout_ms: int) -> IcmpResult:
        del timeout_ms
        self.calls.append(ip)
        return IcmpResult(ip in self.reachable, 1.25 if ip in self.reachable else None)


class FakeArpProbe:
    def __init__(self, entries: list[ArpEntry] | None = None) -> None:
        self.current = entries or []

    async def entries(self) -> list[ArpEntry]:
        return list(self.current)


class FakeHostnameProbe:
    def __init__(self, names: dict[str, str] | None = None) -> None:
        self.names = names or {}

    async def resolve(self, ip: str) -> str | None:
        return self.names.get(ip)


class FakeOuiLookup:
    def vendor_for(self, mac: str) -> str | None:
        return "Test Vendor" if mac.startswith("aa:bb:cc") else None


class FakeInterfaceProbe:
    def __init__(self, subnets: list[str]) -> None:
        import ipaddress

        self.subnets = [ipaddress.ip_network(subnet) for subnet in subnets]

    async def local_subnets(self):
        return self.subnets


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"
