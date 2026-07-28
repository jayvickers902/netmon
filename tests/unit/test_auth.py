from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from netmon.api.app import create_app
from netmon.config import ApiConfig, NetmonSettings, ServerConfig
from netmon.db import Store
from netmon.scheduler import Scheduler


def _client(tmp_path, settings: NetmonSettings, host: str = "127.0.0.1") -> TestClient:
    return TestClient(
        create_app(settings=settings, store=Store(tmp_path / "auth.db"), scheduler=Scheduler([])),
        client=(host, 50000),
    )


def test_loopback_is_exempt_by_default_and_health_is_minimal(tmp_path):
    with _client(tmp_path, NetmonSettings()) as client:
        assert client.get("/status").status_code == 200
        assert client.get("/health").json() == {"status": "ok"}


def test_loopback_can_be_enforced(tmp_path):
    settings = NetmonSettings(api=ApiConfig(token="secret", allow_loopback_unauthenticated=False))
    with _client(tmp_path, settings) as client:
        response = client.get("/status")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert client.get("/status", headers={"Authorization": "Bearer secret"}).status_code == 200
        assert client.patch("/devices/999", json={"label": "x"}).status_code == 401
        authenticated = client.patch(
            "/devices/999",
            json={"label": "x"},
            headers={"Authorization": "Bearer secret"},
        )
        assert authenticated.status_code == 404


def test_bad_and_missing_credentials_do_not_leak_token(tmp_path, caplog):
    settings = NetmonSettings(
        api=ApiConfig(token="super-secret", allow_loopback_unauthenticated=False)
    )
    with _client(tmp_path, settings) as client, caplog.at_level(logging.INFO):
        for headers in ({}, {"Authorization": "Bearer wrong-token"}):
            response = client.get("/devices", headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
            assert "super-secret" not in response.text
            assert "wrong-token" not in response.text
        assert "super-secret" not in caplog.text
        assert "wrong-token" not in caplog.text


def test_loopback_exemption_does_not_cover_remote_clients(tmp_path):
    settings = NetmonSettings(api=ApiConfig(token="secret", allow_loopback_unauthenticated=True))
    with _client(tmp_path, settings, host="192.168.1.50") as client:
        assert client.get("/status").status_code == 401
        assert client.get("/status", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_non_ascii_bearer_value_is_rejected_not_a_server_error(tmp_path):
    settings = NetmonSettings(api=ApiConfig(token="secret", allow_loopback_unauthenticated=False))
    with _client(tmp_path, settings) as client:
        # A real client can put non-ASCII bytes on the wire; str comparison would raise TypeError.
        response = client.get("/status", headers={"Authorization": "Bearer über-wrong".encode()})
        assert response.status_code == 401


def test_token_is_not_accepted_as_a_query_parameter(tmp_path):
    settings = NetmonSettings(api=ApiConfig(token="secret", allow_loopback_unauthenticated=False))
    with _client(tmp_path, settings) as client:
        assert client.get("/status?token=secret").status_code == 401
        assert client.get("/status?access_token=secret").status_code == 401


def test_non_loopback_without_token_is_rejected_at_config_load():
    with pytest.raises(ValidationError, match="NETMON_API__TOKEN"):
        NetmonSettings(server=ServerConfig(host="192.168.1.20"))


def test_environment_token_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NETMON_SERVER__HOST", "0.0.0.0")
    monkeypatch.setenv("NETMON_API__TOKEN", "from-env")
    settings = NetmonSettings()
    assert settings.api.token == "from-env"
