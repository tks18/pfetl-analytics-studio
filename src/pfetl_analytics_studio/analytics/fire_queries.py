"""DuckDB & Control Plane SQL queries for Phase 18 FIRE & Simulation Analytics."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from pfetl_analytics_studio.analytics.fire_models import (
    FireMonthlyPoint,
    SimulationRunRecord,
)
from pfetl_analytics_studio.analytics.overview_models import (
    OverviewBreakupItem,
    OverviewKPI,
    as_date,
    as_float,
)
from pfetl_analytics_studio.analytics.overview_queries import table_exists
from pfetl_analytics_studio.data import DuckDBQueryPort
from pfetl_analytics_studio.semantic import MetricFormat


def _row_to_fire_point(r: dict[str, Any]) -> FireMonthlyPoint | None:
    dt = as_date(r.get("MONTH_START_DATE"))
    if dt is None:
        return None
    return FireMonthlyPoint(
        month_start_date=dt,
        month_end_date=as_date(r.get("MONTH_END_DATE")),
        year_month=str(r.get("YEAR_MONTH") or ""),
        projected_fi_date_p50=as_date(r.get("Projected_FI_Date_P50")),
        total_income=as_float(r.get("Total_Income")) or 0.0,
        total_cash_income=as_float(r.get("Total_Cash_Income")) or 0.0,
        trailing_6m_avg_spend=as_float(r.get("Trailing_6M_Avg_Spend")) or 0.0,
        trailing_12m_avg_spend=as_float(r.get("Trailing_12M_Avg_Spend")) or 0.0,
        trailing_6m_avg_savings=as_float(r.get("Trailing_6M_Avg_Savings")) or 0.0,
        trailing_12m_avg_savings=as_float(r.get("Trailing_12M_Avg_Savings")) or 0.0,
        target_fi_today=as_float(r.get("Target_FI_Today")) or 0.0,
        coast_fi_today=as_float(r.get("Coast_FI_Today")) or 0.0,
        lean_fi_today=as_float(r.get("Lean_FI_Today")) or 0.0,
        target_fi_total_future_nominal=as_float(r.get("Target_FI_Total_Future_Nominal")) or 0.0,
        fi_gap=as_float(r.get("FI_Gap")) or 0.0,
        fi_gap_monthly_trend=as_float(r.get("FI_Gap_Monthly_Trend")),
        terminal_wealth_nominal_p50=as_float(r.get("Terminal_Wealth_Nominal_P50")) or 0.0,
        real_return_assumed_pct=as_float(r.get("Real_Return_Assumed_Pct")),
        wealth_velocity=as_float(r.get("Wealth_Velocity")),
        wealth_acceleration=as_float(r.get("Wealth_Acceleration")),
        real_nw_cagr_3y=as_float(r.get("Real_NW_CAGR_3Y")),
        current_fi_coverage_pct=as_float(r.get("Current_FI_Coverage_Pct")),
        estimated_months_to_fi_linear=as_float(r.get("Estimated_Months_To_FI_Linear")),
        months_to_fi_conservative_p90=as_float(r.get("Months_To_FI_Conservative_P90")),
        months_to_fi_base_p50=as_float(r.get("Months_To_FI_Base_P50")),
        months_to_fi_aggressive_p10=as_float(r.get("Months_To_FI_Aggressive_P10")),
        probability_of_success_pct=as_float(r.get("Probability_Of_Success_Pct")),
        years_to_fi_p50=as_float(r.get("Years_To_FI_P50")),
        runway_months_linear=as_float(r.get("Runway_Months_Linear")),
        runway_months_stressed_p10=as_float(r.get("Runway_Months_Stressed_P10")),
        runway_months_base_p50=as_float(r.get("Runway_Months_Base_P50")),
        withdrawal_rate_if_retired_now=as_float(r.get("Withdrawal_Rate_If_Retired_Now")),
        savings_rate_required=as_float(r.get("Savings_Rate_Required")),
        savings_rate_actual=as_float(r.get("Savings_Rate_Actual")),
        fi_velocity=as_float(r.get("FI_Velocity")),
    )


def query_fire_history(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
) -> tuple[FireMonthlyPoint, ...]:
    """Query `gold.wealth_fire_analytics` ordered ascending by `MONTH_START_DATE`."""
    if not table_exists(duck_reader, "gold", "wealth_fire_analytics"):
        return ()
    rows = duck_reader.fetch_all(
        """
        SELECT *
        FROM gold.wealth_fire_analytics
        WHERE "MONTH_START_DATE" BETWEEN ? AND ?
        ORDER BY "MONTH_START_DATE" ASC;
        """,
        (start_date, end_date),
    )
    if not rows:
        latest = duck_reader.fetch_one(
            """
            SELECT *
            FROM gold.wealth_fire_analytics
            WHERE "MONTH_START_DATE" <= ?
            ORDER BY "MONTH_START_DATE" DESC
            LIMIT 1;
            """,
            (end_date,),
        )
        rows = [latest] if latest else []

    points = [_row_to_fire_point(r) for r in rows]
    return tuple(p for p in points if p is not None)


def query_latest_fire_point(
    duck_reader: DuckDBQueryPort,
    end_date: date,
) -> FireMonthlyPoint | None:
    """Fetch latest `gold.wealth_fire_analytics` row on or before `end_date`."""
    if not table_exists(duck_reader, "gold", "wealth_fire_analytics"):
        return None
    row = duck_reader.fetch_one(
        """
        SELECT *
        FROM gold.wealth_fire_analytics
        WHERE "MONTH_START_DATE" <= ?
        ORDER BY "MONTH_START_DATE" DESC
        LIMIT 1;
        """,
        (end_date,),
    )
    return _row_to_fire_point(row) if row else None


def build_fire_kpis(
    latest: FireMonthlyPoint | None,
    *,
    prior: FireMonthlyPoint | None = None,
) -> tuple[OverviewKPI, ...]:
    """Build 9 comprehensive KPIs from `gold.wealth_fire_analytics`."""
    if latest is None:
        return ()
    as_of = latest.month_end_date or latest.month_start_date
    proj_str = (
        latest.projected_fi_date_p50.strftime("%b %Y") if latest.projected_fi_date_p50 else "—"
    )
    implied_wealth = max(latest.target_fi_today - latest.fi_gap, 0.0)
    prior_implied = max(prior.target_fi_today - prior.fi_gap, 0.0) if prior else None

    return (
        OverviewKPI(
            kpi_id="fire_target_today",
            label="Target FI Corpus (Today)",
            value=latest.target_fi_today,
            format=MetricFormat.CURRENCY,
            delta_value=(latest.target_fi_today - prior.target_fi_today) if prior else None,
            delta_format=MetricFormat.CURRENCY,
            delta_label="vs Prior",
            subtitle="Canonical today's purchasing-power FI target",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=True,
            breakups=(
                OverviewBreakupItem(label="Lean FI", value=latest.lean_fi_today),
                OverviewBreakupItem(label="Coast FI", value=latest.coast_fi_today),
                OverviewBreakupItem(
                    label="Future Nominal", value=latest.target_fi_total_future_nominal
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_implied_wealth",
            label="Current Wealth (FIRE Base)",
            value=implied_wealth,
            format=MetricFormat.CURRENCY,
            delta_value=(implied_wealth - prior_implied) if prior_implied is not None else None,
            delta_format=MetricFormat.CURRENCY,
            delta_label="vs Prior",
            subtitle="Net worth backing this FIRE simulation (Target - Gap)",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="Coverage %",
                    value=latest.current_fi_coverage_pct,
                    format=MetricFormat.PERCENT,
                ),
                OverviewBreakupItem(label="FI Gap", value=latest.fi_gap),
                OverviewBreakupItem(
                    label="3Y Real CAGR",
                    value=latest.real_nw_cagr_3y,
                    format=MetricFormat.PERCENT,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_coverage",
            label="FI Coverage %",
            value=latest.current_fi_coverage_pct,
            format=MetricFormat.PERCENT,
            delta_value=(
                (latest.current_fi_coverage_pct - prior.current_fi_coverage_pct)
                if (
                    prior
                    and latest.current_fi_coverage_pct is not None
                    and prior.current_fi_coverage_pct is not None
                )
                else None
            ),
            delta_format=MetricFormat.PERCENT,
            delta_label="vs Prior",
            subtitle="Current net worth share of Target FI Today",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=latest.current_fi_coverage_pct is not None,
            breakups=(
                OverviewBreakupItem(
                    label="FI Velocity", value=latest.fi_velocity, format=MetricFormat.PERCENT
                ),
                OverviewBreakupItem(
                    label="Real Return",
                    value=latest.real_return_assumed_pct,
                    format=MetricFormat.PERCENT,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_gap",
            label="FI Corpus Gap",
            value=latest.fi_gap,
            format=MetricFormat.CURRENCY,
            delta_value=(latest.fi_gap - prior.fi_gap) if prior else None,
            delta_format=MetricFormat.CURRENCY,
            delta_label="vs Prior",
            subtitle="Remaining corpus needed to reach Target FI",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=True,
            breakups=(
                OverviewBreakupItem(label="Monthly Trend", value=latest.fi_gap_monthly_trend),
                OverviewBreakupItem(
                    label="Linear (mos)",
                    value=latest.estimated_months_to_fi_linear,
                    format=MetricFormat.INTEGER,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_years_p50",
            label=f"Years to FI (P50: {proj_str})",
            value=latest.years_to_fi_p50,
            format=MetricFormat.NUMBER,
            subtitle="Median Monte Carlo horizon to Financial Independence",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=latest.years_to_fi_p50 is not None,
            breakups=(
                OverviewBreakupItem(
                    label="P10 (mos)",
                    value=latest.months_to_fi_aggressive_p10,
                    format=MetricFormat.INTEGER,
                ),
                OverviewBreakupItem(
                    label="P50 (mos)",
                    value=latest.months_to_fi_base_p50,
                    format=MetricFormat.INTEGER,
                ),
                OverviewBreakupItem(
                    label="P90 (mos)",
                    value=latest.months_to_fi_conservative_p90,
                    format=MetricFormat.INTEGER,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_probability",
            label="Success Probability %",
            value=latest.probability_of_success_pct,
            format=MetricFormat.PERCENT,
            delta_value=(
                (latest.probability_of_success_pct - prior.probability_of_success_pct)
                if (
                    prior
                    and latest.probability_of_success_pct is not None
                    and prior.probability_of_success_pct is not None
                )
                else None
            ),
            delta_format=MetricFormat.PERCENT,
            delta_label="vs Prior",
            subtitle="Monte Carlo probability of sustaining retirement",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=latest.probability_of_success_pct is not None,
            breakups=(
                OverviewBreakupItem(
                    label="Real Return",
                    value=latest.real_return_assumed_pct,
                    format=MetricFormat.PERCENT,
                ),
                OverviewBreakupItem(
                    label="SWR Now",
                    value=latest.withdrawal_rate_if_retired_now,
                    format=MetricFormat.PERCENT,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_runway_p50",
            label="Post-Retire Runway (P50)",
            value=latest.runway_months_base_p50,
            format=MetricFormat.NUMBER,
            unit_suffix="m",
            subtitle="Simulated base-case retirement runway",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=latest.runway_months_base_p50 is not None,
            breakups=(
                OverviewBreakupItem(
                    label="Stressed P10",
                    value=latest.runway_months_stressed_p10,
                    format=MetricFormat.NUMBER,
                ),
                OverviewBreakupItem(
                    label="Linear", value=latest.runway_months_linear, format=MetricFormat.NUMBER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_avg_income",
            label="Monthly Income (Latest)",
            value=latest.total_income,
            format=MetricFormat.CURRENCY,
            delta_value=(latest.total_income - prior.total_income) if prior else None,
            delta_format=MetricFormat.CURRENCY,
            delta_label="vs Prior",
            subtitle="Total monthly income recorded in FIRE run",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=True,
            breakups=(
                OverviewBreakupItem(label="Cash Income", value=latest.total_cash_income),
                OverviewBreakupItem(
                    label="Save % Actual",
                    value=latest.savings_rate_actual,
                    format=MetricFormat.PERCENT,
                ),
                OverviewBreakupItem(
                    label="Save % Req",
                    value=latest.savings_rate_required,
                    format=MetricFormat.PERCENT,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_avg_spend",
            label="Trailing 12M Avg Spend",
            value=latest.trailing_12m_avg_spend,
            format=MetricFormat.CURRENCY,
            delta_value=(
                (latest.trailing_12m_avg_spend - prior.trailing_12m_avg_spend) if prior else None
            ),
            delta_format=MetricFormat.CURRENCY,
            delta_label="vs Prior",
            subtitle="Baseline monthly spend used for FI corpus target",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=True,
            breakups=(
                OverviewBreakupItem(label="6M Avg Spend", value=latest.trailing_6m_avg_spend),
                OverviewBreakupItem(label="Annualized", value=latest.trailing_12m_avg_spend * 12.0),
                OverviewBreakupItem(
                    label="SWR Now",
                    value=latest.withdrawal_rate_if_retired_now,
                    format=MetricFormat.PERCENT,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_avg_savings",
            label="Trailing 12M Avg Savings",
            value=latest.trailing_12m_avg_savings,
            format=MetricFormat.CURRENCY,
            delta_value=(
                (latest.trailing_12m_avg_savings - prior.trailing_12m_avg_savings)
                if prior
                else None
            ),
            delta_format=MetricFormat.CURRENCY,
            delta_label="vs Prior",
            subtitle="Monthly savings capacity feeding simulation",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=True,
            breakups=(
                OverviewBreakupItem(label="6M Avg Savings", value=latest.trailing_6m_avg_savings),
                OverviewBreakupItem(
                    label="Actual Rate",
                    value=latest.savings_rate_actual,
                    format=MetricFormat.PERCENT,
                ),
                OverviewBreakupItem(
                    label="Required Rate",
                    value=latest.savings_rate_required,
                    format=MetricFormat.PERCENT,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_wealth_velocity",
            label="Wealth Velocity (Monthly)",
            value=latest.wealth_velocity,
            format=MetricFormat.CURRENCY,
            subtitle="Monthly net worth growth pace",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=latest.wealth_velocity is not None,
            breakups=(
                OverviewBreakupItem(label="Accel.", value=latest.wealth_acceleration),
                OverviewBreakupItem(
                    label="FI Velocity", value=latest.fi_velocity, format=MetricFormat.PERCENT
                ),
                OverviewBreakupItem(
                    label="3Y Real CAGR",
                    value=latest.real_nw_cagr_3y,
                    format=MetricFormat.PERCENT,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="fire_terminal_p50",
            label="Terminal Wealth (P50 Nominal)",
            value=latest.terminal_wealth_nominal_p50,
            format=MetricFormat.CURRENCY,
            subtitle="Median simulated end-of-horizon wealth",
            source_table="gold.wealth_fire_analytics",
            as_of_date=as_of,
            drill_route="/fire",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="Future Target", value=latest.target_fi_total_future_nominal
                ),
                OverviewBreakupItem(
                    label="Real Return",
                    value=latest.real_return_assumed_pct,
                    format=MetricFormat.PERCENT,
                ),
            ),
        ),
    )


def _sim_row_to_record(
    r: dict[str, Any],
    source: str,
) -> SimulationRunRecord:
    return SimulationRunRecord(
        simulation_id=str(r.get("simulation_id") or ""),
        run_id=str(r.get("run_id") or ""),
        created_at=str(r.get("created_at") or ""),
        root_seed=str(r.get("root_seed") or ""),
        iterations=int(r["iterations"]) if r.get("iterations") is not None else None,
        horizon=int(r["horizon"]) if r.get("horizon") is not None else None,
        input_as_of_date=as_date(r.get("input_as_of_date")),
        model_implementation_version=str(r.get("model_implementation_version") or ""),
        model_fingerprint=str(r.get("model_fingerprint") or ""),
        input_fingerprint=str(r.get("input_fingerprint") or ""),
        result_fingerprint=str(r.get("result_fingerprint") or ""),
        contract_registry_fingerprint=str(r.get("contract_registry_fingerprint") or ""),
        settings_snapshot_id=str(r.get("settings_snapshot_id") or ""),
        rules_snapshot_id=str(r.get("rules_snapshot_id") or ""),
        status=str(r.get("status") or ""),
        source="meta" if source == "meta" else "control_plane",
    )


def query_latest_simulation_meta(duck_reader: DuckDBQueryPort) -> SimulationRunRecord | None:
    """Fetch `meta.m_Latest_Simulation` from DuckDB."""
    if not table_exists(duck_reader, "meta", "m_Latest_Simulation"):
        return None
    row = duck_reader.fetch_one("SELECT * FROM meta.m_Latest_Simulation LIMIT 1;")
    return _sim_row_to_record(dict(row), "meta") if row else None


def query_cp_simulation_runs(control_plane_path: Path) -> tuple[SimulationRunRecord, ...]:
    """Fetch `cp_simulation_runs` from Control Plane SQLite (read-only)."""
    if not control_plane_path.exists():
        return ()
    con = sqlite3.connect(f"file:{control_plane_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cp_simulation_runs';"
        ).fetchone()
        if not exists:
            return ()
        rows = con.execute(
            "SELECT * FROM cp_simulation_runs ORDER BY created_at DESC LIMIT 50;"
        ).fetchall()
        return tuple(_sim_row_to_record(dict(r), "control_plane") for r in rows)
    finally:
        con.close()
