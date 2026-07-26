"""Pure connection-health state and its non-blocking event coordinator."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from netmon.collectors.base import Clock
from netmon.config import OutageConfig
from netmon.models import Event, HealthObservation
from netmon.probes.traceroute import TracerouteHop, TracerouteProbe
from netmon.probes.upnp import WanStatus


@dataclass(slots=True)
class TargetState:
    protocol: str
    target: str
    failures: int = 0
    successes: int = 0
    failure_started_at: datetime | None = None
    opened_at: datetime | None = None
    last_success: bool | None = None

    @property
    def open(self) -> bool:
        return self.opened_at is not None


@dataclass(frozen=True, slots=True)
class AggregateStatus:
    verdict: str
    open: bool
    since: datetime | None
    duration_seconds: float | None
    targets: dict[str, dict[str, Any]]
    wan: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "open": self.open,
            "since": self.since.isoformat() if self.since else None,
            "duration_seconds": self.duration_seconds,
            "targets": self.targets,
            "wan": self.wan,
        }


class OutageStateMachine:
    """Hysteresis per target, followed by a conservative aggregate verdict."""

    def __init__(self, failures_to_open: int = 3, successes_to_close: int = 2) -> None:
        self.failures_to_open = failures_to_open
        self.successes_to_close = successes_to_close
        self._states: dict[str, TargetState] = {}
        self._latest: dict[str, HealthObservation] = {}
        self._aggregate_open = False
        self._aggregate_since: datetime | None = None
        self._aggregate_verdict = "unknown"
        self._wan: WanStatus | None = None
        self._wan_reconnect_at: datetime | None = None
        self._wan_reconnect_error: str | None = None

    def record(self, observation: HealthObservation) -> list[Event]:
        key = f"{observation.protocol}:{observation.target}"
        state = self._states.setdefault(key, TargetState(observation.protocol, observation.target))
        self._latest[key] = observation
        if observation.success is True:
            state.last_success = True
            state.failures = 0
            if state.open:
                state.successes += 1
                if state.successes >= self.successes_to_close:
                    state.opened_at = None
                    state.successes = 0
                    state.failure_started_at = None
            else:
                state.successes = min(state.successes + 1, self.successes_to_close)
                state.failure_started_at = None
        elif observation.success is False:
            state.last_success = False
            state.successes = 0
            if not state.open:
                if state.failures == 0:
                    state.failure_started_at = observation.ts
                state.failures += 1
                if state.failures >= self.failures_to_open:
                    state.opened_at = state.failure_started_at
        return self._aggregate_events(observation.ts)

    def record_wan(self, status: WanStatus, ts: datetime) -> list[Event]:
        events: list[Event] = []
        if self._wan is not None and status.uptime_seconds < self._wan.uptime_seconds:
            self._wan_reconnect_at = ts
            self._wan_reconnect_error = status.last_connection_error
            events.append(
                Event(
                    ts=ts,
                    kind="wan_reconnect",
                    severity="warning",
                    message="Router reported a WAN reconnect",
                    details={
                        "previous_uptime": self._wan.uptime_seconds,
                        "uptime": status.uptime_seconds,
                        "last_connection_error": status.last_connection_error,
                    },
                )
            )
        self._wan = status
        return events

    def status(self, now: datetime | None = None) -> AggregateStatus:
        duration = None
        if self._aggregate_since is not None and now is not None:
            duration = max(0.0, (now - self._aggregate_since).total_seconds())
        target_status = {
            key: {
                "protocol": state.protocol,
                "target": state.target,
                "open": state.open,
                "failures": state.failures,
                "successes": state.successes,
                "last_success": state.last_success,
            }
            for key, state in self._states.items()
        }
        wan = None
        if self._wan is not None:
            wan = {
                "connection_status": self._wan.connection_status,
                "last_connection_error": self._wan.last_connection_error,
                "uptime_seconds": self._wan.uptime_seconds,
                "external_ip": self._wan.external_ip,
                "reconnected_at": (
                    self._wan_reconnect_at.isoformat() if self._wan_reconnect_at else None
                ),
            }
        return AggregateStatus(
            self._aggregate_verdict,
            self._aggregate_open,
            self._aggregate_since,
            duration,
            target_status,
            wan,
        )

    def _aggregate_events(self, ts: datetime) -> list[Event]:
        verdict = self._classify()
        events: list[Event] = []
        if not self._aggregate_open and verdict not in {"healthy", "unknown"}:
            open_states = [state for state in self._states.values() if state.open]
            since = min((state.opened_at for state in open_states if state.opened_at), default=ts)
            self._aggregate_open = True
            self._aggregate_since = since
            events.append(
                Event(
                    ts=since,
                    kind="outage_start",
                    severity="error",
                    message=f"Connection health degraded: {verdict}",
                    details={
                        "classification": verdict,
                        "target": self._trace_target(),
                        "detected_at": ts.isoformat(),
                    },
                )
            )
        elif self._aggregate_open and verdict in {"healthy", "unknown"}:
            since = self._aggregate_since or ts
            events.append(
                Event(
                    ts=ts,
                    kind="outage_end",
                    severity="info",
                    message="Connection health recovered",
                    details={
                        "classification": self._aggregate_verdict,
                        "started_at": since.isoformat(),
                        "duration_seconds": max(0.0, (ts - since).total_seconds()),
                    },
                )
            )
            self._aggregate_open = False
            self._aggregate_since = None
        self._aggregate_verdict = verdict
        return events

    def _classify(self) -> str:
        gateway = self._state_for("icmp", "gateway")
        open_states = [state for state in self._states.values() if state.open]
        if not open_states:
            return "healthy" if self._latest else "unknown"
        if gateway is not None and gateway.open:
            return "local_problem"

        dns_failures = [
            item
            for key, item in self._latest.items()
            if item.protocol == "dns"
            and item.success is False
            and self._icmp_resolver_ok(item)
            and self._states.get(key, TargetState("", "")).open
        ]
        if dns_failures:
            return "dns_specific_failure"

        gateway_observation = self._latest.get("icmp:gateway")
        if gateway_observation is None or gateway_observation.success is not True:
            return "unknown"

        external = [
            state
            for state in open_states
            if state.protocol in {"icmp", "http"} and state.target != "gateway"
        ]
        if external:
            latest_external = [
                item
                for item in self._latest.values()
                if item.protocol in {"icmp", "http"} and item.target != "gateway"
            ]
            if latest_external and all(item.success is False for item in latest_external):
                return "wan_isp_outage"
            return "partial_path"
        return "partial_path"

    def _icmp_resolver_ok(self, dns_observation: HealthObservation) -> bool:
        resolver = str(dns_observation.details.get("resolver", ""))
        observation = self._latest.get(f"icmp:{resolver}")
        return observation is not None and observation.success is True

    def _state_for(self, protocol: str, target: str) -> TargetState | None:
        return self._states.get(f"{protocol}:{target}")

    def _trace_target(self) -> str:
        gateway = self._state_for("icmp", "gateway")
        if gateway is not None and gateway.open:
            observation = self._latest.get("icmp:gateway")
            if observation is not None:
                return str(observation.details.get("resolved_target", gateway.target))
            return gateway.target
        for state in self._states.values():
            if state.open and state.protocol in {"icmp", "http"}:
                return state.target
        return "1.1.1.1"


EventSink = Callable[[Event], int | Awaitable[int]]
EventUpdate = Callable[[int, dict[str, Any]], Awaitable[None] | None]


class OutageCoordinator:
    """Connect pure transitions to persistence and optional background diagnostics."""

    def __init__(
        self,
        machine: OutageStateMachine,
        clock: Clock,
        event_sink: EventSink,
        config: OutageConfig,
        traceroute: TracerouteProbe | None = None,
        event_update: EventUpdate | None = None,
    ) -> None:
        self.machine = machine
        self.clock = clock
        self.event_sink = event_sink
        self.config = config
        self.traceroute = traceroute
        self.event_update = event_update
        self._trace_tasks: set[asyncio.Task[None]] = set()

    async def observe(self, observation: HealthObservation) -> None:
        await self._emit(self.machine.record(observation))

    async def wan(self, status: WanStatus) -> None:
        await self._emit(self.machine.record_wan(status, self.clock.now()))

    def status(self) -> dict[str, Any]:
        return self.machine.status(self.clock.now()).as_dict()

    async def _emit(self, events: list[Event]) -> None:
        for event in events:
            event_id = await _maybe_await(self.event_sink(event))
            if (
                event.kind == "outage_start"
                and self.config.traceroute_on_open
                and self.traceroute is not None
                and self.event_update is not None
            ):
                task = asyncio.create_task(self._capture(event_id, event))
                self._trace_tasks.add(task)
                task.add_done_callback(self._trace_tasks.discard)

    async def _capture(self, event_id: int, event: Event) -> None:
        target = str(event.details.get("target", "1.1.1.1"))
        hops: list[TracerouteHop] = []
        try:
            assert self.traceroute is not None
            hops = await self.traceroute.trace(
                target,
                self.config.traceroute_max_hops,
                self.config.traceroute_timeout_seconds,
            )
        except Exception:
            hops = []
        details = dict(event.details)
        details["traceroute"] = [asdict(hop) for hop in hops]
        assert self.event_update is not None
        await _maybe_await(self.event_update(event_id, details))


async def _maybe_await(value: Any) -> Any:
    if isinstance(value, Awaitable):
        return await value
    return value
