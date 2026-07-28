from __future__ import annotations

from datetime import UTC, datetime

import pytest

from netmon.collectors.base import FrozenClock
from netmon.collectors.investigation_arm import InvestigationArmCollector
from netmon.config import InvestigationConfig


class FakeService:
    def __init__(self, active: bool = False, error: bool = False) -> None:
        self.is_active = active
        self.error = error
        self.starts: list[dict[str, object]] = []

    def active(self) -> bool:
        return self.is_active

    def start(
        self,
        duration_seconds: float | None,
        label: str | None,
        note: str | None,
        trigger: str = "manual",
    ) -> None:
        if self.error:
            raise RuntimeError("service unavailable")
        self.starts.append(
            {
                "duration_seconds": duration_seconds,
                "label": label,
                "note": note,
                "trigger": trigger,
            }
        )
        self.is_active = True


class FakeStatusProvider:
    """Callable, matching OutageCoordinator.status — the real source the collector is wired to."""

    def __init__(self, open_: bool = True) -> None:
        self.open = open_

    def __call__(self) -> dict[str, object]:
        return {"open": self.open}


def make_collector(
    clock: FrozenClock,
    service: FakeService | None = None,
    status: FakeStatusProvider | None = None,
    cooldown: float = 1800.0,
) -> tuple[InvestigationArmCollector, FakeService]:
    actual_service = service or FakeService()
    collector = InvestigationArmCollector(
        actual_service,
        status or FakeStatusProvider(),
        clock,
        InvestigationConfig(
            auto_arm=True,
            auto_arm_duration_seconds=600.0,
            auto_arm_cooldown_seconds=cooldown,
        ),
    )
    return collector, actual_service


@pytest.mark.asyncio
async def test_arms_on_degradation() -> None:
    collector, service = make_collector(FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)))

    assert await collector.collect() == []
    assert service.starts == [
        {
            "duration_seconds": 600.0,
            "label": None,
            "note": None,
            "trigger": "auto_arm",
        }
    ]


@pytest.mark.asyncio
async def test_does_not_arm_when_active() -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    collector, service = make_collector(clock, FakeService(active=True))

    await collector.collect()

    assert service.starts == []


@pytest.mark.asyncio
async def test_respects_cooldown_and_rearms_after_it() -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    collector, service = make_collector(clock, cooldown=60.0)
    await collector.collect()
    service.is_active = False

    clock.advance(59)
    await collector.collect()
    assert len(service.starts) == 1

    clock.advance(1)
    await collector.collect()
    assert len(service.starts) == 2


@pytest.mark.asyncio
async def test_never_raises_when_service_errors() -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    collector, service = make_collector(clock, FakeService(error=True))

    assert await collector.collect() == []
    assert service.starts == []
