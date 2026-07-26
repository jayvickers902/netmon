"""Host-local network counter probe backed by psutil."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import psutil


@dataclass(frozen=True, slots=True)
class NicCounters:
    bytes_sent: int
    bytes_recv: int


@dataclass(frozen=True, slots=True)
class ProcessCounters:
    pid: int
    name: str
    bytes_sent: int
    bytes_recv: int


class HostNicProbe(Protocol):
    def counters(self) -> dict[str, NicCounters]: ...

    def process_counters(self) -> list[ProcessCounters]: ...


class PsutilHostNicProbe:
    def counters(self) -> dict[str, NicCounters]:
        return {
            name: NicCounters(value.bytes_sent, value.bytes_recv)
            for name, value in psutil.net_io_counters(pernic=True).items()
        }

    def process_counters(self) -> list[ProcessCounters]:
        # psutil exposes socket ownership on Windows, but not per-process byte counters.
        # Returning an explicit empty result avoids pretending that connection counts are traffic.
        return []
