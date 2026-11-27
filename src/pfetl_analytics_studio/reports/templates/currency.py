"""Multi-Currency Exposure Review built-in presentation template and block sources.

Owns
----
- `multi_currency_exposure_review` built-in `ReportDefinition` (default: Latest Complete Month).
- Block sources backed by `InvestmentAnalytics.performance(..., AllocationDimension.CURRENCY)`.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import InvestmentAnalytics
from pfetl_analytics_studio.analytics.investments_models import (
    AllocationDimension,
    FxExposureRow,
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
from pfetl_analytics_studio.semantic import FilterSet, PeriodPreset, PeriodRequest
from pfetl_analytics_studio.ui.formatting import format_currency, format_percent
from pfetl_analytics_studio.ui.theme import StudioColor

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.registry import ReportRegistry, ReportSourceContext


def _exec_currency_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Multi-Currency exposure KPI cards from `InvestmentAnalytics.performance`."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    perf = InvestmentAnalytics.performance(
        profile=profile,
        dimension=AllocationDimension.CURRENCY,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    total_base = sum(r.base_current for r in perf.fx_currency_rows)
    total_invested = sum(r.base_invested for r in perf.fx_currency_rows)
    non_base = sum(r.base_current for r in perf.fx_currency_rows if not r.bucket.endswith("_INR"))
    fx_share = (non_base / total_base) if total_base > 0 else 0.0
    items = (
        ReportKpiItem(
            label="Total Base Exposure",
            value=total_base,
            formatted_value=format_currency(total_base, currency_id=perf.currency_id),
            subtitle=f"Invested: {format_currency(total_invested, currency_id=perf.currency_id)}",
            source_table="gold.investment_by_currency",
        ),
        ReportKpiItem(
            label="Foreign Currency Exposure",
            value=non_base,
            formatted_value=format_currency(non_base, currency_id=perf.currency_id),
            subtitle=f"{format_percent(fx_share)} of portfolio",
            source_table="gold.investment_by_currency",
        ),
        ReportKpiItem(
            label="Currencies Held",
            value=len(perf.fx_currency_rows),
            formatted_value=f"{len(perf.fx_currency_rows)} currencies",
            subtitle=f"{len(perf.fx_country_rows)} countries",
            source_table="gold.investment_by_currency",
        ),
        ReportKpiItem(
            label="As-Of Period",
            value=perf.period_label,
            formatted_value=perf.period_label,
            subtitle=f"Closing: {perf.as_of_date or 'Latest'}",
            source_table="gold.investment_by_currency",
        ),
    )
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="currency.kpis",
        title=ctx.title_override or f"Multi-Currency Exposure KPIs ({perf.period_label})",
        items=items,
        source_tables=("gold.investment_by_currency", "gold.investment_by_country"),
    )


def _exec_currency_exposure_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Currency Base Invested vs Current Value chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    perf = InvestmentAnalytics.performance(
        profile=profile,
        dimension=AllocationDimension.CURRENCY,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    rows = sorted(perf.fx_currency_rows, key=lambda r: r.base_current, reverse=True)
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Base Invested",
                "x": [r.bucket for r in rows],
                "y": [r.base_invested for r in rows],
                "marker": {"color": StudioColor.MUTED},
            },
            {
                "type": "bar",
                "name": "Base Current Value",
                "x": [r.bucket for r in rows],
                "y": [r.base_current for r in rows],
                "marker": {"color": StudioColor.ACCENT},
            },
        ],
        "layout": {
            "barmode": "group",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Currency"},
            "yaxis": {"title": "Base Value (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="currency.exposure_chart",
        title=ctx.title_override or f"Currency Exposure (Base Value) ({perf.period_label})",
        figure_dict=figure_dict,
        source_tables=("gold.investment_by_currency",),
        caption=f"As of {perf.as_of_date or perf.period_label} · Source: gold.investment_by_currency",
    )


def _fx_rows_to_dicts(rows: Sequence[FxExposureRow], bucket_label: str) -> list[dict[str, Any]]:
    """Convert `FxExposureRow` sequence into formatted table dictionaries."""
    return [
        {
            bucket_label: r.bucket,
            "Base Current": r.base_current,
            "Local Current": r.local_current,
            "FX Rate": r.fx_rate,
            "Blended FX": r.blended_buy_rate,
            "FX Apprec (%)": (
                (r.currency_appreciation_pct * 100.0)
                if r.currency_appreciation_pct is not None
                else None
            ),
            "Local XIRR (%)": (r.local_xirr * 100.0) if r.local_xirr is not None else None,
            "Base XIRR (%)": (r.base_xirr * 100.0) if r.base_xirr is not None else None,
            "FX XIRR Impact (%)": (
                (r.fx_xirr_impact * 100.0) if r.fx_xirr_impact is not None else None
            ),
        }
        for r in sorted(rows, key=lambda item: item.base_current, reverse=True)
    ]


def _exec_currency_breakdown_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Currency-level Base/Local & FX Return Decomposition table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    perf = InvestmentAnalytics.performance(
        profile=profile,
        dimension=AllocationDimension.CURRENCY,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    rows = _fx_rows_to_dicts(perf.fx_currency_rows, "Currency")
    return RenderedTableBlock(
        block_id=block_id,
        source_id="currency.fx_breakdown_table",
        title=ctx.title_override or "Currency Base vs Local & FX XIRR Impact",
        columns=(
            "Currency",
            "Base Current",
            "Local Current",
            "FX Rate",
            "Blended FX",
            "FX Apprec (%)",
            "Local XIRR (%)",
            "Base XIRR (%)",
            "FX XIRR Impact (%)",
        ),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.investment_by_currency",),
    )


def _exec_country_breakdown_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Country-level Base/Local & FX Return Decomposition table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    perf = InvestmentAnalytics.performance(
        profile=profile,
        dimension=AllocationDimension.COUNTRY,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        filters=ctx.report_context.filters,
        cache=ctx.cache,
    )
    rows = _fx_rows_to_dicts(perf.fx_country_rows, "Country")
    return RenderedTableBlock(
        block_id=block_id,
        source_id="currency.country_breakdown_table",
        title=ctx.title_override or "Country Base vs Local & FX Impact",
        columns=(
            "Country",
            "Base Current",
            "Local Current",
            "FX Rate",
            "Blended FX",
            "FX Apprec (%)",
            "Local XIRR (%)",
            "Base XIRR (%)",
            "FX XIRR Impact (%)",
        ),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.investment_by_country",),
    )


MULTI_CURRENCY_EXPOSURE_REVIEW_DEF = ReportDefinition(
    id="multi_currency_exposure_review",
    name="Multi-Currency Exposure Review",
    description=(
        "Latest-close / custom FX deck: base vs local values, current & blended FX rates, "
        "currency appreciation, local vs base XIRR, and country exposure."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="fx_title",
            title="Multi-Currency Exposure Review",
            subtitle="Base vs Local Valuation, FX Appreciation & FX XIRR Impact",
        ),
        ProvenanceBlock(block_id="fx_provenance", show_snapshots=True, show_disclaimer=True),
        NarrativeBlock(
            block_id="fx_scope",
            heading="Canonical FX Attribution",
            body=(
                "Local/base values, blended buy FX rates, currency appreciation, and FX XIRR "
                "impact come directly from PFETL Gold marts; no synthetic FX rates are computed."
            ),
        ),
        KPIGroupBlock(
            block_id="fx_kpis",
            source_id="currency.kpis",
            title="Multi-Currency Exposure KPIs",
        ),
        PageBreakBlock(block_id="pb_fx_1"),
        ChartBlock(
            block_id="fx_chart",
            source_id="currency.exposure_chart",
            title="Currency Allocation (Base Value)",
        ),
        PageBreakBlock(block_id="pb_fx_2"),
        TableBlock(
            block_id="fx_currency_tbl",
            source_id="currency.fx_breakdown_table",
            title="Currency Base vs Local & FX XIRR Impact",
            max_rows=30,
        ),
        PageBreakBlock(block_id="pb_fx_3"),
        TableBlock(
            block_id="fx_country_tbl",
            source_id="currency.country_breakdown_table",
            title="Country Base vs Local & FX Impact",
            max_rows=30,
        ),
    ),
)


def register_currency_sources(registry: ReportRegistry) -> None:
    """Register Multi-Currency Exposure Review sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="currency.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Multi-Currency Exposure KPIs",
            source_tables=("gold.investment_by_currency", "gold.investment_by_country"),
            executor=_exec_currency_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="currency.exposure_chart",
            kind=ReportSourceKind.CHART,
            title="Currency Exposure Chart",
            source_tables=("gold.investment_by_currency",),
            executor=_exec_currency_exposure_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="currency.fx_breakdown_table",
            kind=ReportSourceKind.TABLE,
            title="Currency FX Breakdown",
            source_tables=("gold.investment_by_currency",),
            executor=_exec_currency_breakdown_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="currency.country_breakdown_table",
            kind=ReportSourceKind.TABLE,
            title="Country FX Breakdown",
            source_tables=("gold.investment_by_country",),
            executor=_exec_country_breakdown_table,
        )
    )
    registry.register_definition(MULTI_CURRENCY_EXPOSURE_REVIEW_DEF)
