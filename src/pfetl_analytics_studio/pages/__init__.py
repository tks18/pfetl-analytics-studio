"""Pages package for PFETL Analytics Studio.

Ownership: Thin NiceGUI composition and presentation layouts.
Forbidden: Does not execute raw SQL, open databases directly, or compute financial formulas.
"""

from __future__ import annotations

from collections.abc import Sequence

from nicegui import ui

from pfetl_analytics_studio.context import AppContext, NavRoute
from pfetl_analytics_studio.semantic import format_period_label
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    empty_state_panel,
    error_state_panel,
    filter_chips_bar,
    kpi_card,
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
                ui.icon(route.icon, size="md").style(f"color: {StudioColor.ACCENT}")
                with ui.column().classes("gap-0"):
                    ui.label(route.label).classes("text-2xl font-bold").style(
                        f"color: {StudioColor.TEXT}"
                    )
                    ui.label(route.description).classes("text-sm").style(
                        f"color: {StudioColor.MUTED}"
                    )

            if context.period_context is not None:
                status_badge(format_period_label(context.period_context), tone=BadgeTone.INFO)

        filter_chips_bar(context.filters)

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

        with ui.grid(columns=3).classes("w-full gap-4"):
            kpi_card(
                title="Active Profile",
                value=context.profile.label,
                subtitle=f"ID: {context.profile.profile_id}",
                tooltip="Resolved from active Settings TOML + FinancialRules TOML pair.",
                icon="folder",
            )
            run_display = (
                context.effective_run_id[:8] if context.effective_run_id else "No committed run"
            )
            core_ver = (
                context.provenance.application_version
                if context.provenance and context.provenance.application_version
                else "Pending discovery"
            )
            kpi_card(
                title="Pinned Run Context",
                value=run_display,
                subtitle=f"Mode: {context.run_mode.value} | Core {core_ver}",
                tooltip="Execution provenance anchored to Control Plane `cp_runs` snapshots.",
                drill_route="/operations",
                icon="history",
            )
            period_val = (
                context.period_context.label
                if context.period_context is not None
                else context.period_request.preset.value
            )
            kpi_card(
                title="Period Horizon",
                value=period_val,
                subtitle=(
                    f"FY {context.period_context.financial_year}"
                    if context.period_context and context.period_context.financial_year
                    else "Awaiting dim_calendar resolution"
                ),
                tooltip="Resolved via authoritative `silver.dim_calendar` boundaries.",
                icon="calendar_today",
            )

        empty_state_panel(
            title=f"{route.label} Workspace Connected",
            message=(
                f"Connected to profile '{context.profile.label}' with active run context and "
                f"validated period boundaries. Analytics modules are not yet populated for this "
                f"domain view."
            ),
            icon=route.icon,
            hint=(f"Views: {', '.join(workspace_views)} | Sources: {', '.join(upstream_sources)}"),
        )
