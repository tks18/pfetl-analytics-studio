"""Upstream PFETL runtime catalog, compatibility checking, provenance, and profile drift engine.

Owns
----
- Complete 82-surface classification baseline (`SURFACE_ATLAS`).
- Runtime schema discovery across DuckDB (`information_schema`, `meta.*`) and Control Plane SQLite.
- Structural compatibility diagnostics (`CompatibilityReport`).
- Historical and latest successful run provenance resolution with immutable snapshot parsing.
- Selected TOML vs run-snapshot drift verification (`evaluate_profile_drift`).

Does not own
------------
- Database connection lifecycle internals (owned by `duckdb_reader.py` and `control_plane_reader.py`).
- Execution log decompression and JSONL parsing (owned by `log_reader.py`).
- Presentation widgets or financial domain computations.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.data.control_plane_reader import ControlPlaneReader
from pfetl_analytics_studio.data.duckdb_reader import DuckDBReader, validate_identifier
from pfetl_analytics_studio.data.errors import (
    ControlPlaneState,
    PFETLDataAccessError,
    WarehouseState,
)
from pfetl_analytics_studio.profile import PFETLProfile, ProfileDriftStatus


def _empty_str_list() -> list[str]:
    """Create a typed empty list of strings."""
    return []


class SurfaceLayer(StrEnum):
    """Architectural storage layer for an upstream PFETL table."""

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    META = "meta"
    CONTROL_PLANE = "control_plane"


class SurfaceUsageCategory(StrEnum):
    """Explicit Studio consumer classification mandated by the README §22 coverage gate."""

    ANALYTICAL = "ANALYTICAL"
    DRILL = "DRILL"
    OPERATIONS = "OPERATIONS"
    EVIDENCE = "EVIDENCE"
    NOT_USED_WITH_REASON = "NOT_USED_WITH_REASON"


class CompatibilityStatus(StrEnum):
    """Overall Studio compatibility evaluation against the upstream PFETL surface."""

    COMPATIBLE = "COMPATIBLE"
    DEGRADED = "DEGRADED"
    INCOMPATIBLE = "INCOMPATIBLE"


class SurfaceSpec(BaseModel):
    """Authoritative metadata classification specification for one upstream PFETL surface."""

    model_config = ConfigDict(frozen=True)

    physical_name: str
    layer: SurfaceLayer
    domain: str
    grain: str
    producer: str
    usage: SurfaceUsageCategory
    purpose: str
    required_columns: tuple[str, ...] = ()
    primary_filters: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class ColumnInfo:
    """Physical database column metadata discovered at runtime."""

    name: str
    data_type: str
    nullable: bool = True


@dataclass(frozen=True)
class CatalogEntry:
    """Discovered upstream table entry merged with runtime contracts and Studio classification."""

    schema_name: str
    table_name: str
    physical_name: str
    layer: SurfaceLayer
    domain: str
    grain: str
    producer: str
    columns: list[ColumnInfo]
    row_count: int | None
    classification: SurfaceUsageCategory | None
    spec: SurfaceSpec | None


@dataclass(frozen=True)
class CompatibilityReport:
    """Structured compatibility report distinguishing missing, unexpected, and degraded surfaces."""

    status: CompatibilityStatus
    is_compatible: bool
    warehouse_state: WarehouseState
    control_plane_state: ControlPlaneState
    total_expected: int
    total_discovered: int
    layer_counts: dict[SurfaceLayer, int]
    supported_surfaces: list[str]
    missing_required_surfaces: list[str]
    unexpected_new_surfaces: list[str]
    missing_required_columns: dict[str, list[str]]
    extra_columns: dict[str, list[str]]
    diagnostics: list[str]


@dataclass(frozen=True)
class RunProvenance:
    """Immutable run execution provenance and parsed configuration/rules snapshots."""

    run_id: str
    started_at: datetime | None
    finished_at: datetime | None
    status: str | None
    commit_state: str | None
    application_version: str | None
    schema_version: str | None
    settings_snapshot_id: str | None
    rules_snapshot_id: str | None
    settings_payload: dict[str, Any] | None
    rules_payload: dict[str, Any] | None
    has_settings_snapshot: bool
    has_rules_snapshot: bool
    has_execution_log: bool
    is_complete: bool
    diagnostics: list[str] = field(default_factory=_empty_str_list)


@dataclass(frozen=True)
class DriftCheckResult:
    """Comparison outcome between current profile TOMLs and historical run snapshots."""

    status: ProfileDriftStatus
    details: list[str] = field(default_factory=_empty_str_list)


def _bronze_spec(name: str, sync_category: str) -> SurfaceSpec:
    """Build classification spec for a Bronze ingestion contract."""
    return SurfaceSpec(
        physical_name=f"bronze.{name}",
        layer=SurfaceLayer.BRONZE,
        domain="Raw",
        grain="File",
        producer="ControlPlane",
        usage=SurfaceUsageCategory.EVIDENCE,
        purpose=f"Evidence Explorer / bounded raw preview (sync category: {sync_category})",
        required_columns=(),
    )


def _silver_spec(
    name: str,
    domain: str,
    grain: str,
    producer: str,
    purpose: str,
    required_columns: tuple[str, ...],
) -> SurfaceSpec:
    """Build classification spec for a Silver dimension, reference, or fact table."""
    return SurfaceSpec(
        physical_name=f"silver.{name}",
        layer=SurfaceLayer.SILVER,
        domain=domain,
        grain=grain,
        producer=producer,
        usage=SurfaceUsageCategory.DRILL,
        purpose=purpose,
        required_columns=required_columns,
    )


def _gold_spec(
    name: str,
    domain: str,
    grain: str,
    producer: str,
    purpose: str,
    required_columns: tuple[str, ...],
    primary_filters: tuple[str, ...] = (),
) -> SurfaceSpec:
    """Build classification spec for a Gold analytical mart."""
    return SurfaceSpec(
        physical_name=f"gold.{name}",
        layer=SurfaceLayer.GOLD,
        domain=domain,
        grain=grain,
        producer=producer,
        usage=SurfaceUsageCategory.ANALYTICAL,
        purpose=purpose,
        required_columns=required_columns,
        primary_filters=primary_filters,
    )


def _meta_spec(name: str, grain: str, purpose: str, cols: tuple[str, ...]) -> SurfaceSpec:
    """Build classification spec for a Meta warehouse table."""
    return SurfaceSpec(
        physical_name=f"meta.{name}",
        layer=SurfaceLayer.META,
        domain="Meta",
        grain=grain,
        producer="MetaLayer",
        usage=SurfaceUsageCategory.OPERATIONS,
        purpose=purpose,
        required_columns=cols,
    )


def _cp_spec(
    name: str,
    grain: str,
    purpose: str,
    cols: tuple[str, ...],
    usage: SurfaceUsageCategory = SurfaceUsageCategory.OPERATIONS,
) -> SurfaceSpec:
    """Build classification spec for a Control Plane SQLite table."""
    return SurfaceSpec(
        physical_name=name,
        layer=SurfaceLayer.CONTROL_PLANE,
        domain="Operations",
        grain=grain,
        producer="ControlPlane",
        usage=usage,
        purpose=purpose,
        required_columns=cols,
    )


_ATLAS_SPECS: tuple[SurfaceSpec, ...] = (
    # --- 20 Bronze Contracts ---
    _bronze_spec("household_zcategory", "sqlite_source"),
    _bronze_spec("household_asset_group", "sqlite_source"),
    _bronze_spec("household_assets", "sqlite_source"),
    _bronze_spec("household_currency", "sqlite_source"),
    _bronze_spec("household_inoutcome", "sqlite_source"),
    _bronze_spec("column_master", "column_master"),
    _bronze_spec("mutual_fund_instrument_mapping", "mutual_fund_instrument_mapping"),
    _bronze_spec("instrument_master", "instrument_master"),
    _bronze_spec("opening_balances", "opening_balances"),
    _bronze_spec("benchmark_master", "benchmark_master"),
    _bronze_spec("financial_year_parameters", "financial_year_parameters"),
    _bronze_spec("currency_reference", "currency_reference"),
    _bronze_spec("mf_market_data", "mf_holdings"),
    _bronze_spec("mf_transactions", "mf_orders"),
    _bronze_spec("stock_market_data", "stock_pl"),
    _bronze_spec("stock_transactions", "stock_orders"),
    _bronze_spec("us_stock_transactions", "us_stock_transactions"),
    _bronze_spec("currency_fx_rates", "currency_history"),
    _bronze_spec("benchmark_history", "benchmark_history"),
    _bronze_spec("us_stock_prices", "us_stock_market"),
    # --- 24 Silver Tables ---
    _silver_spec(
        "dim_calendar",
        "Common",
        "Day",
        "CoreEngine",
        "Authoritative day/month/quarter/FY calendar semantics",
        ("Date", "Day", "Month", "Year", "Financial_Year", "Start_of_Month", "End_of_Month"),
    ),
    _silver_spec(
        "dim_income_category",
        "Wealth",
        "Category",
        "CoreEngine",
        "Income category labels",
        ("UID", "CATEGORY_NAME", "CATEGORY_NAME_SHORT"),
    ),
    _silver_spec(
        "dim_income_subcategory",
        "Wealth",
        "Subcategory",
        "CoreEngine",
        "Income classification, active/passive flags, and tax mappings",
        ("UID", "CATEGORY_ID", "CATEGORY_NAME", "Is_Active_Income", "Tax_Income_Head"),
    ),
    _silver_spec(
        "dim_expense_category",
        "Wealth",
        "Category",
        "CoreEngine",
        "Expense category labels",
        ("UID", "CATEGORY_NAME"),
    ),
    _silver_spec(
        "dim_expense_subcategory",
        "Wealth",
        "Subcategory",
        "CoreEngine",
        "Expense subcategory and core-expense flag",
        ("UID", "CATEGORY_ID", "CATEGORY_NAME", "Is_Core_Expense"),
    ),
    _silver_spec(
        "dim_asset_category",
        "Wealth",
        "Category",
        "CoreEngine",
        "Asset group, ledger role, and cashflow activity classification",
        ("UID", "ASSET_GROUP", "TYPE", "ledger_role", "cashflow_activity_type"),
    ),
    _silver_spec(
        "dim_asset_subcategory",
        "Wealth",
        "Subcategory",
        "CoreEngine",
        "Account/asset detail and liquidity attributes",
        ("UID", "ASSET_GROUP_ID", "CURRENCY_ID", "ASSET_NAME", "Is_Liquid"),
    ),
    _silver_spec(
        "dim_currency",
        "Common",
        "Currency",
        "CoreEngine",
        "Currency names, symbols, and FX tickers",
        ("UID", "CURRENCY_NAME", "ISO", "Target_Currency_Code", "SYMBOL"),
    ),
    _silver_spec(
        "fact_currency_fx_rates",
        "Common",
        "Date-Currency",
        "CoreEngine",
        "Historical FX rate evidence and gap provenance",
        ("Date", "Currency_ID", "Currency_Code", "Target_Currency_Code", "FX_Rate"),
    ),
    _silver_spec(
        "fact_income_transactions",
        "Wealth",
        "Transaction",
        "CoreEngine",
        "Canonical income transactions and account/local/base amounts",
        ("UID", "DATE", "ASSET_ID", "CATEGORY_ID", "CURRENCY_ID", "BASE_AMOUNT"),
    ),
    _silver_spec(
        "fact_expense_transactions",
        "Wealth",
        "Transaction",
        "CoreEngine",
        "Canonical expense transactions and core-expense detail",
        ("UID", "DATE", "ASSET_ID", "CATEGORY_ID", "CURRENCY_ID", "Is_Core_Expense", "BASE_AMOUNT"),
    ),
    _silver_spec(
        "fact_transfer_transactions",
        "Wealth",
        "Transaction",
        "CoreEngine",
        "Transfers, adjusted analysis dates, and activity classification",
        ("UID", "DATE", "ASSET_ID", "TO_ASSET_ID", "CURRENCY_ID", "BASE_AMOUNT"),
    ),
    _silver_spec(
        "fact_opening_balances",
        "Wealth",
        "Balance",
        "CoreEngine",
        "Opening account balances feeding wealth state",
        ("ZASSETUID", "ZAMOUNTACCOUNT", "ZTXDATESTR", "ZUTIME"),
    ),
    _silver_spec(
        "dim_benchmark_master",
        "Investments",
        "Benchmark",
        "CoreEngine",
        "Benchmark labels, currencies, and market tickers",
        ("ID", "CURRENCY_ID", "Benchmark_Name", "yF_Ticker"),
    ),
    _silver_spec(
        "dim_instrument_master",
        "Investments",
        "ISIN",
        "CoreEngine",
        "Instrument master: class/type/subtype/sector/geo/tax mapping",
        ("ISIN", "BENCHMARK_ID", "CURRENCY_ID", "INSTRUMENT_NAME", "INSTRUMENT_CLASS"),
    ),
    _silver_spec(
        "ref_financial_year_parameters",
        "Investments",
        "FinancialYear",
        "CoreEngine",
        "FY-specific inflation, risk-free, and tax parameters",
        ("FY", "FY_Start_Date", "FY_End_Date", "Inflation_Rate", "Risk_Free_Rate"),
    ),
    _silver_spec(
        "fact_investment_market_data",
        "Investments",
        "Quote",
        "CoreEngine",
        "Holdings/valuation history with local/base and provider provenance",
        ("Date", "ISIN", "CURRENCY_ID", "Quantity", "Closing_Price", "Closing_Value"),
    ),
    _silver_spec(
        "fact_investment_purchases",
        "Investments",
        "Trade",
        "CoreEngine",
        "Canonical purchase trades and FX/local values",
        ("Purchase_ID", "Date", "ISIN", "CURRENCY_ID", "Quantity", "Price", "Value"),
    ),
    _silver_spec(
        "fact_investment_sales",
        "Investments",
        "Trade",
        "CoreEngine",
        "Canonical sales and reference P&L fields",
        ("Sale_ID", "Date", "ISIN", "CURRENCY_ID", "Quantity", "Sell_Price", "Sell_Value"),
    ),
    _silver_spec(
        "fact_investment_benchmark_data",
        "Investments",
        "Quote",
        "CoreEngine",
        "Historical benchmark observations and gap provenance",
        ("Date", "ID", "CURRENCY_ID", "Benchmark_Name", "Close"),
    ),
    _silver_spec(
        "fact_investment_lot_analytics",
        "Investments",
        "ISIN-Lot",
        "InvestmentQuantEngine",
        "Open-lot accounting, performance, tax, benchmark, and FX surface",
        ("Lot_ID", "Closing_Date", "ISIN", "CURRENCY_ID", "Buy_Date", "Quantity", "Close_Value"),
    ),
    _silver_spec(
        "fact_investment_realized_events",
        "Investments",
        "RealizedEvent",
        "InvestmentQuantEngine",
        "FIFO disposal events, cost basis, local/base/FX P&L, and tax",
        ("Realized_Event_ID", "Sale_ID", "Lot_ID", "ISIN", "Disposal_Date", "FY"),
    ),
    _silver_spec(
        "fact_investment_reconciliation_events",
        "Investments",
        "ReconEvent",
        "InvestmentQuantEngine",
        "Broker vs reconstructed quantity and cost-basis adjustments",
        ("Reconciliation_Group_ID", "Reconciliation_Event_ID", "Run_ID", "ISIN", "Adjustment_Type"),
    ),
    _silver_spec(
        "fact_tax_events",
        "Taxes",
        "TaxEvent",
        "TaxEngine",
        "Canonical ledger and investment tax events with readiness status",
        ("Tax_Event_ID", "FY", "Event_Date", "Source_Type", "Income_Head", "Amount_INR"),
    ),
    # --- 22 Gold Marts ---
    _gold_spec(
        "tax_financial_year_summary",
        "Taxes",
        "FY",
        "TaxEngine",
        "FY tax cockpit, completeness, and realized/unrealized exposure",
        ("FY", "Total_Ledger_Income", "Total_Investment_Gain_Loss", "Indicative_Net_Tax_Exposure"),
        ("FY",),
    ),
    _gold_spec(
        "tax_income_breakdown",
        "Taxes",
        "FY-Head-SubHead-Source",
        "TaxEngine",
        "Income-head and sub-head tax decomposition",
        ("FY", "Income_Head", "Tax_Sub_Head", "Source_Type", "Event_Amount_INR"),
        ("FY", "Income_Head", "Tax_Sub_Head"),
    ),
    _gold_spec(
        "tax_reconciliation",
        "Taxes",
        "FY-SubHead",
        "TaxEngine",
        "Ledger-vs-quant tax reconciliation and exclusions",
        ("FY", "Tax_Sub_Head", "Ledger_Tax_Event_Amount", "Quant_Realized_Gain_Loss"),
        ("FY", "Tax_Sub_Head"),
    ),
    _gold_spec(
        "wealth_asset_breakdown",
        "Wealth",
        "Month-Asset",
        "WealthPresentationEngine",
        "Asset-level monthly balances, liquidity, drawdown, flows, and runway",
        ("MONTH_START_DATE", "MONTH_END_DATE", "ASSET_SUBCATEGORY_ID", "Closing_Balance"),
        ("MONTH_START_DATE", "ASSET_SUBCATEGORY_ID"),
    ),
    _gold_spec(
        "core_monthly_fact",
        "Wealth",
        "Month",
        "WealthPresentationEngine",
        "Canonical monthly financial cockpit and net-worth attribution",
        ("MONTH_START_DATE", "MONTH_END_DATE", "Total_Income", "Total_Expense", "Total_Net_Worth"),
        ("MONTH_START_DATE",),
    ),
    _gold_spec(
        "cashflow_expense_breakdown",
        "Wealth",
        "Month-ExpenseCategory",
        "WealthPresentationEngine",
        "Expense category trends and core/discretionary/investment spend",
        ("MONTH_START_DATE", "MONTH_END_DATE", "CATEGORY_ID", "Total_Monthly_Spend"),
        ("MONTH_START_DATE", "CATEGORY_ID"),
    ),
    _gold_spec(
        "cashflow_income_breakdown",
        "Wealth",
        "Month-IncomeCategory",
        "WealthPresentationEngine",
        "Income streams and active/passive/dividend/interest decomposition",
        ("MONTH_START_DATE", "MONTH_END_DATE", "CATEGORY_ID", "Total_Monthly_Income"),
        ("MONTH_START_DATE", "CATEGORY_ID"),
    ),
    _gold_spec(
        "wealth_fire_analytics",
        "Wealth",
        "Month",
        "WealthPresentationEngine",
        "FI targets, probabilities, runway, wealth velocity, and savings required",
        ("MONTH_START_DATE", "MONTH_END_DATE", "Projected_FI_Date_P50", "Target_FI_Today"),
        ("MONTH_START_DATE",),
    ),
    _gold_spec(
        "forecast_budget_variance",
        "Wealth",
        "Month",
        "WealthPresentationEngine",
        "Budget vs actual, overspend, investment funding, and runway",
        ("MONTH_START_DATE", "MONTH_END_DATE", "Actual_Income", "Budget_Income"),
        ("MONTH_START_DATE",),
    ),
    _gold_spec(
        "investment_portfolio_summary",
        "Investments",
        "Month-ISIN",
        "InvestmentQuantEngine",
        "Month-ISIN allocation, performance, drift, and harvesting priority",
        ("MONTH_START_DATE", "Max_Closing_Date", "ISIN", "ISIN_Market_Value", "ISIN_Book_Value"),
        ("MONTH_START_DATE", "ISIN"),
    ),
    _gold_spec(
        "cashflow_efficiency_analytics",
        "Wealth",
        "Month",
        "WealthPresentationEngine",
        "Savings/investment efficiency, income mix, liquidity, and resilience",
        ("MONTH_START_DATE", "MONTH_END_DATE", "Savings_Rate_Pct", "Investment_Rate_Pct"),
        ("MONTH_START_DATE",),
    ),
    _gold_spec(
        "investment_by_isin",
        "Investments",
        "Date-ISIN",
        "InvestmentQuantEngine",
        "Deep ISIN performance, tax, benchmark, and FX metrics",
        ("Closing_Date", "ISIN", "CURRENCY_ID", "Total_Invested_Value", "Total_Current_Value"),
        ("Closing_Date", "ISIN", "CURRENCY_ID"),
    ),
    _gold_spec(
        "investment_by_subtype",
        "Investments",
        "Date-Subtype",
        "InvestmentQuantEngine",
        "Allocation and performance by instrument subtype",
        ("Closing_Date", "INSTRUMENT_SUBTYPE", "Total_Invested_Value", "Total_Current_Value"),
        ("Closing_Date", "INSTRUMENT_SUBTYPE"),
    ),
    _gold_spec(
        "investment_by_class",
        "Investments",
        "Date-Class",
        "InvestmentQuantEngine",
        "Allocation and performance by asset class",
        ("Closing_Date", "INSTRUMENT_CLASS", "Total_Invested_Value", "Total_Current_Value"),
        ("Closing_Date", "INSTRUMENT_CLASS"),
    ),
    _gold_spec(
        "investment_by_instrument_type",
        "Investments",
        "Date-InstrumentType",
        "InvestmentQuantEngine",
        "Allocation and performance by instrument type",
        ("Closing_Date", "INSTRUMENT_TYPE", "Total_Invested_Value", "Total_Current_Value"),
        ("Closing_Date", "INSTRUMENT_TYPE"),
    ),
    _gold_spec(
        "investment_by_sector",
        "Investments",
        "Date-Sector",
        "InvestmentQuantEngine",
        "Sector allocation and performance",
        ("Closing_Date", "SECTOR", "Total_Invested_Value", "Total_Current_Value"),
        ("Closing_Date", "SECTOR"),
    ),
    _gold_spec(
        "investment_by_industry",
        "Investments",
        "Date-Industry",
        "InvestmentQuantEngine",
        "Industry allocation and performance",
        ("Closing_Date", "INDUSTRY", "Total_Invested_Value", "Total_Current_Value"),
        ("Closing_Date", "INDUSTRY"),
    ),
    _gold_spec(
        "investment_by_portfolio",
        "Investments",
        "Date",
        "InvestmentQuantEngine",
        "Whole-portfolio return, concentration, and LTCG maturity ladder",
        ("Closing_Date", "Total_Invested_Value", "Total_Current_Value", "XIRR"),
        ("Closing_Date",),
    ),
    _gold_spec(
        "investment_by_geo",
        "Investments",
        "Date-Geo",
        "InvestmentQuantEngine",
        "Geographic allocation and performance",
        ("Closing_Date", "GEO", "Total_Invested_Value", "Total_Current_Value"),
        ("Closing_Date", "GEO"),
    ),
    _gold_spec(
        "investment_by_country",
        "Investments",
        "Date-Country",
        "InvestmentQuantEngine",
        "Country allocation with local/base and FX decomposition",
        ("Closing_Date", "COUNTRY", "Total_Invested_Value", "Total_Current_Value"),
        ("Closing_Date", "COUNTRY"),
    ),
    _gold_spec(
        "investment_by_currency",
        "Investments",
        "Date-Currency",
        "InvestmentQuantEngine",
        "Currency exposure with local/base return decomposition",
        ("Closing_Date", "CURRENCY_ID", "Total_Invested_Value", "Total_Current_Value"),
        ("Closing_Date", "CURRENCY_ID"),
    ),
    _gold_spec(
        "cashflow_activity_summary",
        "Wealth",
        "Month",
        "WealthPresentationEngine",
        "Operating/investing/financing cashflow statement and reconciliation",
        ("MONTH_START_DATE", "MONTH_END_DATE", "Opening_Cash_Balance", "Unreconciled_Difference"),
        ("MONTH_START_DATE",),
    ),
    # --- 6 Meta Tables ---
    _meta_spec(
        "m_File_Registry",
        "File",
        "Latest warehouse-side artifact registry and Bronze row counts",
        ("file_id", "file_name", "relative_path", "file_category", "file_hash", "row_count"),
    ),
    _meta_spec(
        "m_Table_Row_Counts",
        "Table",
        "Latest Silver/Gold table row counts and generation timestamp",
        ("schema_name", "table_name", "row_count", "generated_at"),
    ),
    _meta_spec(
        "m_Financial_Rules",
        "RuleEntry",
        "Flattened latest FinancialRules audit projection",
        ("Rule_Domain", "Rule_Type", "Target_Level", "Target_ID"),
    ),
    _meta_spec(
        "m_Settings",
        "SettingKey",
        "Flattened latest Settings audit projection",
        ("Setting_Group", "Setting_Key", "Setting_Value"),
    ),
    _meta_spec(
        "m_Data_Contracts",
        "Contract",
        "Runtime contract catalog: layer, grain, producer, and publication order",
        (
            "contract_id",
            "layer",
            "physical_table",
            "domain",
            "grain",
            "producer",
            "is_full_replace",
            "publication_order",
        ),
    ),
    _meta_spec(
        "m_Latest_Simulation",
        "Simulation",
        "Latest simulation fingerprints, seed, horizon, snapshots, and model version",
        ("simulation_id", "run_id", "created_at", "root_seed", "iterations", "horizon", "status"),
    ),
    # --- 10 Control Plane Tables ---
    _cp_spec(
        "cp_file_registry",
        "File",
        "Current source artifact state, hashes, size, and sync history",
        (
            "file_id",
            "file_name",
            "relative_path",
            "file_category",
            "file_hash",
            "sync_status",
            "first_seen_at",
            "last_seen_at",
        ),
    ),
    _cp_spec(
        "cp_file_payloads",
        "File",
        "Retained raw evidence bytes (on-demand download only)",
        ("file_id", "file_bytes"),
        usage=SurfaceUsageCategory.EVIDENCE,
    ),
    _cp_spec(
        "cp_settings_snapshots",
        "Snapshot",
        "Immutable effective Settings snapshots used by runs",
        ("snapshot_id", "content_hash", "canonical_payload", "created_at"),
    ),
    _cp_spec(
        "cp_rules_snapshots",
        "Snapshot",
        "Immutable effective FinancialRules snapshots used by runs",
        ("snapshot_id", "content_hash", "canonical_payload", "created_at"),
    ),
    _cp_spec(
        "cp_runs",
        "Run",
        "Run history, commit state, versions, snapshots, and compressed JSONL trace",
        (
            "run_id",
            "started_at",
            "finished_at",
            "status",
            "commit_state",
            "application_version",
            "schema_version",
            "settings_snapshot_id",
            "rules_snapshot_id",
            "execution_log",
        ),
    ),
    _cp_spec(
        "cp_run_failures",
        "Failure",
        "Failure stage/type/domain/message/traceback and failed ISIN",
        ("id", "run_id", "failed_isin", "stage", "error_type", "error_message", "failure_domain"),
    ),
    _cp_spec(
        "cp_artifact_run_events",
        "ArtifactEvent",
        "Source change timeline, replay/convergence, and affected-table lineage",
        ("event_id", "run_id", "file_id", "event_type", "event_at", "affected_table"),
    ),
    _cp_spec(
        "cp_external_provider_requests",
        "ProviderRequest",
        "Provider/entity/request window, attempts, status, and failure details",
        ("request_id", "run_id", "provider", "entity", "attempt_count", "status", "created_at"),
    ),
    _cp_spec(
        "cp_run_sync_summaries",
        "RunSync",
        "Per-run actionable/synced/healed/failed/deferred ingestion summary",
        (
            "run_id",
            "actionable_count",
            "synced_count",
            "healed_count",
            "failed_count",
            "deferred_count",
            "sync_status",
            "created_at",
        ),
    ),
    _cp_spec(
        "cp_simulation_runs",
        "Simulation",
        "Simulation history, seed, horizon, and reproducibility fingerprints",
        (
            "simulation_id",
            "run_id",
            "created_at",
            "root_seed",
            "iterations",
            "horizon",
            "settings_snapshot_id",
            "rules_snapshot_id",
            "status",
        ),
    ),
)

SURFACE_ATLAS: dict[str, SurfaceSpec] = {spec.physical_name: spec for spec in _ATLAS_SPECS}


def _parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp string or datetime instance safely."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _parse_json_mapping(
    raw_payload: Any,
    label: str,
    diagnostics: list[str],
) -> dict[str, Any] | None:
    """Deserialize a canonical JSON payload string into a typed dictionary."""
    if raw_payload is None:
        diagnostics.append(f"{label} payload is NULL.")
        return None
    if isinstance(raw_payload, Mapping):
        return dict(cast("Mapping[str, Any]", raw_payload))
    if not isinstance(raw_payload, str):
        diagnostics.append(f"{label} payload has unexpected type {type(raw_payload).__name__}.")
        return None
    try:
        parsed: Any = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        diagnostics.append(f"Malformed {label} JSON payload: {exc}")
        return None
    if not isinstance(parsed, dict):
        diagnostics.append(f"{label} JSON root must be an object, got {type(parsed).__name__}.")
        return None
    return cast("dict[str, Any]", parsed)


def _flatten_leaves(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested dictionaries into dotted leaf key paths."""
    leaves: dict[str, Any] = {}
    for key, val in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(val, Mapping) and val:
            leaves.update(_flatten_leaves(cast("Mapping[str, Any]", val), path))
        else:
            leaves[path] = val
    return leaves


