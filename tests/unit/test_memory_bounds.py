from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from netmon.collectors.discovery import DiscoveryCollector
from netmon.config import DiscoveryConfig, OutageConfig
from netmon.models import HealthObservation
from netmon.outage import OutageCoordinator, OutageStateMachine
from netmon.probes.icmp import IcmpResult
from netmon.probes.upnp import WanStatus


class EmptyArp:
    async def entries(self):
        return []


class EmptyHostname:
    async def resolve(self, ip: str):
        del ip
        return None


class EmptyOui:
    def vendor_for(self, mac: str):
        del mac
        return None


class ImmediateIcmp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def ping(self, ip: str, timeout_ms: int) -> IcmpResult:
        del timeout_ms
        self.calls.append(ip)
        return IcmpResult(False, None)


@pytest.mark.asyncio
async def test_discovery_never_queues_more_than_concurrency_pings(monkeypatch, frozen_clock):
    real_gather = asyncio.gather
    batch_sizes: list[int] = []

    async def gather(*awaitables):
        batch_sizes.append(len(awaitables))
        return await real_gather(*awaitables)

    monkeypatch.setattr(asyncio, "gather", gather)
    icmp = ImmediateIcmp()
    collector = DiscoveryCollector(
        EmptyArp(),
        icmp,
        EmptyOui(),
        EmptyHostname(),
        object(),
        frozen_clock,
        DiscoveryConfig(subnets=["192.168.1.0/24"], concurrency=8),
        lambda observations, now, misses: None,
    )

    await collector.collect()

    assert max(batch_sizes) <= 8
    assert sum(batch_sizes) == 254
    assert len(icmp.calls) == 254


def test_wan_history_discards_healthy_samples_and_caps_positive_samples():
    machine = OutageStateMachine(wan_interval_seconds=None)
    base = datetime(2026, 1, 1, tzinfo=UTC)

    for index in range(2_000):
        machine.record_wan(
            WanStatus("Connected", "ERROR_NONE", index, "198.51.100.5"),
            base + timedelta(seconds=index),
        )
    assert machine._wan_history == []

    for index in range(2_000):
        machine.record_wan(
            WanStatus("Disconnected", "ERROR_ISP_DISCONNECTED", index, None),
            base + timedelta(seconds=index),
        )
    assert len(machine._wan_history) == machine._MAX_WAN_HISTORY


@pytest.mark.asyncio
async def test_closed_outage_details_are_released(frozen_clock):
    events = []
    coordinator = OutageCoordinator(
        OutageStateMachine(failures_to_open=1, successes_to_close=1),
        frozen_clock,
        lambda event: events.append(event) or len(events),
        OutageConfig(failures_to_open=1, successes_to_close=1, traceroute_on_open=False),
    )

    for index in range(2_000):
        start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index * 2)
        await coordinator.observe(HealthObservation("gateway", "icmp", False, start, {}))
        await coordinator.observe(
            HealthObservation("gateway", "icmp", True, start + timedelta(seconds=1), {})
        )

    assert len([event for event in events if event.kind == "outage_start"]) == 2_000
    assert coordinator._event_details == {}
