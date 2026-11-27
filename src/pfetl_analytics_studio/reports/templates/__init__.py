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
from pfetl_analytics_studio.reports.templates.expense import (
    EXPENSE_LIFESTYLE_REVIEW_DEF,
    register_expense_sources,
)
from pfetl_analytics_studio.reports.templates.financial_year import (
    FINANCIAL_YEAR_REVIEW_DEF,
    register_financial_year_sources,
)
from pfetl_analytics_studio.reports.templates.income import (
    INCOME_RESILIENCE_REVIEW_DEF,
    register_income_sources,
)
from pfetl_analytics_studio.reports.templates.monthly import (
    MONTHLY_FINANCIAL_REVIEW_DEF,
    register_monthly_sources,
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


__all__ = [
    "CASHFLOW_BUDGET_REVIEW_DEF",
    "EXPENSE_LIFESTYLE_REVIEW_DEF",
    "FINANCIAL_YEAR_REVIEW_DEF",
    "INCOME_RESILIENCE_REVIEW_DEF",
    "MONTHLY_FINANCIAL_REVIEW_DEF",
    "NET_WORTH_WEALTH_ATTRIBUTION_DEF",
    "register_cashflow_expense_income_templates",
    "register_monthly_fy_wealth_templates",
]
