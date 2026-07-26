"""Retention job facade used by the scheduler and callers outside the store."""

from __future__ import annotations

from datetime import datetime

from netmon.config import RetentionConfig

from .store import Store


class RetentionJob:
    def __init__(self, store: Store, config: RetentionConfig) -> None:
        self.store = store
        self.config = config

    def run(self, now: datetime) -> None:
        self.store.run_retention(now, self.config)
