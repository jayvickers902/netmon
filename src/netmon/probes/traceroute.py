"""Cross-platform traceroute execution and output parsing."""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TracerouteHop:
    index: int
    host: str | None
    ip: str | None
    rtts_ms: tuple[float, ...]
    timed_out: bool


class TracerouteProbe(Protocol):
    async def trace(
        self, target: str, max_hops: int, timeout_seconds: float
    ) -> list[TracerouteHop]: ...


_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_RTT = re.compile(r"(?:<\s*)?(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
_HOP = re.compile(r"^\s*(\d+)\s+(.*)$")


def parse_traceroute(output: str) -> list[TracerouteHop]:
    hops: list[TracerouteHop] = []
    for line in output.splitlines():
        match = _HOP.match(line)
        if match is None:
            continue
        index = int(match.group(1))
        body = match.group(2)
        rtts = tuple(float(value) for value in _RTT.findall(body))
        ip_match = _IP.search(body)
        ip = ip_match.group(0) if ip_match else None
        timed_out = not rtts or "*" in body or "timed out" in body.lower()
        host: str | None = None
        if ip_match is not None:
            for token in body[: ip_match.start()].split():
                candidate = token.strip("()[]")
                if (
                    candidate
                    and candidate.lower() != "ms"
                    and not candidate.replace(".", "", 1).isdigit()
                ):
                    host = candidate
            after_ip = body[ip_match.end() :].strip()
            if host is None and after_ip:
                host = after_ip.split()[0].strip("()[]") or None
        else:
            tokens = [
                token for token in body.split() if token not in {"*", "Request", "timed", "out"}
            ]
            host = tokens[0] if tokens else None
        hops.append(TracerouteHop(index, host, ip, rtts, timed_out))
    return hops


class SystemTracerouteProbe:
    async def trace(
        self, target: str, max_hops: int = 30, timeout_seconds: float = 30.0
    ) -> list[TracerouteHop]:
        command = (
            ("tracert", "-d", "-h", str(max_hops), target)
            if sys.platform == "win32"
            else ("traceroute", "-n", "-m", str(max_hops), target)
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout_seconds)
            except TimeoutError:
                process.kill()
                await process.communicate()
                return []
        except OSError:
            return []
        return parse_traceroute(stdout.decode(errors="replace"))
