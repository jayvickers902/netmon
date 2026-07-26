"""Read-only UPnP WAN connection status probe."""

from __future__ import annotations

import asyncio
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx

from netmon.probes.gateway import GatewayProbe, SystemGatewayProbe

SSDP_ADDRESS = ("239.255.255.250", 1900)
SSDP_MESSAGE = (
    b"M-SEARCH * HTTP/1.1\r\n"
    b"HOST: 239.255.255.250:1900\r\n"
    b'MAN: "ssdp:discover"\r\n'
    b"MX: 2\r\n"
    b"ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
    b"\r\n"
)


@dataclass(frozen=True, slots=True)
class WanStatus:
    connection_status: str
    last_connection_error: str
    uptime_seconds: int
    external_ip: str | None


class UpnpProbe:
    def __init__(
        self,
        lan_ip: str | None = None,
        gateway: GatewayProbe | None = None,
        gateway_ip: str | None = None,
        timeout_seconds: float = 3.0,
    ) -> None:
        self.lan_ip = lan_ip
        self.gateway = gateway or SystemGatewayProbe()
        self.gateway_ip = gateway_ip
        self.timeout_seconds = timeout_seconds

    async def status(self) -> WanStatus | None:
        lan_ip = self.lan_ip or await self.gateway.lan_address(self.gateway_ip)
        if lan_ip is None:
            return None
        locations = await asyncio.to_thread(self._discover, lan_ip)
        for location in locations:
            try:
                return await self._read_status(location)
            except (ElementTree.ParseError, httpx.HTTPError, OSError, ValueError, KeyError):
                continue
        return None

    @staticmethod
    def _discover(lan_ip: str) -> list[str]:
        locations: list[str] = []
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(lan_ip))
            sock.bind((lan_ip, 0))
            sock.settimeout(2.5)
            try:
                sock.sendto(SSDP_MESSAGE, SSDP_ADDRESS)
                while True:
                    try:
                        payload, _ = sock.recvfrom(65535)
                    except TimeoutError:
                        break
                    location = re.search(
                        rb"^LOCATION\s*:\s*(\S+)\s*$", payload, re.IGNORECASE | re.MULTILINE
                    )
                    if location:
                        value = location.group(1).decode("ascii", errors="ignore")
                        if value not in locations:
                            locations.append(value)
            finally:
                sock.close()
        except (OSError, ValueError):
            return []
        return locations

    async def _read_status(self, location: str) -> WanStatus:
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            description_response = await client.get(location)
            description_response.raise_for_status()
            root = ElementTree.fromstring(description_response.content)
            control_url = _wan_ip_control_url(root, location)
            status = await self._soap(client, control_url, "GetStatusInfo")
            address = await self._soap(client, control_url, "GetExternalIPAddress")
        connection_status = _value(status, "NewConnectionStatus")
        last_connection_error = _value(status, "NewLastConnectionError")
        uptime = _value(status, "NewUptime")
        assert connection_status is not None
        assert last_connection_error is not None
        assert uptime is not None
        return WanStatus(
            connection_status=connection_status,
            last_connection_error=last_connection_error,
            uptime_seconds=int(uptime),
            external_ip=_value(address, "NewExternalIPAddress", required=False),
        )

    @staticmethod
    async def _soap(
        client: httpx.AsyncClient, control_url: str, action: str
    ) -> ElementTree.Element:
        service = "urn:schemas-upnp-org:service:WANIPConnection:1"
        body = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{service}"/></s:Body></s:Envelope>'
        )
        response = await client.post(
            control_url,
            content=body.encode(),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"{service}#{action}"',
            },
        )
        response.raise_for_status()
        return ElementTree.fromstring(response.content)


def _wan_ip_control_url(root: ElementTree.Element, location: str) -> str:
    for service in root.iter():
        if _local_name(service.tag) != "service":
            continue
        values = {_local_name(child.tag): (child.text or "").strip() for child in service}
        service_type = values.get("serviceType", "")
        if "WANIPConnection" in service_type and values.get("controlURL"):
            return urljoin(location, values["controlURL"])
    raise KeyError("WANIPConnection control URL not found")


def _value(root: ElementTree.Element, name: str, required: bool = True) -> str | None:
    for item in root.iter():
        if _local_name(item.tag) == name:
            return item.text or ""
    if required:
        raise KeyError(name)
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