def _values_equivalent(toml_val: Any, snapshot_val: Any) -> bool:
    """Compare a raw TOML leaf value against a serialized snapshot leaf value."""
    if toml_val == snapshot_val:
        return True
    if (
        isinstance(toml_val, (int, float))
        and not isinstance(toml_val, bool)
        and isinstance(snapshot_val, (int, float))
        and not isinstance(snapshot_val, bool)
    ):
        return float(toml_val) == float(snapshot_val)
    if isinstance(toml_val, str) and isinstance(snapshot_val, str):
        norm_toml = toml_val.strip().replace("\\", "/").rstrip("/")
        norm_snap = snapshot_val.strip().replace("\\", "/").rstrip("/")
        if norm_toml.lower() == norm_snap.lower():
            return True
    if isinstance(toml_val, list) and isinstance(snapshot_val, list):
        left = cast(Sequence[Any], toml_val)
        right = cast(Sequence[Any], snapshot_val)
        return len(left) == len(right) and all(
            _values_equivalent(a, b) for a, b in zip(left, right, strict=True)
        )
    if not isinstance(toml_val, (str, int, float, bool, list, dict)) and isinstance(
        snapshot_val, str
    ):
        # e.g., tomllib parses TOML dates as datetime.date while JSON stores ISO strings
        iso_fn = getattr(toml_val, "isoformat", None)
        if callable(iso_fn):
            return str(iso_fn()) == snapshot_val
    return False


