"""Wealth page module — balance sheet, liquidity runway, drawdown, and net worth."""

from __future__ import annotations

from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.pages import render_domain_workspace


def render_wealth_page(context: AppContext) -> None:
    """Render the Wealth workspace from shared `AppContext`."""
    render_domain_workspace(
        context=context,
        route=ROUTES_BY_ID["wealth"],
        workspace_views=(
            "Asset-Level Balances",
            "Liquid vs Illiquid Split",
            "Drawdown from Peak",
            "Net-Worth Attribution",
        ),
        upstream_sources=(
            "gold.wealth_asset_breakdown",
            "gold.core_monthly_fact",
            "silver.dim_asset_subcategory",
            "silver.fact_opening_balances",
        ),
    )
