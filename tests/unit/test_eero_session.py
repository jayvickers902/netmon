from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest

from netmon.collectors.eero_usage import EeroUsageCollector
from netmon.config import EeroConfig
from netmon.probes.eero_cloud import (
    EeroAuthenticationError,
    EeroCloudProbe,
    EeroDevice,
    EeroNetworkSnapshot,
    EeroNode,
    EeroSchemaError,
    EeroSpeedTest,
    HttpxEeroTransport,
)
from netmon.probes.eero_token import FileEeroTokenStore


@dataclass
class RecordingTokenStore:
    saved: list[str] = field(default_factory=list)

    async def save(self, token: str) -> None:
        self.saved.append(token)


class RefreshingProbe:
    def __init__(self) -> None:
        self.rate_calls: list[tuple[str, str | None]] = []
        self.closed = False

    async def network_snapshot(
        self, token: str, network_id: str | None = None
    ) -> EeroNetworkSnapshot:
        self.rate_calls.append((token, network_id))
        if len(self.rate_calls) == 1:
            raise EeroAuthenticationError("eero cloud authentication failed")
        return EeroNetworkSnapshot(
            "fake-network",
            [
                EeroDevice(
                    "aa:bb", upload_bytes_per_second=125_000.0, download_bytes_per_second=250_000.0
                )
            ],
            None,
        )

    async def refresh(self, token: str) -> str:
        assert token == "expired-token"
        return "refreshed-token"

    async def aclose(self) -> None:
        self.closed = True


class FailingRefreshProbe(RefreshingProbe):
    async def refresh(self, token: str) -> str:
        del token
        raise EeroAuthenticationError("eero cloud authentication failed")


@pytest.mark.asyncio
async def test_eero_collector_refreshes_once_persists_token_and_retries(frozen_clock):
    probe = RefreshingProbe()
    token_store = RecordingTokenStore()
    collector = EeroUsageCollector(
        probe,
        "expired-token",
        frozen_clock,
        EeroConfig(network_id="network-42"),
        token_store,
    )

    samples = await collector.collect()

    assert probe.rate_calls == [
        ("expired-token", "network-42"),
        ("refreshed-token", "network-42"),
    ]
    assert token_store.saved == ["refreshed-token"]
    assert collector.token == "refreshed-token"
    assert {sample.metric_kind for sample in samples} >= {
        "eero_usage_available",
        "eero_device_upload_rate",
        "eero_device_download_rate",
    }

    await collector.close()
    assert probe.closed


@pytest.mark.asyncio
async def test_eero_collector_refresh_failure_remains_soft(frozen_clock):
    collector = EeroUsageCollector(
        FailingRefreshProbe(),
        "expired-token",
        frozen_clock,
        EeroConfig(),
    )

    samples = await collector.collect()

    assert [(sample.metric_kind, sample.value) for sample in samples] == [
        ("eero_usage_available", 0.0)
    ]


@pytest.mark.asyncio
async def test_file_eero_token_store_round_trips_and_normalizes(tmp_path):
    token_path = tmp_path / "private" / "eero.token"
    store = FileEeroTokenStore(token_path)

    assert store.read() is None
    await store.save("long-lived-token")

    assert store.read() == "long-lived-token"
    assert token_path.read_text(encoding="utf-8") == "long-lived-token\n"


@pytest.mark.asyncio
async def test_eero_http_transport_reuses_and_closes_client_with_session_cookie():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"meta": {"code": 200}, "data": {}})

    transport = HttpxEeroTransport(
        "https://example.invalid",
        transport=httpx.MockTransport(handler),
    )

    await transport.request("GET", "/first", token="secret-token")
    await transport.request("GET", "/second", token="secret-token")
    await transport.aclose()

    assert [request.url.path for request in requests] == ["/first", "/second"]
    assert all(request.headers["cookie"] == "s=secret-token" for request in requests)
    assert transport._client.is_closed


@pytest.mark.asyncio
async def test_eero_http_transport_rejects_cross_origin_resource_before_sending_token():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    transport = HttpxEeroTransport(
        "https://api-user.e2ro.com",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(EeroSchemaError, match="invalid resource URL") as captured:
            await transport.request(
                "GET",
                "https://attacker.invalid/devices",
                token="secret-token",
            )
        assert "secret-token" not in str(captured.value)
        assert requests == []
    finally:
        await transport.aclose()


class _FixtureTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, str, str | None]] = []

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
        pass


@pytest.mark.asyncio
async def test_eero_devices_prefer_nickname_then_display_name_then_hostname(fixture_dir):
    names = ["eero_account.json", "eero_network.json", "eero_devices.json"]
    responses = [json.loads((fixture_dir / name).read_text()) for name in names]
    snapshot = await EeroCloudProbe(_FixtureTransport(responses)).network_snapshot(
        "secret-token", "900001"
    )

    by_id = {device.device_id: device for device in snapshot.devices}
    assert by_id["aa:bb:cc:dd:ee:01"].name == "Living Room TV"  # nickname wins
    assert by_id["aa:bb:cc:dd:ee:02"].name == "guest-phone"  # no nickname/display_name: hostname
    assert by_id["aa:bb:cc:dd:ee:03"].name == "Kitchen Sensor"  # display_name, no nickname

    assert by_id["aa:bb:cc:dd:ee:01"].connection_type == "wired"
    assert by_id["aa:bb:cc:dd:ee:01"].node == "Living Room eero"
    assert by_id["aa:bb:cc:dd:ee:01"].ip == "192.168.1.50"
    assert by_id["aa:bb:cc:dd:ee:02"].connected is False


