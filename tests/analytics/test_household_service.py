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


def test_household_denominators_and_unclassified_taxability(tmp_path: Path) -> None:
    """Verify full-denominator >10 account shares, gross-positive group share, and unclassified taxability."""
    prof = _build_household_profile(tmp_path)
    con = duckdb.connect(str(prof.duckdb_path))
    try:
        # Add 11 extra receiving accounts + unclassified taxability subcategory
        con.execute(
            "INSERT INTO silver.dim_income_subcategory VALUES ('c_unk', NULL, 'UnknownMisc', NULL, FALSE, FALSE, NULL);"
        )
        for idx in range(12):
            acc_id = f"acc_extra_{idx}"
            con.execute(
                "INSERT INTO silver.dim_asset_subcategory VALUES (?, ?);",
                (acc_id, f"Extra Bank {idx}"),
            )
            con.execute(
                """
                INSERT INTO silver.fact_income_transactions VALUES
                (?, '2026-10-12', ?, 'c_unk', 'INR_INR', 'Misc Unclassified', FALSE, FALSE, FALSE, FALSE, 100.0, 100.0, 100.0, 1.0);
                """,
                (f"tx_extra_{idx}", acc_id),
            )
    finally:
        con.close()

    period = PeriodResolver(DuckDBReader(prof)).resolve(
        PeriodRequest(preset=PeriodPreset.SELECTED_MONTH, selected_month="2026-10")
    )
    snap = HouseholdAnalytics.income_summary(prof, period)

    # Top-10 account shares must sum to < 1.0 when >10 receiving accounts exist
    assert len(snap.account_mix) == 10
    assert sum(pt.share_pct or 0.0 for pt in snap.account_mix) < 1.0

    # Group concentration uses gross positive income (88,000), not net after -15,000 TDS (73,000)
    sal_grp = next(g for g in snap.group_rows if g.category_group == "Income from Salary")
    assert sal_grp.share_pct == pytest.approx(65000.0 / 88000.0)

    # Unclassified taxability is surfaced alongside Taxable and Non-Taxable
    assert snap.taxability.unclassified_amount == pytest.approx(1200.0)
    tax_kpi = next(k for k in snap.primary_kpis if k.kpi_id == "income_taxability")
    assert any(b.label == "Unclassified" for b in tax_kpi.breakups)


