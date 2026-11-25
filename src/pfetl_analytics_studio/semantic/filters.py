"""Whitelisted filter contracts, normalization, UI chips, and bound SQL compilation.

Owns
----
- `FilterField`, `FilterOperator`, `FilterSpec`, `FilterSet`, and `FilterChip` immutable models.
- Stable canonical cache key serialization for filter sets.
- Parameterized SQL `WHERE` clause compilation restricted strictly to whitelisted columns.

Does not own
------------
- Arbitrary user SQL generation or database connections.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from pfetl_analytics_studio.semantic.periods import CalendarQueryPort

FilterScalar = str | int | float | bool | date

_COLUMN_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class FilterField(StrEnum):
    """Approved domain filter vocabulary from README §11."""

    # Household / Wealth
    ASSET_GROUP = "asset_group"
    ASSET_ID = "asset_id"
    INCOME_CATEGORY = "income_category"
    INCOME_SUBCATEGORY = "income_subcategory"
    INCOME_GROUP = "income_group"
    EXPENSE_CATEGORY = "expense_category"
    EXPENSE_SUBCATEGORY = "expense_subcategory"
    EXPENSE_GROUP = "expense_group"
    IS_CORE_EXPENSE = "is_core_expense"
    IS_CASH_POOL = "is_cash_pool"
    IS_ACTIVE_INCOME = "is_active_income"
    IS_PASSIVE_INCOME = "is_passive_income"
    CASHFLOW_ACTIVITY_TYPE = "cashflow_activity_type"
    IS_LIQUID = "is_liquid"

    # Investments
    ISIN = "isin"
    INSTRUMENT_NAME = "instrument_name"
    INSTRUMENT_CLASS = "instrument_class"
    INSTRUMENT_TYPE = "instrument_type"
    INSTRUMENT_SUBTYPE = "instrument_subtype"
    SECTOR = "sector"
    INDUSTRY = "industry"
    COUNTRY = "country"
    GEO = "geo"
    GEO_SUBTYPE = "geo_subtype"
    CURRENCY_ID = "currency_id"
    BENCHMARK_ID = "benchmark_id"
    TAX_TYPE = "tax_type"
    TAX_SUBTYPE = "tax_subtype"

    # Tax
    FY = "fy"
    INCOME_HEAD = "income_head"
    TAX_SUB_HEAD = "tax_sub_head"
    SOURCE_TYPE = "source_type"
    TAX_STATUS = "tax_status"
    LOT_ID = "lot_id"

    # Operations
    RUN_ID = "run_id"
    STATUS = "status"
    COMMIT_STATE = "commit_state"
    APPLICATION_VERSION = "application_version"
    FILE_CATEGORY = "file_category"
    SYNC_STATUS = "sync_status"
    EVENT_TYPE = "event_type"
    PROVIDER = "provider"
    REQUEST_STATUS = "request_status"
    FAILURE_DOMAIN = "failure_domain"
    STAGE = "stage"
    ERROR_TYPE = "error_type"
    SEVERITY = "severity"
    COMPONENT = "component"
    OPERATION = "operation"
    EVENT_CODE = "event_code"
    ENTITY_ID = "entity_id"
    CACHE_HIT = "cache_hit"
    WORKER_ID = "worker_id"
    BATCH_ID = "batch_id"


class FilterOperator(StrEnum):
    """Supported typed filter comparison operators."""

    EQ = "eq"
    IN = "in"
    BETWEEN = "between"
    GTE = "gte"
    LTE = "lte"
    CONTAINS = "contains"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


@dataclass(frozen=True)
class FilterChip:
    """Presentation chip descriptor for displaying active semantic filters."""

    field: FilterField
    label: str
    value_display: str


def _scalar_sort_key(val: FilterScalar) -> tuple[str, str]:
    """Produce stable deterministic sort key across mixed scalar types."""
    return (type(val).__name__, str(val))


def _format_scalar(val: FilterScalar) -> str:
    """Format a scalar filter value cleanly for labels and cache keys."""
    if isinstance(val, date):
        return val.isoformat()
    return str(val)


class FilterSpec(BaseModel):
    """Immutable single-field predicate over a whitelisted semantic dimension."""

    model_config = ConfigDict(frozen=True)

    field: FilterField
    operator: FilterOperator = FilterOperator.EQ
    value: FilterScalar | tuple[FilterScalar, ...] | None = None
    label: str | None = None

    @model_validator(mode="after")
    def _validate_and_normalize(self) -> FilterSpec:
        """Validate value arity and normalize `IN` tuples deterministically."""
        if self.operator in (FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL):
            if self.value is not None:
                raise ValueError(
                    f"Operator '{self.operator.value}' does not accept a filter value."
                )
            return self

        if self.value is None:
            raise ValueError(f"Operator '{self.operator.value}' requires a non-None value.")

        if self.operator == FilterOperator.IN:
            raw_items = self.value if isinstance(self.value, tuple) else (self.value,)
            if not raw_items:
                raise ValueError("Operator 'in' requires at least one scalar value.")
            deduped = tuple(sorted(set(raw_items), key=_scalar_sort_key))
            object.__setattr__(self, "value", deduped)
            return self

        if self.operator == FilterOperator.BETWEEN:
            if not isinstance(self.value, tuple) or len(self.value) != 2:
                raise ValueError("Operator 'between' requires a 2-tuple (low, high).")
            low = self.value[0]
            high = self.value[1]
            if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                if low > high:
                    raise ValueError("Numeric range lower bound cannot exceed upper bound.")
            elif type(low) is type(high) and _format_scalar(low) > _format_scalar(high):
                raise ValueError("Range lower bound cannot exceed upper bound.")
            return self

        if isinstance(self.value, tuple):
            raise ValueError(f"Operator '{self.operator.value}' expects a scalar, not a sequence.")
        return self

    def display_chip(self) -> FilterChip:
        """Build a human-friendly `FilterChip` for UI filter ribbons."""
        display_field = self.label or self.field.value.replace("_", " ").title()
        if self.operator == FilterOperator.IS_NULL:
            val_text = "is empty"
        elif self.operator == FilterOperator.IS_NOT_NULL:
            val_text = "is set"
        elif self.operator == FilterOperator.BETWEEN and isinstance(self.value, tuple):
            val_text = f"{_format_scalar(self.value[0])} .. {_format_scalar(self.value[1])}"
        elif isinstance(self.value, tuple):
            val_text = ", ".join(_format_scalar(item) for item in self.value)
        elif self.value is not None:
            op_prefix = {
                FilterOperator.EQ: "",
                FilterOperator.GTE: ">= ",
                FilterOperator.LTE: "<= ",
                FilterOperator.CONTAINS: "~ ",
            }.get(self.operator, "")
            val_text = f"{op_prefix}{_format_scalar(self.value)}"
        else:
            val_text = ""

        return FilterChip(field=self.field, label=display_field, value_display=val_text)

    def canonical_token(self) -> str:
        """Serialize filter predicate deterministically for cache keys."""
        if isinstance(self.value, tuple):
            v_str = "[" + ",".join(_format_scalar(item) for item in self.value) + "]"
        elif self.value is None:
            v_str = "null"
        else:
            v_str = _format_scalar(self.value)
        return f"{self.field.value}:{self.operator.value}:{v_str}"


class FilterSet(BaseModel):
    """Canonical normalized immutable collection of `FilterSpec` predicates."""

    model_config = ConfigDict(frozen=True)

    filters: tuple[FilterSpec, ...] = ()

    @field_validator("filters", mode="after")
    @classmethod
    def _sort_filters(cls, value: tuple[FilterSpec, ...]) -> tuple[FilterSpec, ...]:
        """Sort filters canonically so permutation never breaks cache keys."""
        return tuple(sorted(value, key=lambda f: f.canonical_token()))

    def with_filter(self, spec: FilterSpec) -> FilterSet:
        """Return a new `FilterSet` appending/updating a predicate."""
        return FilterSet(filters=(*self.filters, spec))

    def without_field(self, field: FilterField) -> FilterSet:
        """Return a new `FilterSet` excluding all predicates on `field`."""
        remaining = tuple(f for f in self.filters if f.field != field)
        return FilterSet(filters=remaining)

    def validate_for(self, allowed_fields: Iterable[FilterField]) -> None:
        """Ensure all active filters belong to an approved dataset whitelist.

        Parameters
        ----------
        allowed_fields : Iterable[FilterField]
            Allowed `FilterField` set for the target query or dataset.

        Raises
        ------
        ValueError
            If any active filter targets a disallowed field.
        """
        allowed_set = set(allowed_fields)
        disallowed = [f.field.value for f in self.filters if f.field not in allowed_set]
        if disallowed:
            raise ValueError(
                f"Disallowed filter field(s) for target dataset: {', '.join(sorted(disallowed))}."
            )

    def to_chips(self) -> list[FilterChip]:
        """Convert active predicates into presentation `FilterChip` objects."""
        return [f.display_chip() for f in self.filters]

    def cache_key(self) -> str:
        """Return stable serialized filter fingerprint."""
        if not self.filters:
            return "filters:none"
        return ";".join(f.canonical_token() for f in self.filters)


def _quote_column_reference(col_ref: str) -> str:
    """Safely quote a validated `col` or `alias.col` SQL column expression."""
    parts = col_ref.split(".")
    if len(parts) == 1:
        col = parts[0]
        if not _COLUMN_IDENTIFIER_RE.match(col):
            raise ValueError(f"Unsafe SQL column reference rejected: {col_ref!r}")
        return f'"{col}"'
    if len(parts) == 2:
        alias, col = parts
        if not _COLUMN_IDENTIFIER_RE.match(alias) or not _COLUMN_IDENTIFIER_RE.match(col):
            raise ValueError(f"Unsafe SQL column reference rejected: {col_ref!r}")
        return f'"{alias}"."{col}"'
    raise ValueError(f"Unsupported SQL column reference format: {col_ref!r}")


def compile_filter_clause(
    filter_set: FilterSet,
    column_map: Mapping[FilterField, str],
) -> tuple[str, list[Any]]:
    """Compile a `FilterSet` into a parameterized SQL predicate and parameter list.

    Parameters
    ----------
    filter_set : FilterSet
        Normalized active filters to compile.
    column_map : Mapping[FilterField, str]
        Whitelisted mapping from `FilterField` to target table column names.

    Returns
    -------
    tuple[str, list[Any]]
        Tuple of `(sql_where_fragment_without_WHERE, bound_params)`. Returns
        `("1 = 1", [])` when no filters are active.
    """
    if not filter_set.filters:
        return "1 = 1", []

    filter_set.validate_for(column_map.keys())
    clauses: list[str] = []
    params: list[Any] = []

    for spec in filter_set.filters:
        quoted_col = _quote_column_reference(column_map[spec.field])
        if spec.operator == FilterOperator.IS_NULL:
            clauses.append(f"{quoted_col} IS NULL")
        elif spec.operator == FilterOperator.IS_NOT_NULL:
            clauses.append(f"{quoted_col} IS NOT NULL")
        elif spec.operator == FilterOperator.EQ:
            clauses.append(f"{quoted_col} = ?")
            params.append(spec.value)
        elif spec.operator == FilterOperator.GTE:
            clauses.append(f"{quoted_col} >= ?")
            params.append(spec.value)
        elif spec.operator == FilterOperator.LTE:
            clauses.append(f"{quoted_col} <= ?")
            params.append(spec.value)
        elif spec.operator == FilterOperator.CONTAINS:
            clauses.append(f"LOWER(CAST({quoted_col} AS VARCHAR)) LIKE ?")
            params.append(f"%{str(spec.value).lower()}%")
        elif spec.operator == FilterOperator.BETWEEN and isinstance(spec.value, tuple):
            clauses.append(f"{quoted_col} BETWEEN ? AND ?")
            params.extend([spec.value[0], spec.value[1]])
        elif spec.operator == FilterOperator.IN and isinstance(spec.value, tuple):
            placeholders = ", ".join("?" for _ in spec.value)
            clauses.append(f"{quoted_col} IN ({placeholders})")
            params.extend(spec.value)

    return " AND ".join(clauses), params


class FilterCatalogOptions(BaseModel):
    """Distinct filterable domain values discovered read-only from PFETL Gold/Silver tables."""

    model_config = ConfigDict(frozen=True)

    values_by_field: dict[FilterField, tuple[str, ...]] = {}

    def options_for(self, field: FilterField) -> tuple[str, ...]:
        """Return discovered distinct values for a `FilterField`, or empty tuple."""
        return self.values_by_field.get(field, ())


_FILTER_SOURCE_QUERIES: tuple[tuple[FilterField, str, str, str, bool], ...] = (
    (FilterField.FY, "silver", "dim_calendar", "Financial_Year", True),
    (FilterField.INCOME_GROUP, "gold", "cashflow_income_breakdown", "CATEGORY_GROUPS", False),
    (FilterField.INCOME_CATEGORY, "gold", "cashflow_income_breakdown", "CATEGORY_NAME", False),
    (FilterField.EXPENSE_GROUP, "gold", "cashflow_expense_breakdown", "CATEGORY_GROUPS", False),
    (FilterField.EXPENSE_CATEGORY, "gold", "cashflow_expense_breakdown", "CATEGORY_NAME", False),
    (FilterField.INSTRUMENT_CLASS, "gold", "investment_by_class", "INSTRUMENT_CLASS", False),
    (FilterField.SECTOR, "gold", "investment_by_sector", "SECTOR", False),
    (FilterField.CURRENCY_ID, "gold", "investment_by_currency", "CURRENCY_ID", False),
)


class FilterCatalogResolver:
    """Queries safe distinct filter option lists from existing PFETL warehouse surfaces."""

    def __init__(self, query_port: CalendarQueryPort) -> None:
        """Initialize filter catalog resolver bound to a read-only SQL port.

        Parameters
        ----------
        query_port : CalendarQueryPort
            Read-only query adapter supporting `table_exists`, `fetch_one`, and `fetch_all`.
        """
        self.query_port = query_port

    def _column_exists(self, schema: str, table: str, column: str) -> bool:
        """Verify table & column existence cleanly before querying distinct values."""
        if not self.query_port.table_exists(schema, table):
            return False
        hit = self.query_port.fetch_one(
            """
            SELECT 1 AS ok
            FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ? AND column_name = ?
            LIMIT 1;
            """,
            (schema, table, column),
        )
        return hit is not None

    def list_available_options(self) -> FilterCatalogOptions:
        """Enumerate sorted distinct values per whitelisted `FilterField` safely."""
        values: dict[FilterField, tuple[str, ...]] = {
            FilterField.IS_CORE_EXPENSE: ("true", "false"),
            FilterField.IS_ACTIVE_INCOME: ("true", "false"),
            FilterField.IS_PASSIVE_INCOME: ("true", "false"),
        }
        for field, schema, table, col, desc in _FILTER_SOURCE_QUERIES:
            if not self._column_exists(schema, table, col):
                continue
            order = "DESC" if desc else "ASC"
            rows = self.query_port.fetch_all(
                f"""
                SELECT DISTINCT CAST("{col}" AS VARCHAR) AS val
                FROM "{schema}"."{table}"
                WHERE "{col}" IS NOT NULL AND TRIM(CAST("{col}" AS VARCHAR)) <> ''
                ORDER BY val {order}
                LIMIT 200;
                """
            )
            extracted = tuple(str(r["val"]) for r in rows if r.get("val") is not None)
            if extracted:
                values[field] = extracted
        return FilterCatalogOptions(values_by_field=values)
