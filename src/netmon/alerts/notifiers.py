"""Independent alert notification backends."""

from __future__ import annotations

import importlib
import logging
from collections.abc import Awaitable
from typing import Protocol

from netmon.models import Event

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    async def notify(self, event: Event) -> None: ...


class WebhookProbe(Protocol):
    async def post(self, url: str, payload: dict[str, object]) -> None: ...


class LogNotifier:
    async def notify(self, event: Event) -> None:
        logger.warning("%s: %s", event.severity, event.message)


class WebhookNotifier:
    def __init__(self, probe: WebhookProbe, url: str) -> None:
        self.probe = probe
        self.url = url

    async def notify(self, event: Event) -> None:
        await self.probe.post(
            self.url,
            {
                "kind": event.kind,
                "severity": event.severity,
                "message": event.message,
                "ts": event.ts.isoformat(),
                "details": event.details,
            },
        )


class WindowsToastNotifier:
    def __init__(self, fallback: Notifier | None = None) -> None:
        self.fallback = fallback or LogNotifier()
        self._warned = False

    async def notify(self, event: Event) -> None:
        try:
            module = importlib.import_module("win10toast")
            toaster = module.ToastNotifier()
            toaster.show_toast("netmon", event.message, duration=5, threaded=True)
            return
        except (ImportError, RuntimeError, OSError):
            if not self._warned:
                logger.warning("Windows toast unavailable; falling back to log notifications")
                self._warned = True
            await self.fallback.notify(event)


class NotificationDispatcher:
    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    async def notify(self, event: Event) -> None:
        async def call(notifier: Notifier) -> None:
            try:
                await notifier.notify(event)
            except Exception:
                logger.exception("alert notifier failed; continuing with other notifiers")

        await _gather(*(call(notifier) for notifier in self.notifiers))


async def _gather(*calls: Awaitable[None]) -> None:
    import asyncio

    await asyncio.gather(*calls)
