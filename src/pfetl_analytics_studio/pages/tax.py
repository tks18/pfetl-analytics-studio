"""Tax page module — FY tax cockpit, reconciliation, events, and harvesting."""

from __future__ import annotations

from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.pages import render_domain_workspace


def render_tax_page(context: AppContext) -> None:
    """Render the Tax workspace from shared `AppContext`."""
    render_domain_workspace(
        context=context,
        route=ROUTES_BY_ID["tax"],
        workspace_views=(
            "FY Tax Summary",
            "Income Head Breakdown",
            "Ledger vs Quant Reconciliation",
            "Indicative Harvesting & LTCG Exposure",
        ),
        upstream_sources=(
            "gold.tax_financial_year_summary",
            "gold.tax_income_breakdown",
            "gold.tax_reconciliation",
            "silver.fact_tax_events",
            "silver.ref_financial_year_parameters",
        ),
    )
