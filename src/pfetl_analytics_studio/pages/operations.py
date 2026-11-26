"""Operations page module — runs, sync health, warehouse freshness & forensic placeholders."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from pfetl_analytics_studio.analytics import (
    OperationRunRecord,
    OperationsAnalytics,
    OverviewKPI,
    RunHistoryFilter,
    WarehouseHealthSnapshot,
)
from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.pages import render_domain_workspace
from pfetl_analytics_studio.ui import (
    BadgeTone,
    TableColumn,
    bounded_data_table,
    build_ops_duration_trend_figure,
    build_ops_layer_contracts_figure,
    build_ops_status_breakdown_figure,
    build_ops_sync_outcomes_figure,
    build_ops_table_row_counts_figure,
    build_ops_version_timeline_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    format_metric_value,
    kpi_card,
    status_badge,
)

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


def _open_run_drawer(run: OperationRunRecord) -> None:
    """Open modal run-detail drawer linking snapshots, sync counts, and Phase 20 forensics."""
    with ui.dialog() as dlg, ui.card().classes("studio-card w-full max-w-2xl p-5 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("fact_check", size="22px").style("color: var(--studio-accent);")
                ui.label("Run Detail & Provenance").classes("text-h6 font-semibold")
            status_badge(run.status or "UNKNOWN", tone=BadgeTone.INFO)

        fields: list[tuple[str, str]] = [
            ("Run ID", run.run_id),
            ("Status", run.status or "—"),
            ("Commit State", run.commit_state or "—"),
            ("Started At", run.started_at_raw or "—"),
            ("Finished At", run.finished_at_raw or "In Progress / Unfinished"),
            ("Duration", _fmt_duration(run)),
            ("Core Version", run.application_version or "—"),
            ("Schema Version", run.schema_version or "—"),
            ("Execution Log", "Available (BLOB)" if run.has_execution_log else "Missing"),
            ("Settings Snapshot", run.settings_snapshot_id or "—"),
            ("Rules Snapshot", run.rules_snapshot_id or "—"),
            ("Sync Status", run.sync.sync_status if run.sync else "No Sync Summary"),
        ]
        with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 gap-2"):
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

        if run.sync is not None:
            with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                status_badge(f"Actionable: {run.sync.actionable_count}", tone=BadgeTone.NEUTRAL)
                status_badge(f"Synced: {run.sync.synced_count}", tone=BadgeTone.INFO)
                status_badge(f"Healed: {run.sync.healed_count}", tone=BadgeTone.INFO)
                status_badge(
                    f"Failed: {run.sync.failed_count}",
                    tone=BadgeTone.WARNING if run.sync.failed_count > 0 else BadgeTone.NEUTRAL,
                )
                status_badge(f"Deferred: {run.sync.deferred_count}", tone=BadgeTone.NEUTRAL)

        with ui.row().classes("w-full items-center justify-between pt-2"):
            ui.label("Forensic JSONL log & provider traces coming in Phase 20.").classes(
                "text-caption"
            ).style("color: var(--studio-text-muted);")
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


def _render_phase20_placeholder(title: str, description: str, sources: tuple[str, ...]) -> None:
    with ui.card().classes("studio-card w-full p-6 gap-3 rounded-lg"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("construction", size="22px").style("color: var(--studio-accent);")
            ui.label(title).classes("text-subtitle1 font-semibold")
            status_badge("Phase 20", tone=BadgeTone.NEUTRAL)
        ui.label(description).classes("text-body2").style("color: var(--studio-text-muted);")
        with ui.row().classes("items-center gap-2 flex-wrap"):
            for src in sources:
                status_badge(src, tone=BadgeTone.INFO)


def render_operations_page(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
    initial_tab: str = "runs",
    on_tab_change: Callable[[str], Any] | None = None,
) -> None:
    """Render the Phase 19 Operations workspace from shared `AppContext`."""
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
                status_badge("Operations Workspace Connected", tone=BadgeTone.INFO)

        if context.profile is None:
            render_domain_workspace(
                context=context,
                route=route,
                workspace_views=tuple(lbl for _, lbl, _ in _SUB_TABS),
                upstream_sources=(
                    "cp_runs",
                    "cp_run_sync_summaries",
                    "meta.m_Table_Row_Counts",
                    "meta.m_Data_Contracts",
                ),
            )
            return

        profile = context.profile
        filter_state: dict[str, Any] = {"status": None, "commit_state": None, "limit": 100}

        valid_tab = initial_tab if any(t[0] == initial_tab for t in _SUB_TABS) else "runs"
        with ui.tabs().classes("w-full studio-subtabs") as tabs:
            for tab_id, tab_label, tab_icon in _SUB_TABS:
                ui.tab(tab_id, label=tab_label, icon=tab_icon)
        tabs.set_value(valid_tab)
        if on_tab_change is not None:
            tabs.on_value_change(lambda e: on_tab_change(str(e.value)))

        with ui.tab_panels(tabs, value=valid_tab).classes("w-full bg-transparent"):
            p_runs = ui.tab_panel("runs").classes("p-0 pt-3")
            p_sources = ui.tab_panel("sources").classes("p-0 pt-3")
            p_providers = ui.tab_panel("providers").classes("p-0 pt-3")
            p_forensics = ui.tab_panel("forensics").classes("p-0 pt-3")

        with p_sources:
            _render_phase20_placeholder(
                "Sources & Artifacts Registry",
                "Ingestion file registry, artifact run events, and payload provenance will be wired in Phase 20.",
                ("cp_file_registry", "cp_file_payloads", "cp_artifact_run_events"),
            )
        with p_providers:
            _render_phase20_placeholder(
                "Provider Requests & Failures",
                "External market/FX provider request traces and run failure diagnostics will be wired in Phase 20.",
                ("cp_external_provider_requests", "cp_run_failures"),
            )
        with p_forensics:
            _render_phase20_placeholder(
                "Forensics & Evidence Download",
                "Decompressed execution_log JSONL viewer and archived run trace explorer will be wired in Phase 20.",
                ("cp_runs.execution_log", "logs_directory JSONL"),
            )

        def _render_runs() -> None:
            p_runs.clear()
            with p_runs:
                try:
                    snap = OperationsAnalytics.run_history(
                        profile,
                        RunHistoryFilter(
                            limit=int(filter_state["limit"]),
                            status=filter_state["status"],
                            commit_state=filter_state["commit_state"],
                        ),
                        catalog_snapshot=context.catalog_snapshot,
                        cache=cache,
                    )
                    warehouse = OperationsAnalytics.warehouse_health(
                        profile,
                        catalog_snapshot=context.catalog_snapshot,
                        cache=cache,
                    )
                except Exception as exc:
                    error_state_panel("Unable to load Operations telemetry", str(exc))
                    return

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
                            label="Inspect Run Detail",
                        )
                        .props("dense outlined dark")
                        .classes("w-80")
                    )
                    ui.button(
                        "Open Run Drawer",
                        icon="open_in_new",
                        on_click=lambda: (
                            _open_run_drawer(run_map[str(inspect_sel.value)])
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

        _render_runs()
