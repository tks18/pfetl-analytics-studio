"""Reports page module — prebuilt financial reports, custom builder, and exporters."""

from __future__ import annotations

from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.pages import render_domain_workspace


def render_reports_page(context: AppContext) -> None:
    """Render the Reports workspace from shared `AppContext`."""
    render_domain_workspace(
        context=context,
        route=ROUTES_BY_ID["reports"],
        workspace_views=(
            "15 Prebuilt Reports Catalog",
            "Custom Report Builder",
            "PDF & Excel Deterministic Exporters",
            "Report Run Provenance History",
        ),
        upstream_sources=(
            "All registered semantic datasets",
            "cp_settings_snapshots & cp_rules_snapshots",
            "Studio saved_reports & report_runs",
        ),
    )
