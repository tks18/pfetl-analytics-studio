"""Studio-owned SQLite persistence manager.

Persists saved views, report definitions, recent profiles, and export history.
Never writes to PFETL DuckDB or Control Plane databases.
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir


def get_studio_state_path() -> Path:
    """Resolve destination path for Studio state database.

    Returns
    -------
    Path
        Path to studio.sqlite in user data directory.
    """
    data_dir = Path(user_data_dir("PFETL Analytics Studio"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "studio.sqlite"
