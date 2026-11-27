"""Report source and template registry with stable-ID uniqueness checks.

Owns
----
- `ReportRegistry` registering analytical block sources and built-in `ReportDefinition` templates.
- Built-in demonstration definition (`builtin.executive_snapshot`) backed by `OverviewService`.

Does not own
------------
- NiceGUI widgets, PFETL SQLite/DuckDB connections, or PDF/XLSX serialization.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.analytics import OverviewService
from pfetl_analytics_studio.analytics.overview_models import (
    CashflowPoint,
    NetWorthPoint,
    OverviewSnapshot,
)
from pfetl_analytics_studio.context import AppContext
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.reports.models import (
    ChartBlock,
    DividerBlock,
    KPIGroupBlock,
    NarrativeBlock,
    PageBreakBlock,
    ProvenanceBlock,
    RenderedChartBlock,
    RenderedKPIGroupBlock,
    RenderedTableBlock,
    ReportContext,
    ReportDefinition,
    ReportKpiItem,
    SubtitleBlock,
    TableBlock,
    TitleBlock,
)
from pfetl_analytics_studio.semantic import FilterSet, PeriodRequest
from pfetl_analytics_studio.ui.formatting import format_metric_value
from pfetl_analytics_studio.ui.theme import StudioColor


class DuplicateReportRegistrationError(ValueError):
    """Raised when attempting to register a duplicate report source or template ID."""


class ReportSourceKind(StrEnum):
    """Supported analytical block source categories."""

    KPI_GROUP = "kpi_group"
    CHART = "chart"
    TABLE = "table"


@dataclass(frozen=True)
class ReportSourceContext:
    """Execution parameters passed to a registered analytical block source."""

    app_context: AppContext
    report_context: ReportContext
    cache: QueryCache
    title_override: str = ""
    max_rows: int = 200


BlockSourceExecutor = Callable[
    [ReportSourceContext, str],
    RenderedKPIGroupBlock | RenderedChartBlock | RenderedTableBlock,
]


class RegisteredBlockSource(BaseModel):
    """Metadata and callable binding for a single registered report block source."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    source_id: str
    kind: ReportSourceKind
    title: str
    description: str = ""
    source_tables: tuple[str, ...] = ()
    executor: BlockSourceExecutor


class ReportRegistry:
    """Thread-safe registry of analytical block sources and built-in report definitions."""

    def __init__(self) -> None:
        self._sources: dict[str, RegisteredBlockSource] = {}
        self._definitions: dict[str, ReportDefinition] = {}

    def register_source(self, source: RegisteredBlockSource) -> None:
        """Register an analytical block source, rejecting duplicate `source_id`."""
        if source.source_id in self._sources:
            raise DuplicateReportRegistrationError(
                f"Report source '{source.source_id}' is already registered."
            )
        self._sources[source.source_id] = source

    def get_source(self, source_id: str) -> RegisteredBlockSource | None:
        """Return registered block source by `source_id`, or `None` if unknown."""
        return self._sources.get(source_id)

    def list_sources(
        self, kind: ReportSourceKind | None = None
    ) -> tuple[RegisteredBlockSource, ...]:
        """List all registered block sources, optionally filtered by kind."""
        items = list(self._sources.values())
        if kind is not None:
            items = [s for s in items if s.kind == kind]
        return tuple(sorted(items, key=lambda s: s.source_id))

    def register_definition(self, definition: ReportDefinition) -> None:
        """Register a built-in/fixture `ReportDefinition`, rejecting duplicate `id`."""
        if definition.id in self._definitions:
            raise DuplicateReportRegistrationError(
                f"Report definition '{definition.id}' is already registered."
            )
        self._definitions[definition.id] = definition

    def get_definition(self, definition_id: str) -> ReportDefinition | None:
        """Return registered `ReportDefinition` by `id`, or `None` if unknown."""
        return self._definitions.get(definition_id)

    def list_definitions(self, *, builtin_only: bool = False) -> tuple[ReportDefinition, ...]:
        """List registered report definitions sorted by name."""
        items = list(self._definitions.values())
        if builtin_only:
            items = [d for d in items if d.builtin]
        return tuple(sorted(items, key=lambda d: d.name))


