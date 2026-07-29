from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from netmon.collectors.eero_usage import EeroUsageCollector
from netmon.collectors.host_nic import HostNicCollector
from netmon.collectors.speedtest import SpeedtestCollector
from netmon.config import EeroConfig, HostNicConfig, SpeedtestConfig
from netmon.probes.eero_cloud import EeroCloudError, EeroCloudProbe, EeroDeviceRate, EeroNetwork
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

    async def device_rates(self, token: str, network_id: str | None = None) -> list[EeroDeviceRate]:
        assert token == "secret-token"
        assert network_id is None
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
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, str, str | None]] = []
        self.closed = False

    async def request(
        self,
        method: str,
        path: str,
        payload: dict[str, str] | None = None,
        token: str | None = None,
    ) -> dict[str, object]:
        del payload
        self.calls.append((method, path, token))
        return next(self.responses)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_eero_fixture_protocol_login_verify_and_network_discovery(fixture_dir):
    names = ["eero_login.json", "eero_verify.json", "eero_account.json"]
    responses = [json.loads((fixture_dir / name).read_text()) for name in names]
    transport = EeroFixtureTransport(responses)
    probe = EeroCloudProbe(transport)
    assert await probe.login("person@example.com") == "login-token"
    assert await probe.verify("login-token", "123456") == "long-lived-token"
    assert await probe.list_networks("long-lived-token") == [
        EeroNetwork("900001", "Home", "/2.2/networks/900001"),
        EeroNetwork("900002", "Workshop", "/2.2/networks/900002"),
    ]
    assert transport.calls[-1][2] == "long-lived-token"


@pytest.mark.asyncio
async def test_eero_explicit_network_follows_resource_url_and_converts_mbps(fixture_dir):
    responses = [
        json.loads((fixture_dir / name).read_text())
        for name in ("eero_account.json", "eero_network.json", "eero_devices.json")
    ]
    transport = EeroFixtureTransport(responses)
    rates = await EeroCloudProbe(transport).device_rates("secret-token", "900001")
    assert rates == [
        EeroDeviceRate("aa:bb:cc:dd:ee:01", 156_250.0, 1_062_500.0),
        EeroDeviceRate("aa:bb:cc:dd:ee:03", 0.0, 500.0),
    ]
    assert transport.calls[-1][1] == "/2.2/networks/900001/devices"


@pytest.mark.asyncio
async def test_eero_single_network_is_selected_automatically(fixture_dir):
    account = json.loads((fixture_dir / "eero_account.json").read_text())
    account["data"]["networks"]["data"] = account["data"]["networks"]["data"][:1]
    network = json.loads((fixture_dir / "eero_network.json").read_text())
    devices = json.loads((fixture_dir / "eero_devices.json").read_text())
    rates = await EeroCloudProbe(EeroFixtureTransport([account, network, devices])).device_rates(
        "secret-token"
    )
    assert len(rates) == 2


@pytest.mark.asyncio
async def test_eero_multiple_networks_require_explicit_selection(fixture_dir):
    account = json.loads((fixture_dir / "eero_account.json").read_text())
    probe = EeroCloudProbe(EeroFixtureTransport([account]))
    with pytest.raises(EeroCloudError, match="network"):
        await probe.device_rates("secret-token")


@pytest.mark.asyncio
async def test_eero_verify_and_refresh_preserve_token_when_response_omits_one():
    ok_without_token = {"meta": {"code": 200}, "data": {"verified": True}}
    probe = EeroCloudProbe(EeroFixtureTransport([ok_without_token, ok_without_token]))
    assert await probe.verify("login-token", "123456") == "login-token"
    assert await probe.refresh("long-lived-token") == "long-lived-token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, match",
    [
        ({"meta": {"code": 401, "error": "invalid token"}, "data": None}, "authentication"),
        ({"meta": {"code": 200}, "data": "not-an-object"}, "invalid"),
        ({"data": {}}, "invalid"),
    ],
)
async def test_eero_meta_and_schema_failures_are_sanitized(response: dict[str, object], match: str):
    probe = EeroCloudProbe(EeroFixtureTransport([response]))
    with pytest.raises(EeroCloudError, match=match):
        await probe.list_networks("secret-token")


@pytest.mark.asyncio
async def test_eero_probe_closes_injected_transport():
    transport = EeroFixtureTransport([])
    probe = EeroCloudProbe(transport)
    await probe.aclose()
    assert transport.closed


