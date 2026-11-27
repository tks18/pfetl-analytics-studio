"""Typed DTOs for FIRE & Simulation Analytics."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.analytics.overview_models import OverviewKPI


class FireMonthlyPoint(BaseModel):
    """All 35 columns from one monthly row of `gold.wealth_fire_analytics`."""

    model_config = ConfigDict(frozen=True)

    month_start_date: date
    month_end_date: date | None = None
    year_month: str = ""
    projected_fi_date_p50: date | None = None
    total_income: float = 0.0
    total_cash_income: float = 0.0
    trailing_6m_avg_spend: float = 0.0
    trailing_12m_avg_spend: float = 0.0
    trailing_6m_avg_savings: float = 0.0
    trailing_12m_avg_savings: float = 0.0
    target_fi_today: float = 0.0
    coast_fi_today: float = 0.0
    lean_fi_today: float = 0.0
    target_fi_total_future_nominal: float = 0.0
    fi_gap: float = 0.0
    fi_gap_monthly_trend: float | None = None
    terminal_wealth_nominal_p50: float = 0.0
    real_return_assumed_pct: float | None = None
    wealth_velocity: float | None = None
    wealth_acceleration: float | None = None
    real_nw_cagr_3y: float | None = None
    current_fi_coverage_pct: float | None = None
    estimated_months_to_fi_linear: float | None = None
    months_to_fi_conservative_p90: float | None = None
    months_to_fi_base_p50: float | None = None
    months_to_fi_aggressive_p10: float | None = None
    probability_of_success_pct: float | None = None
    years_to_fi_p50: float | None = None
    runway_months_linear: float | None = None
    runway_months_stressed_p10: float | None = None
    runway_months_base_p50: float | None = None
    withdrawal_rate_if_retired_now: float | None = None
    savings_rate_required: float | None = None
    savings_rate_actual: float | None = None
    fi_velocity: float | None = None


class SimulationRunRecord(BaseModel):
    """Simulation provenance record from `meta.m_Latest_Simulation` / `cp_simulation_runs`."""

    model_config = ConfigDict(frozen=True)

    simulation_id: str
    run_id: str = ""
    created_at: str = ""
    root_seed: str = ""
    iterations: int | None = None
    horizon: int | None = None
    input_as_of_date: date | None = None
    model_implementation_version: str = ""
    model_fingerprint: str = ""
    input_fingerprint: str = ""
    result_fingerprint: str = ""
    contract_registry_fingerprint: str = ""
    settings_snapshot_id: str = ""
    rules_snapshot_id: str = ""
    status: str = ""
    source: Literal["meta", "control_plane"] = "meta"


class FireSummarySnapshot(BaseModel):
    """Complete FIRE planning snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    latest: FireMonthlyPoint | None = None
    history: tuple[FireMonthlyPoint, ...] = ()
    kpis: tuple[OverviewKPI, ...] = ()
    latest_simulation: SimulationRunRecord | None = None
    diagnostics: tuple[str, ...] = ()


class SimulationHistorySnapshot(BaseModel):
    """Simulation reproducibility provenance & FinancialRules context."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    latest_meta: SimulationRunRecord | None = None
    runs: tuple[SimulationRunRecord, ...] = ()
    rules_assumptions: dict[str, Any] = {}
    diagnostics: tuple[str, ...] = ()
