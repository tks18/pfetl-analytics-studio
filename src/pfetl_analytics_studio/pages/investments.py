"""Investments page module — allocation, XIRR performance, benchmarks, FX, and lots."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from pfetl_analytics_studio.analytics import (
    AllocationDimension,
    AllocationRow,
    InvestmentAnalytics,
    InvestmentHoldingRow,
    InvestmentPortfolioSnapshot,
    LotFilter,
    OverviewKPI,
)
from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.pages import render_domain_workspace
from pfetl_analytics_studio.ui import (
    BadgeTone,
    TableColumn,
    bounded_data_table,
    build_inv_active_alpha_figure,
    build_inv_allocation_bars_figure,
    build_inv_allocation_treemap_figure,
    build_inv_class_drift_figure,
    build_inv_class_target_drift_figure,
    build_inv_gainers_losers_figure,
    build_inv_holdings_top_figure,
    build_inv_isin_history_figure,
    build_inv_perf_alpha_trend_figure,
    build_inv_perf_asset_vs_fx_pnl_figure,
    build_inv_perf_concentration_figure,
    build_inv_perf_drawdown_figure,
    build_inv_perf_fx_impact_figure,
    build_inv_perf_risk_return_quadrant_figure,
    build_inv_perf_segment_alpha_figure,
    build_inv_perf_xirr_vs_bm_figure,
    build_inv_pnl_decomposition_figure,
    build_inv_risk_return_scatter_figure,
    build_inv_tax_harvest_figure,
    build_lot_alpha_scatter_figure,
    build_lot_days_to_ltcg_figure,
    build_lot_gain_loss_by_class_figure,
    build_lot_harvest_rec_figure,
    build_lot_holding_type_split_figure,
    build_lot_pre_vs_after_tax_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    format_currency,
    format_metric_value,
    format_percent,
    kpi_card,
    status_badge,
)

_SUB_TABS: tuple[tuple[str, str, str], ...] = (
    ("overview", "Overview & Allocation", "pie_chart"),
    ("performance", "Performance & Benchmarks", "show_chart"),
    ("lots", "Lot Explorer", "inventory_2"),
    ("realized", "Realized & Reconciliation", "fact_check"),
    ("evidence", "Market Evidence", "candlestick_chart"),
)

_SUB_DIMENSION_OPTIONS: dict[str, str] = {
    "none": "None (Single Level)",
    "instrument_subtype": "Subtype",
    "sector": "Sector",
    "isin": "ISIN",
}

_HOLDING_DIM_ATTR: dict[str, str] = {
    AllocationDimension.CLASS.value: "instrument_class",
    AllocationDimension.INSTRUMENT_TYPE.value: "instrument_type",
    AllocationDimension.SUBTYPE.value: "instrument_subtype",
    AllocationDimension.SECTOR.value: "sector",
    AllocationDimension.CURRENCY.value: "currency_id",
    AllocationDimension.ISIN.value: "isin",
}


def _aggregate_sub_allocation(
    holdings: tuple[InvestmentHoldingRow, ...],
    primary_dim: str,
    sub_dim: str,
) -> tuple[AllocationRow, ...]:
    """Aggregate ISIN-grain holdings by (primary, sub) without mixing Gold marts."""
    if not holdings:
        return ()
    p_attr = _HOLDING_DIM_ATTR.get(primary_dim, "instrument_class")
    buckets: dict[tuple[str, str], list[InvestmentHoldingRow]] = {}
    for h in holdings:
        p_val = str(getattr(h, p_attr, "Unclassified") or "Unclassified")
        s_val = str(getattr(h, sub_dim, "Unclassified") or "Unclassified")
        buckets.setdefault((p_val, s_val), []).append(h)

    rows: list[AllocationRow] = []
    for (p_val, s_val), items in buckets.items():
        inv = sum(i.book_value for i in items)
        cur = sum(i.market_value for i in items)
        unr = sum(i.unrealized_pnl for i in items)
        asset_p = sum(i.asset_pnl for i in items if i.asset_pnl is not None)
        forex_p = sum(i.forex_pnl for i in items if i.forex_pnl is not None)
        wt = sum(i.isin_weight for i in items if i.isin_weight is not None)
        weighted_xirr_num = sum(
            (i.xirr_local or 0.0) * i.market_value for i in items if i.xirr_local is not None
        )
        weighted_xirr_den = sum(i.market_value for i in items if i.xirr_local is not None)
        xirr = (weighted_xirr_num / weighted_xirr_den) if weighted_xirr_den > 0 else None
        rows.append(
            AllocationRow(
                label=s_val if p_val != s_val else f"{p_val} · {s_val}",
                parent_label=p_val,
                closing_date=items[0].closing_date,
                invested_value=inv,
                current_value=cur,
                unrealized_pl=unr,
                asset_pnl=asset_p,
                forex_pnl=forex_p,
                xirr=xirr,
                weight=wt,
                stock_count=len(items),
            )
        )
    rows.sort(key=lambda r: r.current_value, reverse=True)
    return tuple(rows)


_DIMENSION_OPTIONS: dict[str, str] = {
    AllocationDimension.CLASS.value: "Asset Class",
    AllocationDimension.SUBTYPE.value: "Subtype",
    AllocationDimension.INSTRUMENT_TYPE.value: "Instrument Type",
    AllocationDimension.SECTOR.value: "Sector",
    AllocationDimension.INDUSTRY.value: "Industry",
    AllocationDimension.GEO.value: "Geography",
    AllocationDimension.COUNTRY.value: "Country",
    AllocationDimension.CURRENCY.value: "Currency",
    AllocationDimension.ISIN.value: "ISIN",
}


def render_investments_page(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
    initial_tab: str = "overview",
    on_tab_change: Callable[[str], Any] | None = None,
) -> None:
    """Render the Investments workspace from shared `AppContext`."""
    route = ROUTES_BY_ID["investments"]
    with ui.column().classes("w-full gap-4"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
            with ui.row().classes("items-center gap-3"):
                ui.icon(route.icon, size="28px").style("color: var(--studio-accent);")
                with ui.column().classes("gap-0"):
                    ui.label(route.label).classes("text-h5 font-semibold").style(
                        "color: var(--studio-text-primary);"
                    )
                    ui.label(route.description).classes("text-caption").style(
                        "color: var(--studio-text-muted);"
                    )

        if context.profile is None:
            render_domain_workspace(
                context=context,
                route=route,
                workspace_views=tuple(label for _, label, _ in _SUB_TABS),
                upstream_sources=(
                    "gold.investment_portfolio_summary",
                    "gold.investment_by_isin",
                    "gold.investment_by_class",
                ),
            )
            return

        active_tab = initial_tab if any(t[0] == initial_tab for t in _SUB_TABS) else "overview"

        with ui.tabs().classes("w-full studio-subtabs") as tabs:
            for tab_id, tab_label, tab_icon in _SUB_TABS:
                ui.tab(tab_id, label=tab_label, icon=tab_icon)
        tabs.set_value(active_tab)

        if on_tab_change is not None:
            tabs.on_value_change(lambda e: on_tab_change(str(e.value)))

        with ui.tab_panels(tabs, value=active_tab).classes("w-full bg-transparent p-0"):
            with ui.tab_panel("overview").classes("p-0 pt-3"):
                _render_overview_tab(context, cache=cache)
            with ui.tab_panel("performance").classes("p-0 pt-3"):
                _render_performance_tab(context, cache=cache)
            with ui.tab_panel("lots").classes("p-0 pt-3"):
                _render_lots_tab(context, cache=cache)
            with ui.tab_panel("realized").classes("p-0 pt-3"):
                empty_state_panel(
                    "Realized & Reconciliation",
                    "Realized capital gains, dividend ledger, and broker reconciliation arrive in Phase 16.",
                    icon="fact_check",
                )
            with ui.tab_panel("evidence").classes("p-0 pt-3"):
                empty_state_panel(
                    "Market Evidence",
                    "ISIN price history, benchmark series, and FX rate evidence arrive in Phase 16.",
                    icon="candlestick_chart",
                )


def _render_overview_tab(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
) -> None:
    """Render the Overview & Allocation sub-tab."""
    profile = context.profile
    if profile is None:
        return

    try:
        snapshot = InvestmentAnalytics.portfolio_snapshot(
            profile,
            context.period_context,
            run_id=context.provenance.run_id if context.provenance else None,
            provenance=context.provenance,
            compatibility=context.compatibility,
            catalog_snapshot=context.catalog_snapshot,
            filters=context.filters,
            cache=cache,
        )
    except Exception as exc:
        error_state_panel(
            "Unable to load investment portfolio snapshot",
            str(exc),
        )
        return

    if snapshot.state != "ready" or (
        snapshot.total_invested == 0.0 and snapshot.total_current == 0.0 and not snapshot.holdings
    ):
        empty_state_panel(
            "No investment holdings found",
            "No portfolio holdings matched the active profile and filter selection.",
            icon="pie_chart_outline",
        )
        return

    currency_id = snapshot.currency_id or "INR_INR"

    with ui.row().classes("w-full items-center justify-between flex-wrap gap-2 mb-3"):
        with ui.row().classes("items-center gap-2 flex-wrap"):
            status_badge(
                f"Holdings: {len(snapshot.holdings)}",
                tone=BadgeTone.INFO,
            )
            if snapshot.as_of_date is not None:
                status_badge(
                    f"As of: {snapshot.as_of_date.isoformat()}",
                    tone=BadgeTone.INFO,
                )
            if snapshot.period_label:
                status_badge(
                    f"Period: {snapshot.period_label}",
                    tone=BadgeTone.NEUTRAL,
                )

    _render_kpi_grid(snapshot.kpis, currency_id)

    dimension_state = {"primary": AllocationDimension.CLASS.value, "sub": "none"}

    with ui.row().classes("w-full items-center justify-between flex-wrap gap-3 mb-2"):
        ui.label("ALLOCATION & P&L ATTRIBUTION").classes(
            "text-caption font-semibold tracking-wider"
        ).style("color: var(--studio-text-muted);")

    with ui.element("div").classes("studio-filter-bar w-full mb-3"):
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            dim_select = (
                ui.select(
                    options=_DIMENSION_OPTIONS,
                    value=dimension_state["primary"],
                    label="Allocation Dimension",
                )
                .props("dense outlined dark")
                .classes("w-56")
            )
            sub_dim_select = (
                ui.select(
                    options=_SUB_DIMENSION_OPTIONS,
                    value=dimension_state["sub"],
                    label="Sub-Allocation (ISIN Drill)",
                )
                .props("dense outlined dark")
                .classes("w-56")
            )

    drawer_container = ui.column().classes("w-full")
    alloc_container = ui.column().classes("w-full gap-4")

    def _open_isin_drawer(isin: str) -> None:
        detail = InvestmentAnalytics.instrument_detail(
            profile,
            isin,
            context.period_context,
            catalog_snapshot=context.catalog_snapshot,
        )
        drawer_container.clear()
        with drawer_container:
            with (
                ui.dialog() as dlg,
                ui.card()
                .classes("w-full max-w-2xl p-5")
                .style(
                    "background: #0F172A; border: 1px solid rgba(148, 163, 184, 0.25); color: #F8FAFC;"
                ),
            ):
                if detail is None:
                    ui.label(f"Instrument {isin} not found").classes("text-subtitle1 font-semibold")
                    ui.button("Close", on_click=dlg.close).props("flat")
                else:
                    meta = detail.master_metadata
                    holding = next((h for h in snapshot.holdings if h.isin == isin), None)
                    latest = holding or (detail.history[-1] if detail.history else None)
                    with ui.row().classes("w-full items-center justify-between mb-3"):
                        with ui.column().classes("gap-0"):
                            ui.label(detail.instrument_name or detail.isin).classes(
                                "text-h6 font-semibold"
                            ).style("color: var(--studio-text-primary);")
                            ui.label(
                                f"{detail.isin} · {meta.get('TICKER', meta.get('ticker', '—'))} · {meta.get('INSTRUMENT_CLASS', meta.get('instrument_class', '—'))}"
                            ).classes("text-caption").style("color: var(--studio-text-muted);")
                        ui.button(icon="close", on_click=dlg.close).props("flat round dense")

                    if latest is not None:
                        with ui.grid(columns=3).classes("w-full gap-3 mb-4"):
                            _detail_stat(
                                "XIRR vs Benchmark",
                                f"{format_percent(latest.xirr_local) if latest.xirr_local is not None else '—'} / {format_percent(latest.bm_xirr) if latest.bm_xirr is not None else '—'}",
                            )
                            _detail_stat(
                                "Active Alpha",
                                format_percent(latest.active_return)
                                if latest.active_return is not None
                                else "—",
                            )
                            _detail_stat(
                                "Asset / Forex P&L",
                                f"{format_currency(latest.asset_pnl or 0.0, currency_id)} / {format_currency(latest.forex_pnl or 0.0, currency_id)}",
                            )
                            _detail_stat(
                                "Class Weight / Target",
                                f"{format_percent(latest.class_weight) if latest.class_weight is not None else '—'} / {format_percent(latest.class_target_weight) if latest.class_target_weight is not None else '—'}",
                            )
                            _detail_stat(
                                "Rebalance Signal",
                                "Rebalance Required" if latest.rebalance_required else "On Target",
                            )
                            _detail_stat(
                                "Harvestable Loss",
                                format_currency(latest.harvestable_loss or 0.0, currency_id),
                            )
                    hist_fig = build_inv_isin_history_figure(detail.history, currency_id)
                    if hist_fig is not None:
                        chart_panel(
                            title="Monthly Book vs Market Value",
                            subtitle="",
                            figure=hist_fig,
                            source_caption="Source: gold.investment_portfolio_summary",
                        )
            dlg.open()

    def _render_allocation_section(dim_value: str, sub_value: str = "none") -> None:
        alloc_container.clear()
        dim = AllocationDimension(dim_value)
        dim_label = _DIMENSION_OPTIONS.get(dim_value, dim_value)
        if sub_value != "none":
            sub_label = _SUB_DIMENSION_OPTIONS.get(sub_value, sub_value)
            alloc_rows = _aggregate_sub_allocation(snapshot.holdings, dim_value, sub_value)
            effective_label = f"{dim_label} → {sub_label}"
        else:
            alloc = InvestmentAnalytics.allocation(
                profile,
                dim,
                context.period_context,
                run_id=context.provenance.run_id if context.provenance else None,
                provenance=context.provenance,
                catalog_snapshot=context.catalog_snapshot,
                cache=cache,
            )
            alloc_rows = alloc.rows
            effective_label = dim_label

        with alloc_container:
            with ui.element("div").classes("studio-chart-grid w-full"):
                _render_chart_card(
                    f"Allocation by {effective_label}",
                    build_inv_allocation_treemap_figure(
                        alloc_rows, currency_id, dimension_label=effective_label
                    ),
                )
                _render_chart_card(
                    f"Invested vs Current ({effective_label})",
                    build_inv_allocation_bars_figure(
                        alloc_rows, currency_id, dimension_label=effective_label
                    ),
                )
                _render_chart_card(
                    f"Weight % vs XIRR % ({effective_label})",
                    build_inv_class_drift_figure(alloc_rows, dimension_label=effective_label),
                )
                _render_chart_card(
                    f"P&L Decomposition ({effective_label})",
                    build_inv_pnl_decomposition_figure(
                        alloc_rows, currency_id, dimension_label=effective_label
                    ),
                )
                _render_chart_card(
                    "Top Holdings by Market Value",
                    build_inv_holdings_top_figure(snapshot.holdings, currency_id, limit=12),
                )
                _render_chart_card(
                    "Risk-Return Scatter (Weight % vs XIRR %)",
                    build_inv_risk_return_scatter_figure(snapshot.holdings, currency_id),
                )
                _render_chart_card(
                    "Class Drift: Actual vs Target Weight %",
                    build_inv_class_target_drift_figure(snapshot.holdings),
                )
                _render_chart_card(
                    "Top Unrealized Gainers vs Losers",
                    build_inv_gainers_losers_figure(snapshot.holdings, currency_id),
                )
                _render_chart_card(
                    "Tax-Loss Harvesting Candidates",
                    build_inv_tax_harvest_figure(snapshot.holdings, currency_id),
                )
                _render_chart_card(
                    "Active Alpha by Holding (vs Benchmark)",
                    build_inv_active_alpha_figure(snapshot.holdings),
                )

            _render_allocation_table(alloc_rows, currency_id, effective_label)
            _render_holdings_table(snapshot, currency_id, _open_isin_drawer)

    def _on_primary_change(e: Any) -> None:
        dimension_state["primary"] = str(e.value)
        _render_allocation_section(dimension_state["primary"], dimension_state["sub"])

    def _on_sub_change(e: Any) -> None:
        dimension_state["sub"] = str(e.value)
        _render_allocation_section(dimension_state["primary"], dimension_state["sub"])

    dim_select.on_value_change(_on_primary_change)
    sub_dim_select.on_value_change(_on_sub_change)
    _render_allocation_section(dimension_state["primary"], dimension_state["sub"])


def _render_kpi_grid(kpis: tuple[OverviewKPI, ...], currency_id: str) -> None:
    with ui.element("div").classes("studio-kpi-grid w-full mb-4"):
        for kpi in kpis:
            delta_text: str | None = None
            delta_tone = BadgeTone.NEUTRAL
            if kpi.delta_value is not None and kpi.delta_format is not None:
                d_str = format_metric_value(
                    kpi.delta_value,
                    kpi.delta_format,
                    currency_id=currency_id,
                    compact=True,
                )
                delta_text = f"{kpi.delta_label}: {d_str}" if kpi.delta_label else d_str
                delta_tone = BadgeTone.INFO if kpi.delta_value >= 0 else BadgeTone.NEUTRAL
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
                title=kpi.label,
                value=format_metric_value(
                    kpi.value,
                    kpi.format,
                    currency_id=currency_id,
                    compact=True,
                    unit_suffix=kpi.unit_suffix,
                ),
                subtitle=kpi.subtitle,
                delta=delta_text,
                delta_tone=delta_tone,
                breakups=breakups,
                icon="trending_up",
            )


def _render_chart_card(title: str, figure: dict[str, Any] | None) -> None:
    if figure is None:
        return
    chart_panel(
        title=title,
        subtitle="",
        figure=figure,
        source_caption="Source: gold.investment_*",
    )


def _detail_stat(label: str, value: str) -> None:
    with ui.column().classes("gap-0 p-2 rounded").style("background: rgba(15, 23, 42, 0.55);"):
        ui.label(label).classes("text-caption").style("color: var(--studio-text-muted);")
        ui.label(value).classes("text-body2 font-semibold").style(
            "color: var(--studio-text-primary);"
        )


def _render_allocation_table(rows: tuple[Any, ...], currency_id: str, dim_label: str) -> None:
    columns = [
        TableColumn(name="bucket", label=dim_label, field="bucket", align="left"),
        TableColumn(name="invested", label="Invested", field="invested", align="right"),
        TableColumn(name="current", label="Current Value", field="current", align="right"),
        TableColumn(name="weight", label="Weight %", field="weight", align="right"),
        TableColumn(name="unrealized", label="Unrealized P&L", field="unrealized", align="right"),
        TableColumn(name="xirr", label="XIRR", field="xirr", align="right"),
    ]
    table_rows = [
        {
            "bucket": r.label,
            "invested": format_currency(r.invested_value, currency_id),
            "current": format_currency(r.current_value, currency_id),
            "weight": format_percent(r.weight) if r.weight is not None else "—",
            "unrealized": format_currency(r.unrealized_pl, currency_id),
            "xirr": format_percent(r.xirr) if r.xirr is not None else "—",
        }
        for r in rows
    ]
    bounded_data_table(
        title=f"Allocation Breakdown by {dim_label}",
        columns=columns,
        rows=table_rows,
        pagination=12,
    )


def _render_holdings_table(
    snapshot: InvestmentPortfolioSnapshot,
    currency_id: str,
    on_select_isin: Callable[[str], None],
) -> None:
    columns = [
        TableColumn(name="isin", label="ISIN", field="isin", align="left"),
        TableColumn(name="name", label="Instrument", field="name", align="left"),
        TableColumn(name="asset_class", label="Class", field="asset_class", align="left"),
        TableColumn(name="invested", label="Invested", field="invested", align="right"),
        TableColumn(name="current", label="Current Value", field="current", align="right"),
        TableColumn(name="weight", label="Weight %", field="weight", align="right"),
        TableColumn(name="unrealized", label="Unrealized P&L", field="unrealized", align="right"),
        TableColumn(name="xirr", label="XIRR", field="xirr", align="right"),
    ]
    table_rows = [
        {
            "isin": h.isin,
            "name": h.instrument_name or h.isin,
            "asset_class": h.instrument_class,
            "invested": format_currency(h.book_value, currency_id),
            "current": format_currency(h.market_value, currency_id),
            "weight": format_percent(h.isin_weight) if h.isin_weight is not None else "—",
            "unrealized": format_currency(h.unrealized_pnl, currency_id),
            "xirr": format_percent(h.xirr_local) if h.xirr_local is not None else "—",
        }
        for h in snapshot.holdings
    ]
    with ui.column().classes("w-full gap-2"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            search_input = (
                ui.input(
                    placeholder="Search ISIN, instrument, class, subtype, sector...",
                )
                .props("dense outlined dark clearable")
                .classes("w-96")
            )
        table_box = ui.column().classes("w-full")

        def _render_filtered_holdings(query: str = "") -> None:
            q = query.strip().lower()
            if q:
                filtered = [
                    r
                    for r, h in zip(table_rows, snapshot.holdings, strict=True)
                    if q in h.isin.lower()
                    or q in (h.instrument_name or "").lower()
                    or q in (h.instrument_class or "").lower()
                    or q in (h.instrument_type or "").lower()
                    or q in (h.instrument_subtype or "").lower()
                    or q in (h.sector or "").lower()
                ]
            else:
                filtered = table_rows
            table_box.clear()
            with table_box:
                bounded_data_table(
                    title=f"Holdings by ISIN ({len(filtered)})",
                    columns=columns,
                    rows=filtered,
                    pagination=15,
                )

        search_input.on_value_change(lambda e: _render_filtered_holdings(str(e.value or "")))
        _render_filtered_holdings("")
        if snapshot.holdings:
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                isin_options: dict[str, str] = {
                    h.isin: f"{h.instrument_name or h.isin} ({h.isin})" for h in snapshot.holdings
                }
                picker = (
                    ui.select(
                        options=isin_options,
                        value=snapshot.holdings[0].isin,
                        label="Inspect ISIN",
                    )
                    .props("dense outlined dark")
                    .classes("w-80")
                )
                ui.button(
                    "Open ISIN Detail",
                    icon="open_in_new",
                    on_click=lambda: on_select_isin(str(picker.value)),
                ).props("outline dense")


def _render_performance_tab(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
) -> None:
    """Render the Phase 14 Performance, Benchmark, FX & Concentration sub-tab."""
    profile = context.profile
    if profile is None:
        return

    dim_state = {"value": AllocationDimension.CLASS.value}

    with ui.element("div").classes("studio-filter-bar w-full mb-3"):
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            dim_select = (
                ui.select(
                    options=_DIMENSION_OPTIONS,
                    value=dim_state["value"],
                    label="Segment Dimension",
                )
                .props("dense outlined dark")
                .classes("w-56")
            )

    perf_container = ui.column().classes("w-full gap-4")

    def _render_perf_body(dim_value: str) -> None:
        perf_container.clear()
        dim = AllocationDimension(dim_value)
        dim_label = _DIMENSION_OPTIONS.get(dim_value, dim_value)
        try:
            snap = InvestmentAnalytics.performance(
                profile,
                dim,
                context.period_context,
                run_id=context.provenance.run_id if context.provenance else None,
                provenance=context.provenance,
                catalog_snapshot=context.catalog_snapshot,
                filters=context.filters,
                cache=cache,
            )
        except Exception as exc:
            with perf_container:
                error_state_panel("Unable to load investment performance", str(exc))
            return

        if snap.state != "ready":
            with perf_container:
                empty_state_panel(
                    "No performance data found",
                    "No performance rows matched the active profile and period.",
                    icon="show_chart",
                )
            return

        currency_id = snap.currency_id or "INR_INR"
        with perf_container:
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2 mb-1"):
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    if snap.as_of_date is not None:
                        status_badge(f"As of: {snap.as_of_date.isoformat()}", tone=BadgeTone.INFO)
                    if snap.period_label:
                        status_badge(f"Period: {snap.period_label}", tone=BadgeTone.NEUTRAL)
                    status_badge(f"Segment: {dim_label}", tone=BadgeTone.NEUTRAL)

            _render_kpi_grid(snap.kpis, currency_id)

            with ui.element("div").classes("studio-chart-grid w-full"):
                _render_chart_card(
                    "Portfolio XIRR vs Benchmark XIRR",
                    build_inv_perf_xirr_vs_bm_figure(snap.history),
                )
                _render_chart_card(
                    "Asset P&L vs Forex P&L",
                    build_inv_perf_asset_vs_fx_pnl_figure(snap.history, currency_id),
                )
                _render_chart_card(
                    "Historical Max Drawdown",
                    build_inv_perf_drawdown_figure(snap.history),
                )
                _render_chart_card(
                    f"Segment XIRR vs Benchmark ({dim_label})",
                    build_inv_perf_segment_alpha_figure(
                        snap.segment_rows, dimension_label=dim_label
                    ),
                )
                _render_chart_card(
                    "Country / Currency FX Impact",
                    build_inv_perf_fx_impact_figure(snap.fx_country_rows or snap.fx_currency_rows),
                )
                _render_chart_card(
                    "Portfolio Concentration Ladder",
                    build_inv_perf_concentration_figure(snap.concentration),
                )
                _render_chart_card(
                    "Active Alpha Trend (vs Benchmark)",
                    build_inv_perf_alpha_trend_figure(snap.history),
                )
                _render_chart_card(
                    "Risk-Adjusted Return (XIRR % vs Max Drawdown %)",
                    build_inv_perf_risk_return_quadrant_figure(snap.isin_perf_rows, currency_id),
                )

            _render_segment_perf_table(snap.segment_rows, currency_id, dim_label)
            _render_fx_table(snap.fx_country_rows or snap.fx_currency_rows, currency_id)
            _render_isin_alpha_leaderboard(snap.isin_perf_rows, currency_id)

    dim_select.on_value_change(lambda e: _render_perf_body(str(e.value)))
    _render_perf_body(dim_state["value"])


def _render_segment_perf_table(
    rows: tuple[Any, ...],
    currency_id: str,
    dim_label: str,
) -> None:
    columns = [
        TableColumn(name="bucket", label=dim_label, field="bucket", align="left"),
        TableColumn(name="current", label="Current Value", field="current", align="right"),
        TableColumn(name="xirr", label="XIRR %", field="xirr", align="right"),
        TableColumn(name="bm_xirr", label="BM XIRR %", field="bm_xirr", align="right"),
        TableColumn(name="alpha", label="Active Alpha %", field="alpha", align="right"),
        TableColumn(name="dd", label="Max DD %", field="dd", align="right"),
    ]
    table_rows = [
        {
            "bucket": r.label,
            "current": format_currency(r.current_value, currency_id),
            "xirr": format_percent(r.xirr) if r.xirr is not None else "—",
            "bm_xirr": format_percent(r.bm_xirr) if r.bm_xirr is not None else "—",
            "alpha": format_percent(r.active_return) if r.active_return is not None else "—",
            "dd": format_percent(r.max_drawdown) if r.max_drawdown is not None else "—",
        }
        for r in rows
    ]
    bounded_data_table(
        title=f"Segment Performance by {dim_label}",
        columns=columns,
        rows=table_rows,
        pagination=12,
    )


def _render_fx_table(rows: tuple[Any, ...], currency_id: str) -> None:
    if not rows:
        return
    columns = [
        TableColumn(name="bucket", label="Country / Currency", field="bucket", align="left"),
        TableColumn(name="base_val", label="Base Value", field="base_val", align="right"),
        TableColumn(name="fx_rate", label="FX Rate", field="fx_rate", align="right"),
        TableColumn(name="local_xirr", label="Local XIRR %", field="local_xirr", align="right"),
        TableColumn(name="base_xirr", label="Base XIRR %", field="base_xirr", align="right"),
        TableColumn(name="fx_impact", label="FX Impact %", field="fx_impact", align="right"),
    ]
    table_rows = [
        {
            "bucket": r.bucket,
            "base_val": format_currency(r.base_current, currency_id),
            "fx_rate": f"{r.fx_rate:.4f}" if r.fx_rate is not None else "—",
            "local_xirr": format_percent(r.local_xirr) if r.local_xirr is not None else "—",
            "base_xirr": format_percent(r.base_xirr) if r.base_xirr is not None else "—",
            "fx_impact": format_percent(r.fx_xirr_impact) if r.fx_xirr_impact is not None else "—",
        }
        for r in rows
    ]
    bounded_data_table(
        title="Country & Currency FX Attribution",
        columns=columns,
        rows=table_rows,
        pagination=12,
    )


def _render_isin_alpha_leaderboard(rows: tuple[Any, ...], currency_id: str) -> None:
    if not rows:
        return
    sorted_rows = sorted(
        rows,
        key=lambda r: float(r.active_return) if r.active_return is not None else -9999.0,
        reverse=True,
    )
    columns = [
        TableColumn(name="isin", label="ISIN", field="isin", align="left"),
        TableColumn(name="current", label="Current Value", field="current", align="right"),
        TableColumn(name="xirr", label="XIRR %", field="xirr", align="right"),
        TableColumn(name="bm_xirr", label="BM XIRR %", field="bm_xirr", align="right"),
        TableColumn(name="alpha", label="Active Alpha %", field="alpha", align="right"),
        TableColumn(name="dd", label="Max DD %", field="dd", align="right"),
    ]
    table_rows = [
        {
            "isin": r.label,
            "current": format_currency(r.current_value, currency_id),
            "xirr": format_percent(r.xirr) if r.xirr is not None else "—",
            "bm_xirr": format_percent(r.bm_xirr) if r.bm_xirr is not None else "—",
            "alpha": format_percent(r.active_return) if r.active_return is not None else "—",
            "dd": format_percent(r.max_drawdown) if r.max_drawdown is not None else "—",
        }
        for r in sorted_rows
    ]
    with ui.column().classes("w-full gap-2"):
        search = (
            ui.input(placeholder="Search ISIN...")
            .props("dense outlined dark clearable")
            .classes("w-72")
        )
        box = ui.column().classes("w-full")

        def _render_filtered(q: str = "") -> None:
            ql = q.strip().lower()
            filtered = [r for r in table_rows if ql in str(r["isin"]).lower()] if ql else table_rows
            box.clear()
            with box:
                bounded_data_table(
                    title=f"ISIN Alpha Leaderboard ({len(filtered)})",
                    columns=columns,
                    rows=filtered,
                    pagination=15,
                )

        search.on_value_change(lambda e: _render_filtered(str(e.value or "")))
        _render_filtered("")


def _render_lots_tab(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
) -> None:
    """Render the Phase 15 Investment Lot Explorer sub-tab."""
    profile = context.profile
    if profile is None:
        return

    state: dict[str, Any] = {
        "search": "",
        "holding_type": "ALL",
        "harvest_rec": "ALL",
        "gain_state": "all",
        "max_days": "ALL",
        "offset": 0,
        "limit": 25,
    }

    with ui.element("div").classes("studio-filter-bar w-full mb-3"):
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            search_in = (
                ui.input(placeholder="Search Lot ID, ISIN, or Instrument...")
                .props("dense outlined dark clearable")
                .classes("w-64")
            )
            holding_sel = (
                ui.select(
                    options={"ALL": "All Holding Types", "LTCG": "LTCG", "STCG": "STCG"},
                    value="ALL",
                    label="Holding Type",
                )
                .props("dense outlined dark")
                .classes("w-44")
            )
            harvest_sel = (
                ui.select(
                    options={
                        "ALL": "All Harvest Flags",
                        "HARVEST_LOSS": "Harvest Loss",
                        "WAIT_FOR_LTCG": "Wait for LTCG",
                        "HARVEST_LTCG_EXEMPT": "Harvest LTCG Exempt",
                        "HOLD": "Hold",
                    },
                    value="ALL",
                    label="Harvest Recommendation",
                )
                .props("dense outlined dark")
                .classes("w-48")
            )
            gain_sel = (
                ui.select(
                    options={"all": "All P/L", "gain": "Gains Only", "loss": "Losses Only"},
                    value="all",
                    label="Gain / Loss",
                )
                .props("dense outlined dark")
                .classes("w-36")
            )
            days_sel = (
                ui.select(
                    options={
                        "ALL": "Any Days to LTCG",
                        "30": "≤ 30 Days",
                        "90": "≤ 90 Days",
                        "180": "≤ 180 Days",
                    },
                    value="ALL",
                    label="Maturing to LTCG",
                )
                .props("dense outlined dark")
                .classes("w-44")
            )

    drawer_box = ui.column().classes("w-full")
    lots_container = ui.column().classes("w-full gap-4")

    def _open_lot_detail(lot_id: str, closing_dt: Any) -> None:
        detail = InvestmentAnalytics.lot_detail(
            profile,
            lot_id,
            closing_dt,
            catalog_snapshot=context.catalog_snapshot,
        )
        drawer_box.clear()
        with drawer_box:
            with (
                ui.dialog() as dlg,
                ui.card()
                .classes("w-full max-w-4xl p-5")
                .style(
                    "background: #0F172A; border: 1px solid rgba(148, 163, 184, 0.25); color: #F8FAFC; max-height: 85vh; overflow-y: auto;"
                ),
            ):
                if detail is None:
                    ui.label(f"Lot {lot_id} not found").classes("text-subtitle1 font-semibold")
                    ui.button("Close", on_click=dlg.close).props("flat")
                else:
                    with ui.row().classes("w-full items-center justify-between mb-3"):
                        with ui.column().classes("gap-0"):
                            ui.label(detail.instrument_name or detail.isin).classes(
                                "text-h6 font-semibold"
                            ).style("color: var(--studio-text-primary);")
                            ui.label(
                                f"Lot: {detail.lot_id} · ISIN: {detail.isin} · Close: {detail.closing_date.isoformat()}"
                            ).classes("text-caption").style("color: var(--studio-text-muted);")
                        with ui.row().classes("items-center gap-2"):
                            raw_toggle = ui.switch("Raw Source Fields", value=False).props("dense")
                            ui.button(icon="close", on_click=dlg.close).props("flat round dense")

                    sections_box = ui.column().classes("w-full gap-3")

                    def _render_sections(show_raw: bool = False) -> None:
                        sections_box.clear()
                        with sections_box:
                            if show_raw:
                                raw_cols = [
                                    TableColumn(
                                        name="col", label="Source Column", field="col", align="left"
                                    ),
                                    TableColumn(
                                        name="val", label="Raw Value", field="val", align="left"
                                    ),
                                ]
                                raw_rows = [
                                    {"col": k, "val": str(v) if v is not None else "—"}
                                    for k, v in detail.raw_columns.items()
                                ]
                                bounded_data_table(
                                    title=f"All Source Columns ({len(raw_rows)})",
                                    columns=raw_cols,
                                    rows=raw_rows,
                                    pagination=20,
                                )
                            else:
                                for sec in detail.sections:
                                    with (
                                        ui.card()
                                        .classes("w-full p-3")
                                        .style(
                                            "background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(148, 163, 184, 0.16);"
                                        )
                                    ):
                                        ui.label(sec.title.upper()).classes(
                                            "text-caption font-semibold tracking-wider mb-2"
                                        ).style("color: var(--studio-accent);")
                                        with ui.grid(columns=3).classes("w-full gap-2"):
                                            for lbl, _raw_name, val in sec.fields:
                                                disp = (
                                                    f"{val:,.4f}"
                                                    if isinstance(val, float)
                                                    else (str(val) if val is not None else "—")
                                                )
                                                _detail_stat(lbl, disp)

                    raw_toggle.on_value_change(lambda e: _render_sections(bool(e.value)))
                    _render_sections(False)
            dlg.open()

    def _build_filter() -> LotFilter:
        max_d = int(state["max_days"]) if state["max_days"] != "ALL" else None
        return LotFilter(
            holding_type=None if state["holding_type"] == "ALL" else str(state["holding_type"]),
            harvest_recommendation=None
            if state["harvest_rec"] == "ALL"
            else str(state["harvest_rec"]),
            can_harvest_loss=True if state["harvest_rec"] == "HARVEST_LOSS" else None,
            days_to_ltcg_max=max_d,
            gain_state=state["gain_state"],
            search=str(state["search"]).strip() or None,
        )

    def _render_lots_body() -> None:
        lots_container.clear()
        lf = _build_filter()
        try:
            snap = InvestmentAnalytics.lot_rows(
                profile,
                lf,
                context.period_context,
                limit=int(state["limit"]),
                offset=int(state["offset"]),
                run_id=context.provenance.run_id if context.provenance else None,
                provenance=context.provenance,
                catalog_snapshot=context.catalog_snapshot,
                cache=cache,
            )
        except Exception as exc:
            with lots_container:
                error_state_panel("Unable to load investment lots", str(exc))
            return

        if snap.state != "ready" or not snap.rows:
            msg = "No lots matched the active filter criteria."
            if snap.nearest_dates:
                msg += f" Nearest available dates: {', '.join(d.isoformat() for d in snap.nearest_dates)}."
            with lots_container:
                empty_state_panel("No lots found", msg, icon="inventory_2")
            return

        currency_id = snap.currency_id or "INR_INR"
        with lots_container:
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2 mb-1"):
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    if snap.closing_date is not None:
                        status_badge(
                            f"As of: {snap.closing_date.isoformat()}",
                            tone=BadgeTone.INFO,
                        )
                    status_badge(f"Matching Lots: {snap.total_count}", tone=BadgeTone.NEUTRAL)

            _render_kpi_grid(snap.kpis, currency_id)

            with ui.element("div").classes("studio-chart-grid w-full"):
                _render_chart_card(
                    "LTCG vs STCG Market Value",
                    build_lot_holding_type_split_figure(snap.summary, currency_id),
                )
                _render_chart_card(
                    "Lots by Harvest Recommendation",
                    build_lot_harvest_rec_figure(snap.chart_rows, currency_id),
                )
                _render_chart_card(
                    "STCG Lots Maturing to LTCG",
                    build_lot_days_to_ltcg_figure(snap.chart_rows, currency_id),
                )
                _render_chart_card(
                    "Lot Age vs XIRR %",
                    build_lot_alpha_scatter_figure(snap.chart_rows, currency_id),
                )
                _render_chart_card(
                    "Pre-Tax vs After-Tax P/L (Top 10 Lots)",
                    build_lot_pre_vs_after_tax_figure(snap.chart_rows, currency_id),
                )
                _render_chart_card(
                    "Lot Gain vs Loss by Asset Class",
                    build_lot_gain_loss_by_class_figure(snap.chart_rows, currency_id),
                )

            cols = [
                TableColumn(name="lot_id", label="Lot ID", field="lot_id", align="left"),
                TableColumn(name="inst", label="Instrument", field="inst", align="left"),
                TableColumn(name="buy_dt", label="Buy Date", field="buy_dt", align="left"),
                TableColumn(name="qty", label="Qty", field="qty", align="right"),
                TableColumn(name="buy_val", label="Cost", field="buy_val", align="right"),
                TableColumn(name="close_val", label="Value", field="close_val", align="right"),
                TableColumn(name="pl", label="P/L", field="pl", align="right"),
                TableColumn(name="xirr", label="XIRR", field="xirr", align="right"),
                TableColumn(name="hold", label="Type", field="hold", align="left"),
                TableColumn(name="d_ltcg", label="Days LTCG", field="d_ltcg", align="right"),
                TableColumn(name="tax", label="Tax If Sold", field="tax", align="right"),
                TableColumn(name="rec", label="Harvest", field="rec", align="left"),
            ]
            t_rows = [
                {
                    "lot_id": r.lot_id,
                    "inst": r.instrument_name[:30],
                    "buy_dt": r.buy_date.isoformat() if r.buy_date else "—",
                    "qty": f"{r.quantity:,.2f}",
                    "buy_val": format_currency(r.buy_value, currency_id),
                    "close_val": format_currency(r.close_value, currency_id),
                    "pl": format_currency(r.unrealized_pl, currency_id),
                    "xirr": format_percent(r.xirr) if r.xirr is not None else "—",
                    "hold": r.holding_type,
                    "d_ltcg": str(r.days_to_ltcg) if r.days_to_ltcg is not None else "—",
                    "tax": format_currency(r.tax_if_sold, currency_id),
                    "rec": r.harvest_recommendation,
                }
                for r in snap.chart_rows
            ]
            bounded_data_table(
                title=f"Lot Explorer ({snap.total_count} lots)",
                columns=cols,
                rows=t_rows,
                pagination=int(state["limit"]),
            )
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
                with ui.row().classes("items-center gap-2"):
                    lot_opts = {
                        r.lot_id: f"{r.instrument_name[:28]} ({r.lot_id})" for r in snap.rows
                    }
                    lot_picker = (
                        ui.select(
                            options=lot_opts,
                            value=snap.rows[0].lot_id,
                            label="Inspect Lot (88 Columns)",
                        )
                        .props("dense outlined dark")
                        .classes("w-80")
                    )
                    ui.button(
                        "Inspect Lot Detail",
                        icon="open_in_new",
                        on_click=lambda: _open_lot_detail(str(lot_picker.value), snap.closing_date),
                    ).props("outline dense")
                with ui.row().classes("items-center gap-2"):

                    def _prev_page() -> None:
                        state["offset"] = max(0, int(state["offset"]) - int(state["limit"]))
                        _render_lots_body()

                    def _next_page() -> None:
                        state["offset"] = int(state["offset"]) + int(state["limit"])
                        _render_lots_body()

                    ui.button(
                        "Prev Page",
                        icon="chevron_left",
                        on_click=_prev_page,
                    ).props("outline dense").set_enabled(int(state["offset"]) > 0)
                    ui.button(
                        "Next Page",
                        icon="chevron_right",
                        on_click=_next_page,
                    ).props("outline dense").set_enabled(
                        int(state["offset"]) + int(state["limit"]) < snap.total_count
                    )

    def _reset_and_render(key: str, val: Any) -> None:
        state[key] = val
        state["offset"] = 0
        _render_lots_body()

    search_in.on_value_change(lambda e: _reset_and_render("search", e.value or ""))
    holding_sel.on_value_change(lambda e: _reset_and_render("holding_type", e.value))
    harvest_sel.on_value_change(lambda e: _reset_and_render("harvest_rec", e.value))
    gain_sel.on_value_change(lambda e: _reset_and_render("gain_state", e.value))
    days_sel.on_value_change(lambda e: _reset_and_render("max_days", e.value))
    _render_lots_body()
