"""Scheduled alert evaluation over the latest persisted metric samples."""

from __future__ import annotations

from datetime import UTC, datetime

from netmon.alerts.notifiers import NotificationDispatcher
from netmon.alerts.rules import AlertEvaluator
from netmon.collectors.base import Clock, Collector
from netmon.config import AlertsConfig
from netmon.db import Store
from netmon.models import Sample


class AlertCollector(Collector):
    def __init__(
        self,
        store: Store,
        clock: Clock,
        config: AlertsConfig,
        evaluator: AlertEvaluator,
        dispatcher: NotificationDispatcher,
    ) -> None:
        self.store = store
        self.clock = clock
        self.config = config
        self.evaluator = evaluator
        self.dispatcher = dispatcher
        self._last_samples: dict[tuple[str, str, str], float] = {}

    @property
    def name(self) -> str:
        return "alerts"

    @property
    def interval(self) -> float:
        return self.config.interval_seconds

    async def collect(self) -> list[Sample]:
        for rule in self.config.rules:
            latest = self.store.latest_metric(rule.metric_kind, rule.target, rule.source)
            if latest is None:
                continue
            key = (rule.name, str(latest["target"]), str(latest["source"]))
            timestamp = float(latest["ts"])
            if self._last_samples.get(key) == timestamp:
                continue
            self._last_samples[key] = timestamp
            sample = Sample(
                str(latest["kind"]),
                str(latest["target"]),
                str(latest["unit"]),
                datetime.fromtimestamp(timestamp, UTC),
                float(latest["value"]),
                str(latest["source"]),
            )
            for event in self.evaluator.evaluate(sample):
                self.store.record_event(event)
                await self.dispatcher.notify(event)
        return []
