"""Opt-in, soft-failing per-device usage collector for eero cloud rates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from netmon.collectors.base import Clock, Collector
from netmon.config import EeroConfig
from netmon.investigation import InvestigationState
from netmon.models import Sample
from netmon.probes.eero_cloud import (
    EeroAuthenticationError,
    EeroCloudError,
    EeroCloudProbe,
    EeroDevice,
    EeroDeviceRate,
    EeroNetworkSnapshot,
    EeroNode,
)
from netmon.probes.eero_token import EeroTokenStore

EeroDeviceSink = Callable[[list[EeroDevice]], Awaitable[None] | None]


class EeroUsageCollector(Collector):
    def __init__(
        self,
        probe: EeroCloudProbe,
        token: str | None,
        clock: Clock,
        config: EeroConfig,
        token_store: EeroTokenStore | None = None,
        investigation: InvestigationState | None = None,
        burst_interval_seconds: float | None = None,
        device_sink: EeroDeviceSink | None = None,
    ) -> None:
        self.probe = probe
        self.token = token
        self.clock = clock
        self.config = config
        self.token_store = token_store
        self.investigation = investigation
        self.burst_interval_seconds = burst_interval_seconds
        self.device_sink = device_sink
        self._last_speedtest_at: datetime | None = None

    @property
    def name(self) -> str:
        return "eero_usage"

    @property
    def interval(self) -> float:
        if (
            self.investigation is not None
            and self.investigation.active
            and self.burst_interval_seconds is not None
        ):
            return self.burst_interval_seconds
        return self.config.interval_seconds

    async def collect(self) -> list[Sample]:
        now = self.clock.now()
        if not self.token:
            return [self._unavailable(now)]
        try:
            snapshot = await self.probe.network_snapshot(self.token, self.config.network_id)
        except EeroAuthenticationError:
            try:
                snapshot = await self._refresh_and_retry()
            except EeroCloudError:
                return [self._unavailable(now)]
            except Exception:
                return [self._unavailable(now)]
        except EeroCloudError:
            return [self._unavailable(now)]
        except Exception:
            # A schema change or unexpected transport failure must not reach the scheduler.
            return [self._unavailable(now)]

        rates = [
            EeroDeviceRate(
                device.device_id, device.upload_bytes_per_second, device.download_bytes_per_second
            )
            for device in snapshot.devices
            if device.upload_bytes_per_second is not None
            and device.download_bytes_per_second is not None
        ]
        samples = [
            Sample("eero_usage_available", "eero", "boolean", now, 1.0, "eero_cloud"),
            # Devices without rate fields are skipped upstream. Recording how many did report
            # keeps a silent schema change visible: available stays 1 while this sits at 0.
            Sample("eero_devices_reporting", "eero", "count", now, float(len(rates)), "eero_cloud"),
            Sample(
                "wan_upstream_estimated_rate",
                "wan",
                "bytes_per_second",
                now,
                sum(rate.upload_bytes_per_second for rate in rates),
                "eero_cloud",
            ),
        ]
        for rate in rates:
            samples.extend(
                (
                    Sample(
                        "eero_device_upload_rate",
                        rate.device_id,
                        "bytes_per_second",
                        now,
                        rate.upload_bytes_per_second,
                        "eero_cloud",
                    ),
                    Sample(
                        "eero_device_download_rate",
                        rate.device_id,
                        "bytes_per_second",
                        now,
                        rate.download_bytes_per_second,
                        "eero_cloud",
                    ),
                )
            )
        for device in snapshot.devices:
            if device.signal_dbm is not None:
                samples.append(
                    Sample(
                        "eero_device_rssi",
                        device.device_id,
                        "dbm",
                        now,
                        device.signal_dbm,
                        "eero_cloud",
                    )
                )

        if self.device_sink is not None:
            sink_result = self.device_sink(snapshot.devices)
            if sink_result is not None:
                await sink_result

        for node in await self._node_health(snapshot):
            target = node.target
            if target is None:
                continue
            samples.append(
                Sample(
                    "eero_node_online",
                    target,
                    "boolean",
                    now,
                    1.0 if node.status == "green" else 0.0,
                    "eero_cloud",
                )
            )
            if node.connected_clients is not None:
                samples.append(
                    Sample(
                        "eero_node_clients",
                        target,
                        "count",
                        now,
                        float(node.connected_clients),
                        "eero_cloud",
                    )
                )

        speed = snapshot.speed
        if speed is not None and speed.measured_at != self._last_speedtest_at:
            self._last_speedtest_at = speed.measured_at
            samples.extend(
                (
                    Sample(
                        "eero_speedtest_upload_rate",
                        "capacity",
                        "Mbps",
                        speed.measured_at,
                        speed.upload_mbps,
                        "eero_cloud",
                    ),
                    Sample(
                        "eero_speedtest_download_rate",
                        "capacity",
                        "Mbps",
                        speed.measured_at,
                        speed.download_mbps,
                        "eero_cloud",
                    ),
                )
            )
        return samples

    async def close(self) -> None:
        await self.probe.aclose()

    async def _refresh_and_retry(self) -> EeroNetworkSnapshot:
        if self.token is None:
            raise EeroAuthenticationError("eero cloud authentication failed")
        refreshed = await self.probe.refresh(self.token)
        self.token = refreshed
        if self.token_store is not None:
            await self.token_store.save(refreshed)
        return await self.probe.network_snapshot(refreshed, self.config.network_id)

    async def _node_health(self, snapshot: EeroNetworkSnapshot) -> list[EeroNode]:
        # One extra GET per cycle; a failure here must degrade to "no node samples" rather
        # than blank the device/usage samples already collected above.
        assert self.token is not None
        try:
            return await self.probe.node_health(self.token, snapshot.network_id)
        except Exception:
            return []

    @staticmethod
    def _unavailable(now: datetime) -> Sample:
        return Sample("eero_usage_available", "eero", "boolean", now, 0.0, "eero_cloud")
