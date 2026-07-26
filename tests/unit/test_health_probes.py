from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from netmon.probes.gateway import SystemGatewayProbe, parse_default_gateway
from netmon.probes.http import HttpxProbe
from netmon.probes.traceroute import parse_traceroute
from netmon.probes.upnp import UpnpProbe, _wan_ip_control_url


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


def test_upnp_description_locates_wan_control_url(fixture_dir: Path):
    from xml.etree import ElementTree

    root = ElementTree.fromstring((fixture_dir / "upnp_description.xml").read_bytes())
    assert (
        _wan_ip_control_url(root, "http://192.168.4.1:1900/igd.xml")
        == "http://192.168.4.1:1900/ipc"
    )


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
