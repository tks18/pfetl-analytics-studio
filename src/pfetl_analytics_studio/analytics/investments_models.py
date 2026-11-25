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
