from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import ClassVar

import pytest

from netmon import __main__ as cli
from netmon.db import Store
from netmon.probes.eero_cloud import EeroCloudError


def test_gen_token_prints_value_and_storage_instruction(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["netmon", "gen-token"])

    cli.main()

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert len(lines[0]) >= 40
    assert "NETMON_API__TOKEN" in lines[1]


def test_backup_and_restore_cli_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NETMON_CONFIG", raising=False)
    (tmp_path / "netmon.toml").write_text("[database]\npath = 'data/netmon.db'\n", encoding="utf-8")
    Store(tmp_path / "data/netmon.db")
    backup = tmp_path / "backup.db"

    monkeypatch.setattr(sys, "argv", ["netmon", "backup", str(backup)])
    cli.main()
    assert "backup written" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["netmon", "restore", str(backup)])
    cli.main()
    assert "database restored" in capsys.readouterr().out


def test_service_run_returns_child_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "netmon",
            "service-run",
            "--working-dir",
            str(tmp_path),
            "--log-dir",
            str(tmp_path / "logs"),
        ],
    )
    monkeypatch.setattr(cli, "run_service", lambda working, logs: 7)

    with pytest.raises(SystemExit, match="7"):
        cli.main()


@dataclass(frozen=True)
class _Network:
    id: str
    name: str


class _FakeEeroProbe:
    instances: ClassVar[list[_FakeEeroProbe]] = []

    def __init__(self, transport: object) -> None:
        del transport
        self.closed = False
        self.instances.append(self)

    async def login(self, identifier: str) -> str:
        assert identifier == "owner@example.com"
        return "login-token"

    async def verify(self, login_token: str, code: str) -> str:
        assert (login_token, code) == ("login-token", "123456")
        return "long-lived-token"

    async def list_networks(self, token: str) -> list[_Network]:
        assert token == "long-lived-token"
        return [_Network(id="network-1", name="Home"), _Network(id="network-2", name="Cabin")]

    async def aclose(self) -> None:
        self.closed = True


def test_eero_login_saves_token_lists_networks_and_closes(tmp_path, monkeypatch, capsys):
    _FakeEeroProbe.instances.clear()
    answers = iter(["owner@example.com", "123456"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(cli, "EeroCloudProbe", _FakeEeroProbe)
    monkeypatch.setattr(cli, "HttpxEeroTransport", lambda base_url: object())
    token_path = tmp_path / "private" / "eero.token"

    asyncio.run(cli._eero_login("https://example.invalid", token_path))

    assert token_path.read_text(encoding="utf-8") == "long-lived-token\n"
    assert _FakeEeroProbe.instances[0].closed
    output = capsys.readouterr().out
    assert "Home: network-1" in output
    assert "Cabin: network-2" in output
    assert "long-lived-token" not in output


class _FailingEeroProbe(_FakeEeroProbe):
    async def login(self, identifier: str) -> str:
        del identifier
        raise EeroCloudError("eero cloud request unavailable")


def test_eero_login_closes_probe_and_does_not_persist_on_error(tmp_path, monkeypatch):
    _FailingEeroProbe.instances.clear()
    monkeypatch.setattr("builtins.input", lambda prompt: "owner@example.com")
    monkeypatch.setattr(cli, "EeroCloudProbe", _FailingEeroProbe)
    monkeypatch.setattr(cli, "HttpxEeroTransport", lambda base_url: object())
    token_path = tmp_path / "eero.token"

    with pytest.raises(SystemExit, match="eero cloud request unavailable"):
        asyncio.run(cli._eero_login("https://example.invalid", token_path))

    assert _FailingEeroProbe.instances[0].closed
    assert not token_path.exists()