def _load_overview_snapshot(ctx: ReportSourceContext) -> OverviewSnapshot:
    """Load `OverviewSnapshot` pinned to the active `ReportContext` run & period."""
    profile = ctx.app_context.profile
    if profile is None:
        raise ValueError("No active PFETL profile is selected for report execution.")
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


def _execute_overview_kpis(ctx: ReportSourceContext, block_id: str) -> RenderedKPIGroupBlock:
    """Build exporter-neutral KPI items from `OverviewService.get_snapshot`."""
    snapshot = _load_overview_snapshot(ctx)
    items: list[ReportKpiItem] = []
    for kpi in snapshot.primary_kpis:
        formatted = format_metric_value(
            kpi.value,
            kpi.format,
            currency_id=snapshot.currency_id,
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
        source_id="overview.kpis",
        title=ctx.title_override or f"Executive KPI Snapshot ({snapshot.period_label})",
        items=tuple(items),
        source_tables=(
            "gold.core_monthly_fact",
            "gold.cashflow_efficiency_analytics",
            "gold.investment_by_portfolio",
            "gold.wealth_fire_analytics",
        ),
    )


def _execute_overview_net_worth_chart(
    ctx: ReportSourceContext, block_id: str
) -> RenderedChartBlock:
    """Build an exporter-neutral Plotly figure dict for Net Worth & Savings Flow."""
    snapshot = _load_overview_snapshot(ctx)
    dates = [pt.month_end.isoformat() for pt in snapshot.net_worth_series]
    nw_values = [pt.net_worth_market for pt in snapshot.net_worth_series]
    savings_values = [pt.net_savings_flow or 0.0 for pt in snapshot.net_worth_series]

    figure_dict: dict[str, Any] = {
        "data": [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Market Net Worth",
                "x": dates,
                "y": nw_values,
                "line": {"color": StudioColor.ACCENT, "width": 2.5},
            },
            {
                "type": "bar",
                "name": "Net Savings Flow",
                "x": dates,
                "y": savings_values,
                "yaxis": "y2",
                "marker": {"color": StudioColor.SUCCESS, "opacity": 0.45},
            },
        ],
        "layout": {
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(13,17,23,0.65)",
            "font": {"color": StudioColor.TEXT},
            "margin": {"l": 56, "r": 56, "t": 32, "b": 40},
            "legend": {"orientation": "h", "y": 1.12, "x": 0},
            "xaxis": {"gridcolor": StudioColor.BORDER, "title": "Month End"},
            "yaxis": {"gridcolor": StudioColor.BORDER, "title": "Net Worth"},
            "yaxis2": {
                "overlaying": "y",
                "side": "right",
                "showgrid": False,
                "title": "Net Savings Flow",
            },
        },
    }
    return RenderedChartBlock(
        block_id=block_id,
        source_id="overview.net_worth_trend",
        title=ctx.title_override
        or f"Market Net Worth & Savings Flow ({snapshot.chart_window_label})",
        figure_dict=figure_dict,
        source_tables=("gold.core_monthly_fact",),
        caption=f"Window: {snapshot.chart_window_label} · Source: gold.core_monthly_fact",
    )


def _execute_overview_monthly_table(ctx: ReportSourceContext, block_id: str) -> RenderedTableBlock:
    """Build an exporter-neutral monthly summary table from `OverviewSnapshot`."""
    snapshot = _load_overview_snapshot(ctx)
    nw_by_date: dict[str, NetWorthPoint] = {
        pt.month_end.isoformat(): pt for pt in snapshot.net_worth_series
    }
    cf_by_date: dict[str, CashflowPoint] = {
        pt.month_end.isoformat(): pt for pt in snapshot.cashflow_series
    }
    all_dates = sorted(set(nw_by_date.keys()) | set(cf_by_date.keys()), reverse=True)
    total_rows = len(all_dates)
    sliced_dates = all_dates[: ctx.max_rows]

    rows: list[dict[str, Any]] = []
    for dt in sliced_dates:
        nw = nw_by_date.get(dt)
        cf = cf_by_date.get(dt)
        rows.append(
            {
                "Month End": dt,
                "Market Net Worth": nw.net_worth_market if nw is not None else None,
                "Net Savings Flow": nw.net_savings_flow if nw is not None else None,
                "Market Return Flow": nw.market_return_flow if nw is not None else None,
                "Income": cf.income if cf is not None else None,
                "Expense": cf.expense if cf is not None else None,
                "Net Cashflow": cf.net_cashflow if cf is not None else None,
            }
        )

    columns = (
        "Month End",
        "Market Net Worth",
        "Net Savings Flow",
        "Market Return Flow",
        "Income",
        "Expense",
        "Net Cashflow",
    )
    return RenderedTableBlock(
        block_id=block_id,
        source_id="overview.monthly_summary_table",
        title=ctx.title_override or "Monthly Net Worth & Cashflow Summary",
        columns=columns,
        rows=tuple(rows),
        total_rows=total_rows,
        truncated=total_rows > len(rows),
        source_tables=("gold.core_monthly_fact",),
    )


