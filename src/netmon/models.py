"""Small domain values shared by collectors and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Sample:
    metric_kind: str
    target: str
    unit: str
    ts: datetime
    value: float


@dataclass(frozen=True, slots=True)
class DeviceObservation:
    mac: str
    ip: str
    vendor: str | None
    hostname: str | None
