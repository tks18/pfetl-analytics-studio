"""FIRE page module — financial independence targets, runway, and simulations."""

from __future__ import annotations

from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.pages import render_domain_workspace


def render_fire_page(context: AppContext) -> None:
    """Render the FIRE workspace from shared `AppContext`."""
    render_domain_workspace(
        context=context,
        route=ROUTES_BY_ID["fire"],
        workspace_views=(
            "FI Corpus Targets & Gap",
            "Projected P10/P50/P90 Dates",
            "Success Probability & Runway",
            "Simulation Reproducibility Provenance",
        ),
        upstream_sources=(
            "gold.wealth_fire_analytics",
            "meta.m_Latest_Simulation",
            "cp_simulation_runs",
        ),
    )
