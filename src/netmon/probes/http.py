"""Timed HTTP probe with useful failure categories."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Protocol

import httpx

from netmon.collectors.base import Clock, RealClock


@dataclass(frozen=True, slots=True)
class HttpResult:
    url: str
    status: str
    status_code: int | None
    ttfb_ms: float | None
    total_ms: float | None


class HttpProbe(Protocol):
    async def fetch(self, url: str) -> HttpResult: ...


class HttpxProbe:
    def __init__(self, clock: Clock | None = None, timeout_seconds: float = 5.0) -> None:
        self.clock = clock or RealClock()
        self.timeout_seconds = timeout_seconds

    async def fetch(self, url: str) -> HttpResult:
        started = self.clock.monotonic()
        try:
            timeout = httpx.Timeout(self.timeout_seconds)
            async with (
                httpx.AsyncClient(
                    timeout=timeout, follow_redirects=True, trust_env=False
                ) as client,
                client.stream("GET", url) as response,
            ):
                ttfb = (self.clock.monotonic() - started) * 1000
                await response.aread()
                total = (self.clock.monotonic() - started) * 1000
                status = "ok" if response.status_code < 400 else "http_error"
                return HttpResult(url, status, response.status_code, ttfb, total)
        except httpx.TimeoutException:
            return HttpResult(url, "timeout", None, None, None)
        except httpx.ConnectError as error:
            if _is_tls_error(error):
                status = "tls_failure"
            elif _is_dns_error(error):
                status = "dns_failure"
            else:
                status = "connect_failure"
            return HttpResult(url, status, None, None, None)
        except httpx.TransportError:
            return HttpResult(url, "connect_failure", None, None, None)
        except ValueError:
            return HttpResult(url, "invalid_url", None, None, None)


def _causes(error: BaseException) -> list[BaseException]:
    causes: list[BaseException] = []
    current: BaseException | None = error
    while current is not None:
        causes.append(current)
        current = current.__cause__ or current.__context__
    return causes


def _is_tls_error(error: BaseException) -> bool:
    return any(isinstance(item, (ssl.SSLError, ssl.CertificateError)) for item in _causes(error))


def _is_dns_error(error: BaseException) -> bool:
    return any(
        isinstance(item, OSError) and getattr(item, "errno", None) in (-2, -3, 11001)
        for item in _causes(error)
    )
