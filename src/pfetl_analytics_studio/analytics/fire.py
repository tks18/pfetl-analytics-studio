"""Phase 18 FIRE & Simulation Analytics service."""

from __future__ import annotations

from pfetl_analytics_studio.analytics.fire_models import (
    FireSummarySnapshot,
    SimulationHistorySnapshot,
    SimulationRunRecord,
)
from pfetl_analytics_studio.analytics.fire_queries import (
    build_fire_kpis,
    query_cp_simulation_runs,
    query_fire_history,
    query_latest_fire_point,
    query_latest_simulation_meta,
)
from pfetl_analytics_studio.analytics.overview import OverviewService
from pfetl_analytics_studio.analytics.overview_queries import (
    reset_catalog_snapshot,
    use_catalog_snapshot,
)
from pfetl_analytics_studio.data import (
    CatalogSnapshot,
    DuckDBReader,
    QueryCache,
    RunProvenance,
    WarehouseState,
    build_cache_key,
)
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import PeriodContext


class FireAnalytics:
    """Read-only analytical query service for FIRE & Monte Carlo Simulation."""

    @classmethod
    def summary(
        cls,
        profile: PFETLProfile,
        period_context: PeriodContext | None,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> FireSummarySnapshot:
        """Return latest canonical FIRE row, monthly history, KPIs & simulation meta."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        _, has_success = OverviewService.load_run_health(
            profile, run_id=run_id, provenance=provenance, compatibility=None
        )
        if not has_success:
            return FireSummarySnapshot(
                state="no_run",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return FireSummarySnapshot(
                state="locked",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                diagnostics=("PFETL pipeline.lock is active on DuckDB.",),
            )
        if period_context is None or period_context.is_empty:
            return FireSummarySnapshot(
                state="empty",
                currency_id=currency_id,
                diagnostics=("No active period selected.",),
            )
        assert period_context.start_date is not None and period_context.end_date is not None
        active_run_id = run_id or (provenance.run_id if provenance else "unknown")

        def _execute() -> FireSummarySnapshot:
            assert period_context.start_date is not None and period_context.end_date is not None
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    history = query_fire_history(
                        session_reader, period_context.start_date, period_context.end_date
                    )
                    comp = period_context.comparison
                    prior = (
                        query_latest_fire_point(session_reader, comp.end_date)
                        if comp is not None and not comp.is_empty and comp.end_date is not None
                        else None
                    )
                    latest_sim = query_latest_simulation_meta(session_reader)
                finally:
                    reset_catalog_snapshot(tok)

            latest = history[-1] if history else None
            if latest is None:
                return FireSummarySnapshot(
                    state="empty",
                    currency_id=currency_id,
                    period_label=period_context.label,
                    latest_simulation=latest_sim,
                    diagnostics=("No rows found in gold.wealth_fire_analytics.",),
                )
            kpis = build_fire_kpis(latest, prior=prior)
            as_of = latest.month_end_date or latest.month_start_date
            return FireSummarySnapshot(
                state="ready",
                currency_id=currency_id,
                as_of_date=as_of,
                period_label=period_context.label,
                latest=latest,
                history=history,
                kpis=kpis,
                latest_simulation=latest_sim,
            )

        if cache is None:
            return _execute()
        key = build_cache_key("fire_summary", profile.profile_id, active_run_id, period_context)
        cached = cache.get(key)
        if isinstance(cached, FireSummarySnapshot):
            return cached
        snap = _execute()
        cache.set(key, snap)
        return snap

    @classmethod
    def simulation_history(
        cls,
        profile: PFETLProfile,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> SimulationHistorySnapshot:
        """Return `meta.m_Latest_Simulation`, Control Plane `cp_simulation_runs` & rules."""
        _, has_success = OverviewService.load_run_health(
            profile, run_id=run_id, provenance=provenance, compatibility=None
        )
        if not has_success:
            return SimulationHistorySnapshot(
                state="no_run",
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return SimulationHistorySnapshot(
                state="locked",
                diagnostics=("PFETL pipeline.lock is active on DuckDB.",),
            )
        active_run_id = run_id or (provenance.run_id if provenance else "unknown")

        def _execute() -> SimulationHistorySnapshot:
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    latest_meta = query_latest_simulation_meta(session_reader)
                finally:
                    reset_catalog_snapshot(tok)

            cp_runs = query_cp_simulation_runs(profile.control_plane_path)
            runs: tuple[SimulationRunRecord, ...] = cp_runs or (
                (latest_meta,) if latest_meta is not None else ()
            )
            rules = (
                dict(provenance.rules_payload) if provenance and provenance.rules_payload else {}
            )
            diags: list[str] = []
            if latest_meta is None and not cp_runs:
                diags.append("No simulation provenance found in Meta or Control Plane.")
            return SimulationHistorySnapshot(
                state="ready" if (latest_meta is not None or runs) else "empty",
                latest_meta=latest_meta,
                runs=runs,
                rules_assumptions=rules,
                diagnostics=tuple(diags),
            )

        if cache is None:
            return _execute()
        key = build_cache_key("fire_sim_history", profile.profile_id, active_run_id)
        cached = cache.get(key)
        if isinstance(cached, SimulationHistorySnapshot):
            return cached
        snap = _execute()
        cache.set(key, snap)
        return snap
