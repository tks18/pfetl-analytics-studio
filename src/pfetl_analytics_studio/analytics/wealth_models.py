"""Typed DTOs for Wealth: Net Worth, Liquidity, Drawdown & Attribution Analytics.

Owns
----
- `WealthMonthlyPoint`, `NetWorthAttributionSnapshot`, `AssetGroupMixRow`.
- `WealthAssetRow`, `WealthSummarySnapshot`, and `AssetBreakdownSnapshot`.

Does not own
------------
- DuckDB SQL execution (`analytics/wealth_queries.py`) or NiceGUI widgets.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.analytics.overview_models import OverviewKPI, OverviewRunHealth


class WealthMonthlyPoint(BaseModel):
    """Monthly balance sheet, liquidity, P&L/FX, and net-worth attribution point."""

    model_config = ConfigDict(frozen=True)

    month_end: date
    book_net_worth: float | None = None
    market_net_worth: float | None = None
    total_assets: float | None = None
    total_assets_market: float | None = None
    total_liabilities: float | None = None
    liquid_assets: float | None = None
    liquid_assets_market: float | None = None
    opening_market_assets: float | None = None
    closing_market_assets: float | None = None
    foreign_exposure: float | None = None
    foreign_exposure_pct: float | None = None
    closing_unrealized_pl: float | None = None
    closing_asset_pnl: float | None = None
    closing_forex_pnl: float | None = None
    closing_investment_book: float | None = None
    closing_investment_market: float | None = None
    closing_investment_xirr: float | None = None
    net_savings_flow: float = 0.0
    market_return_flow: float = 0.0
    attribution_residual: float = 0.0
    asset_market_delta: float = 0.0
    liquidity_ratio_months: float | None = None
    debt_to_asset_pct: float | None = None
    yoy_nw_growth_pct: float | None = None
    yoy_nw_growth_real_pct: float | None = None
    emergency_coverage: float | None = None


class NetWorthAttributionSnapshot(BaseModel):
    """Canonical Gold attribution reconciling Market Asset Delta over the active period."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    period_label: str = ""
    opening_market_assets: float = 0.0
    closing_market_assets: float = 0.0
    asset_market_delta: float = 0.0
    net_savings_flow: float = 0.0
    market_return_flow: float = 0.0
    attribution_residual: float = 0.0
    reconciles_within_tolerance: bool = True
    monthly_attribution: tuple[WealthMonthlyPoint, ...] = ()
    diagnostics: tuple[str, ...] = ()


class AssetGroupMixRow(BaseModel):
    """Aggregated asset group composition across book, market, and liquid balances."""

    model_config = ConfigDict(frozen=True)

    asset_group: str
    book_value: float = 0.0
    market_value: float = 0.0
    liquid_value: float = 0.0
    share_pct: float | None = None
    asset_count: int = 0


class CurrencyExposureRow(BaseModel):
    """Aggregated wealth exposure and P&L/FX breakdown by currency."""

    model_config = ConfigDict(frozen=True)

    currency_id: str
    book_value: float = 0.0
    market_value: float = 0.0
    forex_pnl: float = 0.0
    asset_pnl: float = 0.0
    share_pct: float | None = None
    asset_count: int = 0
    is_foreign: bool = False


class WealthAssetRow(BaseModel):
    """Asset-subcategory balance sheet, drawdown, P&L/FX, and opening-balance context."""

    model_config = ConfigDict(frozen=True)

    asset_id: str
    asset_name: str
    asset_group: str = "Unclassified"
    currency_id: str = "INR_INR"
    ledger_role: str = ""
    cashflow_activity_type: str = ""
    is_liquid: bool = False
    is_cash_pool: bool = False
    month_end: date
    opening_balance: float = 0.0
    closing_balance: float = 0.0
    closing_balance_market: float | None = None
    all_time_high: float | None = None
    drawdown_from_peak: float | None = None
    closing_asset_pnl: float | None = None
    closing_forex_pnl: float | None = None
    mom_growth_pct: float | None = None
    yoy_growth_pct: float | None = None
    organic_yield_pct: float | None = None
    cumulative_net_savings: float | None = None
    months_of_runway: float | None = None
    net_cashflow_month: float = 0.0
    organic_growth_value: float = 0.0
    opening_anchor_amount: float | None = None
    opening_anchor_date: date | None = None


class AssetBreakdownSnapshot(BaseModel):
    """Filtered asset-subcategory breakdown snapshot for the Wealth table & drill."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    group_mix: tuple[AssetGroupMixRow, ...] = ()
    currency_mix: tuple[CurrencyExposureRow, ...] = ()
    assets: tuple[WealthAssetRow, ...] = ()
    liquid_total_market: float = 0.0
    illiquid_total_market: float = 0.0
    diagnostics: tuple[str, ...] = ()


class WealthSummarySnapshot(BaseModel):
    """Complete immutable Wealth workspace snapshot consumed by `pages/wealth.py`."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    period_label: str = ""
    chart_window_label: str = ""
    kpis: tuple[OverviewKPI, ...] = ()
    monthly_series: tuple[WealthMonthlyPoint, ...] = ()
    attribution: NetWorthAttributionSnapshot | None = None
    asset_breakdown: AssetBreakdownSnapshot | None = None
    run_health: OverviewRunHealth = OverviewRunHealth()
    diagnostics: tuple[str, ...] = ()
