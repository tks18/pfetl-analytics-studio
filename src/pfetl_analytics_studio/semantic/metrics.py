"""Semantic metric registry enforcing additivity and dimension safety.

Owns
----
- `MetricAggregation`, `MetricFormat`, `MetricSpec`, and `MetricRegistry`.
- `FOUNDATIONAL_METRICS` and `DEFAULT_METRICS` covering Overview and core domain measures.
- Guardrails rejecting invalid `SUM`/`AVG` aggregations on non-additive ratios/rates/balances.

Does not own
------------
- PFETL financial calculation methodology.
- Exhaustive column browsing (owned by Explorer raw catalog).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from pfetl_analytics_studio.semantic.datasets import DEFAULT_DATASETS, DatasetRegistry


class MetricAggregation(StrEnum):
    """Approved aggregation semantics for a registered `MetricSpec`."""

    SUM = "sum"
    AVG = "avg"
    LATEST = "latest"
    WEIGHTED = "weighted"
    NONE = "none"


class MetricFormat(StrEnum):
    """Display formatting classification for KPI cards, charts, and exports."""

    CURRENCY = "currency"
    PERCENT = "percent"
    RATIO = "ratio"
    NUMBER = "number"
    INTEGER = "integer"
    DATE = "date"


class MetricOrigin(StrEnum):
    """Distinguishes canonical PFETL Core measures from Studio-derived presentation metrics."""

    CORE = "core"
    STUDIO_DERIVED = "studio_derived"


class MetricSpec(BaseModel):
    """Semantic measure contract specifying safe aggregation and dimensional grain."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    dataset: str
    expression: str
    aggregation: MetricAggregation
    format: MetricFormat
    unit: str
    allowed_dimensions: tuple[str, ...]
    additive: bool
    description: str
    origin: MetricOrigin = MetricOrigin.CORE

    @model_validator(mode="after")
    def _validate_invariants(self) -> MetricSpec:
        """Reject empty identifiers or non-additive metrics configured with `SUM`."""
        if not self.id.strip():
            raise ValueError("MetricSpec id cannot be empty.")
        if not self.expression.strip():
            raise ValueError(f"MetricSpec '{self.id}' expression cannot be empty.")
        if not self.additive and self.aggregation == MetricAggregation.SUM:
            raise ValueError(
                f"Non-additive metric '{self.id}' cannot use default aggregation 'sum'."
            )
        return self


