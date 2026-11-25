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


class ExpenseMonthlyPoint(BaseModel):
    """Monthly expense composition, cash/non-cash split, and real core trajectory."""

    model_config = ConfigDict(frozen=True)

    month_end: date
    total_expense: float
    core_expense: float | None = None
    non_core_expense: float | None = None
    cash_expense: float | None = None
    non_cash_expense: float | None = None
    inflation_adjusted_core: float | None = None
    real_core_growth_pct: float | None = None
    trailing_3m_avg: float | None = None
    expense_to_nw_ratio: float | None = None


class ExpenseCategoryRow(BaseModel):
    """Category-level expense rollup with Spend_Type, Core/Discretionary/Inv flags & YTD."""

    model_config = ConfigDict(frozen=True)

    category_id: str
    category_name: str
    category_group: str
    spend_type: str = "Variable"
    is_core: bool = False
    is_investment: bool = False
    is_discretionary: bool = False
    amount: float
    cash_amount: float | None = None
    non_cash_amount: float | None = None
    trailing_3m_avg: float | None = None
    cumulative_ytd: float | None = None
    share_pct: float | None = None


class ExpenseGroupRow(BaseModel):
    """Group-level expense rollup (Rents, Food, Gadgets, Insurances, etc.)."""

    model_config = ConfigDict(frozen=True)

    category_group: str
    spend_type: str = "Variable"
    is_core: bool = False
    amount: float
    cash_amount: float | None = None
    non_cash_amount: float | None = None
    share_pct: float | None = None
    category_count: int = 0


class ExpenseAccountMixPoint(BaseModel):
    """Expense outflow breakdown by payment/funding account."""

    model_config = ConfigDict(frozen=True)

    account_name: str
    amount: float
    share_pct: float | None = None
    tx_count: int = 0


class ExpenseHeatmapData(BaseModel):
    """Month × Expense Group spend intensity matrix for pattern heatmap."""

    model_config = ConfigDict(frozen=True)

    months: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    matrix: tuple[tuple[float | None, ...], ...] = ()


class ExpenseTransactionRow(BaseModel):
    """Single Silver expense transaction row joined with category, group, and account."""

    model_config = ConfigDict(frozen=True)

    uid: str
    tx_date: date
    category_name: str
    category_group: str
    parent_category: str
    account_name: str
    description: str
    paid: str = ""
    currency_id: str
    base_amount: float
    account_amount: float | None = None
    local_amount: float | None = None
    exch_rate: float | None = None
    is_core: bool = False


class ExpenseTransactionPage(BaseModel):
    """Bounded, paginated Silver expense transaction drill result."""

    model_config = ConfigDict(frozen=True)

    rows: tuple[ExpenseTransactionRow, ...] = ()
    total_count: int = 0
    total_base_amount: float = 0.0
    page: int = 1
    page_size: int = 50


