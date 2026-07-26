"""Capacity probe using Cloudflare-compatible HTTP download and upload endpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

import httpx

from netmon.collectors.base import Clock, RealClock
from netmon.config import SpeedtestConfig


@dataclass(frozen=True, slots=True)
class SpeedtestResult:
    download_mbps: float | None
    upload_mbps: float | None
    idle_latency_ms: float | None
    loaded_latency_ms: float | None


class SpeedtestProbe(Protocol):
    async def run(self, config: SpeedtestConfig) -> SpeedtestResult: ...


class HttpxSpeedtestProbe:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or RealClock()

    async def run(self, config: SpeedtestConfig) -> SpeedtestResult:
        base = str(config.endpoint).rstrip("/") + "/"
        timeout = httpx.Timeout(config.total_time_budget_seconds)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            try:
                idle = await self._latency(client, urljoin(base, "__down?bytes=1"))
                download, loaded = await self._download(client, base, config)
                upload = await self._upload(client, base, config)
                return SpeedtestResult(download, upload, idle, loaded)
            except (httpx.HTTPError, OSError, TimeoutError):
                return SpeedtestResult(None, None, idle if "idle" in locals() else None, None)

    async def _download(
        self, client: httpx.AsyncClient, base: str, config: SpeedtestConfig
    ) -> tuple[float | None, float | None]:
        url = urljoin(base, f"__down?bytes={min(config.download_bytes, config.max_bytes)}")
        started = self.clock.monotonic()
        loaded_task = asyncio.create_task(self._latency(client, urljoin(base, "__down?bytes=1")))
        received = 0
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    received = min(config.max_bytes, received + len(chunk))
                    if self.clock.monotonic() - started >= config.total_time_budget_seconds:
                        break
        finally:
            loaded = await loaded_task
        elapsed = self.clock.monotonic() - started
        return (received * 8 / elapsed / 1_000_000 if received and elapsed > 0 else None, loaded)

    async def _upload(
        self, client: httpx.AsyncClient, base: str, config: SpeedtestConfig
    ) -> float | None:
        amount = min(config.upload_bytes, config.max_bytes)
        started = self.clock.monotonic()
        response = await client.post(urljoin(base, "__up"), content=b"0" * amount)
        response.raise_for_status()
        elapsed = self.clock.monotonic() - started
        return amount * 8 / elapsed / 1_000_000 if elapsed > 0 else None

    async def _latency(self, client: httpx.AsyncClient, url: str) -> float | None:
        started = self.clock.monotonic()
        response = await client.get(url)
        response.raise_for_status()
        return (self.clock.monotonic() - started) * 1000
