"""Run Forensic built-in presentation template and block sources.

Owns
----
- `run_forensic` built-in `ReportDefinition` (pinned to a single PFETL run).
- Block sources backed by `OperationsAnalytics.forensic_summary`, `forensic_events`,
  `operation_tree`, and `run_failures`.

Does not own
------------
- Direct DuckDB/SQLite connections (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import OperationsAnalytics
from pfetl_analytics_studio.analytics.operations_models import (
    ForensicEventFilter,
    OperationTreeNode,
)
from pfetl_analytics_studio.reports.models import (
    ChartBlock,
    KPIGroupBlock,
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


def _resolve_target_run_id(ctx: ReportSourceContext) -> str:
    """Resolve the selected PFETL run ID from `FilterField.RUN_ID` or `ReportContext`."""
    return _first_filter_str(ctx, FilterField.RUN_ID) or ctx.report_context.pfetl_run_id


def _build_forensic_filter(ctx: ReportSourceContext, limit: int = 500) -> ForensicEventFilter:
    """Build `ForensicEventFilter` from whitelisted report context filters."""
    return ForensicEventFilter(
        severity=_first_filter_str(ctx, FilterField.SEVERITY),
        event_code=_first_filter_str(ctx, FilterField.EVENT_CODE),
        stage=_first_filter_str(ctx, FilterField.STAGE),
        component=_first_filter_str(ctx, FilterField.COMPONENT),
        provider=_first_filter_str(ctx, FilterField.PROVIDER),
        worker_id=_first_filter_str(ctx, FilterField.WORKER_ID),
        failure_domain=_first_filter_str(ctx, FilterField.FAILURE_DOMAIN),
        limit=limit,
    )


def _exec_forensic_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Run Forensic 4-up KPI cards strictly from emitted JSONL fields."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    run_id = _resolve_target_run_id(ctx)
    summary = OperationsAnalytics.forensic_summary(profile, run_id)
    error_count = summary.severity_counts.get("ERROR", 0) + summary.severity_counts.get(
        "CRITICAL", 0
    )

    items = (
        ReportKpiItem(
            label="Emitted Events",
            value=summary.total_events,
            formatted_value=f"{summary.total_events:,}",
            subtitle=f"Source: {summary.source} · Malformed: {summary.malformed_lines}",
            source_table="cp_runs.execution_log",
        ),
        ReportKpiItem(
            label="Rows Emitted",
            value=summary.total_rows_emitted,
            formatted_value=f"{summary.total_rows_emitted:,}",
            subtitle=f"{len(summary.stage_counts)} stages · {len(summary.provider_counts)} providers",
            source_table="cp_runs.execution_log",
        ),
        ReportKpiItem(
            label="Retries & Cache",
            value=summary.cache_hits,
            formatted_value=f"{summary.cache_hits} hits / {summary.cache_misses} miss",
            subtitle=f"Retries: {summary.total_retries} · Errors: {error_count}",
            source_table="cp_runs.execution_log",
        ),
        ReportKpiItem(
            label="Snapshot IDs",
            value=ctx.report_context.settings_snapshot_id or "N/A",
            formatted_value=(ctx.report_context.settings_snapshot_id or "N/A")[:16],
            subtitle=f"Rules: {(ctx.report_context.rules_snapshot_id or 'N/A')[:16]}",
            source_table="cp_settings_snapshots",
        ),
    )
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="forensic.kpis",
        title=ctx.title_override or f"Run Forensic Summary ({run_id[:12]})",
        items=items,
        source_tables=("cp_runs.execution_log", "cp_settings_snapshots", "cp_rules_snapshots"),
    )


def _exec_severity_outcome_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Event Counts by Severity and Outcome chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    run_id = _resolve_target_run_id(ctx)
    ev_snap = OperationsAnalytics.forensic_events(
        profile,
        run_id,
        _build_forensic_filter(ctx, limit=5000),
    )
    sev_counts: dict[str, int] = {}
    out_counts: dict[str, int] = {}
    for ev in ev_snap.events:
        sev = ev.severity or "UNSPECIFIED"
        out = ev.outcome or "UNSPECIFIED"
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
        out_counts[out] = out_counts.get(out, 0) + 1

    sev_items = sorted(sev_counts.items(), key=lambda kv: kv[1], reverse=True)
    out_items = sorted(out_counts.items(), key=lambda kv: kv[1], reverse=True)

    figure = {
        "data": [
            {
                "type": "bar",
                "name": "By Severity",
                "x": [f"Sev: {k}" for k, _ in sev_items],
                "y": [v for _, v in sev_items],
                "marker": {"color": StudioColor.ACCENT},
            },
            {
                "type": "bar",
                "name": "By Outcome",
                "x": [f"Out: {k}" for k, _ in out_items],
                "y": [v for _, v in out_items],
                "marker": {"color": StudioColor.RECOVERY},
            },
        ],
        "layout": {
            "title": "Filtered Event Distribution by Severity & Outcome",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Classification"},
            "yaxis": {"title": "Event Count"},
            "legend": {"orientation": "h", "y": -0.22},
            "margin": {"t": 50, "b": 80, "l": 60, "r": 30},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="forensic.severity_outcome_chart",
        title=ctx.title_override or "Event Counts by Severity & Outcome",
        figure_dict=figure,
        caption=f"Severity and outcome distribution across {len(ev_snap.events)} forensic events.",
        source_tables=("cp_runs.execution_log",),
    )


def _exec_stage_timeline_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Stage Event Count & Rows Emitted chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    run_id = _resolve_target_run_id(ctx)
    summary = OperationsAnalytics.forensic_summary(profile, run_id)
    stages = list(summary.stage_counts.keys())
    counts = [summary.stage_counts[s] for s in stages]
    rows = [summary.stage_rows.get(s, 0) for s in stages]

    figure = {
        "data": [
            {
                "type": "bar",
                "name": "Events",
                "x": stages,
                "y": counts,
                "marker": {"color": StudioColor.ACCENT},
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Rows Emitted",
                "x": stages,
                "y": rows,
                "yaxis": "y2",
                "line": {"color": StudioColor.SUCCESS, "width": 2.5},
            },
        ],
        "layout": {
            "title": "Pipeline Stage Activity & Emitted Rows",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Stage"},
            "yaxis": {"title": "Events"},
            "yaxis2": {"title": "Rows", "overlaying": "y", "side": "right"},
            "legend": {"orientation": "h", "y": -0.22},
            "margin": {"t": 50, "b": 80, "l": 60, "r": 65},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="forensic.stage_timeline_chart",
        title=ctx.title_override or "Stage Timeline & Rows Emitted",
        figure_dict=figure,
        caption="Stage-by-stage event count and rows emitted.",
        source_tables=("cp_runs.execution_log",),
    )


def _exec_slow_ops_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Slow Emitted Operations chart strictly from emitted `duration_ms`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    run_id = _resolve_target_run_id(ctx)
    summary = OperationsAnalytics.forensic_summary(profile, run_id)
    emitted = [op for op in summary.slowest_operations if op.duration_ms is not None][:12]
    ordered = list(reversed(emitted))
    labels = [f"{op.operation or op.operation_id} ({op.stage})" for op in ordered]
    durations = [float(op.duration_ms) for op in ordered if op.duration_ms is not None]

    figure = {
        "data": [
            {
                "type": "bar",
                "orientation": "h",
                "name": "Duration (ms)",
                "x": durations,
                "y": labels,
                "marker": {"color": StudioColor.WARNING},
            }
        ],
        "layout": {
            "title": "Slowest Emitted Operations by duration_ms",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Emitted Duration (ms)"},
            "yaxis": {"automargin": True},
            "margin": {"t": 50, "b": 55, "l": 180, "r": 30},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="forensic.slow_ops_chart",
        title=ctx.title_override or "Slow Emitted Operations (duration_ms)",
        figure_dict=figure,
        caption=f"Top {len(emitted)} slowest emitted operations by duration_ms.",
        source_tables=("cp_runs.execution_log",),
    )


def _flatten_tree(nodes: tuple[OperationTreeNode, ...], depth: int = 0) -> list[dict[str, Any]]:
    """Flatten parent-child `OperationTreeNode` hierarchy preserving parent-child order."""
    out: list[dict[str, Any]] = []
    for node in nodes:
        indent = "  " * depth
        out.append(
            {
                "Source": "Orphan Op" if node.is_orphan else f"Op (d={depth})",
                "Operation": f"{indent}{node.operation or node.operation_id}",
                "Stage": node.stage or "—",
                "Outcome": node.outcome or "—",
                "Duration (ms)": round(node.duration_ms, 2)
                if node.duration_ms is not None
                else None,
                "Rows": node.rows,
                "Detail": f"events={node.event_count} · parent={node.parent_operation_id or 'ROOT'}",
            }
        )
        if node.children:
            out.extend(_flatten_tree(node.children, depth + 1))
    return out


def _exec_lineage_failures_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Parent-Child Operation Lineage & Failures/Remediation table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    run_id = _resolve_target_run_id(ctx)
    tree = OperationsAnalytics.operation_tree(profile, run_id)
    fails = OperationsAnalytics.run_failures(profile, run_id=run_id, limit=50, cache=ctx.cache)

    rows = _flatten_tree(tree)
    for fail in fails.failures:
        rows.append(
            {
                "Source": "Failure",
                "Operation": fail.error_type or "Error",
                "Stage": fail.stage or "—",
                "Outcome": fail.failure_domain or "FAILED",
                "Duration (ms)": None,
                "Rows": None,
                "Detail": fail.error_message[:140],
            }
        )

    limit = min(ctx.max_rows, 80)
    sliced = rows[:limit]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="forensic.lineage_failures_table",
        title=ctx.title_override or "Parent-Child Operation Lineage & Failures",
        columns=("Source", "Operation", "Stage", "Outcome", "Duration (ms)", "Rows", "Detail"),
        rows=tuple(sliced),
        total_rows=len(rows),
        truncated=len(rows) > len(sliced),
        source_tables=("cp_runs.execution_log", "cp_run_failures"),
    )


def _exec_events_appendix_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build bounded Forensic Events Appendix strictly ordered by `(sequence, timestamp)`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    run_id = _resolve_target_run_id(ctx)
    limit = min(ctx.max_rows, 100)
    ev_snap = OperationsAnalytics.forensic_events(
        profile,
        run_id,
        _build_forensic_filter(ctx, limit=limit),
    )

    rows: list[dict[str, Any]] = []
    for ev in ev_snap.events[:limit]:
        rows.append(
            {
                "Seq": ev.sequence,
                "Timestamp": ev.timestamp_raw[:23] if ev.timestamp_raw else "—",
                "Severity": ev.severity or "—",
                "Code": ev.event_code or "—",
                "Outcome": ev.outcome or "—",
                "Stage": ev.context.stage or "—",
                "Operation": ev.context.operation or "—",
                "Dur (ms)": round(ev.context.duration_ms, 2)
                if ev.context.duration_ms is not None
                else None,
                "Rows": ev.context.rows,
                "Message": ev.message[:100] if ev.message else "—",
            }
        )
    return RenderedTableBlock(
        block_id=block_id,
        source_id="forensic.events_appendix_table",
        title=ctx.title_override or "Bounded Forensic Events Appendix (Sequence Ordered)",
        columns=(
            "Seq",
            "Timestamp",
            "Severity",
            "Code",
            "Outcome",
            "Stage",
            "Operation",
            "Dur (ms)",
            "Rows",
            "Message",
        ),
        rows=tuple(rows),
        total_rows=ev_snap.total_decoded,
        truncated=ev_snap.total_decoded > len(rows),
        source_tables=("cp_runs.execution_log",),
    )


RUN_FORENSIC_DEF = ReportDefinition(
    id="run_forensic",
    name="Run Forensic Report",
    description=(
        "Single-run forensic telemetry: severity/code/outcome counts, stage timeline, "
        "slowest emitted operations, parent-child lineage, failures, and sequence-ordered appendix."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.SINCE_INCEPTION),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="forensic_title",
            title="PFETL Run Forensic Report",
            subtitle="Structured Execution Telemetry, Lineage & Emitted Diagnostics",
        ),
        ProvenanceBlock(
            block_id="forensic_provenance",
            show_snapshots=True,
            show_disclaimer=False,
        ),
        KPIGroupBlock(
            block_id="forensic_kpis",
            source_id="forensic.kpis",
            title="Run Forensic Headline",
            required=True,
        ),
        PageBreakBlock(block_id="forensic_pb_1"),
        ChartBlock(
            block_id="forensic_sev_chart",
            source_id="forensic.severity_outcome_chart",
            title="Event Counts by Severity & Outcome",
        ),
        ChartBlock(
            block_id="forensic_stage_chart",
            source_id="forensic.stage_timeline_chart",
            title="Stage Timeline & Rows Emitted",
        ),
        PageBreakBlock(block_id="forensic_pb_2"),
        ChartBlock(
            block_id="forensic_slow_chart",
            source_id="forensic.slow_ops_chart",
            title="Slow Emitted Operations (duration_ms)",
        ),
        TableBlock(
            block_id="forensic_lineage_table",
            source_id="forensic.lineage_failures_table",
            title="Parent-Child Operation Lineage & Failures",
            max_rows=60,
        ),
        PageBreakBlock(block_id="forensic_pb_3"),
        TableBlock(
            block_id="forensic_appendix_table",
            source_id="forensic.events_appendix_table",
            title="Bounded Forensic Events Appendix (Sequence Ordered)",
            max_rows=100,
        ),
    ),
)


def register_forensic_sources(registry: ReportRegistry) -> None:
    """Register `run_forensic` definition and its `forensic.*` block sources."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="forensic.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Run Forensic KPIs",
            source_tables=("cp_runs.execution_log", "cp_settings_snapshots", "cp_rules_snapshots"),
            executor=_exec_forensic_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="forensic.severity_outcome_chart",
            kind=ReportSourceKind.CHART,
            title="Event Counts by Severity & Outcome",
            source_tables=("cp_runs.execution_log",),
            executor=_exec_severity_outcome_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="forensic.stage_timeline_chart",
            kind=ReportSourceKind.CHART,
            title="Stage Timeline & Rows Emitted",
            source_tables=("cp_runs.execution_log",),
            executor=_exec_stage_timeline_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="forensic.slow_ops_chart",
            kind=ReportSourceKind.CHART,
            title="Slow Emitted Operations (duration_ms)",
            source_tables=("cp_runs.execution_log",),
            executor=_exec_slow_ops_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="forensic.lineage_failures_table",
            kind=ReportSourceKind.TABLE,
            title="Parent-Child Operation Lineage & Failures",
            source_tables=("cp_runs.execution_log", "cp_run_failures"),
            executor=_exec_lineage_failures_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="forensic.events_appendix_table",
            kind=ReportSourceKind.TABLE,
            title="Bounded Forensic Events Appendix",
            source_tables=("cp_runs.execution_log",),
            executor=_exec_events_appendix_table,
        )
    )
    registry.register_definition(RUN_FORENSIC_DEF)
