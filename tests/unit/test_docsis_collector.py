from __future__ import annotations

from datetime import timedelta

import pytest

from netmon.collectors.base import FrozenClock
from netmon.collectors.docsis import DocsisCollector
from netmon.config import HitronConfig
from netmon.investigation import InvestigationState
from netmon.models import Event
from netmon.probes.hitron import (
    DocsisEventLogEntry,
    DocsisStatus,
    DownstreamChannel,
    UpstreamChannel,
)


class FakeHitronProbe:
    def __init__(
        self,
        statuses: list[DocsisStatus | None],
        clock: FrozenClock | None = None,
        latency_seconds: float = 0.0,
    ) -> None:
        self._statuses = iter(statuses)
        self._clock = clock
        self._latency_seconds = latency_seconds

    async def status(self) -> DocsisStatus | None:
        if self._clock is not None:
            self._clock.advance(self._latency_seconds)
        return next(self._statuses)

    async def close(self) -> None:
        return None


def status(
    *,
    octets: int | None = None,
    uptime: int | None = None,
    link_speed: int | None = None,
    registered: bool | None = None,
    downstream: tuple[DownstreamChannel, ...] = (),
    upstream: tuple[UpstreamChannel, ...] = (),
    event_log: tuple[DocsisEventLogEntry, ...] = (),
    event_log_available: bool = False,
) -> DocsisStatus:
    return DocsisStatus(
        downstream,
        upstream,
        octets,
        uptime,
        link_speed,
        True,
        "7.2.1",
        registered,
        event_log,
        event_log_available,
    )


def make_collector(
    statuses: list[DocsisStatus | None],
    frozen_clock: FrozenClock,
    event_sink=None,
    *,
    investigation: InvestigationState | None = None,
    burst_interval_seconds: float | None = None,
    latency_seconds: float = 0.0,
) -> DocsisCollector:
    return DocsisCollector(
        FakeHitronProbe(statuses, frozen_clock, latency_seconds),
        frozen_clock,
        HitronConfig(),
        event_sink,
        investigation,
        burst_interval_seconds,
    )


@pytest.mark.asyncio
async def test_emits_contract_samples_from_status(frozen_clock: FrozenClock):
    collector = make_collector(
        [
            status(
                uptime=86400,
                link_speed=2500,
                registered=True,
                downstream=(
                    DownstreamChannel(1, 573000000, 39.5, -1.2, 12, 3, 100),
                    DownstreamChannel(2, 579000000, None, 0.4, None, 0, None),
                ),
                upstream=(UpstreamChannel(1, 30000000, 6400000, "64-QAM", 45.0),),
            )
        ],
        frozen_clock,
    )

    samples = await collector.collect()

    assert {
        (sample.metric_kind, sample.target, sample.unit, sample.source) for sample in samples
    } == {
        ("docsis_ds_snr", "ch:1", "db", "hitron"),
        ("docsis_ds_power", "ch:1", "dbmv", "hitron"),
        ("docsis_ds_corrected", "ch:1", "count", "hitron"),
        ("docsis_ds_uncorrectables", "ch:1", "count", "hitron"),
        ("docsis_ds_power", "ch:2", "dbmv", "hitron"),
        ("docsis_ds_uncorrectables", "ch:2", "count", "hitron"),
        ("docsis_us_power", "ch:1", "dbmv", "hitron"),
        ("modem_uptime_seconds", "modem", "seconds", "hitron"),
        ("modem_link_speed", "modem", "mbps", "hitron"),
        ("modem_registered", "modem", "boolean", "hitron"),
        ("modem_available", "modem", "boolean", "hitron"),
        ("modem_poll_latency_ms", "modem", "ms", "hitron"),
    }
    assert all(sample.metric_kind != "wan_downstream_rate" for sample in samples)
    assert (
        next(sample for sample in samples if sample.metric_kind == "modem_registered").value == 1.0
    )
    assert collector.name == "docsis"


@pytest.mark.asyncio
async def test_rate_uses_elapsed_clock_time_and_skips_first_poll(frozen_clock: FrozenClock):
    collector = make_collector(
        [status(octets=1_000), status(octets=1_600)],
        frozen_clock,
    )

    assert not [
        sample
        for sample in await collector.collect()
        if sample.metric_kind == "wan_downstream_rate"
    ]
    frozen_clock.advance(4)
    samples = await collector.collect()

    rate = next(sample for sample in samples if sample.metric_kind == "wan_downstream_rate")
    assert rate.value == 150.0
    assert rate.unit == "bytes_per_second"
    assert rate.target == "wan"
    assert rate.source == "hitron"


@pytest.mark.asyncio
async def test_negative_counter_delta_rebaselines_without_a_spike(frozen_clock: FrozenClock):
    collector = make_collector(
        [status(octets=1_000), status(octets=900), status(octets=1_300)],
        frozen_clock,
    )

    await collector.collect()
    frozen_clock.advance(2)
    assert not [
        sample
        for sample in await collector.collect()
        if sample.metric_kind == "wan_downstream_rate"
    ]
    frozen_clock.advance(2)
    samples = await collector.collect()

    assert (
        next(sample for sample in samples if sample.metric_kind == "wan_downstream_rate").value
        == 200.0
    )


