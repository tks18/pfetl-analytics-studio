"""Unit tests for PFETLProfile loading, path resolution, and immutability."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from pfetl_analytics_studio.profile import (
    PFETLProfile,
    ProfileDriftStatus,
    ProfileLoadErrorCode,
)


def _compute_file_sha256(path: Path) -> str:
    """Calculate SHA-256 hash of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_valid_profile_resolution(valid_settings_path: Path, valid_rules_path: Path) -> None:
    """Verify clean resolution of valid Settings and Rules TOML files."""
    result = PFETLProfile.load(valid_settings_path, valid_rules_path)
    assert result.is_ok
    assert result.error is None
    assert result.profile is not None

    profile = result.profile
    assert len(profile.profile_id) == 16
    assert profile.label == "valid_settings"
    assert profile.settings_path == valid_settings_path.resolve()
    assert profile.rules_path == valid_rules_path.resolve()
    assert profile.duckdb_path.name == "Personal_Finance_DB.duckdb"
    assert profile.control_plane_path.name == "Personal_Finance_DB_Raw_Store.sqlite"
    assert profile.lock_path.name == "pipeline.lock"
    assert profile.logs_path is not None
    assert "logs" in str(profile.logs_path)
    assert profile.drift_status == ProfileDriftStatus.MATCH
    assert profile.drift_details == []


def test_profile_model_frozen_immutability(
    valid_settings_path: Path, valid_rules_path: Path
) -> None:
    """Verify that PFETLProfile is strictly immutable and rejects attribute assignment."""
    result = PFETLProfile.load(valid_settings_path, valid_rules_path)
    assert result.profile is not None
    profile = result.profile

    with pytest.raises(ValidationError):
        setattr(profile, "label", "Attempted Mutation")


def test_deterministic_profile_id(valid_settings_path: Path, valid_rules_path: Path) -> None:
    """Verify that profile ID calculation is deterministic across call styles and casings."""
    id1 = PFETLProfile.compute_profile_id(valid_settings_path, valid_rules_path)
    id2 = PFETLProfile.compute_profile_id(
        Path(str(valid_settings_path)), Path(str(valid_rules_path))
    )
    assert id1 == id2
    assert len(id1) == 16


def test_missing_settings_file(valid_rules_path: Path, tmp_path: Path) -> None:
    """Verify typed error code when Settings file does not exist."""
    missing_settings = tmp_path / "non_existent_settings.toml"
    result = PFETLProfile.load(missing_settings, valid_rules_path)
    assert not result.is_ok
    assert result.profile is None
    assert result.error is not None
    assert result.error.code == ProfileLoadErrorCode.FILE_NOT_FOUND
    assert "Settings configuration file not found" in result.error.message


def test_missing_rules_file(valid_settings_path: Path, tmp_path: Path) -> None:
    """Verify typed error code when FinancialRules file does not exist."""
    missing_rules = tmp_path / "non_existent_rules.toml"
    result = PFETLProfile.load(valid_settings_path, missing_rules)
    assert not result.is_ok
    assert result.profile is None
    assert result.error is not None
    assert result.error.code == ProfileLoadErrorCode.FILE_NOT_FOUND
    assert "Financial rules configuration file not found" in result.error.message


def test_malformed_settings_toml(fixtures_dir: Path, valid_rules_path: Path) -> None:
    """Verify typed error code when Settings file has syntax errors."""
    malformed_path = fixtures_dir / "profiles" / "malformed.toml"
    result = PFETLProfile.load(malformed_path, valid_rules_path)
    assert not result.is_ok
    assert result.profile is None
    assert result.error is not None
    assert result.error.code == ProfileLoadErrorCode.MALFORMED_TOML
    assert "Malformed Settings TOML" in result.error.message


def test_missing_database_section(fixtures_dir: Path, valid_rules_path: Path) -> None:
    """Verify typed error code when Settings file lacks [database] section."""
    missing_db_path = fixtures_dir / "profiles" / "missing_db.toml"
    result = PFETLProfile.load(missing_db_path, valid_rules_path)
    assert not result.is_ok
    assert result.profile is None
    assert result.error is not None
    assert result.error.code == ProfileLoadErrorCode.MISSING_DATABASE_SETTINGS
    assert "Missing required [database] section" in result.error.message


def test_relative_logs_directory_resolution(
    valid_settings_path: Path, valid_rules_path: Path
) -> None:
    """Verify relative logs_directory is resolved against target_db_base_path."""
    res = PFETLProfile.load(valid_settings_path, valid_rules_path)
    assert res.profile is not None
    assert res.profile.logs_path is not None
    expected_logs = res.profile.target_db_base_path / "logs" / "runs"
    assert res.profile.logs_path == expected_logs


def test_toml_immutability_guarantee(
    valid_settings_path: Path, valid_rules_path: Path
) -> None:
    """Verify Studio strictly reads TOML files and preserves exact byte identities."""
    settings_before = _compute_file_sha256(valid_settings_path)
    rules_before = _compute_file_sha256(valid_rules_path)

    # Perform multiple load operations
    for _ in range(3):
        res = PFETLProfile.load(valid_settings_path, valid_rules_path)
        assert res.is_ok

    settings_after = _compute_file_sha256(valid_settings_path)
    rules_after = _compute_file_sha256(valid_rules_path)

    assert settings_before == settings_after, "Settings file was modified by Studio!"
    assert rules_before == rules_after, "FinancialRules file was modified by Studio!"
