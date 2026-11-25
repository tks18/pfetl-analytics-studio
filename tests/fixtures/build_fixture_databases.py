"""Deterministic test fixture database generator for PFETL Analytics Studio."""

from __future__ import annotations

import calendar
import json
import sqlite3
import zlib
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from pfetl_analytics_studio.data.catalog import SURFACE_ATLAS, SurfaceLayer


def create_test_databases(target_dir: Path) -> tuple[Path, Path]:
    """Generate baseline DuckDB and SQLite Control Plane fixtures for reader unit tests."""
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


def seed_calendar_and_gold_months(
    duckdb_path: Path,
    start: date = date(2024, 4, 1),
    end: date = date(2026, 12, 31),
    as_of_date: date = date(2026, 11, 24),
    gold_cutoff_month: date = date(2026, 11, 1),
) -> None:
    """Populate realistic typed `silver.dim_calendar` and `gold.core_monthly_fact` rows."""
    dcon = duckdb.connect(database=str(duckdb_path))
    try:
        dcon.execute("DROP TABLE IF EXISTS silver.dim_calendar;")
        dcon.execute(
            """
            CREATE TABLE silver.dim_calendar (
                "Date" DATE,
                "Day" BIGINT,
                "Month" BIGINT,
                "Month_Year" VARCHAR,
                "Start_of_Month" DATE,
                "End_of_Month" DATE,
                "Quarter" BIGINT,
                "Quarter_Year" VARCHAR,
                "Start_of_Quarter" DATE,
                "End_of_Quarter" DATE,
                "Year" BIGINT,
                "Financial_Year" VARCHAR,
                "FY_Year" BIGINT,
                "FY_Month" BIGINT,
                "FY_Quarter" BIGINT,
                "FY_Quarter_Year" VARCHAR,
                "FY_Start_of_Quarter" DATE,
                "FY_End_of_Quarter" DATE,
                "Is_Future_Date" BOOLEAN
            );
            """
        )

        cal_rows: list[tuple[Any, ...]] = []
        month_bounds: list[tuple[date, date]] = []
        seen_months: set[date] = set()
        curr = start
        while curr <= end:
            last_day = calendar.monthrange(curr.year, curr.month)[1]
            m_start = date(curr.year, curr.month, 1)
            m_end = date(curr.year, curr.month, last_day)
            if m_start not in seen_months:
                seen_months.add(m_start)
                month_bounds.append((m_start, m_end))

            cal_q = (curr.month - 1) // 3 + 1
            q_start_month = (cal_q - 1) * 3 + 1
            q_end_month = q_start_month + 2
            q_start = date(curr.year, q_start_month, 1)
            q_end = date(curr.year, q_end_month, calendar.monthrange(curr.year, q_end_month)[1])
            q_year_label = f"Q{cal_q}-{curr.year}"

            fy_start_year = curr.year if curr.month >= 4 else curr.year - 1
            fy_str = f"{fy_start_year}-{(fy_start_year + 1) % 100:02d}"
            fy_month = curr.month - 3 if curr.month >= 4 else curr.month + 9
            fy_q = (fy_month - 1) // 3 + 1
            fy_q_year = f"Q{fy_q}-{fy_str}"

            cal_rows.append(
                (
                    curr,
                    curr.day,
                    curr.month,
                    curr.strftime("%b %Y"),
                    m_start,
                    m_end,
                    cal_q,
                    q_year_label,
                    q_start,
                    q_end,
                    curr.year,
                    fy_str,
                    fy_start_year,
                    fy_month,
                    fy_q,
                    fy_q_year,
                    q_start,
                    q_end,
                    curr > as_of_date,
                )
            )
            curr += timedelta(days=1)

        dcon.executemany(
            "INSERT INTO silver.dim_calendar VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            cal_rows,
        )

        dcon.execute("DROP TABLE IF EXISTS gold.core_monthly_fact;")
        dcon.execute(
            """
            CREATE TABLE gold.core_monthly_fact (
                "MONTH_START_DATE" DATE,
                "MONTH_END_DATE" DATE,
                "Total_Income" DOUBLE,
                "Total_Expense" DOUBLE,
                "Total_Core_Expense" DOUBLE,
                "Net_Cashflow_Month" DOUBLE,
                "Total_Net_Worth" DOUBLE
            );
            """
        )
        gold_rows: list[tuple[Any, ...]] = []
        for idx, (m_start, m_end) in enumerate(month_bounds):
            if m_start > gold_cutoff_month:
                continue
            income = 100000.0 + idx * 1000.0
            expense = 40000.0 + idx * 500.0
            gold_rows.append(
                (
                    m_start,
                    m_end,
                    income,
                    expense,
                    expense * 0.7,
                    income - expense,
                    1000000.0 + idx * 60000.0,
                )
            )
        dcon.executemany(
            "INSERT INTO gold.core_monthly_fact VALUES (?, ?, ?, ?, ?, ?, ?);",
            gold_rows,
        )
    finally:
        dcon.close()


