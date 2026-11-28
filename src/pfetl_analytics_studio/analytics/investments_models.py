"""Typed DTOs for Investment Overview & Allocation Analytics.

Owns
----
- `AllocationDimension`, `AllocationRow`, `AllocationSnapshot`.
- `InvestmentHoldingRow`, `InvestmentPortfolioSnapshot`, and `InstrumentDetailSnapshot`.

Does not own
------------
- DuckDB SQL execution (`analytics/investments_queries.py`) or NiceGUI widgets.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.analytics.overview_models import OverviewKPI, OverviewRunHealth


class AllocationDimension(StrEnum):
    """Nine whitelisted Gold investment allocation dimensions."""

    CLASS = "class"
    INSTRUMENT_TYPE = "instrument_type"
    SUBTYPE = "subtype"
    SECTOR = "sector"
    INDUSTRY = "industry"
    GEO = "geo"
    COUNTRY = "country"
    CURRENCY = "currency"
    ISIN = "isin"


class InvestmentHoldingRow(BaseModel):
    """ISIN-level holding row from `gold.investment_portfolio_summary`."""

    model_config = ConfigDict(frozen=True)

    isin: str
    instrument_name: str
    currency_id: str = "INR_INR"
    instrument_class: str = "Unclassified"
    instrument_type: str = "Unclassified"
    instrument_subtype: str = "Unclassified"
    sector: str = "Unclassified"
    closing_date: date
    market_value: float = 0.0
    book_value: float = 0.0
    market_value_local: float | None = None
    book_value_local: float | None = None
    unrealized_pnl: float = 0.0
    asset_pnl: float | None = None
    forex_pnl: float | None = None
    xirr_local: float | None = None
    bm_xirr: float | None = None
    active_return: float | None = None
    isin_weight: float | None = None
    class_weight: float | None = None
    class_target_weight: float | None = None
    class_drift: float | None = None
    rebalance_required: bool = False
    harvestable_loss: float | None = None
    harvest_priority_score: float | None = None


class AllocationRow(BaseModel):
    """Normalized allocation row from any of the 9 Gold investment allocation marts."""

    model_config = ConfigDict(frozen=True)

    label: str
    parent_label: str | None = None
    closing_date: date
    invested_value: float = 0.0
    current_value: float = 0.0
    unrealized_pl: float = 0.0
    asset_pnl: float | None = None
    forex_pnl: float | None = None
    absolute_return: float | None = None
    xirr: float | None = None
    bm_xirr: float | None = None
    active_return: float | None = None
    max_drawdown: float | None = None
    weight: float | None = None
    stock_count: int | None = None


class AllocationSnapshot(BaseModel):
    """Immutable allocation slice for one whitelisted `AllocationDimension`."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    dimension: AllocationDimension
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    rows: tuple[AllocationRow, ...] = ()
    diagnostics: tuple[str, ...] = ()


class InvestmentPortfolioSnapshot(BaseModel):
    """Complete immutable Investment Overview snapshot consumed by `pages/investments.py`."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    kpis: tuple[OverviewKPI, ...] = ()
    holdings: tuple[InvestmentHoldingRow, ...] = ()
    class_allocation: tuple[AllocationRow, ...] = ()
    total_invested: float = 0.0
    total_current: float = 0.0
    unrealized_pl: float = 0.0
    run_health: OverviewRunHealth = OverviewRunHealth()
    diagnostics: tuple[str, ...] = ()


class InstrumentDetailSnapshot(BaseModel):
    """ISIN master metadata and monthly valuation history for the Instrument Detail Drawer."""

    model_config = ConfigDict(frozen=True)

    isin: str
    instrument_name: str
    master_metadata: dict[str, Any] = {}
    history: tuple[InvestmentHoldingRow, ...] = ()


class PerformanceHistoryPoint(BaseModel):
    """Single Closing_Date point from `gold.investment_by_portfolio`."""

    model_config = ConfigDict(frozen=True)

    closing_date: date
    invested_value: float = 0.0
    current_value: float = 0.0
    unrealized_pl: float = 0.0
    asset_pnl: float | None = None
    forex_pnl: float | None = None
    forex_contribution_pct: float | None = None
    absolute_return: float | None = None
    xirr: float | None = None
    after_tax_xirr: float | None = None
    bm_xirr: float | None = None
    active_return: float | None = None
    max_drawdown: float | None = None


class ConcentrationSummary(BaseModel):
    """Portfolio concentration metrics from `gold.investment_by_portfolio`."""

    model_config = ConfigDict(frozen=True)

    hhi_score: float | None = None
    effective_holdings_count: float | None = None
    top1_weight: float | None = None
    top5_weight: float | None = None
    top10_weight: float | None = None


class FxExposureRow(BaseModel):
    """Country or currency FX exposure row from Gold country/currency marts."""

    model_config = ConfigDict(frozen=True)

    bucket: str
    local_invested: float | None = None
    local_current: float | None = None
    base_invested: float = 0.0
    base_current: float = 0.0
    fx_rate: float | None = None
    blended_buy_rate: float | None = None
    currency_appreciation_pct: float | None = None
    local_xirr: float | None = None
    base_xirr: float | None = None
    fx_xirr_impact: float | None = None
    bm_xirr: float | None = None
    active_return: float | None = None


class InvestmentPerformanceSnapshot(BaseModel):
    """Complete immutable Performance, Benchmark, FX & Concentration snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    dimension: AllocationDimension = AllocationDimension.CLASS
    kpis: tuple[OverviewKPI, ...] = ()
    history: tuple[PerformanceHistoryPoint, ...] = ()
    concentration: ConcentrationSummary = ConcentrationSummary()
    segment_rows: tuple[AllocationRow, ...] = ()
    isin_perf_rows: tuple[AllocationRow, ...] = ()
    fx_country_rows: tuple[FxExposureRow, ...] = ()
    fx_currency_rows: tuple[FxExposureRow, ...] = ()
    diagnostics: tuple[str, ...] = ()


