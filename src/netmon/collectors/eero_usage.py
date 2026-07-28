"""Opt-in, soft-failing per-device usage collector for eero cloud rates."""

from __future__ import annotations

from datetime import datetime

from netmon.collectors.base import Clock, Collector
from netmon.config import EeroConfig
from netmon.investigation import InvestigationState
from netmon.models import Sample
from netmon.probes.eero_cloud import (
    EeroAuthenticationError,
    EeroCloudError,
    EeroCloudProbe,
    EeroDeviceRate,
)
from netmon.probes.eero_token import EeroTokenStore


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
    ) -> None:
        self.probe = probe
        self.token = token
        self.clock = clock
        self.config = config
        self.token_store = token_store
        self.investigation = investigation
        self.burst_interval_seconds = burst_interval_seconds

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
            rates = await self.probe.device_rates(self.token, self.config.network_id)
        except EeroAuthenticationError:
            try:
                rates = await self._refresh_and_retry()
            except EeroCloudError:
                return [self._unavailable(now)]
            except Exception:
                return [self._unavailable(now)]
        except EeroCloudError:
            return [self._unavailable(now)]
        except Exception:
            # A schema change or unexpected transport failure must not reach the scheduler.
            return [self._unavailable(now)]
        samples = [
            Sample("eero_usage_available", "eero", "boolean", now, 1.0, "eero_cloud"),
            # Devices without rate fields are skipped upstream. Recording how many did report
            # keeps a silent schema change visible: available stays 1 while this sits at 0.
            Sample("eero_devices_reporting", "eero", "count", now, float(len(rates)), "eero_cloud"),
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
        return samples

    async def close(self) -> None:
        await self.probe.aclose()

    async def _refresh_and_retry(self) -> list[EeroDeviceRate]:
        if self.token is None:
            raise EeroAuthenticationError("eero cloud authentication failed")
        refreshed = await self.probe.refresh(self.token)
        self.token = refreshed
        if self.token_store is not None:
            await self.token_store.save(refreshed)
        return await self.probe.device_rates(refreshed, self.config.network_id)

    @staticmethod
    def _unavailable(now: datetime) -> Sample:
        return Sample("eero_usage_available", "eero", "boolean", now, 0.0, "eero_cloud")
