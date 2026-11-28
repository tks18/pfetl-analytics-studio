"""Wealth page module — Net Worth, Liquidity, Drawdown, Attribution & Asset Drill."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, cast

from nicegui import run, ui

from pfetl_analytics_studio.analytics import (
    OverviewKPI,
    WealthAnalytics,
    WealthAssetRow,
    WealthSummarySnapshot,
)
from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    TableColumn,
    bounded_data_table,
    build_wealth_attribution_trend_figure,
    build_wealth_attribution_waterfall_figure,
    build_wealth_currency_donut_figure,
    build_wealth_drawdown_figure,
    build_wealth_foreign_exposure_trend_figure,
    build_wealth_group_treemap_figure,
    build_wealth_investment_book_vs_market_figure,
    build_wealth_liquid_vs_illiquid_figure,
    build_wealth_liquidity_debt_figure,
    build_wealth_nw_comparison_figure,
    build_wealth_pnl_fx_figure,
    build_wealth_savings_vs_market_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    format_currency,
    format_metric_value,
    format_percent,
    kpi_card,
    loading_state_card,
    status_badge,
)

_WEALTH_KPI_ICONS: dict[str, str] = {
    "wealth_book_nw": "account_balance",
    "wealth_market_nw": "savings",
    "wealth_total_assets": "account_balance_wallet",
    "wealth_liabilities": "credit_card",
    "wealth_liquid_assets": "water_drop",
    "wealth_liquidity_ratio": "hourglass_top",
    "wealth_debt_to_assets": "balance",
    "wealth_foreign_exposure": "public",
    "wealth_real_nw_growth": "trending_up",
}


def _render_wealth_kpi_grid(kpis: tuple[OverviewKPI, ...], currency_id: str) -> None:
    """Render 3-column Wealth KPI grid matching Overview & Household styling."""
    with ui.element("div").classes("studio-kpi-grid"):
        for kpi in kpis:
            val_str = (
                format_metric_value(
                    kpi.value,
                    kpi.format,
                    currency_id=currency_id,
                    compact=True,
                    unit_suffix=kpi.unit_suffix,
                )
                if kpi.available and kpi.value is not None
                else "Unavailable"
            )
            delta_str: str | None = None
            delta_tone = BadgeTone.NEUTRAL
            if kpi.delta_value is not None:
                delta_str = format_percent(kpi.delta_value, decimals=1, signed=True)
                if kpi.delta_label:
                    delta_str = f"{delta_str} {kpi.delta_label}"
                delta_tone = BadgeTone.SUCCESS if kpi.delta_value >= 0 else BadgeTone.WARNING

            breakups = [
                (
                    b.label,
                    format_metric_value(
                        b.value,
                        b.format,
                        currency_id=currency_id,
                        compact=True,
                    ),
                )
                for b in kpi.breakups
                if b.value is not None
            ]
            kpi_card(
                kpi.label,
                val_str,
                subtitle=kpi.subtitle,
                delta=delta_str,
                delta_tone=delta_tone,
                icon=_WEALTH_KPI_ICONS.get(kpi.kpi_id, "savings"),
                tooltip=kpi.tooltip,
                breakups=breakups,
            )


def _open_asset_detail_dialog(context: AppContext, asset: WealthAssetRow) -> None:
    """Open modal drawer displaying Silver asset metadata, opening anchor, and monthly history."""
    if context.profile is None:
        return
    profile = context.profile
    period_ctx = context.period_context
    catalog_snap = context.catalog_snapshot
    cid = asset.currency_id
    dialog = ui.dialog()
    with dialog, ui.card().classes("studio-card w-[760px] max-w-full p-6 rounded-xl gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.label(asset.asset_name).classes("text-lg font-semibold")
                ui.label(f"{asset.asset_group} • {asset.asset_id}").classes(
                    "text-xs font-mono"
                ).style(f"color: {StudioColor.MUTED}")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense color=white")

        with ui.row().classes("items-center gap-2 flex-wrap"):
            status_badge(
                "Liquid" if asset.is_liquid else "Illiquid",
                tone=BadgeTone.SUCCESS if asset.is_liquid else BadgeTone.NEUTRAL,
            )
            if asset.is_cash_pool:
                status_badge("Cash Pool", tone=BadgeTone.INFO, outline=True)
            if asset.ledger_role:
                status_badge(asset.ledger_role, tone=BadgeTone.NEUTRAL, outline=True)

        with ui.grid(columns=2).classes("md:grid-cols-4 w-full gap-3 text-xs"):
            for lbl, val in (
                ("Book Balance", format_currency(asset.closing_balance, cid)),
                (
                    "Market Balance",
                    format_currency(asset.closing_balance_market, cid)
                    if asset.closing_balance_market is not None
                    else "—",
                ),
                (
                    "All-Time High",
                    format_currency(asset.all_time_high, cid)
                    if asset.all_time_high is not None
                    else "—",
                ),
                (
                    "Drawdown",
                    format_percent(asset.drawdown_from_peak, decimals=1)
                    if asset.drawdown_from_peak is not None
                    else "—",
                ),
            ):
                with ui.column().classes("gap-0"):
                    ui.label(lbl).style(f"color: {StudioColor.DIM}")
                    ui.label(val).classes("font-mono font-semibold")

        if asset.opening_anchor_amount is not None:
            ui.label(
                f"Opening Balance Anchor (silver.fact_opening_balances): "
                f"{format_currency(asset.opening_anchor_amount, cid)} "
                f"as of {asset.opening_anchor_date.isoformat() if asset.opening_anchor_date else '—'}"
            ).classes("text-xs font-mono").style(f"color: {StudioColor.MUTED}")

        history_box = ui.column().classes("w-full gap-2")

        def _render_history(history: Sequence[Any]) -> None:
            history_box.clear()
            with history_box:
                if history:
                    h_cols = [
                        TableColumn(name="month", label="Month End", field="month", align="left"),
                        TableColumn(name="open", label="Opening", field="open", align="right"),
                        TableColumn(name="book", label="Closing Book", field="book", align="right"),
                        TableColumn(name="mkt", label="Closing Market", field="mkt", align="right"),
                        TableColumn(name="ncf", label="Net Cashflow", field="ncf", align="right"),
                        TableColumn(name="dd", label="Drawdown", field="dd", align="right"),
                    ]
                    h_rows = [
                        {
                            "month": h.month_end.isoformat(),
                            "open": format_currency(h.opening_balance, cid),
                            "book": format_currency(h.closing_balance, cid),
                            "mkt": format_currency(h.closing_balance_market, cid)
                            if h.closing_balance_market is not None
                            else "—",
                            "ncf": format_currency(h.net_cashflow_month, cid, signed=True),
                            "dd": format_percent(h.drawdown_from_peak, decimals=1)
                            if h.drawdown_from_peak is not None
                            else "—",
                        }
                        for h in reversed(history)
                    ]
                    bounded_data_table(h_cols, h_rows, row_key="month", pagination=12)

        def _fetch_sync() -> Sequence[Any]:
            return WealthAnalytics.asset_history(
                profile,
                asset.asset_id,
                period_ctx,
                catalog_snapshot=catalog_snap,
            )

        try:
            asyncio.get_running_loop()
            has_loop = True
        except RuntimeError:
            has_loop = False

        if not has_loop:
            _render_history(_fetch_sync())
        else:
            with history_box:
                with ui.row().classes("w-full items-center gap-2 py-2"):
                    ui.spinner(size="sm")
                    ui.label("Loading asset history...").classes("text-xs").style(
                        f"color: {StudioColor.MUTED}"
                    )

            async def _load_history() -> None:
                try:
                    history = await run.io_bound(_fetch_sync)
                except Exception as exc:  # noqa: BLE001
                    history_box.clear()
                    with history_box:
                        ui.label(f"Unable to load asset history: {exc}").classes(
                            "text-xs text-red-400"
                        )
                    return
                _render_history(history)

            asyncio.ensure_future(_load_history())
    dialog.open()


def _render_wealth_body(context: AppContext, snapshot: WealthSummarySnapshot) -> None:
    """Render ready/empty Wealth cockpit from an immutable `WealthSummarySnapshot`."""
    if snapshot.state != "ready":
        empty_state_panel(
            title=(
                "No Completed PFETL Run Recorded"
                if snapshot.state == "no_run"
                else "Wealth Data Unavailable"
            ),
            message=snapshot.diagnostics[0]
            if snapshot.diagnostics
            else "No populated Wealth Gold rows match the active period.",
            icon="hourglass_empty",
            action_label="Inspect Operations & Runs",
            action_target="/operations",
        )
        return

    cid = snapshot.currency_id
    attr = snapshot.attribution
    ab = snapshot.asset_breakdown

    if attr is not None and attr.state == "ready":
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                status_badge(
                    "Attribution Reconciled"
                    if attr.reconciles_within_tolerance
                    else "Attribution Variance",
                    tone=BadgeTone.SUCCESS
                    if attr.reconciles_within_tolerance
                    else BadgeTone.WARNING,
                )
                if ab is not None and ab.state == "ready":
                    status_badge(
                        f"Liquid: {format_currency(ab.liquid_total_market, cid, compact=True)}",
                        tone=BadgeTone.INFO,
                        outline=True,
                    )
                    status_badge(
                        f"Illiquid: {format_currency(ab.illiquid_total_market, cid, compact=True)}",
                        tone=BadgeTone.NEUTRAL,
                        outline=True,
                    )
            ui.label(
                f"Asset Market Δ: {format_currency(attr.asset_market_delta, cid, signed=True)} = "
                f"Savings {format_currency(attr.net_savings_flow, cid, signed=True)} + "
                f"Market {format_currency(attr.market_return_flow, cid, signed=True)} + "
                f"Residual {format_currency(attr.attribution_residual, cid, signed=True)}"
            ).classes("text-xs font-mono").style(f"color: {StudioColor.MUTED}")

    ui.label("NET WORTH, BALANCE SHEET, LIQUIDITY & EXPOSURE").classes(
        "text-xs font-semibold tracking-wider pt-1"
    ).style(f"color: {StudioColor.DIM}")
    _render_wealth_kpi_grid(snapshot.kpis, cid)

    assets = ab.assets if ab is not None else ()
    asset_by_id = {a.asset_id: a for a in assets}

    def _on_asset_click(evt: Any) -> None:
        args = getattr(evt, "args", None)
        if not isinstance(args, dict):
            return
        points = cast(dict[str, Any], args).get("points")
        if isinstance(points, list):
            pts_seq = cast(Sequence[object], points)
            if pts_seq and isinstance(pts_seq[0], dict):
                first = cast(dict[str, Any], pts_seq[0])
                cd = first.get("customdata")
                key: Any = cast(dict[str, Any], cd).get("key") if isinstance(cd, dict) else cd
                if isinstance(key, str) and key in asset_by_id:
                    _open_asset_detail_dialog(context, asset_by_id[key])

    with ui.element("div").classes("studio-chart-grid"):
        chart_panel(
            title="Book vs Market Net Worth",
            subtitle=f"Separate cost-basis and mark-to-market trajectories ({snapshot.chart_window_label})",
            figure=build_wealth_nw_comparison_figure(snapshot.monthly_series, cid),
            source_caption="Source: gold.core_monthly_fact",
        )
        chart_panel(
            title="Net Worth Attribution Bridge",
            subtitle=f"Opening Market → Savings / Market Return / Residual → Closing ({snapshot.period_label})",
            figure=build_wealth_attribution_waterfall_figure(attr),
            source_caption="Source: gold.core_monthly_fact",
        )
        chart_panel(
            title="Asset Group & Subcategory Composition",
            subtitle=f"Hierarchical mark-to-market wealth allocation ({snapshot.period_label})",
            figure=build_wealth_group_treemap_figure(assets, cid),
            source_caption="Source: gold.wealth_asset_breakdown + silver.dim_asset_*",
            on_point_click=_on_asset_click,
        )
        chart_panel(
            title="Unrealized P&L, Asset P&L & Forex P&L",
            subtitle=f"Cumulative price vs FX translation P&L ({snapshot.chart_window_label})",
            figure=build_wealth_pnl_fx_figure(snapshot.monthly_series, cid),
            source_caption="Source: gold.core_monthly_fact",
        )
        chart_panel(
            title="Liquidity Runway & YoY Real NW Growth",
            subtitle=f"Months of core expense liquidity vs real annual growth ({snapshot.chart_window_label})",
            figure=build_wealth_liquidity_debt_figure(snapshot.monthly_series),
            source_caption="Source: gold.cashflow_efficiency_analytics",
        )
        chart_panel(
            title="Top Assets & Drawdown from Peak (Click to Drill)",
            subtitle=f"Click any asset bar to inspect metadata & monthly history ({snapshot.period_label})",
            figure=build_wealth_drawdown_figure(assets, cid),
            source_caption="Source: gold.wealth_asset_breakdown",
            on_point_click=_on_asset_click,
        )
        chart_panel(
            title="Foreign Currency Exposure Breakup",
            subtitle=f"Base vs foreign currency wealth & FX P&L ({snapshot.period_label})",
            figure=build_wealth_currency_donut_figure(ab.currency_mix if ab else (), cid),
            source_caption="Source: gold.wealth_asset_breakdown + silver.dim_asset_subcategory",
        )
        chart_panel(
            title="Foreign Exposure Amount & Share % Trend",
            subtitle=f"FX wealth value vs % of gross assets ({snapshot.chart_window_label})",
            figure=build_wealth_foreign_exposure_trend_figure(snapshot.monthly_series, cid),
            source_caption="Source: gold.core_monthly_fact",
        )
        chart_panel(
            title="Investment Book vs Market & XIRR",
            subtitle=f"Contributed capital vs market valuation & XIRR ({snapshot.chart_window_label})",
            figure=build_wealth_investment_book_vs_market_figure(snapshot.monthly_series, cid),
            source_caption="Source: gold.core_monthly_fact",
        )
        chart_panel(
            title="Monthly Net Worth Attribution Trend",
            subtitle=f"Savings vs Market Return vs Residual ({snapshot.chart_window_label})",
            figure=build_wealth_attribution_trend_figure(snapshot.monthly_series, cid),
            source_caption="Source: gold.core_monthly_fact",
        )
        chart_panel(
            title="Liquid vs Illiquid Market Wealth",
            subtitle=f"Readily liquid vs illiquid asset split ({snapshot.chart_window_label})",
            figure=build_wealth_liquid_vs_illiquid_figure(snapshot.monthly_series, cid),
            source_caption="Source: gold.core_monthly_fact",
        )
        chart_panel(
            title="Cumulative Net Savings vs Market Balance (Click to Drill)",
            subtitle=f"Contributed savings vs current market value for top assets ({snapshot.period_label})",
            figure=build_wealth_savings_vs_market_figure(assets, cid),
            source_caption="Source: gold.wealth_asset_breakdown",
            on_point_click=_on_asset_click,
        )

    if ab is not None and ab.currency_mix:
        with ui.card().classes("studio-card w-full p-5 rounded-xl gap-3"):
            ui.label("Currency Exposure Breakdown (Base vs Foreign)").classes(
                "text-sm font-semibold"
            )
            c_cols = [
                TableColumn(name="curr", label="Currency", field="curr", align="left"),
                TableColumn(name="scope", label="Scope", field="scope", align="left"),
                TableColumn(name="assets", label="Assets", field="assets", align="right"),
                TableColumn(name="book", label="Book Value", field="book", align="right"),
                TableColumn(name="mkt", label="Market Value", field="mkt", align="right"),
                TableColumn(name="share", label="Share %", field="share", align="right"),
                TableColumn(name="ast_pnl", label="Asset P&L", field="ast_pnl", align="right"),
                TableColumn(name="fx_pnl", label="Forex P&L", field="fx_pnl", align="right"),
            ]
            c_rows = [
                {
                    "curr": c.currency_id,
                    "scope": "Foreign" if c.is_foreign else "Base",
                    "assets": str(c.asset_count),
                    "book": format_currency(c.book_value, cid),
                    "mkt": format_currency(c.market_value, cid),
                    "share": format_percent(c.share_pct, decimals=1)
                    if c.share_pct is not None
                    else "—",
                    "ast_pnl": format_currency(c.asset_pnl, cid, signed=True),
                    "fx_pnl": format_currency(c.forex_pnl, cid, signed=True),
                }
                for c in ab.currency_mix
            ]
            bounded_data_table(c_cols, c_rows, row_key="curr")

    if assets:
        with ui.card().classes("studio-card w-full p-5 rounded-xl gap-3"):
            ui.label(
                "Asset-Subcategory Breakdown (gold.wealth_asset_breakdown — click row to inspect)"
            ).classes("text-sm font-semibold")
            cols = [
                TableColumn(name="name", label="Asset", field="name", align="left"),
                TableColumn(name="group", label="Group", field="group", align="left"),
                TableColumn(name="liq", label="Liquidity", field="liq", align="left"),
                TableColumn(name="book", label="Book Bal", field="book", align="right"),
                TableColumn(name="mkt", label="Market Bal", field="mkt", align="right"),
                TableColumn(name="ath", label="All-Time High", field="ath", align="right"),
                TableColumn(name="dd", label="Drawdown", field="dd", align="right"),
                TableColumn(name="ncf", label="Period Flow", field="ncf", align="right"),
                TableColumn(name="runway", label="Runway", field="runway", align="right"),
            ]
            rows = [
                {
                    "asset_id": a.asset_id,
                    "name": a.asset_name,
                    "group": a.asset_group,
                    "liq": "Liquid" if a.is_liquid else "Illiquid",
                    "book": format_currency(a.closing_balance, cid),
                    "mkt": format_currency(a.closing_balance_market, cid)
                    if a.closing_balance_market is not None
                    else "—",
                    "ath": format_currency(a.all_time_high, cid)
                    if a.all_time_high is not None
                    else "—",
                    "dd": format_percent(a.drawdown_from_peak, decimals=1)
                    if a.drawdown_from_peak is not None
                    else "—",
                    "ncf": format_currency(a.net_cashflow_month, cid, signed=True),
                    "runway": f"{a.months_of_runway:.1f} mos"
                    if a.months_of_runway is not None
                    else "—",
                }
                for a in assets
            ]
            tbl_card = bounded_data_table(cols, rows, row_key="asset_id")
            for child in tbl_card.default_slot.children:
                if isinstance(child, ui.table):

                    def _on_row_click(evt: Any) -> None:
                        args = getattr(evt, "args", None)
                        if isinstance(args, list):
                            args_seq = cast(Sequence[object], args)
                            if len(args_seq) >= 2 and isinstance(args_seq[1], dict):
                                rdict = cast(dict[str, Any], args_seq[1])
                                aid = str(rdict.get("asset_id") or "")
                                if aid in asset_by_id:
                                    _open_asset_detail_dialog(context, asset_by_id[aid])

                    child.on("rowClick", _on_row_click)


def render_wealth_page(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
) -> None:
    """Render the Wealth workspace without blocking the NiceGUI event loop."""
    route = ROUTES_BY_ID["wealth"]

    with ui.column().classes("w-full gap-4"):
        with ui.row().classes("w-full items-center gap-3"):
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
                ui.label(route.description).classes("text-sm").style(f"color: {StudioColor.MUTED}")

        if context.profile is None:
            empty_state_panel(
                title="Select a PFETL Profile to Explore Wealth Analytics",
                message="Open a PFETL Settings TOML and FinancialRules TOML to inspect Net Worth and Liquidity.",
                icon="folder_open",
            )
            return

        if context.is_warehouse_locked:
            empty_state_panel(
                title="Wealth Analytics Paused While PFETL Lock Is Active",
                message="PFETL Core holds `pipeline.lock` on the DuckDB warehouse.",
                icon="lock",
                action_label="Inspect Operations & Runs",
                action_target="/operations",
            )
            return

        if context.is_incompatible and context.compatibility:
            error_state_panel(
                title="Wealth Blocked by Compatibility Check",
                message="Required upstream PFETL surfaces did not match expected catalog contracts.",
                details=list(context.compatibility.diagnostics[:6]),
            )
            return

        def _fetch_sync() -> WealthSummarySnapshot:
            assert context.profile is not None
            return WealthAnalytics.summary(
                context.profile,
                context.period_context,
                run_id=context.analytical_run_id,
                provenance=context.analytical_provenance or context.provenance,
                compatibility=context.compatibility,
                catalog_snapshot=context.catalog_snapshot,
                filters=context.filters,
                cache=cache,
            )

        try:
            asyncio.get_running_loop()
            has_loop = True
        except RuntimeError:
            has_loop = False

        if not has_loop:
            _render_wealth_body(context, _fetch_sync())
            return

        body_slot = ui.column().classes("w-full gap-4")
        with body_slot:
            loading_state_card("Loading Wealth balance sheet & attribution...")

        async def _load_async() -> None:
            try:
                snap = await run.io_bound(_fetch_sync)
            except Exception as exc:  # noqa: BLE001
                body_slot.clear()
                with body_slot:
                    error_state_panel(
                        title="Failed to Load Wealth Analytics",
                        message="An error occurred while querying the read-only warehouse.",
                        details=[str(exc)],
                    )
                return
            body_slot.clear()
            with body_slot:
                _render_wealth_body(context, snap)

        ui.timer(0.01, _load_async, once=True)
