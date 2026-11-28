"""Reports page module — deterministic report runtime preview, catalog, and run history."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nicegui import run, ui

from pfetl_analytics_studio.analytics import OperationsAnalytics, RunHistoryFilter
from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import QueryCache, create_temp_export_path
from pfetl_analytics_studio.pages.report_editor_ui import (
    open_delete_report_dialog,
    open_rename_report_dialog,
    render_custom_editor_panel,
)
from pfetl_analytics_studio.reports import (
    DEFAULT_REPORT_REGISTRY,
    ExportPrerequisiteError,
    ExportResult,
    ProvenanceChange,
    RenderedBlock,
    RenderedBrokenBlock,
    RenderedChartBlock,
    RenderedKPIGroupBlock,
    RenderedProvenanceBlock,
    RenderedStructuralBlock,
    RenderedTableBlock,
    RenderedTextBlock,
    ReportContext,
    ReportContextOverrides,
    ReportDefinition,
    ReportDefinitionDraft,
    ReportExporter,
    ReportResult,
    ReportService,
    check_static_export_capability,
    clone_definition_as_draft,
    compare_report_provenance,
    sanitize_filename,
)
from pfetl_analytics_studio.semantic import (
    PeriodRequest,
    preset_display_name,
)
from pfetl_analytics_studio.state import (
    ReportRunRecord,
    SavedReportRecord,
    SavedViewDefinition,
    StudioStateStore,
)
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    TableColumn,
    bounded_data_table,
    empty_state_panel,
    error_state_panel,
    period_selector,
    status_badge,
    trigger_file_download,
)

_REPORTS_CACHE = QueryCache(max_entries=32)


def _render_provenance_block(block: RenderedProvenanceBlock) -> None:
    """Render mandatory PFETL run provenance & snapshot badge card."""
    with ui.card().classes("studio-card w-full p-4 rounded-xl gap-2"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            ui.label("Report Provenance & Snapshot Audit").classes(
                "text-xs font-semibold uppercase tracking-wider"
            ).style(f"color: {StudioColor.MUTED}")
            status_badge(
                f"Studio v{block.studio_version}",
                tone=BadgeTone.INFO,
                outline=True,
            )
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            status_badge(f"Run: {block.pfetl_run_id}", tone=BadgeTone.INFO)
            if block.core_version:
                status_badge(f"Core: {block.core_version}", tone=BadgeTone.NEUTRAL)
            if block.schema_version:
                status_badge(f"Schema: {block.schema_version}", tone=BadgeTone.NEUTRAL)
            status_badge(f"Period: {block.period_label}", tone=BadgeTone.SUCCESS, outline=True)
            if block.comparison_label:
                status_badge(
                    f"Comparison: {block.comparison_label}",
                    tone=BadgeTone.INFO,
                    outline=True,
                )
            status_badge(
                f"Currency: {block.currency_mode.upper()}",
                tone=BadgeTone.NEUTRAL,
                outline=True,
            )
        if block.settings_snapshot_id or block.rules_snapshot_id:
            with (
                ui.row()
                .classes("w-full items-center gap-4 flex-wrap text-xs")
                .style(f"color: {StudioColor.MUTED}")
            ):
                if block.settings_snapshot_id:
                    ui.label(f"Settings Snapshot: {block.settings_snapshot_id[:24]}…")
                if block.rules_snapshot_id:
                    ui.label(f"Rules Snapshot: {block.rules_snapshot_id[:24]}…")
        if block.disclaimer_text:
            ui.label(block.disclaimer_text).classes("text-xs italic").style(
                f"color: {StudioColor.WARNING}"
            )


def _kpi_delta_badge_tone(tone: str) -> BadgeTone:
    if tone == "positive":
        return BadgeTone.SUCCESS
    if tone == "warning":
        return BadgeTone.WARNING
    if tone == "negative":
        return BadgeTone.ERROR
    return BadgeTone.NEUTRAL


def _render_kpi_group_block(block: RenderedKPIGroupBlock) -> None:
    """Render a `RenderedKPIGroupBlock` as a responsive grid of multi-info KPI cards."""
    with ui.column().classes("w-full gap-2"):
        ui.label(block.title).classes("text-base font-semibold").style(f"color: {StudioColor.TEXT}")
        with ui.grid(columns=4).classes("w-full gap-3"):
            for item in block.items:
                card = ui.card().classes("studio-card p-3 rounded-xl gap-1.5")
                if item.tooltip:
                    card.tooltip(item.tooltip)
                with card:
                    with ui.row().classes("w-full items-center justify-between gap-1"):
                        ui.label(item.label).classes("text-xs font-medium").style(
                            f"color: {StudioColor.MUTED}"
                        )
                        if item.delta_text:
                            status_badge(
                                item.delta_text,
                                tone=_kpi_delta_badge_tone(item.delta_tone),
                                outline=True,
                            )
                    ui.label(item.formatted_value).classes("text-xl font-bold").style(
                        f"color: {StudioColor.TEXT}"
                    )
                    if item.breakups:
                        with ui.row().classes("w-full items-center gap-1.5 flex-wrap pt-0.5"):
                            for b_lbl, b_val in item.breakups[:3]:
                                ui.label(f"{b_lbl}: {b_val}").classes(
                                    "text-[11px] px-1.5 py-0.5 rounded"
                                ).style(
                                    "background: rgba(148, 163, 184, 0.12); "
                                    f"color: {StudioColor.MUTED}"
                                )
                    if item.subtitle:
                        ui.label(item.subtitle).classes("text-xs").style(
                            f"color: {StudioColor.MUTED}"
                        )


def _render_chart_block(
    block: RenderedChartBlock,
    on_export_image: Callable[[RenderedChartBlock, str], Any] | None = None,
) -> None:
    """Render a `RenderedChartBlock` via `ui.plotly` with optional PNG/SVG export buttons."""
    with ui.card().classes("studio-card w-full p-4 rounded-xl gap-2"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            ui.label(block.title).classes("text-base font-semibold").style(
                f"color: {StudioColor.TEXT}"
            )
            if block.figure_dict and on_export_image is not None:
                with ui.row().classes("items-center gap-1"):
                    ui.button(
                        "PNG",
                        icon="image",
                        on_click=lambda _e, b=block: on_export_image(b, "png"),
                    ).props("outline dense size=sm")
                    ui.button(
                        "SVG",
                        icon="polyline",
                        on_click=lambda _e, b=block: on_export_image(b, "svg"),
                    ).props("outline dense size=sm")
        if block.figure_dict:
            ui.plotly(block.figure_dict).classes("w-full h-80")
        else:
            ui.label("No chart data available for the selected period.").classes("text-sm").style(
                f"color: {StudioColor.MUTED}"
            )
        if block.caption:
            ui.label(block.caption).classes("text-xs").style(f"color: {StudioColor.MUTED}")


def _render_table_block(block: RenderedTableBlock) -> None:
    """Render a `RenderedTableBlock` via `bounded_data_table`."""
    cols = [TableColumn(name=col, label=col, field=col, sortable=True) for col in block.columns]
    bounded_data_table(
        columns=cols,
        rows=list(block.rows),
        title=f"{block.title} ({len(block.rows)} of {block.total_rows} rows)",
        max_rows=len(block.rows) or 10,
        export_filename=f"{block.source_id.replace('.', '_')}.csv",
    )


def _render_broken_block(block: RenderedBrokenBlock) -> None:
    """Render a graceful warning card for a broken/missing report block."""
    with (
        ui.card()
        .classes("w-full p-4 rounded-xl gap-1")
        .style("background: rgba(245, 158, 11, 0.10); border: 1px solid rgba(245, 158, 11, 0.35);")
    ):
        with ui.row().classes("items-center gap-2"):
            ui.icon("warning_amber", size="18px").style(f"color: {StudioColor.WARNING}")
            ui.label(
                f"Block '{block.block_id}' ({block.original_block_type}: {block.reference_id}) "
                f"— {block.code}"
            ).classes("text-sm font-semibold").style(f"color: {StudioColor.TEXT}")
        ui.label(block.message).classes("text-xs").style(f"color: {StudioColor.TEXT}")
        if block.remediation:
            ui.label(f"Remediation: {block.remediation}").classes("text-xs").style(
                f"color: {StudioColor.MUTED}"
            )


def _render_single_block(
    block: RenderedBlock,
    on_export_image: Callable[[RenderedChartBlock, str], Any] | None = None,
) -> None:
    """Dispatch one `RenderedBlock` to its corresponding NiceGUI component."""
    if isinstance(block, RenderedTextBlock):
        if block.block_type == "title":
            with ui.column().classes("w-full gap-1"):
                ui.label(block.heading).classes("text-2xl font-bold").style(
                    f"color: {StudioColor.TEXT}"
                )
                if block.body:
                    ui.label(block.body).classes("text-sm").style(f"color: {StudioColor.MUTED}")
        elif block.block_type == "subtitle":
            ui.label(block.heading).classes("text-lg font-semibold mt-2").style(
                f"color: {StudioColor.TEXT}"
            )
        else:
            with ui.card().classes("studio-card w-full p-4 rounded-xl gap-1"):
                if block.heading:
                    ui.label(block.heading).classes("text-sm font-semibold").style(
                        f"color: {StudioColor.TEXT}"
                    )
                ui.label(block.body).classes("text-sm").style(f"color: {StudioColor.MUTED}")
    elif isinstance(block, RenderedProvenanceBlock):
        _render_provenance_block(block)
    elif isinstance(block, RenderedKPIGroupBlock):
        _render_kpi_group_block(block)
    elif isinstance(block, RenderedChartBlock):
        _render_chart_block(block, on_export_image=on_export_image)
    elif isinstance(block, RenderedTableBlock):
        _render_table_block(block)
    elif isinstance(block, RenderedStructuralBlock):
        if block.block_type == "divider":
            ui.separator().classes("my-2")
            if block.label:
                ui.label(block.label).classes(
                    "text-xs font-semibold uppercase tracking-wider"
                ).style(f"color: {StudioColor.MUTED}")
        else:
            with ui.row().classes("w-full items-center gap-2 my-1"):
                ui.separator().classes("flex-grow")
                status_badge("Page Break", tone=BadgeTone.NEUTRAL, outline=True)
                ui.separator().classes("flex-grow")
    else:
        _render_broken_block(block)


def _render_report_result(
    result: ReportResult,
    on_export_image: Callable[[RenderedChartBlock, str], Any] | None = None,
) -> None:
    """Render a complete `ReportResult` inside the preview pane."""
    with ui.row().classes("w-full items-center justify-between flex-wrap gap-2 mb-2"):
        status_badge(
            f"Rendered {len(result.blocks)} blocks in {result.duration_ms:.1f} ms",
            tone=BadgeTone.SUCCESS,
            outline=True,
        )
        if result.warnings:
            status_badge(
                f"{len(result.warnings)} warning(s)",
                tone=BadgeTone.WARNING,
            )

    with ui.column().classes("w-full gap-4"):
        for block in result.blocks:
            _render_single_block(block, on_export_image=on_export_image)


_GALLERY_PAGE_SIZE = 6


def _render_gallery_pagination(
    total_items: int,
    page_state: dict[str, int],
    key: str,
    on_change: Callable[[], Any],
) -> tuple[int, int]:
    """Render compact pagination controls and return `(start_idx, end_idx)` slice."""
    total_pages = max(1, (total_items + _GALLERY_PAGE_SIZE - 1) // _GALLERY_PAGE_SIZE)
    current = min(max(1, page_state[key]), total_pages)
    page_state[key] = current
    start = (current - 1) * _GALLERY_PAGE_SIZE
    end = start + _GALLERY_PAGE_SIZE

    if total_pages > 1:
        with ui.row().classes("w-full items-center justify-between pt-2"):
            ui.label(
                f"Showing {start + 1}–{min(end, total_items)} of {total_items} reports"
            ).classes("text-xs").style(f"color: {StudioColor.MUTED}")
            with ui.row().classes("items-center gap-2"):
                prev_btn = ui.button(
                    icon="chevron_left",
                    on_click=lambda _e: (
                        page_state.update({key: max(1, current - 1)}),
                        on_change(),
                    ),
                ).props("outline dense size=sm")
                if current <= 1:
                    prev_btn.disable()
                ui.label(f"Page {current} / {total_pages}").classes("text-xs font-medium").style(
                    f"color: {StudioColor.TEXT}"
                )
                next_btn = ui.button(
                    icon="chevron_right",
                    on_click=lambda _e: (
                        page_state.update({key: min(total_pages, current + 1)}),
                        on_change(),
                    ),
                ).props("outline dense size=sm")
                if current >= total_pages:
                    next_btn.disable()
    return start, end


def _render_catalog_cards(
    builtins: tuple[ReportDefinition, ...],
    saved_reports: list[SavedReportRecord],
    on_preview: Callable[[ReportDefinition], Any],
    selected_id_ref: dict[str, ReportDefinition],
    *,
    last_runs_by_report: dict[str, ReportRunRecord],
    on_new_custom: Callable[[], Any],
    on_clone_builtin: Callable[[ReportDefinition], Any],
    on_edit_custom: Callable[[SavedReportRecord], Any],
    on_rename_custom: Callable[[SavedReportRecord], Any],
    on_duplicate_custom: Callable[[SavedReportRecord], Any],
    on_delete_custom: Callable[[SavedReportRecord], Any],
    on_rerun_latest_custom: Callable[[SavedReportRecord], Any],
) -> Callable[[], Any]:
    """Render a scalable, searchable, paginated 3-column Report Gallery."""
    search_state: dict[str, str] = {"q": ""}
    page_state: dict[str, int] = {"builtin": 1, "custom": 1}
    clone_ref: dict[str, str] = {"id": builtins[0].id if builtins else ""}
    builtin_by_id = {b.id: b for b in builtins}

    with ui.card().classes("studio-card w-full p-4 rounded-xl gap-3"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
            with ui.tabs().props("dense no-caps active-color=primary") as catalog_tabs:
                tab_builtin = ui.tab("builtin", label=f"Built-in Templates ({len(builtins)})")
                tab_custom = ui.tab("custom", label=f"Custom Reports ({len(saved_reports)})")
            ui.input(
                placeholder="Search reports…",
                on_change=lambda e: (
                    search_state.update({"q": str(e.value or "").strip().lower()}),
                    page_state.update({"builtin": 1, "custom": 1}),
                    _gallery_grid.refresh(),
                ),
            ).props("dense outlined clearable debounce=200").classes("w-64")

        @ui.refreshable
        def _gallery_grid() -> None:
            query = search_state["q"]
            with ui.tab_panels(catalog_tabs, value=tab_builtin).classes("w-full bg-transparent"):
                with ui.tab_panel(tab_builtin).classes("p-0 pt-2"):
                    filtered = [
                        t
                        for t in builtins
                        if not query or query in t.name.lower() or query in t.description.lower()
                    ]
                    if not filtered:
                        ui.label("No built-in templates match your search.").classes(
                            "text-sm"
                        ).style(f"color: {StudioColor.MUTED}")
                    else:
                        start, end = _render_gallery_pagination(
                            len(filtered), page_state, "builtin", _gallery_grid.refresh
                        )
                        with ui.grid(columns=3).classes("w-full gap-3 pt-2"):
                            for tmpl in filtered[start:end]:
                                is_active = selected_id_ref["current"].id == tmpl.id
                                border = (
                                    "1px solid #3B82F6"
                                    if is_active
                                    else "1px solid rgba(148, 163, 184, 0.2)"
                                )
                                with (
                                    ui.card()
                                    .classes("studio-card p-3 rounded-xl justify-between gap-2")
                                    .style(f"border: {border};")
                                ):
                                    with ui.column().classes("gap-1"):
                                        with ui.row().classes(
                                            "w-full items-start justify-between gap-2"
                                        ):
                                            ui.label(tmpl.name).classes(
                                                "text-sm font-semibold leading-snug"
                                            ).style(f"color: {StudioColor.TEXT}")
                                            status_badge(
                                                preset_display_name(tmpl.default_period.preset),
                                                tone=BadgeTone.INFO
                                                if is_active
                                                else BadgeTone.NEUTRAL,
                                                outline=True,
                                            )
                                        ui.label(tmpl.description).classes(
                                            "text-xs line-clamp-2"
                                        ).style(f"color: {StudioColor.MUTED}")
                                    with ui.row().classes("w-full justify-end gap-1 pt-1"):
                                        ui.button(
                                            "Clone",
                                            icon="content_copy",
                                            on_click=lambda _e, d=tmpl: on_clone_builtin(d),
                                        ).props("outline dense size=sm")
                                        ui.button(
                                            "Preview",
                                            icon="play_arrow",
                                            on_click=lambda _e, d=tmpl: on_preview(d),
                                        ).props("color=primary dense size=sm")

                with ui.tab_panel(tab_custom).classes("p-0 pt-2"):
                    with ui.row().classes(
                        "w-full items-center justify-between flex-wrap gap-2 pb-2"
                    ):
                        ui.button(
                            "New Custom Report",
                            icon="add",
                            on_click=lambda _e: on_new_custom(),
                        ).props("color=primary dense size=sm")
                        if builtins:
                            with ui.row().classes("items-center gap-2"):
                                ui.select(
                                    options={b.id: b.name for b in builtins},
                                    value=clone_ref["id"],
                                    label="Clone Built-in",
                                    on_change=lambda e: clone_ref.update(
                                        {"id": str(e.value or "")}
                                    ),
                                ).props("dense outlined").classes("w-60")
                                ui.button(
                                    "Clone",
                                    icon="content_copy",
                                    on_click=lambda _e: (
                                        on_clone_builtin(builtin_by_id[clone_ref["id"]])
                                        if clone_ref["id"] in builtin_by_id
                                        else None
                                    ),
                                ).props("outline dense size=sm")

                    filtered_custom = [
                        r
                        for r in saved_reports
                        if not query or query in r.name.lower() or query in r.description.lower()
                    ]
                    if not filtered_custom:
                        ui.label(
                            "No custom report definitions saved yet. Click 'New Custom Report' "
                            "or clone any Built-in Template to compose your own."
                        ).classes("text-xs").style(f"color: {StudioColor.MUTED}")
                    else:
                        c_start, c_end = _render_gallery_pagination(
                            len(filtered_custom), page_state, "custom", _gallery_grid.refresh
                        )
                        with ui.grid(columns=3).classes("w-full gap-3 pt-2"):
                            for rec in filtered_custom[c_start:c_end]:
                                with ui.card().classes(
                                    "studio-card p-3 rounded-xl justify-between gap-2"
                                ):
                                    with ui.column().classes("gap-1"):
                                        ui.label(rec.name).classes("text-sm font-semibold").style(
                                            f"color: {StudioColor.TEXT}"
                                        )
                                        if rec.description:
                                            ui.label(rec.description).classes(
                                                "text-xs line-clamp-2"
                                            ).style(f"color: {StudioColor.MUTED}")
                                        last_run = last_runs_by_report.get(rec.report_id)
                                        if last_run is not None:
                                            ui.label(
                                                f"Last: {last_run.output_format.upper()} · "
                                                f"{last_run.pfetl_run_id[:10]} · "
                                                f"{last_run.generated_at.strftime('%Y-%m-%d %H:%M')}"
                                            ).classes("text-xs").style(
                                                f"color: {StudioColor.MUTED}"
                                            )
                                    with ui.row().classes(
                                        "w-full justify-end flex-wrap gap-1 pt-1"
                                    ):
                                        ui.button(
                                            "Preview",
                                            icon="play_arrow",
                                            on_click=lambda _e, r=rec: on_preview(
                                                ReportDefinition.model_validate_json(
                                                    r.definition_json
                                                )
                                            ),
                                        ).props("color=primary dense size=sm")
                                        ui.button(
                                            icon="refresh",
                                            on_click=lambda _e, r=rec: on_rerun_latest_custom(r),
                                        ).props("outline dense size=sm").tooltip(
                                            "Rerun on Latest Successful Run"
                                        )
                                        ui.button(
                                            "Edit",
                                            icon="edit",
                                            on_click=lambda _e, r=rec: on_edit_custom(r),
                                        ).props("outline dense size=sm")
                                        ui.button(
                                            icon="drive_file_rename_outline",
                                            on_click=lambda _e, r=rec: on_rename_custom(r),
                                        ).props("flat dense size=sm").tooltip("Rename")
                                        ui.button(
                                            icon="content_copy",
                                            on_click=lambda _e, r=rec: on_duplicate_custom(r),
                                        ).props("flat dense size=sm").tooltip("Duplicate")
                                        ui.button(
                                            icon="delete_outline",
                                            on_click=lambda _e, r=rec: on_delete_custom(r),
                                        ).props("flat dense size=sm color=negative").tooltip(
                                            "Delete"
                                        )

        _gallery_grid()
    return _gallery_grid.refresh


def _render_history_table(run_history: list[ReportRunRecord]) -> None:
    """Render `report_runs` execution and export history table."""
    history_rows: list[dict[str, Any]] = [
        {
            "Generated At": rec.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
            "Report ID": rec.report_id,
            "PFETL Run ID": rec.pfetl_run_id,
            "Format": rec.output_format.upper(),
            "Output Path": rec.output_path or "(in-memory preview)",
        }
        for rec in run_history
    ]
    history_cols = [
        TableColumn(name="Generated At", label="Generated At", field="Generated At"),
        TableColumn(name="Report ID", label="Report ID", field="Report ID"),
        TableColumn(name="PFETL Run ID", label="PFETL Run ID", field="PFETL Run ID"),
        TableColumn(name="Format", label="Format", field="Format"),
        TableColumn(name="Output Path", label="Output Path", field="Output Path"),
    ]
    bounded_data_table(
        columns=history_cols,
        rows=history_rows,
        title="Report Run History (Studio report_runs)",
        max_rows=25,
        export_filename="report_run_history.csv",
    )


def _render_reports_header_and_guards(context: AppContext) -> bool:
    """Render Reports header and standard profile/lock/compatibility guards."""
    route = ROUTES_BY_ID["reports"]
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
                ui.label(route.description).classes("text-sm").style(f"color: {StudioColor.MUTED}")

    if context.profile is None:
        empty_state_panel(
            title="Select a PFETL Profile to Explore Reports",
            message=(
                "No PFETL Settings / FinancialRules pair is active. Use 'Open Profile' "
                "in the top bar or launch with `--settings` and `--rules`."
            ),
            icon="folder_open",
            hint="Reports bind deterministically to one PFETL run and its snapshots.",
            action_label="Go to Overview Profile Picker",
            action_target="/overview",
        )
        return False

    if context.is_warehouse_locked:
        empty_state_panel(
            title="Reports Paused While PFETL Lock Is Active",
            message=(
                "PFETL Core holds `pipeline.lock` on the DuckDB warehouse. Studio defers "
                "report execution until Core finishes writing cleanly."
            ),
            icon="lock",
            hint="Control Plane run telemetry and logs remain available under Operations.",
            action_label="Inspect Operations & Runs",
            action_target="/operations",
        )
        return False

    if context.is_incompatible and context.compatibility:
        error_state_panel(
            title="Reports Blocked by Compatibility Check",
            message=(
                "Required upstream PFETL surfaces or columns did not match expected "
                "catalog contracts."
            ),
            details=list(context.compatibility.diagnostics[:6]),
        )
        return False

    return True


def _temp_export_path(filename: str) -> Path:
    """Resolve a temporary local export path prior to browser download."""
    return create_temp_export_path(filename)


def _load_reports_initial_state(
    context: AppContext, state_db_path: Path | None
) -> tuple[list[SavedReportRecord], list[SavedViewDefinition], list[ReportRunRecord]]:
    """Load saved custom reports, saved views, and recent report runs from `studio.sqlite`."""
    saved_reports: list[SavedReportRecord] = []
    saved_views: list[SavedViewDefinition] = []
    run_history: list[ReportRunRecord] = []
    with StudioStateStore(db_path=state_db_path) as store:
        if context.profile_id:
            saved_reports = store.list_report_definitions(context.profile_id)
            saved_views = store.list_saved_views(context.profile_id)
        run_history = store.list_report_runs(limit=25)
    return saved_reports, saved_views, run_history


def _load_available_run_options(
    context: AppContext,
    cache: QueryCache,
) -> tuple[dict[str, str], str | None]:
    """Load PFETL run options for preview/rerun selector and latest successful run ID."""
    options: dict[str, str] = {"active": "Current Active Run"}
    latest_success_id: str | None = None
    if context.profile is None:
        return options, None
    ops = OperationsAnalytics.run_history(
        context.profile,
        RunHistoryFilter(limit=25),
        catalog_snapshot=context.catalog_snapshot,
        cache=cache,
    )
    for rec in ops.runs:
        if latest_success_id is None and rec.status.upper() == "SUCCESS":
            latest_success_id = rec.run_id
        stamp = rec.started_at_raw[:16] if rec.started_at_raw else ""
        options[rec.run_id] = f"{rec.run_id[:10]} · {rec.status} {stamp}".strip()
    if latest_success_id:
        options = {
            "active": "Current Active Run",
            "latest_success": f"Latest Success ({latest_success_id[:10]})",
            **{k: v for k, v in options.items() if k != "active"},
        }
    return options, latest_success_id


def _build_preview_overrides(
    active_req: PeriodRequest | None,
    sel_run_key: str,
    latest_success_id: str | None,
    context: AppContext,
) -> ReportContextOverrides | None:
    """Build `ReportContextOverrides` combining active `PeriodRequest` and PFETL run."""
    run_id_override: str | None = None
    if sel_run_key == "latest_success":
        run_id_override = latest_success_id
    elif sel_run_key != "active":
        run_id_override = sel_run_key

    if active_req is None and not context.filters.filters and run_id_override is None:
        return None
    return ReportContextOverrides(
        pfetl_run_id=run_id_override,
        period_request=active_req,
        filters=context.filters,
    )


def _render_provenance_changes_banner(changes: tuple[ProvenanceChange, ...]) -> None:
    """Render compact diff strip highlighting provenance changes since the prior run."""
    if not changes:
        return
    with (
        ui.card()
        .classes("studio-card w-full p-3 rounded-xl gap-1")
        .style("border: 1px solid rgba(59, 130, 246, 0.35);")
    ):
        ui.label("Provenance Changes Since Prior Run").classes(
            "text-xs font-semibold uppercase tracking-wider"
        ).style(f"color: {StudioColor.ACCENT}")
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            for ch in changes:
                ui.label(f"{ch.label}: {ch.before[:16]} → {ch.after[:16]}").classes(
                    "text-xs font-mono"
                ).style(f"color: {StudioColor.TEXT}")


def _run_report_export_sync(
    result: ReportResult,
    fmt: str,
    target: Path,
    state_db_path: Path | None,
) -> tuple[ExportResult, str]:
    """Execute synchronous PDF, PPTX, or XLSX export and return `(ExportResult, media_type)`."""
    if fmt == "pdf":
        return (
            ReportExporter.export_pdf(
                result,
                target,
                record_history=True,
                state_db_path=state_db_path,
            ),
            "application/pdf",
        )
    if fmt == "pptx":
        return (
            ReportExporter.export_pptx(
                result,
                target,
                record_history=True,
                state_db_path=state_db_path,
            ),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    return (
        ReportExporter.export_xlsx(
            result,
            target,
            record_history=True,
            state_db_path=state_db_path,
        ),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def render_reports_page(  # pylint: disable=too-many-statements
    context: AppContext,
    *,
    cache: QueryCache | None = None,
    state_db_path: Path | None = None,
) -> None:
    """Render the Reports workspace from shared `AppContext`."""
    active_cache = cache if cache is not None else _REPORTS_CACHE
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY, cache=active_cache)

    with ui.column().classes("w-full gap-4"):
        if not _render_reports_header_and_guards(context):
            return

        builtins = DEFAULT_REPORT_REGISTRY.list_definitions(builtin_only=True)
        selected_def: dict[str, ReportDefinition] = {
            "current": builtins[0]
            if builtins
            else ReportDefinition(id="empty", name="No Template Registered")
        }
        rendered_state: dict[str, ReportResult | None] = {"result": None}
        error_state: dict[str, str | None] = {"error": None}

        saved_reports, saved_views, run_history = _load_reports_initial_state(
            context, state_db_path
        )
        run_options_state: dict[str, Any] = {
            "options": {"active": "Current Active Run"},
            "latest_success": None,
        }
        try:
            asyncio.get_running_loop()
            _has_loop = True
        except RuntimeError:
            _has_loop = False
        if not _has_loop:
            opts, latest_id = _load_available_run_options(context, active_cache)
            run_options_state["options"] = opts
            run_options_state["latest_success"] = latest_id
        last_runs_by_report: dict[str, ReportRunRecord] = {}
        for run_rec in run_history:
            last_runs_by_report.setdefault(run_rec.report_id, run_rec)

        editor_state: dict[str, ReportDefinitionDraft | None] = {"draft": None}
        refresh_gallery_ref: dict[str, Callable[[], Any] | None] = {"fn": None}

        period_override_state: dict[str, Any] = {
            "run_key": "active",
            "period_req": context.period_request or selected_def["current"].default_period,
        }
        prior_context_by_def: dict[str, ReportContext] = {}
        provenance_changes_state: dict[str, tuple[ProvenanceChange, ...]] = {"items": ()}
        export_progress_state: dict[str, str | bool] = {"active": False, "label": ""}

        @ui.refreshable
        def _export_status_strip() -> None:
            if not bool(export_progress_state["active"]):
                return
            with (
                ui.row()
                .classes("items-center gap-2 px-3 py-1 rounded-lg")
                .style(
                    "background: rgba(59, 130, 246, 0.14); border: 1px solid rgba(59, 130, 246, 0.3);"
                )
            ):
                ui.spinner(size="sm", color="primary")
                ui.label(str(export_progress_state["label"])).classes("text-xs font-medium").style(
                    f"color: {StudioColor.TEXT}"
                )

        export_buttons: list[ui.button] = []

        def _set_exporting(active: bool, label: str = "") -> None:
            export_progress_state["active"] = active
            export_progress_state["label"] = label
            for btn in export_buttons:
                if active:
                    btn.disable()
                else:
                    btn.enable()
            _export_status_strip.refresh()

        async def _export_chart_asset(block: RenderedChartBlock, fmt: str) -> None:
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            stem = sanitize_filename(f"{block.block_id}_{stamp}")
            target = _temp_export_path(f"{stem}.{fmt}")
            _set_exporting(True, f"Rendering {fmt.upper()} chart…")
            try:
                fn = (
                    ReportExporter.export_chart_png
                    if fmt == "png"
                    else ReportExporter.export_chart_svg
                )
                res = await run.io_bound(fn, block.figure_dict, target)
                media = "image/png" if fmt == "png" else "image/svg+xml"
                trigger_file_download(res.path, filename=res.path.name, media_type=media)
                ui.notify(
                    f"Downloading {res.format.upper()} chart ({res.bytes_written:,} bytes)…",
                    type="positive",
                )
            except ExportPrerequisiteError as exc:
                ui.notify(str(exc), type="negative")
            finally:
                _set_exporting(False)

        async def _export_report(fmt: str) -> None:
            result = rendered_state["result"]
            if result is None:
                ui.notify("Run Deterministic Preview before exporting.", type="warning")
                return
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            stem = sanitize_filename(f"{result.definition_id}_{stamp}")
            target = _temp_export_path(f"{stem}.{fmt}")
            _set_exporting(True, f"Generating {fmt.upper()} report (may take a few seconds)…")
            try:
                export_res, media = await run.io_bound(
                    _run_report_export_sync,
                    result,
                    fmt,
                    target,
                    state_db_path,
                )
                with StudioStateStore(db_path=state_db_path) as store:
                    run_history[:] = store.list_report_runs(limit=25)
                _history_container.refresh()
                trigger_file_download(
                    export_res.path,
                    filename=export_res.path.name,
                    media_type=media,
                )
                msg = (
                    f"Downloading {export_res.format.upper()} ({export_res.bytes_written:,} bytes)…"
                )
                if export_res.warnings:
                    ui.notify(f"{msg} (warnings: {'; '.join(export_res.warnings)})", type="warning")
                else:
                    ui.notify(msg, type="positive")
            except ExportPrerequisiteError as exc:
                ui.notify(str(exc), type="negative")
            finally:
                _set_exporting(False)

        @ui.refreshable
        def _preview_container() -> None:
            if error_state["error"]:
                error_state_panel(
                    title="Report Preview Error",
                    message=error_state["error"],
                )
                return
            result = rendered_state["result"]
            if result is None:
                empty_state_panel(
                    title=f"Ready to Preview: {selected_def['current'].name}",
                    message=(
                        "Click 'Run Deterministic Preview' to execute all blocks against "
                        "the active PFETL run and render exporter-neutral outputs."
                    ),
                    icon="play_circle_outline",
                )
                return
            _render_provenance_changes_banner(provenance_changes_state["items"])
            _render_report_result(result, on_export_image=_export_chart_asset)

        @ui.refreshable
        def _history_container() -> None:
            _render_history_table(run_history)

        async def _run_preview(defn: ReportDefinition) -> None:
            selected_def["current"] = defn
            error_state["error"] = None
            req_val = period_override_state["period_req"]
            active_req = req_val if isinstance(req_val, PeriodRequest) else None
            overrides = _build_preview_overrides(
                active_req,
                str(period_override_state["run_key"]),
                run_options_state["latest_success"],
                context,
            )
            _set_exporting(True, f"Rendering {defn.name} preview…")
            try:
                result = await run.io_bound(
                    service.render,
                    defn,
                    context,
                    context_overrides=overrides,
                    record_history=True,
                    state_db_path=state_db_path,
                )
                prior_ctx = prior_context_by_def.get(defn.id)
                provenance_changes_state["items"] = (
                    compare_report_provenance(prior_ctx, result.context)
                    if prior_ctx is not None
                    else ()
                )
                prior_context_by_def[defn.id] = result.context
                rendered_state["result"] = result
                with StudioStateStore(db_path=state_db_path) as store:
                    run_history[:] = store.list_report_runs(limit=25)
                last_runs_by_report.clear()
                for run_rec in run_history:
                    last_runs_by_report.setdefault(run_rec.report_id, run_rec)
                refresh_fn = refresh_gallery_ref["fn"]
                if refresh_fn is not None:
                    refresh_fn()
            except Exception as exc:  # noqa: BLE001
                error_state["error"] = str(exc)
            finally:
                _set_exporting(False)
            _configure_period_row.refresh()
            _preview_container.refresh()
            _history_container.refresh()

        @ui.refreshable
        def _configure_period_row() -> None:
            def _on_req_change(new_req: PeriodRequest) -> None:
                period_override_state["period_req"] = new_req
                _configure_period_row.refresh()
                if rendered_state["result"] is not None:
                    try:
                        asyncio.get_running_loop().create_task(
                            _run_preview(selected_def["current"])
                        )
                    except RuntimeError:
                        pass

            def _sync_global() -> None:
                _on_req_change(context.period_request or PeriodRequest())

            def _reset_template_default() -> None:
                _on_req_change(selected_def["current"].default_period)

            cur_res = rendered_state["result"]
            req_val = period_override_state["period_req"]
            active_req = req_val if isinstance(req_val, PeriodRequest) else PeriodRequest()
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                period_selector(
                    current_request=active_req,
                    on_request_change=_on_req_change,
                    period_options=context.period_options,
                    period_context=cur_res.context.period if cur_res is not None else None,
                )
                with ui.row().classes("items-center gap-1.5"):
                    ui.button(
                        "Sync Global",
                        icon="public",
                        on_click=_sync_global,
                    ).props("flat dense size=sm").tooltip(
                        "Apply the global top-bar period & comparison selection"
                    )
                    ui.button(
                        "Template Default",
                        icon="restart_alt",
                        on_click=_reset_template_default,
                    ).props("flat dense size=sm").tooltip(
                        "Reset to the selected report template's default period"
                    )

        async def _on_run_change(new_run_key: str) -> None:
            period_override_state["run_key"] = new_run_key
            if rendered_state["result"] is not None:
                await _run_preview(selected_def["current"])

        async def _rerun_latest_custom(rec: SavedReportRecord) -> None:
            if run_options_state["latest_success"]:
                period_override_state["run_key"] = "latest_success"
            await _run_preview(ReportDefinition.model_validate_json(rec.definition_json))

        def _reload_saved_reports() -> None:
            if not context.profile_id:
                return
            with StudioStateStore(db_path=state_db_path) as store:
                saved_reports[:] = store.list_report_definitions(context.profile_id)
                saved_views[:] = store.list_saved_views(context.profile_id)
            refresh_fn = refresh_gallery_ref["fn"]
            if refresh_fn is not None:
                refresh_fn()

        def _open_new_custom() -> None:
            existing_names = {r.name for r in saved_reports}
            draft = clone_definition_as_draft(
                ReportDefinition(id="new", name="Custom Report", blocks=()),
                existing_names,
                suffix="1",
            )
            draft.name = (
                "Custom Report"
                if "custom report" not in {n.lower() for n in existing_names}
                else draft.name
            )
            editor_state["draft"] = draft
            _editor_container.refresh()

        def _open_clone(defn: ReportDefinition) -> None:
            existing_names = {r.name for r in saved_reports}
            editor_state["draft"] = clone_definition_as_draft(defn, existing_names, suffix="Copy")
            _editor_container.refresh()

        def _open_edit_custom(rec: SavedReportRecord) -> None:
            defn = ReportDefinition.model_validate_json(rec.definition_json)
            editor_state["draft"] = ReportDefinitionDraft.from_definition(defn)
            _editor_container.refresh()

        def _save_draft(draft: ReportDefinitionDraft) -> None:
            if not context.profile_id:
                ui.notify("Select a PFETL profile before saving.", type="warning")
                return
            defn = draft.to_definition()
            try:
                with StudioStateStore(db_path=state_db_path) as store:
                    store.save_report_definition(
                        context.profile_id,
                        defn.name,
                        defn.model_dump_json(),
                        report_id=defn.id,
                        description=defn.description,
                    )
                _reload_saved_reports()
                editor_state["draft"] = None
                _editor_container.refresh()
                ui.notify(f"Saved custom report '{defn.name}'.", type="positive")
            except ValueError as exc:
                ui.notify(str(exc), type="negative")

        def _rename_custom(report_id: str, new_name: str) -> None:
            try:
                with StudioStateStore(db_path=state_db_path) as store:
                    store.rename_report_definition(report_id, new_name)
                _reload_saved_reports()
                ui.notify(f"Renamed report to '{new_name.strip()}'.", type="positive")
            except ValueError as exc:
                ui.notify(str(exc), type="negative")

        def _duplicate_custom(rec: SavedReportRecord) -> None:
            if not context.profile_id:
                return
            defn = ReportDefinition.model_validate_json(rec.definition_json)
            dup = clone_definition_as_draft(
                defn, {r.name for r in saved_reports}, suffix="Copy"
            ).to_definition()
            with StudioStateStore(db_path=state_db_path) as store:
                store.save_report_definition(
                    context.profile_id,
                    dup.name,
                    dup.model_dump_json(),
                    report_id=dup.id,
                    description=dup.description,
                )
            _reload_saved_reports()
            ui.notify(f"Duplicated as '{dup.name}'.", type="positive")

        def _delete_custom(report_id: str) -> None:
            with StudioStateStore(db_path=state_db_path) as store:
                store.delete_report_definition(report_id)
            _reload_saved_reports()
            if editor_state["draft"] is not None and editor_state["draft"].id == report_id:
                editor_state["draft"] = None
                _editor_container.refresh()
            ui.notify("Deleted custom report definition.", type="info")

        @ui.refreshable
        def _editor_container() -> None:
            draft = editor_state["draft"]
            if draft is None:
                return
            render_custom_editor_panel(
                draft,
                saved_reports=saved_reports,
                saved_views=saved_views,
                on_save=_save_draft,
                on_preview=_run_preview,
                on_close=lambda: (
                    editor_state.update({"draft": None}),
                    _editor_container.refresh(),
                ),
            )

        refresh_gallery_ref["fn"] = _render_catalog_cards(
            builtins,
            saved_reports,
            _run_preview,
            selected_def,
            last_runs_by_report=last_runs_by_report,
            on_new_custom=_open_new_custom,
            on_clone_builtin=_open_clone,
            on_edit_custom=_open_edit_custom,
            on_rename_custom=lambda r: open_rename_report_dialog(r, _rename_custom),
            on_duplicate_custom=_duplicate_custom,
            on_delete_custom=lambda r: open_delete_report_dialog(r, _delete_custom),
            on_rerun_latest_custom=_rerun_latest_custom,
        )
        _editor_container()

        with ui.card().classes("studio-card w-full p-4 rounded-xl gap-3"):
            static_ok, static_msg = check_static_export_capability()
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
                with ui.row().classes("items-center gap-2"):
                    status_badge("Configure · Preview · Export", tone=BadgeTone.INFO, outline=True)
                    ui.label("Deterministic Preview & Export").classes(
                        "text-base font-semibold"
                    ).style(f"color: {StudioColor.TEXT}")
                    if not static_ok:
                        status_badge(
                            "Static Chart Engine Unavailable",
                            tone=BadgeTone.WARNING,
                            outline=True,
                        ).tooltip(static_msg)
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    run_sel = (
                        ui.select(
                            options=run_options_state["options"],
                            value=period_override_state["run_key"],
                            label="PFETL Run",
                            on_change=lambda e: _on_run_change(str(e.value or "active")),
                        )
                        .props("dense outlined")
                        .classes("w-56")
                    )
                    if _has_loop:

                        async def _load_run_options_async() -> None:
                            opts, latest_id = await run.io_bound(
                                _load_available_run_options, context, active_cache
                            )
                            run_options_state["options"] = opts
                            run_options_state["latest_success"] = latest_id
                            run_sel.options = opts
                            run_sel.update()

                        ui.timer(0.01, _load_run_options_async, once=True)
                    ui.button(
                        "Rerun Latest",
                        icon="refresh",
                        on_click=lambda _e: _on_run_change("latest_success"),
                    ).props("outline dense")
                    pdf_btn = ui.button(
                        "Export PDF",
                        icon="picture_as_pdf",
                        on_click=lambda _e: _export_report("pdf"),
                    ).props("outline dense")
                    pptx_btn = ui.button(
                        "Export PPTX",
                        icon="slideshow",
                        on_click=lambda _e: _export_report("pptx"),
                    ).props("outline dense")
                    xlsx_btn = ui.button(
                        "Export XLSX",
                        icon="table_view",
                        on_click=lambda _e: _export_report("xlsx"),
                    ).props("outline dense")
                    export_buttons.extend([pdf_btn, pptx_btn, xlsx_btn])
            _configure_period_row()
            _export_status_strip()
            _preview_container()

        _history_container()
