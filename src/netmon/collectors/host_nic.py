"""Host-local per-NIC traffic-rate collector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from netmon.collectors.base import Clock, Collector
from netmon.config import HostNicConfig
from netmon.models import Sample
from netmon.probes.host_nic import HostNicProbe, NicCounters, ProcessCounters


@dataclass(frozen=True, slots=True)
class _NicSnapshot:
    counters: NicCounters
    at: float


@dataclass(frozen=True, slots=True)
class _ProcessSnapshot:
    counters: ProcessCounters
    at: float


class HostNicCollector(Collector):
    def __init__(self, probe: HostNicProbe, clock: Clock, config: HostNicConfig) -> None:
        self.probe = probe
        self.clock = clock
        self.config = config
        self._previous_nics: dict[str, _NicSnapshot] = {}
        self._previous_processes: dict[int, _ProcessSnapshot] = {}

    @property
    def name(self) -> str:
        return "host_nic"

    @property
    def interval(self) -> float:
        return self.config.interval_seconds

    async def collect(self) -> list[Sample]:
        now = self.clock.now()
        monotonic = self.clock.monotonic()
        samples: list[Sample] = []
        current = self.probe.counters()
        for name, counters in current.items():
            previous = self._previous_nics.get(name)
            self._previous_nics[name] = _NicSnapshot(counters, monotonic)
            if previous is None:
                continue
            elapsed = monotonic - previous.at
            if elapsed <= 0:
                continue
            sent = _rate(counters.bytes_sent, previous.counters.bytes_sent, elapsed)
            received = _rate(counters.bytes_recv, previous.counters.bytes_recv, elapsed)
            if sent is not None:
                samples.append(
                    Sample(
                        "host_nic_bytes_sent_rate",
                        name,
                        "bytes_per_second",
                        now,
                        sent,
                        "host_nic",
                    )
                )
            if received is not None:
                samples.append(
                    Sample(
                        "host_nic_bytes_recv_rate",
                        name,
                        "bytes_per_second",
                        now,
                        received,
                        "host_nic",
                    )
                )

        if self.config.top_processes:
            samples.extend(self._process_samples(self.probe.process_counters(), monotonic, now))
        return samples

    def _process_samples(
        self, current: list[ProcessCounters], monotonic: float, now: datetime
    ) -> list[Sample]:
        samples: list[Sample] = []
        ranked: list[tuple[float, ProcessCounters, float, float]] = []
        for counters in current:
            previous = self._previous_processes.get(counters.pid)
            self._previous_processes[counters.pid] = _ProcessSnapshot(counters, monotonic)
            if previous is None or monotonic <= previous.at:
                continue
            sent = _rate(counters.bytes_sent, previous.counters.bytes_sent, monotonic - previous.at)
            received = _rate(
                counters.bytes_recv, previous.counters.bytes_recv, monotonic - previous.at
            )
            if sent is None or received is None:
                continue
            ranked.append((sent + received, counters, sent, received))
        for _, counters, sent, received in sorted(ranked, reverse=True, key=lambda item: item[0])[
            : self.config.top_processes
        ]:
            target = f"process:{counters.pid}:{counters.name}"
            samples.extend(
                (
                    Sample(
                        "host_process_bytes_sent_rate",
                        target,
                        "bytes_per_second",
                        now,
                        sent,
                        "host_nic",
                    ),
                    Sample(
                        "host_process_bytes_recv_rate",
                        target,
                        "bytes_per_second",
                        now,
                        received,
                        "host_nic",
                    ),
                )
            )
        return samples


def _rate(current: int, previous: int, elapsed: float) -> float | None:
    # Adapter resets and reboot counter resets are expected; discarding is safer than a spike.
    if current < previous:
        return None
    return (current - previous) / elapsed
