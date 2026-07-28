"""Classify upload contention from an investigation's already-collected samples."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from math import isnan
from typing import Any


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    ts: float
    value: float


@dataclass(frozen=True, slots=True)
class DeviceSeries:
    target: str
    label: str | None
    source: str
    points: list[SeriesPoint]


@dataclass(frozen=True, slots=True)
class AnalysisInputs:
    started_at: float
    ended_at: float
    internet_rtt: list[SeriesPoint]
    internet_loss: list[SeriesPoint]
    gateway_rtt: list[SeriesPoint]
    gateway_loss: list[SeriesPoint]
    wan_uptime: list[SeriesPoint]
    device_upload: list[DeviceSeries]
    baseline_internet_rtt_ms: float | None
    baseline_gateway_rtt_ms: float | None
    uplink_bps: float | None
    wan_reconnect_events: int


@dataclass(frozen=True, slots=True)
class Thresholds:
    latency_degraded_ms: float
    gateway_degraded_ms: float
    loss_degraded_fraction: float
    saturation_fraction: float
    bucket_seconds: float


@dataclass(frozen=True, slots=True)
class DeviceAttribution:
    target: str
    label: str | None
    source: str
    mean_upload_bps: float
    peak_upload_bps: float
    share_of_upload: float
    correlation_with_latency: float | None
    seconds_covered: float


@dataclass(frozen=True, slots=True)
class InvestigationReport:
    investigation_id: int
    started_at: datetime
    ended_at: datetime
    verdict: str
    confidence: str
    headline: str
    reasons: list[str]
    caveats: list[str]
    baseline_rtt_ms: float | None
    degraded_rtt_p95_ms: float | None
    gateway_rtt_p95_ms: float | None
    degraded_seconds: float
    total_seconds: float
    peak_upload_bps: float | None
    uplink_capacity_bps: float | None
    peak_utilisation: float | None
    attributions: list[DeviceAttribution]
    coverage: dict[str, Any]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _bucketise(
    points: list[SeriesPoint], started_at: float, ended_at: float, bucket_seconds: float
) -> dict[int, list[SeriesPoint]]:
    if bucket_seconds <= 0:
        return {}
    result: dict[int, list[SeriesPoint]] = {}
    for point in points:
        if started_at <= point.ts <= ended_at:
            index = int((point.ts - started_at) // bucket_seconds)
            result.setdefault(index, []).append(point)
    return result


def _rank(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average = (cursor + end - 1) / 2.0 + 1.0
        for position in range(cursor, end):
            ranks[indexed[position][0]] = average
        cursor = end
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    paired = [(x, y) for x, y in zip(xs, ys, strict=True) if not isnan(x) and not isnan(y)]
    if len(paired) < 2:
        return None
    xr, yr = _rank([pair[0] for pair in paired]), _rank([pair[1] for pair in paired])
    xbar, ybar = sum(xr) / len(xr), sum(yr) / len(yr)
    numerator = sum((x - xbar) * (y - ybar) for x, y in zip(xr, yr, strict=True))
    xnorm = sum((x - xbar) ** 2 for x in xr)
    ynorm = sum((y - ybar) ** 2 for y in yr)
    return numerator / (xnorm * ynorm) ** 0.5 if xnorm and ynorm else 0.0


def _series_value(points: list[SeriesPoint]) -> float | None:
    return _mean([point.value for point in points])


def _window_baseline(points: list[SeriesPoint], started_at: float) -> float | None:
    """Use the first minute only when trailing history did not provide a baseline."""
    return _percentile([p.value for p in points if started_at <= p.ts <= started_at + 60.0], 50)


def analyse(
    inputs: AnalysisInputs, thresholds: Thresholds, investigation_id: int
) -> InvestigationReport:
    duration = max(0.0, inputs.ended_at - inputs.started_at)
    baseline = inputs.baseline_internet_rtt_ms or _window_baseline(
        inputs.internet_rtt, inputs.started_at
    )
    gateway_baseline = inputs.baseline_gateway_rtt_ms or _window_baseline(
        inputs.gateway_rtt, inputs.started_at
    )
    internet = _bucketise(
        inputs.internet_rtt, inputs.started_at, inputs.ended_at, thresholds.bucket_seconds
    )
    losses = _bucketise(
        inputs.internet_loss, inputs.started_at, inputs.ended_at, thresholds.bucket_seconds
    )
    gateway = _bucketise(
        inputs.gateway_rtt, inputs.started_at, inputs.ended_at, thresholds.bucket_seconds
    )
    gateway_loss = _bucketise(
        inputs.gateway_loss, inputs.started_at, inputs.ended_at, thresholds.bucket_seconds
    )
    all_indices = (
        range(max(0, int(duration // thresholds.bucket_seconds) + 1))
        if thresholds.bucket_seconds > 0
        else []
    )
    degraded: list[int] = []
    degraded_rtts: list[float] = []
    for index in all_indices:
        rtt = _percentile([p.value for p in internet.get(index, [])], 95)
        loss = _mean([p.value for p in losses.get(index, [])])
        if (
            rtt is not None
            and baseline is not None
            and rtt >= baseline + thresholds.latency_degraded_ms
        ) or (loss is not None and loss >= thresholds.loss_degraded_fraction):
            degraded.append(index)
            if rtt is not None:
                degraded_rtts.append(rtt)
    gateway_values = [p.value for points in gateway.values() for p in points]
    gateway_p95 = _percentile(gateway_values, 95)
    gateway_loss_value = max(
        (p.value for points in gateway_loss.values() for p in points), default=None
    )
    gateway_bad = (
        gateway_baseline is not None
        and gateway_p95 is not None
        and gateway_p95 >= gateway_baseline + thresholds.gateway_degraded_ms
    )
    gateway_bad = gateway_bad or (
        gateway_loss_value is not None and gateway_loss_value >= thresholds.loss_degraded_fraction
    )
    uptime_values = [p.value for p in sorted(inputs.wan_uptime, key=lambda p: p.ts)]
    wan_dropped = (
        any(next_value < value for value, next_value in pairwise(uptime_values))
        or inputs.wan_reconnect_events > 0
    )
    device_buckets = [
        _bucketise(device.points, inputs.started_at, inputs.ended_at, thresholds.bucket_seconds)
        for device in inputs.device_upload
    ]
    totals = {
        index: sum(_series_value(bucket.get(index, [])) or 0.0 for bucket in device_buckets)
        for index in degraded
    }
    capacity = inputs.uplink_bps
    saturated = bool(
        degraded
        and capacity is not None
        and sum(value >= thresholds.saturation_fraction * capacity for value in totals.values())
        >= (len(degraded) / 2)
    )
    coverage_count = sum(any(bucket.get(index) for bucket in device_buckets) for index in degraded)
    fraction = coverage_count / len(degraded) if degraded else 0.0
    coverage: dict[str, Any] = {
        "eero_available": any(d.source == "eero_cloud" and d.points for d in inputs.device_upload),
        "managed_hosts_available": [
            d.target for d in inputs.device_upload if d.source == "managed_host_ssh" and d.points
        ],
        "degraded_buckets": len(degraded),
        "degraded_buckets_with_device_data": coverage_count,
        "device_data_fraction": fraction,
    }
    caveats: list[str] = []
    if baseline is None:
        caveats.append("no latency baseline")
    if capacity is None:
        caveats.append("uplink capacity unavailable; saturation could not be assessed")
    if not inputs.device_upload:
        caveats.append("no per-device upload data")
    elif fraction < 0.5 and degraded:
        caveats.append("device coverage is sparse; attribution is suggestive")
    if not inputs.wan_uptime:
        caveats.append("WAN uptime data unavailable")
    if wan_dropped:
        verdict, confidence = "wan_upstream", "high"
    elif gateway_bad:
        verdict, confidence = "local_link", "high"
    elif saturated:
        verdict, confidence = "upload_saturation", "low" if fraction < 0.5 else "high"
    elif degraded:
        verdict, confidence = "wan_upstream", "medium"
    elif baseline is not None and inputs.internet_rtt:
        verdict, confidence = "healthy", "high"
    else:
        # "Nothing was degraded" and "nothing was measured" are different answers, and only one of
        # them is reassuring. Without a single latency sample in the window there is no evidence
        # of health -- reporting healthy here would be the tool inventing good news.
        verdict, confidence = "inconclusive", "low"
        if not inputs.internet_rtt:
            caveats.append("no latency samples were collected during the window")
    if degraded and fraction < 0.5 and confidence != "high":
        confidence = "low"
    attributions: list[DeviceAttribution] = []
    if verdict == "upload_saturation":
        degraded_total = sum(totals.values())
        latency_by_bucket = {
            i: _percentile([p.value for p in internet.get(i, [])], 95) for i in degraded
        }
        for device, buckets in zip(inputs.device_upload, device_buckets, strict=True):
            values = [p.value for p in device.points]
            aligned = [
                (_series_value(buckets.get(i, [])), latency_by_bucket[i])
                for i in degraded
                if buckets.get(i) and latency_by_bucket[i] is not None
            ]
            correlation_x: list[float] = []
            correlation_y: list[float] = []
            for x, y in aligned:
                if x is not None and y is not None:
                    correlation_x.append(x)
                    correlation_y.append(y)
            upload_sum = sum(_series_value(buckets.get(i, [])) or 0.0 for i in degraded)
            attributions.append(
                DeviceAttribution(
                    device.target,
                    device.label,
                    device.source,
                    _mean(values) or 0.0,
                    max(values, default=0.0),
                    upload_sum / degraded_total if degraded_total else 0.0,
                    _spearman(correlation_x, correlation_y),
                    sum(bool(buckets.get(i)) for i in range(len(list(all_indices))))
                    * thresholds.bucket_seconds,
                )
            )
        attributions.sort(
            key=lambda item: (item.share_of_upload, item.correlation_with_latency or 0.0),
            reverse=True,
        )
    peak = max(totals.values(), default=None)
    utilisation = peak / capacity if peak is not None and capacity else None
    if verdict == "healthy":
        headline = "Healthy. Nothing was degraded during this investigation window."
    elif verdict == "upload_saturation":
        who = (
            attributions[0].label or attributions[0].target
            if attributions
            else "The measured devices"
        )
        share = (attributions[0].share_of_upload if attributions else 0) * 100
        headline = (
            f"Upload saturation. {who} accounted for {share:.0f}% of estimated upload "
            "during degraded buckets."
        )
    elif verdict == "local_link":
        headline = "Local link problem. Gateway latency or loss was degraded during the window."
    elif verdict == "wan_upstream":
        headline = "Upstream problem. The evidence points beyond the local gateway."
    else:
        headline = "Inconclusive. There was not enough reliable evidence to identify the cause."
    reasons = [f"{len(degraded)} of {len(list(all_indices))} buckets were degraded."]
    if gateway_p95 is not None:
        reasons.append(f"Gateway RTT p95 was {gateway_p95:.1f} ms.")
    if peak is not None:
        reasons.append(f"Estimated peak upload was {peak:.0f} bps.")
    return InvestigationReport(
        investigation_id,
        datetime.fromtimestamp(inputs.started_at, UTC),
        datetime.fromtimestamp(inputs.ended_at, UTC),
        verdict,
        confidence,
        headline,
        reasons,
        caveats,
        baseline,
        _percentile(degraded_rtts, 95),
        gateway_p95,
        len(degraded) * thresholds.bucket_seconds,
        duration,
        peak,
        capacity,
        utilisation,
        attributions,
        coverage,
    )
