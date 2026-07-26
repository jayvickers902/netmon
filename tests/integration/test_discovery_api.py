from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from netmon.api.app import create_app
from netmon.collectors.discovery import DiscoveryCollector
from netmon.config import DiscoveryConfig, NetmonSettings
from netmon.db import Store
from netmon.probes.arp import ArpEntry
from netmon.scheduler import Scheduler


def test_fake_collector_store_api_identity_and_offline(tmp_path, frozen_clock):
    from tests.conftest import (
        FakeArpProbe,
        FakeHostnameProbe,
        FakeIcmpProbe,
        FakeInterfaceProbe,
        FakeOuiLookup,
    )

    arp = FakeArpProbe([ArpEntry("192.168.1.1", "aa:bb:cc:00:00:01")])
    icmp = FakeIcmpProbe({"192.168.1.1", "192.168.1.2"})
    store = Store(tmp_path / "netmon.db")
    collector = DiscoveryCollector(
        arp,
        icmp,
        FakeOuiLookup(),
        FakeHostnameProbe({"192.168.1.1": "lamp.local", "192.168.1.2": "lamp.local"}),
        FakeInterfaceProbe(["192.168.1.0/30"]),
        frozen_clock,
        DiscoveryConfig(subnets=["192.168.1.0/30"], offline_after_misses=2),
        store.record_discovery,
    )
    asyncio.run(collector.collect())
    arp.current = [ArpEntry("192.168.1.2", "aa:bb:cc:00:00:01")]
    asyncio.run(collector.collect())
    app = create_app(NetmonSettings(), store=store, scheduler=Scheduler([], clock=frozen_clock))
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        devices = client.get("/devices").json()
        assert len(devices) == 1
        assert devices[0]["hostname"] == "lamp.local"
        assert devices[0]["vendor"] == "Test Vendor"
        assert {item["ip"] for item in devices[0]["ips"]} == {"192.168.1.1", "192.168.1.2"}
        assert client.get(f"/devices/{devices[0]['id']}").json()["mac"] == "aa:bb:cc:00:00:01"
        assert client.get("/events").json()[0]["kind"] == "device_join"
        assert "netmon" in client.get("/").text

    collector.observation_sink([], frozen_clock.now(), 2)
    collector.observation_sink([], frozen_clock.now(), 2)
    assert store.list_devices()[0]["online"] is False
