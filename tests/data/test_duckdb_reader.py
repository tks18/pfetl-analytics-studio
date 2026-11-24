"""Unit tests for DuckDBReader gateway, lock safety, and read-only query primitives."""

from __future__ import annotations

import polars as pl
import pytest

from pfetl_analytics_studio.data.duckdb_reader import DuckDBReader
from pfetl_analytics_studio.data.errors import (
    DatabaseNotFoundError,
    PFETLLockedError,
    ReadOnlyViolationError,
    WarehouseState,
)
from pfetl_analytics_studio.profile import PFETLProfile


def test_duckdb_reader_state_available(profile_with_databases: PFETLProfile) -> None:
    """Verify DuckDB warehouse state evaluates to AVAILABLE when unlocked."""
    reader = DuckDBReader(profile_with_databases)
    assert reader.get_state() == WarehouseState.AVAILABLE
    assert reader.is_available()
    assert not reader.is_locked()


def test_duckdb_reader_fetch_scalar(profile_with_databases: PFETLProfile) -> None:
    """Verify scalar query execution returns single value."""
    reader = DuckDBReader(profile_with_databases)
    count = reader.fetch_scalar("SELECT count(*) FROM meta.m_File_Registry;")
    assert count == 2


def test_duckdb_reader_fetch_one(profile_with_databases: PFETLProfile) -> None:
    """Verify single row fetch returns column-mapped dictionary."""
    reader = DuckDBReader(profile_with_databases)
    row = reader.fetch_one(
        "SELECT file_id, file_name, row_count FROM meta.m_File_Registry WHERE file_id = ?;",
        ("F1",),
    )
    assert row is not None
    assert row["file_id"] == "F1"
    assert row["file_name"] == "statements.csv"
    assert row["row_count"] == 100

    # Non-existent record returns None
    assert (
        reader.fetch_one("SELECT * FROM meta.m_File_Registry WHERE file_id = 'NONEXISTENT';")
        is None
    )


def test_duckdb_reader_fetch_all(profile_with_databases: PFETLProfile) -> None:
    """Verify fetch_all returns list of dictionaries."""
    reader = DuckDBReader(profile_with_databases)
    rows = reader.fetch_all("SELECT file_id, file_name FROM meta.m_File_Registry ORDER BY file_id;")
    assert len(rows) == 2
    assert rows[0]["file_id"] == "F1"
    assert rows[1]["file_id"] == "F2"


def test_duckdb_reader_fetch_df(profile_with_databases: PFETLProfile) -> None:
    """Verify fetch_df returns a Polars DataFrame with correct schema and contents."""
    reader = DuckDBReader(profile_with_databases)
    df = reader.fetch_df("SELECT * FROM gold.core_monthly_fact ORDER BY period;")
    assert isinstance(df, pl.DataFrame)
    assert df.height == 2
    assert "period" in df.columns
    assert "net_worth" in df.columns
    assert df["period"].to_list() == ["2026-03", "2026-04"]


def test_duckdb_reader_table_exists(profile_with_databases: PFETLProfile) -> None:
    """Verify table_exists verifies table existence accurately."""
    reader = DuckDBReader(profile_with_databases)
    assert reader.table_exists("meta", "m_File_Registry")
    assert reader.table_exists("silver", "dim_calendar")
    assert reader.table_exists("gold", "core_monthly_fact")
    assert not reader.table_exists("gold", "non_existent_table")
    assert not reader.table_exists("invalid_schema", "dim_calendar")


def test_duckdb_reader_bounded_preview(profile_with_databases: PFETLProfile) -> None:
    """Verify bounded_preview returns limited rows and clamps maximum limit."""
    reader = DuckDBReader(profile_with_databases)
    df_limited = reader.bounded_preview("silver", "dim_calendar", limit=1)
    assert isinstance(df_limited, pl.DataFrame)
    assert df_limited.height == 1

    df_full = reader.bounded_preview("silver", "dim_calendar", limit=1000)
    assert df_full.height == 2  # Total rows in fixture table


def test_duckdb_reader_identifier_validation(profile_with_databases: PFETLProfile) -> None:
    """Verify SQL injection characters in identifier are rejected with ValueError."""
    reader = DuckDBReader(profile_with_databases)
    with pytest.raises(ValueError, match="Security violation: Invalid SQL identifier"):
        reader.bounded_preview('silver"; DROP TABLE dim_calendar; --', "dim_calendar")

    with pytest.raises(ValueError, match="Security violation: Invalid SQL identifier"):
        reader.table_exists("silver", "table; --")


def test_duckdb_reader_locked_state(profile_with_databases: PFETLProfile) -> None:
    """Verify active pipeline.lock raises PFETLLockedError and blocks connection."""
    reader = DuckDBReader(profile_with_databases)
    lock_file = profile_with_databases.lock_path
    lock_file.write_text("PID 12345 PFETL Running", encoding="utf-8")

    try:
        assert reader.get_state() == WarehouseState.LOCKED
        assert reader.is_locked()
        assert not reader.is_available()

        with pytest.raises(PFETLLockedError) as exc_info:
            reader.fetch_scalar("SELECT 1;")

        assert "active pipeline lock" in str(exc_info.value)
        assert exc_info.value.path == lock_file
        assert exc_info.value.profile_id == profile_with_databases.profile_id
    finally:
        if lock_file.exists():
            lock_file.unlink()


def test_duckdb_reader_missing_database(profile_with_databases: PFETLProfile) -> None:
    """Verify missing database file raises DatabaseNotFoundError."""
    missing_duckdb = profile_with_databases.target_db_base_path / "non_existent.duckdb"
    missing_profile = profile_with_databases.model_copy(update={"duckdb_path": missing_duckdb})

    reader = DuckDBReader(missing_profile)
    assert reader.get_state() == WarehouseState.UNAVAILABLE
    assert not reader.is_available()

    with pytest.raises(DatabaseNotFoundError) as exc_info:
        reader.fetch_scalar("SELECT 1;")

    assert "not found" in str(exc_info.value)


def test_duckdb_reader_write_attempt_fails(profile_with_databases: PFETLProfile) -> None:
    """Verify write attempts are rejected with ReadOnlyViolationError."""
    reader = DuckDBReader(profile_with_databases)

    with pytest.raises(ReadOnlyViolationError):
        reader.fetch_scalar("CREATE TABLE meta.malicious (id INTEGER);")

    with pytest.raises(ReadOnlyViolationError):
        reader.fetch_scalar("DELETE FROM meta.m_File_Registry;")
