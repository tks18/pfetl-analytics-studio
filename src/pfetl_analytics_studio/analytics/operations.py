"""Phase 19 Operations Run History, Sync Health & Warehouse Freshness service."""

from __future__ import annotations

from typing import Literal

from pfetl_analytics_studio.analytics.operations_models import (
    ArtifactEventFilter,
    ArtifactEventsSnapshot,
    OperationRunRecord,
    OperationsSnapshot,
    ProviderRequestFilter,
    ProviderRequestsSnapshot,
    RunFailuresSnapshot,
    RunHistoryFilter,
    SnapshotDiffResult,
    SourceRegistryFilter,
    SourceRegistrySnapshot,
    WarehouseHealthSnapshot,
)
from pfetl_analytics_studio.analytics.operations_queries import (
    build_operations_kpis,
    build_providers_kpis,
    build_sources_kpis,
    compute_structural_json_diff,
    query_artifact_events,
    query_cp_runs_with_sync,
    query_provider_requests,
    query_run_failures,
    query_snapshot_payload,
    query_source_registry,
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

    @classmethod
    def source_registry(
        cls,
        profile: PFETLProfile,
        filters: SourceRegistryFilter | None = None,
        *,
        cache: QueryCache | None = None,
    ) -> SourceRegistrySnapshot:
        """Return `cp_file_registry` lifecycle & independent freshness snapshot."""
        eff = filters or SourceRegistryFilter()
        cp_reader = ControlPlaneReader(profile)
        if not cp_reader.is_available():
            return SourceRegistrySnapshot(
                state="unavailable",
                diagnostics=("Control Plane SQLite database is unavailable.",),
            )

        def _execute() -> SourceRegistrySnapshot:
            files, total_cnt, cats, statuses, cat_counts = query_source_registry(cp_reader, eff)
            if not files and not cats:
                return SourceRegistrySnapshot(
                    state="empty",
                    diagnostics=("No source files recorded in cp_file_registry.",),
                )
            ev_snap = cls.artifact_events(profile, ArtifactEventFilter(limit=200), cache=cache)
            kpis = build_sources_kpis(files, total_cnt, cat_counts, len(ev_snap.events))
            return SourceRegistrySnapshot(
                state="ready",
                files=files,
                total_count=total_cnt,
                available_categories=cats,
                available_statuses=statuses,
                category_counts=cat_counts,
                kpis=kpis,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            f"ops_source_registry:{eff.category}:{eff.sync_status}:{eff.search}:{eff.limit}",
            profile.profile_id,
            "latest",
        )
        cached = cache.get(key)
        if isinstance(cached, SourceRegistrySnapshot):
            return cached
        res = _execute()
        cache.set(key, res)
        return res

    @classmethod
    def artifact_events(
        cls,
        profile: PFETLProfile,
        filters: ArtifactEventFilter | None = None,
        *,
        cache: QueryCache | None = None,
    ) -> ArtifactEventsSnapshot:
        """Return verbatim artifact lifecycle events from `cp_artifact_run_events`."""
        eff = filters or ArtifactEventFilter()
        cp_reader = ControlPlaneReader(profile)
        if not cp_reader.is_available():
            return ArtifactEventsSnapshot(
                state="unavailable",
                diagnostics=("Control Plane SQLite database is unavailable.",),
            )

        def _execute() -> ArtifactEventsSnapshot:
            events, ev_types, conv_types = query_artifact_events(cp_reader, eff)
            if not events and not ev_types:
                return ArtifactEventsSnapshot(
                    state="empty",
                    diagnostics=("No artifact events recorded in cp_artifact_run_events.",),
                )
            return ArtifactEventsSnapshot(
                state="ready",
                events=events,
                available_event_types=ev_types,
                available_convergence=conv_types,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            f"ops_artifact_events:{eff.run_id}:{eff.file_id}:{eff.event_type}:{eff.convergence_result}:{eff.limit}",
            profile.profile_id,
            "latest",
        )
        cached = cache.get(key)
        if isinstance(cached, ArtifactEventsSnapshot):
            return cached
        res = _execute()
        cache.set(key, res)
        return res

    @classmethod
    def provider_requests(
        cls,
        profile: PFETLProfile,
        filters: ProviderRequestFilter | None = None,
        *,
        cache: QueryCache | None = None,
    ) -> ProviderRequestsSnapshot:
        """Return external provider request attempts/status/failures."""
        eff = filters or ProviderRequestFilter()
        cp_reader = ControlPlaneReader(profile)
        if not cp_reader.is_available():
            return ProviderRequestsSnapshot(
                state="unavailable",
                diagnostics=("Control Plane SQLite database is unavailable.",),
            )

        def _execute() -> ProviderRequestsSnapshot:
            reqs, provs, statuses = query_provider_requests(cp_reader, eff)
            if not reqs and not provs:
                return ProviderRequestsSnapshot(
                    state="empty",
                    diagnostics=(
                        "No provider requests recorded in cp_external_provider_requests.",
                    ),
                )
            fail_snap = cls.run_failures(profile, run_id=eff.run_id, cache=cache)
            kpis = build_providers_kpis(reqs, fail_snap.failures)
            return ProviderRequestsSnapshot(
                state="ready",
                requests=reqs,
                available_providers=provs,
                available_statuses=statuses,
                kpis=kpis,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            f"ops_provider_reqs:{eff.run_id}:{eff.provider}:{eff.status}:{eff.limit}",
            profile.profile_id,
            "latest",
        )
        cached = cache.get(key)
        if isinstance(cached, ProviderRequestsSnapshot):
            return cached
        res = _execute()
        cache.set(key, res)
        return res

    @classmethod
    def run_failures(
        cls,
        profile: PFETLProfile,
        *,
        run_id: str | None = None,
        limit: int = 200,
        cache: QueryCache | None = None,
    ) -> RunFailuresSnapshot:
        """Return diagnostic failure records from `cp_run_failures`."""
        cp_reader = ControlPlaneReader(profile)
        if not cp_reader.is_available():
            return RunFailuresSnapshot(
                state="unavailable",
                diagnostics=("Control Plane SQLite database is unavailable.",),
            )

        def _execute() -> RunFailuresSnapshot:
            failures = query_run_failures(cp_reader, run_id=run_id, limit=limit)
            if not failures:
                return RunFailuresSnapshot(
                    state="empty",
                    diagnostics=("No failures recorded in cp_run_failures.",),
                )
            return RunFailuresSnapshot(state="ready", failures=failures)

        if cache is None:
            return _execute()
        key = build_cache_key(f"ops_run_failures:{run_id}:{limit}", profile.profile_id, "latest")
        cached = cache.get(key)
        if isinstance(cached, RunFailuresSnapshot):
            return cached
        res = _execute()
        cache.set(key, res)
        return res

    @classmethod
    def snapshot_diff(
        cls,
        profile: PFETLProfile,
        run_a: str,
        run_b: str,
        kind: str = "rules",
        *,
        cache: QueryCache | None = None,
    ) -> SnapshotDiffResult:
        """Compute deterministic structural JSON diff of canonical snapshots between two runs."""
        eff_kind: Literal["settings", "rules"] = "settings" if kind == "settings" else "rules"
        cp_reader = ControlPlaneReader(profile)
        if not cp_reader.is_available():
            return SnapshotDiffResult(
                state="unavailable",
                kind=eff_kind,
                run_a=run_a,
                run_b=run_b,
                diagnostics=("Control Plane SQLite database is unavailable.",),
            )

        def _execute() -> SnapshotDiffResult:
            rec_a = query_snapshot_payload(cp_reader, run_a, eff_kind)
            rec_b = query_snapshot_payload(cp_reader, run_b, eff_kind)
            if rec_a is None or rec_b is None:
                return SnapshotDiffResult(
                    state="unavailable",
                    kind=eff_kind,
                    run_a=run_a,
                    run_b=run_b,
                    snapshot_a_id=rec_a.snapshot_id if rec_a else "",
                    snapshot_b_id=rec_b.snapshot_id if rec_b else "",
                    raw_text_a=rec_a.canonical_payload_raw if rec_a else "",
                    raw_text_b=rec_b.canonical_payload_raw if rec_b else "",
                    diagnostics=("One or both canonical snapshots were not found.",),
                )
            if rec_a.parsed_payload is None or rec_b.parsed_payload is None:
                return SnapshotDiffResult(
                    state="parse_error",
                    kind=eff_kind,
                    run_a=run_a,
                    run_b=run_b,
                    snapshot_a_id=rec_a.snapshot_id,
                    snapshot_b_id=rec_b.snapshot_id,
                    payload_a=rec_a.parsed_payload,
                    payload_b=rec_b.parsed_payload,
                    raw_text_a=rec_a.canonical_payload_raw,
                    raw_text_b=rec_b.canonical_payload_raw,
                    parse_warning_a=rec_a.parse_warning,
                    parse_warning_b=rec_b.parse_warning,
                    diagnostics=("Malformed snapshot payload; raw text preserved.",),
                )
            diffs = compute_structural_json_diff(rec_a.parsed_payload, rec_b.parsed_payload)
            return SnapshotDiffResult(
                state="ready",
                kind=eff_kind,
                run_a=run_a,
                run_b=run_b,
                snapshot_a_id=rec_a.snapshot_id,
                snapshot_b_id=rec_b.snapshot_id,
                payload_a=rec_a.parsed_payload,
                payload_b=rec_b.parsed_payload,
                raw_text_a=rec_a.canonical_payload_raw,
                raw_text_b=rec_b.canonical_payload_raw,
                diffs=diffs,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            f"ops_snapshot_diff:{eff_kind}:{run_a}:{run_b}", profile.profile_id, "latest"
        )
        cached = cache.get(key)
        if isinstance(cached, SnapshotDiffResult):
            return cached
        res = _execute()
        cache.set(key, res)
        return res
