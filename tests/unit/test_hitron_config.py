from __future__ import annotations

import pytest
from pydantic import ValidationError

from netmon.config import HitronConfig, NetmonSettings


def test_hitron_defaults_are_safe(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    config = HitronConfig()
    assert config.enabled is True
    assert config.interval_seconds == 30.0
    assert str(config.base_url) == "http://192.168.100.1/"
    assert config.timeout_seconds == 5.0
    assert NetmonSettings().investigation.burst_intervals.hitron == 30.0


def test_hitron_interval_floor_is_rejected():
    with pytest.raises(ValidationError, match="30"):
        HitronConfig(interval_seconds=5)


def test_hitron_toml_section_loads(monkeypatch, tmp_path):
    (tmp_path / "netmon.toml").write_text(
        "[hitron]\n"
        "enabled = false\n"
        "interval_seconds = 60\n"
        'base_url = "http://192.168.100.2"\n'
        "timeout_seconds = 8\n"
    )
    monkeypatch.chdir(tmp_path)

    settings = NetmonSettings()

    assert settings.hitron.enabled is False
    assert settings.hitron.interval_seconds == 60.0
    assert str(settings.hitron.base_url) == "http://192.168.100.2/"
    assert settings.hitron.timeout_seconds == 8.0
