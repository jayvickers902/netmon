from __future__ import annotations

import pytest
from pydantic import ValidationError

from netmon.config import EeroConfig, NetmonSettings, RetentionConfig, SpeedtestConfig


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


def test_dangerous_phase_intervals_and_tiers_are_rejected():
    with pytest.raises(ValidationError, match="30"):
        EeroConfig(interval_seconds=29)
    with pytest.raises(ValidationError, match="900"):
        SpeedtestConfig(interval_seconds=899)
    with pytest.raises(ValidationError, match="ordered"):
        RetentionConfig(raw_days=30, rollup_1m_days=7, rollup_5m_days=365)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(None, None), ("", None), ("   ", None), ("  network-42  ", "network-42")],
)
def test_eero_network_id_is_normalized(configured, expected):
    assert EeroConfig(network_id=configured).network_id == expected
