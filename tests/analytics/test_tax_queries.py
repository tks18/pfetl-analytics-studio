"""Tests for TaxAnalytics service, Gold tax marts, and completeness."""

from __future__ import annotations

from pathlib import Path

import duckdb

from pfetl_analytics_studio.analytics import TaxAnalytics, TaxEventFilter
from pfetl_analytics_studio.data import QueryCache, RunProvenance
from pfetl_analytics_studio.profile import PFETLProfile


def _create_tax_db(db_path: Path) -> None:
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA gold;")
        con.execute("CREATE SCHEMA silver;")

        con.execute(
            """
            CREATE TABLE gold.tax_financial_year_summary (
                "FY" VARCHAR, "Total_Ledger_Income" DOUBLE, "Total_Investment_Gain_Loss" DOUBLE,
                "Total_Tax_Credits" DOUBLE, "Total_Estimated_Investment_Tax" DOUBLE,
                "Investment_Tax_Estimate_Count" BIGINT, "Investment_Tax_Estimate_Missing_Count" BIGINT,
                "Investment_Tax_Estimate_Complete" BOOLEAN, "Total_Estimated_Ledger_Tax" DOUBLE,
                "Ledger_Tax_Estimate_Count" BIGINT, "Ledger_Tax_Estimate_Missing_Count" BIGINT,
                "Ledger_Tax_Estimate_Complete" BOOLEAN, "Total_Event_Count" BIGINT,
                "Ready_Count" BIGINT, "Check_Required_Count" BIGINT,
                "Indicative_Unrealized_Tax_Liability" DOUBLE,
                "Indicative_Harvestable_Tax_Savings" DOUBLE,
                "Indicative_Net_Tax_Exposure" DOUBLE
            );
            INSERT INTO gold.tax_financial_year_summary VALUES
                ('2026-27', 1200000.0, 50000.0, 80000.0, 6250.0, 2, 1, FALSE, 150000.0, 12, 0, TRUE, 15, 13, 2, 20000.0, 3000.0, 93250.0),
                ('2025-26', 1000000.0, 20000.0, 60000.0, 2500.0, 3, 0, TRUE, 120000.0, 12, 0, TRUE, 15, 15, 0, 10000.0, 0.0, 72500.0);
            """
        )

        con.execute(
            """
            CREATE TABLE gold.tax_income_breakdown (
                "FY" VARCHAR, "Income_Head" VARCHAR, "Tax_Sub_Head" VARCHAR, "Source_Type" VARCHAR,
                "Event_Amount_INR" DOUBLE, "Estimated_Tax_INR" DOUBLE,
                "Estimated_Tax_Event_Count" BIGINT, "Estimated_Tax_Missing_Count" BIGINT,
                "Estimated_Tax_Complete" BOOLEAN, "Event_Count" BIGINT,
                "Ready_Count" BIGINT, "Check_Required_Count" BIGINT
            );
            INSERT INTO gold.tax_income_breakdown VALUES
                ('2026-27', 'Salary', 'Salary Income', 'LEDGER', 1200000.0, 150000.0, 12, 0, TRUE, 12, 12, 0),
                ('2026-27', 'Capital Gains', 'Equity Listed LTCG', 'QUANT', 50000.0, 6250.0, 2, 1, FALSE, 3, 1, 2);
            """
        )

        con.execute(
            """
            CREATE TABLE gold.tax_reconciliation (
                "FY" VARCHAR, "Tax_Sub_Head" VARCHAR,
                "Ledger_Tax_Event_Amount" DOUBLE, "Excluded_Non_Taxable_Amount" DOUBLE,
                "Excluded_Non_Taxable_Event_Count" BIGINT, "Excluded_Investment_Ledger_Amount" DOUBLE,
                "Excluded_Investment_Ledger_Event_Count" BIGINT, "Quant_Realized_Gain_Loss" DOUBLE,
                "Estimated_Investment_Tax" DOUBLE, "Estimated_Investment_Tax_Complete" BOOLEAN,
                "Estimated_Ledger_Tax" DOUBLE, "Estimated_Ledger_Tax_Complete" BOOLEAN,
                "Tax_Credit_Amount" DOUBLE, "Tax_Event_Amount" DOUBLE,
                "Investment_Source_Difference_INR" DOUBLE, "Ledger_Event_Count" BIGINT,
                "Quant_Event_Count" BIGINT, "Tax_Credit_Event_Count" BIGINT, "Check_Required_Count" BIGINT
            );
            INSERT INTO gold.tax_reconciliation VALUES
                ('2026-27', 'Equity Listed LTCG', 0.0, 0.0, 0, 5000.0, 1, 50000.0, 6250.0, FALSE, 0.0, TRUE, 0.0, 50000.0, 45000.0, 0, 3, 0, 2);
            """
        )

        con.execute(
            """
            CREATE TABLE silver.fact_tax_events (
                "Tax_Event_ID" VARCHAR, "FY" VARCHAR, "Event_Date" DATE, "Source_Type" VARCHAR,
                "Source_ID" VARCHAR, "Income_Head" VARCHAR, "Tax_Sub_Head" VARCHAR,
                "Amount_INR" DOUBLE, "Applied_Rate" DOUBLE, "Estimated_Tax" DOUBLE,
                "CURRENCY_ID" VARCHAR, "Tax_Status" VARCHAR, "Tax_Status_Reason" VARCHAR,
                "ISIN" VARCHAR, "Lot_ID" VARCHAR
            );
            INSERT INTO silver.fact_tax_events VALUES
                ('TE1', '2026-27', DATE '2026-04-30', 'LEDGER', 'TX1', 'Salary', 'Salary Income', 100000.0, 0.15, 15000.0, 'INR_INR', 'READY', '', '', ''),
                ('TE2', '2026-27', DATE '2026-10-15', 'QUANT', 'RE1', 'Capital Gains', 'Equity Listed LTCG', 50000.0, 0.125, 6250.0, 'INR_INR', 'CHECK_REQUIRED', 'Missing lot rate', 'INE001', 'L1');
            """
        )

        con.execute(
            """
            CREATE TABLE silver.ref_financial_year_parameters (
                "FY" VARCHAR, "FY_Start_Date" DATE, "FY_End_Date" DATE, "Debt_MF_Cutoff_Date" DATE,
                "Equity_Listed_LTCG" DOUBLE, "Equity_Listed_STCG" DOUBLE, "Equity_LTCG_Exemption" BIGINT,
                "Gold_LTCG" DOUBLE, "Gold_STCG" DOUBLE,
                "Debt_MF_Post_Cutoff_LTCG" DOUBLE, "Debt_MF_Post_Cutoff_STCG" DOUBLE,
                "Estimated_Ordinary_Income_Tax_Rate" DOUBLE, "Remarks" VARCHAR
            );
            INSERT INTO silver.ref_financial_year_parameters VALUES
                ('2026-27', DATE '2026-04-01', DATE '2027-03-31', DATE '2023-04-01', 0.125, 0.20, 125000, 0.125, 0.30, 0.30, 0.30, 0.30, 'FY 2026-27 rules');
            """
        )
    finally:
        con.close()


