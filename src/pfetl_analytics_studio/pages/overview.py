"""Overview page module — personalized monthly financial decision cockpit."""

from __future__ import annotations

from nicegui import ui

from pfetl_analytics_studio.analytics import OverviewKPI, OverviewService, OverviewSnapshot
from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.semantic import MetricFormat
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    build_asset_class_mix_figure,
    build_cashflow_efficiency_figure,
    build_category_breakup_figure,
    build_deployment_flow_figure,
    build_fire_trajectory_figure,
    build_net_worth_attribution_figure,
    build_portfolio_return_figure,
    build_wealth_asset_split_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    format_currency,
    format_metric_value,
    format_percent,
    kpi_card,
    status_badge,
)

_KPI_ICONS: dict[str, str] = {
    "market_net_worth": "account_balance",
    "total_income": "payments",
    "total_expense": "receipt",
    "net_cashflow": "swap_vert",
    "savings_rate": "savings",
    "net_investment_flow": "moving",
    "portfolio_value": "trending_up",
    "indicative_tax_exposure": "receipt_long",
    "fi_coverage": "local_fire_department",
    "portfolio_xirr": "show_chart",
    "portfolio_hhi": "pie_chart",
    "liquidity_runway": "water_drop",
    "passive_coverage": "autorenew",
    "active_dependency": "work_outline",
    "latest_pfetl_run": "engineering",
    "book_vs_market": "balance",
    "wealth_origin": "auto_graph",
    "liquid_illiquid_mix": "account_balance_wallet",
}


def _tone_from_name(name: str) -> BadgeTone:
    """Map string tone label from `OverviewKPI` into `BadgeTone`."""
    mapping = {
        "success": BadgeTone.SUCCESS,
        "warning": BadgeTone.WARNING,
        "error": BadgeTone.ERROR,
        "info": BadgeTone.INFO,
        "recovery": BadgeTone.RECOVERY,
    }
    return mapping.get(name, BadgeTone.NEUTRAL)


def _format_kpi_card_inputs(
    kpi: OverviewKPI,
    currency_id: str,
) -> tuple[str, str | None, BadgeTone, str, tuple[tuple[str, str], ...]]:
    """Format headline metric, comparison delta pill, dark tooltip, and breakup pills."""
    if not kpi.available or kpi.value is None:
        val_str = "Unavailable"
        exact_note = "Data unavailable for current period/FY"
    else:
        val_str = format_metric_value(
            kpi.value,
            kpi.format,
            currency_id=currency_id,
            compact=True,
            unit_suffix=kpi.unit_suffix,
        )
        if kpi.format == MetricFormat.CURRENCY and isinstance(kpi.value, (int, float)):
            exact_note = f"Exact: {format_currency(float(kpi.value), currency_id, compact=False)}"
        else:
            exact_note = f"Value: {val_str}"

    delta_str: str | None = None
    delta_tone = BadgeTone.NEUTRAL
    if kpi.delta_value is not None:
        if kpi.delta_format == MetricFormat.CURRENCY:
            delta_str = format_currency(
                kpi.delta_value, currency_id, compact=True, signed=True, decimals=1
            )
        else:
            delta_str = format_percent(kpi.delta_value, decimals=1, signed=True)
        if kpi.delta_label:
            delta_str = f"{delta_str} {kpi.delta_label}"
        is_positive = kpi.delta_value >= 0
        delta_tone = (
            BadgeTone.SUCCESS if is_positive == kpi.delta_positive_is_good else BadgeTone.WARNING
        )

    formatted_breakups: list[tuple[str, str]] = []
    for item in kpi.breakups:
        if item.value is None:
            continue
        formatted_breakups.append(
            (
                item.label,
                format_metric_value(
                    item.value,
                    item.format,
                    currency_id=currency_id,
                    compact=True,
                    unit_suffix=item.unit_suffix,
                ),
            )
        )

    as_of_note = f"\nAs of: {kpi.as_of_date.isoformat()}" if kpi.as_of_date else ""
    tooltip = f"{kpi.tooltip}\n{exact_note}{as_of_note}"
    return val_str, delta_str, delta_tone, tooltip, tuple(formatted_breakups)


