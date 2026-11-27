"""Tax page module — FY tax cockpit, reconciliation, events, and harvesting."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from pfetl_analytics_studio.analytics import (
    InvestmentAnalytics,
    OverviewKPI,
    TaxAnalytics,
    TaxEventFilter,
    TaxFYParameters,
)
from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.pages import render_domain_workspace
from pfetl_analytics_studio.semantic import FilterField
from pfetl_analytics_studio.ui import (
    BadgeTone,
    TableColumn,
    bounded_data_table,
    build_tax_completeness_figure,
    build_tax_composition_donut_figure,
    build_tax_events_status_source_figure,
    build_tax_events_timeline_figure,
    build_tax_fy_readiness_figure,
    build_tax_fy_trend_figure,
    build_tax_harvesting_exposure_figure,
    build_tax_harvesting_holding_figure,
    build_tax_head_breakdown_figure,
    build_tax_indicative_exposure_figure,
    build_tax_recon_bridge_figure,
    build_tax_source_type_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    format_currency,
    format_metric_value,
    format_percent,
    kpi_card,
    open_lot_detail_dialog,
    status_badge,
)

_SUB_TABS: tuple[tuple[str, str, str], ...] = (
    ("overview", "FY Overview & Exposure", "account_balance"),
    ("breakdown", "Income Head Breakdown", "category"),
    ("reconciliation", "Reconciliation & Audit", "fact_check"),
    ("events", "Tax Events & Harvesting", "receipt_long"),
)


def render_tax_page(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
    initial_tab: str = "overview",
    on_tab_change: Callable[[str], Any] | None = None,
) -> None:
    """Render the Tax workspace from shared `AppContext`."""
    route = ROUTES_BY_ID["tax"]
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

        with (
            ui.card()
            .classes("w-full p-3")
            .style(
                "background: rgba(245, 158, 11, 0.10); border: 1px solid rgba(245, 158, 11, 0.35);"
            )
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon("info_outline", size="18px").style("color: #FBBF24;")
                ui.label(
                    "Indicative / estimated, not statutory tax liability. Figures reflect PFETL rules and estimate completeness metadata."
                ).classes("text-caption font-medium").style("color: #FDE68A;")

        if context.profile is None:
            render_domain_workspace(
                context=context,
                route=route,
                workspace_views=tuple(lbl for _, lbl, _ in _SUB_TABS),
                upstream_sources=(
                    "gold.tax_financial_year_summary",
                    "gold.tax_income_breakdown",
                    "gold.tax_reconciliation",
                    "silver.fact_tax_events",
                    "silver.ref_financial_year_parameters",
                ),
            )
            return

        profile = context.profile
        active_tab = initial_tab if any(t[0] == initial_tab for t in _SUB_TABS) else "overview"

        fy_state: dict[str, Any] = {"fy": None, "rendering": False}
        with ui.element("div").classes("studio-filter-bar w-full"):
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
                fy_sel = (
                    ui.select(options={}, value=None, label="Financial Year")
                    .props("dense outlined dark")
                    .classes("w-48")
                )
                status_box = ui.row().classes("items-center gap-2 flex-wrap")

        with ui.tabs().classes("w-full studio-subtabs") as tabs:
            for tab_id, tab_label, tab_icon in _SUB_TABS:
                ui.tab(tab_id, label=tab_label, icon=tab_icon)
        tabs.set_value(active_tab)

        if on_tab_change is not None:
            tabs.on_value_change(lambda e: on_tab_change(str(e.value)))

        with ui.tab_panels(tabs, value=active_tab).classes("w-full bg-transparent p-0"):
            p_overview = ui.tab_panel("overview").classes("p-0 pt-3")
            p_breakdown = ui.tab_panel("breakdown").classes("p-0 pt-3")
            p_recon = ui.tab_panel("reconciliation").classes("p-0 pt-3")
            p_events = ui.tab_panel("events").classes("p-0 pt-3")

        def _render_all() -> None:
            if fy_state["rendering"]:
                return
            fy_state["rendering"] = True
            try:
                _render_all_inner()
            finally:
                fy_state["rendering"] = False

        def _render_all_inner() -> None:
            try:
                summary = TaxAnalytics.fy_summary(
                    profile,
                    fy_state["fy"],
                    run_id=context.provenance.run_id if context.provenance else None,
                    provenance=context.provenance,
                    catalog_snapshot=context.catalog_snapshot,
                    cache=cache,
                )
            except Exception as exc:
                p_overview.clear()
                with p_overview:
                    error_state_panel("Unable to load Tax FY summary", str(exc))
                return

            if summary.available_fys:
                fy_opts = {f: f"FY {f}" for f in summary.available_fys}
                fy_sel.options = fy_opts
                fy_sel.value = summary.fy or summary.available_fys[0]
                fy_sel.update()
                fy_state["fy"] = summary.fy

            status_box.clear()
            with status_box:
                if summary.fy:
                    status_badge(f"FY {summary.fy}", tone=BadgeTone.INFO)
                status_badge(
                    "Ledger Tax: Complete"
                    if summary.ledger_tax_estimate_complete
                    else f"Ledger Tax: Incomplete ({summary.ledger_tax_estimate_missing_count} missing)",
                    tone=BadgeTone.INFO
                    if summary.ledger_tax_estimate_complete
                    else BadgeTone.WARNING,
                )
                status_badge(
                    "Inv. Tax: Complete"
                    if summary.investment_tax_estimate_complete
                    else f"Inv. Tax: Incomplete ({summary.investment_tax_estimate_missing_count} missing)",
                    tone=BadgeTone.INFO
                    if summary.investment_tax_estimate_complete
                    else BadgeTone.WARNING,
                )
                if summary.check_required_count > 0:
                    status_badge(
                        f"Check Required: {summary.check_required_count}",
                        tone=BadgeTone.WARNING,
                    )

            currency_id = summary.currency_id or "INR_INR"
            active_fy = summary.fy

            p_overview.clear()
            with p_overview:
                if summary.state != "ready":
                    empty_state_panel(
                        "No Tax summary found",
                        "No Gold tax summary data matched the selected Financial Year.",
                        icon="account_balance",
                    )
                else:
                    _render_kpi_grid(summary.kpis, currency_id)
                    with ui.element("div").classes("studio-chart-grid w-full mb-4"):
                        _render_chart_card(
                            "Multi-FY Tax & Income Trend",
                            build_tax_fy_trend_figure(summary.all_fy_rows, currency_id),
                            "gold.tax_financial_year_summary",
                        )
                        _render_chart_card(
                            f"FY {active_fy} Tax Composition",
                            build_tax_composition_donut_figure(summary, currency_id),
                            "gold.tax_financial_year_summary",
                        )
                        _render_chart_card(
                            f"FY {active_fy} Indicative Exposure & Savings",
                            build_tax_indicative_exposure_figure(summary, currency_id),
                            "gold.tax_financial_year_summary",
                        )
                        _render_chart_card(
                            "Multi-FY Tax Event Readiness",
                            build_tax_fy_readiness_figure(summary.all_fy_rows),
                            "gold.tax_financial_year_summary",
                        )
                    _render_fy_params_card(summary.fy_params, active_fy, currency_id)

            p_breakdown.clear()
            with p_breakdown:
                _render_breakdown_section(context, active_fy, currency_id, cache=cache)

            p_recon.clear()
            with p_recon:
                _render_reconciliation_section(context, active_fy, currency_id, cache=cache)

            p_events.clear()
            with p_events:
                _render_events_and_harvest_section(context, active_fy, currency_id, cache=cache)

        def _on_fy_change(e: Any) -> None:
            if fy_state["rendering"]:
                return
            fy_state["fy"] = str(e.value) if e.value else None
            _render_all()

        fy_sel.on_value_change(_on_fy_change)
        _render_all()


def _render_breakdown_section(
    context: AppContext,
    fy: str,
    currency_id: str,
    *,
    cache: QueryCache | None = None,
) -> None:
    profile = context.profile
    if profile is None or not fy:
        return
    snap = TaxAnalytics.income_breakdown(
        profile,
        fy,
        run_id=context.provenance.run_id if context.provenance else None,
        provenance=context.provenance,
        catalog_snapshot=context.catalog_snapshot,
        cache=cache,
    )
    if snap.state != "ready":
        empty_state_panel(
            "No income breakdown rows", f"No breakdown rows for FY {fy}.", icon="category"
        )
        return

    with ui.element("div").classes("studio-chart-grid w-full mb-4"):
        _render_chart_card(
            f"FY {fy} Tax Sub-Head Breakdown",
            build_tax_head_breakdown_figure(snap.rows, currency_id),
            "gold.tax_income_breakdown",
        )
        _render_chart_card(
            f"FY {fy} Tax by Source Type",
            build_tax_source_type_figure(snap.rows, currency_id),
            "gold.tax_income_breakdown",
        )

    cols = [
        TableColumn(name="head", label="Income Head", field="head", align="left"),
        TableColumn(name="sub", label="Tax Sub-Head", field="sub", align="left"),
        TableColumn(name="src", label="Source", field="src", align="left"),
        TableColumn(name="amt", label="Event Amount", field="amt", align="right"),
        TableColumn(name="tax", label="Est. Tax", field="tax", align="right"),
        TableColumn(name="comp", label="Complete", field="comp", align="left"),
        TableColumn(name="ev", label="Events", field="ev", align="right"),
        TableColumn(name="chk", label="Check Req", field="chk", align="right"),
    ]
    rows = [
        {
            "head": r.income_head,
            "sub": r.tax_sub_head,
            "src": r.source_type,
            "amt": format_currency(r.event_amount_inr, currency_id),
            "tax": format_currency(r.estimated_tax_inr, currency_id),
            "comp": "Yes"
            if r.estimated_tax_complete
            else f"No ({r.estimated_tax_missing_count} miss)",
            "ev": str(r.event_count),
            "chk": str(r.check_required_count),
        }
        for r in snap.rows
    ]
    bounded_data_table(
        title=f"Tax Income Breakdown — FY {fy} ({len(rows)})",
        columns=cols,
        rows=rows,
        pagination=15,
    )


def _render_reconciliation_section(
    context: AppContext,
    fy: str,
    currency_id: str,
    *,
    cache: QueryCache | None = None,
) -> None:
    profile = context.profile
    if profile is None or not fy:
        return
    snap = TaxAnalytics.reconciliation(
        profile,
        fy,
        run_id=context.provenance.run_id if context.provenance else None,
        provenance=context.provenance,
        catalog_snapshot=context.catalog_snapshot,
        cache=cache,
    )
    if snap.state != "ready":
        empty_state_panel(
            "No reconciliation rows", f"No reconciliation rows for FY {fy}.", icon="fact_check"
        )
        return

    with ui.element("div").classes("studio-chart-grid w-full mb-4"):
        _render_chart_card(
            f"FY {fy} Ledger vs Quant Bridge",
            build_tax_recon_bridge_figure(snap.rows, currency_id),
            "gold.tax_reconciliation",
        )
        _render_chart_card(
            f"FY {fy} Event Readiness by Sub-Head",
            build_tax_completeness_figure(snap.rows),
            "gold.tax_reconciliation",
        )

    cols = [
        TableColumn(name="sub", label="Tax Sub-Head", field="sub", align="left"),
        TableColumn(name="led", label="Ledger Amt", field="led", align="right"),
        TableColumn(name="qnt", label="Quant P&L", field="qnt", align="right"),
        TableColumn(name="diff", label="Source Diff", field="diff", align="right"),
        TableColumn(name="excl", label="Excluded Non-Tax", field="excl", align="right"),
        TableColumn(name="cred", label="Tax Credits", field="cred", align="right"),
        TableColumn(name="est_l", label="Est. Ledger Tax", field="est_l", align="right"),
        TableColumn(name="est_i", label="Est. Inv Tax", field="est_i", align="right"),
    ]
    rows = [
        {
            "sub": r.tax_sub_head,
            "led": format_currency(r.ledger_tax_event_amount, currency_id),
            "qnt": format_currency(r.quant_realized_gain_loss, currency_id),
            "diff": format_currency(r.investment_source_difference_inr, currency_id),
            "excl": format_currency(r.excluded_non_taxable_amount, currency_id),
            "cred": format_currency(r.tax_credit_amount, currency_id),
            "est_l": format_currency(r.estimated_ledger_tax, currency_id),
            "est_i": format_currency(r.estimated_investment_tax, currency_id),
        }
        for r in snap.rows
    ]
    bounded_data_table(
        title=f"Ledger vs Quant Tax Reconciliation — FY {fy} ({len(rows)})",
        columns=cols,
        rows=rows,
        pagination=15,
    )


def _render_events_and_harvest_section(
    context: AppContext,
    fy: str,
    currency_id: str,
    *,
    cache: QueryCache | None = None,
) -> None:
    profile = context.profile
    if profile is None or not fy:
        return

    def _global_val(field: FilterField) -> str | None:
        for spec in context.filters.filters:
            if spec.field == field and spec.value is not None:
                return str(spec.value[0] if isinstance(spec.value, tuple) else spec.value)
        return None

    ev_filter = TaxEventFilter(
        income_head=_global_val(FilterField.INCOME_HEAD),
        tax_status=_global_val(FilterField.TAX_STATUS),
        search=_global_val(FilterField.ISIN),
    )
    ev_snap = TaxAnalytics.tax_events(
        profile,
        fy,
        ev_filter,
        run_id=context.provenance.run_id if context.provenance else None,
        provenance=context.provenance,
        catalog_snapshot=context.catalog_snapshot,
        cache=cache,
    )
    lot_drawer_box = ui.column().classes("w-full")

    def _open_lot(lot_id: str, closing_dt: Any) -> None:
        if not lot_id:
            ui.notify("No Lot ID attached to this row.", type="info")
            return
        detail = InvestmentAnalytics.lot_detail(
            profile,
            lot_id,
            closing_dt,
            catalog_snapshot=context.catalog_snapshot,
        )
        lot_drawer_box.clear()
        with lot_drawer_box:
            open_lot_detail_dialog(detail, lot_id)

    harv = TaxAnalytics.harvesting_snapshot(
        profile,
        context.period_context,
        run_id=context.provenance.run_id if context.provenance else None,
        provenance=context.provenance,
        catalog_snapshot=context.catalog_snapshot,
        cache=cache,
    )

    if ev_snap.state == "ready":
        with ui.element("div").classes("studio-chart-grid w-full mb-4"):
            _render_chart_card(
                f"FY {fy} Monthly Tax Events",
                build_tax_events_timeline_figure(ev_snap.rows, currency_id),
                "silver.fact_tax_events",
            )
            _render_chart_card(
                f"FY {fy} Status & Source Breakdown",
                build_tax_events_status_source_figure(ev_snap.rows, currency_id),
                "silver.fact_tax_events",
            )
        cols = [
            TableColumn(name="id", label="Event ID", field="id", align="left"),
            TableColumn(name="dt", label="Date", field="dt", align="left"),
            TableColumn(name="src", label="Source", field="src", align="left"),
            TableColumn(name="isin", label="ISIN", field="isin", align="left"),
            TableColumn(name="lot_id", label="Lot ID", field="lot_id", align="left"),
            TableColumn(name="head", label="Head", field="head", align="left"),
            TableColumn(name="sub", label="Sub-Head", field="sub", align="left"),
            TableColumn(name="amt", label="Amount", field="amt", align="right"),
            TableColumn(name="rate", label="Rate", field="rate", align="right"),
            TableColumn(name="tax", label="Est. Tax", field="tax", align="right"),
            TableColumn(name="st", label="Status", field="st", align="left"),
            TableColumn(name="reason", label="Status Reason", field="reason", align="left"),
        ]
        rows = [
            {
                "id": r.tax_event_id,
                "dt": r.event_date.isoformat() if r.event_date else "—",
                "src": r.source_type,
                "isin": r.isin or "—",
                "lot_id": r.lot_id or "",
                "head": r.income_head,
                "sub": r.tax_sub_head,
                "amt": format_currency(r.amount_inr, r.currency_id or currency_id),
                "rate": format_percent(r.applied_rate) if r.applied_rate is not None else "—",
                "tax": format_currency(r.estimated_tax, r.currency_id or currency_id)
                if r.estimated_tax is not None
                else "—",
                "st": r.tax_status,
                "reason": r.tax_status_reason or "—",
            }
            for r in ev_snap.rows
        ]
        search_in = (
            ui.input(placeholder="Search Event ID, ISIN, Lot ID, Sub-Head, Source, Status...")
            .props("dense outlined dark clearable")
            .classes("w-96 mb-2")
        )
        tbl_box = ui.column().classes("w-full")

        def _render_ev_tbl(q: str = "") -> None:
            ql = q.strip().lower()
            filtered = (
                [
                    r
                    for r in rows
                    if ql in str(r["id"]).lower()
                    or ql in str(r["isin"]).lower()
                    or ql in str(r["lot_id"]).lower()
                    or ql in str(r["sub"]).lower()
                    or ql in str(r["src"]).lower()
                    or ql in str(r["st"]).lower()
                ]
                if ql
                else rows
            )
            tbl_box.clear()
            with tbl_box:
                bounded_data_table(
                    title=(
                        f"Canonical Tax Events — FY {fy} ({len(filtered)} · "
                        "click investment rows with Lot ID for 88-col detail)"
                    ),
                    columns=cols,
                    rows=filtered,
                    pagination=15,
                    on_row_click=lambda row: _open_lot(str(row.get("lot_id", "")), harv.as_of_date),
                )

        search_in.on_value_change(lambda e: _render_ev_tbl(str(e.value or "")))
        _render_ev_tbl("")

    if harv.state == "ready" and harv.harvest_lots:
        with ui.element("div").classes("studio-chart-grid w-full my-4"):
            _render_chart_card(
                "Harvestable Loss vs Est. Tax Savings (Top Lots)",
                build_tax_harvesting_exposure_figure(harv.harvest_lots, currency_id),
                "silver.fact_investment_lot_analytics",
            )
            _render_chart_card(
                "Harvesting Exposure by Holding Type & LTCG Maturity",
                build_tax_harvesting_holding_figure(
                    harv.harvest_lots, harv.maturing_lots, currency_id
                ),
                "silver.fact_investment_lot_analytics",
            )
        h_cols = [
            TableColumn(name="lot", label="Lot ID", field="lot", align="left"),
            TableColumn(name="isin", label="ISIN", field="isin", align="left"),
            TableColumn(name="inst", label="Instrument", field="inst", align="left"),
            TableColumn(name="buy_dt", label="Buy Date", field="buy_dt", align="left"),
            TableColumn(name="qty", label="Qty", field="qty", align="right"),
            TableColumn(name="cost", label="Cost Basis", field="cost", align="right"),
            TableColumn(name="val", label="Market Value", field="val", align="right"),
            TableColumn(name="pl", label="Unrealized Loss", field="pl", align="right"),
            TableColumn(name="hold", label="Holding", field="hold", align="left"),
            TableColumn(name="d_ltcg", label="Days LTCG", field="d_ltcg", align="right"),
            TableColumn(name="xirr", label="XIRR", field="xirr", align="right"),
            TableColumn(name="tax", label="Tax If Sold", field="tax", align="right"),
            TableColumn(name="rec", label="Recommendation", field="rec", align="left"),
        ]
        h_rows = [
            {
                "lot": r.lot_id,
                "isin": r.isin,
                "inst": r.instrument_name[:28],
                "buy_dt": r.buy_date.isoformat() if r.buy_date else "—",
                "qty": f"{r.quantity:,.2f}",
                "cost": format_currency(r.buy_value, currency_id),
                "val": format_currency(r.close_value, currency_id),
                "pl": format_currency(r.unrealized_pl, currency_id),
                "hold": r.holding_type,
                "d_ltcg": str(r.days_to_ltcg) if r.days_to_ltcg is not None else "—",
                "xirr": format_percent(r.xirr) if r.xirr is not None else "—",
                "tax": format_currency(r.tax_if_sold, currency_id),
                "rec": r.harvest_recommendation,
            }
            for r in harv.harvest_lots
        ]
        bounded_data_table(
            title=f"Indicative Harvestable Loss Lots ({len(h_rows)} · click any row for 88-col lot detail)",
            columns=h_cols,
            rows=h_rows,
            pagination=10,
            on_row_click=lambda row: _open_lot(str(row.get("lot", "")), harv.as_of_date),
        )


def _render_fy_params_card(
    params: TaxFYParameters | None,
    fy: str,
    currency_id: str,
) -> None:
    with ui.card().classes("studio-card w-full p-4 rounded-lg"):
        ui.label(f"REFERENCE TAX PARAMETERS — FY {fy}").classes(
            "text-caption font-semibold tracking-wider mb-3"
        ).style("color: var(--studio-text-muted);")
        if params is None:
            ui.label(
                f"Reference parameters unavailable for FY {fy} in silver.ref_financial_year_parameters."
            ).classes("text-caption").style("color: #FBBF24;")
            return
        with ui.grid(columns=4).classes("w-full gap-3"):
            _param_tile(
                "Equity Listed LTCG",
                format_percent(params.equity_listed_ltcg)
                if params.equity_listed_ltcg is not None
                else "—",
            )
            _param_tile(
                "Equity Listed STCG",
                format_percent(params.equity_listed_stcg)
                if params.equity_listed_stcg is not None
                else "—",
            )
            _param_tile(
                "Equity LTCG Exemption",
                format_currency(float(params.equity_ltcg_exemption), currency_id)
                if params.equity_ltcg_exemption is not None
                else "—",
            )
            _param_tile(
                "Ordinary Income Rate",
                format_percent(params.estimated_ordinary_income_tax_rate)
                if params.estimated_ordinary_income_tax_rate is not None
                else "—",
            )


def _param_tile(label: str, value: str) -> None:
    with ui.column().classes("gap-0 p-2 rounded").style("background: rgba(15, 23, 42, 0.55);"):
        ui.label(label).classes("text-caption").style("color: var(--studio-text-muted);")
        ui.label(value).classes("text-body2 font-semibold").style(
            "color: var(--studio-text-primary);"
        )


def _render_kpi_grid(kpis: tuple[OverviewKPI, ...], currency_id: str) -> None:
    with ui.element("div").classes("studio-kpi-grid w-full mb-4"):
        for kpi in kpis:
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
                breakups=breakups,
                icon="receipt_long",
            )


def _render_chart_card(title: str, figure: dict[str, Any] | None, source: str) -> None:
    if figure is None:
        return
    chart_panel(title=title, subtitle="", figure=figure, source_caption=f"Source: {source}")
