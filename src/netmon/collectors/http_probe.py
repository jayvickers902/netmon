"""Scheduled HTTP reachability and response timing checks."""

from __future__ import annotations

from collections.abc import Awaitable

from netmon.collectors.base import Clock, Collector
from netmon.collectors.health import HealthSink
from netmon.config import HttpConfig
from netmon.models import HealthObservation, Sample
from netmon.probes.http import HttpProbe


class HttpCollector(Collector):
    def __init__(
        self,
        http: HttpProbe,
        clock: Clock,
        config: HttpConfig,
        health_sink: HealthSink | None = None,
    ) -> None:
        self.http = http
        self.clock = clock
        self.config = config
        self.health_sink = health_sink

    @property
    def name(self) -> str:
        return "http"

    @property
    def interval(self) -> float:
        return self.config.interval_seconds

    async def collect(self) -> list[Sample]:
        now = self.clock.now()
        samples: list[Sample] = []
        for configured_url in self.config.urls:
            url = str(configured_url)
            result = await self.http.fetch(url)
            samples.append(Sample("http_ttfb_ms", url, "ms", now, result.ttfb_ms or 0.0))
            samples.append(
                Sample("http_status_code", url, "code", now, float(result.status_code or 0))
            )
            if self.health_sink is not None:
                callback_result = self.health_sink(
                    HealthObservation(
                        url,
                        "http",
                        result.status == "ok",
                        now,
                        {"status": result.status, "status_code": result.status_code},
                    )
                )
                if callback_result is not None:
                    await callback_result
        return samples

    async def close(self) -> None:
        close = getattr(self.http, "close", None)
        if close is not None:
            result = close()
            if isinstance(result, Awaitable):
                await result


HttpProbeCollector = HttpCollector