class MetricRegistry:
    """Validated catalog of reusable semantic metrics bound to approved datasets."""

    def __init__(
        self,
        dataset_registry: DatasetRegistry,
        initial: Iterable[MetricSpec] = (),
    ) -> None:
        """Initialize metric registry bound to a `DatasetRegistry`.

        Parameters
        ----------
        dataset_registry : DatasetRegistry
            Dataset registry against which metric datasets and dimensions are validated.
        initial : Iterable[MetricSpec], optional
            Initial metric specs to register immediately.
        """
        self.dataset_registry = dataset_registry
        self._metrics: dict[str, MetricSpec] = {}
        for spec in initial:
            self.register(spec)

    def register(self, spec: MetricSpec) -> None:
        """Register a `MetricSpec`, enforcing unique IDs and valid dataset/dimensions.

        Parameters
        ----------
        spec : MetricSpec
            Metric descriptor to register.

        Raises
        ------
        ValueError
            If ID is duplicated or allowed dimensions violate the backing `DatasetSpec`.
        KeyError
            If target `spec.dataset` is missing from `dataset_registry`.
        """
        if spec.id in self._metrics:
            raise ValueError(f"Duplicate metric ID registered: '{spec.id}'.")
        self.dataset_registry.validate_dimensions(spec.dataset, spec.allowed_dimensions)
        self._metrics[spec.id] = spec

    def get(self, metric_id: str) -> MetricSpec:
        """Retrieve a registered `MetricSpec` by ID."""
        if metric_id not in self._metrics:
            raise KeyError(f"Unknown metric ID: '{metric_id}'.")
        return self._metrics[metric_id]

    def has(self, metric_id: str) -> bool:
        """Check whether `metric_id` exists in this registry."""
        return metric_id in self._metrics

    def list_metrics(self, dataset_id: str | None = None) -> list[MetricSpec]:
        """List registered metrics, optionally filtered by dataset ID."""
        items = list(self._metrics.values())
        if dataset_id is not None:
            items = [m for m in items if m.dataset == dataset_id]
        return sorted(items, key=lambda m: m.id)

    def validate_query(
        self,
        metric_id: str,
        dimensions: Sequence[str] = (),
        requested_aggregation: MetricAggregation | None = None,
    ) -> MetricSpec:
        """Validate a metric query's dimensions and aggregation semantics.

        Parameters
        ----------
        metric_id : str
            Target registered metric identifier.
        dimensions : Sequence[str], optional
            Requested grouping dimensions.
        requested_aggregation : MetricAggregation | None, optional
            Explicit aggregation requested by caller/explorer; defaults to metric default.

        Returns
        -------
        MetricSpec
            Validated `MetricSpec` ready for SQL compilation.

        Raises
        ------
        ValueError
            If dimensions are disallowed or a non-additive metric is aggregated unsafely.
        """
        spec = self.get(metric_id)
        allowed_dims = set(spec.allowed_dimensions)
        bad_dims = [d for d in dimensions if d not in allowed_dims]
        if bad_dims:
            raise ValueError(
                f"Metric '{metric_id}' does not permit dimension(s): {', '.join(bad_dims)}."
            )
        self.dataset_registry.validate_dimensions(spec.dataset, dimensions)

        agg = requested_aggregation if requested_aggregation is not None else spec.aggregation
        if not spec.additive and agg in (MetricAggregation.SUM, MetricAggregation.AVG):
            raise ValueError(
                f"Metric '{metric_id}' is non-additive and cannot be aggregated with '{agg.value}'."
            )
        if spec.aggregation == MetricAggregation.NONE and agg not in (
            MetricAggregation.NONE,
            MetricAggregation.LATEST,
        ):
            raise ValueError(
                f"Metric '{metric_id}' requires native grain ('none'/'latest'), "
                f"rejected '{agg.value}'."
            )
        return spec


def _metric(
    metric_id: str,
    label: str,
    dataset: str,
    expression: str,
    aggregation: MetricAggregation,
    fmt: MetricFormat,
    unit: str,
    allowed_dimensions: tuple[str, ...],
    additive: bool,
    description: str,
) -> MetricSpec:
    """Construct a `MetricSpec` concisely."""
    return MetricSpec(
        id=metric_id,
        label=label,
        dataset=dataset,
        expression=expression,
        aggregation=aggregation,
        format=fmt,
        unit=unit,
        allowed_dimensions=allowed_dimensions,
        additive=additive,
        description=description,
    )


_M_DIMS = ("MONTH_START_DATE", "MONTH_END_DATE")
_M_YM_DIMS = ("MONTH_START_DATE", "MONTH_END_DATE", "YEAR_MONTH")

