"""Pure catalog filtering, bounded preview routing, and GuidedQuery SQL compiler.

Owns
----
- `filter_catalog_entries()` and `build_catalog_coverage()` over 82-surface catalog.
- `fetch_surface_preview()` enforcing classification, Bronze warnings, and bounded limits.
- `compile_guided_query()` compiling strictly whitelisted dimensions, metrics, filters, and bounds.
- `recommend_chart()` deterministic chart mapping without auto-analytics magic.

Does not own
------------
- NiceGUI widgets or `QueryCache` lifecycle (owned by `analytics/explorer.py` and `pages/explorer.py`).
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from pfetl_analytics_studio.analytics.explorer_models import (
    CatalogCoverageSummary,
    CatalogFilter,
    ChartRecommendation,
    ChartSuggestion,
    GuidedQuery,
    SqlQueryRequest,
    SqlQueryResult,
    SurfaceColumnFilter,
    TablePreviewResult,
)
from pfetl_analytics_studio.data import (
    SURFACE_ATLAS,
    CatalogEntry,
    CatalogSnapshot,
    ColumnInfo,
    CompatibilityReport,
    ControlPlaneReader,
    DuckDBReader,
    SurfaceLayer,
    SurfaceUsageCategory,
    validate_identifier,
)
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    DEFAULT_DATASETS,
    DEFAULT_METRICS,
    DatasetRegistry,
    DatasetSpec,
    FilterField,
    FilterOperator,
    MetricAggregation,
    MetricRegistry,
    MetricSpec,
    PeriodContext,
    compile_filter_clause,
    format_period_label,
)
from pfetl_analytics_studio.state import SqlDatasource

_DATE_LIKE_DIMENSIONS: frozenset[str] = frozenset(
    {
        "MONTH_START_DATE",
        "MONTH_END_DATE",
        "YEAR_MONTH",
        "Closing_Date",
        "Max_Closing_Date",
        "DATE",
        "ADJUSTED_DATE_FOR_ANALYSIS",
        "FY",
        "ZTXDATESTR",
    }
)

_BASE_FILTER_COLUMN_MAP: dict[FilterField, str] = {
    FilterField.ASSET_ID: "ASSET_ID",
    FilterField.ASSET_GROUP: "ASSET_GROUP",
    FilterField.INCOME_CATEGORY: "CATEGORY_NAME",
    FilterField.INCOME_GROUP: "CATEGORY_GROUPS",
    FilterField.EXPENSE_CATEGORY: "CATEGORY_NAME",
    FilterField.EXPENSE_GROUP: "CATEGORY_GROUPS",
    FilterField.IS_CORE_EXPENSE: "Is_Core_Expense",
    FilterField.IS_ACTIVE_INCOME: "Is_Active_Income",
    FilterField.IS_PASSIVE_INCOME: "Is_Passive_Income",
    FilterField.ISIN: "ISIN",
    FilterField.INSTRUMENT_NAME: "INSTRUMENT_NAME",
    FilterField.INSTRUMENT_CLASS: "INSTRUMENT_CLASS",
    FilterField.INSTRUMENT_TYPE: "INSTRUMENT_TYPE",
    FilterField.INSTRUMENT_SUBTYPE: "INSTRUMENT_SUBTYPE",
    FilterField.SECTOR: "SECTOR",
    FilterField.INDUSTRY: "INDUSTRY",
    FilterField.COUNTRY: "COUNTRY",
    FilterField.GEO: "GEO",
    FilterField.CURRENCY_ID: "CURRENCY_ID",
    FilterField.FY: "FY",
}

_DATASET_FILTER_OVERRIDES: dict[str, dict[FilterField, str]] = {
    "wealth_asset_breakdown": {
        FilterField.ASSET_ID: "ASSET_SUBCATEGORY_ID",
        FilterField.ASSET_GROUP: "ASSET_GROUP",
    },
    "silver_fact_income_transactions": {
        FilterField.INCOME_CATEGORY: "CATEGORY_ID",
        FilterField.INCOME_SUBCATEGORY: "CATEGORY_ID",
    },
    "silver_fact_expense_transactions": {
        FilterField.EXPENSE_CATEGORY: "CATEGORY_ID",
        FilterField.EXPENSE_SUBCATEGORY: "CATEGORY_ID",
    },
}


def filter_catalog_entries(
    entries: Mapping[str, CatalogEntry],
    catalog_filter: CatalogFilter,
) -> list[CatalogEntry]:
    """Filter discovered catalog entries by layer, domain, usage, availability, and text search."""
    search_term = catalog_filter.search.strip().lower()
    domain_term = catalog_filter.domain.strip().lower() if catalog_filter.domain else None

    matched: list[CatalogEntry] = []
    for entry in entries.values():
        if catalog_filter.layer is not None and entry.layer != catalog_filter.layer:
            continue
        if domain_term is not None and entry.domain.lower() != domain_term:
            continue
        if catalog_filter.usage is not None and entry.classification != catalog_filter.usage:
            continue
        if catalog_filter.only_available and entry.row_count is None:
            continue
        if search_term:
            haystack = " ".join(
                (
                    entry.physical_name,
                    entry.domain,
                    entry.grain,
                    entry.producer,
                    entry.spec.purpose if entry.spec else "",
                )
            ).lower()
            if search_term not in haystack:
                continue
        matched.append(entry)

    return sorted(matched, key=lambda e: (e.layer.value, e.physical_name))


def build_catalog_coverage(
    entries: Mapping[str, CatalogEntry],
    report: CompatibilityReport,
) -> CatalogCoverageSummary:
    """Summarize 82-surface coverage across layers and usage classifications."""
    usage_counts: dict[SurfaceUsageCategory, int] = {u: 0 for u in SurfaceUsageCategory}
    for entry in entries.values():
        if entry.classification is not None:
            usage_counts[entry.classification] += 1

    return CatalogCoverageSummary(
        total_expected=report.total_expected,
        total_discovered=report.total_discovered,
        layer_counts=dict(report.layer_counts),
        usage_counts=usage_counts,
        compatibility_status=report.status,
        missing_surfaces=tuple(report.missing_required_surfaces),
        unexpected_surfaces=tuple(report.unexpected_new_surfaces),
    )


def _compile_surface_filters(
    filters: Sequence[SurfaceColumnFilter],
    valid_columns: set[str],
    col_types: Mapping[str, str] | None = None,
) -> tuple[str, list[Any]]:
    """Compile parameterized WHERE clause for Data Catalog surface column filters."""
    if not filters:
        return "", []
    clauses: list[str] = []
    params: list[Any] = []
    types_map = col_types or {}
    for f in filters:
        col = validate_identifier(f.column)
        if valid_columns and col not in valid_columns:
            raise ValueError(f"Unknown column '{col}' for surface filter.")
        quoted = f'"{col}"'
        col_type = types_map.get(col, "").upper()
        is_numeric = any(
            tok in col_type for tok in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "BIGINT")
        )
        if f.operator == FilterOperator.IS_NULL:
            clauses.append(f"{quoted} IS NULL")
        elif f.operator == FilterOperator.IS_NOT_NULL:
            clauses.append(f"{quoted} IS NOT NULL")
        elif f.operator == FilterOperator.CONTAINS:
            clauses.append(f"LOWER(CAST({quoted} AS VARCHAR)) LIKE ?")
            params.append(f"%{str(f.value or '').lower()}%")
        elif f.operator in (FilterOperator.GTE, FilterOperator.LTE):
            op_sql = ">=" if f.operator == FilterOperator.GTE else "<="
            if is_numeric:
                clauses.append(f"CAST({quoted} AS DOUBLE) {op_sql} CAST(? AS DOUBLE)")
                params.append(float(str(f.value)) if f.value is not None else 0.0)
            else:
                clauses.append(f"CAST({quoted} AS VARCHAR) {op_sql} CAST(? AS VARCHAR)")
                params.append(str(f.value) if f.value is not None else "")
        else:
            clauses.append(f"CAST({quoted} AS VARCHAR) = ?")
            params.append(str(f.value) if f.value is not None else "")
    return " WHERE " + " AND ".join(clauses), params


def _discover_table_columns(
    profile: PFETLProfile,
    physical_name: str,
    layer: SurfaceLayer,
    entry: CatalogEntry | None,
) -> tuple[ColumnInfo, ...]:
    """Resolve column metadata from `CatalogEntry` or live schema inspection."""
    if entry is not None and entry.columns:
        return tuple(entry.columns)
    if layer == SurfaceLayer.CONTROL_PLANE:
        rows = ControlPlaneReader(profile).fetch_all(
            f'PRAGMA table_info("{validate_identifier(physical_name)}");'
        )
        return tuple(
            ColumnInfo(
                name=str(r["name"]),
                data_type=str(r.get("type") or "TEXT"),
                nullable=not bool(r.get("notnull", 0)),
            )
            for r in rows
        )
    schema_name, table_name = physical_name.split(".", 1)
    rows = DuckDBReader(profile).fetch_all(
        f'DESCRIBE "{validate_identifier(schema_name)}"."{validate_identifier(table_name)}";'
    )
    return tuple(
        ColumnInfo(
            name=str(r["column_name"]),
            data_type=str(r.get("column_type") or "VARCHAR"),
            nullable=str(r.get("null", "YES")).upper() == "YES",
        )
        for r in rows
    )


def fetch_surface_preview(
    profile: PFETLProfile,
    physical_name: str,
    *,
    limit: int = 100,
    filters: Sequence[SurfaceColumnFilter] = (),
    sort_column: str | None = None,
    sort_desc: bool = False,
    catalog_snapshot: CatalogSnapshot | None = None,
    entries: Mapping[str, CatalogEntry] | None = None,
) -> TablePreviewResult:
    """Return a strictly bounded, read-only preview of any classified PFETL surface."""
    safe_limit = max(1, min(limit, 500))
    spec = SURFACE_ATLAS.get(physical_name)
    if spec is None:
        return TablePreviewResult(
            physical_name=physical_name,
            layer=None,
            domain="Unknown",
            grain="Unknown",
            producer="Unknown",
            usage=None,
            purpose="Unclassified upstream surface.",
            columns=(),
            rows=(),
            total_row_count=None,
            limit_applied=safe_limit,
            truncated=False,
            is_bronze_warning=False,
            is_unclassified_blocked=True,
            warning_message=(
                f"Surface '{physical_name}' is not classified in SURFACE_ATLAS. "
                "Raw preview is disabled until the surface is deliberately classified."
            ),
        )

    entry = entries.get(physical_name) if entries is not None else None
    total_row_count = entry.row_count if entry is not None else None

    if catalog_snapshot is not None and not catalog_snapshot.has_surface(physical_name):
        columns = tuple(entry.columns) if entry is not None else ()
        return TablePreviewResult(
            physical_name=physical_name,
            layer=spec.layer,
            domain=spec.domain,
            grain=spec.grain,
            producer=spec.producer,
            usage=spec.usage,
            purpose=spec.purpose,
            columns=columns,
            rows=(),
            total_row_count=total_row_count,
            limit_applied=safe_limit,
            truncated=False,
            is_bronze_warning=spec.layer == SurfaceLayer.BRONZE,
            warning_message=f"Surface '{physical_name}' is not present in the active warehouse.",
        )

    columns = _discover_table_columns(profile, physical_name, spec.layer, entry)
    valid_cols = {c.name for c in columns}
    col_types = {c.name: c.data_type for c in columns}
    where_sql, params = _compile_surface_filters(filters, valid_cols, col_types)

    order_sql = ""
    if sort_column and sort_column.strip():
        clean_sort = validate_identifier(sort_column)
        if valid_cols and clean_sort not in valid_cols:
            raise ValueError(f"Unknown sort column '{clean_sort}' for '{physical_name}'.")
        order_sql = f' ORDER BY "{clean_sort}" {"DESC" if sort_desc else "ASC"}'

    if spec.layer == SurfaceLayer.CONTROL_PLANE:
        cp_reader = ControlPlaneReader(profile)
        tbl = validate_identifier(physical_name)
        filtered_count = (
            int(cp_reader.fetch_scalar(f'SELECT COUNT(*) FROM "{tbl}"{where_sql};', params) or 0)
            if filters
            else total_row_count
        )
        df = cp_reader.fetch_df(
            f'SELECT * FROM "{tbl}"{where_sql}{order_sql} LIMIT ?;',
            [*params, safe_limit],
        )
    else:
        schema_name, table_name = physical_name.split(".", 1)
        s_id = validate_identifier(schema_name)
        t_id = validate_identifier(table_name)
        duck_reader = DuckDBReader(profile)
        with duck_reader.session() as db:
            filtered_count = (
                int(
                    db.fetch_scalar(f'SELECT COUNT(*) FROM "{s_id}"."{t_id}"{where_sql};', params)
                    or 0
                )
                if filters
                else total_row_count
            )
            df = db.fetch_df(
                f'SELECT * FROM "{s_id}"."{t_id}"{where_sql}{order_sql} LIMIT ?;',
                [*params, safe_limit],
            )

    rows = tuple(df.to_dicts())
    effective_total = filtered_count if filtered_count is not None else total_row_count
    truncated = (effective_total is not None and effective_total > len(rows)) or (
        len(rows) == safe_limit
    )
    bronze_warning = spec.layer == SurfaceLayer.BRONZE
    warning_msg = (
        "Bronze raw source evidence: never use Bronze tables for derived headline KPIs."
        if bronze_warning
        else None
    )

    return TablePreviewResult(
        physical_name=physical_name,
        layer=spec.layer,
        domain=spec.domain,
        grain=spec.grain,
        producer=spec.producer,
        usage=spec.usage,
        purpose=spec.purpose,
        columns=columns,
        rows=rows,
        total_row_count=total_row_count,
        filtered_row_count=filtered_count,
        limit_applied=safe_limit,
        truncated=truncated,
        is_bronze_warning=bronze_warning,
        is_unclassified_blocked=False,
        warning_message=warning_msg,
    )


def export_surface_csv(
    profile: PFETLProfile,
    physical_name: str,
    *,
    filters: Sequence[SurfaceColumnFilter] = (),
    sort_column: str | None = None,
    sort_desc: bool = False,
) -> tuple[bytes, int, str]:
    """Export all rows of a classified PFETL surface (respecting filters) as CSV bytes."""
    spec = SURFACE_ATLAS.get(physical_name)
    if spec is None:
        raise ValueError(
            f"Surface '{physical_name}' is not classified in SURFACE_ATLAS and cannot be exported."
        )

    columns = _discover_table_columns(profile, physical_name, spec.layer, None)
    valid_cols = {c.name for c in columns}
    col_types = {c.name: c.data_type for c in columns}
    where_sql, params = _compile_surface_filters(filters, valid_cols, col_types)

    order_sql = ""
    if sort_column and sort_column.strip():
        clean_sort = validate_identifier(sort_column)
        if valid_cols and clean_sort not in valid_cols:
            raise ValueError(f"Unknown sort column '{clean_sort}' for '{physical_name}'.")
        order_sql = f' ORDER BY "{clean_sort}" {"DESC" if sort_desc else "ASC"}'

    if spec.layer == SurfaceLayer.CONTROL_PLANE:
        tbl = validate_identifier(physical_name)
        df = ControlPlaneReader(profile).fetch_df(
            f'SELECT * FROM "{tbl}"{where_sql}{order_sql};',
            params,
        )
    else:
        schema_name, table_name = physical_name.split(".", 1)
        s_id = validate_identifier(schema_name)
        t_id = validate_identifier(table_name)
        df = DuckDBReader(profile).fetch_df(
            f'SELECT * FROM "{s_id}"."{t_id}"{where_sql}{order_sql};',
            params,
        )

    safe_name = physical_name.replace(".", "_")
    return df.write_csv().encode("utf-8"), df.height, f"{safe_name}.csv"


def _filter_column_map_for(dataset: DatasetSpec) -> dict[FilterField, str]:
    """Build whitelisted FilterField -> physical column map for `dataset`."""
    overrides = _DATASET_FILTER_OVERRIDES.get(dataset.id, {})
    col_map: dict[FilterField, str] = {}
    for field in dataset.allowed_filters:
        col = overrides.get(field) or _BASE_FILTER_COLUMN_MAP.get(field)
        if col is not None:
            col_map[field] = validate_identifier(col)
    return col_map


def _resolve_metric_aggregation(
    spec: MetricSpec,
    requested: MetricAggregation | None,
) -> MetricAggregation:
    """Resolve effective aggregation and enforce non-additive safety."""
    eff = requested if requested is not None else spec.aggregation
    if not spec.additive and eff in (MetricAggregation.SUM, MetricAggregation.AVG):
        raise ValueError(
            f"Metric '{spec.id}' is non-additive and cannot be aggregated with '{eff.value}'."
        )
    return eff


def _format_semantic_summary(
    dataset: DatasetSpec,
    dimensions: Sequence[str],
    metric_pairs: Sequence[tuple[MetricSpec, MetricAggregation]],
    period_context: PeriodContext | None,
    query: GuidedQuery,
) -> str:
    """Build human-readable semantic query description (no raw SQL exposed)."""
    dim_part = ", ".join(dimensions) if dimensions else f"Native grain ({dataset.grain})"
    if metric_pairs:
        met_part = ", ".join(f"{m.label} ({agg.value.upper()})" for m, agg in metric_pairs)
    else:
        met_part = "Distinct dimension values"

    parts = [
        f"Dataset: {dataset.label} [{dataset.grain}]",
        f"Group/Slice: {dim_part}",
        f"Measures: {met_part}",
    ]
    if period_context is not None and not period_context.is_empty:
        parts.append(f"Period: {format_period_label(period_context)}")
    if query.filters.filters:
        parts.append(f"Filters: {len(query.filters.filters)} active")
    parts.append(f"Limit: {query.limit:,}")
    return " | ".join(parts)


def compile_guided_query(
    query: GuidedQuery,
    *,
    dataset_registry: DatasetRegistry = DEFAULT_DATASETS,
    metric_registry: MetricRegistry = DEFAULT_METRICS,
    period_context: PeriodContext | None = None,
    include_limit: bool = True,
) -> tuple[str, list[Any], tuple[MetricSpec, ...], str]:
    """Validate `GuidedQuery` and compile parameterized DuckDB SQL over whitelisted identifiers."""
    if not query.dimensions and not query.metrics:
        raise ValueError("GuidedQuery requires at least one dimension or one metric.")

    dataset = dataset_registry.get(query.dataset_id)
    dataset_registry.validate_dimensions(dataset.id, query.dimensions)
    clean_dims = tuple(validate_identifier(d) for d in query.dimensions)

    metric_specs: list[MetricSpec] = []
    metric_aggs: list[tuple[MetricSpec, MetricAggregation]] = []
    for m_id in query.metrics:
        req_agg = query.metric_aggregations.get(m_id)
        spec = metric_registry.validate_query(
            m_id,
            dimensions=clean_dims,
            requested_aggregation=req_agg,
        )
        if spec.dataset != dataset.id:
            raise ValueError(
                f"Metric '{m_id}' belongs to dataset '{spec.dataset}', not '{dataset.id}'."
            )
        eff_agg = _resolve_metric_aggregation(spec, req_agg)
        metric_specs.append(spec)
        metric_aggs.append((spec, eff_agg))

    # Enforce native grain if any non-additive metric uses NONE without full default dimensions
    if clean_dims:
        default_dim_set = set(dataset.default_dimensions)
        for spec, eff_agg in metric_aggs:
            if (
                not spec.additive
                and eff_agg == MetricAggregation.NONE
                and not default_dim_set.issubset(set(clean_dims))
            ):
                raise ValueError(
                    f"Non-additive metric '{spec.id}' with aggregation 'none' requires native "
                    f"grain dimensions ({', '.join(dataset.default_dimensions)}) or 'latest'."
                )

    uses_group_by = bool(clean_dims) and any(
        agg in (MetricAggregation.SUM, MetricAggregation.AVG, MetricAggregation.LATEST)
        for _, agg in metric_aggs
    )
    if clean_dims and not metric_aggs:
        uses_group_by = True

    schema_name, table_name = dataset.physical_table.split(".", 1)
    valid_schema = validate_identifier(schema_name)
    valid_table = validate_identifier(table_name)
    valid_date_field = validate_identifier(dataset.date_field)

    select_parts: list[str] = [f'"{d}"' for d in clean_dims]
    valid_output_fields: set[str] = set(clean_dims)

    for spec, eff_agg in metric_aggs:
        alias = validate_identifier(spec.id)
        valid_output_fields.add(alias)
        if uses_group_by:
            if eff_agg == MetricAggregation.SUM:
                select_parts.append(f'SUM({spec.expression}) AS "{alias}"')
            elif eff_agg == MetricAggregation.AVG:
                select_parts.append(f'AVG({spec.expression}) AS "{alias}"')
            elif eff_agg == MetricAggregation.LATEST:
                select_parts.append(
                    f'ARG_MAX({spec.expression}, "{valid_date_field}") AS "{alias}"'
                )
            else:
                raise ValueError(
                    f"Metric '{spec.id}' aggregation '{eff_agg.value}' cannot be mixed with "
                    "grouped aggregations in the same query."
                )
        elif eff_agg in (MetricAggregation.SUM, MetricAggregation.AVG) and not clean_dims:
            fn = "SUM" if eff_agg == MetricAggregation.SUM else "AVG"
            select_parts.append(f'{fn}({spec.expression}) AS "{alias}"')
        elif eff_agg == MetricAggregation.LATEST and not clean_dims:
            select_parts.append(f'ARG_MAX({spec.expression}, "{valid_date_field}") AS "{alias}"')
        else:
            select_parts.append(f'{spec.expression} AS "{alias}"')

    where_clauses: list[str] = []
    params: list[Any] = []

    if period_context is not None and not period_context.is_empty:
        if dataset.date_field == "FY":
            if period_context.financial_year:
                where_clauses.append('"FY" = ?')
                params.append(period_context.financial_year)
        elif period_context.start_date is not None and period_context.end_date is not None:
            where_clauses.append(f'"{valid_date_field}" BETWEEN ? AND ?')
            params.extend([period_context.start_date, period_context.end_date])

    if query.filters.filters:
        col_map = _filter_column_map_for(dataset)
        filter_sql, filter_params = compile_filter_clause(query.filters, col_map)
        if filter_sql != "1 = 1":
            where_clauses.append(f"({filter_sql})")
            params.extend(filter_params)

    sql_lines = [
        f"SELECT {', '.join(select_parts)}",
        f'FROM "{valid_schema}"."{valid_table}"',
    ]
    if where_clauses:
        sql_lines.append(f"WHERE {' AND '.join(where_clauses)}")
    if uses_group_by and clean_dims:
        group_cols = ", ".join(f'"{d}"' for d in clean_dims)
        sql_lines.append(f"GROUP BY {group_cols}")

    if query.sort:
        order_parts: list[str] = []
        for s_spec in query.sort:
            if s_spec.field not in valid_output_fields:
                raise ValueError(
                    f"Sort field '{s_spec.field}' must be a selected dimension or metric."
                )
            direction = "DESC" if s_spec.descending else "ASC"
            order_parts.append(f'"{validate_identifier(s_spec.field)}" {direction}')
        sql_lines.append(f"ORDER BY {', '.join(order_parts)}")
    elif clean_dims:
        default_order = ", ".join(f'"{d}" ASC' for d in clean_dims)
        sql_lines.append(f"ORDER BY {default_order}")

    if include_limit:
        sql_lines.append("LIMIT ?")
        params.append(query.limit)
    sql = "\n".join(sql_lines) + ";"

    summary = _format_semantic_summary(dataset, clean_dims, metric_aggs, period_context, query)
    return sql, params, tuple(metric_specs), summary


def recommend_chart(
    query: GuidedQuery,
    dataset: DatasetSpec,
    metric_specs: Sequence[MetricSpec],
    rows: Sequence[Mapping[str, Any]],
) -> ChartSuggestion:
    """Deterministically recommend a chart type from query dimensions and metrics."""
    if not rows or not metric_specs:
        return ChartSuggestion(
            chart_type=ChartRecommendation.TABLE,
            reason="Tabular view selected (no numeric metrics or empty result).",
        )

    dims = query.dimensions
    y_fields = tuple(m.id for m in metric_specs)
    date_dims = [d for d in dims if d == dataset.date_field or d in _DATE_LIKE_DIMENSIONS]
    cat_dims = [d for d in dims if d not in date_dims]

    primary_date = (
        dataset.date_field
        if dataset.date_field in date_dims
        else (date_dims[0] if date_dims else None)
    )

    if primary_date is not None and not cat_dims:
        return ChartSuggestion(
            chart_type=ChartRecommendation.TIME_SERIES,
            reason=f"Time-series trend over {primary_date}.",
            x_field=primary_date,
            y_fields=y_fields,
        )

    if primary_date is not None and cat_dims:
        cat_col = cat_dims[0]
        if len(metric_specs) == 1 and metric_specs[0].additive:
            return ChartSuggestion(
                chart_type=ChartRecommendation.STACKED_BAR,
                reason=f"Stacked Bar by {primary_date} and {cat_col}.",
                x_field=primary_date,
                y_fields=y_fields,
                series_field=cat_col,
            )
        return ChartSuggestion(
            chart_type=ChartRecommendation.TIME_SERIES,
            reason=f"Time-series by {primary_date} grouped by {cat_col}.",
            x_field=primary_date,
            y_fields=(y_fields[0],),
            series_field=cat_col,
        )

    if not date_dims and len(cat_dims) == 1:
        if len(metric_specs) == 1 and len(rows) > 8:
            return ChartSuggestion(
                chart_type=ChartRecommendation.HORIZONTAL_BAR,
                reason=f"Horizontal ranking across {len(rows)} {cat_dims[0]} values.",
                x_field=cat_dims[0],
                y_fields=y_fields,
            )
        chart_kind = (
            ChartRecommendation.BAR if len(metric_specs) == 1 else ChartRecommendation.GROUPED_BAR
        )
        return ChartSuggestion(
            chart_type=chart_kind,
            reason=f"Categorical comparison across {cat_dims[0]}.",
            x_field=cat_dims[0],
            y_fields=y_fields,
        )

    if not date_dims and len(cat_dims) >= 2:
        return ChartSuggestion(
            chart_type=ChartRecommendation.STACKED_BAR,
            reason=f"Stacked breakdown across {cat_dims[0]} and {cat_dims[1]}.",
            x_field=cat_dims[0],
            y_fields=(y_fields[0],),
            series_field=cat_dims[1],
        )

    return ChartSuggestion(
        chart_type=ChartRecommendation.BAR,
        reason="Summary bar comparison across selected measures.",
        x_field=None,
        y_fields=y_fields,
    )


_FORBIDDEN_SQL_KEYWORDS: frozenset[str] = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "CREATE",
        "ALTER",
        "TRUNCATE",
        "REPLACE",
        "MERGE",
        "UPSERT",
        "PRAGMA",
        "ATTACH",
        "DETACH",
        "COPY",
        "INSTALL",
        "LOAD",
        "EXPORT",
        "IMPORT",
        "CALL",
        "EXECUTE",
        "VACUUM",
        "REINDEX",
        "SET",
        "RESET",
        "GRANT",
        "REVOKE",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
    }
)


def _strip_sql_comments_and_literals(sql: str) -> tuple[str, str]:
    """Strip SQL comments for validation while producing an unquoted-token scan string."""
    cleaned: list[str] = []
    unquoted: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        # Single-line comment
        if ch == "-" and nxt == "-":
            i += 2
            while i < n and sql[i] != "\n":
                i += 1
            cleaned.append(" ")
            unquoted.append(" ")
            continue
        # Block comment
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            cleaned.append(" ")
            unquoted.append(" ")
            continue
        # Single-quoted string literal
        if ch == "'":
            cleaned.append(ch)
            unquoted.append(" ")
            i += 1
            while i < n:
                cleaned.append(sql[i])
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    cleaned.append("'")
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        # Double-quoted identifier
        if ch == '"':
            cleaned.append(ch)
            unquoted.append(" ")
            i += 1
            while i < n:
                cleaned.append(sql[i])
                if sql[i] == '"' and i + 1 < n and sql[i + 1] == '"':
                    cleaned.append('"')
                    i += 2
                    continue
                if sql[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        cleaned.append(ch)
        unquoted.append(ch)
        i += 1
    return "".join(cleaned).strip(), "".join(unquoted).strip()


def validate_read_only_sql(raw_sql: str) -> str:
    """Validate that `raw_sql` is a single read-only SELECT or WITH query."""
    if not raw_sql or not raw_sql.strip():
        raise ValueError("SQL query cannot be empty.")

    cleaned, unquoted = _strip_sql_comments_and_literals(raw_sql)
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if unquoted.endswith(";"):
        unquoted = unquoted[:-1].strip()

    if not cleaned:
        raise ValueError("SQL query contains only comments or whitespace.")
    if ";" in unquoted:
        raise ValueError("Multiple SQL statements are not permitted (unexpected semicolon).")

    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", unquoted.upper())
    if not tokens:
        raise ValueError("Unable to parse a valid SQL statement.")
    if tokens[0] not in ("SELECT", "WITH"):
        raise ValueError(
            f"Only read-only SELECT or WITH statements are permitted (got '{tokens[0]}')."
        )
    if tokens[0] == "WITH" and "SELECT" not in tokens:
        raise ValueError("CTE query beginning with WITH must contain a SELECT statement.")

    forbidden_hits = [t for t in tokens if t in _FORBIDDEN_SQL_KEYWORDS]
    if forbidden_hits:
        raise ValueError(
            f"Read-only SQL guard rejected forbidden keyword(s): {', '.join(sorted(set(forbidden_hits)))}."
        )
    return cleaned


def execute_read_only_sql(
    profile: PFETLProfile,
    request: SqlQueryRequest,
) -> SqlQueryResult:
    """Execute a validated single-statement SELECT/WITH query with a hard LIMIT wrapper."""
    normalized = validate_read_only_sql(request.sql)
    safe_limit = max(1, min(request.limit, 10_000))
    wrapped_sql = f"SELECT * FROM ({normalized}) AS _studio_sql_q LIMIT ?;"
    probe_limit = safe_limit + 1

    t0 = time.perf_counter()
    if request.datasource == SqlDatasource.CONTROL_PLANE:
        df = ControlPlaneReader(profile).fetch_df(wrapped_sql, (probe_limit,))
    else:
        df = DuckDBReader(profile).fetch_df(wrapped_sql, (probe_limit,))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    truncated = df.height > safe_limit
    if truncated:
        df = df.head(safe_limit)

    cols = tuple(df.columns)
    rows = tuple(df.to_dicts())
    warnings: list[str] = []
    if truncated:
        warnings.append(
            f"Result truncated at {safe_limit:,} rows (use Download Full CSV for all rows)."
        )
    if not rows:
        warnings.append("Query executed successfully and returned 0 rows.")

    return SqlQueryResult(
        sql=request.sql,
        normalized_sql=normalized,
        datasource=request.datasource,
        columns=cols,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        limit_applied=safe_limit,
        duration_ms=elapsed_ms,
        warnings=tuple(warnings),
    )


def export_sql_csv(
    profile: PFETLProfile,
    sql: str,
    datasource: SqlDatasource = SqlDatasource.DUCKDB,
) -> tuple[bytes, int, str]:
    """Execute a validated read-only SQL query without row cap and export as CSV bytes."""
    normalized = validate_read_only_sql(sql)
    wrapped_sql = f"SELECT * FROM ({normalized}) AS _studio_sql_export;"
    if datasource == SqlDatasource.CONTROL_PLANE:
        df = ControlPlaneReader(profile).fetch_df(wrapped_sql)
    else:
        df = DuckDBReader(profile).fetch_df(wrapped_sql)
    fname = f"sql_{datasource.value}_export.csv"
    return df.write_csv().encode("utf-8"), df.height, fname
