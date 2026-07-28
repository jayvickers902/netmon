"""Token persistence isolated behind an injectable store."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol


class EeroTokenStore(Protocol):
    def read(self) -> str | None: ...

    async def save(self, token: str) -> None: ...


class FileEeroTokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> str | None:
        try:
            token = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return token or None

    async def save(self, token: str) -> None:
        await asyncio.to_thread(self._save, token)

    def _save(self, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(token + "\n", encoding="utf-8")
