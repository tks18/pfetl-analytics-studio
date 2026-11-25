"""Household Transactions & Classification Explorer tab UI.

Owns
----
- Persistent filter bar (outside `@ui.refreshable` so typing never drops input focus).
- Context charts, server-paginated unified Silver table, CSV export, and detail drawer.

Does not own
------------
- DuckDB queries (`analytics/household_transaction_queries.py`) or `HouseholdAnalytics` service.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any, Literal, cast

from nicegui import run, ui

from pfetl_analytics_studio.analytics import (
    HouseholdAnalytics,
    HouseholdTransactionPage,
    HouseholdTransactionQuery,
    HouseholdTransactionRow,
)
from pfetl_analytics_studio.context import AppContext
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    TableColumn,
    bounded_data_table,
    build_tx_account_breakdown_figure,
    build_tx_daily_activity_figure,
    build_tx_kind_volume_figure,
    chart_panel,
    error_state_panel,
    format_currency,
    kpi_card,
    loading_state_card,
    status_badge,
)


def _open_tx_detail_dialog(
    context: AppContext,
    tx_kind: Literal["income", "expense", "transfer"],
    uid: str,
) -> None:
    """Open modal drawer displaying full Silver classification lineage & linked transfers."""
    if context.profile is None:
        return
    detail = HouseholdAnalytics.household_transaction_detail(
        context.profile,
        tx_kind,
        uid,
        catalog_snapshot=context.catalog_snapshot,
    )
    if detail is None:
        ui.notify("Transaction detail unavailable.", type="warning")
        return

    r = detail.row
    dialog = ui.dialog()
    with dialog, ui.card().classes("studio-card w-[760px] max-w-full p-6 rounded-xl gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.label(f"{r.transaction_kind.title()} Transaction Detail").classes(
                    "text-lg font-semibold"
                )
                ui.label(f"UID: {r.uid}").classes("text-xs font-mono").style(
                    f"color: {StudioColor.MUTED}"
                )
            ui.button(icon="close", on_click=dialog.close).props("flat round dense color=white")

        with ui.row().classes("items-center gap-2 flex-wrap"):
            status_badge(r.transaction_kind.upper(), tone=BadgeTone.INFO)
            for fl in detail.flags:
                status_badge(fl, tone=BadgeTone.SUCCESS if fl == "Core" else BadgeTone.NEUTRAL)

        with ui.grid(columns=2).classes("md:grid-cols-4 w-full gap-3 text-xs"):
            for lbl, val in (
                ("Date / Time", f"{r.tx_date.isoformat()} {r.tx_time}".strip()),
                ("Base Amount", format_currency(r.base_amount, r.currency_id, signed=True)),
                (
                    "Account Amount",
                    format_currency(r.amount_account, r.currency_id, signed=True)
                    if r.amount_account is not None
                    else "—",
                ),
                (
                    "Local Amount",
                    format_currency(r.local_amount, r.currency_id, signed=True)
                    if r.local_amount is not None
                    else "—",
                ),
            ):
                with ui.column().classes("gap-0"):
                    ui.label(lbl).style(f"color: {StudioColor.DIM}")
                    ui.label(val).classes("font-mono font-semibold")

        ui.label("CLASSIFICATION LINEAGE").classes("text-xs font-semibold tracking-wider").style(
            f"color: {StudioColor.DIM}"
        )
        with ui.grid(columns=2).classes("md:grid-cols-3 w-full gap-2 text-xs"):
            for k, v in detail.lineage.items():
                with ui.column().classes("gap-0"):
                    ui.label(k).style(f"color: {StudioColor.DIM}")
                    ui.label(v).classes("font-medium")

        if detail.linked_transfers:
            ui.label(f"LINKED TRANSFER LEGS (TRANSFER_UID: {r.transfer_uid})").classes(
                "text-xs font-semibold tracking-wider"
            ).style(f"color: {StudioColor.DIM}")
            l_cols = [
                TableColumn(name="kind", label="Kind", field="kind", align="left"),
                TableColumn(name="date", label="Date", field="date", align="left"),
                TableColumn(name="acc", label="Account", field="acc", align="left"),
                TableColumn(name="amt", label="Base Amount", field="amt", align="right"),
                TableColumn(name="desc", label="Description", field="desc", align="left"),
            ]
            l_rows = [
                {
                    "kind": lr.transaction_kind.title(),
                    "date": lr.tx_date.isoformat(),
                    "acc": lr.asset_name,
                    "amt": format_currency(lr.base_amount, lr.currency_id, signed=True),
                    "desc": lr.description or lr.paid or "—",
                }
                for lr in detail.linked_transfers
            ]
            bounded_data_table(l_cols, l_rows, row_key="acc")

        with ui.expansion("Raw Silver Columns", icon="code").classes("w-full text-xs"):
            with ui.grid(columns=2).classes("w-full gap-2 text-xs font-mono"):
                for k, v in detail.raw_fields.items():
                    ui.label(f"{k}: {v}").classes("truncate")
    dialog.open()


def _render_transactions_body(context: AppContext, initial_page: HouseholdTransactionPage) -> None:
    """Render persistent filter bar, context charts, server-paged ledger, and detail drawer."""
    assert context.profile is not None
    p_ctx = context.period_context
    cid = initial_page.currency_id
    state: dict[str, Any] = {
        "kind": "all",
        "flag": "all",
        "category_name": "",
        "subcategory_name": "",
        "asset_id": "",
        "currency_id": "",
        "transfer_type": "",
        "min_amount": "",
        "max_amount": "",
        "search": "",
        "has_transfer_only": False,
        "offset": 0,
        "limit": 50,
        "current_page": initial_page,
    }
    controls: dict[str, Any] = {}

    def _build_query() -> HouseholdTransactionQuery:
        def _opt_float(raw: str) -> float | None:
            txt = raw.strip()
            if not txt:
                return None
            try:
                return float(txt)
            except ValueError:
                return None

        return HouseholdTransactionQuery(
            transaction_kind=cast(Literal["all", "income", "expense", "transfer"], state["kind"]),
            start_date=p_ctx.start_date if p_ctx else None,
            end_date=p_ctx.end_date if p_ctx else None,
            asset_id=state["asset_id"] or None,
            category_name=state["category_name"] or None,
            subcategory_name=state["subcategory_name"] or None,
            currency_id=state["currency_id"] or None,
            transfer_type=state["transfer_type"] or None,
            classification_flag=state["flag"],
            min_amount=_opt_float(str(state["min_amount"])),
            max_amount=_opt_float(str(state["max_amount"])),
            text_search=state["search"] or None,
            has_transfer_only=bool(state["has_transfer_only"]),
        )

    @ui.refreshable
    def _ledger_body() -> None:
        if (
            state["offset"] == 0
            and state["kind"] == "all"
            and not any(
                (
                    state["flag"] != "all",
                    state["category_name"],
                    state["subcategory_name"],
                    state["asset_id"],
                    state["currency_id"],
                    state["transfer_type"],
                    state["min_amount"],
                    state["max_amount"],
                    state["search"],
                    state["has_transfer_only"],
                )
            )
        ):
            page_res = initial_page
        else:
            page_res = HouseholdAnalytics.household_transactions(
                context.profile,  # type: ignore[arg-type]
                _build_query(),
                limit=int(state["limit"]),
                offset=int(state["offset"]),
                catalog_snapshot=context.catalog_snapshot,
            )
        state["current_page"] = page_res
        s = page_res.summary

        with ui.element("div").classes("studio-kpi-grid"):
            kpi_card(
                "Matching Transactions",
                f"{s.total_count:,}",
                subtitle="Unified Silver ledger across all kinds",
                icon="list_alt",
                breakups=[
                    ("Income", f"{s.income_count:,}"),
                    ("Expense", f"{s.expense_count:,}"),
                    ("Transfers", f"{s.transfer_count:,}"),
                ],
            )
            kpi_card(
                "Income vs Expense",
                format_currency(s.income_total - s.expense_total, cid, compact=True, signed=True),
                subtitle="Net income minus expense in matching slice",
                icon="payments",
                breakups=[
                    ("Income", format_currency(s.income_total, cid, compact=True)),
                    ("Expense", format_currency(s.expense_total, cid, compact=True)),
                ],
            )
            kpi_card(
                "Transfer Pool Volume",
                format_currency(s.transfer_volume, cid, compact=True),
                subtitle="Gross transfer legs (excluded from P&L)",
                icon="swap_horiz",
                breakups=[
                    ("Legs", f"{s.transfer_count:,}"),
                    ("Accounts", f"{len(s.account_breakdown):,}"),
                ],
            )

        with ui.element("div").classes("studio-chart-grid"):
            chart_panel(
                title="Volume by Kind",
                subtitle="Matching Income, Expense & Transfer totals",
                figure=build_tx_kind_volume_figure(s, cid),
                source_caption="Source: silver.fact_*_transactions",
            )
            chart_panel(
                title="Top Accounts by Volume",
                subtitle="Top 10 accounts in matching transaction slice",
                figure=build_tx_account_breakdown_figure(s, cid),
                source_caption="Source: silver.dim_asset_subcategory",
            )

        chart_panel(
            title="Daily Activity Timeline",
            subtitle="Daily Income, Expense & Transfer cashflows",
            figure=build_tx_daily_activity_figure(s, cid),
            source_caption="Source: silver.fact_*_transactions",
        )

        with ui.card().classes("studio-card w-full p-5 rounded-xl gap-3"):
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                start_idx = state["offset"] + 1 if page_res.rows else 0
                end_idx = state["offset"] + len(page_res.rows)
                ui.label(
                    f"Showing {start_idx}–{end_idx} of {s.total_count:,} transactions "
                    "(click a row to inspect classification lineage)"
                ).classes("text-xs font-mono").style(f"color: {StudioColor.MUTED}")
                with ui.row().classes("items-center gap-2"):

                    def _prev_page() -> None:
                        state["offset"] = max(0, int(state["offset"]) - int(state["limit"]))
                        _ledger_body.refresh()

                    def _next_page() -> None:
                        if int(state["offset"]) + int(state["limit"]) < s.total_count:
                            state["offset"] = int(state["offset"]) + int(state["limit"])
                            _ledger_body.refresh()

                    ui.button("Prev", icon="chevron_left", on_click=_prev_page).props(
                        "outline dense size=sm no-caps"
                    ).set_enabled(state["offset"] > 0)
                    ui.button("Next", icon="chevron_right", on_click=_next_page).props(
                        "outline dense size=sm no-caps"
                    ).set_enabled(state["offset"] + len(page_res.rows) < s.total_count)

            if not page_res.rows:
                ui.label("No Silver transactions match the active filter criteria.").classes(
                    "text-xs italic py-3"
                ).style(f"color: {StudioColor.DIM}")
                return

            cols = [
                TableColumn(name="date", label="Date", field="date", align="left"),
                TableColumn(name="kind", label="Kind", field="kind", align="left"),
                TableColumn(name="account", label="Account", field="account", align="left"),
                TableColumn(name="category", label="Category", field="category", align="left"),
                TableColumn(name="subcat", label="Subcategory", field="subcat", align="left"),
                TableColumn(name="classif", label="Classification", field="classif", align="left"),
                TableColumn(name="base_amt", label="Base Amt", field="base_amt", align="right"),
                TableColumn(name="acc_amt", label="Account Amt", field="acc_amt", align="right"),
                TableColumn(name="loc_amt", label="Local Amt", field="loc_amt", align="right"),
                TableColumn(name="desc", label="Paid / Description", field="desc", align="left"),
            ]
            row_lookup: dict[str, HouseholdTransactionRow] = {}
            rows: list[dict[str, Any]] = []
            for r in page_res.rows:
                key = f"{r.transaction_kind}:{r.uid}"
                row_lookup[key] = r
                rows.append(
                    {
                        "row_uid": key,
                        "date": r.tx_date.isoformat(),
                        "kind": r.transaction_kind.title(),
                        "account": r.asset_name,
                        "category": r.category_name or "—",
                        "subcat": r.subcategory_name or "—",
                        "classif": r.classification_summary,
                        "base_amt": format_currency(r.base_amount, r.currency_id, signed=True),
                        "acc_amt": format_currency(r.amount_account, r.currency_id, signed=True)
                        if r.amount_account is not None
                        else "—",
                        "loc_amt": format_currency(r.local_amount, r.currency_id, signed=True)
                        if r.local_amount is not None
                        else "—",
                        "desc": r.description or r.paid or "—",
                    }
                )

            table_card = bounded_data_table(cols, rows, row_key="row_uid", pagination=50)
            for child in table_card.default_slot.children:
                if isinstance(child, ui.table):

                    def _on_row_click(evt: Any) -> None:
                        args = getattr(evt, "args", None)
                        if isinstance(args, list):
                            args_seq = cast(Sequence[object], args)
                            if len(args_seq) >= 2 and isinstance(args_seq[1], dict):
                                row_dict = cast(dict[str, Any], args_seq[1])
                                rk = str(row_dict.get("row_uid") or "")
                                picked = row_lookup.get(rk)
                                if picked is not None:
                                    _open_tx_detail_dialog(
                                        context, picked.transaction_kind, picked.uid
                                    )

                    child.on("rowClick", _on_row_click)

    with ui.card().classes("studio-card w-full p-4 rounded-xl gap-3"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            ui.label("HOUSEHOLD TRANSACTION & CLASSIFICATION EXPLORER").classes(
                "text-xs font-semibold tracking-wider"
            ).style(f"color: {StudioColor.DIM}")
            with ui.row().classes("items-center gap-2"):

                def _export_csv() -> None:
                    pg = cast(HouseholdTransactionPage, state["current_page"])
                    csv_text = HouseholdAnalytics.export_transactions_csv(pg)
                    ui.download(csv_text.encode("utf-8"), "household_transactions.csv")

                def _reset_filters() -> None:
                    state.update(
                        {
                            "kind": "all",
                            "flag": "all",
                            "category_name": "",
                            "subcategory_name": "",
                            "asset_id": "",
                            "currency_id": "",
                            "transfer_type": "",
                            "min_amount": "",
                            "max_amount": "",
                            "search": "",
                            "has_transfer_only": False,
                            "offset": 0,
                        }
                    )
                    for k, default in (
                        ("kind", "all"),
                        ("flag", "all"),
                        ("category_name", ""),
                        ("subcategory_name", ""),
                        ("asset_id", ""),
                        ("currency_id", ""),
                        ("transfer_type", ""),
                        ("min_amount", ""),
                        ("max_amount", ""),
                        ("search", ""),
                        ("has_transfer_only", False),
                    ):
                        ctrl = controls.get(k)
                        if ctrl is not None:
                            ctrl.set_value(default)
                    _ledger_body.refresh()

                ui.button("Reset", icon="restart_alt", on_click=_reset_filters).props(
                    "outline dense size=sm no-caps"
                )
                ui.button("Export CSV", icon="download", on_click=_export_csv).props(
                    "outline dense size=sm color=primary no-caps"
                )

        def _make_updater(field_key: str) -> Callable[[Any], None]:
            def _on_chg(evt: Any) -> None:
                state[field_key] = str(evt.value or "")
                state["offset"] = 0
                _ledger_body.refresh()

            return _on_chg

        def _on_linked_toggle(evt: Any) -> None:
            state["has_transfer_only"] = bool(evt.value)
            state["offset"] = 0
            _ledger_body.refresh()

        asset_opts: dict[str, str] = {"": "All Accounts"}
        for aid, aname in initial_page.summary.available_assets:
            asset_opts[aid] = aname
        curr_opts: dict[str, str] = {"": "All Currencies"}
        for c_id in initial_page.summary.available_currencies:
            curr_opts[c_id] = c_id
        cat_opts: dict[str, str] = {"": "All Categories"}
        for cname in initial_page.summary.available_categories:
            cat_opts[cname] = cname
        sub_opts: dict[str, str] = {"": "All Subcategories"}
        for sname in initial_page.summary.available_subcategories:
            sub_opts[sname] = sname

        sel_props = "dense filled dark hide-bottom-space options-dense"
        inp_props = "debounce=250 dense filled dark hide-bottom-space"

        with ui.row().classes("w-full items-center flex-wrap gap-2.5"):
            controls["kind"] = (
                ui.select(
                    options={
                        "all": "All Kinds",
                        "income": "Income",
                        "expense": "Expense",
                        "transfer": "Transfer",
                    },
                    value="all",
                    label="Kind",
                    on_change=_make_updater("kind"),
                )
                .props(sel_props)
                .classes("studio-field w-36 text-xs")
            )
            controls["flag"] = (
                ui.select(
                    options={
                        "all": "All Classifications",
                        "core": "Core Expense",
                        "non_core": "Non-Core Expense",
                        "active": "Active Income",
                        "passive": "Passive Income",
                        "dividend": "Dividend Income",
                        "interest": "Interest Income",
                        "non_cash": "Non-Cash Income",
                    },
                    value="all",
                    label="Classification",
                    on_change=_make_updater("flag"),
                )
                .props(sel_props)
                .classes("studio-field w-44 text-xs")
            )
            controls["category_name"] = (
                ui.select(
                    options=cat_opts,
                    value="",
                    label="Category",
                    on_change=_make_updater("category_name"),
                )
                .props(sel_props)
                .classes("studio-field w-44 text-xs")
            )
            controls["subcategory_name"] = (
                ui.select(
                    options=sub_opts,
                    value="",
                    label="Subcategory",
                    on_change=_make_updater("subcategory_name"),
                )
                .props(sel_props)
                .classes("studio-field w-48 text-xs")
            )
            controls["asset_id"] = (
                ui.select(
                    options=asset_opts,
                    value="",
                    label="Account",
                    on_change=_make_updater("asset_id"),
                )
                .props(sel_props)
                .classes("studio-field w-48 text-xs")
            )
            controls["currency_id"] = (
                ui.select(
                    options=curr_opts,
                    value="",
                    label="Currency",
                    on_change=_make_updater("currency_id"),
                )
                .props(sel_props)
                .classes("studio-field w-32 text-xs")
            )
            controls["transfer_type"] = (
                ui.select(
                    options={"": "All Flows", "in": "In", "out": "Out"},
                    value="",
                    label="Transfer Flow",
                    on_change=_make_updater("transfer_type"),
                )
                .props(sel_props)
                .classes("studio-field w-32 text-xs")
            )
            controls["min_amount"] = (
                ui.input(
                    label="Min Amt",
                    value="",
                    placeholder="0",
                    on_change=_make_updater("min_amount"),
                )
                .props(inp_props)
                .classes("studio-field w-28 text-xs font-mono")
            )
            controls["max_amount"] = (
                ui.input(
                    label="Max Amt",
                    value="",
                    placeholder="∞",
                    on_change=_make_updater("max_amount"),
                )
                .props(inp_props)
                .classes("studio-field w-28 text-xs font-mono")
            )
            controls["search"] = (
                ui.input(
                    label="Search Paid / Description",
                    value="",
                    placeholder="Search description or payee...",
                    on_change=_make_updater("search"),
                )
                .props(f"{inp_props} clearable")
                .classes("studio-field w-60 text-xs")
            )
            controls["has_transfer_only"] = (
                ui.switch(
                    "Linked Transfers Only",
                    value=False,
                    on_change=_on_linked_toggle,
                )
                .props("dense color=primary")
                .classes("text-xs")
            )

    _ledger_body()


def render_transactions_tab(context: AppContext) -> None:
    """Render unified Household Transactions tab without blocking the event loop."""

    def _fetch_sync() -> HouseholdTransactionPage:
        assert context.profile is not None
        p_ctx = context.period_context
        q = HouseholdTransactionQuery(
            start_date=p_ctx.start_date if p_ctx else None,
            end_date=p_ctx.end_date if p_ctx else None,
        )
        return HouseholdAnalytics.household_transactions(
            context.profile,
            q,
            limit=50,
            offset=0,
            catalog_snapshot=context.catalog_snapshot,
        )

    try:
        asyncio.get_running_loop()
        has_loop = True
    except RuntimeError:
        has_loop = False

    if not has_loop:
        _render_transactions_body(context, _fetch_sync())
        return

    body_slot = ui.column().classes("w-full gap-4")
    with body_slot:
        loading_state_card("Loading Household Transaction Explorer...")

    async def _load_async() -> None:
        try:
            page_res = await run.io_bound(_fetch_sync)
        except Exception as exc:  # noqa: BLE001
            body_slot.clear()
            with body_slot:
                error_state_panel(
                    title="Failed to Load Household Transactions",
                    message="An error occurred while querying the read-only warehouse.",
                    details=[str(exc)],
                )
            return
        body_slot.clear()
        with body_slot:
            _render_transactions_body(context, page_res)

    ui.timer(0.01, _load_async, once=True)
