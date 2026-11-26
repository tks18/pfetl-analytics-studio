"""Typed DTOs for Phase 17 Tax Analytics.

All outputs carry `indicative=True` to preserve the non-statutory boundary.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.analytics.investments_models import LotSummaryRow
from pfetl_analytics_studio.analytics.overview_models import OverviewKPI


class TaxFYParameters(BaseModel):
    """Reference tax & macro parameters from `silver.ref_financial_year_parameters`."""

    model_config = ConfigDict(frozen=True)

    fy: str
    fy_start_date: date | None = None
    fy_end_date: date | None = None
    debt_mf_cutoff_date: date | None = None
    equity_listed_ltcg: float | None = None
    equity_listed_stcg: float | None = None
    equity_ltcg_exemption: int | None = None
    gold_ltcg: float | None = None
    gold_stcg: float | None = None
    debt_mf_post_cutoff_ltcg: float | None = None
    debt_mf_post_cutoff_stcg: float | None = None
    estimated_ordinary_income_tax_rate: float | None = None
    remarks: str = ""


class TaxFYSummarySnapshot(BaseModel):
    """FY summary from `gold.tax_financial_year_summary` with completeness metadata."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    fy: str = ""
    available_fys: tuple[str, ...] = ()
    indicative: bool = True
    total_ledger_income: float = 0.0
    total_investment_gain_loss: float = 0.0
    total_tax_credits: float = 0.0
    total_estimated_investment_tax: float = 0.0
    investment_tax_estimate_count: int = 0
    investment_tax_estimate_missing_count: int = 0
    investment_tax_estimate_complete: bool = False
    total_estimated_ledger_tax: float = 0.0
    ledger_tax_estimate_count: int = 0
    ledger_tax_estimate_missing_count: int = 0
    ledger_tax_estimate_complete: bool = False
    total_event_count: int = 0
    ready_count: int = 0
    check_required_count: int = 0
    indicative_unrealized_tax_liability: float = 0.0
    indicative_harvestable_tax_savings: float = 0.0
    indicative_net_tax_exposure: float = 0.0
    fy_params: TaxFYParameters | None = None
    kpis: tuple[OverviewKPI, ...] = ()
    all_fy_rows: tuple[dict[str, Any], ...] = ()
    diagnostics: tuple[str, ...] = ()


class TaxIncomeBreakdownRow(BaseModel):
    """Row from `gold.tax_income_breakdown`."""

    model_config = ConfigDict(frozen=True)

    fy: str
    income_head: str
    tax_sub_head: str
    source_type: str
    event_amount_inr: float = 0.0
    estimated_tax_inr: float = 0.0
    estimated_tax_event_count: int = 0
    estimated_tax_missing_count: int = 0
    estimated_tax_complete: bool = False
    event_count: int = 0
    ready_count: int = 0
    check_required_count: int = 0


class TaxIncomeBreakdownSnapshot(BaseModel):
    """Filtered `gold.tax_income_breakdown` snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    fy: str = ""
    indicative: bool = True
    rows: tuple[TaxIncomeBreakdownRow, ...] = ()
    available_source_types: tuple[str, ...] = ()
    available_income_heads: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


class TaxReconciliationRow(BaseModel):
    """Row from `gold.tax_reconciliation`."""

    model_config = ConfigDict(frozen=True)

    fy: str
    tax_sub_head: str
    ledger_tax_event_amount: float = 0.0
    excluded_non_taxable_amount: float = 0.0
    excluded_non_taxable_event_count: int = 0
    excluded_investment_ledger_amount: float = 0.0
    excluded_investment_ledger_event_count: int = 0
    quant_realized_gain_loss: float = 0.0
    estimated_investment_tax: float = 0.0
    estimated_investment_tax_complete: bool = False
    estimated_ledger_tax: float = 0.0
    estimated_ledger_tax_complete: bool = False
    tax_credit_amount: float = 0.0
    tax_event_amount: float = 0.0
    investment_source_difference_inr: float = 0.0
    ledger_event_count: int = 0
    quant_event_count: int = 0
    tax_credit_event_count: int = 0
    check_required_count: int = 0


class TaxReconciliationSnapshot(BaseModel):
    """Filtered `gold.tax_reconciliation` snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    fy: str = ""
    indicative: bool = True
    rows: tuple[TaxReconciliationRow, ...] = ()
    diagnostics: tuple[str, ...] = ()


class TaxEventFilter(BaseModel):
    """Filter specification for `silver.fact_tax_events`."""

    model_config = ConfigDict(frozen=True)

    source_type: str | None = None
    income_head: str | None = None
    tax_sub_head: str | None = None
    tax_status: str | None = None
    search: str | None = None


class TaxEventRow(BaseModel):
    """Canonical row from `silver.fact_tax_events`."""

    model_config = ConfigDict(frozen=True)

    tax_event_id: str
    fy: str
    event_date: date | None = None
    source_type: str = ""
    source_id: str = ""
    income_head: str = ""
    tax_sub_head: str = ""
    amount_inr: float = 0.0
    applied_rate: float | None = None
    estimated_tax: float | None = None
    currency_id: str = "INR_INR"
    tax_status: str = ""
    tax_status_reason: str = ""
    isin: str = ""
    lot_id: str = ""


class TaxEventsSnapshot(BaseModel):
    """Filtered `silver.fact_tax_events` snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    fy: str = ""
    indicative: bool = True
    rows: tuple[TaxEventRow, ...] = ()
    diagnostics: tuple[str, ...] = ()


class TaxHarvestingSnapshot(BaseModel):
    """Indicative unrealized tax & harvesting exposure from lot analytics."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "no_run", "locked", "empty"]
    currency_id: str = "INR_INR"
    as_of_date: date | None = None
    indicative: bool = True
    harvest_lots: tuple[LotSummaryRow, ...] = ()
    maturing_lots: tuple[LotSummaryRow, ...] = ()
    total_harvestable_loss: float = 0.0
    total_tax_if_sold: float = 0.0
    diagnostics: tuple[str, ...] = ()
