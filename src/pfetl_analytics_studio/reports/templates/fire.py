"""FIRE & Planning Review built-in presentation template and block sources.

Owns
----
- `fire_planning_review` built-in `ReportDefinition` (default: Latest Complete Month).
- Block sources backed by `FireAnalytics.summary` and `FireAnalytics.simulation_history`.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or Monte Carlo simulation logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import FireAnalytics
from pfetl_analytics_studio.reports.comparison import kpi_items_from_overview_kpis
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
    TableBlock,
    TitleBlock,
)
from pfetl_analytics_studio.semantic import FilterSet, PeriodPreset, PeriodRequest
from pfetl_analytics_studio.ui import (
    build_fire_corpus_targets_figure,
    build_fire_gap_coverage_figure,
    build_fire_monte_carlo_band_figure,
    build_fire_runway_comparison_figure,
    build_fire_savings_rate_figure,
)
from pfetl_analytics_studio.ui.theme import StudioColor

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.registry import ReportRegistry, ReportSourceContext


def _exec_fire_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build FIRE & Planning KPI cards from `FireAnalytics.summary`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = FireAnalytics.summary(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    items = kpi_items_from_overview_kpis(snap.kpis, snap.currency_id, limit=8)
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="fire.kpis",
        title=ctx.title_override or f"FIRE & Runway KPIs ({snap.period_label})",
        items=tuple(items),
        source_tables=("gold.wealth_fire_analytics", "meta.m_Latest_Simulation"),
    )


def _exec_fire_trajectory_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build FI Coverage % & Success Probability trajectory chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = FireAnalytics.summary(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    dates = [
        pt.month_end_date.isoformat() if pt.month_end_date else pt.year_month for pt in snap.history
    ]
    coverage = [pt.current_fi_coverage_pct or 0.0 for pt in snap.history]
    success_prob = [pt.probability_of_success_pct or 0.0 for pt in snap.history]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "FI Coverage (%)",
                "x": dates,
                "y": coverage,
                "line": {"color": StudioColor.ACCENT, "width": 2.5},
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "MC Success Probability (%)",
                "x": dates,
                "y": success_prob,
                "line": {"color": StudioColor.SUCCESS, "width": 2.2, "dash": "dot"},
            },
        ],
        "layout": {
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Month End"},
            "yaxis": {"title": "Percentage (%)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="fire.trajectory_chart",
        title=ctx.title_override or f"FI Coverage & Monte Carlo Probability ({snap.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.wealth_fire_analytics",),
        caption=f"Period: {snap.period_label} · Source: gold.wealth_fire_analytics",
    )


def _exec_simulation_provenance_table(
    ctx: ReportSourceContext, block_id: str
) -> RenderedTableBlock:
    """Build Simulation Provenance & Reproducibility Fingerprints table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    sim = FireAnalytics.simulation_history(
        profile=profile,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    rows: list[dict[str, Any]] = [
        {
            "Simulation ID": r.simulation_id,
            "Run ID": r.run_id or "—",
            "Seed": r.root_seed,
            "Iterations": r.iterations,
            "Horizon": r.horizon,
            "As-Of": r.input_as_of_date.isoformat() if r.input_as_of_date else "—",
            "Status": r.status or "—",
            "Model Version": r.model_implementation_version or "—",
        }
        for r in sim.runs
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="fire.simulation_provenance_table",
        title=ctx.title_override or "Simulation Provenance & Reproducibility History",
        columns=(
            "Simulation ID",
            "Run ID",
            "Seed",
            "Iterations",
            "Horizon",
            "As-Of",
            "Status",
            "Model Version",
        ),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("cp_simulation_runs", "meta.m_Latest_Simulation"),
    )


def _load_fire_snap(ctx: ReportSourceContext) -> Any:
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    return FireAnalytics.summary(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )


def _exec_fire_corpus_targets_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    snap = _load_fire_snap(ctx)
    fig = (
        build_fire_corpus_targets_figure(snap.history, currency_id=snap.currency_id)
        if snap.history
        else {}
    )
    return RenderedChartBlock(
        block_id=block_id,
        source_id="fire.corpus_targets_chart",
        title=ctx.title_override or "FI Corpus Targets vs Current Wealth",
        figure_dict=fig or {},
        source_tables=("gold.wealth_fire_analytics",),
    )


def _exec_fire_gap_coverage_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    snap = _load_fire_snap(ctx)
    fig = (
        build_fire_gap_coverage_figure(snap.history, currency_id=snap.currency_id)
        if snap.history
        else {}
    )
    return RenderedChartBlock(
        block_id=block_id,
        source_id="fire.gap_coverage_chart",
        title=ctx.title_override or "FI Corpus Gap & Coverage %",
        figure_dict=fig or {},
        source_tables=("gold.wealth_fire_analytics",),
    )


def _exec_fire_mc_band_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    snap = _load_fire_snap(ctx)
    fig = build_fire_monte_carlo_band_figure(snap.history) if snap.history else {}
    return RenderedChartBlock(
        block_id=block_id,
        source_id="fire.mc_band_chart",
        title=ctx.title_override or "Monte Carlo Months to FI (P10 / P50 / P90)",
        figure_dict=fig or {},
        source_tables=("gold.wealth_fire_analytics",),
    )


def _exec_fire_runway_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    snap = _load_fire_snap(ctx)
    fig = build_fire_runway_comparison_figure(snap.history) if snap.history else {}
    return RenderedChartBlock(
        block_id=block_id,
        source_id="fire.runway_chart",
        title=ctx.title_override or "Portfolio Runway Comparison (Months)",
        figure_dict=fig or {},
        source_tables=("gold.wealth_fire_analytics",),
    )


def _exec_fire_savings_rate_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    snap = _load_fire_snap(ctx)
    fig = build_fire_savings_rate_figure(snap.history) if snap.history else {}
    return RenderedChartBlock(
        block_id=block_id,
        source_id="fire.savings_rate_chart",
        title=ctx.title_override or "Savings Rate vs Required & SWR",
        figure_dict=fig or {},
        source_tables=("gold.wealth_fire_analytics",),
    )


FIRE_PLANNING_REVIEW_DEF = ReportDefinition(
    id="fire_planning_review",
    name="FIRE & Planning Review",
    description=(
        "Latest-month FIRE deck: Target/Lean/Coast FI, P10/P50/P90 months to FI, "
        "success probability, savings gap, velocity, and simulation provenance."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="fire_title",
            title="FIRE & Planning Review",
            subtitle="FI Targets, Monte Carlo Horizons & Simulation Provenance",
        ),
        ProvenanceBlock(block_id="fire_prov", show_snapshots=True, show_disclaimer=True),
        NarrativeBlock(
            block_id="fire_scope",
            heading="Canonical PFETL Simulation Outputs",
            body=(
                "All FI targets, P10/P50/P90 horizons, success probabilities, and withdrawal "
                "metrics come directly from PFETL Gold/Meta outputs without Studio Monte Carlo."
            ),
        ),
        KPIGroupBlock(
            block_id="fire_kpis",
            source_id="fire.kpis",
            title="FIRE Targets & Probability KPIs",
        ),
        PageBreakBlock(block_id="pb_fire_1"),
        ChartBlock(
            block_id="fire_chart",
            source_id="fire.trajectory_chart",
            title="FI Coverage & Success Probability Trajectory",
        ),
        ChartBlock(
            block_id="fire_corpus_targets_chart",
            source_id="fire.corpus_targets_chart",
            title="FI Corpus Targets vs Current Wealth",
        ),
        ChartBlock(
            block_id="fire_gap_coverage_chart",
            source_id="fire.gap_coverage_chart",
            title="FI Corpus Gap & Coverage %",
        ),
        ChartBlock(
            block_id="fire_mc_band_chart",
            source_id="fire.mc_band_chart",
            title="Monte Carlo Months to FI (P10 / P50 / P90)",
        ),
        ChartBlock(
            block_id="fire_runway_chart",
            source_id="fire.runway_chart",
            title="Portfolio Runway Comparison (Months)",
        ),
        ChartBlock(
            block_id="fire_savings_rate_chart",
            source_id="fire.savings_rate_chart",
            title="Savings Rate vs Required & SWR",
        ),
        PageBreakBlock(block_id="pb_fire_2"),
        TableBlock(
            block_id="fire_sim_tbl",
            source_id="fire.simulation_provenance_table",
            title="Simulation Provenance & Reproducibility Fingerprints",
            max_rows=25,
        ),
    ),
)


