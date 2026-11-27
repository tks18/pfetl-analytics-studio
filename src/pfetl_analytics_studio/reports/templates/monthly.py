"""Monthly Financial Review built-in presentation template and block sources.

Owns
----
- `monthly_financial_review` built-in `ReportDefinition` (default: Latest Complete Month).
- Block sources for Data Health KPIs, Attribution Waterfall, Income/Expense/Cashflow chart,
  Budget/Cashflow health table, Portfolio/Tax/FIRE KPIs, and Asset Class Mix table.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import HouseholdAnalytics, OverviewService
from pfetl_analytics_studio.analytics.overview_models import OverviewSnapshot
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


def _load_overview(ctx: ReportSourceContext) -> OverviewSnapshot:
    """Load `OverviewSnapshot` pinned to `ReportContext`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    return OverviewService.get_snapshot(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        compatibility=ctx.app_context.compatibility,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )


def _exec_data_health_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Data Health & Operational Freshness KPI cards."""
    snap = _load_overview(ctx)
    health = snap.run_health
    items = (
        ReportKpiItem(
            label="Latest PFETL Run",
            value=health.status or "UNKNOWN",
            formatted_value=health.status or "UNKNOWN",
            subtitle=f"{health.commit_state or 'N/A'} · {health.application_version or ''}".strip(
                " ·"
            ),
            source_table="cp_runs",
        ),
        ReportKpiItem(
            label="Catalog Surfaces",
            value=health.surfaces_discovered,
            formatted_value=f"{health.surfaces_discovered} / {health.surfaces_expected}",
            subtitle=f"Compatibility: {health.compatibility_status or 'CHECKED'}",
            source_table="meta.m_Data_Contracts",
        ),
        ReportKpiItem(
            label="Gold Marts Populated",
            value=health.gold_tables_populated,
            formatted_value=f"{health.gold_tables_populated} tables",
            subtitle=f"{health.warehouse_rows_total:,} total rows",
            source_table="meta.m_Table_Row_Counts",
        ),
        ReportKpiItem(
            label="As-Of Date",
            value=snap.as_of_date.isoformat() if snap.as_of_date else "N/A",
            formatted_value=snap.as_of_date.isoformat() if snap.as_of_date else "N/A",
            subtitle=f"Period: {snap.period_label}",
            source_table="gold.core_monthly_fact",
        ),
    )
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="monthly.data_health_kpis",
        title=ctx.title_override or "Data Health & Warehouse Freshness",
        items=items,
        source_tables=("cp_runs", "meta.m_Table_Row_Counts", "gold.core_monthly_fact"),
    )


def _exec_attribution_waterfall(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Net Worth Attribution Waterfall reconciling Savings + Market + Residual."""
    snap = _load_overview(ctx)
    bridge = snap.attribution_bridge
    x_labels = [
        "Opening NW",
        "Net Savings",
        "Market Return",
        "Residual",
        "Closing NW",
    ]
    y_values: list[float] | None = None
    if bridge is not None:
        y_values = [
            bridge.opening_net_worth or 0.0,
            bridge.net_savings_flow or 0.0,
            bridge.market_return_flow or 0.0,
            bridge.attribution_residual or 0.0,
            bridge.closing_net_worth or 0.0,
        ]
    elif snap.net_worth_series:
        last_pt = snap.net_worth_series[-1]
        sav = last_pt.net_savings_flow or 0.0
        mkt = last_pt.market_return_flow or 0.0
        res = last_pt.attribution_residual or 0.0
        close_nw = last_pt.net_worth_market
        open_nw = close_nw - sav - mkt - res
        y_values = [open_nw, sav, mkt, res, close_nw]

    if y_values is None:
        return RenderedChartBlock(
            block_id=block_id,
            source_id="monthly.attribution_waterfall",
            title=ctx.title_override or "Net Worth Attribution Bridge",
            figure_dict={},
            source_tables=("gold.core_monthly_fact",),
            caption="No attribution bridge data available for the selected period.",
        )

    measures = ["absolute", "relative", "relative", "relative", "total"]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "waterfall",
                "name": "Net Worth Attribution",
                "x": x_labels,
                "y": y_values,
                "measure": measures,
                "connector": {"line": {"color": "#94A3B8", "width": 1.2}},
                "increasing": {"marker": {"color": StudioColor.SUCCESS}},
                "decreasing": {"marker": {"color": StudioColor.ERROR}},
                "totals": {"marker": {"color": StudioColor.ACCENT}},
            }
        ],
        "layout": {
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Attribution Component"},
            "yaxis": {"title": "Amount (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="monthly.attribution_waterfall",
        title=ctx.title_override or f"Net Worth Attribution Bridge ({snap.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.core_monthly_fact",),
        caption=(
            f"Period: {snap.period_label} · Residual explicitly preserved · "
            "Source: gold.core_monthly_fact"
        ),
    )


