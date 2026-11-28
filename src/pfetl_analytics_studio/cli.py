"""Command-line interface for PFETL Analytics Studio."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pfetl_analytics_studio import __version__
from pfetl_analytics_studio.server import (
    ALLOWED_LOOPBACKS,
    StudioServerConfig,
    run_server,
    validate_loopback_host,
)

__all__ = [
    "ALLOWED_LOOPBACKS",
    "main",
    "validate_loopback_host",
]


def main(args: list[str] | None = None) -> int:
    """Parse CLI arguments and delegate launch to `run_server()`."""
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
        help="Port to listen on (default: 8080; pass 0 with --auto-port for automatic)",
    )
    parser.add_argument(
        "--auto-port",
        action="store_true",
        help="Prefer 8080 and automatically fall back to a free loopback port if occupied",
    )
    parser.add_argument(
        "--settings",
        type=str,
        default=None,
        help="Path to PFETL Settings TOML configuration file",
    )
    parser.add_argument(
        "--rules",
        type=str,
        default=None,
        help="Path to PFETL FinancialRules TOML configuration file",
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
        "--runtime-file",
        type=str,
        default=None,
        help="Optional atomic JSON runtime metadata output path for process orchestration",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"pfetl-analytics-studio {__version__}",
    )

    parsed = parser.parse_args(args)
    req_port: int | None = None if (parsed.auto_port or parsed.port == 0) else int(parsed.port)
    config = StudioServerConfig(
        host=str(parsed.host),
        port=req_port,
        settings_path=Path(parsed.settings) if parsed.settings else None,
        rules_path=Path(parsed.rules) if parsed.rules else None,
        open_browser=not parsed.no_browser,
        reload=bool(parsed.reload),
        runtime_file=Path(parsed.runtime_file) if parsed.runtime_file else None,
    )
    return run_server(config)


if __name__ in {"__main__", "__mp_main__"}:
    sys.exit(main())