FOUNDATIONAL_METRICS: tuple[MetricSpec, ...] = (
    # --- Overview / Household Flows (Additive) ---
    _metric(
        "total_income",
        "Total Income",
        "core_monthly_fact",
        '"Total_Income"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        True,
        "Total monthly cash + non-cash household income across canonical flows.",
    ),
    _metric(
        "total_expense",
        "Total Expense",
        "core_monthly_fact",
        '"Total_Expense"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        True,
        "Total monthly household expense (core + non-core).",
    ),
    _metric(
        "total_core_expense",
        "Core Expense",
        "core_monthly_fact",
        '"Total_Core_Expense"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        True,
        "Essential recurring household expense classified by Core rules.",
    ),
    _metric(
        "net_cashflow_month",
        "Net Cashflow",
        "core_monthly_fact",
        '"Net_Cashflow_Month"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        True,
        "Monthly net household savings flow (income minus expenses).",
    ),
    _metric(
        "net_savings_flow",
        "Net Savings Attribution",
        "core_monthly_fact",
        '"Net_Savings_Flow"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        True,
        "Monthly net-worth change attributable to net household savings.",
    ),
    _metric(
        "market_return_flow",
        "Market Return Attribution",
        "core_monthly_fact",
        '"Market_Return_Flow"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        True,
        "Monthly net-worth change attributable to market price and FX movement.",
    ),
    _metric(
        "income_breakdown_amount",
        "Category Income",
        "cashflow_income_breakdown",
        '"Total_Monthly_Income"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        (
            "MONTH_START_DATE",
            "MONTH_END_DATE",
            "YEAR_MONTH",
            "CATEGORY_NAME",
            "CATEGORY_GROUPS",
            "Is_Active_Income",
            "Is_Passive_Income",
        ),
        True,
        "Monthly income flow grouped by category/group/classification.",
    ),
    _metric(
        "expense_breakdown_amount",
        "Category Spend",
        "cashflow_expense_breakdown",
        '"Total_Monthly_Spend"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        (
            "MONTH_START_DATE",
            "MONTH_END_DATE",
            "YEAR_MONTH",
            "CATEGORY_NAME",
            "CATEGORY_GROUPS",
            "Spend_Type",
            "Is_Core_Expense",
        ),
        True,
        "Monthly expense flow grouped by category/group/core classification.",
    ),
    # --- Overview / Wealth Stock Balances (Non-Additive Point-in-Time) ---
    _metric(
        "net_worth_book",
        "Book Net Worth",
        "core_monthly_fact",
        '"Total_Net_Worth"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time book net worth at month end (non-additive across months).",
    ),
    _metric(
        "net_worth_market",
        "Market Net Worth",
        "core_monthly_fact",
        '"Total_Net_Worth_Market"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time mark-to-market net worth at month end (non-additive).",
    ),
    _metric(
        "liquid_assets_market",
        "Liquid Assets (Market)",
        "core_monthly_fact",
        '"Liquid_Assets_Market"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time liquid market wealth balance (non-additive across months).",
    ),
    _metric(
        "total_assets_market",
        "Total Assets (Market)",
        "core_monthly_fact",
        '"Total_Assets_Market"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time gross market assets at month end (non-additive).",
    ),
    _metric(
        "total_liabilities",
        "Total Liabilities",
        "core_monthly_fact",
        '"Total_Liabilities"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time total household liabilities at month end (non-additive).",
    ),
    _metric(
        "asset_closing_balance_market",
        "Asset Market Balance",
        "wealth_asset_breakdown",
        '"Closing_Balance_Market"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        ("MONTH_START_DATE", "MONTH_END_DATE", "ASSET_SUBCATEGORY_ID"),
        False,
        "Point-in-time market balance per asset subcategory at month end.",
    ),
    # --- Efficiency & Resilience Ratios (Non-Additive) ---
    _metric(
        "savings_rate_pct",
        "Savings Rate",
        "cashflow_efficiency_analytics",
        '"Savings_Rate_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Monthly canonical savings rate percentage (native/latest, non-additive).",
    ),
    _metric(
        "investment_rate_pct",
        "Investment Rate",
        "cashflow_efficiency_analytics",
        '"Investment_Rate_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Monthly capital deployment rate percentage (native/latest, non-additive).",
    ),
    _metric(
        "passive_coverage_ratio",
        "Passive / Core Coverage",
        "cashflow_efficiency_analytics",
        '"Passive_Income_Core_Expense_Coverage_Ratio"',
        MetricAggregation.LATEST,
        MetricFormat.RATIO,
        "ratio",
        _M_YM_DIMS,
        False,
        "Ratio of passive income to core household expenses (non-additive).",
    ),
    _metric(
        "active_income_dependency_pct",
        "Active Income Dependency",
        "cashflow_efficiency_analytics",
        '"Active_Income_Dependency_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Share of household income derived from active sources (non-additive).",
    ),
    _metric(
        "liquidity_ratio_months",
        "Liquidity Runway (Months)",
        "cashflow_efficiency_analytics",
        '"Liquidity_Ratio_Months"',
        MetricAggregation.LATEST,
        MetricFormat.NUMBER,
        "months",
        _M_YM_DIMS,
        False,
        "Liquid assets divided by trailing monthly core expenses (non-additive).",
    ),
    _metric(
        "debt_to_asset_ratio_pct",
        "Debt-to-Asset Ratio",
        "cashflow_efficiency_analytics",
        '"Debt_to_Asset_Ratio_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Household liabilities relative to total market assets (non-additive).",
    ),
    # --- Investments & Portfolio Snapshot (Stock/Rates Non-Additive) ---
    _metric(
        "portfolio_current_value",
        "Portfolio Value",
        "investment_by_portfolio",
        '"Total_Current_Value"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        ("Closing_Date",),
        False,
        "Total portfolio mark-to-market value at closing date (non-additive across dates).",
    ),
    _metric(
        "portfolio_invested_value",
        "Portfolio Cost Basis",
        "investment_by_portfolio",
        '"Total_Invested_Value"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        ("Closing_Date",),
        False,
        "Total open-lot cost basis at closing date (non-additive across dates).",
    ),
    _metric(
        "portfolio_xirr",
        "Portfolio XIRR",
        "investment_by_portfolio",
        '"XIRR"',
        MetricAggregation.NONE,
        MetricFormat.PERCENT,
        "percent",
        ("Closing_Date",),
        False,
        "Native money-weighted annualized portfolio XIRR (strictly non-additive).",
    ),
    _metric(
        "portfolio_active_return",
        "Active Return vs Benchmark",
        "investment_by_portfolio",
        '"Active_Return"',
        MetricAggregation.NONE,
        MetricFormat.PERCENT,
        "percent",
        ("Closing_Date",),
        False,
        "Portfolio XIRR minus benchmark XIRR (strictly non-additive).",
    ),
    _metric(
        "portfolio_bm_xirr",
        "Benchmark XIRR",
        "investment_by_portfolio",
        '"BM_XIRR"',
        MetricAggregation.NONE,
        MetricFormat.PERCENT,
        "percent",
        ("Closing_Date",),
        False,
        "Native money-weighted benchmark annualized return (strictly non-additive).",
    ),
    _metric(
        "portfolio_hhi_score",
        "Concentration (HHI)",
        "investment_by_portfolio",
        '"HHI_Score"',
        MetricAggregation.LATEST,
        MetricFormat.NUMBER,
        "index",
        ("Closing_Date",),
        False,
        "Herfindahl-Hirschman portfolio concentration index (non-additive).",
    ),
    _metric(
        "portfolio_effective_holdings",
        "Effective Holdings Count",
        "investment_by_portfolio",
        '"Effective_Holdings_Count"',
        MetricAggregation.LATEST,
        MetricFormat.NUMBER,
        "count",
        ("Closing_Date",),
        False,
        "Effective equal-weighted holdings equivalent derived from HHI (non-additive).",
    ),
    _metric(
        "isin_weight",
        "ISIN Portfolio Weight",
        "investment_portfolio_summary",
        '"ISIN_Weight"',
        MetricAggregation.NONE,
        MetricFormat.PERCENT,
        "percent",
        ("MONTH_START_DATE", "ISIN", "INSTRUMENT_CLASS", "SECTOR"),
        False,
        "Native weight of an ISIN inside portfolio at month close (non-summable).",
    ),
    # --- FIRE & Tax Snapshot ---
    _metric(
        "fire_coverage_pct",
        "FI Coverage",
        "wealth_fire_analytics",
        '"Current_FI_Coverage_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Current wealth relative to Today's FI corpus target (non-additive).",
    ),
    _metric(
        "fire_target_today",
        "Target FI Corpus",
        "wealth_fire_analytics",
        '"Target_FI_Today"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        False,
        "Financial independence corpus required today under active rules.",
    ),
    _metric(
        "fire_gap",
        "FI Corpus Gap",
        "wealth_fire_analytics",
        '"FI_Gap"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        False,
        "Remaining wealth gap between current net worth and Target FI Today.",
    ),
    _metric(
        "fire_success_probability",
        "Monte Carlo Success",
        "wealth_fire_analytics",
        '"Probability_Of_Success_Pct"',
        MetricAggregation.NONE,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Simulated trajectory probability of success (non-additive).",
    ),
    _metric(
        "tax_indicative_net_exposure",
        "Indicative Net Tax Exposure",
        "tax_financial_year_summary",
        '"Indicative_Net_Tax_Exposure"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "INR",
        ("FY",),
        True,
        "Indicative net tax exposure summed safely across selected FY grain.",
    ),
    # --- Household Income Composition & Resilience ---
    _metric(
        "income_active_total",
        "Active Income",
        "cashflow_efficiency_analytics",
        '"Active_Income"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Active salary and earned income summed safely across months.",
    ),
    _metric(
        "income_passive_total",
        "Passive Income",
        "cashflow_efficiency_analytics",
        '"Passive_Income"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Passive dividend and interest income summed safely across months.",
    ),
    _metric(
        "income_dividend_total",
        "Dividend Income",
        "cashflow_efficiency_analytics",
        '"Dividend_Income"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Dividend income summed safely across months.",
    ),
    _metric(
        "income_interest_total",
        "Interest Income",
        "cashflow_efficiency_analytics",
        '"Interest_Income"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Interest income summed safely across months.",
    ),
    _metric(
        "income_cash_total",
        "Cash Income",
        "cashflow_efficiency_analytics",
        '"Total_Cash_Income"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Cash-realized household income summed safely across months.",
    ),
    _metric(
        "income_non_cash_total",
        "Non-Cash Income",
        "cashflow_efficiency_analytics",
        '"Total_Non_Cash_Income"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Non-cash income (EPF contributions, accrued PF interest) summed across months.",
    ),
    _metric(
        "income_active_share",
        "Active Income Share",
        "cashflow_efficiency_analytics",
        '"Active_Income_Share_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Latest share of total income derived from active work (non-additive).",
    ),
    _metric(
        "income_passive_share",
        "Passive Income Share",
        "cashflow_efficiency_analytics",
        '"Passive_Income_Share_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Latest share of total income derived from passive sources (non-additive).",
    ),
    _metric(
        "income_trailing_3m_avg",
        "Trailing 3M Avg Income",
        "cashflow_efficiency_analytics",
        '"Trailing_3M_Avg_Income"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        False,
        "3-month rolling average monthly income (non-additive across months).",
    ),
    _metric(
        "income_cumulative_ytd",
        "Cumulative YTD Income",
        "cashflow_income_breakdown",
        '"Cumulative_YTD_Income"',
        MetricAggregation.NONE,
        MetricFormat.CURRENCY,
        "base_currency",
        ("YEAR_MONTH", "CATEGORY_NAME", "CATEGORY_GROUPS"),
        False,
        "Cumulative fiscal year-to-date income at category grain (non-additive across months).",
    ),
    # --- Household Expense Composition, Real Growth & Lifestyle ---
    _metric(
        "expense_core_total",
        "Core Expense",
        "cashflow_efficiency_analytics",
        '"Core_Expense"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Essential core household expenses summed safely across months.",
    ),
    _metric(
        "expense_non_core_total",
        "Non-Core Expense",
        "cashflow_efficiency_analytics",
        '"NonCore_Expense"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Discretionary and lifestyle expenses summed safely across months.",
    ),
    _metric(
        "expense_cash_total",
        "Cash Expense",
        "cashflow_efficiency_analytics",
        '"Total_Cash_Expense"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Cash and card-settled household expenses summed across months.",
    ),
    _metric(
        "expense_non_cash_total",
        "Non-Cash Expense",
        "cashflow_efficiency_analytics",
        '"Total_Non_Cash_Expense"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Non-cash amortizations and asset depreciations summed across months.",
    ),
    _metric(
        "expense_inflation_adjusted_core",
        "Inflation-Adjusted Core Expense",
        "core_monthly_fact",
        '"Inflation_Adjusted_Core_Expense"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        True,
        "Real inflation-adjusted core household expense from Gold core fact.",
    ),
    _metric(
        "expense_real_core_growth",
        "Real Core Expense Growth",
        "core_monthly_fact",
        '"Real_Core_Expense_Growth_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_DIMS,
        False,
        "Canonical real inflation-adjusted core expense growth rate (non-additive).",
    ),
    _metric(
        "expense_core_share",
        "Core Expense Share",
        "cashflow_efficiency_analytics",
        '"Core_Expense_Share_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Share of total expenses classified as Core essentials (non-additive).",
    ),
    _metric(
        "expense_non_core_share",
        "Non-Core Expense Share",
        "cashflow_efficiency_analytics",
        '"NonCore_Expense_Share_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Share of total expenses classified as Non-Core lifestyle (non-additive).",
    ),
    _metric(
        "expense_to_nw",
        "Expense / Net Worth Ratio",
        "cashflow_efficiency_analytics",
        '"Expense_to_NW_Ratio"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Monthly expense relative to total market net worth (non-additive).",
    ),
    _metric(
        "expense_trailing_3m_avg",
        "Trailing 3M Avg Expense",
        "cashflow_efficiency_analytics",
        '"Trailing_3M_Avg_Expense"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        False,
        "3-month rolling average monthly household spend (non-additive across months).",
    ),
    _metric(
        "expense_cumulative_ytd",
        "Cumulative YTD Expense",
        "cashflow_expense_breakdown",
        '"Cumulative_YTD_Spend"',
        MetricAggregation.NONE,
        MetricFormat.CURRENCY,
        "base_currency",
        ("YEAR_MONTH", "CATEGORY_NAME", "CATEGORY_GROUPS"),
        False,
        "Cumulative fiscal year-to-date expense at category grain (non-additive).",
    ),
    # --- Household Budget Variance (Additive Flows & Point-in-Time Targets) ---
    _metric(
        "budget_core_expense",
        "Budget Core Expense",
        "forecast_budget_variance",
        '"Budget_Core_Expense_Target"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Canonical monthly budget target for Core essential expenses.",
    ),
    _metric(
        "actual_core_expense",
        "Actual Core Expense",
        "forecast_budget_variance",
        '"Actual_Core_Expense"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Canonical actual Core essential expenses against budget.",
    ),
    _metric(
        "budget_non_core_expense",
        "Budget Non-Core Expense",
        "forecast_budget_variance",
        '"Budget_NonCore_Expense_Target"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Canonical monthly budget target for Non-Core discretionary expenses.",
    ),
    _metric(
        "actual_non_core_expense",
        "Actual Non-Core Expense",
        "forecast_budget_variance",
        '"Actual_NonCore_Expense"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Canonical actual Non-Core discretionary expenses against budget.",
    ),
    _metric(
        "budget_investment",
        "Budget Investment",
        "forecast_budget_variance",
        '"Budget_Investment_Target"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Canonical monthly budget target for household investment funding.",
    ),
    _metric(
        "budget_surplus_buffer",
        "Budget Surplus Buffer",
        "forecast_budget_variance",
        '"Budget_Surplus_Buffer"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Unallocated rule buffer available for discretionary spend or extra investment.",
    ),
    _metric(
        "actual_investment",
        "Actual Investment",
        "forecast_budget_variance",
        '"Actual_Investment"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Canonical actual household investment funding against budget.",
    ),
    _metric(
        "emergency_fund_gap",
        "Emergency Fund Gap",
        "forecast_budget_variance",
        '"Emergency_Fund_Gap"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        False,
        "Point-in-time shortfall/surplus vs emergency fund target (non-additive).",
    ),
    _metric(
        "emergency_fund_runway_months",
        "Zero-Income Runway",
        "forecast_budget_variance",
        '"Zero_Income_Runway_Months"',
        MetricAggregation.LATEST,
        MetricFormat.NUMBER,
        "months",
        _M_YM_DIMS,
        False,
        "Months of core expense coverage from liquid emergency funds (non-additive).",
    ),
    # --- Household Cashflow Activity (Additive Flows & Point-in-Time Balances) ---
    _metric(
        "opening_cash_balance",
        "Opening Cash Balance",
        "cashflow_activity_summary",
        '"Opening_Cash_Balance"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        False,
        "Opening liquid cash balance at start of month (non-additive).",
    ),
    _metric(
        "closing_cash_balance",
        "Closing Cash Balance",
        "cashflow_activity_summary",
        '"Closing_Cash_Balance"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        False,
        "Closing liquid cash balance at end of month (non-additive).",
    ),
    _metric(
        "net_operating_cashflow",
        "Net Operating Cashflow",
        "cashflow_activity_summary",
        '"Net_Cashflow_Operating"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Monthly operating cash inflows minus outflows.",
    ),
    _metric(
        "net_investing_cashflow",
        "Net Investing Cashflow",
        "cashflow_activity_summary",
        '"Net_Cashflow_Investing"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Monthly net cash deployed into or redeemed from investments.",
    ),
    _metric(
        "net_financing_cashflow",
        "Net Financing Cashflow",
        "cashflow_activity_summary",
        '"Net_Cashflow_Financing"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Monthly net financing inflows/repayments.",
    ),
    _metric(
        "net_internal_transfers",
        "Net Internal Transfers",
        "cashflow_activity_summary",
        '"Net_Internal_Transfers"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Net cash movement between internal cash-pool accounts (not income/expense).",
    ),
    _metric(
        "calculated_net_cashflow",
        "Calculated Net Cashflow",
        "cashflow_activity_summary",
        '"Calculated_Net_Cashflow"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Sum of operating, investing, financing, and transfer cashflows.",
    ),
    _metric(
        "unreconciled_difference",
        "Unreconciled Cashflow Difference",
        "cashflow_activity_summary",
        '"Unreconciled_Difference"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        True,
        "Residual difference between actual cash change and calculated net cashflow.",
    ),
    # --- Wealth Stock Balances & Attribution Flows ---
    _metric(
        "total_assets_book",
        "Total Assets (Book)",
        "core_monthly_fact",
        '"Total_Assets"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time gross book assets at month end (non-additive).",
    ),
    _metric(
        "liquid_assets_book",
        "Liquid Assets (Book)",
        "core_monthly_fact",
        '"Liquid_Assets"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time liquid book wealth balance (non-additive).",
    ),
    _metric(
        "foreign_exposure_amount",
        "Foreign Currency Exposure",
        "core_monthly_fact",
        '"Total_Foreign_Currency_Exposure"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time assets denominated in non-base currencies (non-additive).",
    ),
    _metric(
        "foreign_exposure_pct",
        "Foreign Exposure Share",
        "core_monthly_fact",
        '"Foreign_Exposure_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_DIMS,
        False,
        "Share of gross wealth in foreign currencies (non-additive).",
    ),
    _metric(
        "closing_unrealized_pl",
        "Closing Unrealized P&L",
        "core_monthly_fact",
        '"Closing_Unrealized_PL"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time cumulative unrealized gain/loss across portfolio (non-additive).",
    ),
    _metric(
        "closing_asset_pnl",
        "Closing Asset P&L",
        "core_monthly_fact",
        '"Closing_Asset_PnL"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time price/asset P&L component (non-additive).",
    ),
    _metric(
        "closing_forex_pnl",
        "Closing Forex P&L",
        "core_monthly_fact",
        '"Closing_Forex_PnL"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        False,
        "Point-in-time FX translation P&L component (non-additive).",
    ),
    _metric(
        "asset_delta",
        "Book Asset Delta",
        "core_monthly_fact",
        '"Asset_Delta"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        True,
        "Monthly change in book asset balance.",
    ),
    _metric(
        "asset_market_delta",
        "Market Asset Delta",
        "core_monthly_fact",
        '"Asset_Market_Delta"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        True,
        "Monthly change in mark-to-market asset balance.",
    ),
    _metric(
        "attribution_residual",
        "Net Worth Attribution Residual",
        "core_monthly_fact",
        '"Attribution_Residual"',
        MetricAggregation.SUM,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_DIMS,
        True,
        "Unallocated residual reconciling Asset_Market_Delta = Net_Savings_Flow + Market_Return_Flow + Residual.",
    ),
    _metric(
        "yoy_nw_growth_pct",
        "YoY Net Worth Growth",
        "cashflow_efficiency_analytics",
        '"YoY_Net_Worth_Growth_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Canonical nominal year-over-year net worth growth rate (non-additive).",
    ),
    _metric(
        "yoy_nw_growth_real_pct",
        "YoY Real Net Worth Growth",
        "cashflow_efficiency_analytics",
        '"YoY_Net_Worth_Growth_Pct_Real"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        _M_YM_DIMS,
        False,
        "Canonical inflation-adjusted year-over-year net worth growth rate (non-additive).",
    ),
    # --- Investment Portfolio Stock & Return Metrics (Non-Additive) ---
    _metric(
        "total_invested_value",
        "Total Invested Value",
        "investment_by_portfolio",
        '"Total_Invested_Value"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        ("Closing_Date",),
        False,
        "Point-in-time total invested cost basis across portfolio (non-additive).",
    ),
    _metric(
        "total_current_value",
        "Total Current Value",
        "investment_by_portfolio",
        '"Total_Current_Value"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        ("Closing_Date",),
        False,
        "Point-in-time total mark-to-market portfolio value (non-additive).",
    ),
    _metric(
        "portfolio_unrealized_pl",
        "Portfolio Unrealized P&L",
        "investment_by_portfolio",
        '"Unrealized_PL"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        ("Closing_Date",),
        False,
        "Point-in-time portfolio unrealized gain/loss (non-additive).",
    ),
    _metric(
        "portfolio_max_drawdown",
        "Portfolio Max Drawdown",
        "investment_by_portfolio",
        '"Max_Drawdown"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        ("Closing_Date",),
        False,
        "Maximum historical portfolio drawdown (non-additive).",
    ),
    _metric(
        "portfolio_cagr",
        "Portfolio CAGR",
        "investment_by_portfolio",
        '"CAGR"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        ("Closing_Date",),
        False,
        "Canonical compound annual growth rate (non-additive).",
    ),
    _metric(
        "portfolio_forex_contribution_pct",
        "Forex Contribution %",
        "investment_by_portfolio",
        '"Forex_Contribution_Pct"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        ("Closing_Date",),
        False,
        "Share of portfolio return attributable to FX translation (non-additive).",
    ),
    _metric(
        "portfolio_top1_weight",
        "Top 1 Holding Weight",
        "investment_by_portfolio",
        '"Top1_Holdings_Weight"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        ("Closing_Date",),
        False,
        "Portfolio weight of the largest single holding (non-additive).",
    ),
    _metric(
        "portfolio_top5_weight",
        "Top 5 Holdings Weight",
        "investment_by_portfolio",
        '"Top5_Holdings_Weight"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        ("Closing_Date",),
        False,
        "Combined portfolio weight of top 5 holdings (non-additive).",
    ),
    _metric(
        "portfolio_top10_weight",
        "Top 10 Holdings Weight",
        "investment_by_portfolio",
        '"Top10_Holdings_Weight"',
        MetricAggregation.LATEST,
        MetricFormat.PERCENT,
        "percent",
        ("Closing_Date",),
        False,
        "Combined portfolio weight of top 10 holdings (non-additive).",
    ),
    _metric(
        "fire_coast_today",
        "Coast FI Corpus",
        "wealth_fire_analytics",
        '"Coast_FI_Today"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        False,
        "Coast FI corpus required today under active rules (non-additive).",
    ),
    _metric(
        "fire_lean_today",
        "Lean FI Corpus",
        "wealth_fire_analytics",
        '"Lean_FI_Today"',
        MetricAggregation.LATEST,
        MetricFormat.CURRENCY,
        "base_currency",
        _M_YM_DIMS,
        False,
        "Lean FI corpus required today under active rules (non-additive).",
    ),
    _metric(
        "fire_months_p50",
        "Months to FI (P50)",
        "wealth_fire_analytics",
        '"Months_To_FI_Base_P50"',
        MetricAggregation.LATEST,
        MetricFormat.NUMBER,
        "months",
        _M_YM_DIMS,
        False,
        "Median Monte Carlo months remaining to reach FI (non-additive).",
    ),
    _metric(
        "fire_runway_p50",
        "Runway Months (P50)",
        "wealth_fire_analytics",
        '"Runway_Months_Base_P50"',
        MetricAggregation.LATEST,
        MetricFormat.NUMBER,
        "months",
        _M_YM_DIMS,
        False,
        "Median Monte Carlo portfolio runway in months (non-additive).",
    ),
)

DEFAULT_METRICS = MetricRegistry(DEFAULT_DATASETS, FOUNDATIONAL_METRICS)