class LotFilter(BaseModel):
    """Query-side filter specification for `silver.fact_investment_lot_analytics`."""

    model_config = ConfigDict(frozen=True)

    closing_date: date | None = None
    isin: str | None = None
    instrument_class: str | None = None
    sector: str | None = None
    instrument_type: str | None = None
    instrument_subtype: str | None = None
    currency_id: str | None = None
    holding_type: str | None = None
    can_harvest_loss: bool | None = None
    harvest_recommendation: str | None = None
    days_to_ltcg_min: int | None = None
    days_to_ltcg_max: int | None = None
    gain_state: Literal["all", "gain", "loss"] = "all"
    search: str | None = None


class LotSummaryRow(BaseModel):
    """Curated lot summary projection from `silver.fact_investment_lot_analytics`."""

    model_config = ConfigDict(frozen=True)

    lot_id: str
    closing_date: date
    isin: str
    instrument_name: str
    instrument_class: str = "Unclassified"
    currency_id: str = "INR_INR"
    benchmark_name: str = "—"
    buy_date: date | None = None
    quantity: float = 0.0
    buy_value: float = 0.0
    close_value: float = 0.0
    unrealized_pl: float = 0.0
    absolute_return: float | None = None
    xirr: float | None = None
    bm_xirr: float | None = None
    lot_alpha: float | None = None
    holding_type: str = "STCG"
    age_days: int | None = None
    days_to_ltcg: int | None = None
    tax_if_sold: float = 0.0
    after_tax_pl: float | None = None
    can_harvest_loss: bool = False
    harvest_recommendation: str = "HOLD"
    is_lagging_benchmark: bool = False


class LotKpiSummary(BaseModel):
    """Aggregate lot-level counts and valuations for the active LotFilter."""

    model_config = ConfigDict(frozen=True)

    total_lots: int = 0
    total_buy_value: float = 0.0
    total_close_value: float = 0.0
    total_unrealized_pl: float = 0.0
    ltcg_lots: int = 0
    stcg_lots: int = 0
    ltcg_value: float = 0.0
    stcg_value: float = 0.0
    harvest_loss_lots: int = 0
    harvest_loss_value: float = 0.0
    total_tax_if_sold: float = 0.0
    lagging_lots: int = 0
    maturing_30d_lots: int = 0
    maturing_90d_lots: int = 0


