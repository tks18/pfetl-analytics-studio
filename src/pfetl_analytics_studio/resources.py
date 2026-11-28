"""Bundled static resource resolution for source, wheel, and PyInstaller builds."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_resource_path(filename: str) -> Path | None:
    """Resolve a bundled static asset (`logo.ico`, `logo.png`, etc.) if present.

    Search order:
    1. PyInstaller frozen bundle root (`sys._MEIPASS` / `sys.executable.parent`)
    2. Installed package directory (`pfetl_analytics_studio/`)
    3. Repository root (`<repo>/`)
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(str(meipass))
        candidates.append(base / filename)
        candidates.append(base / "pfetl_analytics_studio" / filename)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / filename)
        candidates.append(exe_dir / "_internal" / filename)

    pkg_dir = Path(__file__).resolve().parent
    candidates.append(pkg_dir / filename)
    candidates.append(pkg_dir / "assets" / filename)
    candidates.append(pkg_dir / "ui" / "assets" / filename)

    repo_root = pkg_dir.parent.parent
    candidates.append(repo_root / filename)

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None