def seed_overview_gold_tables(
    duckdb_path: Path,
    *,
    as_of_date: date = date(2026, 11, 24),
    include_fire: bool = True,
    include_tax_fy_2026: bool = True,
    tax_incomplete: bool = True,
) -> None:
    """Populate typed Gold tables & calendar across 14 months for Overview tests."""
    seed_calendar_and_gold_months(
        duckdb_path,
        start=date(2025, 4, 1),
        end=date(2026, 12, 31),
        as_of_date=as_of_date,
        gold_cutoff_month=date(2026, 10, 1),
    )

    months: list[tuple[date, date]] = []
    curr = date(2025, 4, 1)
    while curr <= date(2026, 10, 1):
        last_day = calendar.monthrange(curr.year, curr.month)[1]
        months.append((curr, date(curr.year, curr.month, last_day)))
        next_m = curr.month + 1
        curr = date(curr.year + (1 if next_m > 12 else 0), 1 if next_m > 12 else next_m, 1)

    dcon = duckdb.connect(database=str(duckdb_path))
    try:
        dcon.execute("DROP TABLE IF EXISTS gold.core_monthly_fact;")
        dcon.execute(
            """
            CREATE TABLE gold.core_monthly_fact (
                "MONTH_START_DATE" DATE,
                "MONTH_END_DATE" DATE,
                "Total_Income" DOUBLE,
                "Total_Expense" DOUBLE,
                "Total_Core_Expense" DOUBLE,
                "Net_Cashflow_Month" DOUBLE,
                "Total_Assets_Market" DOUBLE,
                "Total_Liabilities" DOUBLE,
                "Liquid_Assets_Market" DOUBLE,
                "Total_Net_Worth" DOUBLE,
                "Total_Net_Worth_Market" DOUBLE,
                "Net_Savings_Flow" DOUBLE,
                "Market_Return_Flow" DOUBLE,
                "Attribution_Residual" DOUBLE,
                "Inflation_Adjusted_Core_Expense" DOUBLE
            );
            """
        )

        dcon.execute("DROP TABLE IF EXISTS gold.cashflow_efficiency_analytics;")
        dcon.execute(
            """
            CREATE TABLE gold.cashflow_efficiency_analytics (
                "MONTH_START_DATE" DATE,
                "MONTH_END_DATE" DATE,
                "YEAR_MONTH" VARCHAR,
                "Active_Income" DOUBLE,
                "Passive_Income" DOUBLE,
                "Interest_Income" DOUBLE,
                "Dividend_Income" DOUBLE,
                "Core_Expense" DOUBLE,
                "NonCore_Expense" DOUBLE,
                "Total_Investment_Deployed" DOUBLE,
                "Total_Investment_Redeemed" DOUBLE,
                "Redemption_Gain_Loss_Value" DOUBLE,
                "Net_Investment_Flow" DOUBLE,
                "Net_Surplus_After_Invest" DOUBLE,
                "Savings_Rate_Pct" DOUBLE,
                "Investment_Rate_Pct" DOUBLE,
                "Passive_Income_Core_Expense_Coverage_Ratio" DOUBLE,
                "Active_Income_Dependency_Pct" DOUBLE,
                "Liquidity_Ratio_Months" DOUBLE,
                "Debt_to_Asset_Ratio_Pct" DOUBLE
            );
            """
        )

        dcon.execute("DROP TABLE IF EXISTS gold.cashflow_income_breakdown;")
        dcon.execute(
            """
            CREATE TABLE gold.cashflow_income_breakdown (
                "MONTH_START_DATE" DATE,
                "MONTH_END_DATE" DATE,
                "YEAR_MONTH" VARCHAR,
                "CATEGORY_NAME" VARCHAR,
                "CATEGORY_GROUPS" VARCHAR,
                "Is_Active_Income" BOOLEAN,
                "Is_Passive_Income" BOOLEAN,
                "Is_Non_Cash_Income" BOOLEAN,
                "Total_Monthly_Income" DOUBLE
            );
            """
        )

        dcon.execute("DROP TABLE IF EXISTS gold.cashflow_expense_breakdown;")
        dcon.execute(
            """
            CREATE TABLE gold.cashflow_expense_breakdown (
                "MONTH_START_DATE" DATE,
                "MONTH_END_DATE" DATE,
                "YEAR_MONTH" VARCHAR,
                "CATEGORY_NAME" VARCHAR,
                "CATEGORY_GROUPS" VARCHAR,
                "Spend_Type" VARCHAR,
                "Is_Core_Expense" BOOLEAN,
                "Total_Monthly_Spend" DOUBLE
            );
            """
        )

        dcon.execute("DROP TABLE IF EXISTS gold.investment_by_class;")
        dcon.execute(
            """
            CREATE TABLE gold.investment_by_class (
                "Closing_Date" DATE,
                "INSTRUMENT_CLASS" VARCHAR,
                "Total_Invested_Value" DOUBLE,
                "Total_Current_Value" DOUBLE,
                "Unrealized_PL" DOUBLE,
                "Weight" DOUBLE,
                "XIRR" DOUBLE,
                "BM_XIRR" DOUBLE,
                "Active_Return" DOUBLE
            );
            """
        )

        dcon.execute("DROP TABLE IF EXISTS gold.investment_by_portfolio;")
        dcon.execute(
            """
            CREATE TABLE gold.investment_by_portfolio (
                "Closing_Date" DATE,
                "Total_Invested_Value" DOUBLE,
                "Total_Current_Value" DOUBLE,
                "Unrealized_PL" DOUBLE,
                "XIRR" DOUBLE,
                "BM_XIRR" DOUBLE,
                "Active_Return" DOUBLE,
                "HHI_Score" DOUBLE,
                "Effective_Holdings_Count" DOUBLE
            );
            """
        )

        dcon.execute("DROP TABLE IF EXISTS gold.wealth_fire_analytics;")
        dcon.execute(
            """
            CREATE TABLE gold.wealth_fire_analytics (
                "MONTH_START_DATE" DATE,
                "MONTH_END_DATE" DATE,
                "YEAR_MONTH" VARCHAR,
                "Projected_FI_Date_P50" DATE,
                "Target_FI_Today" DOUBLE,
                "FI_Gap" DOUBLE,
                "Current_FI_Coverage_Pct" DOUBLE,
                "Probability_Of_Success_Pct" DOUBLE,
                "Years_To_FI_P50" DOUBLE
            );
            """
        )

        dcon.execute("DROP TABLE IF EXISTS gold.tax_financial_year_summary;")
        dcon.execute(
            """
            CREATE TABLE gold.tax_financial_year_summary (
                "FY" VARCHAR,
                "Indicative_Net_Tax_Exposure" DOUBLE,
                "Indicative_Unrealized_Tax_Liability" DOUBLE,
                "Indicative_Harvestable_Tax_Savings" DOUBLE,
                "Investment_Tax_Estimate_Complete" BOOLEAN,
                "Investment_Tax_Estimate_Missing_Count" BIGINT,
                "Ledger_Tax_Estimate_Complete" BOOLEAN,
                "Ledger_Tax_Estimate_Missing_Count" BIGINT,
                "Total_Event_Count" BIGINT
            );
            """
        )

        for idx, (m_start, m_end) in enumerate(months):
            inc = 200000.0 + idx * 5000.0
            exp = 80000.0 + idx * 1000.0
            core_exp = 55000.0
            net_cf = inc - exp
            nw_mkt = 10000000.0 + idx * 250000.0
            ym = f"{m_start.year:04d}-{m_start.month:02d}"

            dcon.execute(
                "INSERT INTO gold.core_monthly_fact VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    m_start,
                    m_end,
                    inc,
                    exp,
                    core_exp,
                    net_cf,
                    nw_mkt + 100000.0,
                    100000.0,
                    3000000.0 + idx * 50000.0,
                    nw_mkt - 400000.0,
                    nw_mkt,
                    net_cf,
                    150000.0,
                    5000.0,
                    core_exp,
                ),
            )
            dcon.execute(
                "INSERT INTO gold.cashflow_efficiency_analytics VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    m_start,
                    m_end,
                    ym,
                    inc * 0.90,
                    inc * 0.06,
                    inc * 0.05,
                    inc * 0.01,
                    core_exp,
                    exp - core_exp,
                    90000.0 + idx * 1000.0,
                    15000.0,
                    1200.0,
                    75000.0 + idx * 1000.0,
                    net_cf - (75000.0 + idx * 1000.0),
                    net_cf / inc,
                    0.45,
                    0.18 + idx * 0.005,
                    0.92,
                    54.5 + idx * 0.5,
                    0.01,
                ),
            )
            dcon.execute(
                "INSERT INTO gold.cashflow_income_breakdown VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    m_start,
                    m_end,
                    ym,
                    "Salary",
                    "Income from Salary",
                    True,
                    False,
                    False,
                    inc * 0.90,
                ),
            )
            dcon.execute(
                "INSERT INTO gold.cashflow_income_breakdown VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    m_start,
                    m_end,
                    ym,
                    "Dividends",
                    "Income from Other Sources",
                    False,
                    True,
                    False,
                    inc * 0.06,
                ),
            )
            dcon.execute(
                "INSERT INTO gold.cashflow_expense_breakdown VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
                (m_start, m_end, ym, "Rents", "Rents", "Fixed", True, core_exp),
            )
            dcon.execute(
                "INSERT INTO gold.cashflow_expense_breakdown VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    m_start,
                    m_end,
                    ym,
                    "Entertainment",
                    "Entertainment",
                    "Variable",
                    False,
                    exp - core_exp,
                ),
            )
            inv_cost = 6000000.0 + idx * 120000.0
            inv_cur = 7200000.0 + idx * 180000.0
            dcon.execute(
                "INSERT INTO gold.investment_by_portfolio VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    m_end,
                    inv_cost,
                    inv_cur,
                    inv_cur - inv_cost,
                    0.125 + idx * 0.001,
                    0.095,
                    0.030 + idx * 0.001,
                    0.145,
                    6.9,
                ),
            )
            if m_end == date(2026, 10, 31):
                dcon.execute(
                    "INSERT INTO gold.investment_by_class VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (
                        m_end,
                        "Mutual Funds",
                        inv_cost * 0.75,
                        inv_cur * 0.78,
                        (inv_cur * 0.78) - (inv_cost * 0.75),
                        0.78,
                        0.16,
                        0.11,
                        0.05,
                    ),
                )
                dcon.execute(
                    "INSERT INTO gold.investment_by_class VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (
                        m_end,
                        "US Stocks",
                        inv_cost * 0.25,
                        inv_cur * 0.22,
                        (inv_cur * 0.22) - (inv_cost * 0.25),
                        0.22,
                        0.08,
                        0.10,
                        -0.02,
                    ),
                )
            if include_fire:
                dcon.execute(
                    "INSERT INTO gold.wealth_fire_analytics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (
                        m_start,
                        m_end,
                        ym,
                        date(2033, 9, 1),
                        30000000.0,
                        30000000.0 - nw_mkt,
                        nw_mkt / 30000000.0,
                        0.91 + idx * 0.002,
                        6.8,
                    ),
                )

        # Fresher mid-month close beyond 2026-10-31 verifying dual period-vs-latest reporting
        dcon.execute(
            "INSERT INTO gold.investment_by_portfolio VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (
                date(2026, 11, 15),
                8200000.0,
                10800000.0,
                2600000.0,
                0.185,
                0.115,
                0.070,
                0.140,
                7.1,
            ),
        )

        if include_tax_fy_2026:
            dcon.execute(
                "INSERT INTO gold.tax_financial_year_summary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    "2026-27",
                    185000.0,
                    210000.0,
                    25000.0,
                    not tax_incomplete,
                    2 if tax_incomplete else 0,
                    True,
                    0,
                    142,
                ),
            )
        dcon.execute(
            "INSERT INTO gold.tax_financial_year_summary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
            ("2025-26", 142000.0, 150000.0, 8000.0, True, 0, True, 0, 210),
        )
    finally:
        dcon.close()
