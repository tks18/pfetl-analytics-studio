"""Operations page module — runs, sync health, providers, failures, and forensic logs."""

from __future__ import annotations

from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.pages import render_domain_workspace


def render_operations_page(context: AppContext) -> None:
    """Render the Operations workspace from shared `AppContext`."""
    render_domain_workspace(
        context=context,
        route=ROUTES_BY_ID["operations"],
        workspace_views=(
            "Run History & Sync Health",
            "Sources & Artifacts Registry",
            "Provider Requests & Failures",
            "Forensic JSONL Logs & Evidence Download",
        ),
        upstream_sources=(
            "cp_runs",
            "cp_run_sync_summaries",
            "cp_artifact_run_events",
            "cp_external_provider_requests",
            "cp_run_failures",
            "cp_file_registry",
            "cp_file_payloads",
        ),
    )
