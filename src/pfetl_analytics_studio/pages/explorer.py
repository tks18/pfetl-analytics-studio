"""Explorer page module — 82-surface Data Catalog & Guided Whitelisted Analytics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from pfetl_analytics_studio.analytics import (
    CatalogBrowseResult,
    CatalogFilter,
    ChartRecommendation,
    ExplorerAnalytics,
    GuidedQuery,
    GuidedQueryResult,
    GuidedSortSpec,
    SurfaceColumnFilter,
    TablePreviewResult,
)
from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import (
    CatalogSnapshot,
    PFETLDataAccessError,
    QueryCache,
    SurfaceLayer,
    SurfaceUsageCategory,
)
from pfetl_analytics_studio.pages import render_domain_workspace
from pfetl_analytics_studio.pages.explorer_views import (
    open_save_view_dialog,
    render_saved_views_tab,
    render_sql_tab,
)
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    DatasetSpec,
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
    MetricFormat,
    PeriodPreset,
    PeriodRequest,
)
from pfetl_analytics_studio.state import SavedViewKind
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    TableColumn,
    bounded_data_table,
    build_catalog_layer_usage_figure,
    build_catalog_top_tables_figure,
    build_guided_explorer_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    format_metric_value,
    kpi_card,
    status_badge,
)

_EXPLORER_CACHE = QueryCache(max_entries=64)


@dataclass(frozen=True)
class _GuidedRecipe:
    """One-click analytical starter template for Guided Analytics."""

    id: str
    label: str
    icon: str
    dataset_id: str
    dimensions: tuple[str, ...]
    metrics: tuple[str, ...]
    period_preset: str
    sort_field: str
    sort_desc: bool = False


_GUIDED_RECIPES: tuple[_GuidedRecipe, ...] = (
    _GuidedRecipe(
        id="net_worth",
        label="Net Worth & Cashflow Trend",
        icon="trending_up",
        dataset_id="core_monthly_fact",
        dimensions=("MONTH_END_DATE",),
        metrics=("net_worth_market", "total_income", "total_expense"),
        period_preset="since_inception",
        sort_field="MONTH_END_DATE",
    ),
    _GuidedRecipe(
        id="inv_class",
        label="Portfolio by Asset Class",
        icon="pie_chart",
        dataset_id="investment_by_class",
        dimensions=("Closing_Date", "INSTRUMENT_CLASS"),
        metrics=("inv_class_current_value",),
        period_preset="since_inception",
        sort_field="Closing_Date",
    ),
    _GuidedRecipe(
        id="exp_cat",
        label="Expenses by Category",
        icon="shopping_bag",
        dataset_id="cashflow_expense_breakdown",
        dimensions=("CATEGORY_NAME",),
        metrics=("expense_breakdown_amount", "expense_cash_spend"),
        period_preset="rolling_12_months",
        sort_field="expense_breakdown_amount",
        sort_desc=True,
    ),
    _GuidedRecipe(
        id="inc_stream",
        label="Income Streams Trend",
        icon="payments",
        dataset_id="cashflow_income_breakdown",
        dimensions=("YEAR_MONTH", "CATEGORY_NAME"),
        metrics=("income_breakdown_amount",),
        period_preset="rolling_12_months",
        sort_field="YEAR_MONTH",
    ),
    _GuidedRecipe(
        id="tax_fy",
        label="Tax Summary by FY",
        icon="receipt_long",
        dataset_id="tax_financial_year_summary",
        dimensions=("FY",),
        metrics=("tax_ledger_income", "tax_investment_gain_loss", "tax_est_investment_tax"),
        period_preset="all",
        sort_field="FY",
    ),
    _GuidedRecipe(
        id="asset_liq",
        label="Asset Liquidity & Cashflow",
        icon="account_balance",
        dataset_id="wealth_asset_breakdown",
        dimensions=("MONTH_END_DATE",),
        metrics=("asset_closing_balance_market", "asset_net_cashflow_month"),
        period_preset="rolling_12_months",
        sort_field="MONTH_END_DATE",
    ),
)


def _format_cell_value(val: Any, fmt: MetricFormat | None = None) -> str:
    if val is None:
        return "—"
    if fmt is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
        return format_metric_value(float(val), fmt)
    if isinstance(val, float):
        return f"{val:,.2f}"
    return str(val)


def _render_surface_filter_bar(
    preview: TablePreviewResult,
    state: dict[str, Any],
    on_add_filter: Callable[[], None],
    on_clear: Callable[[], None],
    on_refresh: Callable[[], Any],
) -> None:
    """Render per-table column filter and sort controls inside the Surface Inspector."""
    col_opts = {c.name: f"{c.name} ({c.data_type})" for c in preview.columns}
    if not state["draft_col"] and preview.columns:
        state["draft_col"] = preview.columns[0].name
    sort_opts = {"": "Natural Order"} | {c.name: c.name for c in preview.columns}

    def _set_sort_col(val: Any) -> None:
        state["sort_col"] = str(val)
        on_refresh()

    def _set_sort_desc(val: Any) -> None:
        state["sort_desc"] = bool(val)
        on_refresh()

    with ui.row().classes("w-full items-center gap-2 flex-wrap"):
        (
            ui.select(
                options=col_opts,
                value=state["draft_col"]
                if state["draft_col"] in col_opts
                else preview.columns[0].name,
                label="Filter Column",
                on_change=lambda e: state.__setitem__("draft_col", str(e.value)),
            )
            .props("dense outlined dark")
            .classes("w-56")
        )
        (
            ui.select(
                options={
                    "contains": "Contains",
                    "eq": "Equals (=)",
                    "gte": ">= (Min)",
                    "lte": "<= (Max)",
                    "is_null": "Is Null",
                    "is_not_null": "Is Not Null",
                },
                value=state["draft_op"],
                label="Operator",
                on_change=lambda e: state.__setitem__("draft_op", str(e.value)),
            )
            .props("dense outlined dark")
            .classes("w-36")
        )
        (
            ui.input(
                label="Filter Value",
                value=str(state["draft_val"]),
                on_change=lambda e: state.__setitem__("draft_val", e.value or ""),
            )
            .props("dense outlined dark clearable")
            .classes("w-48")
        )
        ui.button("Add Filter", icon="filter_alt", on_click=on_add_filter).props(
            "dense color=primary"
        )
        (
            ui.select(
                options=sort_opts,
                value=state["sort_col"] if state["sort_col"] in sort_opts else "",
                label="Sort Column",
                on_change=lambda e: _set_sort_col(e.value),
            )
            .props("dense outlined dark")
            .classes("w-48")
        )
        ui.checkbox(
            "Desc", value=bool(state["sort_desc"]), on_change=lambda e: _set_sort_desc(e.value)
        )
        if state["col_filters"] or state["sort_col"]:
            ui.button("Clear", icon="clear_all", on_click=on_clear).props(
                "dense flat color=warning"
            )

    if state["col_filters"]:
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            for f in state["col_filters"]:
                lbl = f"{f.column} {f.operator.value} {f.value or ''}".strip()
                status_badge(lbl, tone=BadgeTone.INFO)


def _render_surface_preview_card(
    preview: TablePreviewResult,
    state: dict[str, Any],
    on_update_limit: Callable[[Any], None],
    on_add_filter: Callable[[], None],
    on_clear: Callable[[], None],
    on_refresh: Callable[[], Any],
    on_download: Callable[[], None],
) -> None:
    """Render the Surface Inspector card, column schema, and bounded preview table."""
    with (
        ui.card()
        .classes("w-full p-4 gap-3")
        .style(f"background-color: {StudioColor.CARD}; border: 1px solid {StudioColor.BORDER};")
    ):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.column().classes("gap-0"):
                ui.label(f"Surface Inspector: {preview.physical_name}").classes(
                    "text-lg font-semibold"
                ).style(f"color: {StudioColor.TEXT}")
                ui.label(
                    f"Grain: {preview.grain} · Producer: {preview.producer} · {preview.purpose}"
                ).classes("text-xs").style(f"color: {StudioColor.MUTED}")
            with ui.row().classes("items-center gap-2 flex-wrap"):
                if preview.layer is not None:
                    status_badge(preview.layer.value.upper(), tone=BadgeTone.INFO)
                if preview.usage is not None:
                    status_badge(preview.usage.value, tone=BadgeTone.NEUTRAL)
                (
                    ui.select(
                        options={25: "25 rows", 100: "100 rows", 250: "250 rows", 500: "500 rows"},
                        value=int(state["preview_limit"]),
                        label="UI Preview Limit",
                        on_change=lambda e: on_update_limit(e.value),
                    )
                    .props("dense outlined dark")
                    .classes("w-36")
                )
                if not preview.is_unclassified_blocked:
                    ui.button("Download Full CSV", icon="download", on_click=on_download).props(
                        "outline color=primary dense"
                    )

        if preview.warning_message:
            tone = (
                StudioColor.ERROR.value
                if preview.is_unclassified_blocked
                else StudioColor.WARNING.value
            )
            with (
                ui.row()
                .classes("w-full items-center gap-2 p-2 rounded")
                .style(f"border: 1px solid {tone}; background-color: rgba(245, 158, 11, 0.08);")
            ):
                ui.icon("warning", size="18px").style(f"color: {tone}")
                ui.label(preview.warning_message).classes("text-xs font-medium").style(
                    f"color: {tone}"
                )

        if preview.columns and not preview.is_unclassified_blocked:
            _render_surface_filter_bar(preview, state, on_add_filter, on_clear, on_refresh)
            col_defs = [
                TableColumn(name="name", label="Column", field="name"),
                TableColumn(name="data_type", label="Type", field="data_type"),
                TableColumn(name="nullable", label="Nullable", field="nullable"),
            ]
            col_rows = [
                {
                    "name": c.name,
                    "data_type": c.data_type,
                    "nullable": "Yes" if c.nullable else "No",
                }
                for c in preview.columns
            ]
            with ui.expansion(
                f"Column Schema ({len(col_rows)} columns)", icon="view_column"
            ).classes("w-full"):
                bounded_data_table(columns=col_defs, rows=col_rows, row_key="name", pagination=12)

        if preview.is_unclassified_blocked:
            empty_state_panel(
                title="Raw Preview Blocked",
                message=preview.warning_message or "Unclassified table.",
                icon="block",
            )
        elif not preview.rows:
            empty_state_panel(
                title="No Preview Rows",
                message="No rows matched the current surface filters.",
                icon="table_rows",
            )
        else:
            row_keys = list(preview.rows[0].keys())
            p_cols = [TableColumn(name=k, label=k, field=k) for k in row_keys]
            p_rows = [{k: _format_cell_value(r.get(k)) for k in row_keys} for r in preview.rows]
            eff_total = (
                preview.filtered_row_count
                if preview.filtered_row_count is not None
                else preview.total_row_count
            )
            total_lbl = f"{eff_total:,}" if eff_total is not None else "?"
            bounded_data_table(
                columns=p_cols,
                rows=p_rows,
                max_rows=500,
                pagination=15,
                show_export=False,
                title=(
                    f"UI Preview ({len(preview.rows)} of {total_lbl} matching rows · "
                    "Use 'Download Full CSV' for complete export)"
                ),
            )


def _render_catalog_overview(browse: CatalogBrowseResult) -> None:
    """Render 82-surface KPI strip and layer/row-count overview charts."""
    cov = browse.coverage
    with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3"):
        kpi_card(
            title="Expected Atlas Surfaces",
            value=str(cov.total_expected),
            subtitle="20 Bronze · 24 Silver · 22 Gold · 6 Meta · 10 CP",
            status_pill="Atlas",
            status_pill_tone=BadgeTone.INFO,
        )
        kpi_card(
            title="Discovered Surfaces",
            value=str(cov.total_discovered),
            subtitle=f"Filtered view: {len(browse.entries)} surfaces",
            status_pill=cov.compatibility_status.value,
            status_pill_tone=(
                BadgeTone.SUCCESS
                if not cov.missing_surfaces and not cov.unexpected_surfaces
                else BadgeTone.WARNING
            ),
        )
        kpi_card(
            title="Gold & Silver Surfaces",
            value=str(
                cov.layer_counts.get(SurfaceLayer.GOLD, 0)
                + cov.layer_counts.get(SurfaceLayer.SILVER, 0)
            ),
            subtitle="Analytical marts & canonical drill facts",
            status_pill="Warehouse",
            status_pill_tone=BadgeTone.SUCCESS,
        )
        kpi_card(
            title="Bronze & Control Plane",
            value=str(
                cov.layer_counts.get(SurfaceLayer.BRONZE, 0)
                + cov.layer_counts.get(SurfaceLayer.CONTROL_PLANE, 0)
            ),
            subtitle="Raw evidence & operational audit tables",
            status_pill="Evidence",
            status_pill_tone=BadgeTone.NEUTRAL,
        )

    with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-3"):
        chart_panel(
            title="Surface Coverage by Layer",
            subtitle="Distribution across Bronze, Silver, Gold, Meta, and Control Plane",
            figure=build_catalog_layer_usage_figure(cov),
            source_caption="Source: PFETLCatalog + meta.m_Data_Contracts",
            interaction_mode="analytical",
        )
        chart_panel(
            title="Largest Warehouse Surfaces by Row Count",
            subtitle="Top 12 populated surfaces in the active profile",
            figure=build_catalog_top_tables_figure(browse.entries),
            source_caption="Source: meta.m_Table_Row_Counts & SQLite CP",
            interaction_mode="analytical",
        )


def _render_catalog_tab(
    profile: PFETLProfile,
    run_id: str,
    catalog_snapshot: CatalogSnapshot | None,
) -> None:
    """Render the 82-surface Data Catalog browser, per-table filterer, and full CSV export."""
    state: dict[str, Any] = {
        "layer": "all",
        "domain": "all",
        "usage": "all",
        "search": "",
        "selected_table": "gold.core_monthly_fact",
        "preview_limit": 100,
        "col_filters": [],
        "draft_col": "",
        "draft_op": "contains",
        "draft_val": "",
        "sort_col": "",
        "sort_desc": False,
    }

    def _update_filter(key: str, val: Any) -> None:
        state[key] = val
        _catalog_body.refresh()

    def _select_table(row: dict[str, Any]) -> None:
        state["selected_table"] = str(row["physical_name"])
        state["col_filters"] = []
        state["draft_col"] = ""
        state["draft_val"] = ""
        state["sort_col"] = ""
        _preview_panel.refresh()

    def _update_limit(val: Any) -> None:
        state["preview_limit"] = int(val)
        _preview_panel.refresh()

    def _add_col_filter() -> None:
        col = str(state["draft_col"]).strip()
        if not col:
            return
        op = FilterOperator(str(state["draft_op"]))
        val_str = str(state["draft_val"]).strip()
        if op not in (FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL) and not val_str:
            return
        new_f = SurfaceColumnFilter(
            column=col,
            operator=op,
            value=None if op in (FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL) else val_str,
        )
        state["col_filters"] = [*state["col_filters"], new_f]
        state["draft_val"] = ""
        _preview_panel.refresh()

    def _clear_col_filters() -> None:
        state["col_filters"] = []
        state["sort_col"] = ""
        _preview_panel.refresh()

    def _download_full_surface() -> None:
        target = str(state["selected_table"])
        try:
            csv_bytes, row_cnt, filename = ExplorerAnalytics.export_surface_csv(
                profile,
                target,
                filters=tuple(state["col_filters"]),
                sort_column=str(state["sort_col"]) or None,
                sort_desc=bool(state["sort_desc"]),
            )
            ui.download(csv_bytes, filename=filename)
            ui.notify(f"Exported {row_cnt:,} rows to {filename}", type="positive")
        except Exception as exc:
            ui.notify(f"Export failed: {exc}", type="negative")

    @ui.refreshable
    def _preview_panel() -> None:
        target = str(state["selected_table"])
        if not target:
            return
        try:
            preview = ExplorerAnalytics.preview(
                profile,
                target,
                limit=int(state["preview_limit"]),
                filters=tuple(state["col_filters"]),
                sort_column=str(state["sort_col"]) or None,
                sort_desc=bool(state["sort_desc"]),
                catalog_snapshot=catalog_snapshot,
                run_id=run_id,
                cache=_EXPLORER_CACHE,
            )
        except (PFETLDataAccessError, ValueError) as exc:
            error_state_panel(title="Preview Unavailable", message=str(exc))
            return
        _render_surface_preview_card(
            preview,
            state,
            _update_limit,
            _add_col_filter,
            _clear_col_filters,
            _preview_panel.refresh,
            _download_full_surface,
        )

    @ui.refreshable
    def _catalog_body() -> None:
        cat_filter = CatalogFilter(
            layer=SurfaceLayer(state["layer"]) if state["layer"] != "all" else None,
            domain=None if state["domain"] == "all" else str(state["domain"]),
            usage=SurfaceUsageCategory(state["usage"]) if state["usage"] != "all" else None,
            search=str(state["search"]),
        )
        try:
            browse = ExplorerAnalytics.catalog_entries(
                profile, cat_filter, run_id=run_id, cache=_EXPLORER_CACHE
            )
        except PFETLDataAccessError as exc:
            error_state_panel(title="Catalog Unavailable", message=str(exc))
            return

        _render_catalog_overview(browse)

        layer_opts: dict[str, str] = {"all": "All Layers"} | {
            ly.value: ly.value.replace("_", " ").title() for ly in SurfaceLayer
        }
        domain_opts: dict[str, str] = {"all": "All Domains"} | {
            d: d for d in browse.available_domains
        }
        usage_opts: dict[str, str] = {"all": "All Classifications"} | {
            u.value: u.value for u in SurfaceUsageCategory
        }

        with (
            ui.card()
            .classes("w-full p-3")
            .style(f"background-color: {StudioColor.CARD}; border: 1px solid {StudioColor.BORDER};")
        ):
            with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                (
                    ui.select(
                        options=layer_opts,
                        value=state["layer"],
                        label="Layer",
                        on_change=lambda e: _update_filter("layer", e.value),
                    )
                    .props("dense outlined dark")
                    .classes("w-44")
                )
                (
                    ui.select(
                        options=domain_opts,
                        value=state["domain"],
                        label="Domain",
                        on_change=lambda e: _update_filter("domain", e.value),
                    )
                    .props("dense outlined dark")
                    .classes("w-48")
                )
                (
                    ui.select(
                        options=usage_opts,
                        value=state["usage"],
                        label="Classification",
                        on_change=lambda e: _update_filter("usage", e.value),
                    )
                    .props("dense outlined dark")
                    .classes("w-48")
                )
                (
                    ui.input(
                        label="Search table / grain / producer",
                        value=str(state["search"]),
                        on_change=lambda e: _update_filter("search", e.value or ""),
                    )
                    .props("dense outlined dark clearable")
                    .classes("w-72")
                )

        table_cols = [
            TableColumn(name="physical_name", label="Physical Surface", field="physical_name"),
            TableColumn(name="layer", label="Layer", field="layer"),
            TableColumn(name="domain", label="Domain", field="domain"),
            TableColumn(name="grain", label="Grain", field="grain"),
            TableColumn(name="producer", label="Producer", field="producer"),
            TableColumn(name="classification", label="Usage", field="classification"),
            TableColumn(name="col_count", label="Cols", field="col_count", align="right"),
            TableColumn(name="row_count", label="Rows", field="row_count", align="right"),
        ]
        table_rows = [
            {
                "physical_name": e.physical_name,
                "layer": e.layer.value,
                "domain": e.domain,
                "grain": e.grain,
                "producer": e.producer,
                "classification": e.classification.value if e.classification else "UNCLASSIFIED",
                "col_count": len(e.columns),
                "row_count": f"{e.row_count:,}" if e.row_count is not None else "—",
            }
            for e in browse.entries
        ]
        bounded_data_table(
            columns=table_cols,
            rows=table_rows,
            row_key="physical_name",
            pagination=15,
            title="82-Surface Upstream Data Catalog (click row to inspect, filter & export)",
            on_row_click=_select_table,
        )
        _preview_panel()

    _catalog_body()


def _render_guided_results_view(g_state: dict[str, Any]) -> None:
    """Render Guided Analytics semantic summary, Plotly chart, and bounded table."""
    if g_state["error"]:
        error_state_panel(
            title="Guided Query Validation / Execution Error",
            message=str(g_state["error"]),
        )
        return

    res: GuidedQueryResult | None = g_state["last_result"]
    if res is None:
        empty_state_panel(
            title="Ready to Query",
            message="Pick a Starter Recipe above or configure a dataset, then click Run Guided Query.",
            icon="query_stats",
        )
        return

    with (
        ui.card()
        .classes("w-full p-3")
        .style(f"background-color: {StudioColor.CARD}; border: 1px solid {StudioColor.BORDER};")
    ):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            ui.label(res.semantic_summary).classes("text-xs font-medium").style(
                f"color: {StudioColor.TEXT}"
            )
            with ui.row().classes("items-center gap-2"):
                status_badge(f"{res.row_count:,} rows", tone=BadgeTone.INFO)
                status_badge(
                    res.chart_suggestion.chart_type.value.replace("_", " ").title(),
                    tone=BadgeTone.NEUTRAL,
                )
        for warn in res.warnings:
            ui.label(f"Note: {warn}").classes("text-xs").style(f"color: {StudioColor.WARNING}")

    if not res.rows:
        empty_state_panel(
            title="No Matching Rows",
            message="Your query specification is preserved above—adjust filters or period and re-run.",
            icon="filter_alt_off",
        )
        return

    view_mode = str(g_state["view_mode"])
    chart_override = str(g_state["chart_override"])
    effective_chart = (
        chart_override if chart_override != "auto" else res.chart_suggestion.chart_type.value
    )
    if view_mode in ("both", "chart") and effective_chart != ChartRecommendation.TABLE.value:
        fig = build_guided_explorer_figure(res, chart_override=chart_override)
        with (
            ui.card()
            .classes("w-full p-3")
            .style(f"background-color: {StudioColor.CARD}; border: 1px solid {StudioColor.BORDER};")
        ):
            ui.label(res.chart_suggestion.reason).classes("text-xs mb-1").style(
                f"color: {StudioColor.MUTED}"
            )
            ui.plotly(fig).classes("w-full")

    if view_mode in ("both", "table"):
        metric_fmt_map = {m.id: m.format for m in res.metric_specs}
        cols = [
            TableColumn(
                name=c,
                label=c,
                field=c,
                align="right" if c in metric_fmt_map else "left",
            )
            for c in res.columns
        ]
        formatted_rows = [
            {c: _format_cell_value(r.get(c), metric_fmt_map.get(c)) for c in res.columns}
            for r in res.rows
        ]
        bounded_data_table(
            columns=cols,
            rows=formatted_rows,
            max_rows=min(res.query.limit, 5000),
            pagination=25,
            title=f"Guided Query Output ({res.dataset.label})",
        )


def _render_guided_builder_card(
    profile_id: str,
    datasets: list[DatasetSpec],
    ds_map: dict[str, DatasetSpec],
    g_state: dict[str, Any],
    on_apply_recipe: Callable[[_GuidedRecipe], None],
    on_dataset_change: Callable[[str], None],
    on_view_mode: Callable[[Any], None],
    on_chart_override: Callable[[Any], None],
    on_download_csv: Callable[[], None],
    on_build_query: Callable[[], GuidedQuery],
    on_view_saved: Callable[[], Any] | None,
    on_run_query: Callable[[], None],
) -> None:
    """Render Guided Analytics starter recipes, dataset/metric selectors, and action bar."""
    ds = ds_map[str(g_state["dataset_id"])]
    ds_metrics = ExplorerAnalytics.list_metrics(ds.id)
    ds_opts = {d.id: f"{d.label} ({d.domain} · {d.grain})" for d in datasets}
    dim_opts = {d: d for d in ds.allowed_dimensions}
    met_opts = {
        m.id: f"{m.label} [{'Additive' if m.additive else 'Non-Additive · ' + m.aggregation.value}]"
        for m in ds_metrics
    }
    filter_opts: dict[str, str] = {"none": "No Filter"} | {
        f.value: f.value for f in ds.allowed_filters
    }
    sort_candidates = [*g_state["dimensions"], *g_state["metrics"]]
    sort_opts = {"": "Default Order"} | {s: s for s in sort_candidates}

    with (
        ui.card()
        .classes("w-full p-4 gap-3")
        .style(f"background-color: {StudioColor.CARD}; border: 1px solid {StudioColor.BORDER};")
    ):
        ui.label("Starter Analytical Recipes (One-Click)").classes(
            "text-xs font-semibold uppercase tracking-wider"
        ).style(f"color: {StudioColor.MUTED}")
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            for rec in _GUIDED_RECIPES:
                ui.button(
                    rec.label,
                    icon=rec.icon,
                    on_click=lambda _e, r=rec: on_apply_recipe(r),
                ).props("outline dense size=sm color=primary")

        with ui.grid().classes("w-full grid-cols-1 md:grid-cols-3 gap-3"):
            ui.select(
                options=ds_opts,
                value=ds.id,
                label="Dataset",
                on_change=lambda e: on_dataset_change(str(e.value)),
            ).props("dense outlined dark")
            ui.select(
                options=dim_opts,
                value=g_state["dimensions"],
                multiple=True,
                label="Dimensions (Group / Slice)",
                on_change=lambda e: g_state.__setitem__("dimensions", list(e.value or [])),
            ).props("dense outlined dark use-chips")
            ui.select(
                options=met_opts,
                value=g_state["metrics"],
                multiple=True,
                label="Metrics",
                on_change=lambda e: g_state.__setitem__("metrics", list(e.value or [])),
            ).props("dense outlined dark use-chips")

        with ui.grid().classes("w-full grid-cols-1 md:grid-cols-6 gap-3 items-center"):
            ui.select(
                options={
                    "global": "Use Global Period",
                    "all": "All Dates",
                    "since_inception": "Since Inception",
                    "latest_complete_month": "Latest Complete Month",
                    "fytd": "FYTD",
                    "calendar_ytd": "Calendar YTD",
                    "rolling_12_months": "Rolling 12M",
                },
                value=g_state["period_preset"],
                label="Period",
                on_change=lambda e: g_state.__setitem__("period_preset", str(e.value)),
            ).props("dense outlined dark")
            ui.select(
                options=filter_opts,
                value=g_state["filter_field"],
                label="Filter Field",
                on_change=lambda e: g_state.__setitem__("filter_field", str(e.value)),
            ).props("dense outlined dark")
            ui.select(
                options={"eq": "Equals (=)", "contains": "Contains", "gte": ">=", "lte": "<="},
                value=g_state["filter_op"],
                label="Op",
                on_change=lambda e: g_state.__setitem__("filter_op", str(e.value)),
            ).props("dense outlined dark")
            ui.input(
                label="Filter Value",
                value=str(g_state["filter_value"]),
                on_change=lambda e: g_state.__setitem__("filter_value", e.value or ""),
            ).props("dense outlined dark clearable")
            ui.select(
                options=sort_opts,
                value=g_state["sort_field"] if g_state["sort_field"] in sort_opts else "",
                label="Sort By",
                on_change=lambda e: g_state.__setitem__("sort_field", str(e.value)),
            ).props("dense outlined dark")
            ui.number(
                label="UI Limit (max 10,000)",
                value=int(g_state["limit"]),
                min=1,
                max=10000,
                on_change=lambda e: g_state.__setitem__("limit", int(e.value or 500)),
            ).props("dense outlined dark")

        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.row().classes("items-center gap-3 flex-wrap"):
                ui.toggle(
                    {"both": "Chart + Table", "chart": "Chart Only", "table": "Table Only"},
                    value=g_state["view_mode"],
                    on_change=lambda e: on_view_mode(e.value),
                ).props("dense no-caps")
                (
                    ui.select(
                        options={
                            "auto": "Chart: Auto",
                            "time_series": "Line (Time Series)",
                            "bar": "Bar Chart",
                            "grouped_bar": "Grouped Bar",
                            "stacked_bar": "Stacked Bar",
                            "horizontal_bar": "Horizontal Bar",
                            "table": "Table Only",
                        },
                        value=g_state["chart_override"],
                        label="Chart Type",
                        on_change=lambda e: on_chart_override(e.value),
                    )
                    .props("dense outlined dark")
                    .classes("w-44")
                )
            with ui.row().classes("items-center gap-2"):
                ui.button(
                    "Save as View",
                    icon="bookmark_add",
                    on_click=lambda: open_save_view_dialog(
                        profile_id,
                        kind=SavedViewKind.GUIDED,
                        query=on_build_query(),
                        on_saved=lambda _v: on_view_saved() if on_view_saved else None,
                    ),
                ).props("outline color=primary")
                ui.button(
                    "Download Full CSV Dump",
                    icon="download",
                    on_click=on_download_csv,
                ).props("outline color=primary")
                ui.button("Run Guided Query", icon="play_arrow", on_click=on_run_query).props(
                    "color=primary"
                )


def _render_guided_tab(
    profile: PFETLProfile,
    run_id: str,
    catalog_snapshot: CatalogSnapshot | None,
    *,
    global_period: PeriodRequest | None = None,
    global_filters: FilterSet | None = None,
    on_view_saved: Callable[[], Any] | None = None,
) -> None:
    """Render Guided Analytics with starter recipes, chart overrides, and full CSV dump."""
    datasets = ExplorerAnalytics.list_datasets()
    ds_map = {d.id: d for d in datasets}
    first_recipe = _GUIDED_RECIPES[0]

    g_state: dict[str, Any] = {
        "dataset_id": first_recipe.dataset_id,
        "dimensions": list(first_recipe.dimensions),
        "metrics": list(first_recipe.metrics),
        "period_preset": "global" if global_period is not None else first_recipe.period_preset,
        "filter_field": "none",
        "filter_op": "eq",
        "filter_value": "",
        "sort_field": first_recipe.sort_field,
        "sort_desc": first_recipe.sort_desc,
        "limit": 500,
        "view_mode": "both",
        "chart_override": "auto",
        "last_result": None,
        "error": None,
    }

    def _build_query() -> GuidedQuery:
        ds = ds_map[str(g_state["dataset_id"])]
        allowed_f = set(ds.allowed_filters)
        filters_list: list[FilterSpec] = (
            [f for f in global_filters.filters if f.field in allowed_f]
            if global_filters is not None
            else []
        )
        if g_state["filter_field"] != "none" and str(g_state["filter_value"]).strip():
            f_field = FilterField(str(g_state["filter_field"]))
            f_op = FilterOperator(str(g_state["filter_op"]))
            raw_val = str(g_state["filter_value"]).strip()
            parsed_val: Any = raw_val
            if raw_val.lower() in ("true", "false"):
                parsed_val = raw_val.lower() == "true"
            filters_list = [f for f in filters_list if f.field != f_field]
            filters_list.append(FilterSpec(field=f_field, operator=f_op, value=parsed_val))

        sort_specs: tuple[GuidedSortSpec, ...] = ()
        if str(g_state["sort_field"]).strip():
            sort_specs = (
                GuidedSortSpec(
                    field=str(g_state["sort_field"]),
                    descending=bool(g_state["sort_desc"]),
                ),
            )

        if g_state["period_preset"] == "global":
            period_req = global_period
        elif g_state["period_preset"] != "all":
            period_req = PeriodRequest(preset=PeriodPreset(str(g_state["period_preset"])))
        else:
            period_req = None
        return GuidedQuery(
            dataset_id=str(g_state["dataset_id"]),
            dimensions=tuple(str(d) for d in g_state["dimensions"]),
            metrics=tuple(str(m) for m in g_state["metrics"]),
            filters=FilterSet(filters=tuple(filters_list)),
            period=period_req,
            sort=sort_specs,
            limit=int(g_state["limit"]),
        )

    def _run_query(*, refresh_ui: bool = True) -> None:
        g_state["error"] = None
        try:
            g_state["last_result"] = ExplorerAnalytics.execute_guided(
                profile,
                _build_query(),
                run_id=run_id,
                catalog_snapshot=catalog_snapshot,
                cache=_EXPLORER_CACHE,
            )
        except Exception as exc:
            g_state["error"] = str(exc)
        if refresh_ui:
            _guided_results.refresh()

    def _apply_recipe(recipe: _GuidedRecipe) -> None:
        g_state["dataset_id"] = recipe.dataset_id
        g_state["dimensions"] = list(recipe.dimensions)
        g_state["metrics"] = list(recipe.metrics)
        g_state["period_preset"] = recipe.period_preset
        g_state["filter_field"] = "none"
        g_state["filter_value"] = ""
        g_state["sort_field"] = recipe.sort_field
        g_state["sort_desc"] = recipe.sort_desc
        g_state["chart_override"] = "auto"
        _guided_builder.refresh()
        _run_query()

    def _on_dataset_change(new_ds_id: str) -> None:
        new_ds = ds_map[new_ds_id]
        new_mets = ExplorerAnalytics.list_metrics(new_ds_id)
        g_state["dataset_id"] = new_ds_id
        g_state["dimensions"] = list(new_ds.default_dimensions[:1])
        g_state["metrics"] = [new_mets[0].id] if new_mets else []
        g_state["filter_field"] = "none"
        g_state["filter_value"] = ""
        g_state["sort_field"] = new_ds.default_dimensions[0] if new_ds.default_dimensions else ""
        _guided_builder.refresh()

    def _download_guided_csv() -> None:
        try:
            csv_bytes, row_cnt, filename = ExplorerAnalytics.export_guided_csv(
                profile, _build_query()
            )
            ui.download(csv_bytes, filename=filename)
            ui.notify(f"Exported {row_cnt:,} rows to {filename}", type="positive")
        except Exception as exc:
            ui.notify(f"Export failed: {exc}", type="negative")

    def _set_view_mode(val: Any) -> None:
        g_state["view_mode"] = str(val)
        _guided_results.refresh()

    def _set_chart_override(val: Any) -> None:
        g_state["chart_override"] = str(val)
        _guided_results.refresh()

    @ui.refreshable
    def _guided_results() -> None:
        _render_guided_results_view(g_state)

    @ui.refreshable
    def _guided_builder() -> None:
        _render_guided_builder_card(
            profile.profile_id,
            datasets,
            ds_map,
            g_state,
            _apply_recipe,
            _on_dataset_change,
            _set_view_mode,
            _set_chart_override,
            _download_guided_csv,
            _build_query,
            on_view_saved,
            _run_query,
        )

    _run_query(refresh_ui=False)
    _guided_builder()
    _guided_results()


def render_explorer_page(context: AppContext) -> None:
    """Render the Explorer workspace (Catalog, Guided, Saved Views, Read-Only SQL)."""
    render_domain_workspace(
        context=context,
        route=ROUTES_BY_ID["explorer"],
        workspace_views=(
            "82-Surface Data Catalog",
            "Guided Whitelisted Analytics",
            "Saved Views",
            "Advanced Read-Only SQL",
        ),
        upstream_sources=(
            "meta.m_Data_Contracts",
            "meta.m_Table_Row_Counts",
            "bronze.* (bounded evidence preview)",
            "silver.* & gold.* & cp_*",
        ),
    )

    if context.profile is None or context.is_warehouse_locked or context.is_incompatible:
        return

    profile = context.profile
    run_id = context.analytical_run_id or "latest"
    catalog_snapshot = context.catalog_snapshot

    with ui.tabs().classes("w-full") as tabs:
        cat_tab = ui.tab("Data Catalog (82 Surfaces)", icon="dataset")
        guided_tab = ui.tab("Guided Analytics", icon="query_stats")
        saved_tab = ui.tab("Saved Views", icon="bookmarks")
        sql_tab = ui.tab("Advanced SQL (Read-Only)", icon="terminal")

    refresh_saved_ref: list[Callable[[], Any]] = []
    refresh_sql_ref: list[Callable[[], Any]] = []

    def _notify_saved() -> None:
        for fn in refresh_saved_ref:
            fn()

    def _notify_sql() -> None:
        for fn in refresh_sql_ref:
            fn()

    with ui.tab_panels(tabs, value=cat_tab).classes("w-full bg-transparent p-0"):
        with ui.tab_panel(cat_tab).classes("p-0 gap-3"):
            _render_catalog_tab(profile, run_id, catalog_snapshot)
        with ui.tab_panel(guided_tab).classes("p-0 gap-3"):
            _render_guided_tab(
                profile,
                run_id,
                catalog_snapshot,
                global_period=context.period_request,
                global_filters=context.filters,
                on_view_saved=_notify_saved,
            )
        with ui.tab_panel(saved_tab).classes("p-0 gap-3"):
            refresh_saved_ref.append(
                render_saved_views_tab(
                    profile,
                    run_id,
                    catalog_snapshot,
                    _EXPLORER_CACHE,
                    global_period=context.period_request,
                    global_filters=context.filters,
                    on_views_changed=_notify_sql,
                )
            )
        with ui.tab_panel(sql_tab).classes("p-0 gap-3"):
            refresh_sql_ref.append(
                render_sql_tab(profile, run_id, _EXPLORER_CACHE, on_view_saved=_notify_saved)
            )