def test_household_expense_totals_real_growth_and_silver_drill(tmp_path: Path) -> None:
    """Verify Gold expense reconciliation, real core growth passthrough, >10 accounts, and drill."""
    prof = _build_household_profile(tmp_path)
    con = duckdb.connect(str(prof.duckdb_path))
    try:
        con.execute("DROP TABLE IF EXISTS gold.cashflow_expense_breakdown;")
        con.execute(
            """
            CREATE TABLE gold.cashflow_expense_breakdown (
                MONTH_START_DATE DATE,
                MONTH_END_DATE DATE,
                YEAR_MONTH VARCHAR,
                CATEGORY_ID VARCHAR,
                CATEGORY_NAME VARCHAR,
                CATEGORY_GROUPS VARCHAR,
                Spend_Type VARCHAR,
                Is_Core_Expense BOOLEAN,
                Is_Investment BOOLEAN,
                Is_Discretionary BOOLEAN,
                Total_Monthly_Spend DOUBLE,
                Cash_Spend DOUBLE,
                Non_Cash_Spend DOUBLE,
                Trailing_3M_Avg_Spend DOUBLE,
                Cumulative_YTD_Spend DOUBLE
            );
            """
        )
        con.execute(
            """
            INSERT INTO gold.cashflow_expense_breakdown VALUES
            ('2026-09-01','2026-09-30','2026-09','e_rent','House Rent','Rents','Fixed',TRUE,FALSE,FALSE,15000.0,15000.0,0.0,15000.0,90000.0),
            ('2026-09-01','2026-09-30','2026-09','e_food','Swiggy','Food','Variable',FALSE,FALSE,TRUE,5000.0,5000.0,0.0,4500.0,30000.0),
            ('2026-10-01','2026-10-31','2026-10','e_rent','House Rent','Rents','Fixed',TRUE,FALSE,FALSE,15000.0,15000.0,0.0,15000.0,105000.0),
            ('2026-10-01','2026-10-31','2026-10','e_ins','Life Insurance','Insurances','Fixed',TRUE,FALSE,FALSE,2000.0,0.0,2000.0,2000.0,14000.0),
            ('2026-10-01','2026-10-31','2026-10','e_food','Swiggy','Food','Variable',FALSE,FALSE,TRUE,5000.0,5000.0,0.0,4800.0,35000.0),
            ('2026-10-01','2026-10-31','2026-10','e_misc','Misc Cash',NULL,'Variable',FALSE,FALSE,TRUE,600.0,600.0,0.0,400.0,1200.0),
            ('2026-10-01','2026-10-31','2026-10','e_ref','Refund','Food','Variable',FALSE,FALSE,TRUE,-200.0,-200.0,0.0,0.0,-200.0);
            """
        )

        con.execute("DROP TABLE IF EXISTS gold.cashflow_efficiency_analytics;")
        con.execute(
            """
            CREATE TABLE gold.cashflow_efficiency_analytics (
                MONTH_START_DATE DATE,
                MONTH_END_DATE DATE,
                YEAR_MONTH VARCHAR,
                Total_Expense DOUBLE,
                Core_Expense DOUBLE,
                NonCore_Expense DOUBLE,
                Total_Cash_Expense DOUBLE,
                Total_Non_Cash_Expense DOUBLE,
                Expense_MoM_Delta DOUBLE,
                Expense_MoM_Pct DOUBLE,
                Trailing_3M_Avg_Expense DOUBLE,
                Core_Expense_Share_Pct DOUBLE,
                NonCore_Expense_Share_Pct DOUBLE,
                Expense_to_NW_Ratio DOUBLE
            );
            """
        )
        con.execute(
            """
            INSERT INTO gold.cashflow_efficiency_analytics VALUES
            ('2026-09-01','2026-09-30','2026-09',20000.0,15000.0,5000.0,20000.0,0.0,0.0,0.0,20000.0,0.75,0.25,0.01),
            ('2026-10-01','2026-10-31','2026-10',22400.0,17000.0,5400.0,20400.0,2000.0,2400.0,0.12,21200.0,0.7589,0.2411,0.011);
            """
        )

        con.execute("DROP TABLE IF EXISTS gold.core_monthly_fact;")
        con.execute(
            """
            CREATE TABLE gold.core_monthly_fact (
                MONTH_START_DATE DATE,
                MONTH_END_DATE DATE,
                Total_Expense DOUBLE,
                Total_Core_Expense DOUBLE,
                Inflation_Adjusted_Core_Expense DOUBLE,
                Real_Core_Expense_Growth_Pct DOUBLE,
                INFLATION_YOY_PCT DOUBLE
            );
            """
        )
        con.execute(
            """
            INSERT INTO gold.core_monthly_fact VALUES
            ('2026-09-01','2026-09-30',20000.0,15000.0,14100.0,0.05,0.06),
            ('2026-10-01','2026-10-31',22400.0,17000.0,15900.0,0.1277,0.06);
            """
        )

        con.execute("DROP TABLE IF EXISTS silver.dim_expense_category;")
        con.execute(
            "CREATE TABLE silver.dim_expense_category (UID VARCHAR, CATEGORY_NAME VARCHAR);"
        )
        con.execute(
            """
            INSERT INTO silver.dim_expense_category VALUES
            ('g_rent','Rents'),('g_ins','Insurances'),('g_food','Food');
            """
        )
        con.execute("DROP TABLE IF EXISTS silver.dim_expense_subcategory;")
        con.execute(
            """
            CREATE TABLE silver.dim_expense_subcategory (
                UID VARCHAR, CATEGORY_ID VARCHAR, CATEGORY_NAME VARCHAR, Is_Core_Expense BOOLEAN
            );
            """
        )
        con.execute(
            """
            INSERT INTO silver.dim_expense_subcategory VALUES
            ('e_rent','g_rent','House Rent',TRUE),
            ('e_ins','g_ins','Life Insurance',TRUE),
            ('e_food','g_food','Swiggy',FALSE),
            ('e_misc',NULL,'Misc Cash',FALSE);
            """
        )
        con.execute("DROP TABLE IF EXISTS silver.fact_expense_transactions;")
        con.execute(
            """
            CREATE TABLE silver.fact_expense_transactions (
                UID VARCHAR, DATE DATE, ASSET_ID VARCHAR, CATEGORY_ID VARCHAR,
                CURRENCY_ID VARCHAR, DESCRIPTION VARCHAR, PAID VARCHAR,
                Is_Core_Expense BOOLEAN, BASE_AMOUNT DOUBLE, AMOUNT_ACCOUNT DOUBLE,
                LOCAL_AMOUNT DOUBLE, EXCH_RATE DOUBLE
            );
            """
        )
        con.execute(
            """
            INSERT INTO silver.fact_expense_transactions VALUES
            ('ex1','2026-10-02','acc_hdfc','e_rent','INR_INR','Oct Rent','Yes',TRUE,15000.0,15000.0,15000.0,1.0),
            ('ex2','2026-10-05','acc_epf','e_ins','INR_INR','LIC Amort','Yes',TRUE,2000.0,2000.0,2000.0,1.0),
            ('ex3','2026-10-10','acc_hdfc','e_food','INR_INR','Dinner','Yes',FALSE,5000.0,5000.0,5000.0,1.0),
            ('ex4','2026-10-12','acc_hdfc','e_food','INR_INR','Swiggy Refund','Yes',FALSE,-200.0,-200.0,-200.0,1.0);
            """
        )
        for idx in range(11):
            acc_id = f"acc_pay_{idx}"
            con.execute(
                "INSERT INTO silver.dim_asset_subcategory VALUES (?, ?);", (acc_id, f"Card {idx}")
            )
            con.execute(
                """
                INSERT INTO silver.fact_expense_transactions VALUES
                (?, '2026-10-14', ?, 'e_misc', 'INR_INR', 'Small spend', 'Yes', FALSE, 50.0, 50.0, 50.0, 1.0);
                """,
                (f"ex_p_{idx}", acc_id),
            )
    finally:
        con.close()

    period = PeriodResolver(DuckDBReader(prof)).resolve(
        PeriodRequest(preset=PeriodPreset.SELECTED_MONTH, selected_month="2026-10")
    )
    snap = HouseholdAnalytics.expense_summary(prof, period)
    assert snap.state == "ready"
    assert snap.core_spend + snap.non_core_spend == pytest.approx(snap.total_spend)
    assert snap.cash_spend + snap.non_cash_spend == pytest.approx(snap.total_spend)
    assert sum(c.amount for c in snap.category_rows) == pytest.approx(snap.total_spend)

    misc = next(c for c in snap.category_rows if c.category_name == "Misc Cash")
    assert misc.category_group == "Unclassified"

    assert len(snap.account_mix) == 10
    assert sum(a.share_pct or 0.0 for a in snap.account_mix) < 1.0
    assert len(snap.heatmap.months) >= 1

    # Non-additive real core growth is preserved across 2 months
    period_2m = PeriodResolver(DuckDBReader(prof)).resolve(
        PeriodRequest(
            preset=PeriodPreset.CUSTOM,
            custom_start=date(2026, 9, 1),
            custom_end=date(2026, 10, 31),
        )
    )
    snap_2m = HouseholdAnalytics.expense_summary(prof, period_2m)
    kmap = {k.kpi_id: k for k in (*snap_2m.primary_kpis, *snap_2m.secondary_kpis)}
    assert kmap["expense_total"].value == pytest.approx(42400.0)
    assert kmap["expense_real_growth"].value == pytest.approx(0.1277)

    # Silver drill matches group & refunds filter and retains PAID audit field
    rent_page = HouseholdAnalytics.expense_transactions(prof, period, drill_group="Rents")
    assert rent_page.total_count == 1
    assert rent_page.total_base_amount == pytest.approx(15000.0)
    assert rent_page.rows[0].paid == "Yes"

    ref_page = HouseholdAnalytics.expense_transactions(prof, period, flag_filter="refunds")
    assert ref_page.total_count == 1
    assert ref_page.total_base_amount == pytest.approx(-200.0)