@pytest.mark.asyncio
async def test_eero_signal_and_band_parsing_is_defensive_against_junk(fixture_dir):
    names = ["eero_account.json", "eero_network.json", "eero_devices.json"]
    responses = [json.loads((fixture_dir / name).read_text()) for name in names]
    snapshot = await EeroCloudProbe(_FixtureTransport(responses)).network_snapshot(
        "secret-token", "900001"
    )

    by_id = {device.device_id: device for device in snapshot.devices}
    assert by_id["aa:bb:cc:dd:ee:03"].signal_dbm == -61.0
    assert by_id["aa:bb:cc:dd:ee:03"].band == "5 GHz"
    # "junk" has no parseable dBm number and no interface at all.
    assert by_id["aa:bb:cc:dd:ee:02"].signal_dbm is None
    assert by_id["aa:bb:cc:dd:ee:02"].band is None
    assert by_id["aa:bb:cc:dd:ee:01"].band is None

    assert snapshot.speed == EeroSpeedTest(datetime(2026, 7, 20, 12, 0, tzinfo=UTC), 35.2, 480.5)


@pytest.mark.asyncio
async def test_eero_node_health_parses_status_and_client_counts(fixture_dir):
    response = json.loads((fixture_dir / "eero_eeros.json").read_text())
    transport = _FixtureTransport([response])

    nodes = await EeroCloudProbe(transport).node_health("secret-token", "900001")

    assert [(node.target, node.status, node.connected_clients) for node in nodes] == [
        ("Living Room", "green", 12),
        ("Kitchen", "red", 3),
    ]
    assert transport.calls[-1][1] == "/2.2/networks/900001/eeros"


class RichEeroProbe:
    """A stand-in that always succeeds, exercising rssi/node/speedtest samples."""

    def __init__(self) -> None:
        self.node_calls: list[tuple[str, str]] = []
        self.speed = EeroSpeedTest(datetime(2026, 7, 20, 12, 0, tzinfo=UTC), 35.2, 480.5)

    async def network_snapshot(
        self, token: str, network_id: str | None = None
    ) -> EeroNetworkSnapshot:
        del token, network_id
        return EeroNetworkSnapshot(
            "network-1",
            [
                EeroDevice(
                    "aa:bb",
                    signal_dbm=-52.0,
                    upload_bytes_per_second=125_000.0,
                    download_bytes_per_second=250_000.0,
                ),
                EeroDevice("unknown-id"),
            ],
            self.speed,
        )

    async def node_health(self, token: str, network_id: str) -> list[EeroNode]:
        self.node_calls.append((token, network_id))
        return [
            EeroNode(location="Living Room", status="green", connected_clients=5),
            EeroNode(serial="SN002", status="red", connected_clients=0),
        ]


@pytest.mark.asyncio
async def test_eero_collector_emits_rssi_node_samples_and_pushes_device_sink(frozen_clock):
    pushed: list[list[EeroDevice]] = []
    collector = EeroUsageCollector(
        RichEeroProbe(),
        "token",
        frozen_clock,
        EeroConfig(),
        device_sink=pushed.append,
    )

    samples = await collector.collect()

    assert [(s.target, s.value) for s in samples if s.metric_kind == "eero_device_rssi"] == [
        ("aa:bb", -52.0)
    ]
    assert {s.target: s.value for s in samples if s.metric_kind == "eero_node_online"} == {
        "Living Room": 1.0,
        "SN002": 0.0,
    }
    assert {s.target: s.value for s in samples if s.metric_kind == "eero_node_clients"} == {
        "Living Room": 5.0,
        "SN002": 0.0,
    }
    # The sink receives every parsed device, including ones with no rate fields at all:
    # unknown/rate-less MACs are the Store's business (it leaves unmatched rows alone),
    # not something the collector should filter out before handing devices over.
    assert [device.device_id for device in pushed[0]] == ["aa:bb", "unknown-id"]


@pytest.mark.asyncio
async def test_eero_collector_dedupes_speedtest_samples_by_measurement_date(frozen_clock):
    probe = RichEeroProbe()
    collector = EeroUsageCollector(probe, "token", frozen_clock, EeroConfig())

    first = await collector.collect()
    speed_first = {
        s.metric_kind: s.value for s in first if s.metric_kind.startswith("eero_speedtest_")
    }
    assert speed_first == {
        "eero_speedtest_upload_rate": 35.2,
        "eero_speedtest_download_rate": 480.5,
    }
    assert all(
        s.ts == probe.speed.measured_at
        for s in first
        if s.metric_kind.startswith("eero_speedtest_")
    )

    second = await collector.collect()
    assert not any(s.metric_kind.startswith("eero_speedtest_") for s in second)
