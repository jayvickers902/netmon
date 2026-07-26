from __future__ import annotations

import pytest

from netmon.collectors.dns_probe import DnsCollector
from netmon.collectors.http_probe import HttpCollector
from netmon.collectors.latency import LatencyCollector
from netmon.collectors.wan_status import WanStatusCollector
from netmon.config import DnsConfig, HttpConfig, LatencyConfig, WanConfig
from netmon.probes.dns import DnsResult
from netmon.probes.http import HttpResult
from netmon.probes.icmp import IcmpResult
from netmon.probes.upnp import WanStatus


class Gateway:
    async def default_gateway(self):
        return "192.168.4.1"


class Icmp:
    async def ping(self, ip, timeout_ms):
        del timeout_ms
        return IcmpResult(ip != "8.8.8.8", 10.0 if ip != "8.8.8.8" else None)


class Dns:
    async def resolve(self, resolver, name):
        return DnsResult(resolver, name, "ok", 4.0, ("93.184.216.34",))


class Http:
    async def fetch(self, url):
        return HttpResult(url, "ok", 204, 8.0, 10.0)


class Upnp:
    def __init__(self):
        self.values = [
            WanStatus("Connected", "ERROR_NONE", 100, "198.51.100.1"),
            WanStatus("Connected", "ERROR_NONE", 4, "198.51.100.2"),
        ]

    async def status(self):
        return self.values.pop(0)


@pytest.mark.asyncio
async def test_latency_emits_rtt_jitter_loss_and_resolves_gateway(frozen_clock):
    observations = []
    collector = LatencyCollector(
        Icmp(),
        Gateway(),
        frozen_clock,
        LatencyConfig(targets=["gateway", "8.8.8.8"], probes_per_sample=2),
        observations.append,
    )
    samples = await collector.collect()
    assert len(samples) == 6
    assert samples[0].value == 10.0
    assert samples[5].value == 100.0
    assert observations[0].details["resolved_target"] == "192.168.4.1"


@pytest.mark.asyncio
async def test_dns_http_and_wan_collectors_emit_samples(frozen_clock):
    observations = []
    dns = DnsCollector(
        Dns(),
        frozen_clock,
        DnsConfig(resolvers=["1.1.1.1"], query_names=["example.com"]),
        observations.append,
    )
    http = HttpCollector(
        Http(), frozen_clock, HttpConfig(urls=["https://example.com"]), observations.append
    )
    events = []
    wan = WanStatusCollector(Upnp(), frozen_clock, WanConfig(), events.append, lambda status: None)
    assert (await dns.collect())[0].value == 4.0
    assert (await http.collect())[1].value == 204.0
    await wan.collect()
    await wan.collect()
    assert {event.kind for event in events} == {"wan_reconnect", "wan_external_ip_change"}
    assert {item.protocol for item in observations} == {"dns", "http"}
