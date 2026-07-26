from __future__ import annotations

import sys

import pytest

from netmon import __main__ as cli
from netmon.db import Store


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
