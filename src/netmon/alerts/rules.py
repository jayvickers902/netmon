"""Sustained threshold rules with de-duplication and cool-down."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from netmon.config import AlertRuleConfig
from netmon.models import Event, Sample
from netmon.outage import advance_hysteresis


@dataclass(slots=True)
class _RuleState:
    failures: int = 0
    successes: int = 0
    failure_started_at: datetime | None = None
    opened_at: datetime | None = None
    last_alert_at: datetime | None = None
    notification_active: bool = False


class AlertEvaluator:
    def __init__(self, rules: list[AlertRuleConfig], cooldown_seconds: float) -> None:
        self.rules = rules
        self.cooldown_seconds = cooldown_seconds
        self._states: dict[str, _RuleState] = {rule.name: _RuleState() for rule in rules}

    def evaluate(self, sample: Sample) -> list[Event]:
        events: list[Event] = []
        for rule in self.rules:
            if not self._matches(rule, sample):
                continue
            state = self._states[rule.name]
            condition = _compare(sample.value, rule.operator, rule.threshold)
            opened, closed = advance_hysteresis(
                state,
                condition,
                sample.ts,
                rule.failures_to_open,
                rule.successes_to_close,
            )
            if opened and _cooldown_elapsed(state.last_alert_at, sample.ts, self.cooldown_seconds):
                state.last_alert_at = sample.ts
                state.notification_active = True
                events.append(
                    Event(
                        ts=state.failure_started_at or sample.ts,
                        kind="alert_triggered",
                        severity=rule.severity,
                        message=f"Alert triggered: {rule.name}",
                        details={
                            "rule": rule.name,
                            "metric_kind": sample.metric_kind,
                            "target": sample.target,
                            "source": sample.source,
                            "unit": sample.unit,
                            "value": sample.value,
                            "threshold": rule.threshold,
                            "operator": rule.operator,
                        },
                    )
                )
            if closed and state.notification_active:
                state.notification_active = False
                events.append(
                    Event(
                        ts=sample.ts,
                        kind="alert_resolved",
                        severity="info",
                        message=f"Alert resolved: {rule.name}",
                        details={"rule": rule.name, "metric_kind": sample.metric_kind},
                    )
                )
        return events

    @staticmethod
    def _matches(rule: AlertRuleConfig, sample: Sample) -> bool:
        return (
            rule.metric_kind == sample.metric_kind
            and (rule.target is None or rule.target == sample.target)
            and (rule.source is None or rule.source == sample.source)
        )


def _compare(value: float, operator: str, threshold: float) -> bool:
    return {
        ">": value > threshold,
        ">=": value >= threshold,
        "<": value < threshold,
        "<=": value <= threshold,
        "==": value == threshold,
        "!=": value != threshold,
    }[operator]


def _cooldown_elapsed(
    last_alert_at: datetime | None, now: datetime, cooldown_seconds: float
) -> bool:
    return last_alert_at is None or (now - last_alert_at).total_seconds() >= cooldown_seconds
