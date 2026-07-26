"""Scheduled, verified database retention job."""

from __future__ import annotations

from netmon.collectors.base import Clock, Collector
from netmon.config import RetentionConfig
from netmon.db import Store
from netmon.db.retention import RetentionJob
from netmon.models import Sample


class RetentionCollector(Collector):
    def __init__(self, store: Store, clock: Clock, config: RetentionConfig) -> None:
        self.store = store
        self.clock = clock
        self.config = config
        self.job = RetentionJob(store, config)

    @property
    def name(self) -> str:
        return "retention"

    @property
    def interval(self) -> float:
        # Daily execution is enough for day-granularity retention tiers.
        return 86_400.0

    async def collect(self) -> list[Sample]:
        self.job.run(self.clock.now())
        return []