def _exec_income_expense_cashflow_chart(
    ctx: ReportSourceContext, block_id: str
) -> RenderedChartBlock:
    """Build monthly Income vs Expense & Net Cashflow chart."""
    snap = _load_overview(ctx)
    dates = [pt.month_end.isoformat() for pt in snap.cashflow_series]
    inc = [pt.income for pt in snap.cashflow_series]
    exp = [pt.expense for pt in snap.cashflow_series]
    net_cf = [pt.net_cashflow for pt in snap.cashflow_series]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Total Income",
                "x": dates,
                "y": inc,
                "marker": {"color": StudioColor.SUCCESS},
            },
            {
                "type": "bar",
                "name": "Total Expense",
                "x": dates,
                "y": exp,
                "marker": {"color": StudioColor.ERROR},
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Net Cashflow",
                "x": dates,
                "y": net_cf,
                "line": {"color": StudioColor.ACCENT, "width": 2.5},
            },
        ],
        "layout": {
            "barmode": "group",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Month End"},
            "yaxis": {"title": "Amount (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="monthly.income_expense_cashflow_chart",
        title=ctx.title_override or f"Income, Expense & Net Cashflow ({snap.chart_window_label})",
        figure_dict=figure_dict,
        source_tables=("gold.core_monthly_fact",),
        caption=f"Window: {snap.chart_window_label} · Source: gold.core_monthly_fact",
    )


def _exec_budget_cashflow_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Budget vs Actual & Cashflow Reconciliation summary table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    budget = HouseholdAnalytics.budget_summary(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        compatibility=ctx.app_context.compatibility,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    cashflow = HouseholdAnalytics.cashflow_statement(
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
            "Line Item": "Income (Budget vs Actual)",
            "Target / Opening": budget.total_budget_income,
            "Actual / Closing": budget.total_actual_income,
            "Variance / Status": budget.total_actual_income - budget.total_budget_income,
        },
        {
            "Line Item": "Core Expense (Budget vs Actual)",
            "Target / Opening": budget.total_budget_core,
            "Actual / Closing": budget.total_actual_core,
            "Variance / Status": budget.total_actual_core - budget.total_budget_core,
        },
        {
            "Line Item": "Non-Core Expense (Budget vs Actual)",
            "Target / Opening": budget.total_budget_non_core,
            "Actual / Closing": budget.total_actual_non_core,
            "Variance / Status": budget.total_actual_non_core - budget.total_budget_non_core,
        },
        {
            "Line Item": "Investment Funding (Target vs Actual)",
            "Target / Opening": budget.total_budget_investment,
            "Actual / Closing": budget.total_actual_investment,
            "Variance / Status": budget.total_actual_investment - budget.total_budget_investment,
        },
        {
            "Line Item": "Operating Cashflow (Net)",
            "Target / Opening": cashflow.opening_cash,
            "Actual / Closing": cashflow.net_operating,
            "Variance / Status": cashflow.calculated_net_cashflow,
        },
        {
            "Line Item": "Investing Cashflow (Net)",
            "Target / Opening": None,
            "Actual / Closing": cashflow.net_investing,
            "Variance / Status": cashflow.actual_cash_change,
        },
        {
            "Line Item": "Cashflow Unreconciled Difference",
            "Target / Opening": cashflow.opening_cash,
            "Actual / Closing": cashflow.closing_cash,
            "Variance / Status": cashflow.unreconciled_difference,
        },
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="monthly.budget_cashflow_table",
        title=ctx.title_override or "Budget Variance & Cashflow Reconciliation",
        columns=("Line Item", "Target / Opening", "Actual / Closing", "Variance / Status"),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.forecast_budget_variance", "gold.cashflow_activity_summary"),
    )


def _exec_portfolio_tax_fire_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build secondary Portfolio, Indicative Tax, and FIRE KPI cards."""
    snap = _load_overview(ctx)
    items: list[ReportKpiItem] = []
    for kpi in snap.secondary_kpis[:8]:
        formatted = format_metric_value(
            kpi.value,
            kpi.format,
            currency_id=snap.currency_id,
            unit_suffix=kpi.unit_suffix,
        )
        items.append(
            ReportKpiItem(
                label=kpi.label,
                value=str(kpi.value)
                if not isinstance(kpi.value, (int, float))
                else float(kpi.value),
                formatted_value=formatted,
                subtitle=kpi.subtitle,
                tone=kpi.status_tone,
                source_table=kpi.source_table,
            )
        )
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="monthly.portfolio_tax_fire_kpis",
        title=ctx.title_override or "Portfolio, Indicative Tax & FIRE Highlights",
        items=tuple(items),
        source_tables=(
            "gold.investment_by_portfolio",
            "gold.tax_financial_year_summary",
            "gold.wealth_fire_analytics",
        ),
    )


def _exec_asset_class_mix_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Asset Class allocation & P&L table from `OverviewSnapshot`."""
    snap = _load_overview(ctx)
    rows = [
        {
            "Asset Class": pt.instrument_class,
            "Current Value": pt.current_value,
            "Invested Value": pt.invested_value,
            "Unrealized P&L": pt.unrealized_pl,
            "Weight (%)": (pt.weight * 100.0) if pt.weight is not None else None,
            "XIRR (%)": (pt.xirr * 100.0) if pt.xirr is not None else None,
            "Active Return (%)": (
                (pt.active_return * 100.0) if pt.active_return is not None else None
            ),
        }
        for pt in snap.asset_class_mix
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="monthly.asset_class_mix_table",
        title=ctx.title_override or "Portfolio Allocation by Asset Class",
        columns=(
            "Asset Class",
            "Current Value",
            "Invested Value",
            "Unrealized P&L",
            "Weight (%)",
            "XIRR (%)",
            "Active Return (%)",
        ),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.investment_by_class",),
    )


MONTHLY_FINANCIAL_REVIEW_DEF = ReportDefinition(
    id="monthly_financial_review",
    name="Monthly Financial Review",
    description=(
        "Executive monthly deck: Data Health, Net Worth Attribution, Cashflow & Budget, "
        "Portfolio Allocation, Indicative Tax, and FIRE Coverage."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="monthly_title",
            title="Monthly Financial Review",
            subtitle="Canonical PFETL Monthly Decision Cockpit & Attribution Deck",
        ),
        ProvenanceBlock(block_id="monthly_provenance", show_snapshots=True, show_disclaimer=True),
        NarrativeBlock(
            block_id="monthly_scope",
            heading="Executive Scope",
            body=(
                "Headline figures match Overview, Household, Wealth, Investments, Tax, and FIRE "
                "services for the pinned PFETL run. Tax figures are indicative estimates only."
            ),
        ),
        KPIGroupBlock(
            block_id="monthly_health",
            source_id="monthly.data_health_kpis",
            title="Data Health & Freshness",
        ),
        PageBreakBlock(block_id="pb_monthly_1"),
        KPIGroupBlock(
            block_id="monthly_headline_kpis",
            source_id="overview.kpis",
            title="Headline Wealth, Cashflow & Portfolio KPIs",
        ),
        PageBreakBlock(block_id="pb_monthly_2"),
        ChartBlock(
            block_id="monthly_attribution_chart",
            source_id="monthly.attribution_waterfall",
            title="Net Worth Attribution Bridge",
        ),
        PageBreakBlock(block_id="pb_monthly_3"),
        ChartBlock(
            block_id="monthly_cashflow_chart",
            source_id="monthly.income_expense_cashflow_chart",
            title="Income, Expense & Net Cashflow Trajectory",
        ),
        PageBreakBlock(block_id="pb_monthly_4"),
        TableBlock(
            block_id="monthly_budget_table",
            source_id="monthly.budget_cashflow_table",
            title="Budget Variance & Cashflow Reconciliation",
            max_rows=20,
        ),
        PageBreakBlock(block_id="pb_monthly_5"),
        KPIGroupBlock(
            block_id="monthly_secondary_kpis",
            source_id="monthly.portfolio_tax_fire_kpis",
            title="Portfolio, Indicative Tax & FIRE Summary",
        ),
        TableBlock(
            block_id="monthly_asset_mix",
            source_id="monthly.asset_class_mix_table",
            title="Asset Class Allocation & Performance",
            max_rows=20,
        ),
    ),
)


