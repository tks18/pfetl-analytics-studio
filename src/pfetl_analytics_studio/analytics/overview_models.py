"""Typed DTOs and lightweight date/numeric coercion helpers for Overview analytics.

Owns
----
- `OverviewBreakupItem`, `OverviewKPI`, `OverviewRunHealth`, and `OverviewSnapshot`.
- Chart point DTOs (`NetWorthPoint`, `CashflowPoint`, `PortfolioPoint`, `FirePoint`,
  `CategoryBreakupPoint`, `AssetClassPoint`).
- Pure numeric/date coercion and chart window boundary helpers.

Does not own
------------
- DuckDB/Control Plane SQL execution (`analytics/overview.py`) or NiceGUI widgets.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.semantic import FilterField, FilterSet, MetricFormat


def as_float(value: Any) -> float | None:
    """Safely convert numeric database values into `float`, returning `None` for null/NaN."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num):
        return None
    return num


def as_date(value: Any) -> date | None:
    """Convert DuckDB date/datetime/ISO strings cleanly to `datetime.date`."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def shift_months_start(anchor: date, months_back: int) -> date:
    """Return the first day of the calendar month `months_back` prior to `anchor`."""
    total_months = anchor.year * 12 + (anchor.month - 1) - months_back
    target_year, target_month_zero = divmod(total_months, 12)
    return date(target_year, target_month_zero + 1, 1)


def chart_window_start(start_date: date, end_date: date) -> tuple[date, str]:
    """Expand single/two-month KPI windows to a trailing 12M chart trajectory."""
    if (end_date - start_date).days >= 60:
        return start_date, f"{start_date.strftime('%b %Y')} – {end_date.strftime('%b %Y')}"
    chart_start = shift_months_start(end_date, 11)
    return chart_start, f"Trailing 12M to {end_date.strftime('%b %Y')}"


def slice_filters(filters: FilterSet, allowed_map: dict[FilterField, str]) -> FilterSet:
    """Extract subset of `FilterSet` applicable to a specific Gold table cleanly."""
    matched = tuple(spec for spec in filters.filters if spec.field in allowed_map)
    return FilterSet(filters=matched)


class OverviewBreakupItem(BaseModel):
    """Structured inline sub-metric displayed cleanly inside a KPI card."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: float | date | str | None = None
    format: MetricFormat = MetricFormat.CURRENCY
    unit_suffix: str = ""


class OverviewKPI(BaseModel):
    """Single KPI card payload carrying headline value, breakups, source table, and drill route."""

    model_config = ConfigDict(frozen=True)

    kpi_id: str
    label: str
    value: float | date | str | None = None
    format: MetricFormat
    unit_suffix: str = ""
    subtitle: str = ""
    source_table: str
    as_of_date: date | None = None
    drill_route: str
    delta_value: float | None = None
    delta_format: MetricFormat | None = None
    delta_label: str | None = None
    delta_positive_is_good: bool = True
    status_badge: str | None = None
    status_tone: str = "neutral"
    available: bool = True
    tooltip: str = ""
    breakups: tuple[OverviewBreakupItem, ...] = ()


class NetWorthPoint(BaseModel):
    """Monthly net worth balance and attribution decomposition point."""

    model_config = ConfigDict(frozen=True)

    month_end: date
    net_worth_market: float
    net_worth_book: float | None = None
    net_savings_flow: float | None = None
    market_return_flow: float | None = None
    attribution_residual: float | None = None


class CashflowPoint(BaseModel):
    """Monthly household income, expense, deployment, and net cashflow point."""

    model_config = ConfigDict(frozen=True)

    month_end: date
    income: float
    expense: float
    core_expense: float | None = None
    net_cashflow: float
    savings_rate: float | None = None
    investment_rate: float | None = None
    deployed: float | None = None
    redeemed: float | None = None
    net_investment_flow: float | None = None


class PortfolioPoint(BaseModel):
    """Portfolio valuation and return comparison point anchored on `Closing_Date`."""

    model_config = ConfigDict(frozen=True)

    closing_date: date
    current_value: float
    invested_value: float | None = None
    xirr: float | None = None
    bm_xirr: float | None = None
    active_return: float | None = None


class FirePoint(BaseModel):
    """Monthly Financial Independence progress and simulation trajectory point."""

    model_config = ConfigDict(frozen=True)

    month_end: date
    fi_coverage: float | None = None
    success_probability: float | None = None
    target_fi_today: float | None = None
    projected_fi_date: date | None = None


class CategoryBreakupPoint(BaseModel):
    """Ranked income or expense group/category total for the selected period."""

    model_config = ConfigDict(frozen=True)

    label: str
    group: str
    amount: float
    bucket: str


class AssetClassPoint(BaseModel):
    """Instrument class valuation, weight, XIRR, and benchmark alpha summary."""

    model_config = ConfigDict(frozen=True)

    instrument_class: str
    closing_date: date
    current_value: float
    invested_value: float | None = None
    unrealized_pl: float | None = None
    weight: float | None = None
    xirr: float | None = None
    bm_xirr: float | None = None
    active_return: float | None = None


class WealthAssetSplitPoint(BaseModel):
    """Net worth split by asset group from `gold.wealth_asset_breakdown`."""

    model_config = ConfigDict(frozen=True)

    asset_group: str
    market_value: float
    book_value: float
    unrealized_pl: float | None = None
    cumulative_savings: float | None = None
    all_time_high: float | None = None
    weight: float | None = None


class AttributionBridgeSummary(BaseModel):
    """Period-level opening-to-closing market net worth attribution bridge."""

    model_config = ConfigDict(frozen=True)

    opening_net_worth: float
    net_savings_flow: float
    market_return_flow: float
    attribution_residual: float
    closing_net_worth: float
    closing_book_worth: float | None = None
    unrealized_cushion: float | None = None


class OverviewRunHealth(BaseModel):
    """Compact PFETL Control Plane run health and Meta warehouse row-count summary."""

    model_config = ConfigDict(frozen=True)

    latest_run_id: str | None = None
    status: str | None = None
    commit_state: str | None = None
    started_at: str | None = None
    application_version: str | None = None
    warehouse_rows_total: int = 0
    gold_tables_populated: int = 0
    compatibility_status: str = "UNKNOWN"
    surfaces_discovered: int = 0
    surfaces_expected: int = 82


class OverviewSnapshot(BaseModel):
    """Complete immutable cockpit snapshot consumed by the Overview page renderer."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    chart_window_label: str = ""
    primary_kpis: tuple[OverviewKPI, ...] = ()
    secondary_kpis: tuple[OverviewKPI, ...] = ()
    net_worth_series: tuple[NetWorthPoint, ...] = ()
    cashflow_series: tuple[CashflowPoint, ...] = ()
    portfolio_series: tuple[PortfolioPoint, ...] = ()
    fire_series: tuple[FirePoint, ...] = ()
    income_breakup: tuple[CategoryBreakupPoint, ...] = ()
    expense_breakup: tuple[CategoryBreakupPoint, ...] = ()
    asset_class_mix: tuple[AssetClassPoint, ...] = ()
    wealth_split: tuple[WealthAssetSplitPoint, ...] = ()
    attribution_bridge: AttributionBridgeSummary | None = None
    run_health: OverviewRunHealth = OverviewRunHealth()
    diagnostics: tuple[str, ...] = ()
