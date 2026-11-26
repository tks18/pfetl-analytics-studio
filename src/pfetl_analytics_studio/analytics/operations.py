"""Phase 19 Operations Run History, Sync Health & Warehouse Freshness service."""

from __future__ import annotations

from pfetl_analytics_studio.analytics.operations_models import (
    OperationRunRecord,
    OperationsSnapshot,
    RunHistoryFilter,
    WarehouseHealthSnapshot,
)
from pfetl_analytics_studio.analytics.operations_queries import (
    build_operations_kpis,
    query_cp_runs_with_sync,
    query_warehouse_contracts,
    query_warehouse_row_counts,
)
from pfetl_analytics_studio.analytics.overview_queries import (
    reset_catalog_snapshot,
    use_catalog_snapshot,
)
from pfetl_analytics_studio.data import (
    CatalogSnapshot,
    ControlPlaneReader,
    DuckDBReader,
    QueryCache,
    WarehouseState,
    build_cache_key,
)
from pfetl_analytics_studio.profile import PFETLProfile


class OperationsAnalytics:
    """Read-only analytical service for PFETL Control Plane runs, syncs & warehouse health."""

    @classmethod
    def warehouse_health(
        cls,
        profile: PFETLProfile,
        *,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> WarehouseHealthSnapshot:
        """Return classified contract counts and diagnostic table row counts."""
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return WarehouseHealthSnapshot(
                state="locked",
                diagnostics=("PFETL pipeline.lock is active on DuckDB.",),
            )

        def _execute() -> WarehouseHealthSnapshot:
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    layers = query_warehouse_contracts(session_reader)
                    tables, gen_at = query_warehouse_row_counts(session_reader)
                finally:
                    reset_catalog_snapshot(tok)

            if not layers and not tables:
                return WarehouseHealthSnapshot(
                    state="empty",
                    diagnostics=("No meta.m_Data_Contracts or meta.m_Table_Row_Counts found.",),
                )
            return WarehouseHealthSnapshot(
                state="ready",
                total_contracts=sum(layer.contract_count for layer in layers),
                total_tracked_tables=len(tables),
                total_tracked_rows=sum(t.row_count for t in tables),
                generated_at=gen_at,
                layers=layers,
                tables=tables,
            )

        if cache is None:
            return _execute()
        key = build_cache_key("ops_warehouse_health", profile.profile_id, "latest")
        cached = cache.get(key)
        if isinstance(cached, WarehouseHealthSnapshot):
            return cached
        res = _execute()
        cache.set(key, res)
        return res

    @classmethod
    def run_history(
        cls,
        profile: PFETLProfile,
        filters: RunHistoryFilter | None = None,
        *,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> OperationsSnapshot:
        """Return filtered run lifecycle joined 1:1 with sync summaries & warehouse health."""
        eff_filters = filters or RunHistoryFilter()
        cp_reader = ControlPlaneReader(profile)
        if not cp_reader.is_available():
            return OperationsSnapshot(
                state="unavailable",
                diagnostics=("Control Plane SQLite database is unavailable.",),
            )

        warehouse = cls.warehouse_health(profile, catalog_snapshot=catalog_snapshot, cache=cache)

        def _execute() -> OperationsSnapshot:
            runs, statuses, commit_states = query_cp_runs_with_sync(cp_reader, eff_filters)
            if not runs and not statuses:
                return OperationsSnapshot(
                    state="empty",
                    available_statuses=statuses,
                    available_commit_states=commit_states,
                    diagnostics=("No runs recorded in Control Plane cp_runs.",),
                )
            kpis = build_operations_kpis(runs, warehouse)
            return OperationsSnapshot(
                state="ready",
                runs=runs,
                available_statuses=statuses,
                available_commit_states=commit_states,
                kpis=kpis,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            f"ops_run_history:{eff_filters.status}:{eff_filters.commit_state}:{eff_filters.limit}:{eff_filters.start_date}:{eff_filters.end_date}",
            profile.profile_id,
            "latest",
        )
        cached = cache.get(key)
        if isinstance(cached, OperationsSnapshot):
            return cached
        res = _execute()
        cache.set(key, res)
        return res

    @classmethod
    def sync_history(
        cls,
        profile: PFETLProfile,
        filters: RunHistoryFilter | None = None,
        *,
        cache: QueryCache | None = None,
    ) -> tuple[OperationRunRecord, ...]:
        """Return run records joined 1:1 on `run_id` with sync summaries."""
        snap = cls.run_history(profile, filters=filters, cache=cache)
        return snap.runs
