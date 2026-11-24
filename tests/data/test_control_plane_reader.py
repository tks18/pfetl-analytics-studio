"""Unit tests for ControlPlaneReader gateway and read-only query primitives."""

from __future__ import annotations

import polars as pl
import pytest

from pfetl_analytics_studio.data.control_plane_reader import ControlPlaneReader
from pfetl_analytics_studio.data.errors import (
    ControlPlaneState,
    DatabaseNotFoundError,
    ReadOnlyViolationError,
)
from pfetl_analytics_studio.profile import PFETLProfile


def test_control_plane_reader_state_available(profile_with_databases: PFETLProfile) -> None:
    """Verify Control Plane SQLite state evaluates to AVAILABLE."""
    reader = ControlPlaneReader(profile_with_databases)
    assert reader.get_state() == ControlPlaneState.AVAILABLE
    assert reader.is_available()


def test_control_plane_reader_fetch_scalar(profile_with_databases: PFETLProfile) -> None:
    """Verify scalar query execution returns single value."""
    reader = ControlPlaneReader(profile_with_databases)
    count = reader.fetch_scalar("SELECT count(*) FROM cp_runs;")
    assert count == 2


def test_control_plane_reader_fetch_one(profile_with_databases: PFETLProfile) -> None:
    """Verify single row fetch returns column-mapped dictionary."""
    reader = ControlPlaneReader(profile_with_databases)
    row = reader.fetch_one(
        "SELECT run_id, status, version FROM cp_runs WHERE run_id = ?;", ("run-001",)
    )
    assert row is not None
    assert row["run_id"] == "run-001"
    assert row["status"] == "SUCCESS"
    assert row["version"] == "v11.3.1"

    # Non-existent record returns None
    assert reader.fetch_one("SELECT * FROM cp_runs WHERE run_id = 'NONEXISTENT';") is None


def test_control_plane_reader_fetch_all(profile_with_databases: PFETLProfile) -> None:
    """Verify fetch_all returns list of dictionaries."""
    reader = ControlPlaneReader(profile_with_databases)
    rows = reader.fetch_all("SELECT run_id FROM cp_runs ORDER BY run_id;")
    assert len(rows) == 2
    assert rows[0]["run_id"] == "run-001"
    assert rows[1]["run_id"] == "run-002"


def test_control_plane_reader_fetch_df(profile_with_databases: PFETLProfile) -> None:
    """Verify fetch_df returns a Polars DataFrame with correct schema and contents."""
    reader = ControlPlaneReader(profile_with_databases)
    df = reader.fetch_df("SELECT * FROM cp_file_registry ORDER BY file_id;")
    assert isinstance(df, pl.DataFrame)
    assert df.height == 2
    assert "file_id" in df.columns
    assert "sha256" in df.columns
    assert df["file_id"].to_list() == ["file-001", "file-002"]


def test_control_plane_reader_table_exists(profile_with_databases: PFETLProfile) -> None:
    """Verify table_exists checks table existence accurately."""
    reader = ControlPlaneReader(profile_with_databases)
    assert reader.table_exists("cp_runs")
    assert reader.table_exists("cp_file_registry")
    assert not reader.table_exists("non_existent_cp_table")


def test_control_plane_reader_bounded_preview(profile_with_databases: PFETLProfile) -> None:
    """Verify bounded_preview returns limited rows and clamps maximum limit."""
    reader = ControlPlaneReader(profile_with_databases)
    df_limited = reader.bounded_preview("cp_runs", limit=1)
    assert isinstance(df_limited, pl.DataFrame)
    assert df_limited.height == 1

    df_full = reader.bounded_preview("cp_runs", limit=1000)
    assert df_full.height == 2


def test_control_plane_reader_missing_database(profile_with_databases: PFETLProfile) -> None:
    """Verify missing database file raises DatabaseNotFoundError."""
    missing_cp = profile_with_databases.target_db_base_path / "non_existent.sqlite"
    missing_profile = profile_with_databases.model_copy(update={"control_plane_path": missing_cp})

    reader = ControlPlaneReader(missing_profile)
    assert reader.get_state() == ControlPlaneState.UNAVAILABLE
    assert not reader.is_available()

    with pytest.raises(DatabaseNotFoundError) as exc_info:
        reader.fetch_scalar("SELECT 1;")

    assert "not found" in str(exc_info.value)


def test_control_plane_reader_write_attempt_fails(profile_with_databases: PFETLProfile) -> None:
    """Verify write attempts are rejected with ReadOnlyViolationError."""
    reader = ControlPlaneReader(profile_with_databases)

    with pytest.raises(ReadOnlyViolationError):
        reader.fetch_scalar("INSERT INTO cp_runs VALUES ('bad', 't', 't', 'f', 'v');")

    with pytest.raises(ReadOnlyViolationError):
        reader.fetch_scalar("DELETE FROM cp_runs;")
