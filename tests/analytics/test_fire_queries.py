"""Tests for Phase 18 FireAnalytics service, simulation provenance, and FIRE charts."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import duckdb

from pfetl_analytics_studio.analytics import FireAnalytics
from pfetl_analytics_studio.data import QueryCache, RunProvenance
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    ComparisonMode,
    ComparisonPeriod,
    PeriodContext,
    PeriodPreset,
)
from pfetl_analytics_studio.ui import (
    build_fire_acceleration_cagr_figure,
    build_fire_corpus_targets_figure,
    build_fire_future_terminal_figure,
    build_fire_gap_coverage_figure,
    build_fire_horizon_years_figure,
    build_fire_income_vs_spend_figure,
    build_fire_monte_carlo_band_figure,
    build_fire_probability_trend_figure,
    build_fire_runway_comparison_figure,
    build_fire_savings_rate_figure,
    build_fire_spend_vs_savings_figure,
    build_fire_velocity_accel_figure,
)


def _create_fire_db(db_path: Path) -> None:
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA gold;")
        con.execute("CREATE SCHEMA meta;")
        con.execute(
            """
            CREATE TABLE gold.wealth_fire_analytics (
                "MONTH_START_DATE" DATE,
                "MONTH_END_DATE" DATE,
                "YEAR_MONTH" VARCHAR,
                "Projected_FI_Date_P50" DATE,
                "Total_Income" DOUBLE,
                "Total_Cash_Income" DOUBLE,
                "Trailing_6M_Avg_Spend" DOUBLE,
                "Trailing_12M_Avg_Spend" DOUBLE,
                "Trailing_6M_Avg_Savings" DOUBLE,
                "Trailing_12M_Avg_Savings" DOUBLE,
                "Target_FI_Today" DOUBLE,
                "Coast_FI_Today" DOUBLE,
                "Lean_FI_Today" DOUBLE,
                "Target_FI_Total_Future_Nominal" DOUBLE,
                "FI_Gap" DOUBLE,
                "FI_Gap_Monthly_Trend" DOUBLE,
                "Terminal_Wealth_Nominal_P50" DOUBLE,
                "Real_Return_Assumed_Pct" DOUBLE,
                "Wealth_Velocity" DOUBLE,
                "Wealth_Acceleration" DOUBLE,
                "Real_NW_CAGR_3Y" DOUBLE,
                "Current_FI_Coverage_Pct" DOUBLE,
                "Estimated_Months_To_FI_Linear" DOUBLE,
                "Months_To_FI_Conservative_P90" DOUBLE,
                "Months_To_FI_Base_P50" DOUBLE,
                "Months_To_FI_Aggressive_P10" DOUBLE,
                "Probability_Of_Success_Pct" DOUBLE,
                "Years_To_FI_P50" DOUBLE,
                "Runway_Months_Linear" DOUBLE,
                "Runway_Months_Stressed_P10" DOUBLE,
                "Runway_Months_Base_P50" DOUBLE,
                "Withdrawal_Rate_If_Retired_Now" DOUBLE,
                "Savings_Rate_Required" DOUBLE,
                "Savings_Rate_Actual" DOUBLE,
                "FI_Velocity" DOUBLE
            );
            INSERT INTO gold.wealth_fire_analytics VALUES
                ('2026-08-01', '2026-08-31', '2026-08', '2035-08-01', 250000.0, 240000.0, 90000.0, 88000.0, 150000.0, 145000.0, 26400000.0, 11000000.0, 18000000.0, 52000000.0, 14400000.0, -200000.0, 68000000.0, 0.05, 180000.0, 10000.0, 0.14, 0.4545, 76.0, 98.0, 84.0, 70.0, 0.88, 7.0, 140.0, 105.0, 132.0, 0.075, 0.42, 0.60, 0.015),
                ('2026-09-01', '2026-09-30', '2026-09', '2035-06-01', 255000.0, 245000.0, 91000.0, 89000.0, 154000.0, 148000.0, 26700000.0, 11200000.0, 18200000.0, 52500000.0, 14000000.0, -400000.0, 69500000.0, 0.05, 210000.0, 30000.0, 0.15, 0.4756, 74.0, 95.0, 81.0, 68.0, 0.89, 6.75, 145.0, 108.0, 136.0, 0.072, 0.41, 0.61, 0.018),
                ('2026-10-01', '2026-10-31', '2026-10', '2035-04-01', 260000.0, 250000.0, 92000.0, 90000.0, 160000.0, 152000.0, 27000000.0, 11500000.0, 18500000.0, 53000000.0, 13500000.0, -500000.0, 71000000.0, 0.05, 240000.0, 30000.0, 0.16, 0.5000, 71.0, 92.0, 78.0, 65.0, 0.91, 6.50, 150.0, 112.0, 140.0, 0.068, 0.40, 0.62, 0.021);
            """
        )
        con.execute(
            """
            CREATE TABLE meta.m_Latest_Simulation (
                "simulation_id" VARCHAR,
                "run_id" VARCHAR,
                "created_at" VARCHAR,
                "root_seed" VARCHAR,
                "iterations" BIGINT,
                "horizon" BIGINT,
                "input_as_of_date" DATE,
                "model_implementation_version" VARCHAR,
                "model_fingerprint" VARCHAR,
                "input_fingerprint" VARCHAR,
                "result_fingerprint" VARCHAR,
                "contract_registry_fingerprint" VARCHAR,
                "settings_snapshot_id" VARCHAR,
                "rules_snapshot_id" VARCHAR
            );
            INSERT INTO meta.m_Latest_Simulation VALUES (
                'sim-002', 'r-fire', '2026-10-31T10:00:00Z', '42', 10000, 30,
                '2026-10-31', '2.1.0', 'mod-fp', 'in-fp', 'res-fp', 'cr-fp', 's1', 'r1'
            );
            """
        )
    finally:
        con.close()


def _create_cp_db(cp_path: Path) -> None:
    con = sqlite3.connect(str(cp_path))
    try:
        con.execute(
            """
            CREATE TABLE cp_simulation_runs (
                simulation_id TEXT,
                run_id TEXT,
                status TEXT,
                created_at TEXT,
                root_seed TEXT,
                iterations INTEGER,
                horizon INTEGER,
                model_implementation_version TEXT,
                model_fingerprint TEXT,
                input_fingerprint TEXT,
                result_fingerprint TEXT,
                contract_registry_fingerprint TEXT,
                settings_snapshot_id TEXT,
                rules_snapshot_id TEXT
            );
            """
        )
        con.execute(
            """
            INSERT INTO cp_simulation_runs VALUES
                ('sim-002', 'r-fire', 'SUCCESS', '2026-10-31T10:00:00Z', '42', 10000, 30, '2.1.0', 'mod-fp', 'in-fp', 'res-fp', 'cr-fp', 's1', 'r1'),
                ('sim-001', 'r-old', 'FAILED', '2026-10-30T09:00:00Z', '41', 10000, 30, '2.1.0', 'mod-fp', 'in-fp', '', 'cr-fp', 's0', 'r0');
            """
        )
        con.commit()
    finally:
        con.close()


def _make_profile(db_path: Path, cp_path: Path) -> PFETLProfile:
    return PFETLProfile(
        profile_id="fire-test",
        label="FIRE Test",
        settings_path=db_path.parent / "s.toml",
        rules_path=db_path.parent / "r.toml",
        target_db_base_path=db_path.parent,
        duckdb_path=db_path,
        control_plane_path=cp_path,
        lock_path=db_path.parent / "p.lock",
    )


def _make_prov() -> RunProvenance:
    return RunProvenance(
        run_id="r-fire",
        status="SUCCESS",
        started_at=None,
        finished_at=None,
        commit_state="committed",
        application_version="1.0.0",
        schema_version="1.0.0",
        settings_snapshot_id="s1",
        rules_snapshot_id="r1",
        settings_payload={},
        rules_payload={"fire": {"safe_withdrawal_rate": 0.035, "inflation_rate": 0.06}},
        has_settings_snapshot=True,
        has_rules_snapshot=True,
        has_execution_log=True,
        is_complete=True,
    )


def test_fire_summary_history_and_charts(tmp_path: Path) -> None:
    db_path = tmp_path / "fire.duckdb"
    cp_path = tmp_path / "cp.sqlite"
    _create_fire_db(db_path)
    _create_cp_db(cp_path)
    profile = _make_profile(db_path, cp_path)
    prov = _make_prov()
    cache = QueryCache()

    comp = ComparisonPeriod(
        mode=ComparisonMode.PREVIOUS_PERIOD,
        label="Sep 2026",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )
    period = PeriodContext(
        preset=PeriodPreset.ROLLING_3M,
        label="Aug 2026 - Oct 2026",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 10, 31),
        is_empty=False,
        comparison=comp,
    )

    snap = FireAnalytics.summary(profile, period, run_id=prov.run_id, provenance=prov, cache=cache)
    assert snap.state == "ready"
    assert snap.as_of_date == date(2026, 10, 31)
    assert len(snap.history) == 3
    assert len(snap.kpis) == 12
    assert snap.latest is not None
    assert snap.latest.target_fi_today == 27000000.0
    assert snap.latest.coast_fi_today == 11500000.0
    assert snap.latest_simulation is not None
    assert snap.latest_simulation.simulation_id == "sim-002"

    # Build all 8 Plotly figures without errors
    figs = [
        build_fire_corpus_targets_figure(snap.history, currency_id=snap.currency_id),
        build_fire_gap_coverage_figure(snap.history, currency_id=snap.currency_id),
        build_fire_monte_carlo_band_figure(snap.history),
        build_fire_probability_trend_figure(snap.history),
        build_fire_runway_comparison_figure(snap.history),
        build_fire_savings_rate_figure(snap.history),
        build_fire_spend_vs_savings_figure(snap.history, currency_id=snap.currency_id),
        build_fire_velocity_accel_figure(snap.history, currency_id=snap.currency_id),
        build_fire_future_terminal_figure(snap.history, currency_id=snap.currency_id),
        build_fire_horizon_years_figure(snap.history, currency_id=snap.currency_id),
        build_fire_income_vs_spend_figure(snap.history, currency_id=snap.currency_id),
        build_fire_acceleration_cagr_figure(snap.history, currency_id=snap.currency_id),
    ]
    for fig in figs:
        assert len(fig["data"]) >= 1

    # Simulation history includes failed runs & FinancialRules
    sim_hist = FireAnalytics.simulation_history(
        profile, run_id=prov.run_id, provenance=prov, cache=cache
    )
    assert sim_hist.state == "ready"
    assert sim_hist.latest_meta is not None
    assert sim_hist.latest_meta.root_seed == "42"
    statuses = [r.status for r in sim_hist.runs]
    assert "SUCCESS" in statuses
    assert "FAILED" in statuses
    assert sim_hist.rules_assumptions["fire"]["safe_withdrawal_rate"] == 0.035


def test_fire_simulation_history_missing_cp_fallback(tmp_path: Path) -> None:
    db_path = tmp_path / "fire.duckdb"
    cp_path = tmp_path / "missing_cp.sqlite"
    _create_fire_db(db_path)
    profile = _make_profile(db_path, cp_path)
    prov = _make_prov()

    sim_hist = FireAnalytics.simulation_history(profile, run_id=prov.run_id, provenance=prov)
    assert sim_hist.state == "ready"
    assert sim_hist.latest_meta is not None
    assert sim_hist.latest_meta.simulation_id == "sim-002"
    assert len(sim_hist.runs) == 1
    assert sim_hist.runs[0].source == "meta"
