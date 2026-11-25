"""Semantic dataset registry for approved analytical surfaces.

Owns
----
- `DatasetSpec` frozen metadata defining safe grain, date column, dimensions, and filter mappings.
- `DatasetRegistry` and `DEFAULT_DATASETS` covering foundational Overview and domain datasets.

Does not own
------------
- Physical table schema discovery (owned by `data/catalog.py`).
- Metric definitions (owned by `semantic/metrics.py`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.semantic.filters import FilterField


class DatasetSpec(BaseModel):
    """Semantic metadata for an analytical dataset safe for Guided Explorer & services."""

    model_config = ConfigDict(frozen=True)

    id: str
    physical_table: str
    label: str
    domain: str
    grain: str
    date_field: str
    default_dimensions: tuple[str, ...] = ()
    allowed_dimensions: tuple[str, ...] = ()
    allowed_filters: tuple[FilterField, ...] = ()
    description: str = ""


class DatasetRegistry:
    """Registry managing validated semantic dataset descriptors."""

    def __init__(self, initial: Iterable[DatasetSpec] = ()) -> None:
        """Initialize dataset registry optionally populated with initial specs.

        Parameters
        ----------
        initial : Iterable[DatasetSpec], optional
            Initial dataset descriptors to register cleanly.
        """
        self._datasets: dict[str, DatasetSpec] = {}
        for spec in initial:
            self.register(spec)

    def register(self, spec: DatasetSpec) -> None:
        """Register a new dataset descriptor, rejecting duplicate IDs.

        Parameters
        ----------
        spec : DatasetSpec
            Dataset descriptor to add.

        Raises
        ------
        ValueError
            If `spec.id` is empty or already registered.
        """
        if not spec.id.strip():
            raise ValueError("DatasetSpec id cannot be empty.")
        if spec.id in self._datasets:
            raise ValueError(f"Duplicate dataset ID registered: '{spec.id}'.")
        self._datasets[spec.id] = spec

    def get(self, dataset_id: str) -> DatasetSpec:
        """Retrieve a registered dataset by ID.

        Parameters
        ----------
        dataset_id : str
            Registered dataset identifier.

        Returns
        -------
        DatasetSpec
            Registered dataset specification.

        Raises
        ------
        KeyError
            If `dataset_id` is not registered.
        """
        if dataset_id not in self._datasets:
            raise KeyError(f"Unknown semantic dataset ID: '{dataset_id}'.")
        return self._datasets[dataset_id]

    def has(self, dataset_id: str) -> bool:
        """Check whether `dataset_id` exists in the registry."""
        return dataset_id in self._datasets

    def list_datasets(self, domain: str | None = None) -> list[DatasetSpec]:
        """Return registered datasets, optionally filtered by analytical domain."""
        specs = list(self._datasets.values())
        if domain is not None:
            target = domain.strip().lower()
            specs = [s for s in specs if s.domain.lower() == target]
        return sorted(specs, key=lambda s: s.id)

    def validate_dimensions(self, dataset_id: str, dimensions: Sequence[str]) -> None:
        """Ensure all requested grouping dimensions are approved for `dataset_id`."""
        ds = self.get(dataset_id)
        allowed = set(ds.allowed_dimensions)
        invalid = [d for d in dimensions if d not in allowed]
        if invalid:
            raise ValueError(
                f"Disallowed dimension(s) for dataset '{dataset_id}': {', '.join(invalid)}."
            )


FOUNDATIONAL_DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        id="core_monthly_fact",
        physical_table="gold.core_monthly_fact",
        label="Monthly Financial Cockpit",
        domain="Overview / Wealth",
        grain="Month",
        date_field="MONTH_END_DATE",
        default_dimensions=("MONTH_START_DATE", "MONTH_END_DATE"),
        allowed_dimensions=("MONTH_START_DATE", "MONTH_END_DATE"),
        allowed_filters=(),
        description="Canonical monthly household cashflow, assets, net worth, and attribution.",
    ),
    DatasetSpec(
        id="cashflow_efficiency_analytics",
        physical_table="gold.cashflow_efficiency_analytics",
        label="Cashflow Efficiency Analytics",
        domain="Overview / Household",
        grain="Month",
        date_field="MONTH_END_DATE",
        default_dimensions=("YEAR_MONTH",),
        allowed_dimensions=("MONTH_START_DATE", "MONTH_END_DATE", "YEAR_MONTH"),
        allowed_filters=(),
        description="Monthly savings/investment rates, passive coverage, and active dependency.",
    ),
    DatasetSpec(
        id="cashflow_income_breakdown",
        physical_table="gold.cashflow_income_breakdown",
        label="Income Category Breakdown",
        domain="Household",
        grain="Month-IncomeCategory",
        date_field="MONTH_END_DATE",
        default_dimensions=("YEAR_MONTH", "CATEGORY_NAME"),
        allowed_dimensions=(
            "MONTH_START_DATE",
            "MONTH_END_DATE",
            "YEAR_MONTH",
            "CATEGORY_ID",
            "CATEGORY_NAME",
            "CATEGORY_GROUPS",
            "Is_Active_Income",
            "Is_Passive_Income",
            "Is_Dividend_Income",
            "Is_Interest_Income",
            "Is_Non_Cash_Income",
        ),
        allowed_filters=(
            FilterField.INCOME_CATEGORY,
            FilterField.INCOME_GROUP,
            FilterField.IS_ACTIVE_INCOME,
            FilterField.IS_PASSIVE_INCOME,
        ),
        description="Monthly income composition across categories, groups, and active/passive flags.",
    ),
    DatasetSpec(
        id="cashflow_expense_breakdown",
        physical_table="gold.cashflow_expense_breakdown",
        label="Expense Category Breakdown",
        domain="Household",
        grain="Month-ExpenseCategory",
        date_field="MONTH_END_DATE",
        default_dimensions=("YEAR_MONTH", "CATEGORY_NAME"),
        allowed_dimensions=(
            "MONTH_START_DATE",
            "MONTH_END_DATE",
            "YEAR_MONTH",
            "CATEGORY_ID",
            "CATEGORY_NAME",
            "CATEGORY_GROUPS",
            "Spend_Type",
            "Is_Core_Expense",
            "Is_Investment",
            "Is_Discretionary",
        ),
        allowed_filters=(
            FilterField.EXPENSE_CATEGORY,
            FilterField.EXPENSE_GROUP,
            FilterField.IS_CORE_EXPENSE,
        ),
        description="Monthly expense composition with core, discretionary, and investment splits.",
    ),
    DatasetSpec(
        id="wealth_asset_breakdown",
        physical_table="gold.wealth_asset_breakdown",
        label="Monthly Asset & Wealth Breakdown",
        domain="Wealth",
        grain="Month-Asset",
        date_field="MONTH_END_DATE",
        default_dimensions=("MONTH_END_DATE", "ASSET_SUBCATEGORY_ID"),
        allowed_dimensions=("MONTH_START_DATE", "MONTH_END_DATE", "ASSET_SUBCATEGORY_ID"),
        allowed_filters=(FilterField.ASSET_ID, FilterField.IS_LIQUID, FilterField.IS_CASH_POOL),
        description="Asset-level monthly balances, liquidity, drawdown, flows, and runway.",
    ),
    DatasetSpec(
        id="wealth_fire_analytics",
        physical_table="gold.wealth_fire_analytics",
        label="FIRE & Trajectory Analytics",
        domain="FIRE",
        grain="Month",
        date_field="MONTH_END_DATE",
        default_dimensions=("YEAR_MONTH",),
        allowed_dimensions=("MONTH_START_DATE", "MONTH_END_DATE", "YEAR_MONTH"),
        allowed_filters=(),
        description="Financial independence targets, coverage, P50 date, and Monte Carlo success.",
    ),
    DatasetSpec(
        id="investment_by_portfolio",
        physical_table="gold.investment_by_portfolio",
        label="Portfolio Return & Concentration",
        domain="Investments",
        grain="Date",
        date_field="Closing_Date",
        default_dimensions=("Closing_Date",),
        allowed_dimensions=("Closing_Date",),
        allowed_filters=(),
        description="Portfolio-level value, XIRR, active return, concentration, and LTCG ladder.",
    ),
    DatasetSpec(
        id="investment_portfolio_summary",
        physical_table="gold.investment_portfolio_summary",
        label="Monthly Portfolio ISIN Summary",
        domain="Investments",
        grain="Month-ISIN",
        date_field="Max_Closing_Date",
        default_dimensions=("MONTH_START_DATE", "ISIN", "INSTRUMENT_NAME"),
        allowed_dimensions=(
            "MONTH_START_DATE",
            "Max_Closing_Date",
            "ISIN",
            "CURRENCY_ID",
            "INSTRUMENT_NAME",
            "INSTRUMENT_CLASS",
            "INSTRUMENT_TYPE",
            "INSTRUMENT_SUBTYPE",
            "SECTOR",
        ),
        allowed_filters=(
            FilterField.ISIN,
            FilterField.INSTRUMENT_CLASS,
            FilterField.INSTRUMENT_TYPE,
            FilterField.INSTRUMENT_SUBTYPE,
            FilterField.SECTOR,
            FilterField.CURRENCY_ID,
        ),
        description="Monthly ISIN-level market/book valuation, weights, drift, and harvest priority.",
    ),
    DatasetSpec(
        id="tax_financial_year_summary",
        physical_table="gold.tax_financial_year_summary",
        label="Tax Financial Year Summary",
        domain="Tax",
        grain="FY",
        date_field="FY",
        default_dimensions=("FY",),
        allowed_dimensions=("FY",),
        allowed_filters=(FilterField.FY,),
        description="Annual tax cockpit combining ledger income, investment gains, and exposure.",
    ),
    DatasetSpec(
        id="silver_fact_income_transactions",
        physical_table="silver.fact_income_transactions",
        label="Income Transaction Ledger (Silver Drill)",
        domain="Household",
        grain="Transaction",
        date_field="DATE",
        default_dimensions=("DATE", "CATEGORY_ID", "ASSET_ID"),
        allowed_dimensions=(
            "DATE",
            "CATEGORY_ID",
            "ASSET_ID",
            "CURRENCY_ID",
            "Is_Active_Income",
            "Is_Dividend_Income",
            "Is_Interest_Income",
            "Is_Non_Cash_Income",
        ),
        allowed_filters=(
            FilterField.INCOME_GROUP,
            FilterField.INCOME_CATEGORY,
            FilterField.INCOME_SUBCATEGORY,
            FilterField.IS_ACTIVE_INCOME,
            FilterField.IS_PASSIVE_INCOME,
            FilterField.ASSET_ID,
        ),
        description="Canonical Silver income transactions used strictly for category/group drill.",
    ),
    DatasetSpec(
        id="silver_fact_expense_transactions",
        physical_table="silver.fact_expense_transactions",
        label="Expense Transaction Ledger (Silver Drill)",
        domain="Household",
        grain="Transaction",
        date_field="DATE",
        default_dimensions=("DATE", "CATEGORY_ID", "ASSET_ID"),
        allowed_dimensions=(
            "DATE",
            "CATEGORY_ID",
            "ASSET_ID",
            "CURRENCY_ID",
            "Is_Core_Expense",
        ),
        allowed_filters=(
            FilterField.EXPENSE_GROUP,
            FilterField.EXPENSE_CATEGORY,
            FilterField.EXPENSE_SUBCATEGORY,
            FilterField.IS_CORE_EXPENSE,
            FilterField.ASSET_ID,
        ),
        description="Canonical Silver expense transactions used strictly for category/group drill.",
    ),
)

DEFAULT_DATASETS = DatasetRegistry(FOUNDATIONAL_DATASETS)
