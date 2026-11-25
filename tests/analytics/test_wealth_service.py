"""Unit and integration tests for Wealth Analytics (Gold + Silver asset drill)."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import duckdb
import pytest

from pfetl_analytics_studio.analytics import WealthAnalytics
from pfetl_analytics_studio.data import DuckDBReader
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
    PeriodPreset,
    PeriodRequest,
    PeriodResolver,
)
from tests.fixtures.build_fixture_databases import (
    create_full_atlas_databases,
    seed_overview_gold_tables,
)


def _build_wealth_profile(tmp_path: Path) -> PFETLProfile:
    base_dir = tmp_path / "outputs"
    duck_path, cp_path = create_full_atlas_databases(base_dir)
    seed_overview_gold_tables(duck_path)
    settings_path = tmp_path / "Settings.toml"
    rules_path = tmp_path / "FinancialRules.toml"
    settings_path.write_text(
        f'[database]\ntarget_db_base_path = "{base_dir.as_posix()}"\n'
        f'target_db_name = "{duck_path.name}"\n'
        f'raw_document_store_name = "{cp_path.name}"\n',
        encoding="utf-8",
    )
    rules_path.write_text('DEFAULT_CURRENCY_ID = "INR_INR"\n', encoding="utf-8")
    prof = PFETLProfile.load(settings_path, rules_path).profile
    assert prof is not None
    return prof


def test_wealth_stock_latest_attribution_and_asset_drill(tmp_path: Path) -> None:
    """Verify stock/latest vs flow/sum semantics, attribution identity, and asset drill."""
    prof = _build_wealth_profile(tmp_path)
    con = duckdb.connect(str(prof.duckdb_path))
    try:
        con.execute("DROP TABLE IF EXISTS gold.core_monthly_fact;")
        con.execute(
            """
            CREATE TABLE gold.core_monthly_fact (
                MONTH_START_DATE DATE,
                MONTH_END_DATE DATE,
                Total_Net_Worth DOUBLE,
                Total_Net_Worth_Market DOUBLE,
                Total_Assets DOUBLE,
                Total_Assets_Market DOUBLE,
                Total_Liabilities DOUBLE,
                Liquid_Assets DOUBLE,
                Liquid_Assets_Market DOUBLE,
                Opening_Balance_Asset_Market DOUBLE,
                Closing_Balance_Asset_Market DOUBLE,
                Total_Foreign_Currency_Exposure DOUBLE,
                Foreign_Exposure_Pct DOUBLE,
                Closing_Unrealized_PL DOUBLE,
                Closing_Asset_PnL DOUBLE,
                Closing_Forex_PnL DOUBLE,
                Net_Savings_Flow DOUBLE,
                Market_Return_Flow DOUBLE,
                Attribution_Residual DOUBLE,
                Asset_Market_Delta DOUBLE
            );
            """
        )
        con.execute(
            """
            INSERT INTO gold.core_monthly_fact VALUES
            ('2026-09-01','2026-09-30',1000000.0,1050000.0,1020000.0,1070000.0,20000.0,
             800000.0,850000.0,1000000.0,1070000.0,100000.0,0.10,50000.0,45000.0,5000.0,
             40000.0,25000.0,5000.0,70000.0),
            ('2026-10-01','2026-10-31',1050000.0,1130000.0,1065000.0,1145000.0,15000.0,
             840000.0,920000.0,1070000.0,1145000.0,120000.0,0.1048,80000.0,74000.0,6000.0,
             50000.0,30000.0,-5000.0,75000.0);
            """
        )

        con.execute("DROP TABLE IF EXISTS gold.wealth_asset_breakdown;")
        con.execute(
            """
            CREATE TABLE gold.wealth_asset_breakdown (
                MONTH_START_DATE DATE,
                MONTH_END_DATE DATE,
                ASSET_SUBCATEGORY_ID VARCHAR,
                Opening_Balance DOUBLE,
                Closing_Balance DOUBLE,
                Closing_Balance_Market DOUBLE,
                All_Time_High_Balance DOUBLE,
                Drawdown_From_Peak DOUBLE,
                Closing_Asset_PnL DOUBLE,
                Closing_Forex_PnL DOUBLE,
                "MoM_Balance_Growth_%" DOUBLE,
                "YoY_Balance_Growth_%" DOUBLE,
                "Organic_Yield_%" DOUBLE,
                Cumulative_Net_Savings DOUBLE,
                Months_of_Runway DOUBLE,
                Net_Cashflow_Month DOUBLE,
                Organic_Growth_Value DOUBLE
            );
            """
        )
        con.execute(
            """
            INSERT INTO gold.wealth_asset_breakdown VALUES
            ('2026-09-01','2026-09-30','a_hdfc',480000.0,500000.0,500000.0,500000.0,0.0,0.0,0.0,0.04,0.12,0.02,100000.0,15.0,20000.0,0.0),
            ('2026-10-01','2026-10-31','a_hdfc',500000.0,530000.0,530000.0,530000.0,0.0,0.0,0.0,0.06,0.15,0.03,130000.0,16.0,30000.0,0.0),
            ('2026-10-01','2026-10-31','a_eq',520000.0,535000.0,615000.0,650000.0,-0.0538,74000.0,6000.0,0.03,0.20,0.01,50000.0,18.0,15000.0,10000.0);
            """
        )

        con.execute("DROP TABLE IF EXISTS silver.dim_asset_category;")
        con.execute("CREATE TABLE silver.dim_asset_category (UID VARCHAR, ASSET_GROUP VARCHAR);")
        con.execute(
            "INSERT INTO silver.dim_asset_category VALUES ('g_cash','Cash & Bank'),('g_eq','Equity');"
        )

        con.execute("DROP TABLE IF EXISTS silver.dim_asset_subcategory;")
        con.execute(
            """
            CREATE TABLE silver.dim_asset_subcategory (
                UID VARCHAR,
                ASSET_GROUP_ID VARCHAR,
                ASSET_NAME VARCHAR,
                CURRENCY_ID VARCHAR,
                ledger_role VARCHAR,
                cashflow_activity_type VARCHAR,
                Is_Liquid BOOLEAN,
                is_cash_pool BOOLEAN
            );
            """
        )
        con.execute(
            """
            INSERT INTO silver.dim_asset_subcategory VALUES
            ('a_hdfc','g_cash','HDFC Savings','INR_INR','CASH_POOL','OPERATING',TRUE,TRUE),
            ('a_eq','g_eq','Direct Equity','INR_INR','INVESTMENT','INVESTING',TRUE,FALSE);
            """
        )

        con.execute("DROP TABLE IF EXISTS silver.fact_opening_balances;")
        con.execute(
            "CREATE TABLE silver.fact_opening_balances (ZASSETUID VARCHAR, ZAMOUNTACCOUNT DOUBLE, ZTXDATESTR DATE);"
        )
        con.execute(
            "INSERT INTO silver.fact_opening_balances VALUES ('a_hdfc', 400000.0, '2024-07-01');"
        )
    finally:
        con.close()

    period_2m = PeriodResolver(DuckDBReader(prof)).resolve(
        PeriodRequest(
            preset=PeriodPreset.CUSTOM,
            custom_start=date(2026, 9, 1),
            custom_end=date(2026, 10, 31),
        )
    )
    snap = WealthAnalytics.summary(prof, period_2m)
    assert snap.state == "ready"
    kmap = {k.kpi_id: k for k in snap.kpis}
    # Latest stock balances are never summed across months
    assert kmap["wealth_book_nw"].value == pytest.approx(1050000.0)
    assert kmap["wealth_market_nw"].value == pytest.approx(1130000.0)

    # Attribution flows sum across the 2 months and reconcile to Asset_Market_Delta
    attr = snap.attribution
    assert attr is not None and attr.state == "ready"
    assert attr.net_savings_flow == pytest.approx(90000.0)
    assert attr.market_return_flow == pytest.approx(55000.0)
    assert attr.attribution_residual == pytest.approx(0.0)
    assert attr.asset_market_delta == pytest.approx(145000.0)
    assert attr.reconciles_within_tolerance is True

    # Asset breakdown filters & opening anchor context
    cash_only = WealthAnalytics.asset_breakdown(
        prof,
        period_2m,
        filters=FilterSet(
            filters=(
                FilterSpec(field=FilterField.IS_CASH_POOL, operator=FilterOperator.EQ, value=True),
            )
        ),
    )
    assert len(cash_only.assets) == 1
    assert cash_only.assets[0].asset_name == "HDFC Savings"
    assert cash_only.assets[0].net_cashflow_month == pytest.approx(50000.0)
    assert cash_only.assets[0].opening_anchor_amount == pytest.approx(400000.0)

    hist = WealthAnalytics.asset_history(prof, "a_hdfc", period_2m)
    assert len(hist) == 2


def test_wealth_module_never_touches_bronze_or_ui() -> None:
    """Enforce Gold/Silver boundary and headless purity on Wealth analytics modules."""
    for name in ("wealth.py", "wealth_queries.py"):
        src_path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "pfetl_analytics_studio"
            / "analytics"
            / name
        )
        content = src_path.read_text(encoding="utf-8")
        assert "bronze." not in content.lower()
        tree = ast.parse(content, filename=str(src_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(("nicegui", "personal_finance_etl"))
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(
                    ("nicegui", "pfetl_analytics_studio.ui", "personal_finance_etl")
                )
