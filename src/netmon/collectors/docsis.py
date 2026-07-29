"""Scheduled DOCSIS and Hitron modem status collection."""

from __future__ import annotations

from collections.abc import Awaitable

from netmon.collectors.base import Clock, Collector
from netmon.collectors.health import EventSink
from netmon.config import HitronConfig
from netmon.investigation import InvestigationState
from netmon.models import Event, Sample
from netmon.probes.hitron import HitronProbe


class DocsisCollector(Collector):
    def __init__(
        self,
        hitron: HitronProbe,
        clock: Clock,
        config: HitronConfig,
        event_sink: EventSink | None = None,
        investigation: InvestigationState | None = None,
        burst_interval_seconds: float | None = None,
    ) -> None:
        self.hitron = hitron
        self.clock = clock
        self.config = config
        self.event_sink = event_sink
        self.investigation = investigation
        self.burst_interval_seconds = burst_interval_seconds
        self.previous_octets: int | None = None
        self.previous_octets_monotonic: float | None = None
        self.previous_uptime: int | None = None

    @property
    def name(self) -> str:
        return "docsis"

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
        monotonic = self.clock.monotonic()
        status = await self.hitron.status()
        if status is None:
            return []

        rebooted = (
            self.previous_uptime is not None
            and status.uptime_seconds is not None
            and status.uptime_seconds < self.previous_uptime
        )
        if rebooted and self.event_sink is not None:
            event_result = self.event_sink(
                Event(
                    now,
                    "modem_reboot",
                    "warning",
                    "Modem reported a reboot",
                    {
                        "previous_uptime": self.previous_uptime,
                        "uptime": status.uptime_seconds,
                    },
                )
            )
            await _maybe_await(event_result)

        samples: list[Sample] = []
        octets = status.downstream_octets_total
        if (
            not rebooted
            and octets is not None
            and self.previous_octets is not None
            and self.previous_octets_monotonic is not None
        ):
            elapsed = monotonic - self.previous_octets_monotonic
            delta = octets - self.previous_octets
            if delta >= 0 and elapsed > 0:
                samples.append(
                    Sample(
                        "wan_downstream_rate",
                        "wan",
                        "bytes_per_second",
                        now,
                        delta / elapsed,
                        "hitron",
                    )
                )

        for downstream_channel in status.downstream:
            target = f"ch:{downstream_channel.channel_id}"
            if downstream_channel.snr_db is not None:
                samples.append(
                    Sample("docsis_ds_snr", target, "db", now, downstream_channel.snr_db, "hitron")
                )
            if downstream_channel.power_dbmv is not None:
                samples.append(
                    Sample(
                        "docsis_ds_power",
                        target,
                        "dbmv",
                        now,
                        downstream_channel.power_dbmv,
                        "hitron",
                    )
                )
            if downstream_channel.uncorrectable is not None:
                samples.append(
                    Sample(
                        "docsis_ds_uncorrectables",
                        target,
                        "count",
                        now,
                        float(downstream_channel.uncorrectable),
                        "hitron",
                    )
                )

        for upstream_channel in status.upstream:
            if upstream_channel.power_dbmv is not None:
                samples.append(
                    Sample(
                        "docsis_us_power",
                        f"ch:{upstream_channel.channel_id}",
                        "dbmv",
                        now,
                        upstream_channel.power_dbmv,
                        "hitron",
                    )
                )

        if status.uptime_seconds is not None:
            samples.append(
                Sample(
                    "modem_uptime_seconds",
                    "modem",
                    "seconds",
                    now,
                    float(status.uptime_seconds),
                    "hitron",
                )
            )
        if status.link_speed_mbps is not None:
            samples.append(
                Sample(
                    "modem_link_speed",
                    "modem",
                    "mbps",
                    now,
                    float(status.link_speed_mbps),
                    "hitron",
                )
            )
        if status.registered is not None:
            samples.append(
                Sample(
                    "modem_registered",
                    "modem",
                    "boolean",
                    now,
                    float(status.registered),
                    "hitron",
                )
            )

        if rebooted:
            self.previous_octets = octets
            self.previous_octets_monotonic = monotonic if octets is not None else None
        elif octets is not None:
            self.previous_octets = octets
            self.previous_octets_monotonic = monotonic
        if status.uptime_seconds is not None:
            self.previous_uptime = status.uptime_seconds

        return samples

    async def close(self) -> None:
        close = getattr(self.hitron, "close", None)
        if close is not None:
            result = close()
            if isinstance(result, Awaitable):
                await result


async def _maybe_await(value: int | Awaitable[int]) -> int:
    if isinstance(value, Awaitable):
        return await value
    return value
