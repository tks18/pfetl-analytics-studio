"""Unit and integration tests for Household & Income Analytics (Gold + Silver drill)."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import duckdb
import pytest

from pfetl_analytics_studio.analytics import HouseholdAnalytics
from pfetl_analytics_studio.data import DuckDBReader, PFETLCatalog
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import PeriodPreset, PeriodRequest, PeriodResolver
from tests.fixtures.build_fixture_databases import (
    create_full_atlas_databases,
    seed_overview_gold_tables,
)


def _build_household_profile(tmp_path: Path) -> PFETLProfile:
    """Create isolated DuckDB + CP profile seeded with rich Income Gold & Silver rows."""
    base_dir = tmp_path / "outputs"
    duck_path, cp_path = create_full_atlas_databases(base_dir)
    seed_overview_gold_tables(duck_path)
    con = duckdb.connect(str(duck_path))
    try:
        con.execute("DROP TABLE IF EXISTS gold.cashflow_income_breakdown;")
        con.execute(
            """
            CREATE TABLE gold.cashflow_income_breakdown (
                MONTH_START_DATE DATE,
                MONTH_END_DATE DATE,
                YEAR_MONTH VARCHAR,
                CATEGORY_ID VARCHAR,
                CATEGORY_NAME VARCHAR,
                CATEGORY_GROUPS VARCHAR,
                Is_Active_Income BOOLEAN,
                Is_Passive_Income BOOLEAN,
                Is_Dividend_Income BOOLEAN,
                Is_Interest_Income BOOLEAN,
                Is_Non_Cash_Income BOOLEAN,
                Total_Monthly_Income DOUBLE,
                Trailing_3M_Avg_Income DOUBLE,
                Cumulative_YTD_Income DOUBLE
            );
            """
        )
        con.execute(
            """
            INSERT INTO gold.cashflow_income_breakdown VALUES
            ('2026-09-01','2026-09-30','2026-09','c_basic','Basic','Income from Salary',TRUE,FALSE,FALSE,FALSE,FALSE,80000.0,80000.0,480000.0),
            ('2026-09-01','2026-09-30','2026-09','c_tds','TDS','Income from Salary',TRUE,FALSE,FALSE,FALSE,FALSE,-15000.0,-15000.0,-90000.0),
            ('2026-09-01','2026-09-30','2026-09','c_div','Dividends','Income from Other Sources',FALSE,TRUE,TRUE,FALSE,FALSE,2000.0,1500.0,10000.0),
            ('2026-10-01','2026-10-31','2026-10','c_basic','Basic','Income from Salary',TRUE,FALSE,FALSE,FALSE,FALSE,80000.0,80000.0,560000.0),
            ('2026-10-01','2026-10-31','2026-10','c_tds','TDS','Income from Salary',TRUE,FALSE,FALSE,FALSE,FALSE,-15000.0,-15000.0,-105000.0),
            ('2026-10-01','2026-10-31','2026-10','c_div','Dividends','Income from Other Sources',FALSE,TRUE,TRUE,FALSE,FALSE,3000.0,2000.0,13000.0),
            ('2026-10-01','2026-10-31','2026-10','c_int','Savings Interest','Income from Other Sources',FALSE,TRUE,FALSE,TRUE,FALSE,1500.0,1200.0,8000.0),
            ('2026-10-01','2026-10-31','2026-10','c_epf','Eer PF','Non-Cash Income',FALSE,FALSE,FALSE,FALSE,TRUE,3000.0,3000.0,21000.0),
            ('2026-10-01','2026-10-31','2026-10','c_misc','Cashback',NULL,FALSE,FALSE,FALSE,FALSE,FALSE,500.0,300.0,2000.0);
            """
        )

        con.execute("DROP TABLE IF EXISTS gold.cashflow_efficiency_analytics;")
        con.execute(
            """
            CREATE TABLE gold.cashflow_efficiency_analytics (
                MONTH_START_DATE DATE,
                MONTH_END_DATE DATE,
                YEAR_MONTH VARCHAR,
                Is_Surplus_Month BOOLEAN,
                Total_Income DOUBLE,
                Total_Cash_Income DOUBLE,
                Total_Non_Cash_Income DOUBLE,
                Active_Income DOUBLE,
                Passive_Income DOUBLE,
                Dividend_Income DOUBLE,
                Interest_Income DOUBLE,
                Income_MoM_Delta DOUBLE,
                Trailing_3M_Avg_Income DOUBLE,
                Savings_Rate_Pct DOUBLE,
                Active_Income_Share_Pct DOUBLE,
                Passive_Income_Share_Pct DOUBLE,
                Passive_Income_Core_Expense_Coverage_Ratio DOUBLE,
                Active_Income_Dependency_Pct DOUBLE
            );
            """
        )
        con.execute(
            """
            INSERT INTO gold.cashflow_efficiency_analytics VALUES
            ('2026-09-01','2026-09-30','2026-09',TRUE,67000.0,67000.0,0.0,65000.0,2000.0,2000.0,0.0,0.0,67000.0,0.50,0.97,0.03,0.05,0.97),
            ('2026-10-01','2026-10-31','2026-10',TRUE,73000.0,70000.0,3000.0,65000.0,4500.0,3000.0,1500.0,6000.0,70000.0,0.55,0.89,0.06,0.12,0.89);
            """
        )

        con.execute("DROP TABLE IF EXISTS gold.forecast_budget_variance;")
        con.execute(
            """
            CREATE TABLE gold.forecast_budget_variance (
                MONTH_START_DATE DATE,
                MONTH_END_DATE DATE,
                Total_Income DOUBLE,
                Actual_Income DOUBLE,
                Budget_Income DOUBLE,
                Is_Income_Volatile BOOLEAN
            );
            """
        )
        con.execute(
            """
            INSERT INTO gold.forecast_budget_variance VALUES
            ('2026-09-01','2026-09-30',67000.0,67000.0,65000.0,FALSE),
            ('2026-10-01','2026-10-31',73000.0,70000.0,68000.0,FALSE);
            """
        )

        con.execute("DROP TABLE IF EXISTS silver.dim_income_category;")
        con.execute(
            """
            CREATE TABLE silver.dim_income_category (
                UID VARCHAR,
                CATEGORY_NAME VARCHAR
            );
            """
        )
        con.execute(
            """
            INSERT INTO silver.dim_income_category VALUES
            ('p_sal','Income from Salary'),
            ('p_oth','Income from Other Sources'),
            ('p_nc','Non-Cash Income');
            """
        )

        con.execute("DROP TABLE IF EXISTS silver.dim_income_subcategory;")
        con.execute(
            """
            CREATE TABLE silver.dim_income_subcategory (
                UID VARCHAR,
                CATEGORY_ID VARCHAR,
                CATEGORY_NAME VARCHAR,
                CATEGORY_GROUPS VARCHAR,
                Is_Active_Income BOOLEAN,
                Is_Passive_Income BOOLEAN,
                Taxability VARCHAR
            );
            """
        )
        con.execute(
            """
            INSERT INTO silver.dim_income_subcategory VALUES
            ('c_basic','p_sal','Basic','Income from Salary',TRUE,FALSE,'taxable'),
            ('c_tds','p_sal','TDS','Income from Salary',TRUE,FALSE,'taxable'),
            ('c_div','p_oth','Dividends','Income from Other Sources',FALSE,TRUE,'taxable'),
            ('c_int','p_oth','Savings Interest','Income from Other Sources',FALSE,TRUE,'taxable'),
            ('c_epf','p_nc','Eer PF','Non-Cash Income',FALSE,FALSE,'non_taxable'),
            ('c_misc',NULL,'Cashback',NULL,FALSE,FALSE,'non_taxable');
            """
        )

        con.execute("DROP TABLE IF EXISTS silver.dim_asset_subcategory;")
        con.execute(
            """
            CREATE TABLE silver.dim_asset_subcategory (
                UID VARCHAR,
                ASSET_NAME VARCHAR
            );
            """
        )
        con.execute(
            """
            INSERT INTO silver.dim_asset_subcategory VALUES
            ('acc_hdfc','HDFC Bank'),
            ('acc_epf','EPF Account');
            """
        )

        con.execute("DROP TABLE IF EXISTS silver.fact_income_transactions;")
        con.execute(
            """
            CREATE TABLE silver.fact_income_transactions (
                UID VARCHAR,
                DATE DATE,
                ASSET_ID VARCHAR,
                CATEGORY_ID VARCHAR,
                CURRENCY_ID VARCHAR,
                DESCRIPTION VARCHAR,
                Is_Active_Income BOOLEAN,
                Is_Dividend_Income BOOLEAN,
                Is_Interest_Income BOOLEAN,
                Is_Non_Cash_Income BOOLEAN,
                BASE_AMOUNT DOUBLE,
                AMOUNT_ACCOUNT DOUBLE,
                LOCAL_AMOUNT DOUBLE,
                EXCH_RATE DOUBLE
            );
            """
        )
        con.execute(
            """
            INSERT INTO silver.fact_income_transactions VALUES
            ('tx1','2026-10-30','acc_hdfc','c_basic','INR_INR','Salary Basic',TRUE,FALSE,FALSE,FALSE,80000.0,80000.0,80000.0,1.0),
            ('tx2','2026-10-30','acc_hdfc','c_tds','INR_INR','Salary TDS',TRUE,FALSE,FALSE,FALSE,-15000.0,-15000.0,-15000.0,1.0),
            ('tx3','2026-10-20','acc_hdfc','c_div','INR_INR','Infosys Dividend',FALSE,TRUE,FALSE,FALSE,3000.0,3000.0,3000.0,1.0),
            ('tx4','2026-10-15','acc_hdfc','c_int','INR_INR','Savings Interest',FALSE,FALSE,TRUE,FALSE,1500.0,1500.0,1500.0,1.0),
            ('tx5','2026-10-30','acc_epf','c_epf','INR_INR','EPF Employer',FALSE,FALSE,FALSE,TRUE,3000.0,3000.0,3000.0,1.0),
            ('tx6','2026-10-10','acc_hdfc','c_misc','INR_INR','Card Cashback',FALSE,FALSE,FALSE,FALSE,500.0,500.0,500.0,1.0);
            """
        )
    finally:
        con.close()

    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
        f"""
