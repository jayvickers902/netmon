"""Scheduled link-capacity measurements; these are not utilisation counters."""

from __future__ import annotations

import logging

from netmon.collectors.base import Clock, Collector
from netmon.config import SpeedtestConfig
from netmon.investigation import InvestigationState
from netmon.models import Sample
from netmon.probes.speedtest import SpeedtestProbe


class SpeedtestCollector(Collector):
    def __init__(
        self,
        probe: SpeedtestProbe,
        clock: Clock,
        config: SpeedtestConfig,
        investigation: InvestigationState | None = None,
        suppress_during_investigation: bool = False,
    ) -> None:
        self.probe = probe
        self.clock = clock
        self.config = config
        self.investigation = investigation
        self.suppress_during_investigation = suppress_during_investigation
        self._logger = logging.getLogger(__name__)

    @property
    def name(self) -> str:
        return "speedtest"

    @property
    def interval(self) -> float:
        return self.config.interval_seconds

    async def collect(self) -> list[Sample]:
        if (
            self.investigation is not None
            and self.investigation.active
            and self.suppress_during_investigation
        ):
            # Speedtest deliberately saturates the uplink; running it now would
            # manufacture the verdict.
            self._logger.info("Skipping speedtest during active investigation")
            return []
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
