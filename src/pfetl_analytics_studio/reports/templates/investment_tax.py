"""Investment Tax & Harvesting built-in presentation template and block sources.

Owns
----
- `investment_tax_harvesting` built-in `ReportDefinition` (default: FYTD).
- Block sources backed by `TaxAnalytics.fy_summary`, `harvesting_snapshot`, and `tax_events`.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import TaxAnalytics
from pfetl_analytics_studio.analytics.tax_models import TaxEventFilter
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
from pfetl_analytics_studio.ui.formatting import format_currency
from pfetl_analytics_studio.ui.theme import StudioColor

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.registry import ReportRegistry, ReportSourceContext


def _resolve_fy_arg(ctx: ReportSourceContext) -> str | None:
    """Resolve FY string from `ReportContext.period` or return `None` for latest."""
    start = ctx.report_context.period.start_date
    end = ctx.report_context.period.end_date
    if start is None or end is None:
        return None
    if end.month >= 4:
        return f"FY{end.year}-{str(end.year + 1)[-2:]}"
    return f"FY{start.year}-{str(end.year)[-2:]}" if start.year < end.year else None


def _exec_tax_inv_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build Investment Tax & Harvesting KPIs (realized separated from unrealized)."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    fy_arg = _resolve_fy_arg(ctx)
    fy_snap = TaxAnalytics.fy_summary(
        profile=profile,
        fy=fy_arg,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    harvest = TaxAnalytics.harvesting_snapshot(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    cid = fy_snap.currency_id
    items = (
        ReportKpiItem(
            label="Realized Investment Gain/Loss",
            value=fy_snap.total_investment_gain_loss,
            formatted_value=format_currency(fy_snap.total_investment_gain_loss, currency_id=cid),
            subtitle=f"{fy_snap.fy or 'Latest'} · Realized",
            source_table="gold.tax_financial_year_summary",
        ),
        ReportKpiItem(
            label="Est. Realized Inv. Tax (Indicative)",
            value=fy_snap.total_estimated_investment_tax,
            formatted_value=format_currency(
                fy_snap.total_estimated_investment_tax, currency_id=cid
            ),
            subtitle="Indicative / estimated, not statutory",
            source_table="gold.tax_financial_year_summary",
        ),
        ReportKpiItem(
            label="Unrealized Tax-If-Sold (Indicative)",
            value=harvest.total_tax_if_sold,
            formatted_value=format_currency(harvest.total_tax_if_sold, currency_id=cid),
            subtitle=f"{len(harvest.maturing_lots)} maturing lots",
            source_table="silver.fact_investment_lot_analytics",
        ),
        ReportKpiItem(
            label="Harvestable Unrealized Loss",
            value=harvest.total_harvestable_loss,
            formatted_value=format_currency(harvest.total_harvestable_loss, currency_id=cid),
            subtitle=f"{len(harvest.harvest_lots)} harvest candidate lots",
            source_table="silver.fact_investment_lot_analytics",
        ),
        ReportKpiItem(
            label="Indicative Harvestable Tax Savings",
            value=fy_snap.indicative_harvestable_tax_savings,
            formatted_value=format_currency(
                fy_snap.indicative_harvestable_tax_savings, currency_id=cid
            ),
            subtitle="Estimated offset potential",
            source_table="gold.tax_financial_year_summary",
        ),
        ReportKpiItem(
            label="Net Indicative Tax Exposure",
            value=fy_snap.indicative_net_tax_exposure,
            formatted_value=format_currency(fy_snap.indicative_net_tax_exposure, currency_id=cid),
            subtitle="Indicative / estimated only",
            source_table="gold.tax_financial_year_summary",
        ),
    )
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="tax_inv.kpis",
        title=ctx.title_override
        or f"Indicative Investment Tax & Harvesting ({fy_snap.fy or 'Latest'})",
        items=items,
        source_tables=("gold.tax_financial_year_summary", "silver.fact_investment_lot_analytics"),
    )


def _exec_harvest_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Top Harvestable Loss Lots vs Estimated Tax Savings chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    harvest = TaxAnalytics.harvesting_snapshot(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    top_lots = sorted(harvest.harvest_lots, key=lambda lt: lt.unrealized_pl)[:10]
    labels = [f"{lt.instrument_name[:18]} ({lt.lot_id[-6:]})" for lt in top_lots]
    losses = [abs(lt.unrealized_pl) for lt in top_lots]
    tax_impact = [abs(lt.tax_if_sold or 0.0) for lt in top_lots]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Unrealized Loss",
                "x": labels,
                "y": losses,
                "marker": {"color": StudioColor.ERROR},
            },
            {
                "type": "bar",
                "name": "Indicative Tax Impact",
                "x": labels,
                "y": tax_impact,
                "marker": {"color": StudioColor.SUCCESS},
            },
        ],
        "layout": {
            "barmode": "group",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Lot (Instrument · Lot ID)"},
            "yaxis": {"title": "Amount (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="tax_inv.harvest_chart",
        title=ctx.title_override or "Top Harvestable Loss Lots (Indicative)",
        figure_dict=figure_dict,
        source_tables=("silver.fact_investment_lot_analytics",),
        caption="Indicative / estimated, not statutory liability · Source: silver.fact_investment_lot_analytics",
    )


def _exec_harvest_lots_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Prioritized Harvestable & Maturing Lots table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    harvest = TaxAnalytics.harvesting_snapshot(
        profile=profile,
        period_context=ctx.report_context.period,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    combined = list(harvest.harvest_lots) + [
        lt for lt in harvest.maturing_lots if lt not in harvest.harvest_lots
    ]
    rows: list[dict[str, Any]] = [
        {
            "Lot ID": lt.lot_id,
            "Instrument": lt.instrument_name,
            "Holding": lt.holding_type,
            "Days LTCG": lt.days_to_ltcg,
            "Cost Basis": lt.buy_value,
            "Market Value": lt.close_value,
            "Unrealized P&L": lt.unrealized_pl,
            "Tax If Sold": lt.tax_if_sold,
            "Recommendation": lt.harvest_recommendation or "HOLD",
        }
        for lt in combined
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="tax_inv.harvest_lots_table",
        title=ctx.title_override or "Harvestable & Maturing Investment Lots (Indicative)",
        columns=(
            "Lot ID",
            "Instrument",
            "Holding",
            "Days LTCG",
            "Cost Basis",
            "Market Value",
            "Unrealized P&L",
            "Tax If Sold",
            "Recommendation",
        ),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("silver.fact_investment_lot_analytics",),
    )


def _exec_realized_events_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build bounded FY Realized Investment Tax Events table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    fy_snap = TaxAnalytics.fy_summary(
        profile=profile,
        fy=_resolve_fy_arg(ctx),
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    events = TaxAnalytics.tax_events(
        profile=profile,
        fy=fy_snap.fy or "FY2025-26",
        filter_spec=TaxEventFilter(),
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    limit = min(ctx.max_rows, 30)
    sliced = events.rows[:limit]
    rows: list[dict[str, Any]] = [
        {
            "Date": ev.event_date.isoformat() if ev.event_date else "—",
            "Sub-Head": ev.tax_sub_head,
            "Source": ev.source_type,
            "ISIN": ev.isin or "—",
            "Amount": ev.amount_inr,
            "Est. Tax": ev.estimated_tax,
            "Status": ev.tax_status,
        }
        for ev in sliced
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="tax_inv.realized_events_table",
        title=ctx.title_override or "Canonical Realized & Tax Events (Indicative)",
        columns=("Date", "Sub-Head", "Source", "ISIN", "Amount", "Est. Tax", "Status"),
        rows=tuple(rows),
        total_rows=len(events.rows),
        truncated=len(events.rows) > len(rows),
        source_tables=("silver.fact_tax_events",),
    )


INVESTMENT_TAX_HARVESTING_DEF = ReportDefinition(
    id="investment_tax_harvesting",
    name="Investment Tax & Harvesting",
    description=(
        "FY & latest-close tax harvesting deck: realized gains/tax separated from "
        "unrealized tax-if-sold, harvestable losses, LTCG maturity, and prioritized lots."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.FYTD),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="tax_inv_title",
            title="Investment Tax & Harvesting",
            subtitle="Indicative / Estimated Tax Exposure & Lot-Level Harvesting Deck",
        ),
        ProvenanceBlock(block_id="tax_inv_prov", show_snapshots=True, show_disclaimer=True),
        NarrativeBlock(
            block_id="tax_inv_scope",
            heading="Indicative Tax & Realized/Unrealized Separation",
            body=(
                "Indicative / estimated figures only, not statutory tax liability. Realized FY "
                "investment events are kept strictly separate from unrealized tax-if-sold."
            ),
        ),
        KPIGroupBlock(
            block_id="tax_inv_kpis",
            source_id="tax_inv.kpis",
            title="Investment Tax & Harvesting KPIs (Indicative)",
        ),
        PageBreakBlock(block_id="pb_tax_inv_1"),
        ChartBlock(
            block_id="tax_inv_chart",
            source_id="tax_inv.harvest_chart",
            title="Top Harvestable Loss Lots (Indicative)",
        ),
        PageBreakBlock(block_id="pb_tax_inv_2"),
        TableBlock(
            block_id="tax_inv_lots",
            source_id="tax_inv.harvest_lots_table",
            title="Harvestable & Maturing Lots",
            max_rows=50,
        ),
        PageBreakBlock(block_id="pb_tax_inv_3"),
        TableBlock(
            block_id="tax_inv_events",
            source_id="tax_inv.realized_events_table",
            title="Realized & Canonical Tax Events",
            max_rows=30,
        ),
    ),
)


def register_investment_tax_sources(registry: ReportRegistry) -> None:
    """Register Investment Tax & Harvesting sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="tax_inv.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Investment Tax & Harvesting KPIs",
            source_tables=(
                "gold.tax_financial_year_summary",
                "silver.fact_investment_lot_analytics",
            ),
            executor=_exec_tax_inv_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="tax_inv.harvest_chart",
            kind=ReportSourceKind.CHART,
            title="Top Harvestable Loss Lots",
            source_tables=("silver.fact_investment_lot_analytics",),
            executor=_exec_harvest_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="tax_inv.harvest_lots_table",
            kind=ReportSourceKind.TABLE,
            title="Harvestable & Maturing Lots",
            source_tables=("silver.fact_investment_lot_analytics",),
            executor=_exec_harvest_lots_table,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="tax_inv.realized_events_table",
            kind=ReportSourceKind.TABLE,
            title="Canonical Tax Events",
            source_tables=("silver.fact_tax_events",),
            executor=_exec_realized_events_table,
        )
    )
    registry.register_definition(INVESTMENT_TAX_HARVESTING_DEF)
