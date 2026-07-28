"""Rate collector for NIC counters obtained from a managed remote host."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from netmon.collectors.base import Clock, Collector
from netmon.investigation import InvestigationState
from netmon.models import Sample
from netmon.probes.managed_host import (
    ManagedHostNicProbe,
    ManagedHostProbeError,
    RemoteNicCounters,
)


@dataclass(frozen=True, slots=True)
class _Snapshot:
    bytes_sent: int
    bytes_recv: int
    at: float


class ManagedHostNicCollector(Collector):
    """Derive aggregate remote-host NIC rates without retaining remote I/O state."""

    def __init__(
        self,
        probe: ManagedHostNicProbe,
        clock: Clock,
        host_name: str,
        interval_seconds: float,
        adapter_name: str | None = None,
        expected_mac: str | None = None,
        investigation: InvestigationState | None = None,
        burst_interval_seconds: float | None = None,
    ) -> None:
        self.probe = probe
        self.clock = clock
        self.host_name = host_name
        self.interval_seconds = interval_seconds
        self.adapter_name = adapter_name
        self.expected_mac = expected_mac
        self.investigation = investigation
        self.burst_interval_seconds = burst_interval_seconds
        self._previous: dict[str, _Snapshot] = {}

    @property
    def name(self) -> str:
        return f"managed_host_nic:{self.host_name}"

    @property
    def interval(self) -> float:
        if (
            self.investigation is not None
            and self.investigation.active
            and self.burst_interval_seconds is not None
        ):
            return self.burst_interval_seconds
        return self.interval_seconds

    async def collect(self) -> list[Sample]:
        now = self.clock.now()
        try:
            counters = await self.probe.counters()
        except ManagedHostProbeError:
            return [self._availability(now, 0.0)]

        selected = [counter for counter in counters if self._matches(counter)]
        if not selected:
            return [self._availability(now, 0.0)]

        monotonic = self.clock.monotonic()
        sent_rate = 0.0
        recv_rate = 0.0
        has_sent_rate = False
        has_recv_rate = False
        for counter in selected:
            key = _normalise_mac(counter.mac)
            previous = self._previous.get(key)
            self._previous[key] = _Snapshot(counter.bytes_sent, counter.bytes_recv, monotonic)
            if previous is None:
                continue
            elapsed = monotonic - previous.at
            if elapsed <= 0:
                continue
            # A reboot or NIC reset must establish a new baseline, never a false spike.
            if counter.bytes_sent >= previous.bytes_sent:
                sent_rate += (counter.bytes_sent - previous.bytes_sent) / elapsed
                has_sent_rate = True
            if counter.bytes_recv >= previous.bytes_recv:
                recv_rate += (counter.bytes_recv - previous.bytes_recv) / elapsed
                has_recv_rate = True

        samples = [self._availability(now, 1.0)]
        if has_sent_rate:
            samples.append(
                Sample(
                    "managed_host_nic_bytes_sent_rate",
                    self.host_name,
                    "bytes_per_second",
                    now,
                    sent_rate,
                    "managed_host_ssh",
                )
            )
        if has_recv_rate:
            samples.append(
                Sample(
                    "managed_host_nic_bytes_recv_rate",
                    self.host_name,
                    "bytes_per_second",
                    now,
                    recv_rate,
                    "managed_host_ssh",
                )
            )
        return samples

    def _matches(self, counter: RemoteNicCounters) -> bool:
        if (
            self.adapter_name is not None
            and counter.adapter_name.casefold() != self.adapter_name.casefold()
        ):
            return False
        return self.expected_mac is None or _normalise_mac(counter.mac) == _normalise_mac(
            self.expected_mac
        )

    def _availability(self, now: datetime, value: float) -> Sample:
        return Sample(
            "managed_host_available", self.host_name, "boolean", now, value, "managed_host_ssh"
        )


def _normalise_mac(mac: str) -> str:
    return "".join(character for character in mac.casefold() if character in "0123456789abcdef")
