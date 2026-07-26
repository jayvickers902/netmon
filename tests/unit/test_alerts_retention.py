from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from netmon.alerts.notifiers import (
    LogNotifier,
    NotificationDispatcher,
    WebhookNotifier,
    WindowsToastNotifier,
)
from netmon.alerts.rules import AlertEvaluator
from netmon.collectors.alerts import AlertCollector
from netmon.config import AlertRuleConfig, AlertsConfig, RetentionConfig
from netmon.db import Store
from netmon.models import Event, Sample


def _rule(**kwargs) -> AlertRuleConfig:
    values = {
        "name": "latency",
        "metric_kind": "latency_rtt_ms",
        "threshold": 100.0,
        "failures_to_open": 2,
        "successes_to_close": 2,
    }
    values.update(kwargs)
    return AlertRuleConfig(**values)


def test_alert_rule_is_sustained_and_deduplicated_on_flap():
    evaluator = AlertEvaluator([_rule()], cooldown_seconds=60)
    base = datetime(2026, 1, 1, tzinfo=UTC)

    def sample(value: float, second: int) -> Sample:
        return Sample("latency_rtt_ms", "gateway", "ms", base + timedelta(seconds=second), value)

    assert evaluator.evaluate(sample(200, 0)) == []
    assert len(evaluator.evaluate(sample(200, 1))) == 1
    assert evaluator.evaluate(sample(50, 2)) == []
    assert len(evaluator.evaluate(sample(50, 3))) == 1
    assert evaluator.evaluate(sample(200, 4)) == []
    assert evaluator.evaluate(sample(50, 5)) == []


class FailingNotifier:
    async def notify(self, event: Event) -> None:
        del event
        raise RuntimeError("dead")


class RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def notify(self, event: Event) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_notifiers_fail_independently():
    recording = RecordingNotifier()
    event = Event(datetime.now(UTC), "alert_triggered", "warning", "test", {})
    await NotificationDispatcher([FailingNotifier(), recording]).notify(event)
    assert recording.events == [event]


@pytest.mark.asyncio
async def test_log_webhook_and_toast_notifiers_are_usable(caplog):
    class Webhook:
        def __init__(self):
            self.calls = []

        async def post(self, url, payload):
            self.calls.append((url, payload))

    webhook = Webhook()
    event = Event(datetime(2026, 1, 1, tzinfo=UTC), "alert_triggered", "warning", "test", {})
    await LogNotifier().notify(event)
    await WebhookNotifier(webhook, "https://example.test/hook").notify(event)
    toast = WindowsToastNotifier(recording := RecordingNotifier())
    await toast.notify(event)
    await toast.notify(event)
    assert webhook.calls[0][0].startswith("https://")
    assert recording.events == [event, event]
    assert caplog.text.count("Windows toast unavailable") <= 1


@pytest.mark.asyncio
async def test_alert_collector_persists_and_notifies(tmp_path, frozen_clock):
    store = Store(tmp_path / "alerts.db")
    now = frozen_clock.now()
    store.persist_samples([Sample("x", "target", "count", now, 10.0)])
    config = AlertsConfig(
        rules=[_rule(name="x-rule", metric_kind="x", threshold=5, failures_to_open=1)]
    )
    recording = RecordingNotifier()
    collector = AlertCollector(
        store,
        frozen_clock,
        config,
        AlertEvaluator(config.rules, 0),
        NotificationDispatcher([recording]),
    )
    await collector.collect()
    await collector.collect()
    assert store.list_alerts()[0]["kind"] == "alert_triggered"
    assert len(recording.events) == 1


def test_retention_verifies_before_deleting_raw_rows(tmp_path):
    store = Store(tmp_path / "retention.db")
    now = datetime(2026, 1, 10, tzinfo=UTC)
    old = now - timedelta(days=2)
    store.persist_samples([Sample("x", "a", "count", old, 4.0)])
    store.run_retention(now, RetentionConfig(raw_days=1, rollup_1m_days=2, rollup_5m_days=3))
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT sample_count FROM sample_rollup WHERE bucket_seconds=60"
            ).fetchone()[0]
            == 1
        )


def test_retention_failure_rolls_back_without_data_loss(monkeypatch, tmp_path):
    store = Store(tmp_path / "retention-failure.db")
    now = datetime(2026, 1, 10, tzinfo=UTC)
    store.persist_samples([Sample("x", "a", "count", now - timedelta(days=2), 4.0)])
    monkeypatch.setattr(Store, "_rollup_raw", staticmethod(lambda connection, cutoff: 1 / 0))
    with pytest.raises(ZeroDivisionError):
        store.run_retention(now, RetentionConfig(raw_days=1, rollup_1m_days=2, rollup_5m_days=3))
    assert store.list_metrics(kind="x")
