"""ARP table probes and platform-independent parsers."""

from __future__ import annotations

import asyncio
import ctypes
import ipaddress
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ArpEntry:
    ip: str
    mac: str


class ArpProbe(Protocol):
    async def entries(self) -> list[ArpEntry]: ...


class InterfaceProbe(Protocol):
    async def local_subnets(self) -> list[ipaddress.IPv4Network]: ...


def normalize_mac(value: str) -> str:
    compact = re.sub(r"[^0-9a-fA-F]", "", value).lower()
    if len(compact) != 12:
        return value.lower()
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def parse_get_ip_net_table(data: bytes) -> list[ArpEntry]:
    """Parse the native MIB_IPNETTABLE layout returned by GetIpNetTable."""
    if len(data) < 4:
        return []
    count = int.from_bytes(data[:4], "little")
    entries: list[ArpEntry] = []
    row_size = 24
    for index in range(count):
        offset = 4 + index * row_size
        if offset + row_size > len(data):
            break
        physical_length = int.from_bytes(data[offset + 4 : offset + 8], "little")
        physical = data[offset + 8 : offset + 16][:physical_length]
        raw_ip = data[offset + 16 : offset + 20]
        row_type = int.from_bytes(data[offset + 20 : offset + 24], "little")
        mac = physical[:6]
        if row_type in (3, 4) and physical_length >= 6 and any(mac) and not mac[0] & 1:
            entries.append(
                ArpEntry(
                    str(ipaddress.IPv4Address(raw_ip)),
                    normalize_mac(mac.hex()),
                )
            )
    return entries


def parse_arp_output(output: str) -> list[ArpEntry]:
    """Parse common Windows ``arp -a`` and Linux/BSD ``arp`` output."""
    entries: list[ArpEntry] = []
    windows = re.compile(
        r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+"
        r"([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})\s+",
        re.MULTILINE,
    )
    linux = re.compile(
        r"\(?(\d{1,3}(?:\.\d{1,3}){3})\)?\s+at\s+"
        r"([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})",
        re.MULTILINE,
    )
    for match in (*windows.finditer(output), *linux.finditer(output)):
        try:
            ipaddress.IPv4Address(match.group(1))
        except ValueError:
            continue
        entries.append(ArpEntry(match.group(1), normalize_mac(match.group(2))))
    return entries


def parse_ip_neigh(output: str) -> list[ArpEntry]:
    entries: list[ArpEntry] = []
    pattern = re.compile(
        r"^(\d{1,3}(?:\.\d{1,3}){3})\s+dev\s+\S+\s+lladdr\s+"
        r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\s+(?:\S+)"
    )
    for line in output.splitlines():
        match = pattern.search(line)
        if match:
            entries.append(ArpEntry(match.group(1), normalize_mac(match.group(2))))
    return entries


class WindowsArpProbe:
    async def entries(self) -> list[ArpEntry]:
        try:
            return await asyncio.to_thread(self._get_ip_net_table)
        except (OSError, ctypes.ArgumentError):
            return parse_arp_output(await self._arp_command())

    @staticmethod
    def _get_ip_net_table() -> list[ArpEntry]:
        iphlpapi = ctypes.WinDLL("iphlpapi.dll")
        function = iphlpapi.GetIpNetTable
        size = ctypes.c_ulong(0)
        result = function(None, ctypes.byref(size), False)
        if result not in (0, 122):
            raise OSError(result, "GetIpNetTable failed")
        buffer = ctypes.create_string_buffer(size.value)
        result = function(buffer, ctypes.byref(size), False)
        if result != 0:
            raise OSError(result, "GetIpNetTable failed")
        return parse_get_ip_net_table(buffer.raw[: size.value])

    @staticmethod
    async def _arp_command() -> str:
        process = await asyncio.create_subprocess_exec(
            "arp", "-a", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        return stdout.decode(errors="replace")


class PosixArpProbe:
    async def entries(self) -> list[ArpEntry]:
        process = await asyncio.create_subprocess_exec(
            "ip", "neigh", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            return parse_ip_neigh(stdout.decode(errors="replace"))
        return parse_arp_output(await self._arp_command())

    @staticmethod
    async def _arp_command() -> str:
        process = await asyncio.create_subprocess_exec(
            "arp", "-an", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        return stdout.decode(errors="replace")


class SystemInterfaceProbe:
    async def local_subnets(self) -> list[ipaddress.IPv4Network]:
        import psutil

        return await asyncio.to_thread(self._read_subnets, psutil.net_if_addrs())

    @staticmethod
    def _read_subnets(addresses: Mapping[str, Sequence[object]]) -> list[ipaddress.IPv4Network]:
        result: set[ipaddress.IPv4Network] = set()
        for interface_addresses in addresses.values():
            for address in interface_addresses:
                ip = getattr(address, "address", "").split("%", 1)[0]
                netmask = getattr(address, "netmask", None)
                if not netmask or ":" in ip:
                    continue
                try:
                    network = ipaddress.ip_network(f"{ip}/{netmask}", strict=False)
                except ValueError:
                    continue
                if (
                    isinstance(network, ipaddress.IPv4Network)
                    and not network.is_loopback
                    and not network.is_link_local
                ):
                    result.add(network)
        return sorted(result, key=lambda network: (int(network.network_address), network.prefixlen))


def create_arp_probe() -> ArpProbe:
    return WindowsArpProbe() if sys.platform == "win32" else PosixArpProbe()


def create_interface_probe() -> InterfaceProbe:
    return SystemInterfaceProbe()
