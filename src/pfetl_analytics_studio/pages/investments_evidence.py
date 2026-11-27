"""Realized, Reconciliation & Market Evidence UI sub-tabs."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from pfetl_analytics_studio.analytics import (
    InvestmentAnalytics,
    OverviewKPI,
    RealizedEventFilter,
)
from pfetl_analytics_studio.context import AppContext
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.ui import (
    BadgeTone,
    TableColumn,
    bounded_data_table,
    build_inv_evidence_benchmark_figure,
    build_inv_evidence_fx_figure,
    build_inv_evidence_normalized_index_figure,
    build_inv_evidence_price_figure,
    build_inv_evidence_provenance_bar_figure,
    build_inv_evidence_value_area_figure,
    build_inv_realized_asset_vs_fx_figure,
    build_inv_realized_fy_pnl_figure,
    build_inv_realized_tax_drag_figure,
    build_inv_realized_tax_type_donut_figure,
    build_inv_recon_adjustments_figure,
    build_inv_recon_qty_bridge_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    format_currency,
    format_metric_value,
    kpi_card,
    status_badge,
)


def _render_kpi_grid(kpis: tuple[OverviewKPI, ...], currency_id: str) -> None:
    with ui.element("div").classes("studio-kpi-grid w-full mb-4"):
        for kpi in kpis:
            delta_text: str | None = None
            delta_tone = BadgeTone.NEUTRAL
            if kpi.delta_value is not None and kpi.delta_format is not None:
                d_str = format_metric_value(
                    kpi.delta_value, kpi.delta_format, currency_id=currency_id, compact=True
                )
                delta_text = f"{kpi.delta_label}: {d_str}" if kpi.delta_label else d_str
                delta_tone = BadgeTone.INFO if kpi.delta_value >= 0 else BadgeTone.NEUTRAL
            breakups = [
                (
                    b.label,
                    format_metric_value(b.value, b.format, currency_id=currency_id, compact=True),
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
        title=title, subtitle="", figure=figure, source_caption="Source: silver.fact_investment_*"
    )


def _detail_stat(label: str, value: str) -> None:
    with ui.column().classes("gap-0 p-2 rounded").style("background: rgba(15, 23, 42, 0.55);"):
        ui.label(label).classes("text-caption").style("color: var(--studio-text-muted);")
        ui.label(value).classes("text-body2 font-semibold").style(
            "color: var(--studio-text-primary);"
        )


def render_realized_tab(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
) -> None:
    """Render the Realized Events, Lineage & Reconciliation sub-tab."""
    profile = context.profile
    if profile is None:
        return

    state: dict[str, str] = {"fy": "ALL", "holding_type": "ALL", "gain_state": "all", "search": ""}

    with ui.element("div").classes("studio-filter-bar w-full mb-3"):
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            search_in = (
                ui.input(placeholder="Search Event ID, Sale ID, Lot ID, ISIN...")
                .props("dense outlined dark clearable")
                .classes("w-72")
            )
            fy_sel = (
                ui.select(options={"ALL": "All FYs"}, value="ALL", label="Financial Year")
                .props("dense outlined dark")
                .classes("w-40")
            )
            hold_sel = (
                ui.select(
                    options={"ALL": "All Types", "LTCG": "LTCG", "STCG": "STCG"},
                    value="ALL",
                    label="Holding Type",
                )
                .props("dense outlined dark")
                .classes("w-36")
            )
            gain_sel = (
                ui.select(
                    options={"all": "All P/L", "gain": "Gains", "loss": "Losses"},
                    value="all",
                    label="Gain / Loss",
                )
                .props("dense outlined dark")
                .classes("w-36")
            )

    drawer_box = ui.column().classes("w-full")
    body_box = ui.column().classes("w-full gap-4")
    rendering = {"active": False}

    def _open_lineage_drawer(realized_event_id: str) -> None:
        trace = InvestmentAnalytics.trace_lineage(
            profile, realized_event_id, catalog_snapshot=context.catalog_snapshot
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
                if trace is None:
                    ui.label(f"Realized Event {realized_event_id} not found").classes(
                        "text-subtitle1"
                    )
                    ui.button("Close", on_click=dlg.close).props("flat")
                else:
                    ev = trace.realized_event
                    with ui.row().classes("w-full items-center justify-between mb-3"):
                        with ui.column().classes("gap-0"):
                            ui.label(f"Trade Lineage: {ev.instrument_name}").classes(
                                "text-h6 font-semibold"
                            )
                            ui.label(
                                f"Event: {ev.realized_event_id} · Sale: {ev.sale_id} · Lot: {ev.lot_id} · Purchase: {ev.purchase_id}"
                            ).classes("text-caption").style("color: var(--studio-text-muted);")
                        ui.button(icon="close", on_click=dlg.close).props("flat round dense")

                    if trace.missing_links:
                        status_badge(
                            f"Broken Links: {', '.join(trace.missing_links)}",
                            tone=BadgeTone.WARNING,
                        )

                    for title, rec in (
                        ("1. Sale Record (fact_investment_sales)", trace.sale_record),
                        ("2. Realized Event (fact_investment_realized_events)", ev.model_dump()),
                        ("3. Lot Snapshot (fact_investment_lot_analytics)", trace.lot_record),
                        ("4. Purchase Record (fact_investment_purchases)", trace.purchase_record),
                    ):
                        with (
                            ui.card()
                            .classes("w-full p-3 mb-2")
                            .style(
                                "background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(148, 163, 184, 0.16);"
                            )
                        ):
                            ui.label(title).classes("text-caption font-semibold mb-2").style(
                                "color: var(--studio-accent);"
                            )
                            if rec is None:
                                ui.label("Record not found in warehouse.").classes(
                                    "text-caption"
                                ).style("color: var(--studio-text-muted);")
                            else:
                                with ui.grid(columns=3).classes("w-full gap-2"):
                                    for k, v in list(rec.items())[:12]:
                                        _detail_stat(str(k), str(v) if v is not None else "—")
            dlg.open()

    def _render_body() -> None:
        if rendering["active"]:
            return
        rendering["active"] = True
        try:
            _render_body_inner()
        finally:
            rendering["active"] = False

    def _render_body_inner() -> None:
        body_box.clear()
        rf = RealizedEventFilter(
            fy=None if state["fy"] == "ALL" else state["fy"],
            holding_type=None if state["holding_type"] == "ALL" else state["holding_type"],
            gain_state=state["gain_state"],  # type: ignore[arg-type]
            search=state["search"].strip() or None,
        )
        try:
            snap = InvestmentAnalytics.realized_and_reconciliation(
                profile,
                rf,
                recon_search=state["search"].strip() or None,
                run_id=context.provenance.run_id if context.provenance else None,
                provenance=context.provenance,
                catalog_snapshot=context.catalog_snapshot,
                cache=cache,
            )
        except Exception as exc:
            with body_box:
                error_state_panel("Unable to load realized & reconciliation data", str(exc))
            return

        if snap.available_fys:
            fy_opts = {"ALL": "All FYs", **{f: f for f in snap.available_fys}}
            fy_sel.options = fy_opts
            fy_sel.value = state["fy"] if state["fy"] in fy_opts else "ALL"
            fy_sel.update()

        if snap.state != "ready":
            with body_box:
                empty_state_panel(
                    "No realized or reconciliation records found",
                    "No records matched the active filter.",
                    icon="fact_check",
                )
            return

        currency_id = snap.currency_id or "INR_INR"
        with body_box:
            _render_kpi_grid(snap.kpis, currency_id)

            with ui.element("div").classes("studio-chart-grid w-full"):
                _render_chart_card(
                    "Realized LTCG vs STCG P&L by FY",
                    build_inv_realized_fy_pnl_figure(snap.realized_rows, currency_id),
                )
                _render_chart_card(
                    "Top 10 Disposals: Pre-Tax P&L vs Est. Tax",
                    build_inv_realized_tax_drag_figure(snap.realized_rows, currency_id),
                )
                _render_chart_card(
                    "Realized Asset vs Forex P&L by FY",
                    build_inv_realized_asset_vs_fx_figure(snap.realized_rows, currency_id),
                )
                _render_chart_card(
                    "Proceeds by Tax & Holding Type",
                    build_inv_realized_tax_type_donut_figure(snap.realized_rows, currency_id),
                )
                _render_chart_card(
                    "Reconciliation Cost Basis Adjustments",
                    build_inv_recon_adjustments_figure(snap.reconciliation_groups, currency_id),
                )
                _render_chart_card(
                    "Broker vs Reconstructed Quantity Bridge",
                    build_inv_recon_qty_bridge_figure(snap.reconciliation_groups),
                )

            if snap.realized_rows:
                r_cols = [
                    TableColumn(name="ev", label="Event ID", field="ev", align="left"),
                    TableColumn(name="inst", label="Instrument", field="inst", align="left"),
                    TableColumn(name="fy", label="FY", field="fy", align="left"),
                    TableColumn(name="disp", label="Disposal", field="disp", align="left"),
                    TableColumn(name="qty", label="Qty", field="qty", align="right"),
                    TableColumn(name="cost", label="Cost Basis", field="cost", align="right"),
                    TableColumn(name="proc", label="Proceeds", field="proc", align="right"),
                    TableColumn(name="gl", label="Realized P&L", field="gl", align="right"),
                    TableColumn(name="tax", label="Est. Tax", field="tax", align="right"),
                    TableColumn(name="type", label="Type", field="type", align="left"),
                ]
                r_rows = [
                    {
                        "ev": r.realized_event_id,
                        "inst": r.instrument_name[:28],
                        "fy": r.fy,
                        "disp": r.disposal_date.isoformat() if r.disposal_date else "—",
                        "qty": f"{r.quantity_disposed:,.2f}",
                        "cost": format_currency(r.disposed_cost_basis, currency_id),
                        "proc": format_currency(r.sale_proceeds, currency_id),
                        "gl": format_currency(r.realized_gain_loss, currency_id),
                        "tax": format_currency(r.estimated_tax, currency_id),
                        "type": r.holding_type,
                    }
                    for r in snap.realized_rows
                ]
                bounded_data_table(
                    title=f"Realized FIFO Events ({len(r_rows)} · click any row to trace lineage)",
                    columns=r_cols,
                    rows=r_rows,
                    pagination=15,
                    on_row_click=lambda row: _open_lineage_drawer(str(row.get("ev", ""))),
                )

            if snap.reconciliation_groups:
                g_cols = [
                    TableColumn(name="grp", label="Group ID", field="grp", align="left"),
                    TableColumn(name="inst", label="Instrument", field="inst", align="left"),
                    TableColumn(name="adj_t", label="Adjustment Type", field="adj_t", align="left"),
                    TableColumn(name="reason", label="Reason", field="reason", align="left"),
                    TableColumn(name="cnt", label="Events", field="cnt", align="right"),
                    TableColumn(name="bq", label="Broker Qty", field="bq", align="right"),
                    TableColumn(name="rq", label="Recon Qty", field="rq", align="right"),
                    TableColumn(name="qa", label="Qty Adj", field="qa", align="right"),
                    TableColumn(name="ca", label="Cost Adj", field="ca", align="right"),
                ]
                g_rows = [
                    {
                        "grp": g.reconciliation_group_id,
                        "inst": g.instrument_name[:26],
                        "adj_t": g.adjustment_type,
                        "reason": g.reason[:32],
                        "cnt": str(g.event_count),
                        "bq": f"{g.broker_quantity:,.2f}",
                        "rq": f"{g.reconstructed_quantity:,.2f}",
                        "qa": f"{g.quantity_adjustment:,.2f}",
                        "ca": format_currency(g.cost_basis_adjustment, currency_id),
                    }
                    for g in snap.reconciliation_groups
                ]
                bounded_data_table(
                    title=f"Reconciliation Groups ({len(g_rows)})",
                    columns=g_cols,
                    rows=g_rows,
                    pagination=15,
                )

    def _on_chg(k: str, v: Any) -> None:
        state[k] = str(v or "")
        _render_body()

    search_in.on_value_change(lambda e: _on_chg("search", e.value))
    fy_sel.on_value_change(lambda e: _on_chg("fy", e.value))
    hold_sel.on_value_change(lambda e: _on_chg("holding_type", e.value))
    gain_sel.on_value_change(lambda e: _on_chg("gain_state", e.value))
    _render_body()


def render_evidence_tab(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
) -> None:
    """Render the Market, Benchmark & FX Evidence sub-tab."""
    profile = context.profile
    if profile is None:
        return

    sel: dict[str, str | None] = {"isin": None, "bm": None, "cur": None}

    with ui.element("div").classes("studio-filter-bar w-full mb-3"):
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            isin_sel = (
                ui.select(options={}, value=None, label="Instrument (ISIN)")
                .props("dense outlined dark")
                .classes("w-72")
            )
            bm_sel = (
                ui.select(options={}, value=None, label="Benchmark")
                .props("dense outlined dark")
                .classes("w-60")
            )
            cur_sel = (
                ui.select(options={}, value=None, label="FX Currency Pair")
                .props("dense outlined dark")
                .classes("w-44")
            )

    ev_box = ui.column().classes("w-full gap-4")
    ev_rendering = {"active": False}

    def _render_ev_body() -> None:
        if ev_rendering["active"]:
            return
        ev_rendering["active"] = True
        try:
            _render_ev_body_inner()
        finally:
            ev_rendering["active"] = False

    def _render_ev_body_inner() -> None:
        ev_box.clear()
        try:
            snap = InvestmentAnalytics.market_evidence(
                profile,
                context.period_context,
                isin=sel["isin"],
                benchmark_id=sel["bm"],
                currency_id=sel["cur"],
                run_id=context.provenance.run_id if context.provenance else None,
                provenance=context.provenance,
                catalog_snapshot=context.catalog_snapshot,
                cache=cache,
            )
        except Exception as exc:
            with ev_box:
                error_state_panel("Unable to load market evidence", str(exc))
            return

        if snap.available_isins:
            i_opts = {k: f"{v[:30]} ({k})" for k, v in snap.available_isins}
            isin_sel.options = i_opts
            isin_sel.value = snap.selected_isin or None
            isin_sel.update()
        if snap.available_benchmarks:
            b_opts = dict(snap.available_benchmarks)
            bm_sel.options = b_opts
            bm_sel.value = snap.selected_benchmark_id or None
            bm_sel.update()
        if snap.available_currencies:
            c_opts = {c: c for c in snap.available_currencies}
            cur_sel.options = c_opts
            cur_sel.value = snap.selected_currency_id or None
            cur_sel.update()

        if snap.state != "ready":
            with ev_box:
                empty_state_panel(
                    "No market evidence observations found",
                    "No Market, Benchmark, or FX observations exist in the active period.",
                    icon="candlestick_chart",
                )
            return

        currency_id = snap.currency_id or "INR_INR"
        with ev_box:
            _render_kpi_grid(snap.kpis, currency_id)

            with ui.element("div").classes("studio-chart-grid w-full"):
                _render_chart_card(
                    "Instrument Closing Price & Provenance",
                    build_inv_evidence_price_figure(snap.market_points, currency_id),
                )
                _render_chart_card(
                    "Instrument Market Value vs Cost Basis",
                    build_inv_evidence_value_area_figure(snap.market_points, currency_id),
                )
                _render_chart_card(
                    "Benchmark Close & Provenance",
                    build_inv_evidence_benchmark_figure(snap.benchmark_points),
                )
                _render_chart_card(
                    "Daily FX Rate & Provenance",
                    build_inv_evidence_fx_figure(snap.fx_points),
                )
                _render_chart_card(
                    "Normalized Index (Base = 100): Instrument vs Benchmark",
                    build_inv_evidence_normalized_index_figure(
                        snap.market_points, snap.benchmark_points
                    ),
                )
                _render_chart_card(
                    "Observation Provenance (Observed vs Imputed vs Gap)",
                    build_inv_evidence_provenance_bar_figure(
                        snap.market_points, snap.benchmark_points, snap.fx_points
                    ),
                )

            if snap.market_points:
                m_cols = [
                    TableColumn(name="dt", label="Date", field="dt", align="left"),
                    TableColumn(name="qty", label="Units", field="qty", align="right"),
                    TableColumn(name="buy_px", label="Avg Buy", field="buy_px", align="right"),
                    TableColumn(name="px", label="Close Price", field="px", align="right"),
                    TableColumn(name="cost", label="Cost Basis", field="cost", align="right"),
                    TableColumn(name="val", label="Market Value", field="val", align="right"),
                    TableColumn(name="pnl", label="Unrealized P&L", field="pnl", align="right"),
                    TableColumn(name="prov", label="Provider", field="prov", align="left"),
                    TableColumn(name="imp", label="Imputed", field="imp", align="left"),
                    TableColumn(name="gap", label="Gap", field="gap", align="left"),
                ]
                m_rows = [
                    {
                        "dt": p.obs_date.isoformat(),
                        "qty": f"{p.quantity:,.2f}",
                        "buy_px": format_currency(p.buy_price, currency_id),
                        "px": format_currency(p.closing_price, currency_id),
                        "cost": format_currency(p.buy_value, currency_id),
                        "val": format_currency(p.closing_value, currency_id),
                        "pnl": format_currency(p.closing_value - p.buy_value, currency_id),
                        "prov": p.data_provider or "—",
                        "imp": "Yes" if p.is_imputed else "No",
                        "gap": "Yes" if p.is_closure_gap else "No",
                    }
                    for p in reversed(snap.market_points)
                ]
                bounded_data_table(
                    title=f"Daily Market Observations ({len(m_rows)})",
                    columns=m_cols,
                    rows=m_rows,
                    pagination=15,
                )

    def _on_sel(k: str, v: Any) -> None:
        sel[k] = str(v) if v else None
        _render_ev_body()

    isin_sel.on_value_change(lambda e: _on_sel("isin", e.value))
    bm_sel.on_value_change(lambda e: _on_sel("bm", e.value))
    cur_sel.on_value_change(lambda e: _on_sel("cur", e.value))
    _render_ev_body()