def register_monthly_sources(registry: ReportRegistry) -> None:
    """Register Monthly Financial Review sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="monthly.data_health_kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Data Health & Freshness KPIs",
            source_tables=("cp_runs", "meta.m_Table_Row_Counts"),
            executor=_exec_data_health_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="monthly.attribution_waterfall",
            kind=ReportSourceKind.CHART,
            title="Net Worth Attribution Bridge",
            source_tables=("gold.core_monthly_fact",),
            executor=_exec_attribution_waterfall,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="monthly.income_expense_cashflow_chart",
            kind=ReportSourceKind.CHART,
            title="Income, Expense & Net Cashflow Chart",
            source_tables=("gold.core_monthly_fact",),
            executor=_exec_income_expense_cashflow_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="monthly.budget_cashflow_table",
            kind=ReportSourceKind.TABLE,
            title="Budget Variance & Cashflow Table",
            source_tables=("gold.forecast_budget_variance", "gold.cashflow_activity_summary"),
            executor=_exec_budget_cashflow_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="monthly.portfolio_tax_fire_kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Portfolio, Tax & FIRE KPIs",
            source_tables=(
                "gold.investment_by_portfolio",
                "gold.tax_financial_year_summary",
                "gold.wealth_fire_analytics",
            ),
            executor=_exec_portfolio_tax_fire_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="monthly.asset_class_mix_table",
            kind=ReportSourceKind.TABLE,
            title="Asset Class Allocation Table",
            source_tables=("gold.investment_by_class",),
            executor=_exec_asset_class_mix_table,
        )
    )
    registry.register_definition(MONTHLY_FINANCIAL_REVIEW_DEF)
