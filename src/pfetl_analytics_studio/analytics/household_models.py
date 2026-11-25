"""Typed DTOs for Household & Income Analytics.

Owns
----
- `IncomeMonthlyPoint`, `IncomeCategoryRow`, `IncomeGroupRow`, `IncomeAccountMixPoint`.
- `IncomeTaxabilitySummary`, `IncomeTransactionRow`, `IncomeTransactionPage`.
- `HouseholdIncomeSnapshot` consumed by `pages/household.py`.

Does not own
------------
- DuckDB SQL execution (`analytics/household_queries.py`) or NiceGUI widgets.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.analytics.overview_models import OverviewKPI, OverviewRunHealth


class IncomeMonthlyPoint(BaseModel):
    """Monthly income composition, smoothing, budget target, and resilience point."""

    model_config = ConfigDict(frozen=True)

    month_end: date
    total_income: float
    cash_income: float | None = None
    non_cash_income: float | None = None
    active_income: float | None = None
    passive_income: float | None = None
    dividend_income: float | None = None
    interest_income: float | None = None
    trailing_3m_avg: float | None = None
    budget_income: float | None = None
    cumulative_ytd: float | None = None
    active_dependency_pct: float | None = None
    passive_core_coverage: float | None = None


class IncomeCategoryRow(BaseModel):
    """Category-level income rollup with flags, 3M avg, YTD, and taxability."""

    model_config = ConfigDict(frozen=True)

    category_id: str
    category_name: str
    category_group: str
    amount: float
    trailing_3m_avg: float | None = None
    cumulative_ytd: float | None = None
    share_pct: float | None = None
    is_active: bool = False
    is_passive: bool = False
    is_dividend: bool = False
    is_interest: bool = False
    is_non_cash: bool = False
    taxability: str = "unclassified"


class IncomeGroupRow(BaseModel):
    """Group-level income rollup (Salary, Non-Cash, Other Sources, etc.)."""

    model_config = ConfigDict(frozen=True)

    category_group: str
    amount: float
    share_pct: float | None = None
    category_count: int = 0


class IncomeAccountMixPoint(BaseModel):
    """Income inflow breakdown by receiving account and taxability."""

    model_config = ConfigDict(frozen=True)

    account_name: str
    amount: float
    share_pct: float | None = None
    tx_count: int = 0


class IncomeTaxabilitySummary(BaseModel):
    """Taxable vs non-taxable income split from Silver income dimensions."""

    model_config = ConfigDict(frozen=True)

    taxable_amount: float = 0.0
    non_taxable_amount: float = 0.0
    unclassified_amount: float = 0.0
    taxable_share_pct: float | None = None


class IncomeTransactionRow(BaseModel):
    """Single Silver income transaction row joined with category and account labels."""

    model_config = ConfigDict(frozen=True)

    uid: str
    tx_date: date
    category_name: str
    category_group: str
    parent_category: str
    account_name: str
    description: str
    currency_id: str
    base_amount: float
    account_amount: float | None = None
    local_amount: float | None = None
    exch_rate: float | None = None
    taxability: str = "unclassified"
    is_active: bool = False
    is_passive: bool = False
    is_dividend: bool = False
    is_interest: bool = False
    is_non_cash: bool = False


class IncomeTransactionPage(BaseModel):
    """Bounded, paginated Silver income transaction drill result."""

    model_config = ConfigDict(frozen=True)

    rows: tuple[IncomeTransactionRow, ...] = ()
    total_count: int = 0
    total_base_amount: float = 0.0
    page: int = 1
    page_size: int = 50


class HouseholdIncomeSnapshot(BaseModel):
    """Complete immutable Income cockpit snapshot consumed by the Household page."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    chart_window_label: str = ""
    primary_kpis: tuple[OverviewKPI, ...] = ()
    secondary_kpis: tuple[OverviewKPI, ...] = ()
    monthly_series: tuple[IncomeMonthlyPoint, ...] = ()
    category_rows: tuple[IncomeCategoryRow, ...] = ()
    group_rows: tuple[IncomeGroupRow, ...] = ()
    account_mix: tuple[IncomeAccountMixPoint, ...] = ()
    taxability: IncomeTaxabilitySummary = IncomeTaxabilitySummary()
    gross_positive_income: float = 0.0
    salary_deductions: float = 0.0
    net_income_total: float = 0.0
    run_health: OverviewRunHealth = OverviewRunHealth()
    diagnostics: tuple[str, ...] = ()
