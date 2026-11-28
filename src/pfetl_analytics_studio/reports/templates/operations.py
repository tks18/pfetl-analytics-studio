"""Data Health & PFETL Operations built-in presentation template and block sources.

Owns
----
- `data_health_operations` built-in `ReportDefinition` (default: latest 10 runs).
- Block sources backed by `OperationsAnalytics` (`run_history`, `warehouse_health`,
  `source_registry`, `provider_requests`, `run_failures`, `snapshot_diff`) and
  `FireAnalytics.simulation_provenance` (`cp_simulation_runs`).

Does not own
------------
- Direct DuckDB/SQLite connections (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import FireAnalytics, OperationsAnalytics
from pfetl_analytics_studio.analytics.operations_models import (
    OperationRunRecord,
    ProviderRequestFilter,
    RunHistoryFilter,
)
from pfetl_analytics_studio.reports.models import (
    ChartBlock,
    KPIGroupBlock,
    NarrativeBlock,
    PageBreakBlock,
    ProvenanceBlock,
    RenderedChartBlock,
    RenderedKPIGroupBlock,
    RenderedTableBlock,
    ReportDefinition,
    ReportKpiItem,
    TableBlock,
    TitleBlock,
)
from pfetl_analytics_studio.semantic import FilterField, FilterSet, PeriodPreset, PeriodRequest
from pfetl_analytics_studio.ui import (
    build_ops_status_breakdown_figure,
    build_ops_table_row_counts_figure,
)
from pfetl_analytics_studio.ui.theme import StudioColor

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.registry import ReportRegistry, ReportSourceContext


def _first_filter_str(ctx: ReportSourceContext, field: FilterField) -> str | None:
    """Return first scalar filter value as `str` if present on `ReportContext.filters`."""
    for spec in ctx.report_context.filters.filters:
        if spec.field == field and spec.value is not None:
            raw = spec.value[0] if isinstance(spec.value, (tuple, list)) else spec.value
            return str(raw)
    return None


def _build_run_filter(ctx: ReportSourceContext) -> RunHistoryFilter:
    """Build `RunHistoryFilter` defaulting to latest 10 runs or custom date window."""
    period = ctx.report_context.period
    use_dates = period.preset == PeriodPreset.CUSTOM
    return RunHistoryFilter(
        limit=100 if use_dates else 10,
        status=_first_filter_str(ctx, FilterField.STATUS),
        commit_state=_first_filter_str(ctx, FilterField.COMMIT_STATE),
        start_date=period.start_date if use_dates else None,
        end_date=period.end_date if use_dates else None,
    )


def _snapshot_change_flags(
    runs_chronological: list[OperationRunRecord],
) -> tuple[list[bool], list[bool]]:
    """Return `(settings_changed, rules_changed)` booleans aligned to chronological runs."""
    set_flags: list[bool] = []
    rul_flags: list[bool] = []
    prev_set = ""
    prev_rul = ""
    for idx, run in enumerate(runs_chronological):
        cur_set = run.settings_snapshot_id
        cur_rul = run.rules_snapshot_id
        set_flags.append(idx > 0 and bool(cur_set) and bool(prev_set) and cur_set != prev_set)
        rul_flags.append(idx > 0 and bool(cur_rul) and bool(prev_rul) and cur_rul != prev_rul)
        if cur_set:
            prev_set = cur_set
        if cur_rul:
            prev_rul = cur_rul
    return set_flags, rul_flags


def _exec_ops_health_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Data Health & PFETL Operations 4-up KPI cards."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    run_filter = _build_run_filter(ctx)
    ops = OperationsAnalytics.run_history(
        profile,
        run_filter,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    wh = OperationsAnalytics.warehouse_health(
        profile,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    prv = OperationsAnalytics.provider_requests(
        profile,
        ProviderRequestFilter(limit=200),
        cache=ctx.cache,
    )
    fails = OperationsAnalytics.run_failures(profile, limit=200, cache=ctx.cache)
    sim = FireAnalytics.simulation_history(
        profile,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )

    runs = list(ops.runs)
    total_runs = len(runs)
    ok_runs = sum(1 for r in runs if r.status.upper() == "SUCCESS")
    success_rate = (ok_runs / total_runs * 100.0) if total_runs else 0.0
    latest = runs[0] if runs else None
    latest_dur = (
        f"{latest.duration_seconds:.1f}s"
        if latest and latest.duration_seconds is not None
        else "N/A"
    )

    synced = sum(r.sync.synced_count for r in runs if r.sync)
    healed = sum(r.sync.healed_count for r in runs if r.sync)
    failed_sync = sum(r.sync.failed_count for r in runs if r.sync)
    deferred = sum(r.sync.deferred_count for r in runs if r.sync)

    chrono = list(reversed(runs))
    set_flags, rul_flags = _snapshot_change_flags(chrono)
    config_changes = sum(1 for s, r in zip(set_flags, rul_flags, strict=True) if s or r)

    prov_total = len(prv.requests)
    prov_ok = sum(1 for req in prv.requests if req.status.upper() in ("SUCCESS", "CACHED"))
    latest_sim = sim.runs[0] if sim.runs else None

    prev_run = runs[1] if len(runs) >= 2 else None
    dur_delta = ""
    if (
        latest
        and prev_run
        and latest.duration_seconds is not None
        and prev_run.duration_seconds is not None
    ):
        diff_s = latest.duration_seconds - prev_run.duration_seconds
        dur_delta = f"{diff_s:+.1f}s vs prior run"

    items = (
        ReportKpiItem(
            label="Run Success Rate",
            value=success_rate,
            formatted_value=f"{success_rate:.0f}% ({ok_runs}/{total_runs})",
            subtitle=(f"Latest: {latest.status} · {latest_dur}" if latest else "No runs in window"),
            source_table="cp_runs",
            delta_text=dur_delta,
            breakups=(("Latest", latest_dur), ("Runs", str(total_runs))),
        ),
        ReportKpiItem(
            label="Sync Window Totals",
            value=synced,
            formatted_value=f"{synced:,} synced",
            subtitle=f"Healed {healed} · Failed {failed_sync} · Deferred {deferred}",
            source_table="cp_run_sync_summaries",
            breakups=(("Healed", str(healed)), ("Failed", str(failed_sync))),
        ),
        ReportKpiItem(
            label="Warehouse Contracts",
            value=wh.total_contracts,
            formatted_value=f"{wh.total_contracts} contracts",
            subtitle=f"{wh.total_tracked_tables} tables · {wh.total_tracked_rows:,} rows",
            source_table="meta.m_Data_Contracts",
        ),
        ReportKpiItem(
            label="Providers & Failures",
            value=prov_total,
            formatted_value=f"{prov_ok}/{prov_total} ok",
            subtitle=f"Run failures: {len(fails.failures)} · Config shifts: {config_changes}",
            source_table="cp_external_provider_requests",
        ),
        ReportKpiItem(
            label="Simulation Status",
            value=latest_sim.status if latest_sim else "N/A",
            formatted_value=latest_sim.status if latest_sim else "N/A",
            subtitle=(
                f"{latest_sim.iterations:,} iters · {latest_sim.horizon}m"
                if latest_sim and latest_sim.iterations
                else "cp_simulation_runs"
            ),
            source_table="cp_simulation_runs",
        ),
    )
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="ops.health_kpis",
        title=ctx.title_override or "PFETL Data Health & Operational KPIs",
        items=items,
        source_tables=(
            "cp_runs",
            "cp_run_sync_summaries",
            "meta.m_Data_Contracts",
            "meta.m_Table_Row_Counts",
            "cp_external_provider_requests",
            "cp_run_failures",
            "cp_simulation_runs",
        ),
    )


def _exec_run_trend_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Run Duration, Status & Settings/Rules Snapshot Change Marker chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    ops = OperationsAnalytics.run_history(
        profile,
        _build_run_filter(ctx),
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    chrono = list(reversed(ops.runs))
    labels = [r.run_id[:8] for r in chrono]
    durations = [float(r.duration_seconds or 0.0) for r in chrono]
    colors = [
        StudioColor.SUCCESS
        if r.status.upper() == "SUCCESS" and r.commit_state.upper() == "FULLY_COMMITTED"
        else StudioColor.ERROR
        if r.status.upper() == "FAILED"
        else StudioColor.WARNING
        for r in chrono
    ]
    set_flags, rul_flags = _snapshot_change_flags(chrono)

    data: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": "Duration (s)",
            "x": labels,
            "y": durations,
            "marker": {"color": colors},
            "text": [f"{r.status} / {r.commit_state}" for r in chrono],
            "hovertemplate": "Run %{x}<br>Duration: %{y:.1f}s<br>%{text}<extra></extra>",
        }
    ]

    change_x = [lbl for lbl, sc, rc in zip(labels, set_flags, rul_flags, strict=True) if sc or rc]
    change_y = [
        (dur + max(durations, default=1.0) * 0.06)
        for dur, sc, rc in zip(durations, set_flags, rul_flags, strict=True)
        if sc or rc
    ]
    change_text = [
        "Settings & Rules Changed" if sc and rc else "Settings Changed" if sc else "Rules Changed"
        for sc, rc in zip(set_flags, rul_flags, strict=True)
        if sc or rc
    ]
    if change_x:
        data.append(
            {
                "type": "scatter",
                "mode": "markers",
                "name": "Snapshot Change",
                "x": change_x,
                "y": change_y,
                "text": change_text,
                "hovertemplate": "%{x}<br>%{text}: %{y:.1f}s<extra></extra>",
                "marker": {"color": StudioColor.ACCENT, "size": 11, "symbol": "diamond"},
            }
        )

    figure = {
        "data": data,
        "layout": {
            "title": "Run Duration, Commit Health & Snapshot Change Markers",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Run ID (Chronological)"},
            "yaxis": {"title": "Duration (seconds)"},
            "legend": {"orientation": "h", "y": -0.22},
            "margin": {"t": 50, "b": 70, "l": 60, "r": 30},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="ops.run_trend_chart",
        title=ctx.title_override or "Run Duration & Snapshot Change Trend",
        figure_dict=figure,
        caption=f"Run duration trend across {len(chrono)} PFETL runs.",
        source_tables=("cp_runs", "cp_settings_snapshots", "cp_rules_snapshots"),
    )


def _exec_sync_outcome_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build stacked Sync Actionable / Synced / Healed / Failed / Deferred chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    ops = OperationsAnalytics.run_history(
        profile,
        _build_run_filter(ctx),
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    chrono = list(reversed(ops.runs))
    labels = [r.run_id[:8] for r in chrono]
    synced = [r.sync.synced_count if r.sync else 0 for r in chrono]
    healed = [r.sync.healed_count if r.sync else 0 for r in chrono]
    failed = [r.sync.failed_count if r.sync else 0 for r in chrono]
    deferred = [r.sync.deferred_count if r.sync else 0 for r in chrono]

    figure = {
        "data": [
            {
                "type": "bar",
                "name": "Synced",
                "x": labels,
                "y": synced,
                "marker": {"color": StudioColor.SUCCESS},
            },
            {
                "type": "bar",
                "name": "Healed",
                "x": labels,
                "y": healed,
                "marker": {"color": StudioColor.RECOVERY},
            },
            {
                "type": "bar",
                "name": "Deferred",
                "x": labels,
                "y": deferred,
                "marker": {"color": StudioColor.WARNING},
            },
            {
                "type": "bar",
                "name": "Failed",
                "x": labels,
                "y": failed,
                "marker": {"color": StudioColor.ERROR},
            },
        ],
        "layout": {
            "barmode": "stack",
            "title": "Sync Outcomes per Run (Synced / Healed / Deferred / Failed)",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Run ID"},
            "yaxis": {"title": "Artifact Count"},
            "legend": {"orientation": "h", "y": -0.22},
            "margin": {"t": 50, "b": 70, "l": 60, "r": 30},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="ops.sync_outcome_chart",
        title=ctx.title_override or "Sync Outcome Breakdown",
        figure_dict=figure,
        caption="Stacked sync outcomes across recent PFETL runs.",
        source_tables=("cp_run_sync_summaries",),
    )


def _exec_warehouse_health_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Warehouse Layer Contracts & Full-Replace Policy chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    wh = OperationsAnalytics.warehouse_health(
        profile,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    layers = [layer.layer.title() for layer in wh.layers]
    contracts = [layer.contract_count for layer in wh.layers]
    full_replace = [layer.full_replace_count for layer in wh.layers]

    figure = {
        "data": [
            {
                "type": "bar",
                "name": "Total Contracts",
                "x": layers,
                "y": contracts,
                "marker": {"color": StudioColor.ACCENT},
            },
            {
                "type": "bar",
                "name": "Full Replace",
                "x": layers,
                "y": full_replace,
                "marker": {"color": StudioColor.RECOVERY},
            },
        ],
        "layout": {
            "barmode": "group",
            "title": "Warehouse Data Contracts by Medallion Layer",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Layer"},
            "yaxis": {"title": "Contract Count"},
            "legend": {"orientation": "h", "y": -0.22},
            "margin": {"t": 50, "b": 70, "l": 60, "r": 30},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="ops.warehouse_health_chart",
        title=ctx.title_override or "Warehouse Contract Health by Layer",
        figure_dict=figure,
        caption="Medallion layer contract counts from meta.m_Data_Contracts.",
        source_tables=("meta.m_Data_Contracts", "meta.m_Table_Row_Counts"),
    )


def _exec_run_sync_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build bounded Run Lifecycle, Sync & Snapshot Marker table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    ops = OperationsAnalytics.run_history(
        profile,
        _build_run_filter(ctx),
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    chrono = list(reversed(ops.runs))
    set_flags, rul_flags = _snapshot_change_flags(chrono)
    marker_by_run: dict[str, str] = {}
    for r, sc, rc in zip(chrono, set_flags, rul_flags, strict=True):
        if sc and rc:
            marker_by_run[r.run_id] = "Settings + Rules"
        elif sc:
            marker_by_run[r.run_id] = "Settings"
        elif rc:
            marker_by_run[r.run_id] = "Rules"
        else:
            marker_by_run[r.run_id] = "—"

    limit = min(ctx.max_rows, 50)
    rows: list[dict[str, Any]] = []
    for r in ops.runs[:limit]:
        rows.append(
            {
                "Run ID": r.run_id[:12],
                "Started": r.started_at_raw[:19] if r.started_at_raw else "—",
                "Status": r.status or "—",
                "Commit": r.commit_state or "—",
                "Dur (s)": round(r.duration_seconds, 1) if r.duration_seconds is not None else None,
                "Actionable": r.sync.actionable_count if r.sync else 0,
                "Synced": r.sync.synced_count if r.sync else 0,
                "Healed": r.sync.healed_count if r.sync else 0,
                "Failed": r.sync.failed_count if r.sync else 0,
                "Deferred": r.sync.deferred_count if r.sync else 0,
                "Config Shift": marker_by_run.get(r.run_id, "—"),
                "Log": "Yes" if r.has_execution_log else "No",
            }
        )
    return RenderedTableBlock(
        block_id=block_id,
        source_id="ops.run_sync_table",
        title=ctx.title_override or "Recent Runs, Sync Outcomes & Snapshot Markers",
        columns=(
            "Run ID",
            "Started",
            "Status",
            "Commit",
            "Dur (s)",
            "Actionable",
            "Synced",
            "Healed",
            "Failed",
            "Deferred",
            "Config Shift",
            "Log",
        ),
        rows=tuple(rows),
        total_rows=len(ops.runs),
        truncated=len(ops.runs) > len(rows),
        source_tables=(
            "cp_runs",
            "cp_run_sync_summaries",
            "cp_settings_snapshots",
            "cp_rules_snapshots",
        ),
    )


def _exec_providers_failures_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build operational summary of Warehouse Tables, Providers, Failures & Simulations."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    wh = OperationsAnalytics.warehouse_health(
        profile,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    src = OperationsAnalytics.source_registry(profile, cache=ctx.cache)
    prv = OperationsAnalytics.provider_requests(profile, cache=ctx.cache)
    fails = OperationsAnalytics.run_failures(profile, limit=50, cache=ctx.cache)
    sim = FireAnalytics.simulation_history(
        profile,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )

    rows: list[dict[str, Any]] = []
    for tbl in wh.tables[:8]:
        rows.append(
            {
                "Domain": "Warehouse Table",
                "Item": f"{tbl.schema_name}.{tbl.table_name}",
                "Metric / Status": f"{tbl.row_count:,} rows",
                "Detail": tbl.generated_at.isoformat()[:19] if tbl.generated_at else "Latest",
            }
        )

    prov_counts: dict[str, dict[str, int]] = {}
    for req in prv.requests:
        bucket = prov_counts.setdefault(
            req.provider or "Unknown", {"attempts": 0, "ok": 0, "fail": 0}
        )
        bucket["attempts"] += req.attempt_count or 1
        if req.status.upper() in ("SUCCESS", "CACHED"):
            bucket["ok"] += 1
        else:
            bucket["fail"] += 1
    for prov_name, counts in sorted(prov_counts.items()):
        rows.append(
            {
                "Domain": "Provider",
                "Item": prov_name,
                "Metric / Status": f"{counts['ok']} ok / {counts['fail']} failed",
                "Detail": f"{counts['attempts']} total attempts",
            }
        )

    fail_domains: dict[str, int] = {}
    for failure in fails.failures:
        dom = failure.failure_domain or failure.stage or "UNKNOWN"
        fail_domains[dom] = fail_domains.get(dom, 0) + 1
    if fail_domains:
        for dom, cnt in sorted(fail_domains.items()):
            rows.append(
                {
                    "Domain": "Failure Domain",
                    "Item": dom,
                    "Metric / Status": f"{cnt} failures",
                    "Detail": "cp_run_failures",
                }
            )
    else:
        rows.append(
            {
                "Domain": "Failure Domain",
                "Item": "None",
                "Metric / Status": "0 failures",
                "Detail": "No recorded run failures",
            }
        )

    if src.category_counts:
        top_cats = ", ".join(
            f"{k}:{v}"
            for k, v in sorted(src.category_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        )
        rows.append(
            {
                "Domain": "Source Freshness",
                "Item": f"{src.total_count:,} registered files",
                "Metric / Status": ", ".join(src.available_statuses) or "—",
                "Detail": top_cats,
            }
        )

    for sim_rec in sim.runs[:3]:
        rows.append(
            {
                "Domain": "Simulation",
                "Item": sim_rec.simulation_id[:14],
                "Metric / Status": sim_rec.status or "—",
                "Detail": f"Run {sim_rec.run_id[:8]} · {sim_rec.iterations or 0:,} iters · {sim_rec.horizon or 0}m",
            }
        )

    limit = min(ctx.max_rows, 50)
    sliced = rows[:limit]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="ops.providers_failures_table",
        title=ctx.title_override or "Warehouse, Provider, Failure-Domain & Simulation Health",
        columns=("Domain", "Item", "Metric / Status", "Detail"),
        rows=tuple(sliced),
        total_rows=len(rows),
        truncated=len(rows) > len(sliced),
        source_tables=(
            "meta.m_Table_Row_Counts",
            "cp_external_provider_requests",
            "cp_run_failures",
            "cp_file_registry",
            "cp_simulation_runs",
        ),
    )


def _exec_ops_status_matrix_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = OperationsAnalytics.run_history(
        profile,
        RunHistoryFilter(limit=100),
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    fig = build_ops_status_breakdown_figure(snap.runs) if snap.runs else {}
    return RenderedChartBlock(
        block_id=block_id,
        source_id="ops.status_matrix_chart",
        title=ctx.title_override or "Run Status x Commit State Matrix",
        figure_dict=fig or {},
        source_tables=("cp_runs",),
    )


def _exec_ops_top_tables_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    wh = OperationsAnalytics.warehouse_health(
        profile, catalog_snapshot=ctx.app_context.catalog_snapshot, cache=ctx.cache
    )
    fig = build_ops_table_row_counts_figure(wh.tables) if wh.tables else {}
    return RenderedChartBlock(
        block_id=block_id,
        source_id="ops.top_tables_chart",
        title=ctx.title_override or "Top Warehouse Tables by Row Count",
        figure_dict=fig or {},
        source_tables=("meta.m_Table_Row_Counts",),
    )


DATA_HEALTH_OPERATIONS_DEF = ReportDefinition(
    id="data_health_operations",
    name="Data Health & PFETL Operations",
    description=(
        "Pipeline run success, sync health, source freshness, provider attempts, failure domains, "
        "warehouse contracts, snapshot shifts, and simulation status."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.SINCE_INCEPTION),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="ops_title",
            title="Data Health & PFETL Operations Report",
            subtitle="Control Plane Lifecycle, Sync Telemetry & Warehouse Contract Health",
        ),
        ProvenanceBlock(
            block_id="ops_provenance",
            show_snapshots=True,
            show_disclaimer=False,
        ),
        KPIGroupBlock(
            block_id="ops_kpis",
            source_id="ops.health_kpis",
            title="Operational Health Headline",
            required=True,
        ),
        NarrativeBlock(
            block_id="ops_note",
            heading="Operational Scope",
            body=(
                "Metrics are sourced directly from PFETL Control Plane SQLite and DuckDB meta tables. "
                "Row counts and contract totals measure pipeline completeness only and do not infer financial health."
            ),
        ),
        PageBreakBlock(block_id="ops_pb_1"),
        ChartBlock(
            block_id="ops_run_trend",
            source_id="ops.run_trend_chart",
            title="Run Duration & Snapshot Change Trend",
        ),
        ChartBlock(
            block_id="ops_sync_chart",
            source_id="ops.sync_outcome_chart",
            title="Sync Outcome Breakdown",
        ),
        PageBreakBlock(block_id="ops_pb_2"),
        ChartBlock(
            block_id="ops_wh_chart",
            source_id="ops.warehouse_health_chart",
            title="Warehouse Contract Health by Layer",
        ),
        ChartBlock(
            block_id="ops_status_matrix_chart",
            source_id="ops.status_matrix_chart",
            title="Run Status x Commit State Matrix",
        ),
        ChartBlock(
            block_id="ops_top_tables_chart",
            source_id="ops.top_tables_chart",
            title="Top Warehouse Tables by Row Count",
        ),
        TableBlock(
            block_id="ops_runs_table",
            source_id="ops.run_sync_table",
            title="Recent Runs, Sync Outcomes & Snapshot Markers",
            max_rows=50,
        ),
        PageBreakBlock(block_id="ops_pb_3"),
        TableBlock(
            block_id="ops_health_summary",
            source_id="ops.providers_failures_table",
            title="Warehouse, Provider, Failure-Domain & Simulation Health",
            max_rows=50,
        ),
    ),
)


def register_operations_sources(registry: ReportRegistry) -> None:
    """Register `data_health_operations` definition and its `ops.*` block sources."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="ops.health_kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="PFETL Data Health & Operational KPIs",
            source_tables=("cp_runs", "cp_run_sync_summaries", "meta.m_Data_Contracts"),
            executor=_exec_ops_health_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="ops.run_trend_chart",
            kind=ReportSourceKind.CHART,
            title="Run Duration & Snapshot Change Trend",
            source_tables=("cp_runs", "cp_settings_snapshots", "cp_rules_snapshots"),
            executor=_exec_run_trend_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="ops.sync_outcome_chart",
            kind=ReportSourceKind.CHART,
            title="Sync Outcome Breakdown",
            source_tables=("cp_run_sync_summaries",),
            executor=_exec_sync_outcome_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="ops.warehouse_health_chart",
            kind=ReportSourceKind.CHART,
            title="Warehouse Contract Health by Layer",
            source_tables=("meta.m_Data_Contracts", "meta.m_Table_Row_Counts"),
            executor=_exec_warehouse_health_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="ops.run_sync_table",
            kind=ReportSourceKind.TABLE,
            title="Recent Runs, Sync Outcomes & Snapshot Markers",
            source_tables=("cp_runs", "cp_run_sync_summaries"),
            executor=_exec_run_sync_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="ops.providers_failures_table",
            kind=ReportSourceKind.TABLE,
            title="Warehouse, Provider, Failure-Domain & Simulation Health",
            source_tables=(
                "meta.m_Table_Row_Counts",
                "cp_external_provider_requests",
                "cp_run_failures",
                "cp_file_registry",
                "cp_simulation_runs",
            ),
            executor=_exec_providers_failures_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="ops.status_matrix_chart",
            kind=ReportSourceKind.CHART,
            title="Run Status x Commit State Matrix",
            source_tables=("cp_runs",),
            executor=_exec_ops_status_matrix_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="ops.top_tables_chart",
            kind=ReportSourceKind.CHART,
            title="Top Warehouse Tables by Row Count",
            source_tables=("meta.m_Table_Row_Counts",),
            executor=_exec_ops_top_tables_chart,
        )
    )
    registry.register_definition(DATA_HEALTH_OPERATIONS_DEF)
