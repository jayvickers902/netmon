"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import secrets
from pathlib import Path

import uvicorn

from netmon.api.app import create_app
from netmon.config import NetmonSettings
from netmon.probes.eero_cloud import EeroCloudError, EeroCloudProbe, HttpxEeroTransport


def main() -> None:
    parser = argparse.ArgumentParser(prog="netmon")
    parser.add_argument("command", nargs="?", choices=["eero-login", "gen-token"])
    args = parser.parse_args()
    if args.command == "gen-token":
        print(secrets.token_urlsafe(32))
        print(
            "Put this value in [api] token or NETMON_API__TOKEN; it is not written automatically."
        )
        return
    settings = NetmonSettings()
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
    except EeroCloudError as error:
        raise SystemExit(str(error)) from None
    await asyncio.to_thread(token_path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(token_path.write_text, token + "\n", encoding="utf-8")
    del identifier, code
    print(f"eero token saved to {token_path}")


if __name__ == "__main__":
    main()