class LotPageSnapshot(BaseModel):
    """Paginated Lot Explorer snapshot returned by `InvestmentAnalytics.lot_rows`."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    closing_date: date | None = None
    nearest_dates: tuple[date, ...] = ()
    total_count: int = 0
    kpis: tuple[OverviewKPI, ...] = ()
    summary: LotKpiSummary = LotKpiSummary()
    rows: tuple[LotSummaryRow, ...] = ()
    chart_rows: tuple[LotSummaryRow, ...] = ()
    diagnostics: tuple[str, ...] = ()


class LotDetailSection(BaseModel):
    """Grouped section of canonical columns for the Lot Detail Drawer."""

    model_config = ConfigDict(frozen=True)

    title: str
    fields: tuple[tuple[str, str, Any], ...] = ()


class LotDetailSnapshot(BaseModel):
    """Full 88-column lot detail grouped into 9 audit sections + raw columns."""

    model_config = ConfigDict(frozen=True)

    lot_id: str
    closing_date: date
    instrument_name: str
    isin: str
    sections: tuple[LotDetailSection, ...] = ()
    raw_columns: dict[str, Any] = {}


class RealizedEventFilter(BaseModel):
    """Filter specification for `silver.fact_investment_realized_events`."""

    model_config = ConfigDict(frozen=True)

    fy: str | None = None
    holding_type: str | None = None
    isin: str | None = None
    gain_state: Literal["all", "gain", "loss"] = "all"
    search: str | None = None


class RealizedEventRow(BaseModel):
    """Canonical FIFO realized event from `silver.fact_investment_realized_events`."""

    model_config = ConfigDict(frozen=True)

    realized_event_id: str
    sale_id: str
    lot_id: str
    purchase_id: str
    isin: str
    instrument_name: str
    currency_id: str = "INR_INR"
    acquisition_date: date | None = None
    disposal_date: date | None = None
    fy: str = ""
    quantity_disposed: float = 0.0
    acquisition_price: float = 0.0
    disposed_cost_basis: float = 0.0
    sale_price: float = 0.0
    sale_proceeds: float = 0.0
    realized_gain_loss: float = 0.0
    holding_type: str = "STCG"
    tax_type: str = ""
    tax_subtype: str = ""
    asset_pnl: float | None = None
    forex_pnl: float | None = None
    applied_rate: float | None = None
    estimated_tax: float = 0.0


class ReconciliationEventRow(BaseModel):
    """Single reconciliation event from `silver.fact_investment_reconciliation_events`."""

    model_config = ConfigDict(frozen=True)

    reconciliation_group_id: str
    reconciliation_event_id: str
    run_id: str = ""
    isin: str
    instrument_name: str
    reconciliation_date: date | None = None
    lot_id: str = ""
    purchase_id: str = ""
    adjustment_type: str = ""
    reason: str = ""
    broker_quantity: float = 0.0
    reconstructed_quantity: float = 0.0
    quantity_adjustment: float = 0.0
    broker_cost_basis: float = 0.0
    reconstructed_cost_basis: float = 0.0
    cost_basis_adjustment: float = 0.0
    original_unit_cost: float | None = None
    adjusted_unit_cost: float | None = None


class ReconciliationGroupRow(BaseModel):
    """Grouped reconciliation summary keyed by `Reconciliation_Group_ID`."""

    model_config = ConfigDict(frozen=True)

    reconciliation_group_id: str
    isin: str
    instrument_name: str
    adjustment_type: str
    reason: str
    event_count: int = 0
    broker_quantity: float = 0.0
    reconstructed_quantity: float = 0.0
    quantity_adjustment: float = 0.0
    broker_cost_basis: float = 0.0
    reconstructed_cost_basis: float = 0.0
    cost_basis_adjustment: float = 0.0


class RealizedReconciliationSnapshot(BaseModel):
    """Combined Realized & Reconciliation workspace snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    kpis: tuple[OverviewKPI, ...] = ()
    realized_rows: tuple[RealizedEventRow, ...] = ()
    reconciliation_rows: tuple[ReconciliationEventRow, ...] = ()
    reconciliation_groups: tuple[ReconciliationGroupRow, ...] = ()
    available_fys: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


class TradeLineageTrace(BaseModel):
    """Exact ID-linked trace: Realized Event -> Sale -> Lot -> Purchase."""

    model_config = ConfigDict(frozen=True)

    realized_event: RealizedEventRow
    sale_record: dict[str, Any] | None = None
    lot_record: dict[str, Any] | None = None
    purchase_record: dict[str, Any] | None = None
    missing_links: tuple[str, ...] = ()


class MarketObservationPoint(BaseModel):
    """Single instrument market data observation with provenance."""

    model_config = ConfigDict(frozen=True)

    obs_date: date
    isin: str
    quantity: float = 0.0
    closing_price: float = 0.0
    buy_price: float = 0.0
    closing_value: float = 0.0
    buy_value: float = 0.0
    fx_rate: float | None = None
    data_provider: str = ""
    extraction_time: str = ""
    is_closure_gap: bool = False
    is_imputed: bool = False


class BenchmarkObservationPoint(BaseModel):
    """Single benchmark close observation with provenance."""

    model_config = ConfigDict(frozen=True)

    obs_date: date
    benchmark_id: str
    benchmark_name: str
    yf_ticker: str = ""
    close: float = 0.0
    data_provider: str = ""
    extraction_time: str = ""
    is_closure_gap: bool = False
    is_imputed: bool = False


class FxObservationPoint(BaseModel):
    """Single FX rate observation with provenance."""

    model_config = ConfigDict(frozen=True)

    obs_date: date
    currency_id: str
    currency_code: str = ""
    target_currency_code: str = ""
    yf_ticker: str = ""
    fx_rate: float = 1.0
    data_provider: str = ""
    extraction_time: str = ""
    is_closure_gap: bool = False
    is_imputed: bool = False


class MarketEvidenceSnapshot(BaseModel):
    """Market, Benchmark & FX Evidence snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    selected_isin: str = ""
    selected_benchmark_id: str = ""
    selected_currency_id: str = ""
    available_isins: tuple[tuple[str, str], ...] = ()
    available_benchmarks: tuple[tuple[str, str], ...] = ()
    available_currencies: tuple[str, ...] = ()
    kpis: tuple[OverviewKPI, ...] = ()
    market_points: tuple[MarketObservationPoint, ...] = ()
    benchmark_points: tuple[BenchmarkObservationPoint, ...] = ()
    fx_points: tuple[FxObservationPoint, ...] = ()
    diagnostics: tuple[str, ...] = ()
