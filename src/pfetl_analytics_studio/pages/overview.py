"""Overview page module — monthly financial decision cockpit composition."""

from __future__ import annotations

from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.pages import render_domain_workspace


def render_overview_page(context: AppContext) -> None:
    """Render the Overview landing workspace from shared `AppContext`."""
    render_domain_workspace(
        context=context,
        route=ROUTES_BY_ID["overview"],
        workspace_views=(
            "Net Worth & Attribution",
            "Cashflow & Savings Rate",
            "Portfolio & XIRR Summary",
            "Indicative Tax & FI Coverage",
            "Freshness & Data Health",
        ),
        upstream_sources=(
            "gold.core_monthly_fact",
            "gold.cashflow_efficiency_analytics",
            "gold.investment_by_portfolio",
            "gold.wealth_fire_analytics",
            "gold.tax_financial_year_summary",
        ),
    )
