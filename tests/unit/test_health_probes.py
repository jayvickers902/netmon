from __future__ import annotations

from pathlib import Path

import dns.resolver
import httpx
import pytest

from netmon.probes.dns import ResolverDnsProbe
from netmon.probes.gateway import SystemGatewayProbe, parse_default_gateway
from netmon.probes.http import HttpxProbe
from netmon.probes.traceroute import parse_traceroute
from netmon.probes.upnp import UpnpProbe, WanStatus, _wan_ip_control_url


def test_default_gateway_parser_and_interface_selection():
    assert parse_default_gateway("default via 192.168.4.1 dev eth0") == "192.168.4.1"
    assert parse_default_gateway("0.0.0.0 0.0.0.0 10.0.0.1 10.0.0.2") == "10.0.0.1"
    assert parse_default_gateway("default dev tailscale0") is None
    addresses = {
        "eth0": [type("Addr", (), {"address": "192.168.4.143", "netmask": "255.255.255.0"})()]
    }
    assert SystemGatewayProbe._address_for_gateway("192.168.4.1", addresses) == "192.168.4.143"


def test_traceroute_fixtures(fixture_dir: Path):
    windows = parse_traceroute((fixture_dir / "tracert_windows.txt").read_text())
    posix = parse_traceroute((fixture_dir / "traceroute_posix.txt").read_text())
    assert windows[0].ip == "192.168.4.1"
    assert windows[0].rtts_ms == (1.0, 1.0, 1.0)
    assert windows[1].timed_out and windows[1].ip is None
    assert posix[2].host == "edge.example"
    assert posix[2].rtts_ms == (12.5, 13.1, 12.8)


@pytest.mark.asyncio
async def test_http_probe_distinguishes_status_and_timing(monkeypatch, frozen_clock):
    class Response:
        status_code = 204

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aread(self):
            frozen_clock.advance(0.01)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url):
            del method, url
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    result = await HttpxProbe(frozen_clock).fetch("https://example.com")
    assert result.status == "ok"
    assert result.ttfb_ms == 0
    assert result.total_ms == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (httpx.ReadTimeout("timeout"), "timeout"),
        (
            httpx.ConnectError("connect", request=httpx.Request("GET", "https://x")),
            "connect_failure",
        ),
    ],
)
async def test_http_probe_reports_transport_categories(
    monkeypatch, frozen_clock, exception, expected
):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url):
            del method, url
            raise exception

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    result = await HttpxProbe(frozen_clock).fetch("https://example.com")
    assert result.status == expected


@pytest.mark.asyncio
async def test_dns_probe_uses_only_the_requested_resolver(monkeypatch, frozen_clock):
    seen = []

    class Resolver:
        def __init__(self, configure):
            assert configure is False
            self.nameservers = []
            self.timeout = 0
            self.lifetime = 0

        def resolve(self, name, record_type):
            seen.append((self.nameservers, name, record_type))
            return ["93.184.216.34"]

    monkeypatch.setattr(dns.resolver, "Resolver", Resolver)
    result = await ResolverDnsProbe(frozen_clock).resolve("192.0.2.53", "example.com")
    assert result.status == "ok"
    assert result.answers == ("93.184.216.34",)
    assert seen == [(["192.0.2.53"], "example.com", "A")]


def test_dns_probe_maps_common_failures(monkeypatch, frozen_clock):
    class Resolver:
        def __init__(self, configure):
            del configure
            self.nameservers = []
            self.timeout = 0
            self.lifetime = 0

        def resolve(self, name, record_type):
            del name, record_type
            raise dns.resolver.NXDOMAIN

    monkeypatch.setattr(dns.resolver, "Resolver", Resolver)
    result = ResolverDnsProbe(frozen_clock)._resolve_sync("192.0.2.53", "example.com")
    assert result == ("nxdomain", [])


def test_upnp_description_locates_wan_control_url(fixture_dir: Path):
    from xml.etree import ElementTree

    root = ElementTree.fromstring((fixture_dir / "upnp_description.xml").read_bytes())
    assert (
        _wan_ip_control_url(root, "http://192.168.4.1:1900/igd.xml")
        == "http://192.168.4.1:1900/ipc"
    )


@pytest.mark.asyncio
async def test_upnp_reads_only_wan_connection_status(fixture_dir: Path):
    description = (fixture_dir / "upnp_description.xml").read_bytes()
    status_xml = (
        b"<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/'>"
        b"<s:Body><u:GetStatusInfoResponse "
        b"xmlns:u='urn:schemas-upnp-org:service:WANIPConnection:1'>"
        b"<NewConnectionStatus>Connected</NewConnectionStatus>"
        b"<NewLastConnectionError>ERROR_NONE</NewLastConnectionError><NewUptime>42</NewUptime>"
        b"</u:GetStatusInfoResponse></s:Body></s:Envelope>"
    )
    address_xml = (
        b"<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/'>"
        b"<s:Body><u:GetExternalIPAddressResponse "
        b"xmlns:u='urn:schemas-upnp-org:service:WANIPConnection:1'>"
        b"<NewExternalIPAddress>198.51.100.8</NewExternalIPAddress>"
        b"</u:GetExternalIPAddressResponse></s:Body></s:Envelope>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=description)
        return httpx.Response(
            200,
            content=status_xml if b"GetStatusInfo" in request.content else address_xml,
        )

    probe = UpnpProbe(transport=httpx.MockTransport(handler))
    result = await probe._read_status("http://192.168.4.1:1900/igd.xml")
    assert result == WanStatus("Connected", "ERROR_NONE", 42, "198.51.100.8")


def test_upnp_discovery_pins_lan_interface(monkeypatch):
    calls: list[tuple[str, object]] = []

    class FakeSocket:
        def setsockopt(self, level, option, value):
            calls.append(("setsockopt", (level, option, value)))

        def bind(self, address):
            calls.append(("bind", address))

        def settimeout(self, value):
            del value

        def sendto(self, payload, address):
            del payload, address

        def recvfrom(self, size):
            del size
            raise TimeoutError

        def close(self):
            pass

    monkeypatch.setattr("socket.socket", lambda *args: FakeSocket())
    assert UpnpProbe._discover("192.168.4.143") == []
    assert ("bind", ("192.168.4.143", 0)) in calls
    assert any(item[0] == "setsockopt" and item[1][2] == b"\xc0\xa8\x04\x8f" for item in calls)
