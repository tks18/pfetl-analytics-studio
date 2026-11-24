"""PFETL Settings and FinancialRules profile management and drift verification.

Owns path resolution and run snapshot comparison. Does not execute finance algorithms.
Never writes to PFETL DuckDB, Control Plane SQLite, Settings TOML or FinancialRules TOML.
"""

from __future__ import annotations

import hashlib
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field


class ProfileDriftStatus(StrEnum):
    """Drift check comparison status."""

    MATCH = "MATCH"
    DRIFTED = "DRIFTED"
    UNVERIFIED_DEFAULTS = "UNVERIFIED DEFAULTS"


class ProfileLoadErrorCode(StrEnum):
    """Typed failure code for profile resolution and validation."""

    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    MALFORMED_TOML = "MALFORMED_TOML"
    MISSING_DATABASE_SETTINGS = "MISSING_DATABASE_SETTINGS"
    INVALID_PATHS = "INVALID_PATHS"


class ProfileLoadError(BaseModel):
    """Structured error information when a profile cannot be resolved."""

    model_config = ConfigDict(frozen=True)

    code: ProfileLoadErrorCode
    message: str
    file_path: Path | None = None


class PFETLProfile(BaseModel):
    """Immutable resolved PFETL profile context and database destinations.

    Notes
    -----
    Profile identity is deterministic from normalized absolute Settings + Rules paths,
    not from file contents.
    """

    model_config = ConfigDict(frozen=True)

    profile_id: str
    label: str
    settings_path: Path
    rules_path: Path
    target_db_base_path: Path
    duckdb_path: Path
    control_plane_path: Path
    logs_path: Path | None = None
    lock_path: Path
    drift_status: ProfileDriftStatus = ProfileDriftStatus.MATCH
    drift_details: list[str] = Field(default_factory=list)

    @classmethod
    def compute_profile_id(cls, settings_path: Path, rules_path: Path) -> str:
        """Compute a deterministic 16-character hex profile ID from normalized paths.

        Parameters
        ----------
        settings_path : Path
            Path to Settings TOML.
        rules_path : Path
            Path to FinancialRules TOML.

        Returns
        -------
        str
            First 16 hexadecimal characters of SHA-256 over normalized absolute paths.
        """
        norm_settings = str(settings_path.resolve().as_posix()).lower()
        norm_rules = str(rules_path.resolve().as_posix()).lower()
        composite = f"{norm_settings}:{norm_rules}"
        return hashlib.sha256(composite.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def load(
        cls,
        settings_path: Path | str,
        rules_path: Path | str,
        label: str | None = None,
    ) -> ProfileLoadResult:
        """Load and resolve a PFETLProfile from Settings and Rules TOML files.

        Parameters
        ----------
        settings_path : Path | str
            Filesystem path to PFETL Settings TOML.
        rules_path : Path | str
            Filesystem path to PFETL FinancialRules TOML.
        label : str | None, optional
            Human-friendly profile label, by default derived from settings filename stem.

        Returns
        -------
        ProfileLoadResult
            Result container containing the resolved profile or typed failure details.
        """
        s_path = Path(settings_path)
        r_path = Path(rules_path)

        if not s_path.exists():
            return ProfileLoadResult(
                error=ProfileLoadError(
                    code=ProfileLoadErrorCode.FILE_NOT_FOUND,
                    message=f"Settings configuration file not found: '{s_path}'",
                    file_path=s_path,
                )
            )

        if not r_path.exists():
            return ProfileLoadResult(
                error=ProfileLoadError(
                    code=ProfileLoadErrorCode.FILE_NOT_FOUND,
                    message=f"Financial rules configuration file not found: '{r_path}'",
                    file_path=r_path,
                )
            )

        try:
            settings_content = s_path.read_text(encoding="utf-8")
            settings_data: dict[str, Any] = tomllib.loads(settings_content)
        except tomllib.TOMLDecodeError as exc:
            return ProfileLoadResult(
                error=ProfileLoadError(
                    code=ProfileLoadErrorCode.MALFORMED_TOML,
                    message=f"Malformed Settings TOML in '{s_path.name}': {exc}",
                    file_path=s_path,
                )
            )
        except OSError as exc:
            return ProfileLoadResult(
                error=ProfileLoadError(
                    code=ProfileLoadErrorCode.INVALID_PATHS,
                    message=f"Failed to read Settings file '{s_path}': {exc}",
                    file_path=s_path,
                )
            )

        try:
            rules_content = r_path.read_text(encoding="utf-8")
            _rules_data: dict[str, Any] = tomllib.loads(rules_content)
        except tomllib.TOMLDecodeError as exc:
            return ProfileLoadResult(
                error=ProfileLoadError(
                    code=ProfileLoadErrorCode.MALFORMED_TOML,
                    message=f"Malformed FinancialRules TOML in '{r_path.name}': {exc}",
                    file_path=r_path,
                )
            )
        except OSError as exc:
            return ProfileLoadResult(
                error=ProfileLoadError(
                    code=ProfileLoadErrorCode.INVALID_PATHS,
                    message=f"Failed to read FinancialRules file '{r_path}': {exc}",
                    file_path=r_path,
                )
            )

        db_raw = settings_data.get("database")
        if not isinstance(db_raw, dict):
            return ProfileLoadResult(
                error=ProfileLoadError(
                    code=ProfileLoadErrorCode.MISSING_DATABASE_SETTINGS,
                    message="Missing required [database] section in Settings TOML.",
                    file_path=s_path,
                )
            )

        db_section = cast("dict[str, Any]", db_raw)
        target_base_raw = db_section.get("target_db_base_path")
        if not isinstance(target_base_raw, str) or not target_base_raw.strip():
            return ProfileLoadResult(
                error=ProfileLoadError(
                    code=ProfileLoadErrorCode.MISSING_DATABASE_SETTINGS,
                    message="Required 'target_db_base_path' is missing or empty in [database].",
                    file_path=s_path,
                )
            )

        target_base_str: str = target_base_raw.strip()
        db_name_val = db_section.get("target_db_name")
        target_db_name: str = (
            str(db_name_val).strip()
            if isinstance(db_name_val, str) and db_name_val.strip()
            else "Personal_Finance_DB.duckdb"
        )

        raw_doc_val = db_section.get("raw_document_store_name")
        raw_doc_name: str = (
            str(raw_doc_val).strip()
            if isinstance(raw_doc_val, str) and raw_doc_val.strip()
            else "Personal_Finance_DB_Raw_Store.sqlite"
        )

        # Resolve paths against settings file directory if relative
        settings_dir = s_path.resolve().parent
        target_base_path = Path(target_base_str)
        if not target_base_path.is_absolute():
            resolved_target_base = (settings_dir / target_base_path).resolve()
        else:
            resolved_target_base = target_base_path.resolve()

        duckdb_path = (resolved_target_base / target_db_name).resolve()
        control_plane_path = (resolved_target_base / raw_doc_name).resolve()
        lock_path = (resolved_target_base / "pipeline.lock").resolve()

        # Observability logs directory resolution
        obs_raw = settings_data.get("observability")
        logs_path: Path | None = None
        if isinstance(obs_raw, dict):
            obs_dict = cast("dict[str, Any]", obs_raw)
            logs_dir_val = obs_dict.get("logs_directory")
            if isinstance(logs_dir_val, str) and logs_dir_val.strip():
                p_logs = Path(logs_dir_val.strip())
                if p_logs.is_absolute():
                    logs_path = p_logs.resolve()
                else:
                    logs_path = (resolved_target_base / p_logs).resolve()

        resolved_settings = s_path.resolve()
        resolved_rules = r_path.resolve()
        profile_id = cls.compute_profile_id(resolved_settings, resolved_rules)
        profile_label = label or resolved_settings.stem

        profile = cls(
            profile_id=profile_id,
            label=profile_label,
            settings_path=resolved_settings,
            rules_path=resolved_rules,
            target_db_base_path=resolved_target_base,
            duckdb_path=duckdb_path,
            control_plane_path=control_plane_path,
            logs_path=logs_path,
            lock_path=lock_path,
            drift_status=ProfileDriftStatus.MATCH,
            drift_details=[],
        )

        return ProfileLoadResult(profile=profile)


class ProfileLoadResult(BaseModel):
    """Result wrapper for profile loading operations."""

    model_config = ConfigDict(frozen=True)

    profile: PFETLProfile | None = None
    error: ProfileLoadError | None = None

    @property
    def is_ok(self) -> bool:
        """Evaluate if profile loading succeeded cleanly."""
        return self.profile is not None and self.error is None
