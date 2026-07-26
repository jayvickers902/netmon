"""Capacity probe using Cloudflare-compatible HTTP download and upload endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

import httpx

from netmon.collectors.base import Clock, RealClock
from netmon.config import SpeedtestConfig

# Upload is streamed in chunks rather than materialised, so a large `upload_bytes` does not
# become a large allocation and so the time budget can be honoured mid-transfer.
_UPLOAD_CHUNK_BYTES = 65_536


@dataclass(frozen=True, slots=True)
class SpeedtestResult:
    download_mbps: float | None
    upload_mbps: float | None
    idle_latency_ms: float | None
    loaded_latency_ms: float | None


class SpeedtestProbe(Protocol):
    async def run(self, config: SpeedtestConfig) -> SpeedtestResult: ...


class HttpxSpeedtestProbe:
    def __init__(
        self, clock: Clock | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.clock = clock or RealClock()
        # Injected only by tests; the real probe builds its own transport.
        self.transport = transport

    async def run(self, config: SpeedtestConfig) -> SpeedtestResult:
        base = str(config.endpoint).rstrip("/") + "/"
        # One deadline for the whole run. Budgeting each phase separately would let a slow
        # link spend the full budget three times over, which is the failure this guards.
        deadline = self.clock.monotonic() + config.total_time_budget_seconds
        timeout = httpx.Timeout(config.total_time_budget_seconds)
        idle: float | None = None
        async with httpx.AsyncClient(
            timeout=timeout, trust_env=False, transport=self.transport
        ) as client:
            try:
                idle = await self._latency(client, urljoin(base, "__down?bytes=1"))
                download, loaded = await self._download(client, base, config, deadline)
                upload = await self._upload(client, base, config, deadline)
                return SpeedtestResult(download, upload, idle, loaded)
            except (httpx.HTTPError, OSError, TimeoutError):
                return SpeedtestResult(None, None, idle, None)

    async def _download(
        self, client: httpx.AsyncClient, base: str, config: SpeedtestConfig, deadline: float
    ) -> tuple[float | None, float | None]:
        if self.clock.monotonic() >= deadline:
            return (None, None)
        url = urljoin(base, f"__down?bytes={min(config.download_bytes, config.max_bytes)}")
        started = self.clock.monotonic()
        loaded_task = asyncio.create_task(self._latency(client, urljoin(base, "__down?bytes=1")))
        received = 0
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    received = min(config.max_bytes, received + len(chunk))
                    if self.clock.monotonic() >= deadline:
                        break
        finally:
            loaded = await loaded_task
        elapsed = self.clock.monotonic() - started
        return (received * 8 / elapsed / 1_000_000 if received and elapsed > 0 else None, loaded)

    async def _upload(
        self, client: httpx.AsyncClient, base: str, config: SpeedtestConfig, deadline: float
    ) -> float | None:
        if self.clock.monotonic() >= deadline:
            return None
        amount = min(config.upload_bytes, config.max_bytes)
        sent = 0
        chunk = b"0" * min(amount, _UPLOAD_CHUNK_BYTES)
        started = self.clock.monotonic()

        async def body() -> AsyncIterator[bytes]:
            nonlocal sent
            while sent < amount and self.clock.monotonic() < deadline:
                piece = chunk[: amount - sent]
                sent += len(piece)
                yield piece

        response = await client.post(urljoin(base, "__up"), content=body())
        response.raise_for_status()
        elapsed = self.clock.monotonic() - started
        # Rate is over what was actually sent, not what was intended — a truncated upload
        # must not be reported as though the full payload made it.
        return sent * 8 / elapsed / 1_000_000 if sent and elapsed > 0 else None

    async def _latency(self, client: httpx.AsyncClient, url: str) -> float | None:
        started = self.clock.monotonic()
        response = await client.get(url)
        response.raise_for_status()
        return (self.clock.monotonic() - started) * 1000
