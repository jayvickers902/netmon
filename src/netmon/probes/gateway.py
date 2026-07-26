"""Default-route and LAN-interface discovery."""

from __future__ import annotations

import asyncio
import ipaddress
import sys
from collections.abc import Mapping, Sequence
from typing import Protocol


class GatewayProbe(Protocol):
    async def default_gateway(self) -> str | None: ...

    async def lan_address(self, gateway: str | None = None) -> str | None: ...


def parse_default_gateway(output: str) -> str | None:
    """Extract the first IPv4 default route from Windows or POSIX output."""
    for line in output.splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "default" and len(fields) > 2 and fields[1] == "via":
            try:
                ipaddress.IPv4Address(fields[2])
                return fields[2]
            except ValueError:
                continue
        if fields[0] == "0.0.0.0" and len(fields) >= 3:
            try:
                ipaddress.IPv4Address(fields[2])
                return fields[2]
            except ValueError:
                continue
    return None


class SystemGatewayProbe:
    async def default_gateway(self) -> str | None:
        command = (
            ("route", "print", "-4")
            if sys.platform == "win32"
            else ("ip", "route", "show", "default")
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
        except OSError:
            return None
        return parse_default_gateway(stdout.decode(errors="replace"))

    async def lan_address(self, gateway: str | None = None) -> str | None:
        import psutil

        selected_gateway = gateway or await self.default_gateway()
        if selected_gateway is None:
            return None
        addresses = psutil.net_if_addrs()
        return await asyncio.to_thread(self._address_for_gateway, selected_gateway, addresses)

    @staticmethod
    def _address_for_gateway(gateway: str, addresses: Mapping[str, Sequence[object]]) -> str | None:
        try:
            gateway_address = ipaddress.IPv4Address(gateway)
        except ValueError:
            return None
        for interface_addresses in addresses.values():
            for address in interface_addresses:
                value = getattr(address, "address", "").split("%", 1)[0]
                netmask = getattr(address, "netmask", None)
                if not netmask or ":" in value:
                    continue
                try:
                    local = ipaddress.IPv4Address(value)
                    network = ipaddress.IPv4Network(f"{value}/{netmask}", strict=False)
                except ValueError:
                    continue
                if local != gateway_address and gateway_address in network:
                    return str(local)
        return None


def create_gateway_probe() -> GatewayProbe:
    return SystemGatewayProbe()
