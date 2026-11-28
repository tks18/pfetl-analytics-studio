"""Net Worth & Wealth Attribution built-in presentation template and block sources.

Owns
----
- `net_worth_wealth_attribution` built-in `ReportDefinition` (default: Rolling 12M).
- Block sources for Wealth KPIs, Book-vs-Market Net Worth chart, and Asset Subcategory table.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import WealthAnalytics
from pfetl_analytics_studio.reports.comparison import (
    kpi_items_from_overview_kpis,
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
    TableBlock,
    TitleBlock,
)
from pfetl_analytics_studio.semantic import FilterSet, PeriodPreset, PeriodRequest
from pfetl_analytics_studio.ui import (
    build_wealth_group_treemap_figure,
    build_wealth_savings_vs_market_figure,
)
from pfetl_analytics_studio.ui.theme import StudioColor

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.registry import ReportRegistry, ReportSourceContext


def _exec_wealth_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Wealth balance-sheet, liquidity, and attribution KPIs."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = WealthAnalytics.summary(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        compatibility=ctx.app_context.compatibility,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    items = kpi_items_from_overview_kpis(snap.kpis, snap.currency_id, limit=8)
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="wealth.kpis",
        title=ctx.title_override or f"Wealth & Liquidity KPIs ({snap.period_label})",
        items=tuple(items),
        source_tables=("gold.core_monthly_fact", "gold.wealth_asset_breakdown"),
    )


def _exec_book_vs_market_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Book vs Market Net Worth & Liquid Assets trajectory chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = WealthAnalytics.summary(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        compatibility=ctx.app_context.compatibility,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    dates = [pt.month_end.isoformat() for pt in snap.monthly_series]
    market_nw = [pt.market_net_worth for pt in snap.monthly_series]
    book_nw = [pt.book_net_worth for pt in snap.monthly_series]
    liquid_mkt = [pt.liquid_assets_market for pt in snap.monthly_series]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Market Net Worth",
                "x": dates,
                "y": market_nw,
                "line": {"color": StudioColor.ACCENT, "width": 2.5},
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Book Net Worth",
                "x": dates,
                "y": book_nw,
                "line": {"color": StudioColor.RECOVERY, "width": 2.0, "dash": "dot"},
            },
            {
                "type": "bar",
                "name": "Liquid Assets (Market)",
                "x": dates,
                "y": liquid_mkt,
                "marker": {"color": StudioColor.SUCCESS, "opacity": 0.4},
            },
        ],
        "layout": {
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Month End"},
            "yaxis": {"title": "Amount (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="wealth.book_vs_market_chart",
        title=ctx.title_override
        or f"Book vs Market Net Worth & Liquidity ({snap.chart_window_label})",
        figure_dict=figure_dict,
        source_tables=("gold.core_monthly_fact",),
        caption=f"Window: {snap.chart_window_label} · Source: gold.core_monthly_fact",
    )


def _exec_asset_subcategory_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Asset-Subcategory contribution, Asset P&L vs FX P&L, and drawdown table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    breakdown = WealthAnalytics.asset_breakdown(
        profile=profile,
        period_context=ctx.report_context.period,
        filters=ctx.report_context.filters,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    rows = [
        {
            "Asset": a.asset_name,
            "Group": a.asset_group,
            "Liquid": "Yes" if a.is_liquid else "No",
            "Opening": a.opening_balance,
            "Closing (Market)": a.closing_balance_market,
            "Asset P&L": a.closing_asset_pnl,
            "FX P&L": a.closing_forex_pnl,
            "Drawdown Peak": a.drawdown_from_peak,
        }
        for a in breakdown.assets
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="wealth.asset_subcategory_table",
        title=ctx.title_override or "Asset Subcategory Contribution & P&L",
        columns=(
            "Asset",
            "Group",
            "Liquid",
            "Opening",
            "Closing (Market)",
            "Asset P&L",
            "FX P&L",
            "Drawdown Peak",
        ),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.wealth_asset_breakdown",),
    )


def _exec_asset_split_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Asset Group -> Asset Subcategory treemap from `WealthAssetBreakdown`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    breakdown = WealthAnalytics.asset_breakdown(
        profile=profile,
        period_context=ctx.report_context.period,
        filters=ctx.report_context.filters,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    fig = (
        build_wealth_group_treemap_figure(breakdown.assets, breakdown.currency_id)
        if breakdown.assets
        else {}
    )
    return RenderedChartBlock(
        block_id=block_id,
        source_id="wealth.asset_split_chart",
        title=ctx.title_override or "Asset Group Allocation (Market vs Book)",
        figure_dict=fig or {},
        source_tables=("gold.wealth_asset_breakdown",),
    )


def _exec_cum_return_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Cumulative Net Savings vs Market Balance figure."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    breakdown = WealthAnalytics.asset_breakdown(
        profile=profile,
        period_context=ctx.report_context.period,
        filters=ctx.report_context.filters,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    fig = (
        build_wealth_savings_vs_market_figure(breakdown.assets, breakdown.currency_id)
        if breakdown.assets
        else {}
    )
    return RenderedChartBlock(
        block_id=block_id,
        source_id="wealth.cum_return_chart",
        title=ctx.title_override or "Cumulative Savings vs Market Return",
        figure_dict=fig or {},
        source_tables=("gold.wealth_asset_breakdown",),
    )


NET_WORTH_WEALTH_ATTRIBUTION_DEF = ReportDefinition(
    id="net_worth_wealth_attribution",
    name="Net Worth & Wealth Attribution",
    description=(
        "Rolling 12M balance sheet & attribution deck: Book vs Market NW, "
        "Net Savings + Market Return + Residual reconciliation, Asset vs FX P&L, and drawdown."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.ROLLING_12M),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="wealth_title",
            title="Net Worth & Wealth Attribution",
            subtitle="Book vs Market Net Worth, Liquidity Runway & Asset Attribution",
        ),
        ProvenanceBlock(block_id="wealth_provenance", show_snapshots=True, show_disclaimer=True),
        NarrativeBlock(
            block_id="wealth_scope",
            heading="Attribution Reconciliation",
            body=(
                "Net_Savings_Flow + Market_Return_Flow + Attribution_Residual reconciles directly "
                "to PFETL Core's Asset_Market_Delta; residuals are never hidden or absorbed."
            ),
        ),
        KPIGroupBlock(
            block_id="wealth_kpi_group",
            source_id="wealth.kpis",
            title="Wealth, Liquidity & Drawdown KPIs",
        ),
        PageBreakBlock(block_id="pb_wealth_1"),
        ChartBlock(
            block_id="wealth_book_market",
            source_id="wealth.book_vs_market_chart",
            title="Book vs Market Net Worth & Liquid Assets",
        ),
        PageBreakBlock(block_id="pb_wealth_2"),
        ChartBlock(
            block_id="wealth_attribution_bridge",
            source_id="monthly.attribution_waterfall",
            title="Net Worth Attribution Bridge",
        ),
        ChartBlock(
            block_id="wealth_asset_split_chart",
            source_id="wealth.asset_split_chart",
            title="Asset Group Allocation (Market vs Book)",
        ),
        ChartBlock(
            block_id="wealth_cum_return_chart",
            source_id="wealth.cum_return_chart",
            title="Cumulative Savings vs Market Return",
        ),
        PageBreakBlock(block_id="pb_wealth_3"),
        TableBlock(
            block_id="wealth_asset_tbl",
            source_id="wealth.asset_subcategory_table",
            title="Asset Subcategory Contribution, Asset P&L & FX P&L",
            max_rows=50,
        ),
    ),
)


def register_wealth_sources(registry: ReportRegistry) -> None:
    """Register Net Worth & Wealth Attribution sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="wealth.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Wealth & Liquidity KPIs",
            source_tables=("gold.core_monthly_fact", "gold.wealth_asset_breakdown"),
            executor=_exec_wealth_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="wealth.book_vs_market_chart",
            kind=ReportSourceKind.CHART,
            title="Book vs Market Net Worth Chart",
            source_tables=("gold.core_monthly_fact",),
            executor=_exec_book_vs_market_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="wealth.asset_subcategory_table",
            kind=ReportSourceKind.TABLE,
            title="Asset Subcategory Table",
            source_tables=("gold.wealth_asset_breakdown",),
            executor=_exec_asset_subcategory_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="wealth.asset_split_chart",
            kind=ReportSourceKind.CHART,
            title="Asset Group Allocation (Market vs Book)",
            source_tables=("gold.wealth_asset_breakdown",),
            executor=_exec_asset_split_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="wealth.cum_return_chart",
            kind=ReportSourceKind.CHART,
            title="Cumulative Savings vs Market Return",
            source_tables=("gold.core_monthly_fact",),
            executor=_exec_cum_return_chart,
        )
    )
    registry.register_definition(NET_WORTH_WEALTH_ATTRIBUTION_DEF)
