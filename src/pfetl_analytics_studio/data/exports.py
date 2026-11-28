"""Studio-owned temporary export directory and streaming CSV helpers.

Owns
----
- Resolving and cleaning the Studio-owned temporary export directory.
- Safe filename sanitization and unique temporary path allocation.
- Chunked UTF-8 CSV streaming to disk without full-dataset memory buffers.

Does not own
------------
- Writing into PFETL DuckDB/Control Plane stores or NiceGUI download triggers.
"""

from __future__ import annotations

import csv
import re
import tempfile
import time
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def studio_export_dir() -> Path:
    """Return (and create if needed) the Studio-owned temporary export directory."""
    export_dir = Path(tempfile.gettempdir()) / "pfetl_studio_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def cleanup_old_exports(max_age_seconds: float = 3600.0) -> int:
    """Best-effort removal of stale temporary export files older than `max_age_seconds`."""
    export_dir = studio_export_dir()
    cutoff = time.time() - max_age_seconds
    removed = 0
    for entry in export_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


def sanitize_export_stem(name: str, *, default: str = "export") -> str:
    """Sanitize a filename stem for safe cross-platform filesystem export."""
    cleaned = _SAFE_FILENAME_RE.sub("_", name.strip()).strip("_")
    return cleaned[:80] if cleaned else default


def create_temp_export_path(filename: str, *, default_stem: str = "export") -> Path:
    """Allocate a unique Studio-owned temporary export path and clean stale exports."""
    cleanup_old_exports()
    raw_path = Path(filename.strip() or default_stem)
    stem = sanitize_export_stem(raw_path.stem or raw_path.name, default=default_stem)
    suffix = raw_path.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", raw_path.suffix) else ""
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    token = uuid.uuid4().hex[:8]
    return studio_export_dir() / f"{stem}_{stamp}_{token}{suffix}"


def write_rows_csv_file(
    columns: Sequence[str],
    row_chunks: Iterable[Sequence[Sequence[Any]]],
    target_path: Path,
) -> int:
    """Stream column headers and row chunks to `target_path` as UTF-8 CSV."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    with target_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if columns:
            writer.writerow(columns)
        for chunk in row_chunks:
            if not chunk:
                continue
            writer.writerows(chunk)
            total_rows += len(chunk)
    return total_rows
