"""Deterministic test fixture database generator for PFETL Analytics Studio."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb


def create_test_databases(target_dir: Path) -> tuple[Path, Path]:
    """Generate minimal deterministic DuckDB and SQLite Control Plane test databases.

    Parameters
    ----------
    target_dir : Path
        Directory where test databases should be generated.

    Returns
    -------
    tuple[Path, Path]
        Paths to generated (duckdb_path, control_plane_path).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    duckdb_path = target_dir / "Personal_Finance_DB.duckdb"
    control_plane_path = target_dir / "Personal_Finance_DB_Raw_Store.sqlite"

    # 1. Initialize DuckDB warehouse
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

    # 2. Initialize Control Plane SQLite
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

