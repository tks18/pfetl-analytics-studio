"""Tests for Phase 19 OperationsAnalytics, Control Plane sync join & warehouse health."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb

from pfetl_analytics_studio.analytics import OperationsAnalytics, RunHistoryFilter
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.ui import (
    build_ops_duration_trend_figure,
    build_ops_layer_contracts_figure,
    build_ops_status_breakdown_figure,
    build_ops_sync_outcomes_figure,
    build_ops_table_row_counts_figure,
    build_ops_version_timeline_figure,
)


def _create_cp_db(cp_path: Path) -> None:
    con = sqlite3.connect(str(cp_path))
    try:
        con.execute(
            """
            CREATE TABLE cp_runs (
                run_id TEXT PRIMARY KEY,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                status TEXT,
                commit_state TEXT,
                application_version TEXT,
                schema_version TEXT,
                settings_snapshot_id TEXT,
                rules_snapshot_id TEXT,
                execution_log BLOB
            );
            """
        )
        con.execute(
            """
            CREATE TABLE cp_run_sync_summaries (
                run_id TEXT PRIMARY KEY,
                actionable_count INTEGER,
                synced_count INTEGER,
                healed_count INTEGER,
                failed_count INTEGER,
                deferred_count INTEGER,
                sync_status TEXT,
                created_at TIMESTAMP
            );
            """
        )
        con.execute(
            """
            INSERT INTO cp_runs VALUES
                ('run-3', '2026-11-26T10:00:00', NULL, 'RUNNING', 'UNCOMMITTED', 'v12.1.3', 'v3', 'set-3', 'rul-3', NULL),
                ('run-2', '2026-11-26T00:02:39', '2026-11-26T00:03:37', 'SUCCESS', 'FULLY_COMMITTED', 'v12.1.3', 'v3', 'set-2', 'rul-2', X'789c'),
                ('run-1', '2026-11-25T20:00:00', '2026-11-25T20:00:30', 'FAILED', 'PARTIAL', 'v12.0.0', 'v3', 'set-1', 'rul-1', X'789c');
            """
        )
        con.execute(
            """
            INSERT INTO cp_run_sync_summaries VALUES
                ('run-2', 10, 8, 1, 0, 1, 'COMPLETE', '2026-11-26T00:02:41'),
                ('run-1', 5, 2, 0, 3, 0, 'FAILED', '2026-11-25T20:00:05');
            """
        )
        con.commit()
    finally:
        con.close()


def _create_meta_db(db_path: Path) -> None:
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA meta;")
        con.execute(
            """
            CREATE TABLE meta.m_Data_Contracts (
                contract_id VARCHAR,
                layer VARCHAR,
                physical_table VARCHAR,
                domain VARCHAR,
                grain VARCHAR,
                producer VARCHAR,
                is_full_replace BOOLEAN,
                publication_order INTEGER
            );
            INSERT INTO meta.m_Data_Contracts VALUES
                ('c1', 'bronze', 'bronze.b1', 'Raw', 'File', 'CP', TRUE, 0),
                ('c2', 'silver', 'silver.s1', 'Household', 'Tx', 'ETL', FALSE, 1),
                ('c3', 'gold', 'gold.g1', 'Wealth', 'Month', 'ETL', TRUE, 2);
            """
        )
        con.execute(
            """
            CREATE TABLE meta.m_Table_Row_Counts (
                schema_name VARCHAR,
                table_name VARCHAR,
                row_count BIGINT,
                generated_at TIMESTAMP
            );
            INSERT INTO meta.m_Table_Row_Counts VALUES
                ('silver', 's1', 1200, '2026-11-26 00:03:00'),
                ('gold', 'g1', 350, '2026-11-26 00:03:00');
            """
        )
    finally:
        con.close()


def _make_profile(db_path: Path, cp_path: Path) -> PFETLProfile:
    return PFETLProfile(
        profile_id="ops-test",
        label="Ops Test",
        settings_path=db_path.parent / "s.toml",
        rules_path=db_path.parent / "r.toml",
        target_db_base_path=db_path.parent,
        duckdb_path=db_path,
        control_plane_path=cp_path,
        lock_path=db_path.parent / "p.lock",
    )


def test_operations_run_sync_and_warehouse_health(tmp_path: Path) -> None:
    db_path = tmp_path / "ops.duckdb"
    cp_path = tmp_path / "cp.sqlite"
    _create_meta_db(db_path)
    _create_cp_db(cp_path)
    profile = _make_profile(db_path, cp_path)
    cache = QueryCache()

    snap = OperationsAnalytics.run_history(profile, cache=cache)
    assert snap.state == "ready"
    assert len(snap.runs) == 3
    assert len(snap.kpis) == 8

    by_id = {r.run_id: r for r in snap.runs}
    # Unfinished run has None duration and is_unfinished=True
    assert by_id["run-3"].is_unfinished is True
    assert by_id["run-3"].duration_seconds is None
    assert by_id["run-3"].sync is None  # missing sync summary allowed
    # Finished run computes duration strictly from timestamps
    assert by_id["run-2"].is_unfinished is False
    assert by_id["run-2"].duration_seconds == 58.0
    assert by_id["run-2"].sync is not None
    assert by_id["run-2"].sync.actionable_count == 10
    assert by_id["run-2"].sync.synced_count == 8
    assert by_id["run-2"].sync.healed_count == 1
    assert by_id["run-2"].sync.deferred_count == 1

    # Independent status & commit_state filters
    failed_runs = OperationsAnalytics.sync_history(
        profile, filters=RunHistoryFilter(status="FAILED", commit_state="PARTIAL")
    )
    assert len(failed_runs) == 1
    assert failed_runs[0].run_id == "run-1"
    assert failed_runs[0].duration_seconds == 30.0
    assert failed_runs[0].sync is not None
    assert failed_runs[0].sync.failed_count == 3

    # Warehouse health includes every classified layer
    wh = OperationsAnalytics.warehouse_health(profile, cache=cache)
    assert wh.state == "ready"
    assert wh.total_contracts == 3
    assert wh.total_tracked_tables == 2
    assert wh.total_tracked_rows == 1550
    assert {layer.layer for layer in wh.layers} == {"bronze", "silver", "gold"}

    # All 6 Plotly chart builders produce non-empty traces
    figs = [
        build_ops_duration_trend_figure(snap.runs),
        build_ops_status_breakdown_figure(snap.runs),
        build_ops_sync_outcomes_figure(snap.runs),
        build_ops_version_timeline_figure(snap.runs),
        build_ops_layer_contracts_figure(wh.layers),
        build_ops_table_row_counts_figure(wh.tables),
    ]
    for fig in figs:
        assert len(fig["data"]) >= 1
