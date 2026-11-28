"""Cashflow & Budget Review built-in presentation template and block sources.

Owns
----
- `cashflow_budget_review` built-in `ReportDefinition` (default: Latest Complete Month).
- Block sources backed by `HouseholdAnalytics.budget_summary` and `cashflow_statement`.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import HouseholdAnalytics
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
from pfetl_analytics_studio.ui.theme import StudioColor

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.registry import ReportRegistry, ReportSourceContext


def _exec_cashflow_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Budget & Cashflow KPI cards from `HouseholdAnalytics`."""
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
    combined = list(budget.kpis[:4]) + list(cashflow.kpis[:4])
    items = kpi_items_from_overview_kpis(combined, budget.currency_id, limit=8)
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="cashflow.kpis",
        title=ctx.title_override or f"Budget & Cashflow KPIs ({budget.period_label})",
        items=tuple(items),
        source_tables=("gold.forecast_budget_variance", "gold.cashflow_activity_summary"),
    )


def _exec_budget_vs_actual_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build grouped bar chart comparing Budget Targets vs Actuals."""
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
    categories = ["Income", "Core Expense", "Non-Core Expense", "Investment", "Savings / Buffer"]
    targets = [
        budget.total_budget_income,
        budget.total_budget_core,
        budget.total_budget_non_core,
        budget.total_budget_investment,
        budget.total_budget_buffer,
    ]
    actuals = [
        budget.total_actual_income,
        budget.total_actual_core,
        budget.total_actual_non_core,
        budget.total_actual_investment,
        budget.total_actual_savings,
    ]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Budget Target",
                "x": categories,
                "y": targets,
                "marker": {"color": StudioColor.MUTED},
            },
            {
                "type": "bar",
                "name": "Actual",
                "x": categories,
                "y": actuals,
                "marker": {"color": StudioColor.ACCENT},
            },
        ],
        "layout": {
            "barmode": "group",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Budget Envelope"},
            "yaxis": {"title": "Amount (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="cashflow.budget_vs_actual_chart",
        title=ctx.title_override or f"Budget Target vs Actual ({budget.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.forecast_budget_variance",),
        caption=f"Period: {budget.period_label} · Source: gold.forecast_budget_variance",
    )


def _exec_activity_waterfall_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Cashflow Activity Waterfall from Opening Cash to Closing Cash."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    cf = HouseholdAnalytics.cashflow_statement(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        compatibility=ctx.app_context.compatibility,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    x_labels = [
        "Opening Cash",
        "Operating Net",
        "Investing Net",
        "Financing Net",
        "Transfers Net",
        "Closing Cash",
    ]
    y_values = [
        cf.opening_cash,
        cf.net_operating,
        cf.net_investing,
        cf.net_financing,
        cf.net_transfers,
        cf.closing_cash,
    ]
    measures = ["absolute", "relative", "relative", "relative", "relative", "total"]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "waterfall",
                "name": "Cashflow Activity",
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
            "xaxis": {"title": "Cashflow Activity"},
            "yaxis": {"title": "Amount (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="cashflow.activity_waterfall_chart",
        title=ctx.title_override or f"Cashflow Activity Bridge ({cf.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.cashflow_activity_summary",),
        caption=(
            f"Unreconciled Difference: {cf.unreconciled_difference:,.2f} · "
            "Source: gold.cashflow_activity_summary"
        ),
    )


def _exec_monthly_health_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build monthly budget health flags, emergency gap, and runway table."""
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
    rows: list[dict[str, Any]] = [
        {
            "Month": pt.year_month,
            "Healthy": "Yes" if pt.flags.is_budget_healthy else "No",
            "Core Overspent": "Yes" if pt.flags.is_core_overspent else "No",
            "Non-Core Overspent": "Yes" if pt.flags.is_non_core_overspent else "No",
            "Inv Underfunded": "Yes" if pt.flags.is_investment_underfunded else "No",
            "Emergency Gap": pt.emergency_gap,
            "Runway (Mos)": pt.runway_months,
        }
        for pt in sorted(budget.monthly_series, key=lambda p: p.month_end, reverse=True)
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="cashflow.monthly_health_table",
        title=ctx.title_override or "Monthly Budget Health Flags & Runway",
        columns=(
            "Month",
            "Healthy",
            "Core Overspent",
            "Non-Core Overspent",
            "Inv Underfunded",
            "Emergency Gap",
            "Runway (Mos)",
        ),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.forecast_budget_variance",),
    )


