"""Analytics package for PFETL Analytics Studio.

Ownership: Typed analytical read services and safe query projections.
Forbidden: Does not import NiceGUI widgets or mutate warehouse/state data.
"""

from __future__ import annotations

from pfetl_analytics_studio.analytics.household import HouseholdAnalytics
from pfetl_analytics_studio.analytics.household_models import (
    HouseholdIncomeSnapshot,
    IncomeAccountMixPoint,
    IncomeCategoryRow,
    IncomeGroupRow,
    IncomeMonthlyPoint,
    IncomeTaxabilitySummary,
    IncomeTransactionPage,
    IncomeTransactionRow,
)
from pfetl_analytics_studio.analytics.overview import (
    AssetClassPoint,
    CashflowPoint,
    CategoryBreakupPoint,
    FirePoint,
    NetWorthPoint,
    OverviewBreakupItem,
    OverviewKPI,
    OverviewRunHealth,
    OverviewService,
    OverviewSnapshot,
    PortfolioPoint,
)

__all__ = [
    "AssetClassPoint",
    "CashflowPoint",
    "CategoryBreakupPoint",
    "FirePoint",
    "HouseholdAnalytics",
    "HouseholdIncomeSnapshot",
    "IncomeAccountMixPoint",
    "IncomeCategoryRow",
    "IncomeGroupRow",
    "IncomeMonthlyPoint",
    "IncomeTaxabilitySummary",
    "IncomeTransactionPage",
    "IncomeTransactionRow",
    "NetWorthPoint",
    "OverviewBreakupItem",
    "OverviewKPI",
    "OverviewRunHealth",
    "OverviewService",
    "OverviewSnapshot",
    "PortfolioPoint",
]
