from __future__ import annotations

import pytest

from netmon.collectors.managed_host import ManagedHostNicCollector
from netmon.probes.managed_host import ManagedHostProbeError, RemoteNicCounters


def nic(name: str, mac: str, sent: int, received: int) -> RemoteNicCounters:
    return RemoteNicCounters(name, mac, sent, received)


class FakeProbe:
    def __init__(self, results: list[list[RemoteNicCounters] | ManagedHostProbeError]) -> None:
        self._results = iter(results)

    async def counters(self) -> list[RemoteNicCounters]:
        result = next(self._results)
        if isinstance(result, ManagedHostProbeError):
            raise result
        return result


def rates(samples):
    return {
        sample.metric_kind: sample.value
        for sample in samples
        if sample.metric_kind.endswith("rate")
    }


@pytest.mark.asyncio
async def test_first_and_second_samples_establish_then_emit_host_rates(frozen_clock):
    collector = ManagedHostNicCollector(
        FakeProbe(
            [
                [nic("Ethernet", "AA-BB-CC-DD-EE-FF", 100, 200)],
                [nic("Ethernet", "aa:bb:cc:dd:ee:ff", 160, 260)],
            ]
        ),
        frozen_clock,
        "media-pc",
        15,
    )
    first = await collector.collect()
    assert [(sample.metric_kind, sample.value) for sample in first] == [
        ("managed_host_available", 1.0)
    ]

    frozen_clock.advance(2)
    second = await collector.collect()
    assert rates(second) == {
        "managed_host_nic_bytes_sent_rate": 30.0,
        "managed_host_nic_bytes_recv_rate": 30.0,
    }
    assert {sample.target for sample in second} == {"media-pc"}
    assert {sample.source for sample in second} == {"managed_host_ssh"}
    assert {sample.unit for sample in second if sample.metric_kind.endswith("rate")} == {
        "bytes_per_second"
    }
    assert collector.name == "managed_host_nic:media-pc"
    assert collector.interval == 15


@pytest.mark.asyncio
async def test_aggregates_all_adapters_when_not_selected(frozen_clock):
    collector = ManagedHostNicCollector(
        FakeProbe(
            [
                [
                    nic("Ethernet", "aa:aa:aa:aa:aa:01", 10, 20),
                    nic("Wi-Fi", "aa:aa:aa:aa:aa:02", 30, 40),
                ],
                [
                    nic("Ethernet", "aa:aa:aa:aa:aa:01", 30, 60),
                    nic("Wi-Fi", "aa:aa:aa:aa:aa:02", 50, 70),
                ],
            ]
        ),
        frozen_clock,
        "server",
        10,
    )
    await collector.collect()
    frozen_clock.advance(2)
    assert rates(await collector.collect()) == {
        "managed_host_nic_bytes_sent_rate": 20.0,
        "managed_host_nic_bytes_recv_rate": 35.0,
    }


@pytest.mark.asyncio
async def test_selectors_are_case_insensitive_and_both_must_match(frozen_clock):
    collector = ManagedHostNicCollector(
        FakeProbe(
            [
                [
                    nic("Ethernet", "AA-BB-CC-DD-EE-01", 1, 1),
                    nic("Wi-Fi", "aa:bb:cc:dd:ee:02", 10, 10),
                ],
                [
                    nic("ethernet", "aa:bb:cc:dd:ee:01", 11, 21),
                    nic("Wi-Fi", "aa:bb:cc:dd:ee:02", 100, 100),
                ],
            ]
        ),
        frozen_clock,
        "server",
        10,
        adapter_name="ETHERNET",
        expected_mac="aa:bb:cc:dd:ee:01",
    )
    await collector.collect()
    frozen_clock.advance(1)
    assert rates(await collector.collect()) == {
        "managed_host_nic_bytes_sent_rate": 10.0,
        "managed_host_nic_bytes_recv_rate": 20.0,
    }


@pytest.mark.asyncio
async def test_counter_reset_is_discarded_without_a_spike(frozen_clock):
    collector = ManagedHostNicCollector(
        FakeProbe(
            [
                [nic("Ethernet", "aa:bb:cc:dd:ee:01", 100, 100)],
                [nic("Ethernet", "aa:bb:cc:dd:ee:01", 5, 130)],
            ]
        ),
        frozen_clock,
        "server",
        10,
    )
    await collector.collect()
    frozen_clock.advance(2)
    assert rates(await collector.collect()) == {"managed_host_nic_bytes_recv_rate": 15.0}


@pytest.mark.asyncio
async def test_failure_and_empty_result_preserve_baseline_for_recovery(frozen_clock):
    collector = ManagedHostNicCollector(
        FakeProbe(
            [
                [nic("Ethernet", "aa:bb:cc:dd:ee:01", 0, 0)],
                ManagedHostProbeError("offline"),
                [],
                [nic("Ethernet", "aa:bb:cc:dd:ee:01", 40, 80)],
            ]
        ),
        frozen_clock,
        "server",
        10,
    )
    await collector.collect()
    frozen_clock.advance(2)
    assert (await collector.collect())[0].value == 0.0
    frozen_clock.advance(2)
    assert (await collector.collect())[0].value == 0.0
    frozen_clock.advance(4)
    assert rates(await collector.collect()) == {
        "managed_host_nic_bytes_sent_rate": 5.0,
        "managed_host_nic_bytes_recv_rate": 10.0,
    }


@pytest.mark.asyncio
async def test_no_matching_selector_is_unavailable(frozen_clock):
    collector = ManagedHostNicCollector(
        FakeProbe([[nic("Ethernet", "aa:bb:cc:dd:ee:01", 1, 1)]]),
        frozen_clock,
        "server",
        10,
        expected_mac="aa:bb:cc:dd:ee:99",
    )
    assert [(sample.metric_kind, sample.value) for sample in await collector.collect()] == [
        ("managed_host_available", 0.0)
    ]
