"""Explorer page module — 82-surface catalog, guided safe analytics, and read-only SQL."""

from __future__ import annotations

from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.pages import render_domain_workspace


def render_explorer_page(context: AppContext) -> None:
    """Render the Explorer workspace from shared `AppContext`."""
    render_domain_workspace(
        context=context,
        route=ROUTES_BY_ID["explorer"],
        workspace_views=(
            "82-Surface Data Catalog",
            "Guided Whitelisted Analytics",
            "Saved Views",
            "Guarded Read-Only SQL",
        ),
        upstream_sources=(
            "meta.m_Data_Contracts",
            "meta.m_Table_Row_Counts",
            "bronze.* (bounded evidence preview)",
            "silver.* & gold.* & cp_*",
        ),
    )
