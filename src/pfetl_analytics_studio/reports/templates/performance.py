"""Investment Performance & Benchmark built-in presentation template and block sources.

Owns
----
- `investment_performance_benchmark` built-in `ReportDefinition` (default: Rolling 12M).
- Block sources backed by `InvestmentAnalytics.performance`.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import InvestmentAnalytics
from pfetl_analytics_studio.analytics.investments_models import AllocationDimension
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
    build_inv_perf_alpha_trend_figure,
    build_inv_perf_drawdown_figure,
    build_inv_perf_segment_alpha_figure,
)
from pfetl_analytics_studio.ui.theme import StudioColor

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.registry import ReportRegistry, ReportSourceContext


def _exec_performance_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Performance & Benchmark KPI cards from `InvestmentAnalytics.performance`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    perf = InvestmentAnalytics.performance(
        profile=profile,
        dimension=AllocationDimension.CLASS,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    items = kpi_items_from_overview_kpis(perf.kpis, perf.currency_id, limit=8)
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="performance.kpis",
        title=ctx.title_override or f"Performance & Benchmark KPIs ({perf.period_label})",
        items=tuple(items),
        source_tables=("gold.investment_by_portfolio", "gold.investment_by_isin"),
    )


def _exec_xirr_vs_benchmark_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Portfolio XIRR vs Benchmark XIRR trajectory chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    perf = InvestmentAnalytics.performance(
        profile=profile,
        dimension=AllocationDimension.CLASS,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    dates = [pt.closing_date.isoformat() for pt in perf.history]
    xirr = [(pt.xirr * 100.0) if pt.xirr is not None else None for pt in perf.history]
    bm_xirr = [(pt.bm_xirr * 100.0) if pt.bm_xirr is not None else None for pt in perf.history]
    active = [
        (pt.active_return * 100.0) if pt.active_return is not None else None for pt in perf.history
    ]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Portfolio XIRR (%)",
                "x": dates,
                "y": xirr,
                "line": {"color": StudioColor.ACCENT, "width": 2.5},
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Benchmark XIRR (%)",
                "x": dates,
                "y": bm_xirr,
                "line": {"color": StudioColor.MUTED, "width": 2.0, "dash": "dot"},
            },
            {
                "type": "bar",
                "name": "Active Return (%)",
                "x": dates,
                "y": active,
                "marker": {"color": StudioColor.SUCCESS, "opacity": 0.45},
            },
        ],
        "layout": {
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Closing Date"},
            "yaxis": {"title": "Annualized Return (%)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="performance.xirr_vs_benchmark_chart",
        title=ctx.title_override or f"Portfolio XIRR vs Benchmark ({perf.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.investment_by_portfolio",),
        caption=f"Period: {perf.period_label} · Source: gold.investment_by_portfolio",
    )


def _exec_asset_vs_fx_pnl_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Asset P&L vs Forex P&L decomposition chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    perf = InvestmentAnalytics.performance(
        profile=profile,
        dimension=AllocationDimension.CLASS,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    dates = [pt.closing_date.isoformat() for pt in perf.history]
    asset_pnl = [pt.asset_pnl or 0.0 for pt in perf.history]
    forex_pnl = [pt.forex_pnl or 0.0 for pt in perf.history]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Asset P&L",
                "x": dates,
                "y": asset_pnl,
                "marker": {"color": StudioColor.SUCCESS},
            },
            {
                "type": "bar",
                "name": "Forex P&L",
                "x": dates,
                "y": forex_pnl,
                "marker": {"color": StudioColor.RECOVERY},
            },
        ],
        "layout": {
            "barmode": "relative",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Closing Date"},
            "yaxis": {"title": "P&L (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="performance.asset_vs_fx_pnl_chart",
        title=ctx.title_override or f"Asset P&L vs Forex P&L ({perf.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.investment_by_portfolio",),
        caption=f"Period: {perf.period_label} · Source: gold.investment_by_portfolio",
    )


def _exec_isin_performance_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build ISIN performance & benchmark attribution table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    perf = InvestmentAnalytics.performance(
        profile=profile,
        dimension=AllocationDimension.CLASS,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    rows: list[dict[str, Any]] = [
        {
            "Instrument": r.label,
            "Class": r.parent_label or "",
            "Current Value": r.current_value,
            "Unrealized P&L": r.unrealized_pl,
            "XIRR (%)": (r.xirr * 100.0) if r.xirr is not None else None,
            "BM XIRR (%)": (r.bm_xirr * 100.0) if r.bm_xirr is not None else None,
            "Active Return (%)": (
                (r.active_return * 100.0) if r.active_return is not None else None
            ),
            "Weight (%)": (r.weight * 100.0) if r.weight is not None else None,
        }
        for r in sorted(perf.isin_perf_rows, key=lambda row: row.current_value, reverse=True)
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="performance.isin_table",
        title=ctx.title_override or "ISIN Performance & Benchmark Attribution",
        columns=(
            "Instrument",
            "Class",
            "Current Value",
            "Unrealized P&L",
            "XIRR (%)",
            "BM XIRR (%)",
            "Active Return (%)",
            "Weight (%)",
        ),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.investment_by_isin",),
    )


def _load_perf_snap(ctx: ReportSourceContext) -> Any:
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    return InvestmentAnalytics.performance(
        profile=profile,
        dimension=AllocationDimension.CLASS,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )


def _exec_drawdown_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    perf = _load_perf_snap(ctx)
    fig = build_inv_perf_drawdown_figure(perf.history) if perf.history else {}
    return RenderedChartBlock(
        block_id=block_id,
        source_id="performance.drawdown_chart",
        title=ctx.title_override or "Historical Max Drawdown",
        figure_dict=fig or {},
        source_tables=("gold.investment_by_portfolio",),
    )


def _exec_alpha_trend_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    perf = _load_perf_snap(ctx)
    fig = build_inv_perf_alpha_trend_figure(perf.history) if perf.history else {}
    return RenderedChartBlock(
        block_id=block_id,
        source_id="performance.alpha_trend_chart",
        title=ctx.title_override or "Active Alpha Trend (vs Benchmark)",
        figure_dict=fig or {},
        source_tables=("gold.investment_by_portfolio",),
    )


def _exec_segment_alpha_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    perf = _load_perf_snap(ctx)
    fig = (
        build_inv_perf_segment_alpha_figure(perf.segment_rows, dimension_label="Asset Class")
        if perf.segment_rows
        else {}
    )
    return RenderedChartBlock(
        block_id=block_id,
        source_id="performance.segment_alpha_chart",
        title=ctx.title_override or "Segment XIRR vs Benchmark by Asset Class",
        figure_dict=fig or {},
        source_tables=("gold.investment_by_class",),
    )


INVESTMENT_PERFORMANCE_BENCHMARK_DEF = ReportDefinition(
    id="investment_performance_benchmark",
    name="Investment Performance & Benchmark",
    description=(
        "Rolling/custom period performance deck: XIRR, after-tax XIRR, benchmark XIRR, "
        "active return, max drawdown, Asset vs FX P&L, and ISIN performance breakdown."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.ROLLING_12M),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="perf_title",
            title="Investment Performance & Benchmark",
            subtitle="XIRR, Benchmark Active Return, Drawdown & Asset vs FX P&L",
        ),
        ProvenanceBlock(block_id="perf_provenance", show_snapshots=True, show_disclaimer=True),
        NarrativeBlock(
            block_id="perf_scope",
            heading="Native Pass-Through Return Semantics",
            body=(
                "XIRR, after-tax XIRR, benchmark XIRR, active return, and max drawdown are "
                "canonical PFETL outputs and are never summed or averaged across instruments."
            ),
        ),
        KPIGroupBlock(
            block_id="perf_kpis",
            source_id="performance.kpis",
            title="Performance & Drawdown KPIs",
        ),
        PageBreakBlock(block_id="pb_perf_1"),
        ChartBlock(
            block_id="perf_xirr_chart",
            source_id="performance.xirr_vs_benchmark_chart",
            title="Portfolio XIRR vs Benchmark XIRR",
        ),
        PageBreakBlock(block_id="pb_perf_2"),
        ChartBlock(
            block_id="perf_pnl_chart",
            source_id="performance.asset_vs_fx_pnl_chart",
            title="Asset P&L vs Forex P&L Decomposition",
        ),
        ChartBlock(
            block_id="perf_drawdown_chart",
            source_id="performance.drawdown_chart",
            title="Historical Max Drawdown",
        ),
        ChartBlock(
            block_id="perf_alpha_trend_chart",
            source_id="performance.alpha_trend_chart",
            title="Active Alpha Trend (vs Benchmark)",
        ),
        ChartBlock(
            block_id="perf_segment_alpha_chart",
            source_id="performance.segment_alpha_chart",
            title="Segment XIRR vs Benchmark by Asset Class",
        ),
        PageBreakBlock(block_id="pb_perf_3"),
        TableBlock(
            block_id="perf_isin_tbl",
            source_id="performance.isin_table",
            title="ISIN Performance & Benchmark Table",
            max_rows=50,
        ),
    ),
)


def register_performance_sources(registry: ReportRegistry) -> None:
    """Register Investment Performance & Benchmark sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="performance.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Performance & Benchmark KPIs",
            source_tables=("gold.investment_by_portfolio", "gold.investment_by_isin"),
            executor=_exec_performance_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="performance.xirr_vs_benchmark_chart",
            kind=ReportSourceKind.CHART,
            title="Portfolio XIRR vs Benchmark",
            source_tables=("gold.investment_by_portfolio",),
            executor=_exec_xirr_vs_benchmark_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="performance.asset_vs_fx_pnl_chart",
            kind=ReportSourceKind.CHART,
            title="Asset P&L vs Forex P&L",
            source_tables=("gold.investment_by_portfolio",),
            executor=_exec_asset_vs_fx_pnl_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="performance.isin_table",
            kind=ReportSourceKind.TABLE,
            title="ISIN Performance Table",
            source_tables=("gold.investment_by_isin",),
            executor=_exec_isin_performance_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="performance.drawdown_chart",
            kind=ReportSourceKind.CHART,
            title="Historical Max Drawdown",
            source_tables=("gold.investment_by_portfolio",),
            executor=_exec_drawdown_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="performance.alpha_trend_chart",
            kind=ReportSourceKind.CHART,
            title="Active Alpha Trend (vs Benchmark)",
            source_tables=("gold.investment_by_portfolio",),
            executor=_exec_alpha_trend_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="performance.segment_alpha_chart",
            kind=ReportSourceKind.CHART,
            title="Segment XIRR vs Benchmark by Asset Class",
            source_tables=("gold.investment_by_class",),
            executor=_exec_segment_alpha_chart,
        )
    )
    registry.register_definition(INVESTMENT_PERFORMANCE_BENCHMARK_DEF)
