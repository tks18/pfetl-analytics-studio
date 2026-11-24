"""Deterministic test fixture database generator for PFETL Analytics Studio."""

from __future__ import annotations

import json
import sqlite3
import zlib
from pathlib import Path
from typing import Any

import duckdb

from pfetl_analytics_studio.data.catalog import SURFACE_ATLAS, SurfaceLayer


def create_test_databases(target_dir: Path) -> tuple[Path, Path]:
    """Generate minimal deterministic DuckDB and SQLite Control Plane test databases.

    Preserves exact Phase 03 test contract schemas.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    duckdb_path = target_dir / "Personal_Finance_DB.duckdb"
    control_plane_path = target_dir / "Personal_Finance_DB_Raw_Store.sqlite"

    if duckdb_path.exists():
        duckdb_path.unlink()

    conn_duck = duckdb.connect(database=str(duckdb_path))
    try:
        conn_duck.execute("CREATE SCHEMA IF NOT EXISTS bronze;")
        conn_duck.execute("CREATE SCHEMA IF NOT EXISTS silver;")
        conn_duck.execute("CREATE SCHEMA IF NOT EXISTS gold;")
        conn_duck.execute("CREATE SCHEMA IF NOT EXISTS meta;")

        conn_duck.execute(
            """
            CREATE TABLE meta.m_File_Registry (
                file_id VARCHAR,
                file_name VARCHAR,
                file_category VARCHAR,
                row_count INTEGER
            );
            """
        )
        conn_duck.execute(
            """
            INSERT INTO meta.m_File_Registry VALUES
            ('F1', 'statements.csv', 'broker_statement', 100),
            ('F2', 'orders.csv', 'mf_orders', 50);
            """
        )

        conn_duck.execute(
            """
            CREATE TABLE silver.dim_calendar (
                "Date" DATE,
                "Day" INTEGER,
                "Month" INTEGER,
                "Year" INTEGER,
                "FY" VARCHAR
            );
            """
        )
        conn_duck.execute(
            """
            INSERT INTO silver.dim_calendar VALUES
            ('2026-03-31', 31, 3, 2026, 'FY2025-26'),
            ('2026-04-01', 1, 4, 2026, 'FY2026-27');
            """
        )

        conn_duck.execute(
            """
            CREATE TABLE gold.core_monthly_fact (
                period VARCHAR,
                net_worth DOUBLE,
                monthly_income DOUBLE,
                monthly_expense DOUBLE
            );
            """
        )
        conn_duck.execute(
            """
            INSERT INTO gold.core_monthly_fact VALUES
            ('2026-03', 1500000.0, 150000.0, 50000.0),
            ('2026-04', 1600000.0, 160000.0, 55000.0);
            """
        )
    finally:
        conn_duck.close()

    if control_plane_path.exists():
        control_plane_path.unlink()

    conn_sqlite = sqlite3.connect(control_plane_path)
    try:
        conn_sqlite.execute(
            """
            CREATE TABLE cp_runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT,
                finished_at TEXT,
                status TEXT,
                version TEXT
            );
            """
        )
        conn_sqlite.execute(
            """
            INSERT INTO cp_runs VALUES
            ('run-001', '2026-03-31T20:00:00Z', '2026-03-31T20:05:00Z', 'SUCCESS', 'v11.3.1'),
            ('run-002', '2026-04-30T20:00:00Z', '2026-04-30T20:05:00Z', 'SUCCESS', 'v11.3.1');
            """
        )

        conn_sqlite.execute(
            """
            CREATE TABLE cp_file_registry (
                file_id TEXT PRIMARY KEY,
                path TEXT,
                category TEXT,
                sha256 TEXT
            );
            """
        )
        conn_sqlite.execute(
            """
            INSERT INTO cp_file_registry VALUES
            ('file-001', '/data/statements.csv', 'statements', 'abcdef1234567890'),
            ('file-002', '/data/orders.csv', 'orders', '1234567890abcdef');
            """
        )
        conn_sqlite.commit()
    finally:
        conn_sqlite.close()

    return duckdb_path, control_plane_path


def _sample_events_jsonl(run_id: str) -> bytes:
    """Build deterministic zlib-compressed JSONL trace bytes mimicking Core ObservabilityEvent."""
    events: list[dict[str, Any]] = [
        {
            "schema_version": 1,
            "event_id": "evt_001",
            "sequence": 1,
            "timestamp": "2026-11-24T14:00:00+00:00",
            "event_code": "RUN_START",
            "severity": "INFO",
            "presentation_kind": "STEP",
            "outcome": "STARTED",
            "context": {
                "run_id": run_id,
                "stage": "INITIALIZING",
                "component": "lifecycle",
                "operation": "initialize",
                "operation_id": "op_root",
            },
            "message": "Pipeline execution started",
        },
        {
            "schema_version": 1,
            "event_id": "evt_002",
            "sequence": 2,
            "timestamp": "2026-11-24T14:00:05+00:00",
            "event_code": "SCHEMA_VALIDATION",
            "severity": "INFO",
            "presentation_kind": "SUCCESS",
            "outcome": "COMPLETED",
            "context": {
                "run_id": run_id,
                "stage": "EXTRACTING",
                "component": "gatekeeper",
                "operation": "validate",
                "operation_id": "op_child_1",
                "parent_operation_id": "op_root",
                "duration_ms": 12.5,
                "rows": 42,
            },
            "message": "Validated schemas cleanly",
        },
        {
            "schema_version": 1,
            "event_id": "evt_003",
            "sequence": 3,
            "timestamp": "2026-11-24T14:00:10+00:00",
            "event_code": "RUN_COMPLETED",
            "severity": "INFO",
            "presentation_kind": "SUCCESS",
            "outcome": "COMPLETED",
            "context": {
                "run_id": run_id,
                "stage": "COMPLETED",
                "component": "lifecycle",
                "operation": "finish",
                "operation_id": "op_root",
                "commit_state": "FULLY_COMMITTED",
            },
            "message": "Pipeline execution finished cleanly",
        },
    ]
    jsonl = "\n".join(json.dumps(evt, separators=(",", ":")) for evt in events) + "\n"
    return zlib.compress(jsonl.encode("utf-8"), level=6)


def create_full_atlas_databases(
    target_dir: Path,
    settings_payload: dict[str, Any] | None = None,
    rules_payload: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Create a schema-complete synthetic DuckDB + Control Plane covering all 82 surfaces."""
    target_dir.mkdir(parents=True, exist_ok=True)
    duckdb_path = target_dir / "Personal_Finance_DB.duckdb"
    cp_path = target_dir / "Personal_Finance_DB_Raw_Store.sqlite"

    if duckdb_path.exists():
        duckdb_path.unlink()
    if cp_path.exists():
        cp_path.unlink()

    # 1. Populate DuckDB with all 72 classified DuckDB surfaces
    dcon = duckdb.connect(database=str(duckdb_path))
    try:
        for schema in ("bronze", "silver", "gold", "meta"):
            dcon.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")

        contracts_rows: list[tuple[str, str, str, str, str, str, bool, int]] = []
        row_count_rows: list[tuple[str, str, int, str]] = []

        for idx, (phys, spec) in enumerate(sorted(SURFACE_ATLAS.items()), start=1):
            if spec.layer == SurfaceLayer.CONTROL_PLANE:
                continue
            schema_name, table_name = phys.split(".", 1)
            cols = spec.required_columns or ("sample_id",)
            col_defs = ", ".join(f'"{c}" VARCHAR' for c in cols)
            dcon.execute(f'CREATE TABLE "{schema_name}"."{table_name}" ({col_defs});')

            if spec.layer in (SurfaceLayer.BRONZE, SurfaceLayer.SILVER, SurfaceLayer.GOLD):
                contracts_rows.append(
                    (
                        table_name,
                        spec.layer.value,
                        phys,
                        spec.domain,
                        spec.grain,
                        spec.producer,
                        True,
                        idx,
                    )
                )
            if spec.layer in (SurfaceLayer.SILVER, SurfaceLayer.GOLD):
                row_count_rows.append((schema_name, table_name, idx * 10, "2026-11-24T14:00:10"))

        dcon.executemany(
            "INSERT INTO meta.m_Data_Contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            contracts_rows,
        )
        dcon.executemany(
            "INSERT INTO meta.m_Table_Row_Counts VALUES (?, ?, ?, ?);",
            row_count_rows,
        )
    finally:
        dcon.close()

    # 2. Populate SQLite Control Plane with all 10 cp_* surfaces (plus autoincrement sequence table)
    default_settings = settings_payload or {
        "database": {
            "target_db_base_path": str(target_dir),
            "target_db_name": duckdb_path.name,
            "raw_document_store_name": cp_path.name,
            "disable_file_discoverer": False,
        },
        "observability": {
            "logs_directory": "logs/runs",
        },
    }
    default_rules = rules_payload or {"DEFAULT_CURRENCY_ID": "INR_INR"}

    scon = sqlite3.connect(cp_path)
    try:
        for phys, spec in sorted(SURFACE_ATLAS.items()):
            if spec.layer != SurfaceLayer.CONTROL_PLANE:
                continue
            cols = spec.required_columns
            if phys == "cp_run_failures":
                # Include AUTOINCREMENT PK to mirror real sqlite_sequence behavior
                col_defs = ", ".join(
                    '"id" INTEGER PRIMARY KEY AUTOINCREMENT' if c == "id" else f'"{c}" TEXT'
                    for c in cols
                )
            elif phys == "cp_runs":
                col_defs = ", ".join(
                    f'"{c}" BLOB' if c == "execution_log" else f'"{c}" TEXT' for c in cols
                )
            else:
                col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
            scon.execute(f'CREATE TABLE "{phys}" ({col_defs});')

        scon.execute(
            "INSERT INTO cp_settings_snapshots (snapshot_id, content_hash, canonical_payload, created_at) "
            "VALUES (?, ?, ?, ?);",
            (
                "snap_set_hist",
                "hash_hist",
                json.dumps({"DEFAULT_MARKER": "old", **default_settings}),
                "2026-11-01T10:00:00",
            ),
        )
        scon.execute(
            "INSERT INTO cp_settings_snapshots (snapshot_id, content_hash, canonical_payload, created_at) "
            "VALUES (?, ?, ?, ?);",
            (
                "snap_set_latest",
                "hash_latest",
                json.dumps(default_settings),
                "2026-11-24T14:00:00",
            ),
        )

        scon.execute(
            "INSERT INTO cp_rules_snapshots (snapshot_id, content_hash, canonical_payload, created_at) "
            "VALUES (?, ?, ?, ?);",
            (
                "snap_rule_hist",
                "hash_r_hist",
                json.dumps({"DEFAULT_CURRENCY_ID": "USD_USD"}),
                "2026-11-01T10:00:00",
            ),
        )
        scon.execute(
            "INSERT INTO cp_rules_snapshots (snapshot_id, content_hash, canonical_payload, created_at) "
            "VALUES (?, ?, ?, ?);",
            (
                "snap_rule_latest",
                "hash_r_latest",
                json.dumps(default_rules),
                "2026-11-24T14:00:00",
            ),
        )

        # Older successful run
        scon.execute(
            """
            INSERT INTO cp_runs (
                run_id, started_at, finished_at, status, commit_state,
                application_version, schema_version, settings_snapshot_id, rules_snapshot_id,
                execution_log
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                "run-hist-001",
                "2026-11-01T10:00:00",
                "2026-11-01T10:01:00",
                "SUCCESS",
                "FULLY_COMMITTED",
                "v11.3.1",
                "v2",
                "snap_set_hist",
                "snap_rule_hist",
                _sample_events_jsonl("run-hist-001"),
            ),
        )

        # Latest committed successful run
        scon.execute(
            """
            INSERT INTO cp_runs (
                run_id, started_at, finished_at, status, commit_state,
                application_version, schema_version, settings_snapshot_id, rules_snapshot_id,
                execution_log
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                "run-latest-002",
                "2026-11-24T14:00:00",
                "2026-11-24T14:00:10",
                "SUCCESS",
                "FULLY_COMMITTED",
                "v12.0.0",
                "v3",
                "snap_set_latest",
                "snap_rule_latest",
                _sample_events_jsonl("run-latest-002"),
            ),
        )

        # Newer FAILED run (must NOT override latest successful committed run!)
        scon.execute(
            """
            INSERT INTO cp_runs (
                run_id, started_at, finished_at, status, commit_state,
                application_version, schema_version, settings_snapshot_id, rules_snapshot_id,
                execution_log
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                "run-failed-003",
                "2026-11-24T15:00:00",
                "2026-11-24T15:00:02",
                "FAILED",
                "ROLLED_BACK",
                "v12.0.0",
                "v3",
                "snap_set_latest",
                "snap_rule_latest",
                None,
            ),
        )
        scon.commit()
    finally:
        scon.close()

    return duckdb_path, cp_path
