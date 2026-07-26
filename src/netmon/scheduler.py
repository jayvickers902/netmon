"""Failure-isolated asyncio collector supervisor."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from netmon.collectors.base import Clock, Collector, RealClock
from netmon.models import Sample

logger = logging.getLogger(__name__)
SampleSink = Callable[[list[Sample]], Awaitable[None] | None]


@dataclass(slots=True)
class CollectorStatus:
    name: str
    last_run: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0


class Scheduler:
    def __init__(
        self,
        collectors: list[Collector],
        sink: SampleSink | None = None,
        clock: Clock | None = None,
        startup_jitter: float = 1.0,
        max_backoff: float = 300.0,
    ) -> None:
        self.collectors = collectors
        self.sink = sink
        self.clock = clock or RealClock()
        self.startup_jitter = startup_jitter
        self.max_backoff = max_backoff
        self.statuses = {collector.name: CollectorStatus(collector.name) for collector in collectors}
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [asyncio.create_task(self._run(collector), name=f"collector:{collector.name}") for collector in self.collectors]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def health(self) -> dict[str, dict[str, object | None]]:
        return {
            name: {
                "last_run": status.last_run.isoformat() if status.last_run else None,
                "last_success": status.last_success.isoformat() if status.last_success else None,
                "last_error": status.last_error,
                "consecutive_failures": status.consecutive_failures,
            }
            for name, status in self.statuses.items()
        }

    async def _run(self, collector: Collector) -> None:
        status = self.statuses[collector.name]
        if self.startup_jitter:
            await self.clock.sleep(random.uniform(0.0, self.startup_jitter))
        failures = 0
        while True:
            status.last_run = self.clock.now()
            try:
                samples = await collector.collect()
                if self.sink is not None and samples:
                    result = self.sink(samples)
                    if result is not None:
                        await result
                status.last_success = self.clock.now()
                status.last_error = None
                failures = 0
                status.consecutive_failures = 0
                await self.clock.sleep(collector.interval)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - isolation is the supervisor's job
                failures += 1
                status.consecutive_failures = failures
                status.last_error = str(error)
                logger.exception("collector %s failed; continuing", collector.name)
                backoff = min(collector.interval * (2 ** (failures - 1)), self.max_backoff)
                await self.clock.sleep(backoff)
