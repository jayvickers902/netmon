"""Opt-in investigation capture at the moment an outage opens.

Auto-arm reuses the outage coordinator's sustained-condition signal rather than
introducing a second detector.  This is the same argument that motivated
auto-traceroute in v0.3: capture evidence when things break, not twenty
minutes later when someone finally opens a browser.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from netmon.collectors.base import Clock, Collector
from netmon.config import InvestigationConfig
from netmon.models import Sample


class InvestigationStarter(Protocol):
    def start(
        self,
        duration_seconds: float | None,
        label: str | None,
        note: str | None,
        trigger: str = "manual",
    ) -> Any: ...

    def active(self) -> bool: ...


# A plain callable, matching how Scheduler already types its own status_provider. The concrete
# source is OutageCoordinator.status, which exists before the Scheduler is built — depending on
# the Scheduler itself would be circular, since it is constructed from the collector list.
type StatusProvider = Callable[[], dict[str, object]]


class InvestigationArmCollector(Collector):
    """Start one investigation when the existing outage signal is open."""

    def __init__(
        self,
        service: InvestigationStarter,
        status_provider: StatusProvider,
        clock: Clock,
        config: InvestigationConfig,
    ) -> None:
        self.service = service
        self.status_provider = status_provider
        self.clock = clock
        self.config = config
        self._last_armed_at: datetime | None = None

    @property
    def name(self) -> str:
        return "investigation_arm"

    @property
    def interval(self) -> float:
        # Matches the alerts collector. This only reads an in-memory dict, but a once-per-second
        # scheduler task running forever is not worth the resolution on a sustained-condition
        # signal that already has hysteresis measured in tens of seconds.
        return 5.0

    async def collect(self) -> list[Sample]:
        try:
            status = self.status_provider()
            if not bool(status.get("open", False)):
                return []
            if self.service.active():
                return []

            now = self.clock.now()
            if self._last_armed_at is not None:
                elapsed = (now - self._last_armed_at).total_seconds()
                if elapsed < self.config.auto_arm_cooldown_seconds:
                    return []

            self.service.start(
                duration_seconds=self.config.auto_arm_duration_seconds,
                label=None,
                note=None,
                trigger="auto_arm",
            )
            self._last_armed_at = now
        except Exception:
            # Auto-arm must not turn an optional diagnostic feature into an
            # outage of the scheduler or the primary health collectors.
            return []
        return []
