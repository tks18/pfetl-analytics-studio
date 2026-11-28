"""Expense & Lifestyle Review built-in presentation template and block sources.

Owns
----
- `expense_lifestyle_review` built-in `ReportDefinition` (default: Rolling 12M).
- Block sources backed by `HouseholdAnalytics.expense_summary` and `expense_transactions`.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import HouseholdAnalytics
from pfetl_analytics_studio.reports.comparison import (
    build_period_comparison_bar_figure,
    comparison_period_context,
    enrich_table_with_comparison,
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
from pfetl_analytics_studio.ui.theme import StudioColor

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.registry import ReportRegistry, ReportSourceContext


def _exec_expense_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Expense KPI cards from `HouseholdAnalytics.expense_summary`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = HouseholdAnalytics.expense_summary(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        compatibility=ctx.app_context.compatibility,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    items = kpi_items_from_overview_kpis(
        list(snap.primary_kpis[:4]) + list(snap.secondary_kpis[:4]),
        snap.currency_id,
        limit=8,
    )
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="expense.kpis",
        title=ctx.title_override or f"Expense & Lifestyle KPIs ({snap.period_label})",
        items=tuple(items),
        source_tables=("gold.cashflow_expense_breakdown", "gold.cashflow_efficiency_analytics"),
    )


def _exec_core_vs_real_trend_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Nominal Core vs Inflation-Adjusted Core & Non-Core Expense trend."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = HouseholdAnalytics.expense_summary(
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
    core = [pt.core_expense for pt in snap.monthly_series]
    non_core = [pt.non_core_expense for pt in snap.monthly_series]
    real_core = [pt.inflation_adjusted_core or 0.0 for pt in snap.monthly_series]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Core Expense",
                "x": dates,
                "y": core,
                "marker": {"color": StudioColor.ACCENT},
            },
            {
                "type": "bar",
                "name": "Non-Core Expense",
                "x": dates,
                "y": non_core,
                "marker": {"color": StudioColor.WARNING},
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Inflation-Adjusted Core",
                "x": dates,
                "y": real_core,
                "line": {"color": StudioColor.ERROR, "width": 2.2, "dash": "dot"},
            },
        ],
        "layout": {
            "barmode": "stack",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Month End"},
            "yaxis": {"title": "Spend (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="expense.core_vs_real_trend_chart",
        title=ctx.title_override
        or f"Core vs Non-Core & Real Core Spend ({snap.chart_window_label})",
        figure_dict=figure_dict,
        source_tables=("gold.cashflow_expense_breakdown",),
        caption=f"Window: {snap.chart_window_label} · Source: gold.cashflow_expense_breakdown",
    )


def _exec_expense_category_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Top Expense Categories contribution chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = HouseholdAnalytics.expense_summary(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        compatibility=ctx.app_context.compatibility,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    top_cats = sorted(snap.category_rows, key=lambda r: r.amount, reverse=True)[:10]
    comp_ctx = comparison_period_context(ctx.report_context.period)
    if comp_ctx is not None and top_cats:
        prior_snap = HouseholdAnalytics.expense_summary(
            profile=profile,
            period_context=comp_ctx,
            run_id=ctx.report_context.pfetl_run_id,
            provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
            compatibility=ctx.app_context.compatibility,
            catalog_snapshot=ctx.app_context.catalog_snapshot,
            filters=ctx.report_context.filters,
            cache=ctx.cache,
        )
        prior_map = {r.category_name: r.amount for r in prior_snap.category_rows}
        figure_dict = (
            build_period_comparison_bar_figure(
                [c.category_name for c in top_cats],
                [c.amount for c in top_cats],
                [prior_map.get(c.category_name, 0.0) for c in top_cats],
                currency_id=snap.currency_id,
                current_label=snap.period_label,
                prior_label=comp_ctx.label,
            )
            or {}
        )
    else:
        figure_dict = {
            "data": [
                {
                    "type": "bar",
                    "name": "Category Spend",
                    "x": [c.category_name for c in top_cats],
                    "y": [c.amount for c in top_cats],
                    "marker": {"color": StudioColor.ERROR},
                }
            ],
            "layout": {
                "paper_bgcolor": "rgba(0,0,0,0)",
                "plot_bgcolor": "rgba(13,17,23,0.65)",
                "font": {"color": StudioColor.TEXT},
                "xaxis": {"title": "Expense Category"},
                "yaxis": {"title": "Spend (INR)"},
            },
        }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="expense.category_contribution_chart",
        title=ctx.title_override or f"Top 10 Expense Categories ({snap.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.cashflow_expense_breakdown",),
        caption=f"Period: {snap.period_label} · Source: gold.cashflow_expense_breakdown",
    )


def _exec_expense_category_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Expense Category & Spend-Type table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = HouseholdAnalytics.expense_summary(
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
            "Category": c.category_name,
            "Group": c.category_group,
            "Spend Type": c.spend_type,
            "Core": "Yes" if c.is_core else "No",
            "Amount": c.amount,
            "Trailing 3M Avg": c.trailing_3m_avg,
            "YTD": c.cumulative_ytd,
            "Share (%)": c.share_pct,
        }
        for c in sorted(snap.category_rows, key=lambda r: r.amount, reverse=True)
    ]
    cols: tuple[str, ...] = (
        "Category",
        "Group",
        "Spend Type",
        "Core",
        "Amount",
        "Trailing 3M Avg",
        "YTD",
        "Share (%)",
    )
    comp_ctx = comparison_period_context(ctx.report_context.period)
    if comp_ctx is not None:
        prior_snap = HouseholdAnalytics.expense_summary(
            profile=profile,
            period_context=comp_ctx,
            run_id=ctx.report_context.pfetl_run_id,
            provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
            compatibility=ctx.app_context.compatibility,
            catalog_snapshot=ctx.app_context.catalog_snapshot,
            filters=ctx.report_context.filters,
            cache=ctx.cache,
        )
        prior_by_key = {r.category_name: {"Amount": r.amount} for r in prior_snap.category_rows}
        cols, rows = enrich_table_with_comparison(
            cols,
            rows,
            prior_by_key,
            key_col="Category",
            value_col="Amount",
            comparison_label=comp_ctx.label,
        )
    return RenderedTableBlock(
        block_id=block_id,
        source_id="expense.category_table",
        title=ctx.title_override or "Expense Category & Spend-Type Breakdown",
        columns=cols,
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.cashflow_expense_breakdown",),
    )


def _exec_expense_transactions_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build bounded Top Expense Transactions appendix (empty-safe)."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    tx_page = HouseholdAnalytics.expense_transactions(
        profile=profile,
        period_context=ctx.report_context.period,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        page=1,
        page_size=min(ctx.max_rows, 25),
    )
    rows: list[dict[str, Any]] = [
        {
            "Date": tx.tx_date.isoformat(),
            "Category": tx.category_name,
            "Account": tx.account_name,
            "Core": "Yes" if tx.is_core else "No",
            "Amount": tx.base_amount,
            "Description": tx.description[:60],
        }
        for tx in tx_page.rows
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="expense.top_transactions_table",
        title=ctx.title_override or "Expense Transactions Appendix (Bounded)",
        columns=("Date", "Category", "Account", "Core", "Amount", "Description"),
        rows=tuple(rows),
        total_rows=tx_page.total_count,
        truncated=tx_page.total_count > len(rows),
        source_tables=("silver.fact_household_transactions",),
    )


EXPENSE_LIFESTYLE_REVIEW_DEF = ReportDefinition(
    id="expense_lifestyle_review",
    name="Expense & Lifestyle Review",
    description=(
        "Rolling 12M expense deck: Core vs Non-Core & inflation-adjusted spend, "
        "category/group contribution, trailing 3M averages, and bounded transaction drill."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.ROLLING_12M),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="exp_title",
            title="Expense & Lifestyle Review",
            subtitle="Core vs Discretionary Spend, Real Growth & Category Contribution",
        ),
        ProvenanceBlock(block_id="exp_provenance", show_snapshots=True, show_disclaimer=False),
        NarrativeBlock(
            block_id="exp_scope",
            heading="Classification Fidelity",
            body=(
                "Core, Non-Core, Discretionary, Investment, and Fixed flags come directly from "
                "PFETL Gold/Silver tables without Studio regex inference."
            ),
        ),
        KPIGroupBlock(
            block_id="exp_kpis",
            source_id="expense.kpis",
            title="Expense & Lifestyle KPIs",
        ),
        PageBreakBlock(block_id="pb_exp_1"),
        ChartBlock(
            block_id="exp_trend_chart",
            source_id="expense.core_vs_real_trend_chart",
            title="Core vs Non-Core & Inflation-Adjusted Core Trend",
        ),
        PageBreakBlock(block_id="pb_exp_2"),
        ChartBlock(
            block_id="exp_cat_chart",
            source_id="expense.category_contribution_chart",
            title="Top 10 Expense Categories",
        ),
        ChartBlock(
            block_id="exp_income_compare_chart",
            source_id="monthly.income_expense_cashflow_chart",
            title="Monthly Income vs Expense Trajectory",
        ),
        PageBreakBlock(block_id="pb_exp_3"),
        TableBlock(
            block_id="exp_cat_tbl",
            source_id="expense.category_table",
            title="Expense Category Breakdown",
            max_rows=50,
        ),
        PageBreakBlock(block_id="pb_exp_4"),
        TableBlock(
            block_id="exp_tx_tbl",
            source_id="expense.top_transactions_table",
            title="Bounded Expense Transactions Appendix",
            max_rows=25,
        ),
    ),
)


def register_expense_sources(registry: ReportRegistry) -> None:
    """Register Expense & Lifestyle Review sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="expense.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Expense & Lifestyle KPIs",
            source_tables=(
                "gold.cashflow_expense_breakdown",
                "gold.cashflow_efficiency_analytics",
            ),
            executor=_exec_expense_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="expense.core_vs_real_trend_chart",
            kind=ReportSourceKind.CHART,
            title="Core vs Real Core Spend Trend",
            source_tables=("gold.cashflow_expense_breakdown",),
            executor=_exec_core_vs_real_trend_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="expense.category_contribution_chart",
            kind=ReportSourceKind.CHART,
            title="Top Expense Categories",
            source_tables=("gold.cashflow_expense_breakdown",),
            executor=_exec_expense_category_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="expense.category_table",
            kind=ReportSourceKind.TABLE,
            title="Expense Category Breakdown",
            source_tables=("gold.cashflow_expense_breakdown",),
            executor=_exec_expense_category_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="expense.top_transactions_table",
            kind=ReportSourceKind.TABLE,
            title="Bounded Expense Transactions Appendix",
            source_tables=("silver.fact_household_transactions",),
            executor=_exec_expense_transactions_table,
        )
    )
    registry.register_definition(EXPENSE_LIFESTYLE_REVIEW_DEF)
