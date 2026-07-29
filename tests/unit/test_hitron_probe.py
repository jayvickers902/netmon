from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from netmon.probes.hitron import HitronProbe

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "hitron"
ENDPOINTS = (
    "dsinfo.asp",
    "dsofdminfo.asp",
    "usinfo.asp",
    "getSysInfo.asp",
    "getLinkStatus.asp",
    "getCMInit.asp",
)


def _fixture(name: str) -> bytes:
    return (FIXTURE_DIR / f"{name}.json").read_bytes()


def _transport(
    responses: dict[str, bytes | int | Exception] | None = None,
) -> httpx.MockTransport:
    defaults = {
        f"/data/{endpoint}": _fixture(endpoint.removesuffix(".asp")) for endpoint in ENDPOINTS
    }
    defaults.update(responses or {})

    async def handler(request: httpx.Request) -> httpx.Response:
        result = defaults[request.url.path]
        if isinstance(result, Exception):
            raise result
        if isinstance(result, int):
            return httpx.Response(result, request=request)
        return httpx.Response(200, content=result, request=request)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_status_parses_captured_hitron_fixtures() -> None:
    probe = HitronProbe(transport=_transport())

    status = await probe.status()

    assert status is not None
    assert len(status.downstream) == 24
    assert len(status.upstream) == 4
    assert status.downstream_octets_total == 58_723_275_423
    assert status.uptime_seconds == 68 * 3600 + 18 * 60 + 12
    assert status.link_speed_mbps == 2500
    assert status.link_up is True
    assert status.software_version == "7.3.5.3.3b4"
    assert status.registered is True
    await probe.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("placeholder", ["TODO", "NA", "Unknow", ""])
async def test_numeric_placeholders_are_missing(placeholder: str) -> None:
    dsinfo = json.loads(_fixture("dsinfo"))
    assert isinstance(dsinfo, list)
    assert isinstance(dsinfo[0], dict)
    for field in ("snr", "signalStrength", "correcteds", "uncorrect", "dsoctets"):
        dsinfo[0][field] = placeholder

    probe = HitronProbe(transport=_transport({"/data/dsinfo.asp": json.dumps(dsinfo).encode()}))
    status = await probe.status()

    assert status is not None
    channel = next(channel for channel in status.downstream if channel.channel_id == 6)
    assert channel.snr_db is None
    assert channel.power_dbmv is None
    assert channel.corrected is None
    assert channel.uncorrectable is None
    assert channel.octets is None
    assert status.downstream_octets_total == 58_723_275_423 - 2_415_059_454
    await probe.close()


@pytest.mark.asyncio
async def test_padded_disabled_numeric_is_missing() -> None:
    usinfo = json.loads(_fixture("usinfo"))
    assert isinstance(usinfo, list)
    assert isinstance(usinfo[0], dict)
    usinfo[0]["signalStrength"] = "    0.0000"
    probe = HitronProbe(transport=_transport({"/data/usinfo.asp": json.dumps(usinfo).encode()}))

    status = await probe.status()

    assert status is not None
    assert status.upstream[0].power_dbmv is None
    await probe.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.ConnectError("offline"),
        500,
        b"[{truncated",
    ],
)
async def test_unavailable_or_unusable_responses_return_none(
    response: int | bytes | Exception,
) -> None:
    probe = HitronProbe(
        transport=_transport({f"/data/{endpoint}": response for endpoint in ENDPOINTS})
    )

    assert await probe.status() is None
    await probe.close()


@pytest.mark.asyncio
async def test_one_failed_endpoint_does_not_discard_partial_status() -> None:
    probe = HitronProbe(transport=_transport({"/data/getLinkStatus.asp": 500}))

    status = await probe.status()

    assert status is not None
    assert len(status.downstream) == 24
    assert len(status.upstream) == 4
    assert status.link_speed_mbps is None
    assert status.link_up is None
    assert status.registered is True
    await probe.close()


@pytest.mark.asyncio
async def test_requests_are_concurrent() -> None:
    started: set[str] = set()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.add(request.url.path)
        if len(started) == len(ENDPOINTS):
            release.set()
        await release.wait()
        name = request.url.path.rsplit("/", 1)[-1].removesuffix(".asp")
        return httpx.Response(200, content=_fixture(name), request=request)

    probe = HitronProbe(transport=httpx.MockTransport(handler))
    status = await asyncio.wait_for(probe.status(), timeout=1)

    assert status is not None
    assert started == {f"/data/{endpoint}" for endpoint in ENDPOINTS}
    await probe.close()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_safe_before_status() -> None:
    probe = HitronProbe(transport=_transport())
    await probe.close()
    await probe.close()

    await probe.status()
    await probe.close()
    await probe.close()
