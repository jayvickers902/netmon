from __future__ import annotations

from datetime import UTC, datetime, timedelta

from netmon.config import OutageConfig
from netmon.models import HealthObservation
from netmon.outage import OutageCoordinator, OutageStateMachine
from netmon.probes.traceroute import TracerouteHop
from netmon.probes.upnp import WanStatus

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def observation(protocol: str, target: str, success: bool, seconds: int) -> HealthObservation:
    return HealthObservation(target, protocol, success, BASE + timedelta(seconds=seconds), {})


def test_hysteresis_uses_first_failure_timestamp_and_records_duration():
    machine = OutageStateMachine(failures_to_open=3, successes_to_close=2)
    assert machine.record(observation("icmp", "gateway", False, 0)) == []
    assert machine.record(observation("icmp", "gateway", False, 1)) == []
    events = machine.record(observation("icmp", "gateway", False, 2))
    assert events[0].kind == "outage_start"
    assert events[0].ts == BASE
    assert machine.record(observation("icmp", "gateway", True, 3)) == []
    end = machine.record(observation("icmp", "gateway", True, 4))[0]
    assert end.kind == "outage_end"
    assert end.details["duration_seconds"] == 4


def test_failure_streak_recovery_one_short_of_threshold_does_not_open():
    machine = OutageStateMachine(failures_to_open=3, successes_to_close=2)
    machine.record(observation("icmp", "gateway", False, 0))
    machine.record(observation("icmp", "gateway", False, 1))
    machine.record(observation("icmp", "gateway", True, 2))
    assert machine.record(observation("icmp", "gateway", False, 3)) == []
    assert machine.status().targets["icmp:gateway"]["open"] is False


def test_flapping_at_threshold_is_hysteretic():
    machine = OutageStateMachine(failures_to_open=2, successes_to_close=2)
    assert machine.record(observation("icmp", "gateway", False, 0)) == []
    assert machine.record(observation("icmp", "gateway", True, 1)) == []
    assert machine.record(observation("icmp", "gateway", False, 2)) == []
    assert machine.record(observation("icmp", "gateway", True, 3)) == []
    assert machine.status().verdict == "healthy"


def test_classification_matrix():
    machine = OutageStateMachine(failures_to_open=1, successes_to_close=1)
    machine.record(observation("icmp", "gateway", True, 0))
    machine.record(observation("icmp", "1.1.1.1", False, 1))
    assert machine.status().verdict == "wan_isp_outage"

    machine = OutageStateMachine(failures_to_open=1, successes_to_close=1)
    machine.record(observation("icmp", "gateway", False, 0))
    assert machine.status().verdict == "local_problem"

    machine = OutageStateMachine(failures_to_open=1, successes_to_close=1)
    machine.record(observation("icmp", "gateway", True, 0))
    machine.record(observation("icmp", "1.1.1.1", False, 1))
    machine.record(observation("icmp", "8.8.8.8", True, 1))
    assert machine.status().verdict == "partial_path"

    machine = OutageStateMachine(failures_to_open=1, successes_to_close=1)
    machine.record(observation("icmp", "1.1.1.1", True, 0))
    dns = HealthObservation(
        "1.1.1.1:example.com",
        "dns",
        False,
        BASE + timedelta(seconds=1),
        {"resolver": "1.1.1.1"},
    )
    machine.record(dns)
    assert machine.status().verdict == "dns_specific_failure"


def test_wan_uptime_reset_is_advisory_event():
    machine = OutageStateMachine()
    machine.record_wan(WanStatus("Connected", "ERROR_NONE", 100, "198.51.100.5"), BASE)
    events = machine.record_wan(
        WanStatus("Connected", "ERROR_ISP_DISCONNECTED", 5, "198.51.100.5"),
        BASE + timedelta(seconds=1),
    )
    assert events[0].kind == "wan_reconnect"
    assert events[0].details["previous_uptime"] == 100
    assert machine.status().wan["reconnected_at"] == (BASE + timedelta(seconds=1)).isoformat()


def test_coordinator_attaches_background_traceroute(frozen_clock):
    events = []
    updates = []

    class Trace:
        async def trace(self, target, max_hops, timeout_seconds):
            assert target == "1.1.1.1"
            assert max_hops == 30
            assert timeout_seconds == 30
            return [TracerouteHop(1, "gateway", "192.168.4.1", (1.0,), False)]

    async def run():
        coordinator = OutageCoordinator(
            OutageStateMachine(failures_to_open=1, successes_to_close=1),
            frozen_clock,
            lambda event: events.append(event) or 7,
            OutageConfig(failures_to_open=1, successes_to_close=1),
            traceroute=Trace(),
            event_update=lambda event_id, details: updates.append((event_id, details)),
        )
        await coordinator.observe(observation("icmp", "1.1.1.1", False, 0))
        tasks = tuple(coordinator._trace_tasks)
        if tasks:
            await asyncio.gather(*tasks)

    import asyncio

    asyncio.run(run())
    assert events[0].kind == "outage_start"
    assert updates[0][0] == 7
    assert updates[0][1]["traceroute"][0]["ip"] == "192.168.4.1"
