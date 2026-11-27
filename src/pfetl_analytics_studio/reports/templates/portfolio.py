"""Portfolio Review built-in presentation template and block sources.

Owns
----
- `portfolio_review` built-in `ReportDefinition` (default: Latest Complete Month).
- Block sources backed by `InvestmentAnalytics.portfolio_snapshot`, `allocation`, and `performance`.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import InvestmentAnalytics
from pfetl_analytics_studio.analytics.investments_models import AllocationDimension
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
from pfetl_analytics_studio.semantic import FilterSet, PeriodPreset, PeriodRequest
from pfetl_analytics_studio.ui.formatting import format_metric_value
from pfetl_analytics_studio.ui.theme import StudioColor

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.registry import ReportRegistry, ReportSourceContext


def _exec_portfolio_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Portfolio & Concentration KPI cards from `InvestmentAnalytics`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = InvestmentAnalytics.portfolio_snapshot(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        compatibility=ctx.app_context.compatibility,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    items = [
        ReportKpiItem(
            label=kpi.label,
            value=str(kpi.value) if not isinstance(kpi.value, (int, float)) else float(kpi.value),
            formatted_value=format_metric_value(
                kpi.value,
                kpi.format,
                currency_id=snap.currency_id,
                unit_suffix=kpi.unit_suffix,
            ),
            subtitle=kpi.subtitle,
            tone=kpi.status_tone,
            source_table=kpi.source_table,
        )
        for kpi in snap.kpis[:8]
    ]
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="portfolio.kpis",
        title=ctx.title_override or f"Portfolio Overview KPIs ({snap.period_label})",
        items=tuple(items),
        source_tables=("gold.investment_by_portfolio", "gold.investment_portfolio_summary"),
    )


def _exec_class_allocation_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Asset Class Invested vs Current Value chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    alloc = InvestmentAnalytics.allocation(
        profile=profile,
        dimension=AllocationDimension.CLASS,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    rows = sorted(alloc.rows, key=lambda r: r.current_value, reverse=True)
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Invested Value",
                "x": [r.label for r in rows],
                "y": [r.invested_value for r in rows],
                "marker": {"color": StudioColor.MUTED},
            },
            {
                "type": "bar",
                "name": "Current Value",
                "x": [r.label for r in rows],
                "y": [r.current_value for r in rows],
                "marker": {"color": StudioColor.ACCENT},
            },
        ],
        "layout": {
            "barmode": "group",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Asset Class"},
            "yaxis": {"title": "Value (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="portfolio.class_allocation_chart",
        title=ctx.title_override or f"Asset Class Allocation ({alloc.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.investment_by_class",),
        caption=f"As of {alloc.as_of_date or alloc.period_label} · Source: gold.investment_by_class",
    )


def _exec_sector_allocation_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Top 10 Sectors Current Value bar chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    alloc = InvestmentAnalytics.allocation(
        profile=profile,
        dimension=AllocationDimension.SECTOR,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    rows = sorted(alloc.rows, key=lambda r: r.current_value, reverse=True)[:10]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Current Value",
                "x": [r.label for r in rows],
                "y": [r.current_value for r in rows],
                "marker": {"color": StudioColor.SUCCESS},
            }
        ],
        "layout": {
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Sector"},
            "yaxis": {"title": "Current Value (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="portfolio.sector_allocation_chart",
        title=ctx.title_override or f"Top 10 Sector Allocation ({alloc.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.investment_by_sector",),
        caption=f"As of {alloc.as_of_date or alloc.period_label} · Source: gold.investment_by_sector",
    )


def _exec_holdings_drift_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Holdings Allocation, Drift & Harvesting Priority table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = InvestmentAnalytics.portfolio_snapshot(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        compatibility=ctx.app_context.compatibility,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    rows: list[dict[str, Any]] = [
        {
            "Instrument": h.instrument_name,
            "Class": h.instrument_class,
            "Market Value": h.market_value,
            "Unrealized P&L": h.unrealized_pnl,
            "Weight (%)": (h.isin_weight * 100.0) if h.isin_weight is not None else None,
            "Class Drift (%)": (h.class_drift * 100.0) if h.class_drift is not None else None,
            "Rebalance": "Yes" if h.rebalance_required else "No",
            "Harvest Loss": h.harvestable_loss,
        }
        for h in sorted(snap.holdings, key=lambda item: item.market_value, reverse=True)
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="portfolio.holdings_drift_table",
        title=ctx.title_override or "Holdings Allocation, Drift & Harvesting Context",
        columns=(
            "Instrument",
            "Class",
            "Market Value",
            "Unrealized P&L",
            "Weight (%)",
            "Class Drift (%)",
            "Rebalance",
            "Harvest Loss",
        ),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.investment_portfolio_summary",),
    )


def _exec_concentration_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Portfolio Concentration metrics table."""
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
    conc = perf.concentration
    rows: list[dict[str, Any]] = [
        {
            "Concentration Metric": "Top 1 Holding Weight (%)",
            "Value": (conc.top1_weight * 100.0) if conc.top1_weight is not None else None,
        },
        {
            "Concentration Metric": "Top 5 Holdings Weight (%)",
            "Value": (conc.top5_weight * 100.0) if conc.top5_weight is not None else None,
        },
        {
            "Concentration Metric": "Top 10 Holdings Weight (%)",
            "Value": (conc.top10_weight * 100.0) if conc.top10_weight is not None else None,
        },
        {"Concentration Metric": "Herfindahl-Hirschman Index (HHI)", "Value": conc.hhi_score},
        {
            "Concentration Metric": "Effective Holdings Count",
            "Value": conc.effective_holdings_count,
        },
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="portfolio.concentration_table",
        title=ctx.title_override or "Portfolio Concentration & Diversification",
        columns=("Concentration Metric", "Value"),
        rows=tuple(rows),
        total_rows=len(rows),
        truncated=False,
        source_tables=("gold.investment_by_portfolio",),
    )


