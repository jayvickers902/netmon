from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from netmon.collectors.eero_usage import EeroUsageCollector
from netmon.collectors.host_nic import HostNicCollector
from netmon.collectors.speedtest import SpeedtestCollector
from netmon.config import EeroConfig, HostNicConfig, SpeedtestConfig
from netmon.probes.eero_cloud import EeroCloudError, EeroCloudProbe, EeroDeviceRate
from netmon.probes.host_nic import NicCounters, ProcessCounters
from netmon.probes.speedtest import SpeedtestResult


class FakeNicProbe:
    def __init__(self, counters: list[dict[str, NicCounters]]) -> None:
        self.values = iter(counters)

    def counters(self) -> dict[str, NicCounters]:
        return next(self.values)

    def process_counters(self) -> list[ProcessCounters]:
        return []


@pytest.mark.asyncio
async def test_host_nic_rates_discard_counter_reset(frozen_clock):
    probe = FakeNicProbe(
        [
            {"Ethernet": NicCounters(100, 200)},
            {"Ethernet": NicCounters(300, 500)},
            {"Ethernet": NicCounters(10, 700)},
        ]
    )
    collector = HostNicCollector(probe, frozen_clock, HostNicConfig())
    assert await collector.collect() == []
    frozen_clock.advance(2)
    samples = await collector.collect()
    assert {sample.value for sample in samples} == {100.0, 150.0}
    frozen_clock.advance(2)
    samples = await collector.collect()
    assert [sample.metric_kind for sample in samples] == ["host_nic_bytes_recv_rate"]


@dataclass
class FakeSpeedtestProbe:
    result: SpeedtestResult

    async def run(self, config: SpeedtestConfig) -> SpeedtestResult:
        del config
        return self.result


@pytest.mark.asyncio
async def test_speedtest_samples_are_capacity_and_explicitly_sourced(frozen_clock):
    probe = FakeSpeedtestProbe(SpeedtestResult(100.0, 20.0, 4.0, 40.0))
    collector = SpeedtestCollector(probe, frozen_clock, SpeedtestConfig())
    samples = await collector.collect()
    assert {sample.metric_kind for sample in samples} == {
        "speedtest_download_capacity",
        "speedtest_upload_capacity",
        "speedtest_idle_latency",
        "speedtest_loaded_latency",
    }
    assert all(sample.source == "speedtest" for sample in samples)


class FakeEeroProbe:
    def __init__(self, result: list[EeroDeviceRate] | Exception) -> None:
        self.result = result

    async def device_rates(self, token: str) -> list[EeroDeviceRate]:
        assert token == "secret-token"
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_eero_auth_expiry_fails_soft_without_token_in_logs(frozen_clock, caplog):
    collector = EeroUsageCollector(
        FakeEeroProbe(EeroCloudError("auth expired")),
        "secret-token",
        frozen_clock,
        EeroConfig(),
    )
    samples = await collector.collect()
    assert samples[0].value == 0.0
    assert samples[0].source == "eero_cloud"
    assert "secret-token" not in caplog.text


@pytest.mark.asyncio
async def test_eero_malformed_response_is_unavailable(frozen_clock):
    collector = EeroUsageCollector(
        FakeEeroProbe(EeroCloudError("malformed response")),
        "token",
        frozen_clock,
        EeroConfig(),
    )
    assert (await collector.collect())[0].metric_kind == "eero_usage_available"


class EeroFixtureTransport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, str, str | None]] = []

    async def request(self, method, path, payload=None, token=None):
        del payload
        self.calls.append((method, path, token))
        return next(self.responses)


@pytest.mark.asyncio
async def test_eero_fixture_protocol_login_verify_and_rates(fixture_dir):
    names = ["eero_login.json", "eero_verify.json", "eero_networks.json", "eero_devices.json"]
    responses = [json.loads((fixture_dir / name).read_text()) for name in names]
    transport = EeroFixtureTransport(responses)
    probe = EeroCloudProbe(transport)
    assert await probe.login("person@example.com") == "login-token"
    assert await probe.verify("login-token", "123456") == "long-lived-token"
    rates = await probe.device_rates("long-lived-token")
    assert rates == [EeroDeviceRate("aa:bb", 3, 4)]
    assert transport.calls[-1][2] == "long-lived-token"


@pytest.mark.asyncio
async def test_eero_fixture_malformed_schema_is_sanitized():
    probe = EeroCloudProbe(EeroFixtureTransport([{"networks": []}]))
    with pytest.raises(EeroCloudError, match="invalid"):
        await probe.device_rates("secret-token")


def test_psutil_probe_maps_nic_counters(monkeypatch):
    from netmon.probes import host_nic

    class Counter:
        bytes_sent = 1
        bytes_recv = 2

    monkeypatch.setattr(host_nic.psutil, "net_io_counters", lambda pernic: {"Wi-Fi": Counter()})
    assert host_nic.PsutilHostNicProbe().counters()["Wi-Fi"] == NicCounters(1, 2)


def test_host_nic_emits_injected_process_rates(frozen_clock):
    class ProcessProbe:
        def __init__(self):
            self.values = iter(
                [
                    {"n": NicCounters(1, 1)},
                    {"n": NicCounters(3, 5)},
                ]
            )
            self.processes = iter(
                [
                    [ProcessCounters(1, "browser", 1, 2)],
                    [ProcessCounters(1, "browser", 10, 20)],
                ]
            )

        def counters(self):
            return next(self.values)

        def process_counters(self):
            return next(self.processes)

    import asyncio

    collector = HostNicCollector(ProcessProbe(), frozen_clock, HostNicConfig(top_processes=1))
    asyncio.run(collector.collect())
    frozen_clock.advance(1)
    samples = asyncio.run(collector.collect())
    assert {sample.metric_kind for sample in samples} >= {
        "host_process_bytes_sent_rate",
        "host_process_bytes_recv_rate",
    }
