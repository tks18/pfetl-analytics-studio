"""Household page module — Income, Expense, Budget & Cashflow, and Transaction Evidence."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from pfetl_analytics_studio.analytics import (
    HouseholdAnalytics,
    HouseholdIncomeSnapshot,
    IncomeCategoryRow,
    OverviewKPI,
)
from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.semantic import MetricFormat
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    TableColumn,
    bounded_data_table,
    build_income_account_mix_figure,
    build_income_category_bars_figure,
    build_income_composition_figure,
    build_income_trend_figure,
    build_income_ytd_figure,
    build_passive_income_engine_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    format_currency,
    format_metric_value,
    format_percent,
    kpi_card,
    status_badge,
)

_INCOME_KPI_ICONS: dict[str, str] = {
    "income_total": "payments",
    "income_gross_deductions": "account_balance_wallet",
    "income_active": "work_outline",
    "income_passive": "autorenew",
    "income_budget": "track_changes",
    "income_taxability": "receipt_long",
    "income_active_dependency": "speed",
    "income_concentration": "donut_large",
    "income_cash_mix": "account_balance",
}


def _tone_from_name(name: str) -> BadgeTone:
    return {
        "success": BadgeTone.SUCCESS,
        "warning": BadgeTone.WARNING,
        "error": BadgeTone.ERROR,
        "info": BadgeTone.INFO,
        "recovery": BadgeTone.RECOVERY,
    }.get(name, BadgeTone.NEUTRAL)


def _render_income_kpi_grid(
    kpis: tuple[OverviewKPI, ...],
    currency_id: str,
    *,
    compact: bool = False,
) -> None:
    """Render 3-column Income KPI grid with formatted deltas and breakup pills."""
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
                delta_str = (
                    format_currency(
                        kpi.delta_value, currency_id, compact=True, signed=True, decimals=1
                    )
                    if kpi.delta_format == MetricFormat.CURRENCY
                    else format_percent(kpi.delta_value, decimals=1, signed=True)
                )
                if kpi.delta_label:
                    delta_str = f"{delta_str} {kpi.delta_label}"
                delta_tone = (
                    BadgeTone.SUCCESS
                    if (kpi.delta_value >= 0) == kpi.delta_positive_is_good
                    else BadgeTone.WARNING
                )
            breakups = tuple(
                (
                    b.label,
                    format_metric_value(
                        b.value,
                        b.format,
                        currency_id=currency_id,
                        compact=True,
                        unit_suffix=b.unit_suffix,
                    ),
                )
                for b in kpi.breakups
                if b.value is not None
            )
            exact_note = (
                f"Exact: {format_currency(float(kpi.value), currency_id, compact=False)}"
                if kpi.available
                and kpi.format == MetricFormat.CURRENCY
                and isinstance(kpi.value, (int, float))
                else f"Value: {val_str}"
            )
            kpi_card(
                title=kpi.label,
                value=val_str,
                subtitle=kpi.subtitle,
                delta=delta_str,
                delta_tone=delta_tone,
                tooltip=f"{kpi.tooltip}\n{exact_note}",
                drill_route=kpi.drill_route if kpi.drill_route != "/household" else None,
                icon=_INCOME_KPI_ICONS.get(kpi.kpi_id, "insights"),
                available=kpi.available,
                compact=compact,
                status_pill=kpi.status_badge,
                status_pill_tone=_tone_from_name(kpi.status_tone),
                breakups=breakups,
            )


def _classification_label(row: IncomeCategoryRow) -> str:
    tags: list[str] = []
    if row.is_active:
        tags.append("Active")
    if row.is_passive:
        tags.append("Passive")
    if row.is_dividend:
        tags.append("Dividend")
    if row.is_interest:
        tags.append("Interest")
    if row.is_non_cash:
        tags.append("Non-Cash")
    if row.amount < 0:
        tags.append("Deduction")
    return ", ".join(tags) if tags else "Other"


def _render_income_drill_section(
    context: AppContext,
    snapshot: HouseholdIncomeSnapshot,
) -> None:
    """Render Category Rollup Table + interactive Silver Income Transaction Drill."""
    cid = snapshot.currency_id
    state: dict[str, Any] = {
        "flag": None,
        "group": None,
        "category": None,
        "page": 1,
    }

    ui.label("Income Categories & Interactive Transaction Drill").classes(
        "text-xs font-semibold uppercase tracking-wider mt-2"
    ).style(f"color: {StudioColor.DIM}")

    # Category summary table
    cat_cols = [
        TableColumn(name="category", label="Category", field="category", align="left"),
        TableColumn(name="group", label="Group", field="group", align="left"),
        TableColumn(name="flags", label="Classification", field="flags", align="left"),
        TableColumn(name="taxability", label="Taxability", field="taxability", align="left"),
        TableColumn(name="amount", label="Period Total", field="amount", align="right"),
        TableColumn(name="t3m", label="3M Avg", field="t3m", align="right"),
        TableColumn(name="ytd", label="YTD Cum.", field="ytd", align="right"),
        TableColumn(name="share", label="Share", field="share", align="right"),
    ]
    cat_table_rows = [
        {
            "category": r.category_name,
            "group": r.category_group,
            "flags": _classification_label(r),
            "taxability": r.taxability.replace("_", " ").title(),
            "amount": format_currency(r.amount, cid, compact=False, signed=r.amount < 0),
            "t3m": format_currency(r.trailing_3m_avg, cid, compact=True)
            if r.trailing_3m_avg is not None
            else "—",
            "ytd": format_currency(r.cumulative_ytd, cid, compact=True)
            if r.cumulative_ytd is not None
            else "—",
            "share": format_percent(r.share_pct, decimals=1) if r.share_pct is not None else "—",
        }
        for r in snapshot.category_rows
    ]
    bounded_data_table(
        cat_cols,
        cat_table_rows,
        row_key="category",
        max_rows=50,
        title="Gold Income Category Rollup (gold.cashflow_income_breakdown)",
    )

    # Interactive Drill Container
    groups = sorted({r.category_group for r in snapshot.category_rows})
    categories = [r.category_name for r in snapshot.category_rows]

    with ui.card().classes("studio-card w-full p-4 rounded-2xl gap-3"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("receipt_long", size="18px").style(f"color: {StudioColor.ACC_HOV}")
                ui.label("Silver Income Transaction Evidence").classes(
                    "text-sm font-semibold"
                ).style(f"color: {StudioColor.TEXT}")
            status_badge("silver.fact_income_transactions", tone=BadgeTone.INFO)

        drill_body = ui.column().classes("w-full gap-3")

        def _refresh_drill() -> None:
            drill_body.clear()
            assert context.profile is not None
            tx_page = HouseholdAnalytics.income_transactions(
                context.profile,
                context.period_context,
                filters=context.filters,
                drill_group=state["group"],
                drill_category=state["category"],
                flag_filter=state["flag"],
                page=state["page"],
                page_size=25,
            )
            with drill_body:
                with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        flag_opts: list[tuple[str | None, str]] = [
                            (None, "All"),
                            ("active", "Active"),
                            ("passive", "Passive"),
                            ("dividend", "Dividend"),
                            ("interest", "Interest"),
                            ("non_cash", "Non-Cash"),
                            ("deductions", "Deductions"),
                        ]
                        for f_key, f_lbl in flag_opts:
                            is_on = state["flag"] == f_key

                            def _pick_flag(val: str | None = f_key) -> None:
                                state["flag"] = val
                                state["page"] = 1
                                _refresh_drill()

                            ui.button(f_lbl, on_click=_pick_flag).props(
                                f"dense no-caps size=sm {'unelevated color=primary' if is_on else 'outline color=grey-5'}"
                            )

                    with ui.row().classes("items-center gap-2 flex-wrap"):

                        def _on_grp(e: Any) -> None:
                            state["group"] = e.value or None
                            state["page"] = 1
                            _refresh_drill()

                        def _on_cat(e: Any) -> None:
                            state["category"] = e.value or None
                            state["page"] = 1
                            _refresh_drill()

                        ui.select(
                            options={"": "All Groups", **{g: g for g in groups}},
                            value=state["group"] or "",
                            label="Group",
                            on_change=_on_grp,
                        ).props("dense outlined dark options-dark").classes("min-w-[160px]")
                        ui.select(
                            options={"": "All Categories", **{c: c for c in categories}},
                            value=state["category"] or "",
                            label="Category",
                            on_change=_on_cat,
                        ).props("dense outlined dark options-dark").classes("min-w-[180px]")

                with ui.row().classes("w-full items-center justify-between flex-wrap gap-2 px-1"):
                    ui.label(
                        f"Matching Rows: {tx_page.total_count:,} | "
                        f"Drill Sum (Base): {format_currency(tx_page.total_base_amount, cid, compact=False)}"
                    ).classes("text-xs font-semibold tabular-nums").style(
                        f"color: {StudioColor.TEXT}"
                    )
                    total_pages = max(
                        1, (tx_page.total_count + tx_page.page_size - 1) // tx_page.page_size
                    )
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"Page {tx_page.page} of {total_pages}").classes("text-xs").style(
                            f"color: {StudioColor.MUTED}"
                        )

                        def _prev() -> None:
                            if state["page"] > 1:
                                state["page"] -= 1
                                _refresh_drill()

                        def _next() -> None:
                            if state["page"] < total_pages:
                                state["page"] += 1
                                _refresh_drill()

                        ui.button(icon="chevron_left", on_click=_prev).props(
                            "flat dense size=sm"
                        ).set_enabled(tx_page.page > 1)
                        ui.button(icon="chevron_right", on_click=_next).props(
                            "flat dense size=sm"
                        ).set_enabled(tx_page.page < total_pages)

                tx_cols = [
                    TableColumn(name="date", label="Date", field="date", align="left"),
                    TableColumn(name="category", label="Category", field="category", align="left"),
                    TableColumn(name="group", label="Group", field="group", align="left"),
                    TableColumn(name="account", label="Account", field="account", align="left"),
                    TableColumn(
                        name="description", label="Description", field="description", align="left"
                    ),
                    TableColumn(name="currency", label="CCY", field="currency", align="left"),
                    TableColumn(name="local", label="Local Amt", field="local", align="right"),
                    TableColumn(name="base", label="Base Amt", field="base", align="right"),
                ]
                tx_rows = [
                    {
                        "uid": r.uid,
                        "date": r.tx_date.isoformat(),
                        "category": r.category_name,
                        "group": r.category_group,
                        "account": r.account_name,
                        "description": r.description or "—",
                        "currency": r.currency_id,
                        "local": (f"{r.local_amount:,.2f}" if r.local_amount is not None else "—"),
                        "base": format_currency(
                            r.base_amount, cid, compact=False, signed=r.base_amount < 0
                        ),
                    }
                    for r in tx_page.rows
                ]
                bounded_data_table(tx_cols, tx_rows, row_key="uid", max_rows=25)

        _refresh_drill()


def _render_income_tab(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
) -> None:
    """Render the complete Household Income Analytics tab."""
    assert context.profile is not None
    snapshot = HouseholdAnalytics.income_summary(
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
                else "Household Income Data Unavailable"
            ),
            message=snapshot.diagnostics[0]
            if snapshot.diagnostics
            else "No populated Income Gold rows match the active period.",
            icon="hourglass_empty",
            action_label="Inspect Operations & Runs",
            action_target="/operations",
        )
        return

    ui.label("Income Composition, Gross vs Deductions & Taxability").classes(
        "text-xs font-semibold uppercase tracking-wider"
    ).style(f"color: {StudioColor.DIM}")
    _render_income_kpi_grid(snapshot.primary_kpis, snapshot.currency_id, compact=False)

    ui.label("Resilience, Concentration & Cash Realization").classes(
        "text-xs font-semibold uppercase tracking-wider mt-2"
    ).style(f"color: {StudioColor.DIM}")
    _render_income_kpi_grid(snapshot.secondary_kpis, snapshot.currency_id, compact=True)

    ui.label("Income Trends, Composition & Receiving Accounts").classes(
        "text-xs font-semibold uppercase tracking-wider mt-2"
    ).style(f"color: {StudioColor.DIM}")
    with ui.element("div").classes("studio-chart-grid"):
        chart_panel(
            "Monthly Income & Trailing 3M Trend",
            f"Cash vs Non-Cash Income with 3M Avg & Budget Target ({snapshot.chart_window_label})",
            build_income_trend_figure(snapshot.monthly_series, currency_id=snapshot.currency_id),
            source_caption="Source: gold.cashflow_efficiency_analytics + forecast_budget_variance",
        )
        chart_panel(
            "Active vs Passive vs Non-Cash Flow",
            f"Income classification mix & Active Dependency % ({snapshot.chart_window_label})",
            build_income_composition_figure(
                snapshot.monthly_series, currency_id=snapshot.currency_id
            ),
            source_caption="Source: gold.cashflow_efficiency_analytics",
        )
        chart_panel(
            "Category Breakdown & Salary Deductions",
            f"Top positive inflows and negative salary deductions ({snapshot.period_label})",
            build_income_category_bars_figure(
                snapshot.category_rows, currency_id=snapshot.currency_id
            ),
            source_caption="Source: gold.cashflow_income_breakdown + silver.dim_income_subcategory",
        )
        chart_panel(
            "Cumulative YTD Income Trajectory",
            f"Fiscal year-to-date cumulative income ({snapshot.chart_window_label})",
            build_income_ytd_figure(snapshot.monthly_series, currency_id=snapshot.currency_id),
            source_caption="Source: gold.cashflow_income_breakdown (Cumulative_YTD_Income)",
        )
        chart_panel(
            "Passive Income Engine",
            f"Monthly Dividends vs Interest & Core Expense Coverage % ({snapshot.chart_window_label})",
            build_passive_income_engine_figure(
                snapshot.monthly_series, currency_id=snapshot.currency_id
            ),
            source_caption="Source: gold.cashflow_efficiency_analytics",
        )
        chart_panel(
            "Receiving Account Mix",
            f"Silver income inflows by receiving bank/wallet/EPF account ({snapshot.period_label})",
            build_income_account_mix_figure(snapshot.account_mix, currency_id=snapshot.currency_id),
            source_caption="Source: silver.fact_income_transactions + silver.dim_asset_subcategory",
        )

    _render_income_drill_section(context, snapshot)


def render_household_page(context: AppContext, *, cache: QueryCache | None = None) -> None:
    """Render the Household workspace with Income Analytics & domain sub-tabs."""
    route = ROUTES_BY_ID["household"]

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
                title="Select a PFETL Profile to Explore Household Analytics",
                message="Open a PFETL Settings TOML and FinancialRules TOML to inspect Income, Expenses, and Budget.",
                icon="folder_open",
            )
            return

        if context.is_warehouse_locked:
            empty_state_panel(
                title="Household Analytics Paused While PFETL Lock Is Active",
                message="PFETL Core holds `pipeline.lock` on the DuckDB warehouse.",
                icon="lock",
                action_label="Inspect Operations & Runs",
                action_target="/operations",
            )
            return

        if context.is_incompatible and context.compatibility:
            error_state_panel(
                title="Household Blocked by Compatibility Check",
                message="Required upstream PFETL surfaces did not match expected catalog contracts.",
                details=list(context.compatibility.diagnostics[:6]),
            )
            return

        with ui.tabs().classes("w-full").props("dense align=left no-caps") as tabs:
            tab_income = ui.tab("income", label="Income", icon="payments")
            tab_expense = ui.tab("expense", label="Expense", icon="receipt")
            tab_budget = ui.tab("budget", label="Budget & Cashflow", icon="track_changes")
            tab_tx = ui.tab("transactions", label="Transactions", icon="list_alt")

        with ui.tab_panels(tabs, value=tab_income).classes("w-full bg-transparent"):
            with ui.tab_panel(tab_income).classes("p-0 pt-2 gap-4"):
                _render_income_tab(context, cache=cache)
            with ui.tab_panel(tab_expense).classes("p-0 pt-2"):
                empty_state_panel(
                    title="Expense & Lifestyle Analytics",
                    message="Core vs Non-Core spending, Fixed vs Variable, and expense creep telemetry.",
                    icon="receipt",
                )
            with ui.tab_panel(tab_budget).classes("p-0 pt-2"):
                empty_state_panel(
                    title="Budget Variance & Cashflow Activity",
                    message="FinancialRules income allocation targets, overspend alerts, and operating cashflow.",
                    icon="track_changes",
                )
            with ui.tab_panel(tab_tx).classes("p-0 pt-2"):
                empty_state_panel(
                    title="Unified Household Transaction Explorer",
                    message="Income transaction evidence is available directly inside the Income tab above.",
                    icon="list_alt",
                )
