"""Default-route and LAN-interface discovery."""

from __future__ import annotations

import asyncio
import ctypes
import ipaddress
import sys
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Protocol


class GatewayProbe(Protocol):
    async def default_gateway(self) -> str | None: ...

    async def lan_address(self, gateway: str | None = None) -> str | None: ...


class _MibIpForwardRow(ctypes.Structure):
    """IPv4 route returned by the Windows ``GetBestRoute`` API."""

    _fields_ = tuple(
        (name, ctypes.c_uint32)
        for name in (
            "destination",
            "mask",
            "policy",
            "next_hop",
            "interface_index",
            "route_type",
            "protocol",
            "age",
            "next_hop_as",
            "metric_1",
            "metric_2",
            "metric_3",
            "metric_4",
            "metric_5",
        )
    )


@lru_cache(maxsize=1)
def _gateway_iphlpapi() -> ctypes.WinDLL:
    library = ctypes.WinDLL("iphlpapi.dll")
    library.GetBestRoute.argtypes = (
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(_MibIpForwardRow),
    )
    library.GetBestRoute.restype = ctypes.c_uint32
    return library


def _is_windows() -> bool:
    return sys.platform == "win32"


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
        if _is_windows():
            return self._windows_default_gateway()
        command = ("ip", "route", "show", "default")
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

    @staticmethod
    def _windows_default_gateway() -> str | None:
        route = _MibIpForwardRow()
        # Asking how Windows routes a public address resolves the active IPv4
        # default route while respecting interface metrics.
        destination = int.from_bytes(ipaddress.IPv4Address("1.1.1.1").packed, "little")
        try:
            result = _gateway_iphlpapi().GetBestRoute(destination, 0, ctypes.byref(route))
        except (OSError, ctypes.ArgumentError):
            return None
        if result != 0 or route.next_hop == 0:
            return None
        packed = int(route.next_hop).to_bytes(4, "little")
        return str(ipaddress.IPv4Address(packed))

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
