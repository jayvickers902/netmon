from __future__ import annotations

import pytest
from pydantic import ValidationError

from netmon.config import InvestigationConfig, NetmonSettings


def test_defaults_match_documented_values() -> None:
    config = InvestigationConfig()

    assert config.enabled is True
    assert config.default_duration_seconds == 900.0
    assert config.max_duration_seconds == 3600.0
    assert config.uplink_mbps == 10.0
    assert config.downlink_mbps == 500.0
    assert config.saturation_fraction == 0.8
    assert config.latency_degraded_ms == 50.0
    assert config.gateway_degraded_ms == 30.0
    assert config.loss_degraded_fraction == 0.05
    assert config.bucket_seconds == 10.0
    assert config.suppress_speedtest is True
    assert config.auto_arm is False
    assert config.auto_arm_duration_seconds == 600.0
    assert config.auto_arm_cooldown_seconds == 1800.0
    assert config.burst_intervals.eero == 30.0
    assert config.burst_intervals.managed_hosts == 10.0
    assert config.uplink_bps == 10_000_000.0


def test_eero_burst_floor_is_rejected() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 30"):
        InvestigationConfig(burst_intervals={"eero": 29.9})


@pytest.mark.parametrize(
    "overrides",
    [
        {"burst_intervals": {"managed_hosts": 4.9}},
        {"burst_intervals": {"latency": 0.9}},
        {"saturation_fraction": 1.01},
    ],
)
def test_other_investigation_constraints_are_rejected(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        InvestigationConfig(**overrides)


def test_default_duration_cannot_exceed_maximum() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        InvestigationConfig(default_duration_seconds=601, max_duration_seconds=600)


def test_uplink_zero_requests_speedtest_derived_capacity() -> None:
    assert InvestigationConfig(uplink_mbps=0).uplink_bps is None


def test_environment_override_uses_nested_delimiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETMON_INVESTIGATION__UPLINK_MBPS", "25")

    assert NetmonSettings().investigation.uplink_mbps == 25.0