@pytest.mark.asyncio
async def test_uptime_decrease_emits_reboot_event_and_discards_rate(
    frozen_clock: FrozenClock,
):
    events: list[Event] = []
    collector = make_collector(
        [status(octets=1_000, uptime=100), status(octets=1_300, uptime=90)],
        frozen_clock,
        events.append,
    )

    await collector.collect()
    frozen_clock.advance(5)
    samples = await collector.collect()

    assert not [sample for sample in samples if sample.metric_kind == "wan_downstream_rate"]
    assert len(events) == 1
    assert events[0].kind == "modem_reboot"
    assert events[0].severity == "warning"
    assert events[0].details == {"previous_uptime": 100, "uptime": 90}


@pytest.mark.asyncio
async def test_none_fields_emit_no_placeholder_samples(frozen_clock: FrozenClock):
    collector = make_collector(
        [
            status(
                downstream=(DownstreamChannel(1, 0, None, None, None, None, None),),
                upstream=(UpstreamChannel(1, 0, 0, None, None),),
            )
        ],
        frozen_clock,
    )

    samples = await collector.collect()
    assert {sample.metric_kind for sample in samples} == {
        "modem_available",
        "modem_poll_latency_ms",
    }


@pytest.mark.asyncio
async def test_unreachable_modem_is_missing_data(frozen_clock: FrozenClock):
    collector = make_collector([None], frozen_clock, latency_seconds=0.125)

    samples = await collector.collect()
    assert next(sample for sample in samples if sample.metric_kind == "modem_available").value == 0
    assert (
        next(sample for sample in samples if sample.metric_kind == "modem_poll_latency_ms").value
        == 125
    )


@pytest.mark.asyncio
async def test_error_rates_use_counter_deltas_and_include_ofdm(frozen_clock: FrozenClock):
    first = (
        DownstreamChannel(1, 573000000, 39.5, -1.2, 100, 10, 1_000),
        DownstreamChannel(1, 599600000, 40.0, 6.6, 1_000, 100, 2_000, "ofdm"),
    )
    second = (
        DownstreamChannel(1, 573000000, 39.5, -1.2, 130, 12, 1_500),
        DownstreamChannel(1, 599600000, 40.0, 6.6, 1_060, 106, 2_500, "ofdm"),
    )
    collector = make_collector(
        [status(downstream=first), status(downstream=second)],
        frozen_clock,
    )

    await collector.collect()
    frozen_clock.advance(10)
    samples = await collector.collect()

    rates = {
        (sample.metric_kind, sample.target): sample.value
        for sample in samples
        if sample.metric_kind.endswith("_rate")
    }
    assert rates == {
        ("docsis_ds_corrected_rate", "ch:1"): 3.0,
        ("docsis_ds_uncorrectable_rate", "ch:1"): 0.2,
        ("docsis_ds_corrected_rate", "ofdm:1"): 6.0,
        ("docsis_ds_uncorrectable_rate", "ofdm:1"): 0.6,
        ("docsis_corrected_rate", "modem"): 9.0,
        ("docsis_uncorrectable_rate", "modem"): pytest.approx(0.8),
    }


@pytest.mark.asyncio
async def test_event_log_baselines_then_emits_only_new_sanitized_events(
    frozen_clock: FrozenClock,
):
    old = DocsisEventLogEntry("07/28/26 08:43:34", "82000400", "critical", "T4 time out")
    new = DocsisEventLogEntry("07/28/26 09:39:49", "82000200", "critical", "T3 time-out")
    events: list[Event] = []
    collector = make_collector(
        [
            status(event_log=(old,), event_log_available=True),
            status(event_log=(new, old), event_log_available=True),
            status(event_log=(new, old), event_log_available=True),
        ],
        frozen_clock,
        events.append,
    )

    await collector.collect()
    frozen_clock.advance(30)
    await collector.collect()
    frozen_clock.advance(30)
    await collector.collect()

    assert len(events) == 1
    assert events[0].kind == "docsis_event"
    assert events[0].severity == "critical"
    assert events[0].message == "DOCSIS: T3 time-out"
    assert events[0].details == {
        "modem_time": "07/28/26 09:39:49",
        "type_code": "82000200",
        "priority": "critical",
    }


def test_burst_interval_applies_during_investigation(frozen_clock: FrozenClock):
    investigation = InvestigationState(frozen_clock)
    started = frozen_clock.now()
    investigation.start(1, started, started + timedelta(seconds=60))
    collector = make_collector(
        [status()],
        frozen_clock,
        investigation=investigation,
        burst_interval_seconds=3.0,
    )

    assert collector.interval == 3.0
    investigation.stop()
    assert collector.interval == 30.0
