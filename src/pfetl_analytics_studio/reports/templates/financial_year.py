"""Financial Year Review built-in presentation template and block sources.

Owns
----
- `financial_year_review` built-in `ReportDefinition` (default: FYTD / Selected FY).
- Block sources for FY Headline KPIs, Income/Expense Mix chart, Cashflow Statement table,
  and Indicative Tax Position summary table.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import HouseholdAnalytics, OverviewService, TaxAnalytics
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


def _exec_fy_headline_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build FY headline KPIs from `OverviewService.get_snapshot`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = OverviewService.get_snapshot(
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
        for kpi in snap.primary_kpis[:8]
    ]
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="fy.headline_kpis",
        title=ctx.title_override or f"FY Headline Metrics ({snap.period_label})",
        items=tuple(items),
        source_tables=("gold.core_monthly_fact", "gold.cashflow_efficiency_analytics"),
    )


def _exec_fy_income_expense_mix(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build FY Income & Expense category contribution chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = OverviewService.get_snapshot(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        compatibility=ctx.app_context.compatibility,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    inc_top = sorted(snap.income_breakup, key=lambda x: x.amount, reverse=True)[:6]
    exp_top = sorted(snap.expense_breakup, key=lambda x: x.amount, reverse=True)[:6]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Top Income Categories",
                "x": [p.label for p in inc_top],
                "y": [p.amount for p in inc_top],
                "marker": {"color": StudioColor.SUCCESS},
            },
            {
                "type": "bar",
                "name": "Top Expense Categories",
                "x": [p.label for p in exp_top],
                "y": [p.amount for p in exp_top],
                "marker": {"color": StudioColor.ERROR},
            },
        ],
        "layout": {
            "barmode": "group",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Category"},
            "yaxis": {"title": "FY Amount (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="fy.income_expense_mix_chart",
        title=ctx.title_override or f"FY Top Income & Expense Categories ({snap.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.cashflow_income_breakdown", "gold.cashflow_expense_breakdown"),
        caption=f"Period: {snap.period_label} · Source: gold.cashflow_income/expense_breakdown",
    )


def _exec_fy_cashflow_statement(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build FY Operating / Investing / Financing cashflow statement table."""
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
    rows = [
        {"Activity": "Opening Cash Balance", "Amount": cf.opening_cash},
        {"Activity": "Operating Cashflow (Net)", "Amount": cf.net_operating},
        {"Activity": "Investing Cashflow (Net)", "Amount": cf.net_investing},
        {"Activity": "Financing Cashflow (Net)", "Amount": cf.net_financing},
        {"Activity": "Internal Transfers (Net)", "Amount": cf.net_transfers},
        {"Activity": "Calculated Net Cashflow", "Amount": cf.calculated_net_cashflow},
        {"Activity": "Closing Cash Balance", "Amount": cf.closing_cash},
        {"Activity": "Actual Cash Change", "Amount": cf.actual_cash_change},
        {"Activity": "Unreconciled Difference", "Amount": cf.unreconciled_difference},
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="fy.cashflow_statement_table",
        title=ctx.title_override or "FY Cashflow Statement & Reconciliation",
        columns=("Activity", "Amount"),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.cashflow_activity_summary",),
    )


