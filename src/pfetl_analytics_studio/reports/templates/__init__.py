"""Built-in report templates package for PFETL Analytics Studio.

Owns
----
- Presentation-paced built-in `ReportDefinition` templates and their registered block sources.

Does not own
------------
- Direct DuckDB/SQLite connections, NiceGUI widgets, or financial methodology.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pfetl_analytics_studio.reports.templates.cashflow import (
    CASHFLOW_BUDGET_REVIEW_DEF,
    register_cashflow_sources,
)
from pfetl_analytics_studio.reports.templates.currency import (
    MULTI_CURRENCY_EXPOSURE_REVIEW_DEF,
    register_currency_sources,
)
from pfetl_analytics_studio.reports.templates.expense import (
    EXPENSE_LIFESTYLE_REVIEW_DEF,
    register_expense_sources,
)
from pfetl_analytics_studio.reports.templates.financial_year import (
    FINANCIAL_YEAR_REVIEW_DEF,
    register_financial_year_sources,
)
from pfetl_analytics_studio.reports.templates.fire import (
    FIRE_PLANNING_REVIEW_DEF,
    register_fire_sources,
)
from pfetl_analytics_studio.reports.templates.income import (
    INCOME_RESILIENCE_REVIEW_DEF,
    register_income_sources,
)
from pfetl_analytics_studio.reports.templates.investment_tax import (
    INVESTMENT_TAX_HARVESTING_DEF,
    register_investment_tax_sources,
)
from pfetl_analytics_studio.reports.templates.monthly import (
    MONTHLY_FINANCIAL_REVIEW_DEF,
    register_monthly_sources,
)
from pfetl_analytics_studio.reports.templates.performance import (
    INVESTMENT_PERFORMANCE_BENCHMARK_DEF,
    register_performance_sources,
)
from pfetl_analytics_studio.reports.templates.portfolio import (
    PORTFOLIO_REVIEW_DEF,
    register_portfolio_sources,
)
from pfetl_analytics_studio.reports.templates.tax_position import (
    TAX_POSITION_REVIEW_DEF,
    register_tax_position_sources,
)
from pfetl_analytics_studio.reports.templates.wealth import (
    NET_WORTH_WEALTH_ATTRIBUTION_DEF,
    register_wealth_sources,
)

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.registry import ReportRegistry


def register_monthly_fy_wealth_templates(registry: ReportRegistry) -> None:
    """Register the Phase 26 Monthly, FY, and Wealth built-in templates & sources."""
    register_monthly_sources(registry)
    register_financial_year_sources(registry)
    register_wealth_sources(registry)


def register_cashflow_expense_income_templates(registry: ReportRegistry) -> None:
    """Register the Phase 27 Cashflow, Expense, and Income built-in templates & sources."""
    register_cashflow_sources(registry)
    register_expense_sources(registry)
    register_income_sources(registry)


def register_investment_reports_templates(registry: ReportRegistry) -> None:
    """Register the Phase 28 Portfolio, Performance, and Currency built-in templates."""
    register_portfolio_sources(registry)
    register_performance_sources(registry)
    register_currency_sources(registry)


def register_tax_fire_templates(registry: ReportRegistry) -> None:
    """Register the Phase 29 Investment Tax, Tax Position, and FIRE built-in templates."""
    register_investment_tax_sources(registry)
    register_tax_position_sources(registry)
    register_fire_sources(registry)


__all__ = [
    "CASHFLOW_BUDGET_REVIEW_DEF",
    "EXPENSE_LIFESTYLE_REVIEW_DEF",
    "FINANCIAL_YEAR_REVIEW_DEF",
    "FIRE_PLANNING_REVIEW_DEF",
    "INCOME_RESILIENCE_REVIEW_DEF",
    "INVESTMENT_PERFORMANCE_BENCHMARK_DEF",
    "INVESTMENT_TAX_HARVESTING_DEF",
    "MONTHLY_FINANCIAL_REVIEW_DEF",
    "MULTI_CURRENCY_EXPOSURE_REVIEW_DEF",
    "NET_WORTH_WEALTH_ATTRIBUTION_DEF",
    "PORTFOLIO_REVIEW_DEF",
    "TAX_POSITION_REVIEW_DEF",
    "register_cashflow_expense_income_templates",
    "register_investment_reports_templates",
    "register_monthly_fy_wealth_templates",
    "register_tax_fire_templates",
]
