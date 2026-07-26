"""Shared collector callback types for connection-health observations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from netmon.models import Event, HealthObservation

HealthSink = Callable[[HealthObservation], Awaitable[None] | None]
EventSink = Callable[[Event], int | Awaitable[int]]
