"""Hermetic tests for the managed Windows host NIC SSH probe."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from netmon.probes.managed_host import (
    CommandResult,
    ManagedHostProbeError,
    SshWindowsNicProbe,
)


class FakeRunner:
    def __init__(self, result: CommandResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], float]] = []

    async def run(self, args: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        self.calls.append((args, timeout_seconds))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _probe(result: CommandResult | Exception) -> tuple[SshWindowsNicProbe, FakeRunner]:
    runner = FakeRunner(result)
    return (
        SshWindowsNicProbe(runner, "192.0.2.7", "netmon", Path("C:/keys/host key"), 2202, 8.5),
        runner,
    )


async def test_ssh_command_has_strict_safety_options_and_encoded_nic_query() -> None:
    probe, runner = _probe(CommandResult(0, b"[]", b""))

    assert await probe.counters() == []

    args, timeout = runner.calls[0]
    assert args[:2] == ("ssh.exe", "-4")
    assert args[2:4] == ("-o", "BatchMode=yes")
    assert args[4:6] == ("-o", "IdentitiesOnly=yes")
    assert args[6:8] == ("-o", "StrictHostKeyChecking=yes")
    assert args[8:12] == ("-i", str(Path("C:/keys/host key")), "-p", "2202")
    assert args[12:16] == ("netmon@192.0.2.7", "powershell.exe", "-NoProfile", "-NonInteractive")
    assert args[16] == "-EncodedCommand"
    command = base64.b64decode(args[17]).decode("utf-16le")
    assert "Get-NetAdapter -Physical" in command
    assert "Status -eq 'Up'" in command
    assert "Get-NetAdapterStatistics" in command
    assert "ConvertTo-Json -Compress" in command
    assert timeout == 8.5


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            b'{"name":"Ethernet","mac":"AA-BB-CC-DD-EE-FF","bytes_sent":2,"bytes_recv":3}',
            [("Ethernet", "aa:bb:cc:dd:ee:ff", 2, 3)],
        ),
        (
            b'[{"name":"Ethernet","mac":"aa:bb:cc:dd:ee:ff","bytes_sent":2,"bytes_recv":3},'
            b'{"name":"Wi-Fi","mac":"001122334455","bytes_sent":4,"bytes_recv":5}]',
            [("Ethernet", "aa:bb:cc:dd:ee:ff", 2, 3), ("Wi-Fi", "00:11:22:33:44:55", 4, 5)],
        ),
    ],
)
async def test_parses_singleton_and_list(
    payload: bytes, expected: list[tuple[str, str, int, int]]
) -> None:
    probe, _ = _probe(CommandResult(0, payload, b""))

    counters = await probe.counters()

    assert [
        (item.adapter_name, item.mac, item.bytes_sent, item.bytes_recv) for item in counters
    ] == expected


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"null",
        b"[1]",
        b'[{"name":"Ethernet","mac":"not-a-mac","bytes_sent":1,"bytes_recv":2}]',
        b'[{"name":"Ethernet","mac":"00:11:22:33:44:55","bytes_sent":-1,"bytes_recv":2}]',
        b'[{"name":"Ethernet","mac":"00:11:22:33:44:55","bytes_sent":true,"bytes_recv":2}]',
        b'[{"name":"Ethernet","mac":"00:11:22:33:44:55","bytes_sent":1.0,"bytes_recv":2}]',
        b'[{"name":"Ethernet","mac":"00:11:22:33:44:55","bytes_sent":1,"bytes_recv":2},'
        b'{"name":"Ethernet","mac":"66:77:88:99:aa:bb","bytes_sent":3,"bytes_recv":4}]',
    ],
)
async def test_rejects_malformed_remote_data(payload: bytes) -> None:
    probe, _ = _probe(CommandResult(0, payload, b"sensitive stderr"))

    with pytest.raises(ManagedHostProbeError, match="invalid NIC data") as error:
        await probe.counters()

    assert "sensitive" not in str(error.value)


async def test_nonzero_exit_is_sanitized() -> None:
    probe, _ = _probe(CommandResult(255, b"secret output", b"private key rejected"))

    with pytest.raises(ManagedHostProbeError, match="SSH command failed") as error:
        await probe.counters()

    assert "secret" not in str(error.value)
    assert "key" not in str(error.value)


async def test_timeout_is_sanitized() -> None:
    probe, _ = _probe(TimeoutError("host token was rejected"))

    with pytest.raises(ManagedHostProbeError, match="unavailable") as error:
        await probe.counters()

    assert "token" not in str(error.value)
