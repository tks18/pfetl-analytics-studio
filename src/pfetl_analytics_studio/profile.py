"""PFETL Settings and FinancialRules profile management and drift verification.

Owns path resolution and run snapshot comparison. Does not execute finance algorithms.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class ProfileDriftStatus(StrEnum):
    """Drift check comparison status."""

    MATCH = "MATCH"
    DRIFTED = "DRIFTED"
    UNVERIFIED_DEFAULTS = "UNVERIFIED DEFAULTS"


class ProfilePaths(BaseModel):
    """Resolved warehouse paths from Settings TOML."""

    target_db_base_path: Path
    warehouse_db_path: Path
    raw_document_store_path: Path
    logs_directory: Path | None = None


class ProfileContext(BaseModel):
    """Active profile context and metadata."""

    settings_path: Path
    rules_path: Path
    paths: ProfilePaths
    drift_status: ProfileDriftStatus = ProfileDriftStatus.MATCH
    drift_details: list[str] = Field(default_factory=list)
