from __future__ import annotations

from datetime import timedelta

import pytest

from netmon.collectors.dns_probe import DnsCollector
from netmon.collectors.eero_usage import EeroUsageCollector
from netmon.collectors.host_nic import HostNicCollector
from netmon.collectors.http_probe import HttpCollector
from netmon.collectors.latency import LatencyCollector
from netmon.collectors.managed_host import ManagedHostNicCollector
from netmon.collectors.speedtest import SpeedtestCollector
from netmon.collectors.wan_status import WanStatusCollector
from netmon.config import (
    DnsConfig,
    EeroConfig,
    HostNicConfig,
    HttpConfig,
    LatencyConfig,
    SpeedtestConfig,
    WanConfig,
)
from netmon.investigation import InvestigationState
from netmon.probes.speedtest import SpeedtestResult


class _SpeedtestProbe:
    async def run(self, config):
        return SpeedtestResult(100.0, 10.0, 4.0, 5.0)


@pytest.mark.parametrize(
    "factory, configured",
    [
        (lambda clock: LatencyCollector(object(), object(), clock, LatencyConfig()), 10.0),
        (lambda clock: DnsCollector(object(), clock, DnsConfig()), 60.0),
        (lambda clock: HttpCollector(object(), clock, HttpConfig()), 60.0),
        (lambda clock: WanStatusCollector(object(), clock, WanConfig()), 30.0),
        (lambda clock: HostNicCollector(object(), clock, HostNicConfig()), 10.0),
        (lambda clock: EeroUsageCollector(object(), None, clock, EeroConfig()), 60.0),
        (lambda clock: ManagedHostNicCollector(object(), clock, "host", 30.0), 30.0),
    ],
)
def test_burst_interval_reverts_after_expiry(frozen_clock, factory, configured):
    state = InvestigationState(frozen_clock)
    collector = factory(frozen_clock)
    collector.investigation = state
    collector.burst_interval_seconds = 2.0
    assert collector.interval == configured
    started = frozen_clock.now()
    state.start(1, started, started + timedelta(seconds=10))
    assert collector.interval == 2.0
    frozen_clock.advance(11)
    assert collector.interval == configured


@pytest.mark.asyncio
async def test_speedtest_suppression_is_opt_in(frozen_clock):
    state = InvestigationState(frozen_clock)
    started = frozen_clock.now()
    state.start(1, started, started + timedelta(seconds=10))
    suppressed = SpeedtestCollector(_SpeedtestProbe(), frozen_clock, SpeedtestConfig(), state, True)
    assert await suppressed.collect() == []
    allowed = SpeedtestCollector(_SpeedtestProbe(), frozen_clock, SpeedtestConfig(), state)
    assert await allowed.collect()
