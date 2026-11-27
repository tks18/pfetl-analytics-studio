"""Tax Position Review built-in presentation template and block sources.

Owns
----
- `tax_position_review` built-in `ReportDefinition` (default: FYTD).
- Block sources backed by `TaxAnalytics.fy_summary`, `income_breakdown`, `reconciliation`.

Does not own
------------
- Direct DuckDB SQL queries (`analytics/`) or NiceGUI widgets (`pages/reports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pfetl_analytics_studio.analytics import TaxAnalytics
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


def _exec_tax_pos_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build FY Tax Position & Completeness KPIs."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    snap = TaxAnalytics.fy_summary(
        profile=profile,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    cid = snap.currency_id
    items = (
        ReportKpiItem(
            label="Total Ledger Income",
            value=snap.total_ledger_income,
            formatted_value=format_currency(snap.total_ledger_income, currency_id=cid),
            subtitle=f"{snap.fy or 'Latest'} · Ready: {snap.ready_count}/{snap.total_event_count}",
            source_table="gold.tax_financial_year_summary",
        ),
        ReportKpiItem(
            label="Investment Gain / Loss",
            value=snap.total_investment_gain_loss,
            formatted_value=format_currency(snap.total_investment_gain_loss, currency_id=cid),
            subtitle=f"Check required: {snap.check_required_count}",
            source_table="gold.tax_financial_year_summary",
        ),
        ReportKpiItem(
            label="Est. Ledger Tax (Indicative)",
            value=snap.total_estimated_ledger_tax,
            formatted_value=format_currency(snap.total_estimated_ledger_tax, currency_id=cid),
            subtitle=(
                "Complete"
                if snap.ledger_tax_estimate_complete
                else f"Incomplete ({snap.ledger_tax_estimate_missing_count} missing)"
            ),
            source_table="gold.tax_financial_year_summary",
        ),
        ReportKpiItem(
            label="Est. Inv. Tax (Indicative)",
            value=snap.total_estimated_investment_tax,
            formatted_value=format_currency(snap.total_estimated_investment_tax, currency_id=cid),
            subtitle=(
                "Complete"
                if snap.investment_tax_estimate_complete
                else f"Incomplete ({snap.investment_tax_estimate_missing_count} missing)"
            ),
            source_table="gold.tax_financial_year_summary",
        ),
        ReportKpiItem(
            label="Total Tax Credits",
            value=snap.total_tax_credits,
            formatted_value=format_currency(snap.total_tax_credits, currency_id=cid),
            subtitle="TDS / Advance tax credits",
            source_table="gold.tax_financial_year_summary",
        ),
        ReportKpiItem(
            label="Net Indicative Exposure",
            value=snap.indicative_net_tax_exposure,
            formatted_value=format_currency(snap.indicative_net_tax_exposure, currency_id=cid),
            subtitle="Indicative / estimated only",
            source_table="gold.tax_financial_year_summary",
        ),
    )
    return RenderedKPIGroupBlock(
        block_id=block_id,
        source_id="tax_pos.kpis",
        title=ctx.title_override or f"Indicative FY Tax Position ({snap.fy or 'Latest'})",
        items=items,
        source_tables=("gold.tax_financial_year_summary",),
    )


def _exec_head_breakdown_chart(ctx: ReportSourceContext, block_id: str) -> RenderedChartBlock:
    """Build Income Head & Sub-Head Tax Breakdown chart."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    fy_snap = TaxAnalytics.fy_summary(
        profile=profile,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    inc = TaxAnalytics.income_breakdown(
        profile=profile,
        fy=fy_snap.fy or "FY2025-26",
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    top_rows = sorted(inc.rows, key=lambda r: abs(r.event_amount_inr), reverse=True)[:10]
    labels = [f"{r.income_head}: {r.tax_sub_head}"[:24] for r in top_rows]
    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "bar",
                "name": "Event Amount",
                "x": labels,
                "y": [r.event_amount_inr for r in top_rows],
                "marker": {"color": StudioColor.ACCENT},
            },
            {
                "type": "bar",
                "name": "Est. Tax (Indicative)",
                "x": labels,
                "y": [r.estimated_tax_inr for r in top_rows],
                "marker": {"color": StudioColor.WARNING},
            },
        ],
        "layout": {
            "barmode": "group",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "xaxis": {"title": "Income Head / Sub-Head"},
            "yaxis": {"title": "Amount (INR)"},
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="tax_pos.head_breakdown_chart",
        title=ctx.title_override or f"Tax Income Head Breakdown ({inc.fy})",
        figure_dict=figure_dict,
        source_tables=("gold.tax_income_breakdown",),
        caption="Indicative / estimated, not statutory liability · Source: gold.tax_income_breakdown",
    )


