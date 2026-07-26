"""Injected HTTP POST probe used by the webhook notifier."""

from __future__ import annotations

import httpx


class HttpxWebhookProbe:
    async def post(self, url: str, payload: dict[str, object]) -> None:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
