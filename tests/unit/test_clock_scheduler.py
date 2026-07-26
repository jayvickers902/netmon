from __future__ import annotations

import asyncio

import pytest

from netmon.collectors.base import Collector, FrozenClock
from netmon.models import Sample
from netmon.scheduler import Scheduler


class GoodCollector(Collector):
    name = "good"
    interval = 0.001

    async def collect(self) -> list[Sample]:
        return []


class BadCollector(Collector):
    name = "bad"
    interval = 0.001

    async def collect(self) -> list[Sample]:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_frozen_clock_sleep_advances_datetime(frozen_clock: FrozenClock):
    before = frozen_clock.now()
    await frozen_clock.sleep(2)
    assert (frozen_clock.now() - before).total_seconds() == 2
    assert frozen_clock.monotonic() == 2


@pytest.mark.asyncio
async def test_scheduler_isolates_failures_and_reports_health():
    scheduler = Scheduler([GoodCollector(), BadCollector()], startup_jitter=0, max_backoff=0.01)
    await scheduler.start()
    await asyncio.sleep(0.02)
    await scheduler.stop()
    assert scheduler.health()["good"]["last_success"] is not None
    assert scheduler.health()["bad"]["last_error"] == "boom"