def _exec_cashflow_statement_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Operating/Investing/Financing cashflow & reconciliation statement."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    cf = HouseholdAnalytics.cashflow_statement(
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
        {"Cashflow Line": "Opening Cash Balance", "Amount": cf.opening_cash},
        {"Cashflow Line": "Operating Cashflow (Net)", "Amount": cf.net_operating},
        {"Cashflow Line": "Investing Cashflow (Net)", "Amount": cf.net_investing},
        {"Cashflow Line": "Financing Cashflow (Net)", "Amount": cf.net_financing},
        {"Cashflow Line": "Internal Transfers (Net)", "Amount": cf.net_transfers},
        {"Cashflow Line": "Calculated Net Cashflow", "Amount": cf.calculated_net_cashflow},
        {"Cashflow Line": "Closing Cash Balance", "Amount": cf.closing_cash},
        {"Cashflow Line": "Actual Cash Change", "Amount": cf.actual_cash_change},
        {"Cashflow Line": "Unreconciled Difference", "Amount": cf.unreconciled_difference},
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="cashflow.statement_table",
        title=ctx.title_override or "Cashflow Statement & Reconciliation",
        columns=("Cashflow Line", "Amount"),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.cashflow_activity_summary",),
    )


CASHFLOW_BUDGET_REVIEW_DEF = ReportDefinition(
    id="cashflow_budget_review",
    name="Cashflow & Budget Review",
    description=(
        "Household budget guardrails & cashflow deck: target vs actual, health flags, "
        "operating/investing/financing bridge, emergency gap, and zero-income runway."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="cf_title",
            title="Cashflow & Budget Review",
            subtitle="Budget Guardrails, Cashflow Reconciliation & Liquidity Runway",
        ),
        ProvenanceBlock(block_id="cf_provenance", show_snapshots=True, show_disclaimer=False),
        NarrativeBlock(
            block_id="cf_scope",
            heading="Reconciliation & Budget Guardrails",
            body=(
                "Figures match forecast_budget_variance and cashflow_activity_summary exactly; "
                "unreconciled cashflow differences are surfaced explicitly."
            ),
        ),
        KPIGroupBlock(
            block_id="cf_kpis",
            source_id="cashflow.kpis",
            title="Budget & Cashflow KPIs",
        ),
        PageBreakBlock(block_id="pb_cf_1"),
        ChartBlock(
            block_id="cf_budget_chart",
            source_id="cashflow.budget_vs_actual_chart",
            title="Budget Target vs Actual",
        ),
        PageBreakBlock(block_id="pb_cf_2"),
        ChartBlock(
            block_id="cf_waterfall_chart",
            source_id="cashflow.activity_waterfall_chart",
            title="Cashflow Activity Bridge",
        ),
        ChartBlock(
            block_id="cf_income_expense_trend",
            source_id="monthly.income_expense_cashflow_chart",
            title="Monthly Income, Expense & Net Cashflow Trend",
        ),
        PageBreakBlock(block_id="pb_cf_3"),
        TableBlock(
            block_id="cf_health_tbl",
            source_id="cashflow.monthly_health_table",
            title="Monthly Budget Health & Runway",
            max_rows=24,
        ),
        PageBreakBlock(block_id="pb_cf_4"),
        TableBlock(
            block_id="cf_stmt_tbl",
            source_id="cashflow.statement_table",
            title="Cashflow Statement & Reconciliation",
            max_rows=20,
        ),
    ),
)


def register_cashflow_sources(registry: ReportRegistry) -> None:
    """Register Cashflow & Budget Review sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="cashflow.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Budget & Cashflow KPIs",
            source_tables=("gold.forecast_budget_variance", "gold.cashflow_activity_summary"),
            executor=_exec_cashflow_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="cashflow.budget_vs_actual_chart",
            kind=ReportSourceKind.CHART,
            title="Budget Target vs Actual Chart",
            source_tables=("gold.forecast_budget_variance",),
            executor=_exec_budget_vs_actual_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="cashflow.activity_waterfall_chart",
            kind=ReportSourceKind.CHART,
            title="Cashflow Activity Bridge",
            source_tables=("gold.cashflow_activity_summary",),
            executor=_exec_activity_waterfall_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="cashflow.monthly_health_table",
            kind=ReportSourceKind.TABLE,
            title="Monthly Budget Health & Runway",
            source_tables=("gold.forecast_budget_variance",),
            executor=_exec_monthly_health_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="cashflow.statement_table",
            kind=ReportSourceKind.TABLE,
            title="Cashflow Statement & Reconciliation",
            source_tables=("gold.cashflow_activity_summary",),
            executor=_exec_cashflow_statement_table,
        )
    )
    registry.register_definition(CASHFLOW_BUDGET_REVIEW_DEF)
