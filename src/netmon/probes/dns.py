"""Resolver-pinned DNS probe."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import dns.exception
import dns.resolver

from netmon.collectors.base import Clock, RealClock


@dataclass(frozen=True, slots=True)
class DnsResult:
    resolver: str
    name: str
    status: str
    resolution_ms: float | None
    answers: tuple[str, ...]


class DnsProbe(Protocol):
    async def resolve(self, resolver: str, name: str) -> DnsResult: ...


class ResolverDnsProbe:
    def __init__(self, clock: Clock | None = None, timeout_seconds: float = 5.0) -> None:
        self.clock = clock or RealClock()
        self.timeout_seconds = timeout_seconds

    async def resolve(self, resolver: str, name: str) -> DnsResult:
        started = self.clock.monotonic()
        result = await asyncio.to_thread(self._resolve_sync, resolver, name)
        elapsed = (self.clock.monotonic() - started) * 1000
        if result[0] == "ok":
            return DnsResult(resolver, name, result[0], elapsed, tuple(result[1]))
        return DnsResult(resolver, name, result[0], elapsed, ())

    def _resolve_sync(self, resolver: str, name: str) -> tuple[str, list[str]]:
        client = dns.resolver.Resolver(configure=False)
        client.nameservers = [resolver]
        client.timeout = self.timeout_seconds
        client.lifetime = self.timeout_seconds
        try:
            answer = client.resolve(name, "A")
        except dns.resolver.NXDOMAIN:
            return "nxdomain", []
        except dns.resolver.NoAnswer:
            return "no_answer", []
        except dns.exception.Timeout:
            return "timeout", []
        except (dns.exception.DNSException, OSError, ValueError):
            return "failure", []
        return "ok", [str(item) for item in answer]
