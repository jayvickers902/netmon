from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest

from netmon.collectors.eero_usage import EeroUsageCollector
from netmon.config import EeroConfig
from netmon.probes.eero_cloud import (
    EeroAuthenticationError,
    EeroDeviceRate,
    EeroSchemaError,
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

    async def device_rates(self, token: str, network_id: str | None = None) -> list[EeroDeviceRate]:
        self.rate_calls.append((token, network_id))
        if len(self.rate_calls) == 1:
            raise EeroAuthenticationError("eero cloud authentication failed")
        return [EeroDeviceRate("aa:bb", 125_000.0, 250_000.0)]

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