def test_household_module_never_touches_bronze_or_ui() -> None:
    """Enforce Gold/Silver boundary and headless purity on household analytics modules."""
    for name in (
        "household.py",
        "household_queries.py",
        "household_expense_queries.py",
        "household_budget_cashflow_queries.py",
    ):
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


def test_household_budget_cashflow_and_transfer_drill(tmp_path: Path) -> None:
    """Verify Budget Variance passthrough, Cashflow reconciliation, and Silver transfer drill."""
    prof = _build_household_profile(tmp_path)
    con = duckdb.connect(str(prof.duckdb_path))
    try:
        con.execute("DROP TABLE IF EXISTS gold.forecast_budget_variance;")
        con.execute(
            """
            CREATE TABLE gold.forecast_budget_variance (
                MONTH_START_DATE DATE,
                MONTH_END_DATE DATE,
                YEAR_MONTH VARCHAR,
                Actual_Income DOUBLE,
                Budget_Income DOUBLE,
                Income_Variance DOUBLE,
                Actual_Core_Expense DOUBLE,
                Budget_Core_Expense DOUBLE,
                Core_Variance DOUBLE,
                Actual_Non_Core_Expense DOUBLE,
                Budget_Non_Core_Expense DOUBLE,
                Non_Core_Variance DOUBLE,
                Actual_Investment DOUBLE,
                Budget_Investment DOUBLE,
                Investment_Variance DOUBLE,
                Rule_Core_Pct DOUBLE,
                Rule_Non_Core_Pct DOUBLE,
                Rule_Investment_Pct DOUBLE,
                Emergency_Fund_Target DOUBLE,
                Emergency_Fund_Actual DOUBLE,
                Emergency_Fund_Gap DOUBLE,
                Emergency_Fund_Runway_Months DOUBLE,
                Emergency_Target_Months DOUBLE,
                Is_Core_Overspent BOOLEAN,
                Is_Non_Core_Overspent BOOLEAN,
                Is_Investment_Underfunded BOOLEAN,
                Is_Income_Volatile BOOLEAN,
                Is_Budget_Healthy BOOLEAN
            );
            """
        )
        con.execute(
            """
            INSERT INTO gold.forecast_budget_variance VALUES
            ('2026-09-01','2026-09-30','2026-09',67000.0,65000.0,2000.0,
             26000.0,27000.0,-1000.0,12000.0,13000.0,-1000.0,25000.0,25000.0,0.0,
             40.0,20.0,40.0,180000.0,150000.0,-30000.0,5.5,6.0,
             FALSE,FALSE,FALSE,FALSE,TRUE),
            ('2026-10-01','2026-10-31','2026-10',0.0,68000.0,-68000.0,
             33000.0,27200.0,5800.0,14000.0,13600.0,400.0,10000.0,27200.0,-17200.0,
             40.0,20.0,40.0,180000.0,140000.0,-40000.0,4.2,6.0,
             TRUE,TRUE,TRUE,TRUE,FALSE);
            """
        )

        con.execute("DROP TABLE IF EXISTS gold.cashflow_activity_summary;")
        con.execute(
            """
            CREATE TABLE gold.cashflow_activity_summary (
                MONTH_START_DATE DATE,
                MONTH_END_DATE DATE,
                YEAR_MONTH VARCHAR,
                Opening_Cash_Balance DOUBLE,
                Closing_Cash_Balance DOUBLE,
                Operating_Inflows DOUBLE,
                Operating_Outflows DOUBLE,
                Net_Operating_Cashflow DOUBLE,
                Investing_Inflows DOUBLE,
                Investing_Outflows DOUBLE,
                Net_Investing_Cashflow DOUBLE,
                Financing_Inflows DOUBLE,
                Financing_Outflows DOUBLE,
                Net_Financing_Cashflow DOUBLE,
                Internal_Transfer_Inflows DOUBLE,
                Internal_Transfer_Outflows DOUBLE,
                Net_Internal_Transfers DOUBLE,
                Calculated_Net_Cashflow DOUBLE,
                Actual_Cash_Change DOUBLE,
                Unreconciled_Difference DOUBLE,
                Is_Reconciled BOOLEAN
            );
            """
        )
        con.execute(
            """
            INSERT INTO gold.cashflow_activity_summary VALUES
            ('2026-09-01','2026-09-30','2026-09',100000.0,129000.0,
             67000.0,-38000.0,29000.0,0.0,0.0,0.0,0.0,0.0,0.0,5000.0,-5000.0,0.0,
             29000.0,29000.0,0.0,TRUE),
            ('2026-10-01','2026-10-31','2026-10',129000.0,104500.0,
             50000.0,-47000.0,3000.0,0.0,-25000.0,-25000.0,0.0,-3000.0,-3000.0,
             10000.0,-10000.0,0.0,-25000.0,-24500.0,500.0,FALSE);
            """
        )

        con.execute("DROP TABLE IF EXISTS silver.fact_transfer_transactions;")
        con.execute(
            """
            CREATE TABLE silver.fact_transfer_transactions (
                UID VARCHAR,
                DATE DATE,
                ADJUSTED_DATE_FOR_ANALYSIS DATE,
                ASSET_ID VARCHAR,
                TO_ASSET_ID VARCHAR,
                CURRENCY_ID VARCHAR,
                TRANSFER_TYPE VARCHAR,
                TRANSACTION_TYPE VARCHAR,
                BASE_AMOUNT DOUBLE,
                PAID VARCHAR,
                DESCRIPTION VARCHAR
            );
            """
        )
        con.execute(
            """
            INSERT INTO silver.fact_transfer_transactions VALUES
            ('tr_1','2026-10-05','2026-10-05','a_hdfc','a_cash','INR_INR',
             'Investing Transfer','Transfer',25000.0,'Yes','Monthly SIP'),
            ('tr_2','2026-10-12','2026-10-12','a_hdfc','a_cash','INR_INR',
             'Internal Transfer','Transfer',10000.0,'Yes','Sweep');
            """
        )
    finally:
        con.close()

    period = PeriodResolver(DuckDBReader(prof)).resolve(
        PeriodRequest(preset=PeriodPreset.SELECTED_MONTH, selected_month="2026-10")
    )
    b_snap = HouseholdAnalytics.budget_summary(prof, period)
    assert b_snap.state == "ready"
    assert b_snap.total_actual_income == pytest.approx(0.0)
    assert b_snap.total_actual_core == pytest.approx(33000.0)
    assert b_snap.latest_flags.is_core_overspent is True
    assert b_snap.latest_flags.is_investment_underfunded is True
    assert b_snap.latest_flags.is_budget_healthy is False

    c_snap = HouseholdAnalytics.cashflow_statement(prof, period)
    assert c_snap.state == "ready"
    assert c_snap.opening_cash == pytest.approx(129000.0)
    assert c_snap.closing_cash == pytest.approx(104500.0)
    assert c_snap.net_operating == pytest.approx(3000.0)
    assert c_snap.net_investing == pytest.approx(-25000.0)
    assert c_snap.net_financing == pytest.approx(-3000.0)
    assert c_snap.calculated_net_cashflow == pytest.approx(-25000.0)
    assert c_snap.unreconciled_difference == pytest.approx(500.0)
    assert c_snap.is_reconciled is False

    inv_drill = HouseholdAnalytics.transfer_transactions(prof, period, activity_filter="Investing")
    assert inv_drill.total_count == 1
    assert inv_drill.total_base_amount == pytest.approx(25000.0)
    assert inv_drill.rows[0].paid == "Yes"

    fin_drill = HouseholdAnalytics.transfer_transactions(prof, period, activity_filter="Financing")
    assert fin_drill.total_count == 0
