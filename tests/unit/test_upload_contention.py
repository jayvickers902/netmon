from __future__ import annotations

from netmon.analysis.upload_contention import (
    AnalysisInputs,
    DeviceSeries,
    SeriesPoint,
    Thresholds,
    _bucketise,
    _percentile,
    _spearman,
    analyse,
)

T = Thresholds(50.0, 30.0, 0.05, 0.8, 10.0)


def points(values: list[float], step: float = 10.0) -> list[SeriesPoint]:
    return [SeriesPoint(index * step, value) for index, value in enumerate(values)]


def case(
    *,
    internet: list[float],
    gateway: list[float] | None = None,
    devices: list[DeviceSeries] | None = None,
    uptime: list[float] | None = None,
    capacity: float | None = 10_000.0,
    baseline: float | None = 10.0,
    gateway_baseline: float | None = 10.0,
    reconnects: int = 0,
) -> AnalysisInputs:
    return AnalysisInputs(
        0.0,
        float(len(internet) * 10),
        points(internet),
        [],
        points(gateway if gateway is not None else [10.0] * len(internet)),
        [],
        points(uptime if uptime is not None else [100.0] * len(internet)),
        devices or [],
        baseline,
        gateway_baseline,
        capacity,
        reconnects,
    )


def test_percentile_and_bucketise_edges() -> None:
    assert _percentile([], 95) is None
    assert _percentile([7.0], 50) == 7.0
    assert _percentile([0.0, 10.0], 50) == 5.0
    assert list(_bucketise(points([1.0, 2.0]), 0.0, 10.0, 10.0)) == [0, 1]
    assert _bucketise([SeriesPoint(10.0, 2.0)], 0.0, 10.0, 10.0)[1][0].value == 2.0


def test_spearman_captures_monotonic_nonlinear_relationship() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 4.0, 9.0, 16.0, 25.0]
    assert _spearman(xs, ys) == 1.0


def test_each_verdict_branch_and_precedence() -> None:
    assert analyse(case(internet=[10, 100]), T, 1).verdict == "wan_upstream"
    assert analyse(case(internet=[10, 100], gateway=[10, 100]), T, 2).verdict == "local_link"
    device = DeviceSeries("pc", "PC", "managed_host_ssh", points([9_000, 9_000]))
    assert analyse(case(internet=[10, 100], devices=[device]), T, 3).verdict == "upload_saturation"
    assert analyse(case(internet=[10, 100]), T, 4).verdict == "wan_upstream"
    assert analyse(case(internet=[10, 10]), T, 5).verdict == "healthy"
    assert (
        analyse(case(internet=[], baseline=None, gateway_baseline=None), T, 6).verdict
        == "inconclusive"
    )
    assert (
        analyse(case(internet=[10, 100], gateway=[10, 100], devices=[device]), T, 7).verdict
        == "local_link"
    )
    assert (
        analyse(case(internet=[10, 100], devices=[device], uptime=[100, 90]), T, 8).verdict
        == "wan_upstream"
    )


def test_non_overlapping_upload_burst_is_never_upload_saturation() -> None:
    device = DeviceSeries("pc", "PC", "eero_cloud", points([0, 9_000, 0]))
    report = analyse(case(internet=[100, 10, 100], devices=[device]), T, 9)
    assert report.verdict != "upload_saturation"


def test_attribution_order_share_and_low_coverage() -> None:
    first = DeviceSeries("heavy", None, "eero_cloud", points([8_000, 8_000]))
    second = DeviceSeries("light", "Light", "local", points([2_000, 2_000]))
    report = analyse(case(internet=[100, 100], devices=[first, second]), T, 10)
    assert report.attributions[0].target == "heavy"
    assert sum(item.share_of_upload for item in report.attributions) == 1.0
    sparse = DeviceSeries("sparse", None, "eero_cloud", [SeriesPoint(0.0, 9_000)])
    sparse_report = analyse(case(internet=[100, 100, 100], devices=[sparse]), T, 11)
    assert sparse_report.confidence == "low"


def test_missing_inputs_report_caveats_without_crashing() -> None:
    report = analyse(case(internet=[], capacity=None, baseline=None, gateway_baseline=None), T, 12)
    assert report.verdict == "inconclusive"
    assert "no latency baseline" in report.caveats
    assert any("uplink capacity" in caveat for caveat in report.caveats)
    assert report.total_seconds == 0.0


def test_empty_window_is_inconclusive_not_healthy() -> None:
    """A window with no latency samples must not be reported as healthy.

    "Nothing was degraded" and "nothing was measured" are different answers. Returning healthy
    for an empty window would tell the operator their link is fine on the strength of no evidence.
    """

    report = analyse(
        AnalysisInputs(
            started_at=0.0,
            ended_at=60.0,
            internet_rtt=[],
            internet_loss=[],
            gateway_rtt=[],
            gateway_loss=[],
            wan_uptime=[],
            device_upload=[],
            baseline_internet_rtt_ms=11.4,
            baseline_gateway_rtt_ms=2.0,
            uplink_bps=10_000_000.0,
            wan_reconnect_events=0,
        ),
        Thresholds(
            latency_degraded_ms=50.0,
            gateway_degraded_ms=30.0,
            loss_degraded_fraction=0.05,
            saturation_fraction=0.8,
            bucket_seconds=10.0,
        ),
        investigation_id=1,
    )
    assert report.verdict == "inconclusive"
    assert report.confidence == "low"
    assert any("no latency samples" in caveat for caveat in report.caveats)
