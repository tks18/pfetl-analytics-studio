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


def test_performance_history_fx_and_concentration(tmp_path: Path) -> None:
    db_path = tmp_path / "inv_perf.duckdb"
    _create_investment_db(db_path)
    profile = _make_profile(db_path)
    period = _make_period()
    prov = _make_provenance()

    snap = InvestmentAnalytics.performance(
        profile,
        AllocationDimension.CLASS,
        period,
        run_id=prov.run_id,
        provenance=prov,
        cache=QueryCache(),
    )
    assert snap.state == "ready"
    assert len(snap.history) == 1
    assert snap.history[0].xirr == 16.5
    assert snap.history[0].bm_xirr == 12.0
    assert len(snap.kpis) == 12
    assert len(snap.fx_country_rows) == 2
    assert len(snap.fx_currency_rows) == 2


def test_lot_explorer_filtering_pagination_and_detail(tmp_path: Path) -> None:
    from pfetl_analytics_studio.analytics import LotFilter

    db_path = tmp_path / "inv_lots.duckdb"
    _create_investment_db(db_path)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            """
            CREATE TABLE silver.fact_investment_lot_analytics (
                "Lot_ID" VARCHAR,
                "Closing_Date" DATE,
                "ISIN" VARCHAR,
                "CURRENCY_ID" VARCHAR,
                "Buy_Date" DATE,
                "BENCHMARK_ID" VARCHAR,
                "Quantity" DOUBLE,
                "Buy_Value" DOUBLE,
                "Close_Value" DOUBLE,
                "P/L" DOUBLE,
                "Absolute_Return" DOUBLE,
                "XIRR" DOUBLE,
                "BM_XIRR" DOUBLE,
                "Lot_Alpha" DOUBLE,
                "Holding_Type" VARCHAR,
                "Age_Days" BIGINT,
                "Days_To_LTCG" BIGINT,
                "LTCG_Tax_If_Sold" DOUBLE,
                "STCG_Tax_If_Sold" DOUBLE,
                "After_Tax_PL" DOUBLE,
                "Can_Harvest_Loss" BOOLEAN,
                "Harvest_Recommendation" VARCHAR,
                "Is_Lagging_Benchmark" BIGINT
            );
            INSERT INTO silver.fact_investment_lot_analytics VALUES
                ('L1', DATE '2026-10-31', 'INE001', 'INR_INR', DATE '2024-01-01', 'BM1', 10.0, 60000.0, 75000.0, 15000.0, 0.25, 0.18, 0.12, 0.06, 'LTCG', 1034, 0, 1500.0, 0.0, 13500.0, FALSE, 'HOLD', 0),
                ('L2', DATE '2026-10-31', 'INE002', 'INR_INR', DATE '2026-09-01', 'BM1', 20.0, 40000.0, 35000.0, -5000.0, -0.125, -0.25, 0.12, -0.37, 'STCG', 60, 305, 0.0, 0.0, -5000.0, TRUE, 'HARVEST_LOSS', 1),
                ('L3', DATE '2026-10-31', 'INE002', 'INR_INR', DATE '2025-12-01', 'BM1', 5.0, 10000.0, 12000.0, 2000.0, 0.20, 0.22, 0.12, 0.10, 'STCG', 334, 31, 0.0, 400.0, 1600.0, FALSE, 'WAIT_FOR_LTCG', 0);
            """
        )
    finally:
        con.close()

    profile = _make_profile(db_path)
    period = _make_period()
    prov = _make_provenance()

    page1 = InvestmentAnalytics.lot_rows(
        profile,
        LotFilter(),
        period,
        limit=2,
        offset=0,
        run_id=prov.run_id,
        provenance=prov,
        cache=QueryCache(),
    )
    assert page1.state == "ready"
    assert page1.total_count == 3
    assert len(page1.rows) == 2
    assert len(page1.kpis) == 9

    harvest = InvestmentAnalytics.lot_rows(
        profile,
        LotFilter(can_harvest_loss=True),
        period,
        run_id=prov.run_id,
        provenance=prov,
    )
    assert harvest.total_count == 1
    assert harvest.rows[0].lot_id == "L2"

    maturing = InvestmentAnalytics.lot_rows(
        profile,
        LotFilter(holding_type="STCG", days_to_ltcg_max=45),
        period,
        run_id=prov.run_id,
        provenance=prov,
    )
    assert maturing.total_count == 1
    assert maturing.rows[0].lot_id == "L3"

    detail = InvestmentAnalytics.lot_detail(profile, "L1", date(2026, 10, 31))
    assert detail is not None
    assert len(detail.sections) == 9
    assert detail.raw_columns["Lot_ID"] == "L1"

    missing = InvestmentAnalytics.lot_rows(
        profile,
        LotFilter(closing_date=date(2026, 10, 15)),
        period,
        run_id=prov.run_id,
        provenance=prov,
    )
    assert missing.state == "empty"
    assert date(2026, 10, 31) in missing.nearest_dates