def _exec_tax_reconciliation_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build Ledger-vs-Quant Tax Reconciliation & Exclusions table."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected.")
    fy_snap = TaxAnalytics.fy_summary(
        profile=profile,
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    recon = TaxAnalytics.reconciliation(
        profile=profile,
        fy=fy_snap.fy or "FY2025-26",
        run_id=ctx.report_context.pfetl_run_id,
        provenance=ctx.app_context.analytical_provenance or ctx.app_context.provenance,
        catalog_snapshot=ctx.app_context.catalog_snapshot,
        cache=ctx.cache,
    )
    rows: list[dict[str, Any]] = [
        {
            "Sub-Head": r.tax_sub_head,
            "Ledger Tax Events": r.ledger_tax_event_amount,
            "Excl. Non-Taxable": r.excluded_non_taxable_amount,
            "Quant Gain/Loss": r.quant_realized_gain_loss,
            "Est. Inv Tax": r.estimated_investment_tax,
            "Est. Ledger Tax": r.estimated_ledger_tax,
            "Tax Credits": r.tax_credit_amount,
            "Source Diff (INR)": r.investment_source_difference_inr,
        }
        for r in recon.rows
    ]
    return RenderedTableBlock(
        block_id=block_id,
        source_id="tax_pos.reconciliation_table",
        title=ctx.title_override or f"Ledger vs Quant Tax Reconciliation ({recon.fy})",
        columns=(
            "Sub-Head",
            "Ledger Tax Events",
            "Excl. Non-Taxable",
            "Quant Gain/Loss",
            "Est. Inv Tax",
            "Est. Ledger Tax",
            "Tax Credits",
            "Source Diff (INR)",
        ),
        rows=tuple(rows[: ctx.max_rows]),
        total_rows=len(rows),
        truncated=len(rows) > ctx.max_rows,
        source_tables=("gold.tax_reconciliation",),
    )


TAX_POSITION_REVIEW_DEF = ReportDefinition(
    id="tax_position_review",
    name="Tax Position Review",
    description=(
        "Selected FY tax evidence deck: ledger income, investment gain/loss, tax credits, "
        "estimate completeness flags, head breakdown, and ledger-vs-quant reconciliation."
    ),
    default_period=PeriodRequest(preset=PeriodPreset.FYTD),
    default_filters=FilterSet(),
    builtin=True,
    blocks=(
        TitleBlock(
            block_id="tax_pos_title",
            title="Tax Position Review",
            subtitle="Indicative / Estimated FY Tax Evidence & Reconciliation Deck",
        ),
        ProvenanceBlock(block_id="tax_pos_prov", show_snapshots=True, show_disclaimer=True),
        NarrativeBlock(
            block_id="tax_pos_scope",
            heading="Estimate Completeness & Indicative Disclaimer",
            body=(
                "Indicative / estimated, not statutory tax liability. Incomplete estimate counts "
                "are surfaced explicitly and are never presented as final determined tax."
            ),
        ),
        KPIGroupBlock(
            block_id="tax_pos_kpis",
            source_id="tax_pos.kpis",
            title="FY Tax Position & Completeness (Indicative)",
        ),
        PageBreakBlock(block_id="pb_tax_pos_1"),
        ChartBlock(
            block_id="tax_pos_chart",
            source_id="tax_pos.head_breakdown_chart",
            title="Income Head & Sub-Head Tax Breakdown",
        ),
        PageBreakBlock(block_id="pb_tax_pos_2"),
        TableBlock(
            block_id="tax_pos_recon_tbl",
            source_id="tax_pos.reconciliation_table",
            title="Ledger-vs-Quant Tax Reconciliation & Exclusions",
            max_rows=30,
        ),
        PageBreakBlock(block_id="pb_tax_pos_3"),
        TableBlock(
            block_id="tax_pos_events_tbl",
            source_id="tax_inv.realized_events_table",
            title="Canonical Tax Events Appendix",
            max_rows=30,
        ),
    ),
)


def register_tax_position_sources(registry: ReportRegistry) -> None:
    """Register Tax Position Review sources and template."""
    from pfetl_analytics_studio.reports.registry import (  # noqa: PLC0415
        RegisteredBlockSource,
        ReportSourceKind,
    )

    registry.register_source(
        RegisteredBlockSource(
            source_id="tax_pos.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="FY Tax Position KPIs",
            source_tables=("gold.tax_financial_year_summary",),
            executor=_exec_tax_pos_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="tax_pos.head_breakdown_chart",
            kind=ReportSourceKind.CHART,
            title="Tax Income Head Breakdown",
            source_tables=("gold.tax_income_breakdown",),
            executor=_exec_head_breakdown_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="tax_pos.reconciliation_table",
            kind=ReportSourceKind.TABLE,
            title="Tax Reconciliation Table",
            source_tables=("gold.tax_reconciliation",),
            executor=_exec_tax_reconciliation_table,
        )
    )
    registry.register_definition(TAX_POSITION_REVIEW_DEF)
