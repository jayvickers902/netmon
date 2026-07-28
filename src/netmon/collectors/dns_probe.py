"""Scheduled resolver-pinned DNS checks."""

from __future__ import annotations

from netmon.collectors.base import Clock, Collector
from netmon.collectors.health import HealthSink
from netmon.config import DnsConfig
from netmon.investigation import InvestigationState
from netmon.models import HealthObservation, Sample
from netmon.probes.dns import DnsProbe


class DnsCollector(Collector):
    def __init__(
        self,
        dns: DnsProbe,
        clock: Clock,
        config: DnsConfig,
        health_sink: HealthSink | None = None,
        investigation: InvestigationState | None = None,
        burst_interval_seconds: float | None = None,
    ) -> None:
        self.dns = dns
        self.clock = clock
        self.config = config
        self.health_sink = health_sink
        self.investigation = investigation
        self.burst_interval_seconds = burst_interval_seconds

    @property
    def name(self) -> str:
        return "dns"

    @property
    def interval(self) -> float:
        if (
            self.investigation is not None
            and self.investigation.active
            and self.burst_interval_seconds is not None
        ):
            return self.burst_interval_seconds
        return self.config.interval_seconds

    async def collect(self) -> list[Sample]:
        now = self.clock.now()
        samples: list[Sample] = []
        for resolver in self.config.resolvers:
            for name in self.config.query_names:
                result = await self.dns.resolve(resolver, name)
                samples.append(
                    Sample(
                        "dns_resolution_ms",
                        f"{resolver}:{name}",
                        "ms",
                        now,
                        result.resolution_ms or 0.0,
                    )
                )
                if self.health_sink is not None:
                    observation = HealthObservation(
                        f"{resolver}:{name}",
                        "dns",
                        result.status == "ok",
                        now,
                        {"resolver": resolver, "name": name, "status": result.status},
                    )
                    callback_result = self.health_sink(observation)
                    if callback_result is not None:
                        await callback_result
        return samples


DnsProbeCollector = DnsCollector
