"""Investments page module — allocation, XIRR performance, benchmarks, FX, and lots."""

from __future__ import annotations

from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.pages import render_domain_workspace


def render_investments_page(context: AppContext) -> None:
    """Render the Investments workspace from shared `AppContext`."""
    render_domain_workspace(
        context=context,
        route=ROUTES_BY_ID["investments"],
        workspace_views=(
            "Portfolio & Allocations",
            "Benchmark & FX Attribution",
            "Lot Explorer",
            "Realized & Reconciliation",
            "Market Evidence",
        ),
        upstream_sources=(
            "gold.investment_portfolio_summary",
            "gold.investment_by_isin",
            "silver.fact_investment_lot_analytics",
            "silver.fact_investment_realized_events",
            "silver.fact_investment_reconciliation_events",
        ),
    )
