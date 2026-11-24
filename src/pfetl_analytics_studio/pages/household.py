"""Household page module — income, expenses, budget variance, and cashflow."""

from __future__ import annotations

from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.pages import render_domain_workspace


def render_household_page(context: AppContext) -> None:
    """Render the Household workspace from shared `AppContext`."""
    render_domain_workspace(
        context=context,
        route=ROUTES_BY_ID["household"],
        workspace_views=(
            "Income Resilience",
            "Core & Lifestyle Expenses",
            "Budget Variance",
            "Cashflow Activity",
            "Transaction Drill",
        ),
        upstream_sources=(
            "gold.cashflow_income_breakdown",
            "gold.cashflow_expense_breakdown",
            "gold.forecast_budget_variance",
            "gold.cashflow_activity_summary",
            "silver.fact_income_transactions",
        ),
    )
