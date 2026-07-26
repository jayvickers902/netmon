"""Small domain values shared by collectors and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


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


@dataclass(frozen=True, slots=True)
class HealthObservation:
    """One collector's verdict for one target at one instant."""

    target: str
    protocol: str
    success: bool | None
    ts: datetime
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Event:
    ts: datetime
    kind: str
    severity: str
    message: str
    details: dict[str, Any]
    device_id: int | None = None
