"""Income Resilience Review built-in presentation template and block sources.

Owns
----
- `income_resilience_review` built-in `ReportDefinition` (default: Rolling 12M).
- Block sources backed by `HouseholdAnalytics.income_summary` and `income_transactions`.

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


def _exec_income_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Income Resilience KPI cards from `HouseholdAnalytics.income_summary`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = HouseholdAnalytics.income_summary(
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
        source_id="income.kpis",
        title=ctx.title_override or f"Income Resilience KPIs ({snap.period_label})",
        items=tuple(items),
        source_tables=("gold.cashflow_income_breakdown", "gold.cashflow_efficiency_analytics"),
    )


def _exec_active_vs_passive_trend(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Active vs Passive + Trailing 3M Average Income trend chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = HouseholdAnalytics.income_summary(
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
    active = [pt.active_income for pt in snap.monthly_series]
    passive = [pt.passive_income for pt in snap.monthly_series]
    avg_3m = [pt.trailing_3m_avg or 0.0 for pt in snap.monthly_series]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Active Income",
                "x": dates,
                "y": active,
                "marker": {"color": StudioColor.ACCENT},
            },
            {
                "type": "bar",
                "name": "Passive Income",
                "x": dates,
                "y": passive,
                "marker": {"color": StudioColor.SUCCESS},
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Trailing 3M Avg",
                "x": dates,
                "y": avg_3m,
                "line": {"color": StudioColor.RECOVERY, "width": 2.2},
            },
        ],
        "layout": {
            "barmode": "stack",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Month End"},
            "yaxis": {"title": "Income (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="income.active_vs_passive_trend_chart",
        title=ctx.title_override or f"Active vs Passive Income ({snap.chart_window_label})",
        figure_dict=figure_dict,
        source_tables=("gold.cashflow_income_breakdown",),
        caption=f"Window: {snap.chart_window_label} · Source: gold.cashflow_income_breakdown",
    )


def _exec_income_category_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Top Income Categories contribution chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = HouseholdAnalytics.income_summary(
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
        prior_snap = HouseholdAnalytics.income_summary(
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
                    "name": "Category Income",
                    "x": [c.category_name for c in top_cats],
                    "y": [c.amount for c in top_cats],
                    "marker": {"color": StudioColor.SUCCESS},
                }
            ],
            "layout": {
                "paper_bgcolor": "rgba(0,0,0,0)",
                "plot_bgcolor": "rgba(13,17,23,0.65)",
                "font": {"color": StudioColor.TEXT},
                "xaxis": {"title": "Income Category"},
                "yaxis": {"title": "Income (INR)"},
            },
        }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="income.category_composition_chart",
        title=ctx.title_override or f"Top 10 Income Streams ({snap.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.cashflow_income_breakdown",),
        caption=f"Period: {snap.period_label} · Source: gold.cashflow_income_breakdown",
    )


def _exec_income_category_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Income Category & Active/Passive/Taxability table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = HouseholdAnalytics.income_summary(
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
            "Stream": "Passive" if c.is_passive else ("Active" if c.is_active else "Other"),
            "Taxability": c.taxability,
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
        "Stream",
        "Taxability",
        "Amount",
        "Trailing 3M Avg",
        "YTD",
        "Share (%)",
    )
    comp_ctx = comparison_period_context(ctx.report_context.period)
    if comp_ctx is not None:
        prior_snap = HouseholdAnalytics.income_summary(
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
        source_id="income.category_table",
        title=ctx.title_override or "Income Category & Resilience Breakdown",
        columns=cols,
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.cashflow_income_breakdown",),
    )


def _exec_income_transactions_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build bounded Top Income Transactions appendix (empty-safe)."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    tx_page = HouseholdAnalytics.income_transactions(
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
            "Passive": "Yes" if tx.is_passive else "No",
            "Amount": tx.base_amount,
            "Description": tx.description[:60],
        }
        for tx in tx_page.rows
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="income.top_transactions_table",
        title=ctx.title_override or "Income Transactions Appendix (Bounded)",
        columns=("Date", "Category", "Account", "Passive", "Amount", "Description"),
        rows=tuple(rows),
        total_rows=tx_page.total_count,
        truncated=tx_page.total_count > len(rows),
        source_tables=("silver.fact_household_transactions",),
    )


INCOME_RESILIENCE_REVIEW_DEF = ReportDefinition(
    id="income_resilience_review",
    name="Income Resilience Review",
    description=(
        "Rolling 12M income deck: active vs passive/dividend/interest mix, "
        "passive-to-core-expense coverage, active dependency, and bounded source drill."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.ROLLING_12M),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="inc_title",
            title="Income Resilience Review",
            subtitle="Active vs Passive Income Mix, Coverage & Source Stability",
        ),
        ProvenanceBlock(block_id="inc_provenance", show_snapshots=True, show_disclaimer=False),
        NarrativeBlock(
            block_id="inc_scope",
            heading="Native Coverage & Dependency Semantics",
            body=(
                "Passive-income coverage and active-income dependency are read directly from "
                "PFETL Gold marts and never averaged across incompatible periods."
            ),
        ),
        KPIGroupBlock(
            block_id="inc_kpis",
            source_id="income.kpis",
            title="Income Resilience KPIs",
        ),
        PageBreakBlock(block_id="pb_inc_1"),
        ChartBlock(
            block_id="inc_trend_chart",
            source_id="income.active_vs_passive_trend_chart",
            title="Active vs Passive Income & Trailing 3M Average",
        ),
        PageBreakBlock(block_id="pb_inc_2"),
        ChartBlock(
            block_id="inc_cat_chart",
            source_id="income.category_composition_chart",
            title="Top 10 Income Streams",
        ),
        ChartBlock(
            block_id="inc_cashflow_compare_chart",
            source_id="monthly.income_expense_cashflow_chart",
            title="Monthly Income vs Expense Coverage",
        ),
        PageBreakBlock(block_id="pb_inc_3"),
        TableBlock(
            block_id="inc_cat_tbl",
            source_id="income.category_table",
            title="Income Category Breakdown",
            max_rows=50,
        ),
        PageBreakBlock(block_id="pb_inc_4"),
        TableBlock(
            block_id="inc_tx_tbl",
            source_id="income.top_transactions_table",
            title="Bounded Income Transactions Appendix",
            max_rows=25,
        ),
    ),
)


def register_income_sources(registry: ReportRegistry) -> None:
    """Register Income Resilience Review sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="income.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Income Resilience KPIs",
            source_tables=(
                "gold.cashflow_income_breakdown",
                "gold.cashflow_efficiency_analytics",
            ),
            executor=_exec_income_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="income.active_vs_passive_trend_chart",
            kind=ReportSourceKind.CHART,
            title="Active vs Passive Income Trend",
            source_tables=("gold.cashflow_income_breakdown",),
            executor=_exec_active_vs_passive_trend,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="income.category_composition_chart",
            kind=ReportSourceKind.CHART,
            title="Top Income Streams",
            source_tables=("gold.cashflow_income_breakdown",),
            executor=_exec_income_category_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="income.category_table",
            kind=ReportSourceKind.TABLE,
            title="Income Category Breakdown",
            source_tables=("gold.cashflow_income_breakdown",),
            executor=_exec_income_category_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="income.top_transactions_table",
            kind=ReportSourceKind.TABLE,
            title="Bounded Income Transactions Appendix",
            source_tables=("silver.fact_household_transactions",),
            executor=_exec_income_transactions_table,
        )
    )
    registry.register_definition(INCOME_RESILIENCE_REVIEW_DEF)
