"""Command-line entrypoint."""

from __future__ import annotations

import uvicorn

from netmon.api.app import create_app
from netmon.config import NetmonSettings


def main() -> None:
    settings = NetmonSettings()
    uvicorn.run(create_app(settings), host=settings.server.host, port=settings.server.port)


if __name__ == "__main__":
    main()
