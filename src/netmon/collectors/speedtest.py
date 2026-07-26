"""Scheduled link-capacity measurements; these are not utilisation counters."""

from __future__ import annotations

from netmon.collectors.base import Clock, Collector
from netmon.config import SpeedtestConfig
from netmon.models import Sample
from netmon.probes.speedtest import SpeedtestProbe


class SpeedtestCollector(Collector):
    def __init__(self, probe: SpeedtestProbe, clock: Clock, config: SpeedtestConfig) -> None:
        self.probe = probe
        self.clock = clock
        self.config = config

    @property
    def name(self) -> str:
        return "speedtest"

    @property
    def interval(self) -> float:
        return self.config.interval_seconds

    async def collect(self) -> list[Sample]:
        now = self.clock.now()
        result = await self.probe.run(self.config)
        values = {
            "speedtest_download_capacity": result.download_mbps,
            "speedtest_upload_capacity": result.upload_mbps,
            "speedtest_idle_latency": result.idle_latency_ms,
            "speedtest_loaded_latency": result.loaded_latency_ms,
        }
        return [
            Sample(
                kind,
                "capacity",
                "Mbps" if "capacity" in kind else "ms",
                now,
                value,
                "speedtest",
            )
            for kind, value in values.items()
            if value is not None
        ] or [Sample("speedtest_available", "capacity", "boolean", now, 0.0, "speedtest")]
