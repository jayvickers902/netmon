from __future__ import annotations

import pytest
from pydantic import ValidationError

from netmon.config import NetmonSettings


def test_defaults_are_safe_without_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = NetmonSettings()
    assert settings.server.port == 8787
    assert settings.discovery.subnets == []
    assert str(settings.database.path) == "data\\netmon.db"


def test_toml_is_loaded_and_validated(monkeypatch, tmp_path):
    config = tmp_path / "netmon.toml"
    config.write_text("[server]\nport = 9000\n[discovery]\noffline_after_misses = 4\n")
    monkeypatch.chdir(tmp_path)
    settings = NetmonSettings()
    assert settings.server.port == 9000
    assert settings.discovery.offline_after_misses == 4


def test_invalid_port_is_rejected(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "netmon.toml").write_text("[server]\nport = 0\n")
    with pytest.raises(ValidationError):
        NetmonSettings()
