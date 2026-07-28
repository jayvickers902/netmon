"""Scheduled router-reported WAN connection status."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from netmon.collectors.base import Clock, Collector
from netmon.collectors.health import EventSink
from netmon.config import WanConfig
from netmon.investigation import InvestigationState
from netmon.models import Event, Sample
from netmon.probes.upnp import UpnpProbe, WanStatus


class WanStatusCollector(Collector):
    def __init__(
        self,
        upnp: UpnpProbe,
        clock: Clock,
        config: WanConfig,
        event_sink: EventSink | None = None,
        wan_sink: Callable[[WanStatus], Awaitable[None] | None] | None = None,
        investigation: InvestigationState | None = None,
        burst_interval_seconds: float | None = None,
    ) -> None:
        self.upnp = upnp
        self.clock = clock
        self.config = config
        self.event_sink = event_sink
        self.wan_sink = wan_sink
        self.investigation = investigation
        self.burst_interval_seconds = burst_interval_seconds
        self.previous: WanStatus | None = None

    @property
    def name(self) -> str:
        return "wan_status"

    @property
    def interval(self) -> float:
        if (
            self.investigation is not None
            and self.investigation.active
            and self.burst_interval_seconds is not None
        ):
            return self.burst_interval_seconds
        return self.config.interval_seconds

    async def collect(self) -> list[Sample]:
        now = self.clock.now()
        status = await self.upnp.status()
        if status is None:
            return []
        samples = [
            Sample("wan_uptime_seconds", "wan", "seconds", now, float(status.uptime_seconds)),
            Sample(
                "wan_connected",
                "wan",
                "boolean",
                now,
                float(status.connection_status == "Connected"),
            ),
        ]
        if self.wan_sink is not None:
            wan_result = self.wan_sink(status)
            if wan_result is not None:
                await wan_result
        if (
            self.wan_sink is None
            and self.event_sink is not None
            and self.previous is not None
            and status.uptime_seconds < self.previous.uptime_seconds
        ):
            reconnect_result = self.event_sink(
                Event(
                    now,
                    "wan_reconnect",
                    "warning",
                    "Router reported a WAN reconnect",
                    {
                        "previous_uptime": self.previous.uptime_seconds,
                        "uptime": status.uptime_seconds,
                        "last_connection_error": status.last_connection_error,
                    },
                )
            )
            await _maybe_await(reconnect_result)
        if (
            self.event_sink is not None
            and self.previous is not None
            and status.external_ip != self.previous.external_ip
            and status.external_ip is not None
        ):
            event_result = self.event_sink(
                Event(
                    now,
                    "wan_external_ip_change",
                    "warning",
                    "Router reported an external IP change",
                    {
                        "previous_external_ip": self.previous.external_ip,
                        "external_ip": status.external_ip,
                    },
                )
            )
            await _maybe_await(event_result)
        self.previous = status
        return samples

    async def close(self) -> None:
        close = getattr(self.upnp, "close", None)
        if close is not None:
            result = close()
            if isinstance(result, Awaitable):
                await result


async def _maybe_await(value: int | Awaitable[int]) -> int:
    if isinstance(value, Awaitable):
        return await value
    return value
