"""Tests for InvestmentAnalytics service, 9-mart allocation, and ISIN detail."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from pfetl_analytics_studio.analytics import (
    AllocationDimension,
    InvestmentAnalytics,
)
from pfetl_analytics_studio.data import QueryCache, RunProvenance
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterSet,
    FilterSpec,
    PeriodContext,
    PeriodPreset,
)


def _create_investment_db(db_path: Path) -> None:
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA gold;")
        con.execute("CREATE SCHEMA silver;")

        con.execute(
            """
            CREATE TABLE gold.investment_by_portfolio (
                "Closing_Date" DATE,
                CURRENCY_ID VARCHAR,
                "Total_Invested_Value" DOUBLE,
                "Total_Current_Value" DOUBLE,
                "Unrealized_PL" DOUBLE,
                ASSET_PNL DOUBLE,
                FOREX_PNL DOUBLE,
                ABSOLUTE_RETURN DOUBLE,
                XIRR DOUBLE,
                BM_XIRR DOUBLE,
                ACTIVE_RETURN DOUBLE,
                MAX_DRAWDOWN DOUBLE,
                HHI_SCORE DOUBLE
            );
            INSERT INTO gold.investment_by_portfolio VALUES (
                DATE '2026-10-31', 'INR_INR',
                100000.0, 125000.0, 25000.0, 25000.0, 0.0, 25.0,
                16.5, 12.0, 4.5, -8.2, 2100.0
            );
            """
        )

        con.execute(
            """
            CREATE TABLE gold.investment_portfolio_summary (
                "Max_Closing_Date" DATE,
                ISIN VARCHAR,
                INSTRUMENT_NAME VARCHAR,
                CURRENCY_ID VARCHAR,
                INSTRUMENT_CLASS VARCHAR,
                INSTRUMENT_TYPE VARCHAR,
                INSTRUMENT_SUBTYPE VARCHAR,
                SECTOR VARCHAR,
                "ISIN_Market_Value" DOUBLE,
                "ISIN_Book_Value" DOUBLE,
                "ISIN_Unrealized_PnL" DOUBLE,
                ASSET_PNL DOUBLE,
                FOREX_PNL DOUBLE,
                XIRR_LOCAL DOUBLE,
                BM_XIRR DOUBLE,
                ACTIVE_RETURN DOUBLE,
                ISIN_WEIGHT DOUBLE,
                CLASS_WEIGHT DOUBLE,
                CLASS_TARGET_WEIGHT DOUBLE,
                CLASS_DRIFT DOUBLE,
                REBALANCE_REQUIRED BOOLEAN
            );
            INSERT INTO gold.investment_portfolio_summary VALUES
                (DATE '2026-10-31', 'INE001', 'Tata Consultancy Services', 'INR_INR', 'Equity', 'Stock', 'Large Cap', 'IT', 75000.0, 60000.0, 15000.0, 15000.0, 0.0, 18.0, 12.0, 6.0, 60.0, 100.0, 100.0, 0.0, false),
                (DATE '2026-10-31', 'INE002', 'HDFC Bank', 'INR_INR', 'Equity', 'Stock', 'Large Cap', 'Financials', 50000.0, 40000.0, 10000.0, 10000.0, 0.0, 14.0, 12.0, 2.0, 40.0, 100.0, 100.0, 0.0, false);
            """
        )

        con.execute(
            """
            CREATE TABLE silver.dim_instrument_master (
                ISIN VARCHAR,
                TICKER VARCHAR,
                INSTRUMENT_NAME VARCHAR,
                INSTRUMENT_CLASS VARCHAR,
                SECTOR VARCHAR,
                COUNTRY VARCHAR
            );
            INSERT INTO silver.dim_instrument_master VALUES
                ('INE001', 'TCS', 'Tata Consultancy Services', 'Equity', 'IT', 'India'),
                ('INE002', 'HDFC', 'HDFC Bank', 'Equity', 'Financials', 'India');
            """
        )

        for mart_name, bucket_col, val1, val2 in (
            ("investment_by_class", "INSTRUMENT_CLASS", "Equity", "Debt"),
            ("investment_by_subtype", "INSTRUMENT_SUBTYPE", "Large Cap", "Mid Cap"),
            ("investment_by_instrument_type", "INSTRUMENT_TYPE", "Stock", "ETF"),
            ("investment_by_sector", "SECTOR", "IT", "Financials"),
            ("investment_by_industry", "INDUSTRY", "Software", "Banking"),
            ("investment_by_geo", "GEO", "Domestic", "International"),
            ("investment_by_country", "COUNTRY", "India", "USA"),
            ("investment_by_currency", "CURRENCY_ID", "INR_INR", "INR_USD"),
            ("investment_by_isin", "ISIN", "INE001", "INE002"),
        ):
            extra_col = "" if bucket_col == "CURRENCY_ID" else "CURRENCY_ID VARCHAR,"
            extra_val = "" if bucket_col == "CURRENCY_ID" else "'INR_INR',"
            con.execute(
                f"""
                CREATE TABLE gold.{mart_name} (
                    "Closing_Date" DATE,
                    {bucket_col} VARCHAR,
                    {extra_col}
                    "Total_Invested_Value" DOUBLE,
                    "Total_Current_Value" DOUBLE,
                    "Unrealized_PL" DOUBLE,
                    XIRR DOUBLE,
                    WEIGHT DOUBLE
                );
                INSERT INTO gold.{mart_name} VALUES
                    (DATE '2026-10-31', '{val1}', {extra_val} 60000.0, 75000.0, 15000.0, 18.0, 60.0),
                    (DATE '2026-10-31', '{val2}', {extra_val} 40000.0, 50000.0, 10000.0, 14.0, 40.0);
                """
            )
    finally:
        con.close()


def _make_profile(db_path: Path) -> PFETLProfile:
    return PFETLProfile(
        profile_id="test-profile",
        label="Test Profile",
        settings_path=db_path.parent / "settings.toml",
        rules_path=db_path.parent / "rules.toml",
        target_db_base_path=db_path.parent,
        duckdb_path=db_path,
        control_plane_path=db_path.parent / "control.sqlite",
        lock_path=db_path.parent / "pipeline.lock",
    )


def _make_period() -> PeriodContext:
    return PeriodContext(
        preset=PeriodPreset.LATEST_COMPLETE_MONTH,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 10, 31),
        label="2026-01-01 to 2026-10-31",
    )


def _make_provenance() -> RunProvenance:
    return RunProvenance(
        run_id="run-test-1",
        status="SUCCESS",
        started_at=None,
        finished_at=None,
        commit_state="committed",
        application_version="1.0.0",
        schema_version="1.0.0",
        settings_snapshot_id="s1",
        rules_snapshot_id="r1",
        settings_payload={},
        rules_payload={},
        has_settings_snapshot=True,
        has_rules_snapshot=True,
        has_execution_log=True,
        is_complete=True,
    )


def test_portfolio_snapshot_and_kpis(tmp_path: Path) -> None:
    db_path = tmp_path / "inv.duckdb"
    _create_investment_db(db_path)
    profile = _make_profile(db_path)
    period = _make_period()
    prov = _make_provenance()

    snap = InvestmentAnalytics.portfolio_snapshot(
        profile,
        period,
        run_id=prov.run_id,
        provenance=prov,
        cache=QueryCache(),
    )
    assert snap.state == "ready"
    assert snap.total_invested == 100000.0
    assert snap.total_current == 125000.0
    assert len(snap.holdings) == 2
    assert len(snap.kpis) >= 4


def test_allocation_all_9_dimensions(tmp_path: Path) -> None:
    db_path = tmp_path / "inv.duckdb"
    _create_investment_db(db_path)
    profile = _make_profile(db_path)
    period = _make_period()
    prov = _make_provenance()

    for dim in AllocationDimension:
        alloc = InvestmentAnalytics.allocation(
            profile,
            dim,
            period,
            run_id=prov.run_id,
            provenance=prov,
            cache=QueryCache(),
        )
        assert alloc.dimension == dim
        assert len(alloc.rows) == 2
        assert alloc.rows[0].current_value >= alloc.rows[1].current_value


def test_instrument_detail_and_currency_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "inv.duckdb"
    _create_investment_db(db_path)
    profile = _make_profile(db_path)
    period = _make_period()
    prov = _make_provenance()
    filters = FilterSet(filters=(FilterSpec(field=FilterField.CURRENCY_ID, value="INR_INR"),))

    detail = InvestmentAnalytics.instrument_detail(
        profile,
        "INE001",
        period,
    )
    assert detail is not None
    assert detail.master_metadata.get("TICKER") == "TCS"
    assert detail.history[0].market_value == 75000.0

    alloc_cur = InvestmentAnalytics.allocation(
        profile,
        AllocationDimension.CURRENCY,
        period,
        run_id=prov.run_id,
        provenance=prov,
        cache=QueryCache(),
    )
    assert len(alloc_cur.rows) == 2
    assert len(filters.filters) == 1
