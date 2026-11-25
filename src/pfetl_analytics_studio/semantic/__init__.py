"""Semantic package for PFETL Analytics Studio.

Ownership: Presentation metadata for periods, filters, metrics, and dataset schemas.
Forbidden: Does not compute financial methodology or open external databases.
"""

from __future__ import annotations

from pfetl_analytics_studio.semantic.datasets import (
    DEFAULT_DATASETS,
    FOUNDATIONAL_DATASETS,
    DatasetRegistry,
    DatasetSpec,
)
from pfetl_analytics_studio.semantic.filters import (
    FilterCatalogOptions,
    FilterCatalogResolver,
    FilterChip,
    FilterField,
    FilterOperator,
    FilterScalar,
    FilterSet,
    FilterSpec,
    compile_filter_clause,
)
from pfetl_analytics_studio.semantic.metrics import (
    DEFAULT_METRICS,
    FOUNDATIONAL_METRICS,
    MetricAggregation,
    MetricFormat,
    MetricOrigin,
    MetricRegistry,
    MetricSpec,
)
from pfetl_analytics_studio.semantic.periods import (
    PRESET_LABELS,
    CalendarQueryPort,
    ComparisonMode,
    ComparisonPeriod,
    PeriodCatalogOptions,
    PeriodContext,
    PeriodPreset,
    PeriodRequest,
    PeriodResolver,
    format_comparison_label,
    format_period_label,
    preset_display_name,
)

__all__ = [
    "DEFAULT_DATASETS",
    "DEFAULT_METRICS",
    "FOUNDATIONAL_DATASETS",
    "FOUNDATIONAL_METRICS",
    "PRESET_LABELS",
    "CalendarQueryPort",
    "ComparisonMode",
    "ComparisonPeriod",
    "DatasetRegistry",
    "DatasetSpec",
    "FilterCatalogOptions",
    "FilterCatalogResolver",
    "FilterChip",
    "FilterField",
    "FilterOperator",
    "FilterScalar",
    "FilterSet",
    "FilterSpec",
    "MetricAggregation",
    "MetricFormat",
    "MetricOrigin",
    "MetricRegistry",
    "MetricSpec",
    "PeriodCatalogOptions",
    "PeriodContext",
    "PeriodPreset",
    "PeriodRequest",
    "PeriodResolver",
    "compile_filter_clause",
    "format_comparison_label",
    "format_period_label",
    "preset_display_name",
]
