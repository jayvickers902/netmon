"""Offline MAC OUI vendor lookup."""

from __future__ import annotations

import re
from importlib.resources import files
from typing import Protocol


class OuiLookup(Protocol):
    def vendor_for(self, mac: str) -> str | None: ...


def oui_prefix(mac: str) -> str:
    return re.sub(r"[^0-9a-fA-F]", "", mac).upper()[:6]


class BundledOuiLookup:
    def __init__(self) -> None:
        self._vendors: dict[str, str] = {}
        data = files("netmon.data").joinpath("oui.csv").read_text(encoding="utf-8")
        for line in data.splitlines():
            if line and not line.startswith("#"):
                prefix, vendor = line.split(",", 1)
                self._vendors[prefix.strip().upper()] = vendor.strip()

    def vendor_for(self, mac: str) -> str | None:
        return self._vendors.get(oui_prefix(mac))