def _render_kpi_strip(
    kpis: tuple[OverviewKPI, ...],
    currency_id: str,
    *,
    compact: bool = False,
) -> None:
    """Render a symmetric 3-column KPI card grid with inline breakups and crisp contrast."""
    with ui.element("div").classes("studio-kpi-grid"):
        for item in kpis:
            val_str, delta_str, delta_tone, tooltip, breakups = _format_kpi_card_inputs(
                item, currency_id
            )
            progress = (
                float(item.value)
                if item.kpi_id == "fi_coverage" and isinstance(item.value, (int, float))
                else None
            )
            kpi_card(
                title=item.label,
                value=val_str,
                subtitle=item.subtitle,
                delta=delta_str,
                delta_tone=delta_tone,
                tooltip=tooltip,
                drill_route=item.drill_route,
                icon=_KPI_ICONS.get(item.kpi_id, "insights"),
                available=item.available,
                compact=compact,
                status_pill=item.status_badge,
                status_pill_tone=_tone_from_name(item.status_tone),
                progress_fraction=progress,
                breakups=breakups,
            )


def _render_attribution_bridge(snapshot: OverviewSnapshot) -> None:
    """Render compact 4-step opening-to-closing period net worth attribution bridge."""
    bridge = snapshot.attribution_bridge
    if bridge is None:
        return
    cid = snapshot.currency_id
    steps: list[tuple[str, float, bool, str]] = [
        ("Opening NW", bridge.opening_net_worth, False, StudioColor.TEXT.value),
        (
            "Net Savings",
            bridge.net_savings_flow,
            True,
            StudioColor.SUCCESS.value if bridge.net_savings_flow >= 0 else StudioColor.ERROR.value,
        ),
        (
            "Market Return",
            bridge.market_return_flow,
            True,
            StudioColor.ACC_HOV.value
            if bridge.market_return_flow >= 0
            else StudioColor.WARNING.value,
        ),
        ("Residual", bridge.attribution_residual, True, StudioColor.MUTED.value),
        ("Closing Market NW", bridge.closing_net_worth, False, StudioColor.TEXT.value),
    ]
    with ui.card().classes("studio-card w-full px-4 py-3 rounded-2xl"):
        with ui.row().classes("w-full items-center justify-between gap-3 flex-wrap"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("account_tree", size="18px").style(f"color: {StudioColor.ACC_HOV}")
                ui.label("Period Attribution Bridge").classes(
                    "text-xs font-semibold uppercase tracking-wider"
                ).style(f"color: {StudioColor.MUTED}")
            with ui.row().classes("items-center gap-3 flex-wrap"):
                for idx, (lbl, amt, signed, color) in enumerate(steps):
                    if idx > 0:
                        sep = "=" if idx == len(steps) - 1 else "→"
                        ui.label(sep).classes("text-xs font-bold").style(
                            f"color: {StudioColor.DIM}"
                        )
                    with ui.column().classes("gap-0"):
                        ui.label(lbl).classes("text-[10px] uppercase tracking-wider").style(
                            f"color: {StudioColor.DIM}"
                        )
                        ui.label(format_currency(amt, cid, compact=True, signed=signed)).classes(
                            "text-sm font-semibold tabular-nums"
                        ).style(f"color: {color}")
                if bridge.closing_book_worth is not None:
                    status_badge(
                        f"Book {format_currency(bridge.closing_book_worth, cid, compact=True)}",
                        tone=BadgeTone.NEUTRAL,
                    )
                if bridge.unrealized_cushion is not None:
                    status_badge(
                        f"Cushion {format_currency(bridge.unrealized_cushion, cid, compact=True, signed=True)}",
                        tone=BadgeTone.SUCCESS
                        if bridge.unrealized_cushion >= 0
                        else BadgeTone.WARNING,
                    )


def _render_health_footer(snapshot: OverviewSnapshot) -> None:
    """Render compact single-row PFETL Control Plane & Meta freshness strip."""
    rh = snapshot.run_health
    run_short = rh.latest_run_id[:8] if rh.latest_run_id else "None"
    with ui.card().classes("studio-card w-full px-4 py-3 rounded-2xl"):
        with ui.row().classes("w-full items-center justify-between gap-3 flex-wrap"):
            with ui.row().classes("items-center gap-3 flex-wrap"):
                ui.icon("verified_user", size="sm").style(f"color: {StudioColor.SUCCESS}")
                ui.label("Data Health & Provenance").classes("text-xs font-semibold").style(
                    f"color: {StudioColor.TEXT}"
                )
                status_badge(
                    f"Run {run_short} ({rh.status or 'UNKNOWN'})",
                    tone=BadgeTone.SUCCESS if rh.status == "SUCCESS" else BadgeTone.WARNING,
                )
                status_badge(
                    f"{rh.surfaces_discovered}/{rh.surfaces_expected} Surfaces",
                    tone=BadgeTone.INFO,
                )
                if rh.warehouse_rows_total > 0:
                    ui.label(
                        f"{rh.warehouse_rows_total:,} tracked rows across "
                        f"{rh.gold_tables_populated} Gold tables"
                    ).classes("text-xs tabular-nums").style(f"color: {StudioColor.MUTED}")

            ui.button(
                "Operations & Logs",
                icon="engineering",
                on_click=lambda: ui.navigate.to("/operations"),
            ).props("outline dense size=sm no-caps color=primary")


def render_overview_page(context: AppContext, *, cache: QueryCache | None = None) -> None:
    """Render the Overview monthly financial decision cockpit from shared `AppContext`.

    Parameters
    ----------
    context : AppContext
        Active session context containing profile, run provenance, period, and filters.
    cache : QueryCache | None, optional
        Optional bounded session cache used to accelerate repeated period renders.
    """
    route = ROUTES_BY_ID["overview"]

    with ui.column().classes("w-full gap-4"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.row().classes("items-center gap-3"):
                with (
                    ui.element("div")
                    .classes("w-9 h-9 rounded-lg flex items-center justify-center shrink-0")
                    .style(
                        "background-color: rgba(59, 130, 246, 0.16); "
                        "border: 1px solid rgba(96, 165, 250, 0.28);"
                    )
                ):
                    ui.icon(route.icon, size="20px").style(f"color: {StudioColor.ACC_HOV}")
                with ui.column().classes("gap-0"):
                    ui.label(route.label).classes("text-2xl font-bold tracking-tight").style(
                        f"color: {StudioColor.TEXT}"
                    )
                    ui.label(route.description).classes("text-sm").style(
                        f"color: {StudioColor.MUTED}"
                    )

        if context.profile is None:
            empty_state_panel(
                title="Select a PFETL Profile to Launch Your Cockpit",
                message=(
                    "Choose a PFETL Settings TOML and FinancialRules TOML below (or open a "
                    "recent profile) to inspect Gold analytics, attribution, and run provenance."
                ),
                icon="folder_open",
            )
            return

        if context.is_warehouse_locked:
            empty_state_panel(
                title="Overview Paused While PFETL Lock Is Active",
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

        if context.is_incompatible and context.compatibility:
            error_state_panel(
                title="Overview Blocked by Compatibility Check",
                message=(
                    "Required upstream PFETL surfaces or columns did not match expected "
                    "catalog contracts. Operations and profile diagnostics remain accessible."
                ),
                details=list(context.compatibility.diagnostics[:6]),
            )
            return

        snapshot = OverviewService.get_snapshot(
            context.profile,
            context.period_context,
            run_id=context.selected_run_id,
            provenance=context.provenance,
            compatibility=context.compatibility,
            filters=context.filters,
            cache=cache,
        )

        if snapshot.state != "ready":
            empty_state_panel(
                title=(
                    "No Completed PFETL Run Recorded"
                    if snapshot.state == "no_run"
                    else "Overview Analytical Data Unavailable"
                ),
                message=snapshot.diagnostics[0]
                if snapshot.diagnostics
                else "No populated Gold rows match the active period.",
                icon="hourglass_empty",
                action_label="Inspect Operations & Runs",
                action_target="/operations",
            )
            _render_health_footer(snapshot)
            return

        ui.label("Key Financial Highlights & Breakups").classes(
            "text-xs font-semibold uppercase tracking-wider"
        ).style(f"color: {StudioColor.DIM}")
        _render_kpi_strip(snapshot.primary_kpis, snapshot.currency_id, compact=False)

        ui.label("Portfolio Alpha, Book-vs-Market, Wealth Origin & Pipeline Telemetry").classes(
            "text-xs font-semibold uppercase tracking-wider mt-2"
        ).style(f"color: {StudioColor.DIM}")
        _render_kpi_strip(snapshot.secondary_kpis, snapshot.currency_id, compact=True)

        _render_attribution_bridge(snapshot)

        ui.label("Net Worth Split by Asset Group (Market vs Book)").classes(
            "text-xs font-semibold uppercase tracking-wider mt-2"
        ).style(f"color: {StudioColor.DIM}")
        chart_panel(
            "Net Worth Split by Asset Group",
            "Closing Market vs Book balance across wealth asset groups (including liabilities)",
            build_wealth_asset_split_figure(
                snapshot.wealth_split, currency_id=snapshot.currency_id
            ),
            source_caption="Source: gold.wealth_asset_breakdown + silver.dim_asset_category",
            drill_route="/wealth",
        )

        ui.label("Wealth, Cashflow & Portfolio Trends").classes(
            "text-xs font-semibold uppercase tracking-wider mt-2"
        ).style(f"color: {StudioColor.DIM}")
        with ui.element("div").classes("studio-chart-grid"):
            chart_panel(
                "Net Worth & Attribution",
                f"Market vs Book Net Worth decomposed into Net Savings & Market Return ({snapshot.chart_window_label})",
                build_net_worth_attribution_figure(
                    snapshot.net_worth_series, currency_id=snapshot.currency_id
                ),
                source_caption="Source: gold.core_monthly_fact",
                drill_route="/wealth",
            )
            chart_panel(
                "Cashflow & Savings Flow",
                f"Monthly Income, Expense & Net Cashflow ({snapshot.chart_window_label})",
                build_cashflow_efficiency_figure(
                    snapshot.cashflow_series, currency_id=snapshot.currency_id
                ),
                source_caption="Source: gold.core_monthly_fact + cashflow_efficiency_analytics",
                drill_route="/household",
            )
            chart_panel(
                "Portfolio Valuation & Return",
                f"Market Value vs Cost Basis and Portfolio vs Benchmark XIRR ({snapshot.chart_window_label})",
                build_portfolio_return_figure(
                    snapshot.portfolio_series, currency_id=snapshot.currency_id
                ),
                source_caption="Source: gold.investment_by_portfolio (Closing_Date)",
                drill_route="/investments",
            )
            chart_panel(
                "FIRE Coverage & Simulation Probability",
                f"Current FI Coverage vs Monte Carlo Probability of Success ({snapshot.chart_window_label})",
                build_fire_trajectory_figure(snapshot.fire_series),
                source_caption="Source: gold.wealth_fire_analytics",
                drill_route="/fire",
            )

        ui.label("Income & Expense Breakup, Capital Deployment & Asset Class Mix").classes(
            "text-xs font-semibold uppercase tracking-wider mt-2"
        ).style(f"color: {StudioColor.DIM}")
        with ui.element("div").classes("studio-chart-grid"):
            chart_panel(
                "Income Breakup by Source",
                f"Top positive income categories across {snapshot.period_label}",
                build_category_breakup_figure(
                    snapshot.income_breakup,
                    currency_id=snapshot.currency_id,
                    bar_color="rgba(52, 211, 153, 0.80)",
                ),
                source_caption="Source: gold.cashflow_income_breakdown",
                drill_route="/household",
            )
            chart_panel(
                "Expense Breakup by Group",
                f"Top expense groups (Core vs Non-Core, Fixed vs Variable) across {snapshot.period_label}",
                build_category_breakup_figure(
                    snapshot.expense_breakup,
                    currency_id=snapshot.currency_id,
                    bar_color="rgba(251, 191, 36, 0.80)",
                ),
                source_caption="Source: gold.cashflow_expense_breakdown",
                drill_route="/household",
            )
            chart_panel(
                "Capital Deployment vs Redemptions",
                f"Gross invested, redeemed & net investment flow ({snapshot.chart_window_label})",
                build_deployment_flow_figure(
                    snapshot.cashflow_series, currency_id=snapshot.currency_id
                ),
                source_caption="Source: gold.cashflow_efficiency_analytics",
                drill_route="/investments",
            )
            chart_panel(
                "Asset Class Allocation & Alpha",
                "Market Value, Portfolio Weight, XIRR & Active Return by Instrument Class",
                build_asset_class_mix_figure(
                    snapshot.asset_class_mix, currency_id=snapshot.currency_id
                ),
                source_caption="Source: gold.investment_by_class",
                drill_route="/investments",
            )

        _render_health_footer(snapshot)