def build_default_report_registry() -> ReportRegistry:
    """Create the default `ReportRegistry` with Phase 24 demonstration sources & template."""
    registry = ReportRegistry()
    registry.register_source(
        RegisteredBlockSource(
            source_id="overview.kpis",
            kind=ReportSourceKind.KPI_GROUP,
            title="Executive KPI Snapshot",
            description="Headline Net Worth, Cashflow, Portfolio, and FIRE KPIs from OverviewService.",
            source_tables=(
                "gold.core_monthly_fact",
                "gold.cashflow_efficiency_analytics",
                "gold.investment_by_portfolio",
                "gold.wealth_fire_analytics",
            ),
            executor=_execute_overview_kpis,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="overview.net_worth_trend",
            kind=ReportSourceKind.CHART,
            title="Market Net Worth & Savings Flow",
            description="Dual-axis monthly Market Net Worth and Net Savings Flow trajectory.",
            source_tables=("gold.core_monthly_fact",),
            executor=_execute_overview_net_worth_chart,
        )
    )
    registry.register_source(
        RegisteredBlockSource(
            source_id="overview.monthly_summary_table",
            kind=ReportSourceKind.TABLE,
            title="Monthly Net Worth & Cashflow Table",
            description="Chronological monthly balances, savings flow, market return, and cashflow.",
            source_tables=("gold.core_monthly_fact",),
            executor=_execute_overview_monthly_table,
        )
    )

    registry.register_definition(
        ReportDefinition(
            id="builtin.executive_snapshot",
            name="Executive Financial Snapshot (Runtime Demo)",
            description=(
                "Demonstration report verifying deterministic block ordering, run provenance, "
                "KPI cards, Plotly chart serialization, and monthly tabular output."
            ),
            default_period=PeriodRequest(),
            default_filters=FilterSet(),
            builtin=True,
            blocks=(
                TitleBlock(
                    block_id="cover_title",
                    title="Executive Financial Snapshot",
                    subtitle="Deterministic PFETL Analytics Studio Report Runtime",
                ),
                ProvenanceBlock(
                    block_id="run_provenance",
                    show_snapshots=True,
                    show_disclaimer=True,
                ),
                NarrativeBlock(
                    block_id="exec_note",
                    heading="Methodology & Scope",
                    body=(
                        "All values below are queried read-only from canonical PFETL Gold marts "
                        "and pinned to the exact PFETL run and Settings/Rules snapshots shown above."
                    ),
                ),
                SubtitleBlock(block_id="kpi_heading", text="Key Financial Indicators"),
                KPIGroupBlock(
                    block_id="exec_kpis",
                    source_id="overview.kpis",
                    title="Executive KPIs",
                ),
                DividerBlock(block_id="mid_divider", label="Trajectory & Monthly Ledger"),
                ChartBlock(
                    block_id="nw_chart",
                    source_id="overview.net_worth_trend",
                    title="Net Worth & Net Savings Trajectory",
                ),
                PageBreakBlock(block_id="page_break_1"),
                TableBlock(
                    block_id="monthly_table",
                    source_id="overview.monthly_summary_table",
                    title="Monthly Net Worth & Cashflow Breakdown",
                    max_rows=24,
                ),
            ),
        )
    )
    return registry


DEFAULT_REPORT_REGISTRY = build_default_report_registry()
