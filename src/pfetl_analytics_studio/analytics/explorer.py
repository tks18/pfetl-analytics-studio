"""Explorer analytical service for 82-surface Data Catalog & Guided Explorer.

Owns
----
- `ExplorerAnalytics.catalog_entries(...)`: searchable, layer/domain/usage-filtered 82-surface catalog.
- `ExplorerAnalytics.preview(...)`: strictly bounded, read-only raw surface preview.
- `ExplorerAnalytics.execute_guided(...)`: validated, whitelisted GuidedQuery execution with
  deterministic chart recommendation and in-memory `QueryCache` support.

Does not own
------------
- NiceGUI widgets or raw SQL execution without `DatasetSpec`/`MetricSpec` whitelisting.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from pfetl_analytics_studio.analytics.explorer_models import (
    CatalogBrowseResult,
    CatalogFilter,
    GuidedQuery,
    GuidedQueryResult,
    SurfaceColumnFilter,
    TablePreviewResult,
)
from pfetl_analytics_studio.analytics.explorer_queries import (
    build_catalog_coverage,
    compile_guided_query,
    export_surface_csv,
    fetch_surface_preview,
    filter_catalog_entries,
    recommend_chart,
)
from pfetl_analytics_studio.data import (
    CatalogSnapshot,
    DuckDBReader,
    PFETLCatalog,
    QueryCache,
    build_cache_key,
)
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    DEFAULT_DATASETS,
    DEFAULT_METRICS,
    DatasetSpec,
    MetricSpec,
    PeriodContext,
    PeriodResolver,
)


class ExplorerAnalytics:
    """Read-only analytical facade for Phase 22 Data Catalog & Guided Explorer."""

    @staticmethod
    def list_datasets() -> list[DatasetSpec]:
        """Return all registered semantic datasets sorted by ID."""
        return DEFAULT_DATASETS.list_datasets()

    @staticmethod
    def list_metrics(dataset_id: str) -> list[MetricSpec]:
        """Return all registered semantic metrics for `dataset_id`."""
        return DEFAULT_METRICS.list_metrics(dataset_id=dataset_id)

    @staticmethod
    def catalog_entries(
        profile: PFETLProfile,
        catalog_filter: CatalogFilter | None = None,
        *,
        run_id: str = "latest",
        cache: QueryCache | None = None,
    ) -> CatalogBrowseResult:
        """Discover and filter all 82 PFETL upstream surfaces with coverage telemetry."""
        active_filter = catalog_filter or CatalogFilter()

        def _compute() -> CatalogBrowseResult:
            entries, report = PFETLCatalog(profile).discover()
            filtered = filter_catalog_entries(entries, active_filter)
            coverage = build_catalog_coverage(entries, report)
            domains = tuple(sorted({e.domain for e in entries.values() if e.domain}))
            return CatalogBrowseResult(
                entries=tuple(filtered),
                coverage=coverage,
                available_domains=domains,
            )

        if cache is None:
            return _compute()

        key = build_cache_key(
            profile.profile_id,
            run_id,
            "explorer_catalog",
            extra=active_filter.model_dump(mode="json"),
        )
        return cast(CatalogBrowseResult, cache.get_or_compute(key, _compute))

    @staticmethod
    def preview(
        profile: PFETLProfile,
        physical_name: str,
        *,
        limit: int = 100,
        filters: Sequence[SurfaceColumnFilter] = (),
        sort_column: str | None = None,
        sort_desc: bool = False,
        catalog_snapshot: CatalogSnapshot | None = None,
        run_id: str = "latest",
        cache: QueryCache | None = None,
    ) -> TablePreviewResult:
        """Return a bounded, read-only preview for any classified PFETL surface."""

        def _compute() -> TablePreviewResult:
            entries, _ = PFETLCatalog(profile).discover()
            return fetch_surface_preview(
                profile,
                physical_name,
                limit=limit,
                filters=filters,
                sort_column=sort_column,
                sort_desc=sort_desc,
                catalog_snapshot=catalog_snapshot,
                entries=entries,
            )

        if cache is None:
            return _compute()

        key = build_cache_key(
            profile.profile_id,
            run_id,
            "explorer_preview",
            extra={
                "physical_name": physical_name,
                "limit": max(1, min(limit, 500)),
                "filters": [f.model_dump(mode="json") for f in filters],
                "sort_column": sort_column,
                "sort_desc": sort_desc,
            },
        )
        return cast(TablePreviewResult, cache.get_or_compute(key, _compute))

    @staticmethod
    def export_surface_csv(
        profile: PFETLProfile,
        physical_name: str,
        *,
        filters: Sequence[SurfaceColumnFilter] = (),
        sort_column: str | None = None,
        sort_desc: bool = False,
    ) -> tuple[bytes, int, str]:
        """Export full unbounded rows of a classified PFETL surface as CSV bytes."""
        return export_surface_csv(
            profile,
            physical_name,
            filters=filters,
            sort_column=sort_column,
            sort_desc=sort_desc,
        )

    @staticmethod
    def export_guided_csv(
        profile: PFETLProfile,
        query: GuidedQuery,
    ) -> tuple[bytes, int, str]:
        """Export full unbounded rows of a validated `GuidedQuery` as CSV bytes."""
        reader = DuckDBReader(profile)
        with reader.session() as db:
            resolved_period: PeriodContext | None = None
            if query.period is not None:
                resolved_period = PeriodResolver(db).resolve(query.period)
            sql, params, _, _ = compile_guided_query(
                query,
                dataset_registry=DEFAULT_DATASETS,
                metric_registry=DEFAULT_METRICS,
                period_context=resolved_period,
                include_limit=False,
            )
            df = db.fetch_df(sql, params)
            return df.write_csv().encode("utf-8"), df.height, f"guided_{query.dataset_id}.csv"

    @staticmethod
    def execute_guided(
        profile: PFETLProfile,
        query: GuidedQuery,
        *,
        run_id: str = "latest",
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> GuidedQueryResult:
        """Validate, compile, and execute a `GuidedQuery` over whitelisted semantics."""
        dataset = DEFAULT_DATASETS.get(query.dataset_id)

        def _compute() -> GuidedQueryResult:
            reader = DuckDBReader(profile)
            with reader.session() as db:
                resolved_period: PeriodContext | None = None
                if query.period is not None:
                    resolved_period = PeriodResolver(db).resolve(query.period)

                sql, params, metric_specs, summary = compile_guided_query(
                    query,
                    dataset_registry=DEFAULT_DATASETS,
                    metric_registry=DEFAULT_METRICS,
                    period_context=resolved_period,
                )

                if catalog_snapshot is not None and not catalog_snapshot.has_surface(
                    dataset.physical_table
                ):
                    return GuidedQueryResult(
                        query=query,
                        dataset=dataset,
                        metric_specs=metric_specs,
                        resolved_period=resolved_period,
                        columns=(),
                        rows=(),
                        row_count=0,
                        truncated=False,
                        semantic_summary=summary,
                        chart_suggestion=recommend_chart(query, dataset, metric_specs, ()),
                        warnings=(
                            f"Dataset surface '{dataset.physical_table}' is unavailable in this warehouse.",
                        ),
                    )

                rows = db.fetch_all(sql, params)
                cols = (
                    tuple(rows[0].keys())
                    if rows
                    else tuple([*query.dimensions, *(m.id for m in metric_specs)])
                )
                truncated = len(rows) >= query.limit
                warnings: list[str] = []
                if any(not m.additive for m in metric_specs):
                    warnings.append(
                        "Includes non-additive metric(s) evaluated at latest/native grain."
                    )
                if truncated:
                    warnings.append(f"Results capped at limit ({query.limit:,} rows).")
                if not rows:
                    warnings.append("No rows matched the selected period and filters.")

                suggestion = recommend_chart(query, dataset, metric_specs, rows)
                return GuidedQueryResult(
                    query=query,
                    dataset=dataset,
                    metric_specs=metric_specs,
                    resolved_period=resolved_period,
                    columns=cols,
                    rows=tuple(rows),
                    row_count=len(rows),
                    truncated=truncated,
                    semantic_summary=summary,
                    chart_suggestion=suggestion,
                    warnings=tuple(warnings),
                )

        if cache is None:
            return _compute()

        key = build_cache_key(
            profile.profile_id,
            run_id,
            "explorer_guided",
            filters=query.filters,
            extra=query.model_dump(mode="json", exclude={"filters"}),
        )
        return cast(GuidedQueryResult, cache.get_or_compute(key, _compute))
