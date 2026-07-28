"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import secrets
from pathlib import Path

import uvicorn

from netmon.api.app import create_app
from netmon.config import NetmonSettings
from netmon.db import DatabaseBackupError, backup_database, restore_database
from netmon.probes.eero_cloud import EeroCloudError, EeroCloudProbe, HttpxEeroTransport
from netmon.probes.eero_token import FileEeroTokenStore
from netmon.service import run_service


def main() -> None:
    parser = argparse.ArgumentParser(prog="netmon")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["eero-login", "gen-token", "backup", "restore", "service-run"],
    )
    parser.add_argument("path", nargs="?")
    parser.add_argument("--working-dir", type=Path)
    parser.add_argument("--log-dir", type=Path)
    args = parser.parse_args()
    if args.command == "gen-token":
        print(secrets.token_urlsafe(32))
        print(
            "Put this value in [api] token or NETMON_API__TOKEN; it is not written automatically."
        )
        return
    if args.command == "service-run":
        if args.working_dir is None or args.log_dir is None:
            parser.error("service-run requires --working-dir and --log-dir")
        raise SystemExit(run_service(args.working_dir, args.log_dir))
    settings = NetmonSettings()
    if args.command in {"backup", "restore"}:
        if args.path is None:
            parser.error(f"{args.command} requires a path")
        try:
            if args.command == "backup":
                backup_database(settings.database.path, Path(args.path))
                print(f"backup written to {args.path}")
            else:
                displaced = restore_database(settings.database.path, Path(args.path))
                if displaced is None:
                    print(f"database restored from {args.path}")
                else:
                    print(
                        f"database restored from {args.path}; old database retained at {displaced}"
                    )
        except DatabaseBackupError as error:
            raise SystemExit(str(error)) from None
        return
    if args.command == "eero-login":
        asyncio.run(_eero_login(settings.eero.base_url, settings.eero.token_path))
        return
    uvicorn.run(create_app(settings), host=settings.server.host, port=settings.server.port)


async def _eero_login(base_url: object, token_path: Path) -> None:
    identifier = await asyncio.to_thread(input, "eero email or phone (not stored): ")
    code = ""
    probe = EeroCloudProbe(HttpxEeroTransport(str(base_url)))
    try:
        login_token = await probe.login(identifier)
        code = await asyncio.to_thread(input, "verification code: ")
        token = await probe.verify(login_token, code)
        networks = await probe.list_networks(token)
    except EeroCloudError as error:
        raise SystemExit(str(error)) from None
    finally:
        await probe.aclose()
    await FileEeroTokenStore(token_path).save(token)
    del identifier, code
    print(f"eero token saved to {token_path}")
    if networks:
        print("available eero networks (set eero.network_id in netmon.toml):")
        for network in networks:
            print(f"  {network.name}: {network.id}")
    else:
        print("no eero networks were returned for this account")


if __name__ == "__main__":
    main()
