from __future__ import annotations

import ipaddress

from netmon.probes.arp import (
    ArpEntry,
    SystemInterfaceProbe,
    normalize_mac,
    parse_arp_output,
    parse_get_ip_net_table,
    parse_ip_neigh,
)
from netmon.probes.oui import BundledOuiLookup, oui_prefix


def test_get_ip_net_table_fixture(fixture_dir):
    data = bytes.fromhex((fixture_dir / "get_ip_net_table.hex").read_text())
    assert parse_get_ip_net_table(data) == [ArpEntry(ip="192.168.1.2", mac="00:15:5d:11:22:33")]


def test_arp_fixtures(fixture_dir):
    windows = parse_arp_output((fixture_dir / "arp_windows.txt").read_text())
    linux = parse_arp_output((fixture_dir / "arp_linux.txt").read_text())
    neigh = parse_ip_neigh((fixture_dir / "ip_neigh.txt").read_text())
    assert [entry.ip for entry in windows] == ["192.168.1.1", "192.168.1.2"]
    assert linux == windows
    assert neigh[1].mac == "00:15:5d:11:22:33"


def test_oui_lookup_and_mac_normalization():
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert oui_prefix("b8:27:eb:00:00:01") == "B827EB"
    lookup = BundledOuiLookup()
    assert lookup.vendor_for("b8:27:eb:00:00:01") == "Raspberry Pi Foundation"
    assert lookup.vendor_for("ff:ff:ff:ff:ff:ff") is None


def test_local_subnets_excludes_link_local_networks():
    addresses = {
        "lan": [type("Addr", (), {"address": "192.168.4.143", "netmask": "255.255.255.0"})()],
        "apipa": [type("Addr", (), {"address": "169.254.20.7", "netmask": "255.255.0.0"})()],
    }

    assert SystemInterfaceProbe._read_subnets(addresses) == [
        ipaddress.IPv4Network("192.168.4.0/24")
    ]