PORTFOLIO_REVIEW_DEF = ReportDefinition(
    id="portfolio_review",
    name="Portfolio Review",
    description=(
        "Latest-close portfolio deck: valuation, allocation by class/sector, "
        "concentration (Top 1/5/10, HHI), class drift, and harvesting context."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="port_title",
            title="Portfolio Review",
            subtitle="Allocation, Drift, Concentration & Harvesting Context",
        ),
        ProvenanceBlock(block_id="port_provenance", show_snapshots=True, show_disclaimer=True),
        NarrativeBlock(
            block_id="port_scope",
            heading="Native Gold Weights & Non-Recomputation",
            body=(
                "Allocation weights, drift flags, HHI, and XIRR are sourced directly from PFETL "
                "Gold investment marts without Silver-level weight or return recomputation."
            ),
        ),
        KPIGroupBlock(
            block_id="port_kpis",
            source_id="portfolio.kpis",
            title="Portfolio Headline KPIs",
        ),
        PageBreakBlock(block_id="pb_port_1"),
        ChartBlock(
            block_id="port_class_chart",
            source_id="portfolio.class_allocation_chart",
            title="Asset Class Allocation",
        ),
        PageBreakBlock(block_id="pb_port_2"),
        ChartBlock(
            block_id="port_sector_chart",
            source_id="portfolio.sector_allocation_chart",
            title="Top 10 Sector Allocation",
        ),
        PageBreakBlock(block_id="pb_port_3"),
        TableBlock(
            block_id="port_holdings_tbl",
            source_id="portfolio.holdings_drift_table",
            title="Holdings Allocation, Drift & Harvesting",
            max_rows=50,
        ),
        PageBreakBlock(block_id="pb_port_4"),
        TableBlock(
            block_id="port_conc_tbl",
            source_id="portfolio.concentration_table",
            title="Concentration & Diversification",
            max_rows=10,
        ),
    ),
)


def register_portfolio_sources(registry: ReportRegistry) -> None:
    """Register Portfolio Review sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="portfolio.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Portfolio Headline KPIs",
            source_tables=("gold.investment_by_portfolio", "gold.investment_portfolio_summary"),
            executor=_exec_portfolio_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="portfolio.class_allocation_chart",
            kind=ReportSourceKind.CHART,
            title="Asset Class Allocation",
            source_tables=("gold.investment_by_class",),
            executor=_exec_class_allocation_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="portfolio.sector_allocation_chart",
            kind=ReportSourceKind.CHART,
            title="Top 10 Sector Allocation",
            source_tables=("gold.investment_by_sector",),
            executor=_exec_sector_allocation_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="portfolio.holdings_drift_table",
            kind=ReportSourceKind.TABLE,
            title="Holdings Allocation & Drift",
            source_tables=("gold.investment_portfolio_summary",),
            executor=_exec_holdings_drift_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="portfolio.concentration_table",
            kind=ReportSourceKind.TABLE,
            title="Portfolio Concentration",
            source_tables=("gold.investment_by_portfolio",),
            executor=_exec_concentration_table,
        )
    )
    registry.register_definition(PORTFOLIO_REVIEW_DEF)
