"""Scheduled ICMP latency, jitter, and packet-loss measurements."""

from __future__ import annotations

from itertools import pairwise

from netmon.collectors.base import Clock, Collector
from netmon.collectors.health import HealthSink
from netmon.config import LatencyConfig
from netmon.investigation import InvestigationState
from netmon.models import HealthObservation, Sample
from netmon.probes.gateway import GatewayProbe
from netmon.probes.icmp import IcmpProbe


class LatencyCollector(Collector):
    def __init__(
        self,
        icmp: IcmpProbe,
        gateway: GatewayProbe,
        clock: Clock,
        config: LatencyConfig,
        health_sink: HealthSink | None = None,
        investigation: InvestigationState | None = None,
        burst_interval_seconds: float | None = None,
    ) -> None:
        self.icmp = icmp
        self.gateway = gateway
        self.clock = clock
        self.config = config
        self.health_sink = health_sink
        self.investigation = investigation
        self.burst_interval_seconds = burst_interval_seconds

    @property
    def name(self) -> str:
        return "latency"

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
        for configured_target in self.config.targets:
            if configured_target == "gateway":
                resolved_target = await self.gateway.default_gateway()
                if resolved_target is None:
                    samples.extend(
                        (
                            Sample("latency_rtt_ms", configured_target, "ms", now, 0.0),
                            Sample("latency_jitter_ms", configured_target, "ms", now, 0.0),
                            Sample(
                                "latency_loss_percent", configured_target, "percent", now, 100.0
                            ),
                        )
                    )
                    if self.health_sink is not None:
                        callback_result = self.health_sink(
                            HealthObservation(configured_target, "icmp", None, now, {})
                        )
                        if callback_result is not None:
                            await callback_result
                    continue
                target = resolved_target
            else:
                target = configured_target
            results = [
                await self.icmp.ping(target, self.config.timeout_ms)
                for _ in range(self.config.probes_per_sample)
            ]
            rtts = [
                result.latency_ms
                for result in results
                if result.reachable and result.latency_ms is not None
            ]
            successful = sum(1 for result in results if result.reachable)
            rtt = sum(rtts) / len(rtts) if rtts else 0.0
            # Jitter is mean absolute difference between successive successful RTTs.
            differences = [abs(right - left) for left, right in pairwise(rtts)]
            jitter = sum(differences) / len(differences) if differences else 0.0
            loss = (len(results) - successful) / len(results) * 100
            samples.extend(
                (
                    Sample("latency_rtt_ms", configured_target, "ms", now, rtt),
                    Sample("latency_jitter_ms", configured_target, "ms", now, jitter),
                    Sample("latency_loss_percent", configured_target, "percent", now, loss),
                )
            )
            if self.health_sink is not None:
                result = self.health_sink(
                    HealthObservation(
                        configured_target,
                        "icmp",
                        successful > 0,
                        now,
                        {"resolved_target": target, "loss_percent": loss},
                    )
                )
                if result is not None:
                    await result
        return samples
