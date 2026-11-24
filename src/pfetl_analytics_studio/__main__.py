"""Executable module entrypoint for PFETL Analytics Studio."""

from __future__ import annotations

import sys

from pfetl_analytics_studio.cli import main

if __name__ == "__main__":
    sys.exit(main())