def _exec_fy_tax_position(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Indicative FY Tax Position table from `TaxAnalytics.fy_summary`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    tax = TaxAnalytics.fy_summary(
        profile=profile,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    rows: list[dict[str, Any]] = [
        {"Tax Dimension": "Selected FY", "Value": tax.fy or "Latest"},
        {"Tax Dimension": "Total Ledger Income", "Value": tax.total_ledger_income},
        {
            "Tax Dimension": "Realized Investment Gain / Loss",
            "Value": tax.total_investment_gain_loss,
        },
        {"Tax Dimension": "Total Tax Credits", "Value": tax.total_tax_credits},
        {
            "Tax Dimension": "Estimated Investment Tax (Indicative)",
            "Value": tax.total_estimated_investment_tax,
        },
        {
            "Tax Dimension": "Estimated Ledger Tax (Indicative)",
            "Value": tax.total_estimated_ledger_tax,
        },
        {
            "Tax Dimension": "Unrealized Tax-If-Sold (Indicative)",
            "Value": tax.indicative_unrealized_tax_liability,
        },
        {
            "Tax Dimension": "Harvestable Tax Savings (Indicative)",
            "Value": tax.indicative_harvestable_tax_savings,
        },
        {
            "Tax Dimension": "Net Indicative Tax Exposure",
            "Value": tax.indicative_net_tax_exposure,
        },
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="fy.tax_position_table",
        title=ctx.title_override
        or "Indicative FY Tax Position (Estimated, Not Statutory Liability)",
        columns=("Tax Dimension", "Value"),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.tax_financial_year_summary",),
    )


FINANCIAL_YEAR_REVIEW_DEF = ReportDefinition(
    id="financial_year_review",
    name="Financial Year Review",
    description=(
        "FYTD / Selected FY executive deck: income & expense composition, cashflow statement, "
        "net worth attribution, portfolio results, and indicative FY tax summary."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.FYTD),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="fy_title",
            title="Financial Year Review",
            subtitle="PFETL Financial Year Performance, Cashflow & Indicative Tax Deck",
        ),
        ProvenanceBlock(block_id="fy_provenance", show_snapshots=True, show_disclaimer=True),
        NarrativeBlock(
            block_id="fy_scope",
            heading="FY Boundary & Tax Disclaimer",
            body=(
                "FY boundaries use silver.dim_calendar. Tax figures reflect PFETL rules and "
                "estimate completeness metadata (indicative / estimated, not statutory liability)."
            ),
        ),
        KPIGroupBlock(
            block_id="fy_kpis",
            source_id="fy.headline_kpis",
            title="FY Headline Metrics",
        ),
        PageBreakBlock(block_id="pb_fy_1"),
        ChartBlock(
            block_id="fy_mix_chart",
            source_id="fy.income_expense_mix_chart",
            title="FY Income & Expense Category Mix",
        ),
        PageBreakBlock(block_id="pb_fy_2"),
        ChartBlock(
            block_id="fy_nw_trend",
            source_id="overview.net_worth_trend",
            title="FY Net Worth & Savings Flow",
        ),
        PageBreakBlock(block_id="pb_fy_3"),
        TableBlock(
            block_id="fy_cashflow_tbl",
            source_id="fy.cashflow_statement_table",
            title="FY Cashflow Statement & Reconciliation",
            max_rows=20,
        ),
        PageBreakBlock(block_id="pb_fy_4"),
        TableBlock(
            block_id="fy_tax_tbl",
            source_id="fy.tax_position_table",
            title="Indicative FY Tax Position",
            max_rows=20,
        ),
    ),
)


def register_financial_year_sources(registry: ReportRegistry) -> None:
    """Register Financial Year Review sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="fy.headline_kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="FY Headline Metrics",
            source_tables=("gold.core_monthly_fact", "gold.cashflow_efficiency_analytics"),
            executor=_exec_fy_headline_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="fy.income_expense_mix_chart",
            kind=ReportSourceKind.CHART,
            title="FY Income & Expense Category Mix",
            source_tables=("gold.cashflow_income_breakdown", "gold.cashflow_expense_breakdown"),
            executor=_exec_fy_income_expense_mix,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="fy.cashflow_statement_table",
            kind=ReportSourceKind.TABLE,
            title="FY Cashflow Statement",
            source_tables=("gold.cashflow_activity_summary",),
            executor=_exec_fy_cashflow_statement,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="fy.tax_position_table",
            kind=ReportSourceKind.TABLE,
            title="Indicative FY Tax Position",
            source_tables=("gold.tax_financial_year_summary",),
            executor=_exec_fy_tax_position,
        )
    )
    registry.register_definition(FINANCIAL_YEAR_REVIEW_DEF)