def _make_profile(db_path: Path) -> PFETLProfile:
    return PFETLProfile(
        profile_id="tax-test",
        label="Tax Test",
        settings_path=db_path.parent / "s.toml",
        rules_path=db_path.parent / "r.toml",
        target_db_base_path=db_path.parent,
        duckdb_path=db_path,
        control_plane_path=db_path.parent / "c.sqlite",
        lock_path=db_path.parent / "p.lock",
    )


def _make_prov() -> RunProvenance:
    return RunProvenance(
        run_id="r-tax",
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


def test_tax_summary_completeness_recon_and_events(tmp_path: Path) -> None:
    db_path = tmp_path / "tax.duckdb"
    _create_tax_db(db_path)
    profile = _make_profile(db_path)
    prov = _make_prov()
    cache = QueryCache()

    summary = TaxAnalytics.fy_summary(
        profile, "2026-27", run_id=prov.run_id, provenance=prov, cache=cache
    )
    assert summary.state == "ready"
    assert summary.indicative is True
    assert summary.total_ledger_income == 1200000.0
    assert summary.investment_tax_estimate_complete is False
    assert summary.investment_tax_estimate_missing_count == 1
    assert summary.ledger_tax_estimate_complete is True
    assert summary.fy_params is not None
    assert summary.fy_params.equity_listed_ltcg == 0.125

    missing_params_fy = TaxAnalytics.fy_summary(
        profile, "2025-26", run_id=prov.run_id, provenance=prov, cache=cache
    )
    assert missing_params_fy.state == "ready"
    assert missing_params_fy.fy_params is None

    bd = TaxAnalytics.income_breakdown(
        profile, "2026-27", source_type="QUANT", run_id=prov.run_id, provenance=prov
    )
    assert bd.indicative is True
    assert len(bd.rows) == 1
    assert bd.rows[0].estimated_tax_inr == 6250.0

    recon = TaxAnalytics.reconciliation(profile, "2026-27", run_id=prov.run_id, provenance=prov)
    assert recon.indicative is True
    assert len(recon.rows) == 1
    assert recon.rows[0].investment_source_difference_inr == 45000.0

    ev = TaxAnalytics.tax_events(
        profile, "2026-27", TaxEventFilter(source_type="QUANT"), run_id=prov.run_id, provenance=prov
    )
    assert ev.indicative is True
    assert len(ev.rows) == 1
    assert ev.rows[0].tax_event_id == "TE2"
