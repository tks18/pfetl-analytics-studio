"""Integration tests verifying profile database connections and independent availability."""

from __future__ import annotations

from pathlib import Path

import pytest

from pfetl_analytics_studio.data.control_plane_reader import ControlPlaneReader
from pfetl_analytics_studio.data.duckdb_reader import DuckDBReader
from pfetl_analytics_studio.data.errors import (
    ControlPlaneState,
    PFETLLockedError,
    WarehouseState,
)
from pfetl_analytics_studio.profile import PFETLProfile


def test_profile_database_connections(profile_with_databases: PFETLProfile) -> None:
    """Verify clean read-only connections to both DuckDB and Control Plane SQLite."""
    duck_reader = DuckDBReader(profile_with_databases)
    cp_reader = ControlPlaneReader(profile_with_databases)

    assert duck_reader.get_state() == WarehouseState.AVAILABLE
    assert cp_reader.get_state() == ControlPlaneState.AVAILABLE

    # Query warehouse
    net_worth_sum = duck_reader.fetch_scalar("SELECT sum(net_worth) FROM gold.core_monthly_fact;")
    assert net_worth_sum == 3100000.0

    # Query Control Plane
    run_count = cp_reader.fetch_scalar("SELECT count(*) FROM cp_runs;")
    assert run_count == 2


def test_independent_locked_availability(profile_with_databases: PFETLProfile) -> None:
    """Verify DuckDB warehouse is locked while Control Plane remains available."""
    duck_reader = DuckDBReader(profile_with_databases)
    cp_reader = ControlPlaneReader(profile_with_databases)

    # Create pipeline lock file
    lock_file = profile_with_databases.lock_path
    lock_file.write_text("PID 9999 Core writing", encoding="utf-8")

    try:
        assert duck_reader.is_locked()
        assert duck_reader.get_state() == WarehouseState.LOCKED
        assert not duck_reader.is_available()

        # Control Plane is unaffected by DuckDB lock
        assert cp_reader.get_state() == ControlPlaneState.AVAILABLE
        assert cp_reader.is_available()
        assert cp_reader.fetch_scalar("SELECT count(*) FROM cp_runs;") == 2

        # DuckDB query fails with PFETLLockedError
        with pytest.raises(PFETLLockedError):
            duck_reader.fetch_scalar("SELECT 1;")
    finally:
        if lock_file.exists():
            lock_file.unlink()


def test_partial_availability_missing_duckdb(
    profile_with_databases: PFETLProfile, tmp_path: Path
) -> None:
    """Verify Control Plane is accessible even when DuckDB warehouse is absent."""
    missing_duckdb = tmp_path / "absent.duckdb"
    partial_profile = profile_with_databases.model_copy(update={"duckdb_path": missing_duckdb})

    duck_reader = DuckDBReader(partial_profile)
    cp_reader = ControlPlaneReader(partial_profile)

    assert duck_reader.get_state() == WarehouseState.UNAVAILABLE
    assert not duck_reader.is_available()

    # Control Plane is still available
    assert cp_reader.get_state() == ControlPlaneState.AVAILABLE
    assert cp_reader.fetch_scalar("SELECT count(*) FROM cp_runs;") == 2
