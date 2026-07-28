"""Scheduled subnet discovery with bounded concurrency."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from datetime import datetime
from itertools import islice

from netmon.collectors.base import Clock, Collector
from netmon.config import DiscoveryConfig
from netmon.models import DeviceObservation, Sample
from netmon.probes.arp import ArpProbe, InterfaceProbe
from netmon.probes.hostname import HostnameProbe
from netmon.probes.icmp import IcmpProbe
from netmon.probes.oui import OuiLookup

ObservationSink = Callable[[list[DeviceObservation], datetime, int], Awaitable[None] | None]


class DiscoveryCollector(Collector):
    def __init__(
        self,
        arp: ArpProbe,
        icmp: IcmpProbe,
        oui: OuiLookup,
        hostname: HostnameProbe,
        interfaces: InterfaceProbe,
        clock: Clock,
        config: DiscoveryConfig,
        observation_sink: ObservationSink,
    ) -> None:
        self.arp = arp
        self.icmp = icmp
        self.oui = oui
        self.hostname = hostname
        self.interfaces = interfaces
        self.clock = clock
        self.config = config
        self.observation_sink = observation_sink

    @property
    def name(self) -> str:
        return "discovery"

    @property
    def interval(self) -> float:
        return self.config.interval_seconds

    async def collect(self) -> list[Sample]:
        subnets = await self._subnets()

        async def ping(host: ipaddress.IPv4Address) -> None:
            await self.icmp.ping(str(host), 1000)

        for subnet in subnets:
            hosts = iter(subnet.hosts())
            while batch := list(islice(hosts, self.config.concurrency)):
                await asyncio.gather(*(ping(host) for host in batch))
        arp_entries = await self.arp.entries()
        subnet_set = tuple(subnets)
        by_ip = {entry.ip: entry for entry in arp_entries if self._in_subnets(entry.ip, subnet_set)}
        observations: dict[str, DeviceObservation] = {}
        for entry in by_ip.values():
            hostname = await self.hostname.resolve(entry.ip)
            mac = entry.mac.lower()
            observations[mac] = DeviceObservation(
                mac=mac,
                ip=entry.ip,
                vendor=self.oui.vendor_for(mac),
                hostname=hostname,
            )
        result = self.observation_sink(
            list(observations.values()), self.clock.now(), self.config.offline_after_misses
        )
        if result is not None:
            await result
        return []

    async def _subnets(self) -> list[ipaddress.IPv4Network]:
        if self.config.subnets:
            result: list[ipaddress.IPv4Network] = []
            for value in self.config.subnets:
                network = ipaddress.ip_network(value, strict=False)
                if isinstance(network, ipaddress.IPv4Network):
                    result.append(network)
            return result
        return await self.interfaces.local_subnets()

    @staticmethod
    def _in_subnets(ip: str, subnets: tuple[ipaddress.IPv4Network, ...]) -> bool:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(address in subnet for subnet in subnets)
