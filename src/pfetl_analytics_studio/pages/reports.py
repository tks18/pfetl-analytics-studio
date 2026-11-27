"""Reports page module — deterministic report runtime preview, catalog, and run history."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nicegui import run, ui

from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.reports import (
    DEFAULT_REPORT_REGISTRY,
    ExportPrerequisiteError,
    ExportResult,
    RenderedBlock,
    RenderedBrokenBlock,
    RenderedChartBlock,
    RenderedKPIGroupBlock,
    RenderedProvenanceBlock,
    RenderedStructuralBlock,
    RenderedTableBlock,
    RenderedTextBlock,
    ReportDefinition,
    ReportExporter,
    ReportResult,
    ReportService,
    sanitize_filename,
)
from pfetl_analytics_studio.state import ReportRunRecord, SavedReportRecord, StudioStateStore
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    TableColumn,
    bounded_data_table,
    empty_state_panel,
    error_state_panel,
    status_badge,
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


def _render_kpi_group_block(block: RenderedKPIGroupBlock) -> None:
    """Render a `RenderedKPIGroupBlock` as a responsive grid of KPI cards."""
    with ui.column().classes("w-full gap-2"):
        ui.label(block.title).classes("text-base font-semibold").style(f"color: {StudioColor.TEXT}")
        with ui.grid(columns=4).classes("w-full gap-3"):
            for item in block.items:
                with ui.card().classes("studio-card p-3 rounded-xl gap-1"):
                    ui.label(item.label).classes("text-xs font-medium").style(
                        f"color: {StudioColor.MUTED}"
                    )
                    ui.label(item.formatted_value).classes("text-xl font-bold").style(
                        f"color: {StudioColor.TEXT}"
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


def _render_catalog_cards(
    builtins: tuple[ReportDefinition, ...],
    saved_reports: list[SavedReportRecord],
    on_preview: Callable[[ReportDefinition], Any],
) -> None:
    """Render Built-in Templates and Custom Reports catalog cards."""
    with ui.row().classes("w-full gap-4 flex-wrap"):
        with ui.card().classes("studio-card flex-1 min-w-[300px] p-4 rounded-xl gap-2"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Built-in Templates").classes("text-base font-semibold").style(
                    f"color: {StudioColor.TEXT}"
                )
                status_badge(
                    f"{len(builtins)} Runtime Demo",
                    tone=BadgeTone.INFO,
                    outline=True,
                )
            ui.label(
                "Phases 26–30 populate the 15 prebuilt financial, tax, and operational reports."
            ).classes("text-xs").style(f"color: {StudioColor.MUTED}")
            for tmpl in builtins:
                with ui.row().classes("w-full items-center justify-between gap-2 pt-2"):
                    with ui.column().classes("gap-0"):
                        ui.label(tmpl.name).classes("text-sm font-medium").style(
                            f"color: {StudioColor.TEXT}"
                        )
                        ui.label(tmpl.description).classes("text-xs").style(
                            f"color: {StudioColor.MUTED}"
                        )
                    ui.button(
                        "Run Deterministic Preview",
                        icon="play_arrow",
                        on_click=lambda _e, d=tmpl: on_preview(d),
                    ).props("color=primary dense")

        with ui.card().classes("studio-card flex-1 min-w-[300px] p-4 rounded-xl gap-2"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Custom Reports").classes("text-base font-semibold").style(
                    f"color: {StudioColor.TEXT}"
                )
                status_badge(
                    f"{len(saved_reports)} Saved",
                    tone=BadgeTone.NEUTRAL,
                    outline=True,
                )
            if not saved_reports:
                ui.label(
                    "No custom report definitions saved yet. Phases 31–32 add the "
                    "interactive Clone & Custom Report Builder."
                ).classes("text-xs").style(f"color: {StudioColor.MUTED}")
            else:
                for rec in saved_reports:
                    with ui.row().classes("w-full items-center justify-between gap-2 pt-1"):
                        ui.label(rec.name).classes("text-sm font-medium").style(
                            f"color: {StudioColor.TEXT}"
                        )
                        ui.button(
                            "Preview",
                            icon="play_arrow",
                            on_click=lambda _e, r=rec: on_preview(
                                ReportDefinition.model_validate_json(r.definition_json)
                            ),
                        ).props("outline dense")


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


def render_reports_page(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
    state_db_path: Path | None = None,
) -> None:
    """Render the Reports workspace from shared `AppContext`."""
    route = ROUTES_BY_ID["reports"]
    active_cache = cache if cache is not None else _REPORTS_CACHE
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY, cache=active_cache)

    with ui.column().classes("w-full gap-4"):
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
                    ui.label(route.description).classes("text-sm").style(
                        f"color: {StudioColor.MUTED}"
                    )

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
            return

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
            return

        if context.is_incompatible and context.compatibility:
            error_state_panel(
                title="Reports Blocked by Compatibility Check",
                message=(
                    "Required upstream PFETL surfaces or columns did not match expected "
                    "catalog contracts."
                ),
                details=list(context.compatibility.diagnostics[:6]),
            )
            return

        builtins = DEFAULT_REPORT_REGISTRY.list_definitions(builtin_only=True)
        selected_def: dict[str, ReportDefinition] = {
            "current": builtins[0]
            if builtins
            else ReportDefinition(id="empty", name="No Template Registered")
        }
        rendered_state: dict[str, ReportResult | None] = {"result": None}
        error_state: dict[str, str | None] = {"error": None}

        saved_reports: list[SavedReportRecord] = []
        run_history: list[ReportRunRecord] = []
        with StudioStateStore(db_path=state_db_path) as store:
            if context.profile_id:
                saved_reports = store.list_report_definitions(context.profile_id)
            run_history = store.list_report_runs(limit=25)

        def _temp_export_path(filename: str) -> Path:
            export_dir = Path(tempfile.gettempdir()) / "pfetl_studio_exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            return export_dir / filename

        async def _export_chart_asset(block: RenderedChartBlock, fmt: str) -> None:
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            stem = sanitize_filename(f"{block.block_id}_{stamp}")
            target = _temp_export_path(f"{stem}.{fmt}")
            try:
                fn = (
                    ReportExporter.export_chart_png
                    if fmt == "png"
                    else ReportExporter.export_chart_svg
                )
                res = await run.io_bound(fn, block.figure_dict, target)
                media = "image/png" if fmt == "png" else "image/svg+xml"
                ui.download(res.path.read_bytes(), filename=res.path.name, media_type=media)
                ui.notify(
                    f"Downloading {res.format.upper()} chart ({res.bytes_written:,} bytes)…",
                    type="positive",
                )
            except ExportPrerequisiteError as exc:
                ui.notify(str(exc), type="negative")

        async def _export_report(fmt: str) -> None:
            result = rendered_state["result"]
            if result is None:
                ui.notify("Run Deterministic Preview before exporting.", type="warning")
                return
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            stem = sanitize_filename(f"{result.definition_id}_{stamp}")
            target = _temp_export_path(f"{stem}.{fmt}")
            try:
                export_res: ExportResult
                if fmt == "pdf":
                    export_res = await run.io_bound(
                        ReportExporter.export_pdf,
                        result,
                        target,
                        record_history=True,
                        state_db_path=state_db_path,
                    )
                    media = "application/pdf"
                elif fmt == "pptx":
                    export_res = await run.io_bound(
                        ReportExporter.export_pptx,
                        result,
                        target,
                        record_history=True,
                        state_db_path=state_db_path,
                    )
                    media = (
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    )
                else:
                    export_res = await run.io_bound(
                        ReportExporter.export_xlsx,
                        result,
                        target,
                        record_history=True,
                        state_db_path=state_db_path,
                    )
                    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                with StudioStateStore(db_path=state_db_path) as store:
                    run_history[:] = store.list_report_runs(limit=25)
                _history_container.refresh()
                ui.download(
                    export_res.path.read_bytes(),
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
            _render_report_result(result, on_export_image=_export_chart_asset)

        @ui.refreshable
        def _history_container() -> None:
            _render_history_table(run_history)

        async def _run_preview(defn: ReportDefinition) -> None:
            selected_def["current"] = defn
            error_state["error"] = None
            try:
                result = await run.io_bound(
                    service.render,
                    defn,
                    context,
                    record_history=True,
                    state_db_path=state_db_path,
                )
                rendered_state["result"] = result
                with StudioStateStore(db_path=state_db_path) as store:
                    run_history[:] = store.list_report_runs(limit=25)
            except Exception as exc:  # noqa: BLE001
                error_state["error"] = str(exc)
            _preview_container.refresh()
            _history_container.refresh()

        _render_catalog_cards(builtins, saved_reports, _run_preview)

        with ui.card().classes("studio-card w-full p-4 rounded-xl gap-3"):
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
                ui.label("Deterministic Preview & Export").classes("text-base font-semibold").style(
                    f"color: {StudioColor.TEXT}"
                )
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.button(
                        "Export PDF",
                        icon="picture_as_pdf",
                        on_click=lambda _e: _export_report("pdf"),
                    ).props("outline dense")
                    ui.button(
                        "Export PPTX",
                        icon="slideshow",
                        on_click=lambda _e: _export_report("pptx"),
                    ).props("outline dense")
                    ui.button(
                        "Export XLSX",
                        icon="table_view",
                        on_click=lambda _e: _export_report("xlsx"),
                    ).props("outline dense")
            _preview_container()

        _history_container()
