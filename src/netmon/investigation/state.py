"""In-memory, clock-driven state for a time-boxed investigation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from netmon.collectors.base import Clock


@dataclass(frozen=True, slots=True)
class InvestigationWindow:
    investigation_id: int
    started_at: datetime
    ends_at: datetime


class InvestigationState:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._window: InvestigationWindow | None = None

    @property
    def active(self) -> bool:
        window = self._window
        return window is not None and self._clock.now() < window.ends_at

    def start(self, investigation_id: int, started_at: datetime, ends_at: datetime) -> None:
        if self.active:
            raise ValueError("an investigation is already active")
        self._window = InvestigationWindow(investigation_id, started_at, ends_at)

    def stop(self) -> None:
        self._window = None

    def snapshot(self) -> InvestigationWindow | None:
        return self._window if self.active else None