def test_realized_lineage_reconciliation_and_market_evidence(tmp_path: Path) -> None:
    from pfetl_analytics_studio.analytics import RealizedEventFilter

    db_path = tmp_path / "inv_p16.duckdb"
    _create_investment_db(db_path)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            """
            CREATE TABLE silver.fact_investment_purchases (
                "Purchase_ID" VARCHAR, "Date" DATE, "ISIN" VARCHAR, "Quantity" DOUBLE, "Price" DOUBLE, "Value" DOUBLE
            );
            INSERT INTO silver.fact_investment_purchases VALUES ('P1', DATE '2024-01-01', 'INE001', 10.0, 6000.0, 60000.0);

            CREATE TABLE silver.fact_investment_sales (
                "Sale_ID" VARCHAR, "Date" DATE, "ISIN" VARCHAR, "Quantity" DOUBLE, "Sell_Price" DOUBLE, "Sell_Value" DOUBLE
            );
            INSERT INTO silver.fact_investment_sales VALUES ('S1', DATE '2026-10-15', 'INE001', 4.0, 8000.0, 32000.0);

            CREATE TABLE silver.fact_investment_lot_analytics (
                "Lot_ID" VARCHAR, "Closing_Date" DATE, "ISIN" VARCHAR
            );
            INSERT INTO silver.fact_investment_lot_analytics VALUES ('L1', DATE '2026-10-31', 'INE001');

            CREATE TABLE silver.fact_investment_realized_events (
                "Realized_Event_ID" VARCHAR, "Sale_ID" VARCHAR, "Lot_ID" VARCHAR, "Purchase_ID" VARCHAR,
                "ISIN" VARCHAR, "Acquisition_Date" DATE, "Disposal_Date" DATE, "FY" VARCHAR,
                "Quantity_Disposed" DOUBLE, "Acquisition_Price" DOUBLE, "Disposed_Cost_Basis" DOUBLE,
                "Sale_Price" DOUBLE, "Sale_Proceeds" DOUBLE, "Realized_Gain_Loss" DOUBLE,
                "Holding_Type" VARCHAR, "Tax_Type" VARCHAR, "Tax_Subtype" VARCHAR,
                "Currency_ID" VARCHAR, "Asset_PnL" DOUBLE, "Forex_PnL" DOUBLE,
                "Applied_Rate" DOUBLE, "Estimated_Tax" DOUBLE
            );
            INSERT INTO silver.fact_investment_realized_events VALUES
                ('RE1', 'S1', 'L1', 'P1', 'INE001', DATE '2024-01-01', DATE '2026-10-15', '2026-27', 4.0, 6000.0, 24000.0, 8000.0, 32000.0, 8000.0, 'LTCG', 'EQUITY', 'LISTED', 'INR_INR', 8000.0, 0.0, 0.125, 1000.0),
                ('RE2', 'S2', 'L_MISS', 'P_MISS', 'INE002', DATE '2026-01-01', DATE '2026-10-20', '2026-27', 2.0, 2000.0, 4000.0, 1800.0, 3600.0, -400.0, 'STCG', 'EQUITY', 'LISTED', 'INR_INR', -400.0, 0.0, 0.20, 0.0);

            CREATE TABLE silver.fact_investment_reconciliation_events (
                "Reconciliation_Group_ID" VARCHAR, "Reconciliation_Event_ID" VARCHAR, "Run_ID" VARCHAR,
                "ISIN" VARCHAR, "Reconciliation_Date" DATE, "Lot_ID" VARCHAR, "Purchase_ID" VARCHAR,
                "Adjustment_Type" VARCHAR, "Reason" VARCHAR,
                "Broker_Quantity" DOUBLE, "Reconstructed_Quantity" DOUBLE, "Quantity_Adjustment" DOUBLE,
                "Broker_Cost_Basis" DOUBLE, "Reconstructed_Cost_Basis" DOUBLE, "Cost_Basis_Adjustment" DOUBLE,
                "Original_Unit_Cost" DOUBLE, "Adjusted_Unit_Cost" DOUBLE
            );
            INSERT INTO silver.fact_investment_reconciliation_events VALUES
                ('RG1', 'REC1', 'R1', 'INE001', DATE '2026-10-31', 'L1', 'P1', 'COST_BASIS_ADJUSTMENT', 'Broker mismatch', 10.0, 10.0, 0.0, 60500.0, 60000.0, 500.0, 6000.0, 6050.0);

            CREATE TABLE silver.fact_investment_market_data (
                "Date" DATE, "ISIN" VARCHAR, "Quantity" DOUBLE, "Closing_Price" DOUBLE, "Buy_Price" DOUBLE,
                "Closing_Value" DOUBLE, "Buy_Value" DOUBLE, "FX_Rate" DOUBLE,
                "Data_Provider" VARCHAR, "Extraction_Time" VARCHAR, "Is_Closure_Gap" BOOLEAN, "Is_Imputed" BOOLEAN
            );
            INSERT INTO silver.fact_investment_market_data VALUES
                (DATE '2026-10-01', 'INE001', 10.0, 3400.0, 3000.0, 34000.0, 30000.0, 1.0, 'yfinance', '2026-10-01T18:00:00Z', FALSE, FALSE),
                (DATE '2026-10-02', 'INE001', 10.0, 3400.0, 3000.0, 34000.0, 30000.0, 1.0, 'yfinance', '2026-10-02T18:00:00Z', TRUE, TRUE);

            CREATE TABLE silver.fact_investment_benchmark_data (
                "Date" DATE, "ID" VARCHAR, "Benchmark_Name" VARCHAR, "yF_Ticker" VARCHAR,
                "Close" DOUBLE, "Data_Provider" VARCHAR, "Extraction_Time" VARCHAR, "Is_Closure_Gap" BOOLEAN, "Is_Imputed" BOOLEAN
            );
            INSERT INTO silver.fact_investment_benchmark_data VALUES
                (DATE '2026-10-01', 'BM1', 'NIFTY 50', '^NSEI', 25000.0, 'yfinance', '2026-10-01T18:00:00Z', FALSE, FALSE);

            CREATE TABLE silver.fact_currency_fx_rates (
                "Date" DATE, "Currency_ID" VARCHAR, "Currency_Code" VARCHAR, "Target_Currency_Code" VARCHAR,
                "yF_Ticker" VARCHAR, "FX_Rate" DOUBLE, "Data_Provider" VARCHAR, "Extraction_Time" VARCHAR,
                "Is_Closure_Gap" BOOLEAN, "Is_Imputed" BOOLEAN
            );
            INSERT INTO silver.fact_currency_fx_rates VALUES
                (DATE '2026-10-01', 'INR_USD', 'USD', 'INR', 'INR=X', 84.10, 'yfinance', '2026-10-01T18:00:00Z', FALSE, FALSE);
            """
        )
    finally:
        con.close()

    profile = _make_profile(db_path)
    period = _make_period()
    prov = _make_provenance()

    snap = InvestmentAnalytics.realized_and_reconciliation(
        profile, RealizedEventFilter(), run_id=prov.run_id, provenance=prov, cache=QueryCache()
    )
    assert snap.state == "ready"
    assert len(snap.realized_rows) == 2
    assert len(snap.reconciliation_groups) == 1
    assert snap.reconciliation_groups[0].cost_basis_adjustment == 500.0

    ok_trace = InvestmentAnalytics.trace_lineage(profile, "RE1")
    assert ok_trace is not None
    assert ok_trace.missing_links == ()
    assert ok_trace.sale_record is not None and ok_trace.purchase_record is not None

    broken_trace = InvestmentAnalytics.trace_lineage(profile, "RE2")
    assert broken_trace is not None
    assert "Purchase_ID:P_MISS" in broken_trace.missing_links

    ev = InvestmentAnalytics.market_evidence(
        profile,
        period,
        isin="INE001",
        benchmark_id="BM1",
        currency_id="INR_USD",
        run_id=prov.run_id,
        provenance=prov,
        cache=QueryCache(),
    )
    assert ev.state == "ready"
    assert len(ev.market_points) == 2
    assert ev.market_points[1].is_imputed is True
    assert ev.market_points[1].is_closure_gap is True
    assert len(ev.benchmark_points) == 1
    assert len(ev.fx_points) == 1
