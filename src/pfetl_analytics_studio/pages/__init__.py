"""Pages package for PFETL Analytics Studio.

Ownership: Thin NiceGUI composition and presentation layouts.
Forbidden: Does not execute raw SQL, open databases directly, or compute financial formulas.
"""

from __future__ import annotations

from collections.abc import Sequence

from nicegui import ui

from pfetl_analytics_studio.context import AppContext, NavRoute
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    empty_state_panel,
    error_state_panel,
    status_badge,
)


def render_domain_workspace(
    context: AppContext,
    route: NavRoute,
    workspace_views: Sequence[str],
    upstream_sources: Sequence[str],
) -> None:
    """Render standardized domain workspace shell handling profile, lock, and compatibility states.

    Parameters
    ----------
    context : AppContext
        Immutable global presentation context for the active session.
    route : NavRoute
        Route metadata for the target navigation page.
    workspace_views : Sequence[str]
        Descriptive list of domain views and panels belonging to this route.
    upstream_sources : Sequence[str]
        Upstream Gold, Silver, Meta, or Control Plane tables backing this route.
    """
    with ui.column().classes("w-full gap-4"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.row().classes("items-center gap-3"):
                with (
                    ui.element("div")
                    .classes("w-9 h-9 rounded-lg flex items-center justify-center shrink-0")
                    .style(
                        "background-color: rgba(59, 130, 246, 0.14); "
                        "border: 1px solid rgba(59, 130, 246, 0.25);"
                    )
                ):
                    ui.icon(route.icon, size="20px").style(f"color: {StudioColor.ACCENT}")
                with ui.column().classes("gap-0"):
                    ui.label(route.label).classes("text-2xl font-bold tracking-tight").style(
                        f"color: {StudioColor.TEXT}"
                    )
                    ui.label(route.description).classes("text-sm").style(
                        f"color: {StudioColor.MUTED}"
                    )

        if context.profile is None:
            empty_state_panel(
                title=f"Select a PFETL Profile to Explore {route.label}",
                message=(
                    "No PFETL Settings / FinancialRules pair is active. Use 'Open Profile' "
                    "in the top bar or launch with `--settings` and `--rules`."
                ),
                icon="folder_open",
                hint=f"Workspace views: {', '.join(workspace_views)}.",
                action_label="Go to Overview Profile Picker",
                action_target="/overview",
            )
            return

        if route.requires_warehouse and context.is_warehouse_locked:
            empty_state_panel(
                title=f"{route.label} Paused While PFETL Lock Is Active",
                message=(
                    "PFETL Core holds `pipeline.lock` on the DuckDB warehouse. Studio defers "
                    "warehouse queries until Core finishes writing cleanly."
                ),
                icon="lock",
                hint="Control Plane run telemetry and logs remain available under Operations.",
                action_label="Inspect Operations & Runs",
                action_target="/operations",
            )
            return

        if route.requires_warehouse and context.is_incompatible and context.compatibility:
            diagnostics: list[str] = list(context.compatibility.diagnostics[:6])
            error_state_panel(
                title=f"{route.label} Blocked by Compatibility Check",
                message=(
                    "Required upstream PFETL surfaces or columns did not match expected "
                    "catalog contracts. Operations and profile diagnostics remain accessible."
                ),
                details=diagnostics,
            )
            return

        empty_state_panel(
            title=f"{route.label} Workspace Connected",
            message=(
                f"Connected to profile '{context.profile.label}' with active run provenance "
                f"and validated period boundaries."
            ),
            icon=route.icon,
            hint=f"Views: {', '.join(workspace_views)}",
        )

        with ui.card().classes("studio-card w-full p-4 rounded-xl gap-2"):
            ui.label("Authoritative Upstream Surfaces").classes(
                "text-xs font-semibold uppercase tracking-wider"
            ).style(f"color: {StudioColor.MUTED}")
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                for src in upstream_sources:
                    status_badge(src, tone=BadgeTone.NEUTRAL, outline=True)
