"""Scheduled DOCSIS and Hitron modem status collection."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable
from datetime import datetime

from netmon.collectors.base import Clock, Collector
from netmon.collectors.health import EventSink
from netmon.config import HitronConfig
from netmon.investigation import InvestigationState
from netmon.models import Event, Sample
from netmon.probes.hitron import DocsisEventLogEntry, HitronProbe

_SEVERITIES = frozenset({"info", "warning", "error", "critical"})
_EventKey = tuple[str, str, str, str]


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
        self.previous_errors: dict[str, tuple[int | None, int | None]] = {}
        self.previous_errors_monotonic: float | None = None
        self.events_initialized = False
        self.seen_event_order: deque[_EventKey] = deque()
        self.seen_events: set[_EventKey] = set()

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
        started = self.clock.monotonic()
        status = await self.hitron.status()
        monotonic = self.clock.monotonic()
        samples = [
            Sample(
                "modem_available",
                "modem",
                "boolean",
                now,
                float(status is not None),
                "hitron",
            ),
            Sample(
                "modem_poll_latency_ms",
                "modem",
                "ms",
                now,
                max(0.0, monotonic - started) * 1000.0,
                "hitron",
            ),
        ]
        if status is None:
            return samples

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

        await self._record_new_docsis_events(status.event_log, status.event_log_available, now)

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

        aggregate_corrected_rate = 0.0
        aggregate_uncorrectable_rate = 0.0
        has_corrected_rate = False
        has_uncorrectable_rate = False
        for downstream_channel in status.downstream:
            target = (
                f"ofdm:{downstream_channel.channel_id}"
                if downstream_channel.channel_type == "ofdm"
                else f"ch:{downstream_channel.channel_id}"
            )
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
            if downstream_channel.corrected is not None:
                samples.append(
                    Sample(
                        "docsis_ds_corrected",
                        target,
                        "count",
                        now,
                        float(downstream_channel.corrected),
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
            previous_errors = self.previous_errors.get(target)
            elapsed = (
                monotonic - self.previous_errors_monotonic
                if self.previous_errors_monotonic is not None
                else 0.0
            )
            if not rebooted and previous_errors is not None and elapsed > 0:
                rate_samples = _error_rate_samples(
                    now,
                    target,
                    elapsed,
                    downstream_channel.corrected,
                    downstream_channel.uncorrectable,
                    previous_errors,
                )
                samples.extend(rate_samples)
                for rate_sample in rate_samples:
                    if rate_sample.metric_kind == "docsis_ds_corrected_rate":
                        aggregate_corrected_rate += rate_sample.value
                        has_corrected_rate = True
                    else:
                        aggregate_uncorrectable_rate += rate_sample.value
                        has_uncorrectable_rate = True

        if has_corrected_rate:
            samples.append(
                Sample(
                    "docsis_corrected_rate",
                    "modem",
                    "errors_per_second",
                    now,
                    aggregate_corrected_rate,
                    "hitron",
                )
            )
        if has_uncorrectable_rate:
            samples.append(
                Sample(
                    "docsis_uncorrectable_rate",
                    "modem",
                    "errors_per_second",
                    now,
                    aggregate_uncorrectable_rate,
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
        self.previous_errors = {
            (
                f"ofdm:{channel.channel_id}"
                if channel.channel_type == "ofdm"
                else f"ch:{channel.channel_id}"
            ): (channel.corrected, channel.uncorrectable)
            for channel in status.downstream
        }
        self.previous_errors_monotonic = monotonic

        return samples

    async def _record_new_docsis_events(
        self,
        entries: tuple[DocsisEventLogEntry, ...],
        available: bool,
        now: datetime,
    ) -> None:
        if not available:
            return
        if not self.events_initialized:
            for entry in entries:
                self._remember_event(_event_key(entry))
            self.events_initialized = True
            return
        for entry in reversed(entries):
            key = _event_key(entry)
            if key in self.seen_events:
                continue
            self._remember_event(key)
            if self.event_sink is None:
                continue
            severity = entry.priority if entry.priority in _SEVERITIES else "info"
            event_result = self.event_sink(
                Event(
                    now,
                    "docsis_event",
                    severity,
                    f"DOCSIS: {entry.message}",
                    {
                        "modem_time": entry.modem_time,
                        "type_code": entry.type_code,
                        "priority": entry.priority,
                    },
                )
            )
            await _maybe_await(event_result)

    def _remember_event(self, key: _EventKey) -> None:
        if key in self.seen_events:
            return
        if len(self.seen_event_order) >= 256:
            expired = self.seen_event_order.popleft()
            self.seen_events.remove(expired)
        self.seen_event_order.append(key)
        self.seen_events.add(key)

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


def _event_key(entry: DocsisEventLogEntry) -> _EventKey:
    return entry.modem_time, entry.type_code, entry.priority, entry.message


def _error_rate_samples(
    now: datetime,
    target: str,
    elapsed: float,
    corrected: int | None,
    uncorrectable: int | None,
    previous: tuple[int | None, int | None],
) -> list[Sample]:
    samples: list[Sample] = []
    for metric_kind, current, prior in (
        ("docsis_ds_corrected_rate", corrected, previous[0]),
        ("docsis_ds_uncorrectable_rate", uncorrectable, previous[1]),
    ):
        if current is None or prior is None:
            continue
        delta = current - prior
        if delta >= 0:
            samples.append(
                Sample(
                    metric_kind,
                    target,
                    "errors_per_second",
                    now,
                    delta / elapsed,
                    "hitron",
                )
            )
    return samples