@pytest.mark.asyncio
async def test_eero_success_path_tags_every_per_device_sample(frozen_clock):
    collector = EeroUsageCollector(
        FakeEeroProbe([EeroDeviceRate("aa:bb", 10.0, 20.0), EeroDeviceRate("cc:dd", 1.0, 2.0)]),
        "secret-token",
        frozen_clock,
        EeroConfig(),
    )
    samples = await collector.collect()
    assert all(sample.source == "eero_cloud" for sample in samples)
    rates = {
        (sample.metric_kind, sample.target): sample.value
        for sample in samples
        if sample.metric_kind.startswith("eero_device_")
    }
    assert rates == {
        ("eero_device_upload_rate", "aa:bb"): 10.0,
        ("eero_device_download_rate", "aa:bb"): 20.0,
        ("eero_device_upload_rate", "cc:dd"): 1.0,
        ("eero_device_download_rate", "cc:dd"): 2.0,
    }
    assert {sample.unit for sample in samples if sample.metric_kind.startswith("eero_device_")} == {
        "bytes_per_second"
    }
    counts = {s.metric_kind: s.value for s in samples if s.target == "eero"}
    assert counts == {"eero_usage_available": 1.0, "eero_devices_reporting": 2.0}
    aggregate = next(
        sample for sample in samples if sample.metric_kind == "wan_upstream_estimated_rate"
    )
    assert aggregate.target == "wan"
    assert aggregate.unit == "bytes_per_second"
    assert aggregate.value == 11.0
    assert aggregate.source == "eero_cloud"


@pytest.mark.asyncio
async def test_eero_success_with_no_reporting_devices_persists_zero_aggregate(frozen_clock):
    collector = EeroUsageCollector(
        FakeEeroProbe([]),
        "secret-token",
        frozen_clock,
        EeroConfig(),
    )

    samples = await collector.collect()

    assert [(sample.metric_kind, sample.value) for sample in samples] == [
        ("eero_usage_available", 1.0),
        ("eero_devices_reporting", 0.0),
        ("wan_upstream_estimated_rate", 0),
    ]


@pytest.mark.asyncio
async def test_eero_failure_does_not_persist_false_zero_upstream_aggregate(frozen_clock):
    collector = EeroUsageCollector(
        FakeEeroProbe(EeroCloudError("offline")),
        "secret-token",
        frozen_clock,
        EeroConfig(),
    )

    samples = await collector.collect()

    assert [sample.metric_kind for sample in samples] == ["eero_usage_available"]


@pytest.mark.asyncio
async def test_eero_skips_devices_without_rates_instead_of_failing_the_poll():
    """Invalid usage from one client must not blank the whole network."""
    transport = EeroFixtureTransport(
        [
            {
                "meta": {"code": 200},
                "data": {
                    "networks": {
                        "data": [
                            {
                                "id": "1",
                                "name": "Home",
                                "url": "/custom/network-resource",
                            }
                        ]
                    }
                },
            },
            {
                "meta": {"code": 200},
                "data": {"resources": {"devices": "/custom/client-resource"}},
            },
            {
                "meta": {"code": 200},
                "data": [
                    {"mac": "aa:bb", "usage": {"up_mbps": 3, "down_mbps": 4}},
                    {"mac": "missing", "usage": {}},
                    {"mac": "string", "usage": {"up_mbps": "1", "down_mbps": 2}},
                    {"mac": "negative", "usage": {"up_mbps": -1, "down_mbps": 2}},
                    {"mac": "infinite", "usage": {"up_mbps": 1e309, "down_mbps": 2}},
                    {"mac": "cc:dd", "usage": {"up_mbps": 1, "down_mbps": 2}},
                ],
            },
        ]
    )
    rates = await EeroCloudProbe(transport).device_rates("secret-token")
    assert rates == [
        EeroDeviceRate("aa:bb", 375_000, 500_000),
        EeroDeviceRate("cc:dd", 125_000, 250_000),
    ]
    assert transport.calls[-1][1] == "/custom/client-resource"


class SteppingClock:
    """Advances a fixed step on every reading, so elapsed time is deterministic."""

    def __init__(self, step: float) -> None:
        self.step = step
        self.value = -step

    def now(self):
        from datetime import UTC, datetime

        return datetime.now(UTC)

    def monotonic(self) -> float:
        self.value += self.step
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.value += seconds


@pytest.mark.asyncio
async def test_speedtest_upload_stops_at_the_deadline_and_reports_what_it_sent():
    import httpx

    from netmon.probes.speedtest import HttpxSpeedtestProbe

    uploaded: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            uploaded.append(len(request.content))
            return httpx.Response(200)
        return httpx.Response(200, content=b"x" * 4096)

    config = SpeedtestConfig(
        download_bytes=4096,
        upload_bytes=10_000_000,
        max_bytes=10_000_000,
        total_time_budget_seconds=1.0,
    )
    probe = HttpxSpeedtestProbe(SteppingClock(0.01), transport=httpx.MockTransport(handler))
    result = await probe.run(config)

    assert uploaded, "upload was never attempted"
    # The budget must truncate the payload rather than letting a slow link run unbounded.
    assert 0 < uploaded[0] < config.upload_bytes
    assert result.upload_mbps is not None


@pytest.mark.asyncio
async def test_speedtest_budget_exhausted_before_transfer_yields_no_capacity():
    import httpx

    from netmon.probes.speedtest import HttpxSpeedtestProbe

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 16)

    # A step larger than the budget means the deadline has passed by the first transfer.
    probe = HttpxSpeedtestProbe(SteppingClock(5.0), transport=httpx.MockTransport(handler))
    result = await probe.run(SpeedtestConfig(total_time_budget_seconds=1.0))

    assert result.download_mbps is None
    assert result.upload_mbps is None


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
