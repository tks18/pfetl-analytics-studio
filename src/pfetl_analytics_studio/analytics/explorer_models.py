"""Typed contracts for Data Catalog and Guided Explorer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pfetl_analytics_studio.data import (
    CatalogEntry,
    ColumnInfo,
    CompatibilityStatus,
    SurfaceLayer,
    SurfaceUsageCategory,
)
from pfetl_analytics_studio.semantic import (
    DatasetSpec,
    FilterOperator,
    FilterScalar,
    FilterSet,
    MetricAggregation,
    MetricSpec,
    PeriodContext,
    PeriodRequest,
)
from pfetl_analytics_studio.state import SqlDatasource


class CatalogFilter(BaseModel):
    """Filter criteria for browsing the 82-surface PFETL Data Catalog."""

    model_config = ConfigDict(frozen=True)

    layer: SurfaceLayer | None = None
    domain: str | None = None
    usage: SurfaceUsageCategory | None = None
    search: str = ""
    only_available: bool = False


class CatalogCoverageSummary(BaseModel):
    """Coverage and compatibility telemetry across all 82 upstream PFETL surfaces."""

    model_config = ConfigDict(frozen=True)

    total_expected: int
    total_discovered: int
    layer_counts: dict[SurfaceLayer, int]
    usage_counts: dict[SurfaceUsageCategory, int]
    compatibility_status: CompatibilityStatus
    missing_surfaces: tuple[str, ...] = ()
    unexpected_surfaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogBrowseResult:
    """Filtered Data Catalog entries alongside global 82-surface coverage telemetry."""

    entries: tuple[CatalogEntry, ...]
    coverage: CatalogCoverageSummary
    available_domains: tuple[str, ...]


class SurfaceColumnFilter(BaseModel):
    """Typed column-level filter for Data Catalog surface preview and full CSV export."""

    model_config = ConfigDict(frozen=True)

    column: str
    operator: FilterOperator = FilterOperator.EQ
    value: FilterScalar | None = None

    @field_validator("column")
    @classmethod
    def _clean_column(cls, val: str) -> str:
        clean = val.strip()
        if not clean:
            raise ValueError("SurfaceColumnFilter column cannot be empty.")
        return clean


@dataclass(frozen=True)
class TablePreviewResult:
    """Bounded read-only preview of any classified PFETL surface."""

    physical_name: str
    layer: SurfaceLayer | None
    domain: str
    grain: str
    producer: str
    usage: SurfaceUsageCategory | None
    purpose: str
    columns: tuple[ColumnInfo, ...]
    rows: tuple[dict[str, Any], ...]
    total_row_count: int | None
    limit_applied: int
    truncated: bool
    filtered_row_count: int | None = None
    is_bronze_warning: bool = False
    is_unclassified_blocked: bool = False
    warning_message: str | None = None


class GuidedSortSpec(BaseModel):
    """Whitelisted sort clause targeting a selected dimension or metric ID."""

    model_config = ConfigDict(frozen=True)

    field: str
    descending: bool = True

    @field_validator("field")
    @classmethod
    def _non_empty_field(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Sort field cannot be empty.")
        return clean


class ChartRecommendation(StrEnum):
    """Deterministic visualization modes for Guided Explorer results."""

    TABLE = "table"
    TIME_SERIES = "time_series"
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"
    STACKED_BAR = "stacked_bar"
    GROUPED_BAR = "grouped_bar"


class ChartSuggestion(BaseModel):
    """Rule-based chart recommendation derived from GuidedQuery shape."""

    model_config = ConfigDict(frozen=True)

    chart_type: ChartRecommendation
    reason: str
    x_field: str | None = None
    y_fields: tuple[str, ...] = ()
    series_field: str | None = None


class GuidedQuery(BaseModel):
    """Validated analytical query over registered semantic datasets and metrics."""

    model_config = ConfigDict(frozen=True)

    dataset_id: str
    dimensions: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    metric_aggregations: dict[str, MetricAggregation] = Field(default_factory=dict)
    filters: FilterSet = Field(default_factory=FilterSet)
    period: PeriodRequest | None = None
    sort: tuple[GuidedSortSpec, ...] = ()
    limit: int = 500

    @field_validator("dataset_id")
    @classmethod
    def _clean_dataset_id(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("GuidedQuery dataset_id cannot be empty.")
        return clean

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value: int) -> int:
        if value < 1:
            return 1
        if value > 10_000:
            return 10_000
        return value


@dataclass(frozen=True)
class GuidedQueryResult:
    """Executed Guided Explorer result with semantic summary and chart suggestion."""

    query: GuidedQuery
    dataset: DatasetSpec
    metric_specs: tuple[MetricSpec, ...]
    resolved_period: PeriodContext | None
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_count: int
    truncated: bool
    semantic_summary: str
    chart_suggestion: ChartSuggestion
    warnings: tuple[str, ...] = ()


class SqlQueryRequest(BaseModel):
    """Guarded read-only SQL query request targeting DuckDB or Control Plane."""

    model_config = ConfigDict(frozen=True)

    sql: str
    datasource: SqlDatasource = SqlDatasource.DUCKDB
    limit: int = 500

    @field_validator("sql")
    @classmethod
    def _non_empty_sql(cls, val: str) -> str:
        clean = val.strip()
        if not clean:
            raise ValueError("SQL query cannot be empty.")
        return clean

    @field_validator("limit")
    @classmethod
    def _clamp_sql_limit(cls, val: int) -> int:
        return max(1, min(val, 10_000))


@dataclass(frozen=True)
class SqlQueryResult:
    """Bounded result from guarded read-only SQL execution."""

    sql: str
    normalized_sql: str
    datasource: SqlDatasource
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_count: int
    truncated: bool
    limit_applied: int
    duration_ms: float
    warnings: tuple[str, ...] = ()
