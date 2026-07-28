"""Impure orchestration for on-demand investigations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from netmon.analysis.upload_contention import (
    AnalysisInputs,
    DeviceAttribution,
    DeviceSeries,
    InvestigationReport,
    SeriesPoint,
    Thresholds,
    analyse,
)
from netmon.collectors.base import Clock
from netmon.config import InvestigationConfig
from netmon.db.store import Store
from netmon.investigation.state import InvestigationState
from netmon.models import Investigation


class InvestigationService:
    def __init__(
        self,
        store: Store,
        state: InvestigationState,
        clock: Clock,
        config: InvestigationConfig,
        expected_macs: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store
        self.state = state
        self.clock = clock
        self.config = config
        self.expected_macs = {name: mac.lower() for name, mac in (expected_macs or {}).items()}

    def start(
        self,
        duration_seconds: float | None,
        label: str | None,
        note: str | None,
        trigger: str = "manual",
    ) -> Investigation:
        duration = (
            self.config.default_duration_seconds if duration_seconds is None else duration_seconds
        )
        if duration <= 0 or duration > self.config.max_duration_seconds:
            raise ValueError("duration_seconds exceeds the configured maximum")
        started = self.clock.now()
        ends = started + timedelta(seconds=duration)
        investigation = self.store.create_investigation(started, ends, label, note, trigger)
        try:
            self.state.start(investigation.id, started, ends)
        except Exception:
            # The database row is authoritative if arming unexpectedly fails; never claim
            # that a collector is bursting when the API could not arm its state.
            raise
        return investigation

    def active(self) -> bool:
        """Whether an investigation is currently armed, for callers that must not start a second."""

        return self.state.active

    def stop(self, investigation_id: int) -> Investigation:
        investigation = self.store.get_investigation(investigation_id)
        if investigation is None:
            raise KeyError(investigation_id)
        if investigation.status != "running":
            return investigation
        ended = self.clock.now()
        report = self._report_for(investigation, ended)
        result = self.store.complete_investigation(investigation_id, ended, _report_dict(report))
        self.state.stop()
        if result is None:
            raise KeyError(investigation_id)
        return result

    def finalise_if_expired(self) -> Investigation | None:
        running = self.store.active_investigation(self.clock.now())
        if running is None or running.ends_at > self.clock.now():
            return None
        report = self._report_for(running, running.ends_at)
        result = self.store.complete_investigation(
            running.id, running.ends_at, _report_dict(report)
        )
        self.state.stop()
        return result

    def report(self, investigation_id: int) -> InvestigationReport:
        investigation = self.store.get_investigation(investigation_id)
        if investigation is None:
            raise KeyError(investigation_id)
        if investigation.status != "running" and investigation.verdict is not None:
            return _report_from_dict(investigation.verdict)
        end = investigation.ended_at or min(self.clock.now(), investigation.ends_at)
        return self._report_for(investigation, end)

    def _report_for(self, investigation: Investigation, ended_at: datetime) -> InvestigationReport:
        start = investigation.started_at
        window_metrics = self.store.list_metrics(start=start, end=ended_at, limit=100000)

        def points(
            kind: str, target: str | None = None, source: str | None = None
        ) -> list[SeriesPoint]:
            return [
                SeriesPoint(float(row["ts"]), float(row["value"]))
                for row in window_metrics
                if row["kind"] == kind
                and (target is None or row["target"] == target)
                and (source is None or row["source"] == source)
            ]

        internet = points("latency_rtt_ms", "1.1.1.1") + points("latency_rtt_ms", "8.8.8.8")
        loss = points("latency_loss_percent", "1.1.1.1") + points("latency_loss_percent", "8.8.8.8")
        gateway = points("latency_rtt_ms", "gateway")
        gateway_loss = points("latency_loss_percent", "gateway")
        # The classifier's threshold is a fraction; storage is explicitly percent (0..100).
        loss = [SeriesPoint(point.ts, point.value / 100.0) for point in loss]
        gateway_loss = [SeriesPoint(point.ts, point.value / 100.0) for point in gateway_loss]

        devices = {str(row["mac"]).lower(): row for row in self.store.list_devices()}
        managed_macs = set(self.expected_macs.values())
        # Keep one series per eero MAC; list_metrics returns one row per sample. A device that is
        # also polled over SSH is skipped here so it is not counted twice in the estimated total.
        series: dict[str, DeviceSeries] = {}
        for row in window_metrics:
            if row["kind"] != "eero_device_upload_rate" or row["source"] != "eero_cloud":
                continue
            mac = str(row["target"]).lower()
            if mac in managed_macs:
                continue
            device = devices.get(mac)
            series.setdefault(
                mac,
                DeviceSeries(
                    mac,
                    (device or {}).get("label") or (device or {}).get("hostname") or mac,
                    "eero_cloud",
                    [],
                ),
            ).points.append(SeriesPoint(float(row["ts"]), float(row["value"])))
        device_upload = list(series.values()) + [
            DeviceSeries(
                name,
                name,
                "managed_host_ssh",
                points("managed_host_nic_bytes_sent_rate", name, "managed_host_ssh"),
            )
            for name in self.expected_macs
        ]
        # This host is a suspect too, and ruling it out is as useful as ruling Owen in. Only the
        # physical LAN adapter counts: loopback is not WAN traffic at all, and VPN adapters carry
        # the same bytes as the underlying NIC, so including either would inflate the total.
        host_adapters = {
            str(row["target"])
            for row in window_metrics
            if row["kind"] == "host_nic_bytes_sent_rate"
            and _is_physical_adapter(str(row["target"]))
        }
        device_upload.extend(
            DeviceSeries(
                adapter,
                f"this host ({adapter})",
                "local",
                points("host_nic_bytes_sent_rate", adapter, "host_nic"),
            )
            for adapter in sorted(host_adapters)
        )
        history = self.store.list_metrics(
            kind="latency_rtt_ms",
            start=start - timedelta(hours=24),
            end=start - timedelta(microseconds=1),
            limit=100000,
        )
        baseline = _p50([float(row["value"]) for row in history if row["target"] != "gateway"])
        gateway_history = [float(row["value"]) for row in history if row["target"] == "gateway"]
        capacity = self.config.uplink_bps
        if capacity is None:
            latest = self.store.latest_metric("speedtest_upload_capacity", "speedtest")
            capacity = None if latest is None else float(latest["value"]) * 1_000_000.0
        reconnects = len(
            self.store.list_filtered_events(kind="wan_reconnect", start=start, end=ended_at)
        )
        inputs = AnalysisInputs(
            start.timestamp(),
            ended_at.timestamp(),
            internet,
            loss,
            gateway,
            gateway_loss,
            points("wan_uptime_seconds", "wan"),
            device_upload,
            baseline,
            _p50(gateway_history),
            capacity,
            reconnects,
        )
        thresholds = Thresholds(
            self.config.latency_degraded_ms,
            self.config.gateway_degraded_ms,
            self.config.loss_degraded_fraction,
            self.config.saturation_fraction,
            self.config.bucket_seconds,
        )
        return analyse(inputs, thresholds, investigation.id)


def _p50(values: list[float]) -> float | None:
    return median(values) if values else None


# Substrings, not exact names: adapter naming varies per driver and per install (for example
# "OpenVPN Data Channel Offload for NordVPN"), so an exact-match denylist would silently rot.
_VIRTUAL_ADAPTER_MARKERS = ("loopback", "nordlynx", "tailscale", "openvpn", "wintun", "virtual")


def _is_physical_adapter(name: str) -> bool:
    folded = name.casefold()
    return not any(marker in folded for marker in _VIRTUAL_ADAPTER_MARKERS)


def _report_dict(report: InvestigationReport) -> dict[str, Any]:
    return {
        "investigation_id": report.investigation_id,
        "started_at": report.started_at.isoformat(),
        "ended_at": report.ended_at.isoformat(),
        **{
            key: getattr(report, key)
            for key in (
                "verdict",
                "confidence",
                "headline",
                "reasons",
                "caveats",
                "baseline_rtt_ms",
                "degraded_rtt_p95_ms",
                "gateway_rtt_p95_ms",
                "degraded_seconds",
                "total_seconds",
                "peak_upload_bps",
                "uplink_capacity_bps",
                "peak_utilisation",
                "coverage",
            )
        },
        "attributions": [
            a.__dict__
            if hasattr(a, "__dict__")
            else {
                "target": a.target,
                "label": a.label,
                "source": a.source,
                "mean_upload_bps": a.mean_upload_bps,
                "peak_upload_bps": a.peak_upload_bps,
                "share_of_upload": a.share_of_upload,
                "correlation_with_latency": a.correlation_with_latency,
                "seconds_covered": a.seconds_covered,
            }
            for a in report.attributions
        ],
    }


def _report_from_dict(data: dict[str, Any]) -> InvestigationReport:
    values = dict(data)
    values["started_at"] = datetime.fromisoformat(str(values["started_at"]))
    values["ended_at"] = datetime.fromisoformat(str(values["ended_at"]))
    values["attributions"] = [DeviceAttribution(**item) for item in values["attributions"]]
    return InvestigationReport(**values)
