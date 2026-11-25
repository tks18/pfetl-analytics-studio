"""Data package for PFETL Analytics Studio.

Ownership: All external I/O (DuckDB, SQLite Control Plane, archived logs).
Only this package opens DuckDB, Control Plane SQLite or archived/filesystem logs.
Forbidden: Does not import UI/NiceGUI or execute financial methodologies.
"""

from __future__ import annotations

from pfetl_analytics_studio.data.cache import CacheKey, CacheStats, QueryCache, build_cache_key
from pfetl_analytics_studio.data.catalog import (
    SURFACE_ATLAS,
    CatalogEntry,
    CatalogSnapshot,
    ColumnInfo,
    CompatibilityReport,
    CompatibilityStatus,
    DriftCheckResult,
    PFETLCatalog,
    RunProvenance,
    SurfaceLayer,
    SurfaceSpec,
    SurfaceUsageCategory,
    evaluate_profile_drift,
)
from pfetl_analytics_studio.data.control_plane_reader import ControlPlaneReader
from pfetl_analytics_studio.data.duckdb_reader import (
    DuckDBQueryPort,
    DuckDBReader,
    DuckDBSession,
    validate_identifier,
)
from pfetl_analytics_studio.data.errors import (
    ControlPlaneState,
    DatabaseCorruptedError,
    DatabaseNotFoundError,
    PFETLDataAccessError,
    PFETLLockedError,
    ReadOnlyViolationError,
    WarehouseState,
)
from pfetl_analytics_studio.data.log_reader import ArchivedLogReader, LogReadResult, LogSource

__all__ = [
    "SURFACE_ATLAS",
    "ArchivedLogReader",
    "CacheKey",
    "CacheStats",
    "CatalogEntry",
    "CatalogSnapshot",
    "ColumnInfo",
    "CompatibilityReport",
    "CompatibilityStatus",
    "ControlPlaneReader",
    "ControlPlaneState",
    "DatabaseCorruptedError",
    "DatabaseNotFoundError",
    "DriftCheckResult",
    "DuckDBQueryPort",
    "DuckDBReader",
    "DuckDBSession",
    "LogReadResult",
    "LogSource",
    "PFETLCatalog",
    "PFETLDataAccessError",
    "PFETLLockedError",
    "QueryCache",
    "ReadOnlyViolationError",
    "RunProvenance",
    "SurfaceLayer",
    "SurfaceSpec",
    "SurfaceUsageCategory",
    "WarehouseState",
    "build_cache_key",
    "evaluate_profile_drift",
    "validate_identifier",
]
