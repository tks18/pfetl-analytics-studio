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
from pathlib import Path
from typing import Any, cast

from pfetl_analytics_studio.analytics.explorer_models import (
    CatalogBrowseResult,
    CatalogFilter,
    GuidedQuery,
    GuidedQueryResult,
    SqlQueryRequest,
    SqlQueryResult,
    SurfaceColumnFilter,
    TablePreviewResult,
)
from pfetl_analytics_studio.analytics.explorer_queries import (
    build_catalog_coverage,
    compile_guided_query,
    execute_read_only_sql,
    export_sql_csv,
    export_surface_csv,
    fetch_surface_preview,
    filter_catalog_entries,
    recommend_chart,
    validate_read_only_sql,
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
    FilterSet,
    MetricSpec,
    PeriodContext,
    PeriodRequest,
    PeriodResolver,
)
from pfetl_analytics_studio.state import (
    SavedViewDefinition,
    SavedViewKind,
    SqlDatasource,
    StudioStateStore,
)


class ExplorerAnalytics:
    """Read-only analytical facade for Data Catalog & Guided Explorer."""

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

    @staticmethod
    def execute_sql(
        profile: PFETLProfile,
        request: SqlQueryRequest,
        *,
        run_id: str = "latest",
        cache: QueryCache | None = None,
    ) -> SqlQueryResult:
        """Validate and execute a single-statement read-only SQL query with bounded rows."""
        if cache is None:
            return execute_read_only_sql(profile, request)

        key = build_cache_key(
            profile.profile_id,
            run_id,
            "explorer_sql",
            extra=request.model_dump(mode="json"),
        )
        return cast(
            SqlQueryResult,
            cache.get_or_compute(key, lambda: execute_read_only_sql(profile, request)),
        )

    @staticmethod
    def export_sql_csv(
        profile: PFETLProfile,
        sql: str,
        datasource: SqlDatasource = SqlDatasource.DUCKDB,
    ) -> tuple[bytes, int, str]:
        """Export full unbounded rows of a validated read-only SQL query as CSV bytes."""
        return export_sql_csv(profile, sql, datasource=datasource)

    @staticmethod
    def list_saved_views(
        profile_id: str,
        *,
        kind: SavedViewKind | None = None,
        ensure_starters: bool = True,
        state_db_path: Path | None = None,
    ) -> list[SavedViewDefinition]:
        """List saved Guided and SQL views for `profile_id` from Studio state."""
        with StudioStateStore(db_path=state_db_path) as store:
            if ensure_starters:
                store.seed_default_sql_starters(profile_id)
            return store.list_saved_views(profile_id, kind=kind)

    @staticmethod
    def restore_default_sql_starters(
        profile_id: str,
        *,
        state_db_path: Path | None = None,
    ) -> int:
        """Restore any missing default SQL starter packs into `studio.sqlite`."""
        with StudioStateStore(db_path=state_db_path) as store:
            return store.seed_default_sql_starters(profile_id, force_restore=True)

    @staticmethod
    def get_saved_view(
        view_id: str,
        *,
        state_db_path: Path | None = None,
    ) -> SavedViewDefinition | None:
        """Fetch a single saved view by `view_id`."""
        with StudioStateStore(db_path=state_db_path) as store:
            return store.get_saved_view(view_id)

    @staticmethod
    def save_guided_view(
        profile_id: str,
        name: str,
        query: GuidedQuery,
        *,
        description: str = "",
        period_override: PeriodRequest | None = None,
        filter_override: FilterSet | None = None,
        state_db_path: Path | None = None,
    ) -> SavedViewDefinition:
        """Persist a reusable `GuidedQuery` semantic specification in Studio state."""
        with StudioStateStore(db_path=state_db_path) as store:
            return store.create_saved_view(
                profile_id,
                name,
                SavedViewKind.GUIDED,
                description=description,
                datasource=SqlDatasource.DUCKDB,
                query_spec=query.model_dump(mode="json"),
                period_override=(
                    period_override.model_dump(mode="json") if period_override is not None else None
                ),
                filter_override=(
                    filter_override.model_dump(mode="json") if filter_override is not None else None
                ),
            )

    @staticmethod
    def save_sql_view(
        profile_id: str,
        name: str,
        sql_text: str,
        *,
        datasource: SqlDatasource = SqlDatasource.DUCKDB,
        description: str = "",
        state_db_path: Path | None = None,
    ) -> SavedViewDefinition:
        """Persist a validated read-only SQL query in Studio state."""
        validate_read_only_sql(sql_text)
        with StudioStateStore(db_path=state_db_path) as store:
            return store.create_saved_view(
                profile_id,
                name,
                SavedViewKind.SQL,
                description=description,
                datasource=datasource,
                sql_text=sql_text,
            )

    @staticmethod
    def update_saved_view(
        view_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        datasource: SqlDatasource | None = None,
        category: str | None = None,
        query_spec: dict[str, Any] | None = None,
        sql_text: str | None = None,
        state_db_path: Path | None = None,
    ) -> SavedViewDefinition:
        """Update an existing saved view's metadata or query definition."""
        if sql_text is not None:
            validate_read_only_sql(sql_text)
        with StudioStateStore(db_path=state_db_path) as store:
            return store.update_saved_view(
                view_id,
                name=name,
                description=description,
                datasource=datasource,
                category=category,
                query_spec=query_spec,
                sql_text=sql_text,
            )

    @staticmethod
    def delete_saved_view(
        view_id: str,
        *,
        state_db_path: Path | None = None,
    ) -> None:
        """Delete a saved view by `view_id`."""
        with StudioStateStore(db_path=state_db_path) as store:
            store.delete_saved_view(view_id)

    @staticmethod
    def replay_saved_view(
        profile: PFETLProfile,
        view: SavedViewDefinition,
        *,
        run_id: str = "latest",
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
        global_period: PeriodRequest | None = None,
        global_filters: FilterSet | None = None,
        sql_limit: int = 500,
    ) -> GuidedQueryResult | SqlQueryResult:
        """Replay a saved Guided or SQL view under the active profile."""
        if view.kind == SavedViewKind.SQL:
            return ExplorerAnalytics.execute_sql(
                profile,
                SqlQueryRequest(sql=view.sql_text, datasource=view.datasource, limit=sql_limit),
                run_id=run_id,
                cache=cache,
            )

        query = GuidedQuery.model_validate(view.query_spec or {})
        if global_period is not None and query.period is None:
            query = query.model_copy(update={"period": global_period})
        elif view.period_override and query.period is None:
            query = query.model_copy(
                update={"period": PeriodRequest.model_validate(view.period_override)}
            )
        if global_filters is not None and global_filters.filters and not query.filters.filters:
            ds = DEFAULT_DATASETS.get(query.dataset_id)
            allowed = set(ds.allowed_filters)
            compat = tuple(f for f in global_filters.filters if f.field in allowed)
            if compat:
                query = query.model_copy(update={"filters": FilterSet(filters=compat)})

        return ExplorerAnalytics.execute_guided(
            profile,
            query,
            run_id=run_id,
            catalog_snapshot=catalog_snapshot,
            cache=cache,
        )
