"""Data package for PFETL Analytics Studio.

Ownership: All external I/O (DuckDB, SQLite Control Plane, archived logs).
Only this package opens DuckDB, Control Plane SQLite or archived/filesystem logs.
Forbidden: Does not import UI/NiceGUI or execute financial methodologies.
"""

from __future__ import annotations

from pfetl_analytics_studio.data.control_plane_reader import ControlPlaneReader
from pfetl_analytics_studio.data.duckdb_reader import DuckDBReader, validate_identifier
from pfetl_analytics_studio.data.errors import (
    ControlPlaneState,
    DatabaseCorruptedError,
    DatabaseNotFoundError,
    PFETLDataAccessError,
    PFETLLockedError,
    ReadOnlyViolationError,
    WarehouseState,
)

__all__ = [
    "ControlPlaneReader",
    "ControlPlaneState",
    "DatabaseCorruptedError",
    "DatabaseNotFoundError",
    "DuckDBReader",
    "PFETLDataAccessError",
    "PFETLLockedError",
    "ReadOnlyViolationError",
    "WarehouseState",
    "validate_identifier",
]
