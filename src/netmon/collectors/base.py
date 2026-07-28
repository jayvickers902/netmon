"""Collector and clock contracts."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Protocol

from netmon.models import Sample


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return asyncio.get_running_loop().time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FrozenClock:
    def __init__(self, current: datetime) -> None:
        self.current = current
        self.monotonic_value = 0.0

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.monotonic_value

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
        self.monotonic_value += seconds


class Collector(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def interval(self) -> float: ...

    @abstractmethod
    async def collect(self) -> list[Sample]: ...

    async def close(self) -> None:
        """Release resources owned by the collector's probes."""
        return None
