from __future__ import annotations

from netmon.service import build_service_command, rotate_log


def test_service_command_has_explicit_paths_and_no_shell_redirection(tmp_path):
    command = build_service_command("python.exe", tmp_path, tmp_path / "logs")
    assert command == [
        "python.exe",
        "-m",
        "netmon",
        "service-run",
        "--working-dir",
        str(tmp_path),
        "--log-dir",
        str(tmp_path / "logs"),
    ]
    assert all("1>>" not in argument and "2>>" not in argument for argument in command)


def test_full_log_is_rotated_with_bounded_history(tmp_path):
    log = tmp_path / "netmon.out.log"
    log.write_text("full", encoding="utf-8")
    (tmp_path / "netmon.out.log.1").write_text("previous", encoding="utf-8")

    rotate_log(log, max_bytes=1, backups=2)

    assert not log.exists()
    assert (tmp_path / "netmon.out.log.1").read_text(encoding="utf-8") == "full"
    assert (tmp_path / "netmon.out.log.2").read_text(encoding="utf-8") == "previous"
