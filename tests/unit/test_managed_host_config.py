"""Configuration contracts for opt-in managed-host telemetry."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from netmon.config import ManagedHostsConfig, ManagedHostTargetConfig


def test_managed_host_normalizes_optional_identity_fields() -> None:
    host = ManagedHostTargetConfig(
        name=" office-pc ",
        address=" 192.168.4.50 ",
        username=" netmon ",
        identity_file="keys/netmon",
        adapter_name=" Ethernet ",
        expected_mac="AA-BB-CC-DD-EE-FF",
    )

    assert host.name == "office-pc"
    assert host.address == "192.168.4.50"
    assert host.username == "netmon"
    assert host.adapter_name == "Ethernet"
    assert host.expected_mac == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize("name", ["", "bad name", "-starts-with-a-dash"])
def test_managed_host_rejects_unsafe_name(name: str) -> None:
    with pytest.raises(ValidationError, match="safe nonempty identifier"):
        ManagedHostTargetConfig(
            name=name,
            address="192.168.4.50",
            username="netmon",
            identity_file="keys/netmon",
        )


@pytest.mark.parametrize("field", ["address", "username"])
def test_managed_host_rejects_whitespace_connection_values(field: str) -> None:
    values = {"address": "192.168.4.50", "username": "netmon"}
    values[field] = "contains whitespace"

    with pytest.raises(ValidationError, match="contain no whitespace"):
        ManagedHostTargetConfig(name="office-pc", identity_file="keys/netmon", **values)


def test_managed_hosts_require_unique_names_and_a_host_when_enabled() -> None:
    with pytest.raises(ValidationError, match="at least one host"):
        ManagedHostsConfig(enabled=True)

    target = {
        "name": "office-pc",
        "address": "192.168.4.50",
        "username": "netmon",
        "identity_file": "keys/netmon",
    }
    with pytest.raises(ValidationError, match="must be unique"):
        ManagedHostsConfig(hosts=[target, target])
