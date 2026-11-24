"""Typed error and state hierarchy for PFETL database access.

Preserves security and diagnostics by capturing database path and profile context
without exposing sensitive SQL internals or row contents in end-user messages.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class WarehouseState(StrEnum):
    """Availability state of the analytical DuckDB warehouse."""

    AVAILABLE = "AVAILABLE"
    LOCKED = "LOCKED"
    UNAVAILABLE = "UNAVAILABLE"


class ControlPlaneState(StrEnum):
    """Availability state of SQLite Control Plane and Raw Store."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class PFETLDataAccessError(Exception):
    """Base exception for all PFETL database access failures."""

    def __init__(
        self,
        message: str,
        path: Path | None = None,
        profile_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self.profile_id = profile_id

    def __str__(self) -> str:
        """Return formatted error message with optional path and profile context."""
        ctx: list[str] = []
        if self.profile_id:
            ctx.append(f"profile_id={self.profile_id}")
        if self.path:
            ctx.append(f"path='{self.path}'")
        suffix = f" ({', '.join(ctx)})" if ctx else ""
        return f"{self.message}{suffix}"


class PFETLLockedError(PFETLDataAccessError):
    """Raised when DuckDB warehouse access is blocked because PFETL pipeline.lock is active."""


class DatabaseNotFoundError(PFETLDataAccessError):
    """Raised when the resolved DuckDB or Control Plane database file does not exist on disk."""


class ReadOnlyViolationError(PFETLDataAccessError):
    """Raised when a query attempts to perform data manipulation or DDL writes."""


class DatabaseCorruptedError(PFETLDataAccessError):
    """Raised when DuckDB or SQLite files cannot be opened due to disk/format corruption."""
