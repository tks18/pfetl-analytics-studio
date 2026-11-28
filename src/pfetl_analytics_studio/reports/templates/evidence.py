"""Source Evidence & Change built-in presentation template and block sources.

Owns
----
- `source_evidence_change` built-in `ReportDefinition`.
- Block sources backed by `OperationsAnalytics.source_registry`, `artifact_events`,
  `provider_requests`, and `retained_payload_sizes` (`cp_file_payloads` metadata only).

Does not own
------------
- Direct DuckDB/SQLite connections (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import OperationsAnalytics
from pfetl_analytics_studio.analytics.operations_models import (
    ArtifactEventFilter,
    SourceRegistryFilter,
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
from pfetl_analytics_studio.ui import build_ops_source_freshness_timeline_figure
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


def _exec_evidence_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Source Evidence & Change 4-up KPI cards without loading binary bytes."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    src = OperationsAnalytics.source_registry(
        profile,
        SourceRegistryFilter(
            category=_first_filter_str(ctx, FilterField.FILE_CATEGORY),
            sync_status=_first_filter_str(ctx, FilterField.SYNC_STATUS),
            limit=200,
        ),
        cache=ctx.cache,
    )
    aev = OperationsAnalytics.artifact_events(
        profile,
        ArtifactEventFilter(
            run_id=_first_filter_str(ctx, FilterField.RUN_ID),
            event_type=_first_filter_str(ctx, FilterField.EVENT_TYPE),
            limit=200,
        ),
        cache=ctx.cache,
    )
    payload_sizes = OperationsAnalytics.retained_payload_sizes(profile, cache=ctx.cache)
    retained_count = len(payload_sizes)
    retained_mb = sum(payload_sizes.values()) / (1024 * 1024)
    synced_files = sum(1 for f in src.files if f.sync_status.upper() == "SYNCED")
    converged_events = sum(1 for e in aev.events if e.convergence_result.upper() == "CONVERGED")

    items = (
        ReportKpiItem(
            label="Registered Sources",
            value=src.total_count,
            formatted_value=f"{src.total_count:,} files",
            subtitle=f"{len(src.category_counts)} categories · Sample synced: {synced_files}/{len(src.files)}",
            source_table="cp_file_registry",
            breakups=(
                ("Synced", f"{synced_files}/{len(src.files)}"),
                ("Categories", str(len(src.category_counts))),
            ),
        ),
        ReportKpiItem(
            label="Artifact Events",
            value=len(aev.events),
            formatted_value=f"{len(aev.events):,} events",
            subtitle=f"Converged: {converged_events} · Types: {len(aev.available_event_types)}",
            source_table="cp_artifact_run_events",
            breakups=(
                ("Converged", str(converged_events)),
                ("Types", str(len(aev.available_event_types))),
            ),
        ),
        ReportKpiItem(
            label="Retained Payloads",
            value=retained_count,
            formatted_value=f"{retained_count:,} payloads",
            subtitle=f"{retained_mb:.1f} MB metadata only (bytes not embedded)",
            source_table="cp_file_payloads",
        ),
        ReportKpiItem(
            label="Active Statuses",
            value=len(src.available_statuses),
            formatted_value=", ".join(src.available_statuses) or "N/A",
            subtitle=f"Convergence: {', '.join(aev.available_convergence) or 'N/A'}",
            source_table="cp_file_registry",
        ),
    )
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="evidence.kpis",
        title=ctx.title_override or "Source Registry & Evidence KPIs",
        items=items,
        source_tables=("cp_file_registry", "cp_artifact_run_events", "cp_file_payloads"),
    )


def _exec_category_freshness_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Source Registry distribution by `file_category`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    src = OperationsAnalytics.source_registry(
        profile,
        SourceRegistryFilter(
            category=_first_filter_str(ctx, FilterField.FILE_CATEGORY),
            sync_status=_first_filter_str(ctx, FilterField.SYNC_STATUS),
            limit=200,
        ),
        cache=ctx.cache,
    )
    items = sorted(src.category_counts.items(), key=lambda kv: kv[1], reverse=True)[:14]
    ordered = list(reversed(items))
    figure = {
        "data": [
            {
                "type": "bar",
                "orientation": "h",
                "name": "Registered Files",
                "x": [v for _, v in ordered],
                "y": [k for k, _ in ordered],
                "marker": {"color": StudioColor.ACCENT},
            }
        ],
        "layout": {
            "title": "Registered Source Artifacts by Category",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "File Count"},
            "yaxis": {"automargin": True},
            "margin": {"t": 50, "b": 55, "l": 160, "r": 30},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="evidence.category_freshness_chart",
        title=ctx.title_override or "Source Registry by Category",
        figure_dict=figure,
        caption="Distribution of registered source files across file categories.",
        source_tables=("cp_file_registry",),
    )


def _exec_artifact_events_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Artifact Lifecycle Events & Convergence breakdown chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    aev = OperationsAnalytics.artifact_events(
        profile,
        ArtifactEventFilter(
            run_id=_first_filter_str(ctx, FilterField.RUN_ID),
            event_type=_first_filter_str(ctx, FilterField.EVENT_TYPE),
            limit=200,
        ),
        cache=ctx.cache,
    )
    by_type: dict[str, int] = {}
    by_conv: dict[str, int] = {}
    for ev in aev.events:
        et = ev.event_type or "UNKNOWN"
        cv = ev.convergence_result or "UNKNOWN"
        by_type[et] = by_type.get(et, 0) + 1
        by_conv[cv] = by_conv.get(cv, 0) + 1

    type_items = sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)
    conv_items = sorted(by_conv.items(), key=lambda kv: kv[1], reverse=True)

    figure = {
        "data": [
            {
                "type": "bar",
                "name": "Event Type",
                "x": [k for k, _ in type_items],
                "y": [v for _, v in type_items],
                "marker": {"color": StudioColor.RECOVERY},
            },
            {
                "type": "bar",
                "name": "Convergence",
                "x": [k for k, _ in conv_items],
                "y": [v for _, v in conv_items],
                "marker": {"color": StudioColor.SUCCESS},
            },
        ],
        "layout": {
            "title": "Artifact Change Events & Convergence Outcomes",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Event Type / Convergence"},
            "yaxis": {"title": "Count"},
            "legend": {"orientation": "h", "y": -0.22},
            "margin": {"t": 50, "b": 75, "l": 60, "r": 30},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="evidence.artifact_events_chart",
        title=ctx.title_override or "Artifact Change & Convergence Breakdown",
        figure_dict=figure,
        caption="Artifact lifecycle events by type and convergence result.",
        source_tables=("cp_artifact_run_events",),
    )


def _exec_artifact_events_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build bounded Artifact Change, Hash, Replay & Convergence Events table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    limit = min(ctx.max_rows, 60)
    aev = OperationsAnalytics.artifact_events(
        profile,
        ArtifactEventFilter(
            run_id=_first_filter_str(ctx, FilterField.RUN_ID),
            event_type=_first_filter_str(ctx, FilterField.EVENT_TYPE),
            limit=limit,
        ),
        cache=ctx.cache,
    )
    rows: list[dict[str, Any]] = []
    for ev in aev.events[:limit]:
        transition = (
            f"{ev.previous_status} -> {ev.new_status}"
            if ev.previous_status
            else (ev.new_status or "—")
        )
        rows.append(
            {
                "Event At": ev.event_at_raw[:19] if ev.event_at_raw else "—",
                "Run ID": ev.run_id[:10] if ev.run_id else "—",
                "File ID": ev.file_id[:10] if ev.file_id else "—",
                "Event Type": ev.event_type or "—",
                "Transition": transition,
                "Hash": ev.content_hash[:10] if ev.content_hash else "—",
                "Affected Table": ev.affected_table or "—",
                "Replay Run": ev.replay_run_id[:10] if ev.replay_run_id else "—",
                "Convergence": ev.convergence_result or "—",
            }
        )
    return RenderedTableBlock(
        block_id=block_id,
        source_id="evidence.artifact_events_table",
        title=ctx.title_override or "Artifact Lifecycle, Hash & Convergence Events",
        columns=(
            "Event At",
            "Run ID",
            "File ID",
            "Event Type",
            "Transition",
            "Hash",
            "Affected Table",
            "Replay Run",
            "Convergence",
        ),
        rows=tuple(rows),
        total_rows=len(aev.events),
        truncated=len(aev.events) > len(rows),
        source_tables=("cp_artifact_run_events",),
    )


def _exec_payload_availability_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Retained-Payload Availability & Source Registry table (metadata only)."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")

    limit = min(ctx.max_rows, 60)
    src = OperationsAnalytics.source_registry(
        profile,
        SourceRegistryFilter(
            category=_first_filter_str(ctx, FilterField.FILE_CATEGORY),
            sync_status=_first_filter_str(ctx, FilterField.SYNC_STATUS),
            limit=limit,
        ),
        cache=ctx.cache,
    )
    sliced = src.files[:limit]
    sizes = OperationsAnalytics.retained_payload_sizes(
        profile,
        file_ids=[f.file_id for f in sliced],
        cache=ctx.cache,
    )
    rows: list[dict[str, Any]] = []
    for f in sliced:
        byte_len = sizes.get(f.file_id)
        rows.append(
            {
                "File ID": f.file_id[:12],
                "File Name": f.file_name or "—",
                "Category": f.file_category or "—",
                "Sync Status": f.sync_status or "—",
                "Size (Bytes)": f.file_size_bytes,
                "Hash": f.file_hash[:10] if f.file_hash else "—",
                "Last Synced Run": f.last_synced_run_id[:10] if f.last_synced_run_id else "—",
                "Retained Payload": f"Yes ({byte_len:,} B)" if byte_len is not None else "No",
            }
        )
    return RenderedTableBlock(
        block_id=block_id,
        source_id="evidence.payload_availability_table",
        title=ctx.title_override
        or "Source Registry & Retained-Payload Availability (Metadata Only)",
        columns=(
            "File ID",
            "File Name",
            "Category",
            "Sync Status",
            "Size (Bytes)",
            "Hash",
            "Last Synced Run",
            "Retained Payload",
        ),
        rows=tuple(rows),
        total_rows=src.total_count,
        truncated=src.total_count > len(rows),
        source_tables=("cp_file_registry", "cp_file_payloads"),
    )


def _exec_freshness_timeline_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    src = OperationsAnalytics.source_registry(
        profile, SourceRegistryFilter(limit=250), cache=ctx.cache
    )
    fig = build_ops_source_freshness_timeline_figure(src.files) if src.files else {}
    return RenderedChartBlock(
        block_id=block_id,
        source_id="evidence.freshness_timeline_chart",
        title=ctx.title_override or "Source File Freshness Timeline",
        figure_dict=fig or {},
        source_tables=("cp_file_registry",),
    )


SOURCE_EVIDENCE_CHANGE_DEF = ReportDefinition(
    id="source_evidence_change",
    name="Source Evidence & Change Report",
    description=(
        "Source registry freshness, artifact added/changed/removed/replayed/converged events, "
        "content hashes, affected tables, and retained-payload availability without embedding binary bytes."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.SINCE_INCEPTION),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="evidence_title",
            title="Source Evidence & Change Report",
            subtitle="Artifact Registry, Lifecycle Convergence & Retained-Payload Availability",
        ),
        ProvenanceBlock(
            block_id="evidence_provenance",
            show_snapshots=True,
            show_disclaimer=False,
        ),
        KPIGroupBlock(
            block_id="evidence_kpis",
            source_id="evidence.kpis",
            title="Source Evidence Headline",
            required=True,
        ),
        NarrativeBlock(
            block_id="evidence_note",
            heading="Payload Retention Policy",
            body=(
                "Retained binary source payloads in cp_file_payloads are never embedded automatically "
                "into reports; this report records availability, identity, and byte lengths only."
            ),
        ),
        PageBreakBlock(block_id="evidence_pb_1"),
        ChartBlock(
            block_id="evidence_cat_chart",
            source_id="evidence.category_freshness_chart",
            title="Source Registry by Category",
        ),
        ChartBlock(
            block_id="evidence_events_chart",
            source_id="evidence.artifact_events_chart",
            title="Artifact Change & Convergence Breakdown",
        ),
        ChartBlock(
            block_id="evidence_freshness_chart",
            source_id="evidence.freshness_timeline_chart",
            title="Source File Freshness Timeline",
        ),
        PageBreakBlock(block_id="evidence_pb_2"),
        TableBlock(
            block_id="evidence_events_table",
            source_id="evidence.artifact_events_table",
            title="Artifact Lifecycle, Hash & Convergence Events",
            max_rows=60,
        ),
        PageBreakBlock(block_id="evidence_pb_3"),
        TableBlock(
            block_id="evidence_payload_table",
            source_id="evidence.payload_availability_table",
            title="Source Registry & Retained-Payload Availability (Metadata Only)",
            max_rows=60,
        ),
    ),
)


def register_evidence_sources(registry: ReportRegistry) -> None:
    """Register `source_evidence_change` definition and its `evidence.*` block sources."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="evidence.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Source Registry & Evidence KPIs",
            source_tables=("cp_file_registry", "cp_artifact_run_events", "cp_file_payloads"),
            executor=_exec_evidence_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="evidence.category_freshness_chart",
            kind=ReportSourceKind.CHART,
            title="Source Registry by Category",
            source_tables=("cp_file_registry",),
            executor=_exec_category_freshness_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="evidence.artifact_events_chart",
            kind=ReportSourceKind.CHART,
            title="Artifact Change & Convergence Breakdown",
            source_tables=("cp_artifact_run_events",),
            executor=_exec_artifact_events_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="evidence.artifact_events_table",
            kind=ReportSourceKind.TABLE,
            title="Artifact Lifecycle, Hash & Convergence Events",
            source_tables=("cp_artifact_run_events",),
            executor=_exec_artifact_events_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="evidence.payload_availability_table",
            kind=ReportSourceKind.TABLE,
            title="Source Registry & Retained-Payload Availability",
            source_tables=("cp_file_registry", "cp_file_payloads"),
            executor=_exec_payload_availability_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="evidence.freshness_timeline_chart",
            kind=ReportSourceKind.CHART,
            title="Source File Freshness Timeline",
            source_tables=("cp_file_registry",),
            executor=_exec_freshness_timeline_chart,
        )
    )
    registry.register_definition(SOURCE_EVIDENCE_CHANGE_DEF)