[database]
target_db_base_path = "{base_dir.as_posix()}"
target_db_name = "{duck_path.name}"
raw_document_store_name = "{cp_path.name}"
disable_file_discoverer = false
""",
        encoding="utf-8",
    )
    rules_path = tmp_path / "rules.toml"
    rules_path.write_text('DEFAULT_CURRENCY_ID = "INR_INR"\n', encoding="utf-8")
    loaded = PFETLProfile.load(settings_path, rules_path, label="Household Test")
    assert loaded.profile is not None
    return loaded.profile


def test_household_income_totals_ratios_and_silver_drill(tmp_path: Path) -> None:
    """Verify Gold income totals, deductions, Unclassified fallback, non-additive ratios, and Silver drill."""
    prof = _build_household_profile(tmp_path)
    cat = PFETLCatalog(prof)
    _, compat = cat.discover()
    prov = cat.get_latest_provenance()
    period = PeriodResolver(DuckDBReader(prof)).resolve(
        PeriodRequest(preset=PeriodPreset.SELECTED_MONTH, selected_month="2026-10")
    )

    snap = HouseholdAnalytics.income_summary(prof, period, provenance=prov, compatibility=compat)
    assert snap.state == "ready"
    assert snap.gross_positive_income == pytest.approx(88000.0)
    assert snap.salary_deductions == pytest.approx(-15000.0)
    assert snap.net_income_total == pytest.approx(73000.0)
    assert sum(c.amount for c in snap.category_rows) == pytest.approx(73000.0)

    # Null group renders as "Unclassified"
    misc = next(c for c in snap.category_rows if c.category_name == "Cashback")
    assert misc.category_group == "Unclassified"

    # Ratio fields are not summed across 2 months
    period_2m = PeriodResolver(DuckDBReader(prof)).resolve(
        PeriodRequest(
            preset=PeriodPreset.CUSTOM,
            custom_start=date(2026, 9, 1),
            custom_end=date(2026, 10, 31),
        )
    )
    snap_2m = HouseholdAnalytics.income_summary(
        prof, period_2m, provenance=prov, compatibility=compat
    )
    kpi_map = {k.kpi_id: k for k in (*snap_2m.primary_kpis, *snap_2m.secondary_kpis)}
    assert kpi_map["income_total"].value == pytest.approx(67000.0 + 73000.0)
    assert kpi_map["income_active_dependency"].value == pytest.approx(0.89)

    # Silver drill sums match exact category & flag totals
    div_page = HouseholdAnalytics.income_transactions(prof, period, drill_category="Dividends")
    assert div_page.total_count == 1
    assert div_page.total_base_amount == pytest.approx(3000.0)
    assert div_page.rows[0].account_name == "HDFC Bank"

    ded_page = HouseholdAnalytics.income_transactions(prof, period, flag_filter="deductions")
    assert ded_page.total_count == 1
    assert ded_page.total_base_amount == pytest.approx(-15000.0)


def test_household_module_never_touches_bronze_or_ui() -> None:
    """Enforce Gold/Silver boundary and headless purity on household analytics modules."""
    for name in ("household.py", "household_queries.py"):
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