def register_fire_sources(registry: ReportRegistry) -> None:
    """Register FIRE & Planning Review sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="fire.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="FIRE Targets & Probability KPIs",
            source_tables=("gold.wealth_fire_analytics", "meta.m_Latest_Simulation"),
            executor=_exec_fire_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="fire.trajectory_chart",
            kind=ReportSourceKind.CHART,
            title="FI Coverage & Success Trajectory",
            source_tables=("gold.wealth_fire_analytics",),
            executor=_exec_fire_trajectory_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="fire.simulation_provenance_table",
            kind=ReportSourceKind.TABLE,
            title="Simulation Provenance History",
            source_tables=("cp_simulation_runs", "meta.m_Latest_Simulation"),
            executor=_exec_simulation_provenance_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="fire.corpus_targets_chart",
            kind=ReportSourceKind.CHART,
            title="FI Corpus Targets vs Current Wealth",
            source_tables=("gold.wealth_fire_analytics",),
            executor=_exec_fire_corpus_targets_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="fire.gap_coverage_chart",
            kind=ReportSourceKind.CHART,
            title="FI Corpus Gap & Coverage %",
            source_tables=("gold.wealth_fire_analytics",),
            executor=_exec_fire_gap_coverage_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="fire.mc_band_chart",
            kind=ReportSourceKind.CHART,
            title="Monte Carlo Months to FI (P10 / P50 / P90)",
            source_tables=("gold.wealth_fire_analytics",),
            executor=_exec_fire_mc_band_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="fire.runway_chart",
            kind=ReportSourceKind.CHART,
            title="Portfolio Runway Comparison (Months)",
            source_tables=("gold.wealth_fire_analytics",),
            executor=_exec_fire_runway_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="fire.savings_rate_chart",
            kind=ReportSourceKind.CHART,
            title="Savings Rate vs Required & SWR",
            source_tables=("gold.wealth_fire_analytics",),
            executor=_exec_fire_savings_rate_chart,
        )
    )
    registry.register_definition(FIRE_PLANNING_REVIEW_DEF)