class HouseholdExpenseSnapshot(BaseModel):
    """Complete immutable Expense cockpit snapshot consumed by the Household page."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    chart_window_label: str = ""
    primary_kpis: tuple[OverviewKPI, ...] = ()
    secondary_kpis: tuple[OverviewKPI, ...] = ()
    monthly_series: tuple[ExpenseMonthlyPoint, ...] = ()
    category_rows: tuple[ExpenseCategoryRow, ...] = ()
    group_rows: tuple[ExpenseGroupRow, ...] = ()
    account_mix: tuple[ExpenseAccountMixPoint, ...] = ()
    heatmap: ExpenseHeatmapData = ExpenseHeatmapData()
    total_spend: float = 0.0
    core_spend: float = 0.0
    non_core_spend: float = 0.0
    cash_spend: float = 0.0
    non_cash_spend: float = 0.0
    run_health: OverviewRunHealth = OverviewRunHealth()
    diagnostics: tuple[str, ...] = ()


class BudgetHealthFlags(BaseModel):
    """Canonical Gold budget health and overspend/underfunded flags."""

    model_config = ConfigDict(frozen=True)

    is_core_overspent: bool | None = None
    is_non_core_overspent: bool | None = None
    is_investment_underfunded: bool | None = None
    is_income_volatile: bool | None = None
    is_budget_healthy: bool | None = None


class BudgetMonthlyPoint(BaseModel):
    """Single monthly row from `gold.forecast_budget_variance`."""

    model_config = ConfigDict(frozen=True)

    month_end: date
    year_month: str
    actual_income: float | None = None
    budget_income: float | None = None
    income_variance: float | None = None
    actual_core: float | None = None
    budget_core: float | None = None
    core_variance: float | None = None
    actual_non_core: float | None = None
    budget_non_core: float | None = None
    non_core_variance: float | None = None
    actual_investment: float | None = None
    budget_investment: float | None = None
    investment_variance: float | None = None
    investment_deployed: float | None = None
    investment_redeemed: float | None = None
    budget_surplus_buffer: float | None = None
    actual_savings: float | None = None
    rule_core_pct: float | None = None
    rule_non_core_pct: float | None = None
    rule_investment_pct: float | None = None
    rule_buffer_pct: float | None = None
    emergency_target: float | None = None
    emergency_actual: float | None = None
    emergency_gap: float | None = None
    runway_months: float | None = None
    target_runway_months: float | None = None
    flags: BudgetHealthFlags = BudgetHealthFlags()


class HouseholdBudgetSnapshot(BaseModel):
    """Complete immutable Budget Variance snapshot consumed by Household."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    chart_window_label: str = ""
    kpis: tuple[OverviewKPI, ...] = ()
    latest_flags: BudgetHealthFlags = BudgetHealthFlags()
    monthly_series: tuple[BudgetMonthlyPoint, ...] = ()
    rules_overlay_summary: str | None = None
    total_actual_income: float = 0.0
    total_budget_income: float = 0.0
    total_actual_core: float = 0.0
    total_budget_core: float = 0.0
    total_actual_non_core: float = 0.0
    total_budget_non_core: float = 0.0
    total_actual_investment: float = 0.0
    total_budget_investment: float = 0.0
    total_budget_buffer: float = 0.0
    total_actual_savings: float = 0.0
    run_health: OverviewRunHealth = OverviewRunHealth()
    diagnostics: tuple[str, ...] = ()


class CashflowActivityPoint(BaseModel):
    """Single monthly row from `gold.cashflow_activity_summary`."""

    model_config = ConfigDict(frozen=True)

    month_end: date
    year_month: str
    opening_cash: float = 0.0
    closing_cash: float = 0.0
    operating_inflows: float | None = None
    operating_outflows: float | None = None
    net_operating: float = 0.0
    investing_inflows: float | None = None
    investing_outflows: float | None = None
    net_investing: float = 0.0
    financing_inflows: float | None = None
    financing_outflows: float | None = None
    net_financing: float = 0.0
    transfer_inflows: float | None = None
    transfer_outflows: float | None = None
    net_transfers: float = 0.0
    total_cash_inflow: float | None = None
    total_cash_outflow: float | None = None
    non_cash_adjustments: float | None = None
    calculated_net_cashflow: float = 0.0
    actual_cash_change: float = 0.0
    unreconciled_difference: float = 0.0
    is_reconciled: bool = True


class TransferTransactionRow(BaseModel):
    """Single Silver transfer transaction row for cashflow activity drill."""

    model_config = ConfigDict(frozen=True)

    uid: str
    tx_date: date
    adjusted_date: date | None = None
    asset_id: str
    asset_name: str
    to_asset_id: str | None = None
    to_asset_name: str | None = None
    transfer_type: str
    transaction_type: str
    activity_bucket: str
    amount: float
    currency_id: str
    paid: str
    description: str


class TransferTransactionPage(BaseModel):
    """Bounded, paginated Silver transfer transaction drill result."""

    model_config = ConfigDict(frozen=True)

    rows: tuple[TransferTransactionRow, ...] = ()
    total_count: int = 0
    total_base_amount: float = 0.0
    page: int = 1
    page_size: int = 50


class HouseholdCashflowSnapshot(BaseModel):
    """Complete immutable Cashflow Activity Statement snapshot consumed by Household."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    chart_window_label: str = ""
    kpis: tuple[OverviewKPI, ...] = ()
    monthly_series: tuple[CashflowActivityPoint, ...] = ()
    opening_cash: float = 0.0
    closing_cash: float = 0.0
    net_operating: float = 0.0
    net_investing: float = 0.0
    net_financing: float = 0.0
    net_transfers: float = 0.0
    calculated_net_cashflow: float = 0.0
    actual_cash_change: float = 0.0
    unreconciled_difference: float = 0.0
    is_reconciled: bool = True
    run_health: OverviewRunHealth = OverviewRunHealth()
    diagnostics: tuple[str, ...] = ()