def evaluate_profile_drift(
    profile: PFETLProfile,
    provenance: RunProvenance | None,
) -> DriftCheckResult:
    """Compare explicit leaves in selected Settings/Rules TOMLs against latest run snapshots.

    Parameters
    ----------
    profile : PFETLProfile
        Active resolved PFETL profile pointing to local Settings and Rules TOMLs.
    provenance : RunProvenance | None
        Latest committed run provenance carrying deserialized Settings and Rules snapshots.

    Returns
    -------
    DriftCheckResult
        Drift classification (`MATCH`, `DRIFTED`, or `UNVERIFIED_DEFAULTS`) and diff details.
    """
    if provenance is None:
        return DriftCheckResult(
            status=ProfileDriftStatus.UNVERIFIED_DEFAULTS,
            details=["No successful committed run found in Control Plane to verify drift."],
        )
    if provenance.settings_payload is None or provenance.rules_payload is None:
        return DriftCheckResult(
            status=ProfileDriftStatus.UNVERIFIED_DEFAULTS,
            details=[
                f"Snapshot payload incomplete for run '{provenance.run_id}'; drift unverified."
            ],
        )

    try:
        raw_settings: dict[str, Any] = tomllib.loads(profile.settings_path.read_text("utf-8"))
        raw_rules: dict[str, Any] = tomllib.loads(profile.rules_path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return DriftCheckResult(
            status=ProfileDriftStatus.UNVERIFIED_DEFAULTS,
            details=[f"Unable to parse selected TOMLs during drift comparison: {exc}"],
        )

    drifted: list[str] = []
    defaults_only: list[str] = []
    pairs: tuple[tuple[str, Mapping[str, Any], Mapping[str, Any]], ...] = (
        ("settings", raw_settings, provenance.settings_payload),
        ("rules", raw_rules, provenance.rules_payload),
    )

    for scope_name, toml_map, snap_map in pairs:
        toml_leaves = _flatten_leaves(toml_map)
        snap_leaves = _flatten_leaves(snap_map)

        for key_path, toml_val in sorted(toml_leaves.items()):
            if key_path not in snap_leaves:
                # Special allowance: empty TOML section matches empty snapshot mapping
                if isinstance(toml_val, dict) and any(
                    k.startswith(f"{key_path}.") for k in snap_leaves
                ):
                    continue
                drifted.append(f"{scope_name}.{key_path}: present in TOML, absent from snapshot")
            elif not _values_equivalent(toml_val, snap_leaves[key_path]):
                drifted.append(
                    f"{scope_name}.{key_path}: TOML={toml_val!r} != snapshot={snap_leaves[key_path]!r}"
                )

        for key_path in sorted(snap_leaves):
            if key_path not in toml_leaves:
                defaults_only.append(f"{scope_name}.{key_path}")

    if drifted:
        return DriftCheckResult(status=ProfileDriftStatus.DRIFTED, details=drifted)
    if defaults_only:
        summary = defaults_only[:10]
        if len(defaults_only) > 10:
            summary.append(f"... and {len(defaults_only) - 10} additional effective defaults")
        return DriftCheckResult(status=ProfileDriftStatus.UNVERIFIED_DEFAULTS, details=summary)
    return DriftCheckResult(status=ProfileDriftStatus.MATCH, details=[])


class PFETLCatalog:
    """Runtime surface catalog, compatibility validator, and provenance resolver."""

    def __init__(self, profile: PFETLProfile) -> None:
        """Initialize catalog service for an active PFETL profile.

        Parameters
        ----------
        profile : PFETLProfile
            Resolved PFETL profile context.
        """
        self.profile = profile
        self.duckdb_reader = DuckDBReader(profile)
        self.cp_reader = ControlPlaneReader(profile)

    def discover(self) -> tuple[dict[str, CatalogEntry], CompatibilityReport]:
        """Discover all DuckDB and Control Plane surfaces and validate compatibility.

        Returns
        -------
        tuple[dict[str, CatalogEntry], CompatibilityReport]
            Mapping of physical table name to `CatalogEntry` alongside the structured
            `CompatibilityReport`.
        """
        warehouse_state = self.duckdb_reader.get_state()
        cp_state = self.cp_reader.get_state()
        entries: dict[str, CatalogEntry] = {}
        diagnostics: list[str] = []

        if warehouse_state == WarehouseState.AVAILABLE:
            self._discover_duckdb(entries, diagnostics)
        elif warehouse_state == WarehouseState.LOCKED:
            diagnostics.append("DuckDB warehouse is locked by PFETL pipeline.lock.")
        else:
            diagnostics.append(f"DuckDB warehouse unavailable at '{self.profile.duckdb_path}'.")

        if cp_state == ControlPlaneState.AVAILABLE:
            self._discover_control_plane(entries, diagnostics)
        else:
            diagnostics.append(
                f"Control Plane SQLite unavailable at '{self.profile.control_plane_path}'."
            )

        report = self._build_compatibility_report(
            entries=entries,
            warehouse_state=warehouse_state,
            cp_state=cp_state,
            diagnostics=diagnostics,
        )
        return entries, report

    def _discover_duckdb(
        self,
        entries: dict[str, CatalogEntry],
        diagnostics: list[str],
    ) -> None:
        """Populate catalog entries from DuckDB information_schema and meta tables."""
        try:
            tables = self.duckdb_reader.fetch_all(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema IN ('bronze', 'silver', 'gold', 'meta')
                ORDER BY table_schema, table_name;
                """
            )
            col_rows = self.duckdb_reader.fetch_all(
                """
                SELECT table_schema, table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema IN ('bronze', 'silver', 'gold', 'meta')
                ORDER BY table_schema, table_name, ordinal_position;
                """
            )
        except PFETLDataAccessError as exc:
            diagnostics.append(f"Failed inspecting DuckDB schemas: {exc}")
            return

        contracts_map = self._load_contracts_map()
        row_counts_map = self._load_row_counts_map()

        columns_by_table: dict[str, list[ColumnInfo]] = {}
        for col in col_rows:
            phys = f"{col['table_schema']}.{col['table_name']}"
            columns_by_table.setdefault(phys, []).append(
                ColumnInfo(
                    name=str(col["column_name"]),
                    data_type=str(col["data_type"]),
                    nullable=str(col.get("is_nullable", "YES")).upper() == "YES",
                )
            )

        for row in tables:
            schema = str(row["table_schema"])
            table = str(row["table_name"])
            phys = f"{schema}.{table}"
            layer = SurfaceLayer(schema)
            spec = SURFACE_ATLAS.get(phys)
            contract = contracts_map.get(phys, {})

            domain = str(contract.get("domain") or (spec.domain if spec else "Unknown"))
            grain = str(contract.get("grain") or (spec.grain if spec else "Unknown"))
            producer = str(contract.get("producer") or (spec.producer if spec else "Unknown"))

            entries[phys] = CatalogEntry(
                schema_name=schema,
                table_name=table,
                physical_name=phys,
                layer=layer,
                domain=domain,
                grain=grain,
                producer=producer,
                columns=columns_by_table.get(phys, []),
                row_count=row_counts_map.get(phys),
                classification=spec.usage if spec else None,
                spec=spec,
            )

    def _load_contracts_map(self) -> dict[str, dict[str, Any]]:
        """Load runtime metadata from `meta.m_Data_Contracts` if present."""
        try:
            if not self.duckdb_reader.table_exists("meta", "m_Data_Contracts"):
                return {}
            rows = self.duckdb_reader.fetch_all(
                """
                SELECT physical_table, domain, grain, producer
                FROM meta.m_Data_Contracts;
                """
            )
            return {str(r["physical_table"]): r for r in rows if r.get("physical_table")}
        except PFETLDataAccessError:
            return {}

    def _load_row_counts_map(self) -> dict[str, int]:
        """Load precomputed Silver/Gold row counts from `meta.m_Table_Row_Counts` if present."""
        try:
            if not self.duckdb_reader.table_exists("meta", "m_Table_Row_Counts"):
                return {}
            rows = self.duckdb_reader.fetch_all(
                """
                SELECT schema_name, table_name, row_count
                FROM meta.m_Table_Row_Counts;
                """
            )
            out: dict[str, int] = {}
            for r in rows:
                if r.get("schema_name") and r.get("table_name") and r.get("row_count") is not None:
                    out[f"{r['schema_name']}.{r['table_name']}"] = int(r["row_count"])
            return out
        except PFETLDataAccessError:
            return {}

    def _discover_control_plane(
        self,
        entries: dict[str, CatalogEntry],
        diagnostics: list[str],
    ) -> None:
        """Populate catalog entries from SQLite Control Plane schema metadata."""
        try:
            table_rows = self.cp_reader.fetch_all(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name;
                """
            )
        except PFETLDataAccessError as exc:
            diagnostics.append(f"Failed inspecting Control Plane schema: {exc}")
            return

        for row in table_rows:
            table_name = str(row["name"])
            cols: list[ColumnInfo] = []
            try:
                safe_name = validate_identifier(table_name)
                pragma_rows = self.cp_reader.fetch_all(f'PRAGMA table_info("{safe_name}");')
                for col in pragma_rows:
                    cols.append(
                        ColumnInfo(
                            name=str(col["name"]),
                            data_type=str(col.get("type") or "TEXT"),
                            nullable=not bool(col.get("notnull", 0)),
                        )
                    )
            except (ValueError, PFETLDataAccessError) as exc:
                diagnostics.append(f"Unable to inspect columns for '{table_name}': {exc}")

            spec = SURFACE_ATLAS.get(table_name)
            entries[table_name] = CatalogEntry(
                schema_name="control_plane",
                table_name=table_name,
                physical_name=table_name,
                layer=SurfaceLayer.CONTROL_PLANE,
                domain=spec.domain if spec else "Operations",
                grain=spec.grain if spec else "Unknown",
                producer=spec.producer if spec else "ControlPlane",
                columns=cols,
                row_count=None,
                classification=spec.usage if spec else None,
                spec=spec,
            )

    def _build_compatibility_report(
        self,
        entries: dict[str, CatalogEntry],
        warehouse_state: WarehouseState,
        cp_state: ControlPlaneState,
        diagnostics: list[str],
    ) -> CompatibilityReport:
        """Evaluate catalog discoveries against `SURFACE_ATLAS` expectations."""
        layer_counts: dict[SurfaceLayer, int] = {layer: 0 for layer in SurfaceLayer}
        supported_surfaces: list[str] = []
        unexpected_new_surfaces: list[str] = []
        missing_required_columns: dict[str, list[str]] = {}
        extra_columns: dict[str, list[str]] = {}

        for phys, entry in sorted(entries.items()):
            layer_counts[entry.layer] += 1
            spec = entry.spec
            if spec is None:
                unexpected_new_surfaces.append(phys)
                diagnostics.append(f"Unclassified new surface detected: '{phys}'.")
                continue

            supported_surfaces.append(phys)
            actual_cols = {c.name for c in entry.columns}
            missing_cols = [c for c in spec.required_columns if c not in actual_cols]
            if missing_cols:
                missing_required_columns[phys] = missing_cols
                diagnostics.append(
                    f"Surface '{phys}' missing required columns: {', '.join(missing_cols)}"
                )

            if spec.required_columns:
                req_set = set(spec.required_columns)
                extras = [c.name for c in entry.columns if c.name not in req_set]
                if extras:
                    extra_columns[phys] = extras

        missing_required_surfaces: list[str] = []
        duckdb_available = warehouse_state == WarehouseState.AVAILABLE
        cp_available = cp_state == ControlPlaneState.AVAILABLE

        for phys, spec in sorted(SURFACE_ATLAS.items()):
            if not spec.required:
                continue
            if spec.layer == SurfaceLayer.CONTROL_PLANE and not cp_available:
                continue
            if spec.layer != SurfaceLayer.CONTROL_PLANE and not duckdb_available:
                continue
            if phys not in entries:
                missing_required_surfaces.append(phys)
                diagnostics.append(f"Required upstream surface missing: '{phys}'.")

        has_structural_failure = bool(
            unexpected_new_surfaces or missing_required_surfaces or missing_required_columns
        )
        if has_structural_failure:
            status = CompatibilityStatus.INCOMPATIBLE
            is_compatible = False
        elif not duckdb_available or not cp_available:
            status = CompatibilityStatus.DEGRADED
            is_compatible = False
        else:
            status = CompatibilityStatus.COMPATIBLE
            is_compatible = True

        return CompatibilityReport(
            status=status,
            is_compatible=is_compatible,
            warehouse_state=warehouse_state,
            control_plane_state=cp_state,
            total_expected=len(SURFACE_ATLAS),
            total_discovered=len(entries),
            layer_counts=layer_counts,
            supported_surfaces=supported_surfaces,
            missing_required_surfaces=missing_required_surfaces,
            unexpected_new_surfaces=unexpected_new_surfaces,
            missing_required_columns=missing_required_columns,
            extra_columns=extra_columns,
            diagnostics=diagnostics,
        )

    def get_latest_provenance(self) -> RunProvenance | None:
        """Load provenance for the latest successful committed PFETL run, or None.

        Returns
        -------
        RunProvenance | None
            Hydrated provenance with linked Settings/Rules snapshots, or None when no
            committed successful run exists or Control Plane is unavailable.
        """
        if self.cp_reader.get_state() != ControlPlaneState.AVAILABLE:
            return None
        if not self.cp_reader.table_exists("cp_runs"):
            return None

        try:
            row = self.cp_reader.fetch_one(
                """
                SELECT run_id
                FROM cp_runs
                WHERE UPPER(status) = 'SUCCESS'
                  AND UPPER(commit_state) IN ('FULLY_COMMITTED', 'CONVERGED', 'COMMITTED')
                ORDER BY finished_at DESC, started_at DESC
                LIMIT 1;
                """
            )
        except PFETLDataAccessError:
            return None

        if row is None or not row.get("run_id"):
            return None
        return self.get_run_provenance(str(row["run_id"]))

    def get_run_provenance(self, run_id: str) -> RunProvenance | None:
        """Load provenance and parsed Settings/Rules snapshots for a specific run ID.

        Parameters
        ----------
        run_id : str
            Unique PFETL run identifier.

        Returns
        -------
        RunProvenance | None
            Run metadata and deserialized snapshot payloads, or None if run does not exist.
        """
        if self.cp_reader.get_state() != ControlPlaneState.AVAILABLE:
            return None
        if not self.cp_reader.table_exists("cp_runs"):
            return None

        try:
            run_row = self.cp_reader.fetch_one(
                """
                SELECT run_id, started_at, finished_at, status, commit_state,
                       application_version, schema_version, settings_snapshot_id,
                       rules_snapshot_id, (execution_log IS NOT NULL) AS has_execution_log
                FROM cp_runs
                WHERE run_id = ?;
                """,
                (run_id,),
            )
        except PFETLDataAccessError:
            return None

        if run_row is None:
            return None

        diagnostics: list[str] = []
        set_snap_id = (
            str(run_row["settings_snapshot_id"]) if run_row.get("settings_snapshot_id") else None
        )
        rules_snap_id = (
            str(run_row["rules_snapshot_id"]) if run_row.get("rules_snapshot_id") else None
        )

        settings_payload, has_settings = self._load_snapshot_payload(
            "cp_settings_snapshots", set_snap_id, "Settings snapshot", diagnostics
        )
        rules_payload, has_rules = self._load_snapshot_payload(
            "cp_rules_snapshots", rules_snap_id, "Rules snapshot", diagnostics
        )

        is_complete = bool(
            has_settings
            and has_rules
            and settings_payload is not None
            and rules_payload is not None
        )

        return RunProvenance(
            run_id=str(run_row["run_id"]),
            started_at=_parse_iso_datetime(run_row.get("started_at")),
            finished_at=_parse_iso_datetime(run_row.get("finished_at")),
            status=str(run_row["status"]) if run_row.get("status") is not None else None,
            commit_state=(
                str(run_row["commit_state"]) if run_row.get("commit_state") is not None else None
            ),
            application_version=(
                str(run_row["application_version"])
                if run_row.get("application_version") is not None
                else None
            ),
            schema_version=(
                str(run_row["schema_version"])
                if run_row.get("schema_version") is not None
                else None
            ),
            settings_snapshot_id=set_snap_id,
            rules_snapshot_id=rules_snap_id,
            settings_payload=settings_payload,
            rules_payload=rules_payload,
            has_settings_snapshot=has_settings,
            has_rules_snapshot=has_rules,
            has_execution_log=bool(run_row.get("has_execution_log")),
            is_complete=is_complete,
            diagnostics=diagnostics,
        )

    def _load_snapshot_payload(
        self,
        table_name: str,
        snapshot_id: str | None,
        label: str,
        diagnostics: list[str],
    ) -> tuple[dict[str, Any] | None, bool]:
        """Fetch and deserialize a single Control Plane snapshot payload."""
        if not snapshot_id:
            diagnostics.append(f"{label} ID missing on run record.")
            return None, False
        if not self.cp_reader.table_exists(table_name):
            diagnostics.append(f"Snapshot table '{table_name}' does not exist.")
            return None, False

        try:
            safe_table = validate_identifier(table_name)
            row = self.cp_reader.fetch_one(
                f'SELECT canonical_payload FROM "{safe_table}" WHERE snapshot_id = ?;',
                (snapshot_id,),
            )
        except (ValueError, PFETLDataAccessError) as exc:
            diagnostics.append(f"Error querying {label} '{snapshot_id}': {exc}")
            return None, False

        if row is None:
            diagnostics.append(f"{label} '{snapshot_id}' row missing from '{table_name}'.")
            return None, False

        parsed = _parse_json_mapping(row.get("canonical_payload"), label, diagnostics)
        return parsed, True


def iter_classified_surfaces() -> Iterable[SurfaceSpec]:
    """Iterate all classified surfaces in the authoritative 82-surface atlas."""
    return SURFACE_ATLAS.values()
