"""Command-line interface for PFETL Analytics Studio."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys

from nicegui import ui

from pfetl_analytics_studio import __version__
from pfetl_analytics_studio.app import create_app

ALLOWED_LOOPBACKS = {"127.0.0.1", "localhost", "::1"}


def validate_loopback_host(host: str) -> None:
    """Validate that host is bound to local loopback interface.

    Parameters
    ----------
    host : str
        Host IP address or hostname.

    Raises
    ------
    ValueError
        If host is not a loopback address and remote binding is not explicitly allowed.
    """
    if os.environ.get("PFETL_STUDIO_DEV_ALLOW_REMOTE", "").lower() in ("1", "true", "yes"):
        return

    clean_host = host.strip().lower()
    if clean_host in ALLOWED_LOOPBACKS:
        return

    try:
        ip = ipaddress.ip_address(clean_host)
        if ip.is_loopback:
            return
    except ValueError:
        pass

    raise ValueError(
        f"Security violation: PFETL Analytics Studio must bind to a loopback address "
        f"(got '{host}'). Set PFETL_STUDIO_DEV_ALLOW_REMOTE=1 only for explicit dev testing."
    )


def main(args: list[str] | None = None) -> int:
    """Launch PFETL Analytics Studio NiceGUI server.

    Parameters
    ----------
    args : list[str] | None, optional
        Command line arguments, by default None (sys.argv[1:]).

    Returns
    -------
    int
        Exit return code.
    """
    parser = argparse.ArgumentParser(
        prog="pfetl-studio",
        description="PFETL Analytics Studio — Local-First Analytical Console",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind (must be loopback, default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on (default: 8080)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open browser on startup",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on code change (development only)",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"pfetl-analytics-studio {__version__}",
    )

    parsed = parser.parse_args(args)

    try:
        validate_loopback_host(parsed.host)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    create_app()

    ui.run(  # pyright: ignore[reportUnknownMemberType]
        host=parsed.host,
        port=parsed.port,
        title="PFETL Analytics Studio",
        show=not parsed.no_browser,
        reload=parsed.reload,
    )
    return 0
