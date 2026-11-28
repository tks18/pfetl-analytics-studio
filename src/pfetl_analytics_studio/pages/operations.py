# pylint: disable=too-many-lines
"""Operations page module — runs, sync health, sources/artifacts, providers/failures & snapshot diffs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from nicegui import run, ui

from pfetl_analytics_studio.analytics import (
    ArtifactEventFilter,
    ForensicEvent,
    ForensicEventFilter,
    OperationRunRecord,
    OperationsAnalytics,
    OperationTreeNode,
    OverviewKPI,
    ProviderRequestFilter,
    RunFailureRecord,
    RunHistoryFilter,
    SourceFileRecord,
    SourceRegistryFilter,
    WarehouseHealthSnapshot,
)
from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import QueryCache, create_temp_export_path
from pfetl_analytics_studio.pages import render_domain_workspace
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.ui import (
    BadgeTone,
    TableColumn,
    bounded_data_table,
    build_ops_artifact_events_figure,
    build_ops_duration_trend_figure,
    build_ops_failure_domains_figure,
    build_ops_forensic_severity_figure,
    build_ops_forensic_slowest_ops_figure,
    build_ops_forensic_stage_figure,
    build_ops_forensic_workers_figure,
    build_ops_layer_contracts_figure,
    build_ops_provider_attempts_figure,
    build_ops_provider_status_figure,
    build_ops_source_categories_figure,
    build_ops_source_freshness_timeline_figure,
    build_ops_status_breakdown_figure,
    build_ops_sync_outcomes_figure,
    build_ops_table_row_counts_figure,
    build_ops_version_timeline_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    format_metric_value,
    kpi_card,
    loading_state_card,
    status_badge,
    trigger_file_download,
)

_INLINE_PAYLOAD_THRESHOLD_BYTES = 1_048_576  # 1 MiB


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


_SUB_TABS: tuple[tuple[str, str, str], ...] = (
    ("runs", "Runs & Sync Health", "history"),
    ("sources", "Sources & Artifacts", "folder_copy"),
    ("providers", "Providers & Failures", "cloud_sync"),
    ("forensics", "Forensics & Evidence", "troubleshoot"),
)


def _short_id(val: str) -> str:
    if not val:
        return "—"
    return f"{val[:10]}…{val[-6:]}" if len(val) > 20 else val


def _fmt_duration(run: OperationRunRecord) -> str:
    if run.duration_seconds is not None:
        return f"{run.duration_seconds:.1f}s"
    if run.is_unfinished:
        return "In Progress / Stale"
    return "—"


def _fmt_ts(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _render_kpi_grid(kpis: tuple[OverviewKPI, ...]) -> None:
    with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3"):
        for kpi in kpis:
            breakups = tuple(
                (
                    b.label,
                    format_metric_value(
                        b.value,
                        b.format,
                        compact=True,
                        unit_suffix=b.unit_suffix,
                    ),
                )
                for b in kpi.breakups
                if b.value is not None
            )
            kpi_card(
                title=kpi.label,
                value=format_metric_value(
                    kpi.value,
                    kpi.format,
                    compact=True,
                    unit_suffix=kpi.unit_suffix,
                ),
                subtitle=kpi.subtitle or None,
                breakups=breakups,
                icon="engineering",
            )


def _open_run_drawer(
    selected_run: OperationRunRecord,
    all_runs: tuple[OperationRunRecord, ...],
    profile: PFETLProfile,
    cache: QueryCache | None,
) -> None:
    """Open modal run-detail drawer with snapshot viewer & diff to previous successful run."""
    prev_success = next(
        (r for r in all_runs if r.run_id != selected_run.run_id and r.status.upper() == "SUCCESS"),
        None,
    )
    with ui.dialog() as dlg, ui.card().classes("studio-card w-full max-w-4xl p-5 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("fact_check", size="22px").style("color: var(--studio-accent);")
                ui.label("Run Detail & Snapshot Provenance").classes("text-h6 font-semibold")
            status_badge(selected_run.status or "UNKNOWN", tone=BadgeTone.INFO)

        fields: list[tuple[str, str]] = [
            ("Run ID", selected_run.run_id),
            ("Status", selected_run.status or "—"),
            ("Commit State", selected_run.commit_state or "—"),
            ("Started At", selected_run.started_at_raw or "—"),
            ("Finished At", selected_run.finished_at_raw or "In Progress / Unfinished"),
            ("Duration", _fmt_duration(selected_run)),
            ("Core Version", selected_run.application_version or "—"),
            ("Schema Version", selected_run.schema_version or "—"),
            ("Execution Log", "Available (BLOB)" if selected_run.has_execution_log else "Missing"),
            ("Settings Snapshot", _short_id(selected_run.settings_snapshot_id)),
            ("Rules Snapshot", _short_id(selected_run.rules_snapshot_id)),
            (
                "Sync Status",
                selected_run.sync.sync_status if selected_run.sync else "No Sync Summary",
            ),
        ]
        with ui.grid().classes("w-full grid-cols-1 md:grid-cols-3 gap-2"):
            for lbl, val in fields:
                with (
                    ui.column()
                    .classes("gap-0 p-2 rounded")
                    .style(
                        "background: rgba(15, 23, 42, 0.55); border: 1px solid rgba(148, 163, 184, 0.12);"
                    )
                ):
                    ui.label(lbl).classes("text-caption").style("color: var(--studio-text-muted);")
                    ui.label(val).classes("text-caption font-mono break-all")

        if selected_run.sync is not None:
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                status_badge(
                    f"Actionable: {selected_run.sync.actionable_count}", tone=BadgeTone.NEUTRAL
                )
                status_badge(f"Synced: {selected_run.sync.synced_count}", tone=BadgeTone.INFO)
                status_badge(f"Healed: {selected_run.sync.healed_count}", tone=BadgeTone.INFO)
                status_badge(
                    f"Failed: {selected_run.sync.failed_count}",
                    tone=BadgeTone.WARNING
                    if selected_run.sync.failed_count > 0
                    else BadgeTone.NEUTRAL,
                )
                status_badge(
                    f"Deferred: {selected_run.sync.deferred_count}", tone=BadgeTone.NEUTRAL
                )

        diff_box = ui.column().classes("w-full gap-2")
        diff_gen = {"gen": 0}

        def _draw_diff_body(kind: str, target_prev: str, diff: Any) -> None:
            diff_box.clear()
            with diff_box:
                if diff.state == "parse_error":
                    error_state_panel(
                        f"Malformed {kind.title()} Snapshot JSON",
                        f"{diff.parse_warning_a or ''} {diff.parse_warning_b or ''}".strip(),
                    )
                    with ui.expansion("View Raw Snapshot Text").classes("w-full"):
                        ui.code(diff.raw_text_b or diff.raw_text_a, language="text").classes(
                            "w-full"
                        )
                    return
                if diff.state == "unavailable":
                    ui.label(
                        diff.diagnostics[0] if diff.diagnostics else "Snapshot unavailable."
                    ).classes("text-caption").style("color: var(--studio-text-muted);")
                    return
                ui.label(
                    f"Comparing {kind.title()} Snapshot vs Previous Successful Run ({_short_id(target_prev)}): {len(diff.diffs)} structural differences"
                ).classes("text-caption font-semibold")
                if diff.diffs:
                    d_cols = [
                        TableColumn(name="path", label="JSON Path", field="path"),
                        TableColumn(name="change_type", label="Change", field="change_type"),
                        TableColumn(name="value_a", label="Previous", field="value_a"),
                        TableColumn(name="value_b", label="Selected", field="value_b"),
                    ]
                    d_rows = [
                        {
                            "path": d.path,
                            "change_type": d.change_type.upper(),
                            "value_a": json.dumps(d.value_a, default=str)[:120]
                            if d.value_a is not None
                            else "—",
                            "value_b": json.dumps(d.value_b, default=str)[:120]
                            if d.value_b is not None
                            else "—",
                        }
                        for d in diff.diffs[:150]
                    ]
                    bounded_data_table(
                        columns=d_cols,
                        rows=d_rows,
                        row_key="path",
                        title=f"{kind.title()} Structural Diff",
                    )
                else:
                    status_badge(
                        "Identical canonical snapshot (0 differences)", tone=BadgeTone.INFO
                    )

                with ui.expansion(f"View Full Canonical {kind.title()} Payload JSON").classes(
                    "w-full"
                ):
                    ui.code(
                        json.dumps(diff.payload_b, indent=2, default=str)
                        if diff.payload_b is not None
                        else diff.raw_text_b,
                        language="json",
                    ).classes("w-full text-caption")

        def _render_diff(kind: str) -> None:
            diff_gen["gen"] += 1
            captured = diff_gen["gen"]
            target_prev = prev_success.run_id if prev_success else selected_run.run_id

            def _fetch_diff_sync() -> Any:
                return OperationsAnalytics.snapshot_diff(
                    profile, target_prev, selected_run.run_id, kind=kind, cache=cache
                )

            if not _has_running_loop():
                try:
                    diff = _fetch_diff_sync()
                except Exception as exc:  # noqa: BLE001
                    diff_box.clear()
                    with diff_box:
                        error_state_panel(f"Unable to load {kind.title()} Snapshot Diff", str(exc))
                    return
                _draw_diff_body(kind, target_prev, diff)
                return

            diff_box.clear()
            with diff_box:
                loading_state_card(f"Loading {kind.title()} Snapshot & Diff...")

            async def _load_diff_async() -> None:
                try:
                    diff = await run.io_bound(_fetch_diff_sync)
                except Exception as exc:  # noqa: BLE001
                    if captured == diff_gen["gen"]:
                        diff_box.clear()
                        with diff_box:
                            error_state_panel(
                                f"Unable to load {kind.title()} Snapshot Diff", str(exc)
                            )
                    return
                if captured == diff_gen["gen"]:
                    _draw_diff_body(kind, target_prev, diff)

            ui.timer(0.01, _load_diff_async, once=True)

        with ui.row().classes("w-full items-center gap-2"):
            ui.button("Rules Snapshot & Diff", on_click=lambda: _render_diff("rules")).props(
                "dense outline color=primary"
            )
            ui.button("Settings Snapshot & Diff", on_click=lambda: _render_diff("settings")).props(
                "dense outline color=primary"
            )
        _render_diff("rules")

        with ui.row().classes("w-full items-center justify-between pt-2"):
            ui.label("No archived JSONL forensic log available for this run.").classes(
                "text-caption"
            ).style("color: var(--studio-text-muted);")
            ui.button("Close", on_click=dlg.close).props("flat color=primary")
    dlg.open()


def _open_file_events_drawer(file_rec: SourceFileRecord, profile: PFETLProfile) -> None:
    """Show independent freshness timestamps, artifact events, and size-aware retained payload."""

    def _fetch_meta_sync() -> tuple[Any, bool, int]:
        ev_snap = OperationsAnalytics.artifact_events(
            profile, ArtifactEventFilter(file_id=file_rec.file_id, limit=100)
        )
        has_payload, byte_len = OperationsAnalytics.has_retained_payload(profile, file_rec.file_id)
        return ev_snap, has_payload, byte_len

    def _show_drawer(ev_snap: Any, has_payload: bool, byte_len: int) -> None:
        def _download_payload_sync() -> tuple[Any, Path | None]:
            if byte_len > _INLINE_PAYLOAD_THRESHOLD_BYTES:
                return OperationsAnalytics.export_source_payload_to_file(profile, file_rec.file_id)
            res = OperationsAnalytics.source_payload(profile, file_rec.file_id)
            if not res.available or res.file_bytes is None:
                return res, None
            if len(res.file_bytes) > _INLINE_PAYLOAD_THRESHOLD_BYTES:
                fname = res.file_name or f"{file_rec.file_id}.bin"
                temp_path = create_temp_export_path(fname, default_stem=file_rec.file_id)
                temp_path.write_bytes(res.file_bytes)
                return res, temp_path
            return res, None

        async def _download_evidence() -> None:
            if not _has_running_loop():
                res, temp_path = _download_payload_sync()
            else:
                res, temp_path = await run.io_bound(_download_payload_sync)
            if not res.available or (temp_path is None and res.file_bytes is None):
                ui.notify(
                    res.diagnostics[0] if res.diagnostics else "Retained payload unavailable.",
                    type="warning",
                )
                return
            fname = res.file_name or f"{file_rec.file_id}.bin"
            if temp_path is not None:
                trigger_file_download(temp_path, filename=fname)
            elif res.file_bytes is not None:
                ui.download(res.file_bytes, filename=fname)

        with ui.dialog() as dlg, ui.card().classes("studio-card w-full max-w-4xl p-5 gap-4"):
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                ui.label(f"Artifact Lineage: {file_rec.file_name}").classes("text-h6 font-semibold")
                with ui.row().classes("items-center gap-2"):
                    status_badge(file_rec.sync_status or "UNKNOWN", tone=BadgeTone.INFO)
                    if has_payload:
                        status_badge(
                            f"Retained Payload ({_fmt_bytes(byte_len)})",
                            tone=BadgeTone.INFO,
                        )
                        ui.button(
                            f"Download Evidence ({_fmt_bytes(byte_len)})",
                            icon="download",
                            on_click=_download_evidence,
                        ).props("dense outline color=primary")
                    else:
                        status_badge("No Retained Payload", tone=BadgeTone.NEUTRAL)

            with ui.grid().classes("w-full grid-cols-1 md:grid-cols-3 gap-2"):
                for lbl, val in (
                    ("Last Seen At", _fmt_ts(file_rec.last_seen_at)),
                    ("Last Changed At", _fmt_ts(file_rec.last_changed_at)),
                    ("Last Synced At", _fmt_ts(file_rec.last_synced_at)),
                    ("Category / Type", f"{file_rec.file_category} / {file_rec.file_type}"),
                    ("File Size", _fmt_bytes(file_rec.file_size_bytes)),
                    ("Content Hash", _short_id(file_rec.file_hash)),
                ):
                    with (
                        ui.column()
                        .classes("gap-0 p-2 rounded")
                        .style(
                            "background: rgba(15, 23, 42, 0.55); border: 1px solid rgba(148, 163, 184, 0.12);"
                        )
                    ):
                        ui.label(lbl).classes("text-caption").style(
                            "color: var(--studio-text-muted);"
                        )
                        ui.label(val).classes("text-caption font-mono break-all")

            if ev_snap.events:
                cols = [
                    TableColumn(name="event_at", label="Event At", field="event_at"),
                    TableColumn(name="event_type", label="Event Type", field="event_type"),
                    TableColumn(name="transition", label="Status Transition", field="transition"),
                    TableColumn(name="convergence", label="Convergence", field="convergence"),
                    TableColumn(
                        name="affected_table", label="Affected Table", field="affected_table"
                    ),
                    TableColumn(name="event_reason", label="Reason", field="event_reason"),
                ]
                rows = [
                    {
                        "event_at": e.event_at_raw or "—",
                        "event_type": e.event_type,
                        "transition": f"{e.previous_status or '∅'} → {e.new_status}",
                        "convergence": e.convergence_result,
                        "affected_table": e.affected_table or "—",
                        "event_reason": e.event_reason or "—",
                    }
                    for e in ev_snap.events
                ]
                bounded_data_table(
                    columns=cols, rows=rows, row_key="event_at", title="Artifact Run Events"
                )
            else:
                empty_state_panel("No Artifact Events", "No events recorded for this file.")

            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=dlg.close).props("flat color=primary")
        dlg.open()

    if not _has_running_loop():
        ev_snap, has_payload, byte_len = _fetch_meta_sync()
        _show_drawer(ev_snap, has_payload, byte_len)
        return

    async def _load_drawer() -> None:
        try:
            ev_snap, has_payload, byte_len = await run.io_bound(_fetch_meta_sync)
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"Unable to load file events: {exc}", type="negative")
            return
        _show_drawer(ev_snap, has_payload, byte_len)

    asyncio.ensure_future(_load_drawer())


def _open_failure_drawer(failure: RunFailureRecord) -> None:
    """Open failure detail drawer with traceback collapsed by default (local display only)."""
    with ui.dialog() as dlg, ui.card().classes("studio-card w-full max-w-3xl p-5 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(f"Run Failure #{failure.id}: {failure.error_type}").classes(
                "text-h6 font-semibold"
            )
            status_badge(failure.failure_domain or "UNKNOWN", tone=BadgeTone.WARNING)

        with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 gap-2"):
            for lbl, val in (
                ("Run ID", failure.run_id),
                ("Stage", failure.stage or "—"),
                ("Failed ISIN", failure.failed_isin or "—"),
                ("Created At", failure.created_at_raw or "—"),
            ):
                with (
                    ui.column()
                    .classes("gap-0 p-2 rounded")
                    .style(
                        "background: rgba(15, 23, 42, 0.55); border: 1px solid rgba(148, 163, 184, 0.12);"
                    )
                ):
                    ui.label(lbl).classes("text-caption").style("color: var(--studio-text-muted);")
                    ui.label(val).classes("text-caption font-mono break-all")

        ui.label("Error Message").classes("text-caption font-semibold")
        ui.code(failure.error_message or "—", language="text").classes("w-full text-caption")

        if failure.traceback_log:
            with ui.expansion(
                "Traceback (Local Display Only — Collapsed by Default)",
                icon="bug_report",
                value=False,
            ).classes("w-full"):
                ui.code(failure.traceback_log, language="text").classes("w-full text-caption")

        with ui.row().classes("w-full justify-end"):
            ui.button("Close", on_click=dlg.close).props("flat color=primary")
    dlg.open()


def _render_warehouse_section(warehouse: WarehouseHealthSnapshot) -> None:
    if warehouse.state != "ready":
        empty_state_panel(
            "Warehouse Metadata Unavailable",
            warehouse.diagnostics[0] if warehouse.diagnostics else "No meta tables found.",
        )
        return

    with ui.card().classes("studio-card w-full p-4 gap-3 rounded-lg"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.column().classes("gap-0"):
                ui.label("Warehouse Layer Contracts & Freshness").classes(
                    "text-subtitle1 font-semibold"
                )
                ui.label(
                    "Row counts are diagnostic only; do not infer financial correctness from row count alone."
                ).classes("text-caption").style("color: #FBBF24;")
            if warehouse.generated_at is not None:
                status_badge(
                    f"Generated: {warehouse.generated_at.strftime('%Y-%m-%d %H:%M')}",
                    tone=BadgeTone.INFO,
                )

        with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3"):
            for layer in warehouse.layers:
                kpi_card(
                    title=f"{layer.layer.upper()} Layer Contracts",
                    value=str(layer.contract_count),
                    subtitle=f"Full replace: {layer.full_replace_count} · Domains: {len(layer.domains)}",
                    icon="layers",
                )
            kpi_card(
                title="Tracked Warehouse Tables",
                value=str(warehouse.total_tracked_tables),
                subtitle=f"Total rows: {warehouse.total_tracked_rows:,}",
                icon="table_chart",
            )

        if warehouse.tables:
            cols = [
                TableColumn(name="schema_name", label="Schema", field="schema_name"),
                TableColumn(name="table_name", label="Table", field="table_name"),
                TableColumn(
                    name="row_count",
                    label="Row Count",
                    field="row_count",
                    align="right",
                    numeric=True,
                ),
                TableColumn(name="generated_at", label="Generated At", field="generated_at"),
            ]
            rows = [
                {
                    "schema_name": t.schema_name,
                    "table_name": t.table_name,
                    "row_count": f"{t.row_count:,}",
                    "generated_at": t.generated_at.strftime("%Y-%m-%d %H:%M:%S")
                    if t.generated_at
                    else "—",
                }
                for t in warehouse.tables
            ]
            bounded_data_table(
                columns=cols,
                rows=rows,
                row_key="table_name",
                title="Diagnostic Table Row Counts (meta.m_Table_Row_Counts)",
            )


def _render_sources_tab(
    container: ui.tab_panel,
    profile: PFETLProfile,
    cache: QueryCache | None,
) -> None:
    state: dict[str, Any] = {"category": None, "status": None, "search": "", "limit": 200}
    gen_state: dict[str, int] = {"gen": 0}

    def _draw() -> None:
        gen_state["gen"] += 1
        captured = gen_state["gen"]
        flt = SourceRegistryFilter(
            category=state["category"],
            sync_status=state["status"],
            search=state["search"] or None,
            limit=int(state["limit"]),
        )

        def _fetch_sync() -> tuple[Any, Any]:
            src = OperationsAnalytics.source_registry(profile, flt, cache=cache)
            evs = OperationsAnalytics.artifact_events(
                profile, ArtifactEventFilter(limit=200), cache=cache
            )
            return src, evs

        if not _has_running_loop():
            try:
                src, evs = _fetch_sync()
            except Exception as exc:  # noqa: BLE001
                container.clear()
                with container:
                    error_state_panel("Unable to load Sources & Artifacts", str(exc))
                return
            _draw_body(src, evs)
            return

        if not container.default_slot.children:
            with container:
                loading_state_card("Loading Sources & Artifacts...")

        async def _load_async() -> None:
            try:
                src, evs = await run.io_bound(_fetch_sync)
            except Exception as exc:  # noqa: BLE001
                if captured == gen_state["gen"]:
                    container.clear()
                    with container:
                        error_state_panel("Unable to load Sources & Artifacts", str(exc))
                return
            if captured == gen_state["gen"]:
                _draw_body(src, evs)

        ui.timer(0.01, _load_async, once=True)

    def _draw_body(src: Any, evs: Any) -> None:
        container.clear()
        with container:
            if src.state == "unavailable":
                error_state_panel(
                    "Control Plane Unavailable",
                    src.diagnostics[0] if src.diagnostics else "SQLite Control Plane missing.",
                )
                return

            def _set_cat(v: Any) -> None:
                state["category"] = str(v) or None
                _draw()

            def _set_st(v: Any) -> None:
                state["status"] = str(v) or None
                _draw()

            def _set_search(v: Any) -> None:
                state["search"] = str(v or "")
                _draw()

            with ui.row().classes("w-full items-center gap-3 flex-wrap mb-3"):
                ui.select(
                    options={"": "All Categories", **{c: c for c in src.available_categories}},
                    value=state["category"] or "",
                    label="File Category",
                    on_change=lambda e: _set_cat(e.value),
                ).props("dense outlined dark").classes("w-52")
                ui.select(
                    options={"": "All Sync Statuses", **{s: s for s in src.available_statuses}},
                    value=state["status"] or "",
                    label="Sync Status",
                    on_change=lambda e: _set_st(e.value),
                ).props("dense outlined dark").classes("w-44")
                ui.input(
                    label="Search Filename / Path",
                    value=state["search"],
                    on_change=lambda e: _set_search(e.value),
                ).props("dense outlined dark clearable").classes("w-64")

            if src.state == "empty" or not src.files:
                empty_state_panel(
                    "No Source Files Found",
                    src.diagnostics[0]
                    if src.diagnostics
                    else "No matching files in cp_file_registry.",
                )
                return

            _render_kpi_grid(src.kpis)

            with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4 my-4"):
                chart_panel(
                    title="Registered Files by Category",
                    subtitle="Top source categories in cp_file_registry",
                    figure=build_ops_source_categories_figure(src.category_counts),
                    source_caption="Source: cp_file_registry",
                )
                chart_panel(
                    title="Source File Sync Status Mix",
                    subtitle="SYNCED vs REMOVED / PENDING distribution",
                    figure=build_ops_source_freshness_timeline_figure(src.files),
                    source_caption="Source: cp_file_registry",
                )

            if evs.events:
                chart_panel(
                    title="Artifact Lifecycle Events & Convergence",
                    subtitle="Verbatim event_type × convergence_result across recent events",
                    figure=build_ops_artifact_events_figure(evs.events),
                    source_caption="Source: cp_artifact_run_events",
                )

            file_map = {f.file_id: f for f in src.files}
            with ui.row().classes("w-full items-center gap-3 flex-wrap my-2"):
                file_sel = (
                    ui.select(
                        options={
                            f.file_id: f"{f.file_name} ({f.file_category})" for f in src.files[:100]
                        },
                        value=src.files[0].file_id,
                        label="Inspect File Lineage",
                    )
                    .props("dense outlined dark")
                    .classes("w-96")
                )
                ui.button(
                    "Open File Lineage Drawer",
                    icon="open_in_new",
                    on_click=lambda: (
                        _open_file_events_drawer(file_map[str(file_sel.value)], profile)
                        if str(file_sel.value) in file_map
                        else None
                    ),
                ).props("outline color=primary")

            f_cols = [
                TableColumn(name="file_name", label="File Name", field="file_name"),
                TableColumn(name="file_category", label="Category", field="file_category"),
                TableColumn(name="sync_status", label="Sync Status", field="sync_status"),
                TableColumn(name="size", label="Size", field="size", align="right"),
                TableColumn(name="last_seen", label="Last Seen", field="last_seen"),
                TableColumn(name="last_changed", label="Last Changed", field="last_changed"),
                TableColumn(name="last_synced", label="Last Synced", field="last_synced"),
            ]
            f_rows = [
                {
                    "file_name": f.file_name,
                    "file_category": f.file_category,
                    "sync_status": f.sync_status,
                    "size": _fmt_bytes(f.file_size_bytes),
                    "last_seen": _fmt_ts(f.last_seen_at),
                    "last_changed": _fmt_ts(f.last_changed_at),
                    "last_synced": _fmt_ts(f.last_synced_at),
                }
                for f in src.files
            ]
            bounded_data_table(
                columns=f_cols,
                rows=f_rows,
                row_key="file_name",
                title="Source Registry & Independent Freshness (cp_file_registry)",
            )

            if evs.events:
                e_cols = [
                    TableColumn(name="event_at", label="Event At", field="event_at"),
                    TableColumn(name="event_type", label="Event Type", field="event_type"),
                    TableColumn(name="transition", label="Previous → New", field="transition"),
                    TableColumn(name="convergence", label="Convergence", field="convergence"),
                    TableColumn(
                        name="affected_table", label="Affected Table", field="affected_table"
                    ),
                    TableColumn(name="event_reason", label="Event Reason", field="event_reason"),
                ]
                e_rows = [
                    {
                        "event_at": e.event_at_raw or "—",
                        "event_type": e.event_type,
                        "transition": f"{e.previous_status or '∅'} → {e.new_status}",
                        "convergence": e.convergence_result,
                        "affected_table": e.affected_table or "—",
                        "event_reason": e.event_reason or "—",
                    }
                    for e in evs.events
                ]
                bounded_data_table(
                    columns=e_cols,
                    rows=e_rows,
                    row_key="event_at",
                    title="Recent Artifact Run Events (cp_artifact_run_events)",
                )

    _draw()


def _render_providers_tab(
    container: ui.tab_panel,
    profile: PFETLProfile,
    cache: QueryCache | None,
) -> None:
    state: dict[str, Any] = {"provider": None, "status": None, "run_id": None}
    gen_state: dict[str, int] = {"gen": 0}

    def _draw() -> None:
        gen_state["gen"] += 1
        captured = gen_state["gen"]
        flt = ProviderRequestFilter(
            run_id=state["run_id"],
            provider=state["provider"],
            status=state["status"],
            limit=250,
        )
        run_id_val = state["run_id"]

        def _fetch_sync() -> tuple[Any, Any]:
            prov = OperationsAnalytics.provider_requests(profile, flt, cache=cache)
            fails = OperationsAnalytics.run_failures(
                profile, run_id=run_id_val, limit=200, cache=cache
            )
            return prov, fails

        if not _has_running_loop():
            try:
                prov, fails = _fetch_sync()
            except Exception as exc:  # noqa: BLE001
                container.clear()
                with container:
                    error_state_panel("Unable to load Providers & Failures", str(exc))
                return
            _draw_body(prov, fails)
            return

        if not container.default_slot.children:
            with container:
                loading_state_card("Loading Providers & Failures...")

        async def _load_async() -> None:
            try:
                prov, fails = await run.io_bound(_fetch_sync)
            except Exception as exc:  # noqa: BLE001
                if captured == gen_state["gen"]:
                    container.clear()
                    with container:
                        error_state_panel("Unable to load Providers & Failures", str(exc))
                return
            if captured == gen_state["gen"]:
                _draw_body(prov, fails)

        ui.timer(0.01, _load_async, once=True)

    def _draw_body(prov: Any, fails: Any) -> None:
        container.clear()
        with container:
            if prov.state == "unavailable":
                error_state_panel(
                    "Control Plane Unavailable",
                    prov.diagnostics[0] if prov.diagnostics else "SQLite Control Plane missing.",
                )
                return

            def _set_prov(v: Any) -> None:
                state["provider"] = str(v) or None
                _draw()

            def _set_st(v: Any) -> None:
                state["status"] = str(v) or None
                _draw()

            with ui.row().classes("w-full items-center gap-3 flex-wrap mb-3"):
                ui.select(
                    options={"": "All Providers", **{p: p for p in prov.available_providers}},
                    value=state["provider"] or "",
                    label="Provider",
                    on_change=lambda e: _set_prov(e.value),
                ).props("dense outlined dark").classes("w-52")
                ui.select(
                    options={"": "All Statuses", **{s: s for s in prov.available_statuses}},
                    value=state["status"] or "",
                    label="Request Status",
                    on_change=lambda e: _set_st(e.value),
                ).props("dense outlined dark").classes("w-44")

            if prov.kpis:
                _render_kpi_grid(prov.kpis)

            if prov.requests:
                with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4 my-4"):
                    chart_panel(
                        title="Provider Requests by Status",
                        subtitle="CACHED vs SUCCESS vs FAILED across providers",
                        figure=build_ops_provider_status_figure(prov.requests),
                        source_caption="Source: cp_external_provider_requests",
                    )
                    chart_panel(
                        title="Provider Attempt Count Distribution",
                        subtitle="0 attempts (cache hits) vs retry attempt counts",
                        figure=build_ops_provider_attempts_figure(prov.requests),
                        source_caption="Source: cp_external_provider_requests",
                    )

                p_cols = [
                    TableColumn(name="created_at", label="Created At", field="created_at"),
                    TableColumn(name="provider", label="Provider", field="provider"),
                    TableColumn(name="entity", label="Entity / Ticker", field="entity"),
                    TableColumn(name="window", label="Requested Window", field="window"),
                    TableColumn(name="attempts", label="Attempts", field="attempts", align="right"),
                    TableColumn(name="status", label="Status", field="status"),
                    TableColumn(name="failure_class", label="Failure Class", field="failure_class"),
                ]
                p_rows = [
                    {
                        "created_at": r.created_at_raw or "—",
                        "provider": r.provider,
                        "entity": r.entity,
                        "window": (
                            f"{r.requested_start.isoformat()} → {r.requested_end.isoformat()}"
                            if r.requested_start and r.requested_end
                            else "—"
                        ),
                        "attempts": r.attempt_count,
                        "status": r.status,
                        "failure_class": r.failure_class or "—",
                    }
                    for r in prov.requests
                ]
                bounded_data_table(
                    columns=p_cols,
                    rows=p_rows,
                    row_key="entity",
                    title="External Provider Requests (cp_external_provider_requests)",
                )
            else:
                empty_state_panel(
                    "No Provider Requests",
                    prov.diagnostics[0] if prov.diagnostics else "No requests recorded.",
                )

            if fails.failures:
                chart_panel(
                    title="Run Failures by Domain & Stage",
                    subtitle="Recorded exceptions grouped by failure_domain and stage",
                    figure=build_ops_failure_domains_figure(fails.failures),
                    source_caption="Source: cp_run_failures",
                )
                fail_map = {f.id: f for f in fails.failures}
                with ui.row().classes("w-full items-center gap-3 flex-wrap my-2"):
                    fail_sel = (
                        ui.select(
                            options={
                                f.id: f"#{f.id} · {f.failure_domain}/{f.stage} ({f.error_type})"
                                for f in fails.failures
                            },
                            value=fails.failures[0].id,
                            label="Inspect Failure Detail",
                        )
                        .props("dense outlined dark")
                        .classes("w-96")
                    )
                    ui.button(
                        "Open Failure Drawer",
                        icon="bug_report",
                        on_click=lambda: (
                            _open_failure_drawer(fail_map[int(fail_sel.value)])
                            if int(fail_sel.value or -1) in fail_map
                            else None
                        ),
                    ).props("outline color=warning")

                fl_cols = [
                    TableColumn(name="id", label="ID", field="id", align="right"),
                    TableColumn(name="run_id", label="Run ID", field="run_id"),
                    TableColumn(name="domain", label="Domain", field="domain"),
                    TableColumn(name="stage", label="Stage", field="stage"),
                    TableColumn(name="error_type", label="Error Type", field="error_type"),
                    TableColumn(name="error_message", label="Message", field="error_message"),
                    TableColumn(name="created_at", label="Created At", field="created_at"),
                ]
                fl_rows = [
                    {
                        "id": f.id,
                        "run_id": _short_id(f.run_id),
                        "domain": f.failure_domain,
                        "stage": f.stage,
                        "error_type": f.error_type,
                        "error_message": f.error_message[:120],
                        "created_at": f.created_at_raw or "—",
                    }
                    for f in fails.failures
                ]
                bounded_data_table(
                    columns=fl_cols,
                    rows=fl_rows,
                    row_key="id",
                    title="Run Failures (cp_run_failures)",
                )
            else:
                with ui.card().classes("studio-card w-full p-4 rounded-lg"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("check_circle", size="20px").style("color: #10B981;")
                        ui.label("Zero Run Failures Recorded in cp_run_failures").classes(
                            "text-body2 font-semibold"
                        )

    _draw()


def _open_forensic_event_drawer(ev: ForensicEvent) -> None:
    """Open detailed drawer for a single ForensicEvent with context, error & raw JSON."""
    with ui.dialog() as dlg, ui.card().classes("studio-card w-full max-w-4xl p-5 gap-4"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("troubleshoot", size="22px").style("color: var(--studio-accent);")
                ui.label(f"Seq #{ev.sequence} · {ev.event_code}").classes("text-h6 font-semibold")
            with ui.row().classes("items-center gap-2"):
                status_badge(ev.severity or "INFO", tone=BadgeTone.INFO)
                status_badge(ev.outcome or "—", tone=BadgeTone.NEUTRAL)

        ui.label(ev.message or "—").classes("text-body2 font-medium")

        c = ev.context
        ctx_items = [
            ("Timestamp", ev.timestamp_raw or "—"),
            ("Stage", c.stage or "—"),
            ("Component", c.component or "—"),
            ("Operation", c.operation or "—"),
            ("Operation ID", c.operation_id or "—"),
            ("Parent Op ID", c.parent_operation_id or "—"),
            (
                "Entity",
                f"{c.entity_type}: {c.entity_id}" if (c.entity_type or c.entity_id) else "—",
            ),
            ("Duration (ms)", f"{c.duration_ms:.2f} ms" if c.duration_ms is not None else "—"),
            (
                "Rows / Attempt",
                f"{c.rows if c.rows is not None else '—'} / {c.attempt if c.attempt is not None else '—'}",
            ),
            (
                "Provider / Cache",
                f"{c.provider or '—'} / {c.cache_hit if c.cache_hit is not None else '—'}",
            ),
            ("Worker / Batch", f"{c.worker_id or '—'} / {c.batch_id or '—'}"),
            ("Schema Ver", str(ev.schema_version)),
        ]
        with ui.grid().classes("w-full grid-cols-1 md:grid-cols-3 gap-2"):
            for lbl, val in ctx_items:
                with (
                    ui.column()
                    .classes("gap-0 p-2 rounded")
                    .style(
                        "background: rgba(15, 23, 42, 0.55); border: 1px solid rgba(148, 163, 184, 0.12);"
                    )
                ):
                    ui.label(lbl).classes("text-caption").style("color: var(--studio-text-muted);")
                    ui.label(val).classes("text-caption font-mono break-all")

        if ev.error is not None:
            err = ev.error
            with (
                ui.card()
                .classes("w-full p-3 gap-2")
                .style(
                    "background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.35);"
                )
            ):
                with ui.row().classes("items-center justify-between"):
                    ui.label(f"Error: {err.exception_type}").classes("text-subtitle2 font-semibold")
                    status_badge(err.failure_domain or "UNKNOWN", tone=BadgeTone.WARNING)
                ui.label(err.exception_message or "—").classes("text-caption font-mono")
                if err.remediation_hint:
                    ui.label(f"Remediation: {err.remediation_hint}").classes("text-caption").style(
                        "color: #FDE68A;"
                    )
                if err.traceback_summary:
                    with ui.expansion("Traceback Summary (Local Only)", value=False).classes(
                        "w-full"
                    ):
                        ui.code(err.traceback_summary, language="text").classes(
                            "w-full text-caption"
                        )

        with ui.expansion("Raw Event JSON", icon="code", value=False).classes("w-full"):
            ui.code(json.dumps(ev.raw_payload, indent=2, default=str), language="json").classes(
                "w-full text-caption"
            )

        with ui.row().classes("w-full justify-end"):
            ui.button("Close", on_click=dlg.close).props("flat color=primary")
    dlg.open()


def _tree_nodes_to_quasar(nodes: tuple[OperationTreeNode, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in nodes:
        dur = f" · {n.duration_ms:.1f}ms" if n.duration_ms is not None else ""
        rows = f" · {n.rows} rows" if n.rows is not None else ""
        orphan = " [ORPHAN]" if n.is_orphan else ""
        lbl = f"{n.operation or n.operation_id} ({n.stage or '—'}{dur}{rows}) · {n.outcome or '—'}{orphan}"
        out.append(
            {
                "id": n.operation_id,
                "label": lbl,
                "children": _tree_nodes_to_quasar(n.children),
            }
        )
    return out


def _render_forensics_tab(
    container: ui.tab_panel,
    profile: PFETLProfile,
    cache: QueryCache | None,
) -> None:
    state: dict[str, Any] = {
        "run_id": "",
        "severity": None,
        "event_code": None,
        "outcome": None,
        "stage": None,
        "component": None,
        "operation": None,
        "provider": None,
        "worker_id": None,
        "batch_id": None,
        "failure_domain": None,
        "search": "",
    }
    runs_ref: dict[str, Any] = {"snap": None}
    gen_state: dict[str, int] = {"gen": 0}

    def _draw() -> None:
        gen_state["gen"] += 1
        captured = gen_state["gen"]
        ev_filter = ForensicEventFilter(
            severity=state["severity"],
            event_code=state["event_code"],
            outcome=state["outcome"],
            stage=state["stage"],
            component=state["component"],
            operation=state["operation"],
            provider=state["provider"],
            worker_id=state["worker_id"],
            batch_id=state["batch_id"],
            failure_domain=state["failure_domain"],
            search=state["search"] or None,
            limit=500,
        )

        def _fetch_sync() -> tuple[Any, str, Any, Any, Any]:
            if runs_ref["snap"] is None:
                runs_ref["snap"] = OperationsAnalytics.run_history(
                    profile, RunHistoryFilter(limit=50), cache=cache
                )
            runs_snap_local = runs_ref["snap"]
            if not state["run_id"] and runs_snap_local.runs:
                state["run_id"] = runs_snap_local.runs[0].run_id
            run_id = str(state["run_id"] or "")
            if not run_id:
                return runs_snap_local, "", None, None, None
            summary = OperationsAnalytics.forensic_summary(profile, run_id)
            ev_snap = OperationsAnalytics.forensic_events(profile, run_id, ev_filter)
            tree = OperationsAnalytics.operation_tree(profile, run_id)
            return runs_snap_local, run_id, summary, ev_snap, tree

        if not _has_running_loop():
            try:
                runs_snap_local, run_id, summary, ev_snap, tree = _fetch_sync()
            except Exception as exc:  # noqa: BLE001
                container.clear()
                with container:
                    error_state_panel("Unable to decode forensic logs", str(exc))
                return
            _draw_body(runs_snap_local, run_id, summary, ev_snap, tree)
            return

        if not container.default_slot.children:
            with container:
                loading_state_card("Loading forensic logs & operation tree...")

        async def _load_async() -> None:
            try:
                runs_snap_local, run_id, summary, ev_snap, tree = await run.io_bound(_fetch_sync)
            except Exception as exc:  # noqa: BLE001
                if captured == gen_state["gen"]:
                    container.clear()
                    with container:
                        error_state_panel("Unable to decode forensic logs", str(exc))
                return
            if captured == gen_state["gen"]:
                _draw_body(runs_snap_local, run_id, summary, ev_snap, tree)

        ui.timer(0.01, _load_async, once=True)

    def _draw_body(
        runs_snap: Any,
        run_id: str,
        summary: Any,
        ev_snap: Any,
        tree: Any,
    ) -> None:
        container.clear()
        with container:
            if not run_id:
                empty_state_panel("No Runs Available", "No PFETL runs found to inspect logs.")
                return

            def _set(key: str, val: Any) -> None:
                state[key] = str(val) or None
                _draw()

            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2 mb-2"):
                run_opts = {
                    r.run_id: f"{r.run_id[:12]}… ({r.status} · {r.started_at_raw[:16]})"
                    for r in runs_snap.runs
                } or {run_id: run_id}
                ui.select(
                    options=run_opts,
                    value=run_id,
                    label="Target Run",
                    on_change=lambda e: _set("run_id", e.value),
                ).props("dense outlined dark").classes("w-80")
                with ui.row().classes("items-center gap-2"):
                    status_badge(f"Log Source: {ev_snap.source}", tone=BadgeTone.INFO)
                    if ev_snap.malformed_lines > 0:
                        status_badge(
                            f"Malformed Lines: {ev_snap.malformed_lines}",
                            tone=BadgeTone.WARNING,
                        )
                    if ev_snap.unknown_schema_versions:
                        status_badge(
                            f"Unknown Schema: {', '.join(ev_snap.unknown_schema_versions)}",
                            tone=BadgeTone.WARNING,
                        )

            if ev_snap.state == "unavailable":
                empty_state_panel(
                    "Forensic Logs Unavailable",
                    ev_snap.diagnostics[0]
                    if ev_snap.diagnostics
                    else f"No execution_log or fallback JSONL found for run '{run_id}'.",
                )
                return

            _render_kpi_grid(summary.kpis)

            with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4 my-4"):
                chart_panel(
                    title="Severity & Outcome Breakdown",
                    subtitle="Normalized event counts by severity and outcome",
                    figure=build_ops_forensic_severity_figure(
                        summary.severity_counts, summary.outcome_counts
                    ),
                    source_caption="Source: cp_runs.execution_log",
                )
                chart_panel(
                    title="Pipeline Stage Activity & Emitted Rows",
                    subtitle="Event count (bars) and emitted context.rows (line)",
                    figure=build_ops_forensic_stage_figure(
                        summary.stage_counts, summary.stage_rows
                    ),
                    source_caption="Source: cp_runs.execution_log",
                )
                chart_panel(
                    title="Slowest Operations (Duration in Seconds)",
                    subtitle="Only operations with emitted duration_ms converted to seconds (never synthesized)",
                    figure=build_ops_forensic_slowest_ops_figure(summary.slowest_operations),
                    source_caption="Source: cp_runs.execution_log",
                )
                chart_panel(
                    title="Worker, Provider & Cache Activity",
                    subtitle="Emitted worker_id, provider, and cache_hit counters",
                    figure=build_ops_forensic_workers_figure(
                        summary.worker_counts,
                        summary.provider_counts,
                        summary.cache_hits,
                        summary.cache_misses,
                    ),
                    source_caption="Source: cp_runs.execution_log",
                )

            if tree:
                with ui.expansion(
                    "Operation Lineage Tree (parent_operation_id → operation_id)",
                    icon="account_tree",
                    value=False,
                ).classes("w-full studio-card p-3 rounded-lg mb-3"):
                    ui.tree(_tree_nodes_to_quasar(tree), label_key="label", node_key="id").classes(
                        "w-full"
                    )

            with ui.row().classes("w-full items-center gap-2 flex-wrap mb-3"):
                for key, lbl, opts in (
                    ("severity", "Severity", ev_snap.available_severities),
                    ("outcome", "Outcome", ev_snap.available_outcomes),
                    ("stage", "Stage", ev_snap.available_stages),
                    ("event_code", "Event Code", ev_snap.available_codes),
                    ("component", "Component", ev_snap.available_components),
                    ("operation", "Operation", ev_snap.available_operations),
                    ("provider", "Provider", ev_snap.available_providers),
                    ("worker_id", "Worker", ev_snap.available_workers),
                    ("batch_id", "Batch", ev_snap.available_batches),
                    ("failure_domain", "Failure Domain", ev_snap.available_failure_domains),
                ):
                    if opts:
                        ui.select(
                            options={"": f"All {lbl}", **{o: o for o in opts}},
                            value=state[key] or "",
                            label=lbl,
                            on_change=lambda e, k=key: _set(k, e.value),
                        ).props("dense outlined dark").classes("w-40")
                ui.input(
                    label="Search message / entity / error",
                    value=state["search"],
                    on_change=lambda e: _set("search", e.value),
                ).props("dense outlined dark clearable").classes("w-64")

            if ev_snap.events:
                ev_map = {e.event_id or str(e.sequence): e for e in ev_snap.events}
                with ui.row().classes("w-full items-center gap-3 flex-wrap mb-2"):
                    ev_sel = (
                        ui.select(
                            options={
                                (
                                    e.event_id or str(e.sequence)
                                ): f"#{e.sequence} · {e.severity} · {e.event_code}"
                                for e in ev_snap.events[:150]
                            },
                            value=ev_snap.events[0].event_id or str(ev_snap.events[0].sequence),
                            label="Inspect Forensic Event",
                        )
                        .props("dense outlined dark")
                        .classes("w-96")
                    )
                    ui.button(
                        "Open Event Detail",
                        icon="open_in_new",
                        on_click=lambda: (
                            _open_forensic_event_drawer(ev_map[str(ev_sel.value)])
                            if str(ev_sel.value) in ev_map
                            else None
                        ),
                    ).props("outline color=primary")

                t_cols = [
                    TableColumn(name="sequence", label="Seq", field="sequence", align="right"),
                    TableColumn(name="timestamp", label="Timestamp", field="timestamp"),
                    TableColumn(name="severity", label="Severity", field="severity"),
                    TableColumn(name="event_code", label="Event Code", field="event_code"),
                    TableColumn(name="stage", label="Stage", field="stage"),
                    TableColumn(name="outcome", label="Outcome", field="outcome"),
                    TableColumn(
                        name="duration_ms", label="Dur (ms)", field="duration_ms", align="right"
                    ),
                    TableColumn(name="rows", label="Rows", field="rows", align="right"),
                    TableColumn(name="message", label="Message", field="message"),
                ]
                t_rows = [
                    {
                        "sequence": e.sequence,
                        "timestamp": e.timestamp_raw[11:23]
                        if len(e.timestamp_raw) >= 23
                        else e.timestamp_raw,
                        "severity": e.severity,
                        "event_code": e.event_code,
                        "stage": e.context.stage or "—",
                        "outcome": e.outcome or "—",
                        "duration_ms": f"{e.context.duration_ms:.1f}"
                        if e.context.duration_ms is not None
                        else "—",
                        "rows": e.context.rows if e.context.rows is not None else "—",
                        "message": e.message[:140],
                    }
                    for e in ev_snap.events
                ]
                bounded_data_table(
                    columns=t_cols,
                    rows=t_rows,
                    row_key="sequence",
                    title=f"Forensic Event Timeline ({len(ev_snap.events)} of {ev_snap.total_decoded} decoded)",
                )
            else:
                empty_state_panel("No Matching Events", "Adjust forensic filters to view events.")

    _draw()


def render_operations_page(  # pylint: disable=too-many-statements
    context: AppContext,
    *,
    cache: QueryCache | None = None,
    initial_tab: str = "runs",
    on_tab_change: Callable[[str], Any] | None = None,
) -> None:
    """Render the Operations & Forensics workspace from shared `AppContext`."""
    route = ROUTES_BY_ID["operations"]
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
            if context.profile is not None:
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    status_badge(
                        f"Profile: {context.profile.label}",
                        tone=BadgeTone.INFO,
                        outline=True,
                    )
                    if context.effective_run_id:
                        status_badge(
                            f"Active Run: {context.effective_run_id[:12]}",
                            tone=BadgeTone.SUCCESS,
                            outline=True,
                        )

        if context.profile is None:
            render_domain_workspace(
                context=context,
                route=route,
                workspace_views=tuple(lbl for _, lbl, _ in _SUB_TABS),
                upstream_sources=(
                    "cp_runs",
                    "cp_run_sync_summaries",
                    "cp_file_registry",
                    "cp_artifact_run_events",
                    "cp_external_provider_requests",
                    "cp_run_failures",
                    "cp_settings_snapshots",
                    "cp_rules_snapshots",
                ),
            )
            return

        profile = context.profile
        filter_state: dict[str, Any] = {"status": None, "commit_state": None, "limit": 100}

        valid_tab = initial_tab if any(t[0] == initial_tab for t in _SUB_TABS) else "runs"
        rendered_tabs: set[str] = set()
        runs_gen: dict[str, int] = {"gen": 0}

        with ui.tabs().classes("w-full studio-subtabs") as tabs:
            for tab_id, tab_label, tab_icon in _SUB_TABS:
                ui.tab(tab_id, label=tab_label, icon=tab_icon)
        tabs.set_value(valid_tab)

        with ui.tab_panels(tabs, value=valid_tab).classes("w-full bg-transparent"):
            p_runs = ui.tab_panel("runs").classes("p-0 pt-3")
            p_sources = ui.tab_panel("sources").classes("p-0 pt-3")
            p_providers = ui.tab_panel("providers").classes("p-0 pt-3")
            p_forensics = ui.tab_panel("forensics").classes("p-0 pt-3")

        def _render_runs() -> None:
            runs_gen["gen"] += 1
            captured = runs_gen["gen"]
            flt = RunHistoryFilter(
                limit=int(filter_state["limit"]),
                status=filter_state["status"],
                commit_state=filter_state["commit_state"],
            )

            def _fetch_runs_sync() -> tuple[Any, Any]:
                snap = OperationsAnalytics.run_history(
                    profile,
                    flt,
                    catalog_snapshot=context.catalog_snapshot,
                    cache=cache,
                )
                warehouse = OperationsAnalytics.warehouse_health(
                    profile,
                    catalog_snapshot=context.catalog_snapshot,
                    cache=cache,
                )
                return snap, warehouse

            if not _has_running_loop():
                try:
                    snap, warehouse = _fetch_runs_sync()
                except Exception as exc:  # noqa: BLE001
                    p_runs.clear()
                    with p_runs:
                        error_state_panel("Unable to load Operations telemetry", str(exc))
                    return
                _render_runs_body(snap, warehouse)
                return

            if not p_runs.default_slot.children:
                with p_runs:
                    loading_state_card("Loading Operations & Warehouse health...")

            async def _load_runs_async() -> None:
                try:
                    snap, warehouse = await run.io_bound(_fetch_runs_sync)
                except Exception as exc:  # noqa: BLE001
                    if captured == runs_gen["gen"]:
                        p_runs.clear()
                        with p_runs:
                            error_state_panel("Unable to load Operations telemetry", str(exc))
                    return
                if captured == runs_gen["gen"]:
                    _render_runs_body(snap, warehouse)

            ui.timer(0.01, _load_runs_async, once=True)

        def _render_runs_body(snap: Any, warehouse: Any) -> None:
            p_runs.clear()
            with p_runs:
                if snap.state == "unavailable":
                    error_state_panel(
                        "Control Plane Unavailable",
                        snap.diagnostics[0]
                        if snap.diagnostics
                        else "SQLite Control Plane missing.",
                    )
                    return

                def _set_status(val: Any) -> None:
                    filter_state["status"] = str(val) or None
                    _render_runs()

                def _set_commit(val: Any) -> None:
                    filter_state["commit_state"] = str(val) or None
                    _render_runs()

                def _set_limit(val: Any) -> None:
                    filter_state["limit"] = int(val or 100)
                    _render_runs()

                with ui.row().classes("w-full items-center gap-3 flex-wrap mb-3"):
                    status_opts = {"": "All Statuses", **{s: s for s in snap.available_statuses}}
                    commit_opts = {
                        "": "All Commit States",
                        **{c: c for c in snap.available_commit_states},
                    }
                    limit_opts = {25: "Last 25", 100: "Last 100", 250: "Last 250"}
                    ui.select(
                        options=status_opts,
                        value=filter_state["status"] or "",
                        label="Status",
                        on_change=lambda e: _set_status(e.value),
                    ).props("dense outlined dark").classes("w-44")
                    ui.select(
                        options=commit_opts,
                        value=filter_state["commit_state"] or "",
                        label="Commit State",
                        on_change=lambda e: _set_commit(e.value),
                    ).props("dense outlined dark").classes("w-48")
                    ui.select(
                        options=limit_opts,
                        value=int(filter_state["limit"]),
                        label="Limit",
                        on_change=lambda e: _set_limit(e.value),
                    ).props("dense outlined dark").classes("w-36")

                if snap.state == "empty" or not snap.runs:
                    empty_state_panel(
                        "No Matching Runs",
                        snap.diagnostics[0]
                        if snap.diagnostics
                        else "No Control Plane runs matched your current filter.",
                    )
                    _render_warehouse_section(warehouse)
                    return

                _render_kpi_grid(snap.kpis)

                with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4 my-4"):
                    chart_panel(
                        title="Run Duration Trend (Seconds)",
                        subtitle="Timestamp-derived runtime (finished_at - started_at)",
                        figure=build_ops_duration_trend_figure(snap.runs),
                        source_caption="Source: cp_runs",
                    )
                    chart_panel(
                        title="Sync Outcomes per Run",
                        subtitle="Actionable synced, healed, failed & deferred file counts",
                        figure=build_ops_sync_outcomes_figure(snap.runs),
                        source_caption="Source: cp_run_sync_summaries",
                    )
                    chart_panel(
                        title="Run Status × Commit State",
                        subtitle="Independent lifecycle status vs commit state matrix",
                        figure=build_ops_status_breakdown_figure(snap.runs),
                        source_caption="Source: cp_runs",
                    )
                    chart_panel(
                        title="Core Application Version Mix",
                        subtitle="Distribution of historical runs by PFETL Core version",
                        figure=build_ops_version_timeline_figure(snap.runs),
                        source_caption="Source: cp_runs",
                    )
                    chart_panel(
                        title="Warehouse Data Contracts by Layer",
                        subtitle="Full-replace vs incremental contracts across Bronze/Silver/Gold",
                        figure=build_ops_layer_contracts_figure(warehouse.layers),
                        source_caption="Source: meta.m_Data_Contracts",
                    )
                    chart_panel(
                        title="Top Warehouse Tables by Row Count",
                        subtitle="Diagnostic row counts across Silver & Gold schemas",
                        figure=build_ops_table_row_counts_figure(warehouse.tables),
                        source_caption="Source: meta.m_Table_Row_Counts",
                    )

                run_map = {r.run_id: r for r in snap.runs}
                with ui.row().classes("w-full items-center gap-3 flex-wrap mb-2"):
                    inspect_sel = (
                        ui.select(
                            options={
                                r.run_id: f"{r.run_id[:12]}… ({r.status} · {_fmt_duration(r)})"
                                for r in snap.runs
                            },
                            value=snap.runs[0].run_id,
                            label="Inspect Run Detail & Snapshots",
                        )
                        .props("dense outlined dark")
                        .classes("w-80")
                    )
                    ui.button(
                        "Open Run & Snapshot Drawer",
                        icon="open_in_new",
                        on_click=lambda: (
                            _open_run_drawer(
                                run_map[str(inspect_sel.value)], snap.runs, profile, cache
                            )
                            if str(inspect_sel.value) in run_map
                            else None
                        ),
                    ).props("outline color=primary")

                run_cols = [
                    TableColumn(name="run_id", label="Run ID", field="run_id"),
                    TableColumn(name="started_at", label="Started At", field="started_at"),
                    TableColumn(name="duration", label="Duration", field="duration", align="right"),
                    TableColumn(name="status", label="Status", field="status"),
                    TableColumn(name="commit_state", label="Commit State", field="commit_state"),
                    TableColumn(name="core_ver", label="Core / Schema", field="core_ver"),
                    TableColumn(name="sync_status", label="Sync Status", field="sync_status"),
                    TableColumn(
                        name="snapshots", label="Snapshots (Set / Rule)", field="snapshots"
                    ),
                    TableColumn(name="log_avail", label="Log", field="log_avail"),
                ]
                run_rows = [
                    {
                        "run_id": _short_id(r.run_id),
                        "started_at": r.started_at_raw or "—",
                        "duration": _fmt_duration(r),
                        "status": r.status or "—",
                        "commit_state": r.commit_state or "—",
                        "core_ver": f"{r.application_version} / {r.schema_version}",
                        "sync_status": (
                            f"{r.sync.sync_status} ({r.sync.synced_count}/{r.sync.actionable_count})"
                            if r.sync is not None
                            else "—"
                        ),
                        "snapshots": f"{_short_id(r.settings_snapshot_id)} / {_short_id(r.rules_snapshot_id)}",
                        "log_avail": "BLOB" if r.has_execution_log else "None",
                    }
                    for r in snap.runs
                ]
                bounded_data_table(
                    columns=run_cols,
                    rows=run_rows,
                    row_key="run_id",
                    title="PFETL Run Lifecycle & Sync Summary History",
                )

                _render_warehouse_section(warehouse)

        def _ensure_tab_rendered(tab_key: str) -> None:
            if tab_key in rendered_tabs:
                return
            rendered_tabs.add(tab_key)
            if tab_key == "runs":
                _render_runs()
            elif tab_key == "sources":
                _render_sources_tab(p_sources, profile, cache)
            elif tab_key == "providers":
                _render_providers_tab(p_providers, profile, cache)
            elif tab_key == "forensics":
                _render_forensics_tab(p_forensics, profile, cache)

        _ensure_tab_rendered(valid_tab)

        def _handle_tab_change(evt: Any) -> None:
            tab_key = str(getattr(evt, "value", None) or "runs")
            _ensure_tab_rendered(tab_key)
            if on_tab_change is not None:
                on_tab_change(tab_key)

        tabs.on_value_change(_handle_tab_change)
