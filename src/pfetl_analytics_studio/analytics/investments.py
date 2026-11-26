"""Investment Analytics service backed strictly by Gold marts & Silver instrument dims.

Owns
----
- `InvestmentAnalytics.portfolio_snapshot(...)`
- `InvestmentAnalytics.allocation(...)`
- `InvestmentAnalytics.instrument_detail(...)`

Does not own
------------
- NiceGUI widgets (`pages/investments.py`, `ui/charts_investments.py`) or PFETL methodology.
"""

from __future__ import annotations

from datetime import date

from pfetl_analytics_studio.analytics.investments_evidence_queries import (
    build_realized_recon_kpis,
    query_market_evidence,
    query_realized_events,
    query_reconciliation_events,
    query_trace_lineage,
)
from pfetl_analytics_studio.analytics.investments_models import (
    AllocationDimension,
    AllocationSnapshot,
    InstrumentDetailSnapshot,
    InvestmentPerformanceSnapshot,
    InvestmentPortfolioSnapshot,
    LotDetailSnapshot,
    LotFilter,
    LotPageSnapshot,
    MarketEvidenceSnapshot,
    RealizedEventFilter,
    RealizedReconciliationSnapshot,
    TradeLineageTrace,
)
from pfetl_analytics_studio.analytics.investments_queries import (
    build_investment_kpis,
    build_performance_kpis,
    query_allocation,
    query_fx_exposure,
    query_instrument_detail,
    query_lot_detail,
    query_lot_rows,
    query_lot_summary_and_kpis,
    query_portfolio_header,
    query_portfolio_holdings,
    query_portfolio_performance_history,
    resolve_lot_closing_date,
)
from pfetl_analytics_studio.analytics.overview import OverviewService
from pfetl_analytics_studio.analytics.overview_models import as_date, as_float
from pfetl_analytics_studio.analytics.overview_queries import (
    enrich_meta_counts,
    reset_catalog_snapshot,
    use_catalog_snapshot,
)
from pfetl_analytics_studio.data import (
    CatalogSnapshot,
    CompatibilityReport,
    DuckDBReader,
    QueryCache,
    RunProvenance,
    WarehouseState,
    build_cache_key,
)
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import FilterSet, PeriodContext


class InvestmentAnalytics:
    """Read-only analytical query service for the Investments workspace."""

    @classmethod
    def portfolio_snapshot(
        cls,
        profile: PFETLProfile,
        period_context: PeriodContext | None,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        compatibility: CompatibilityReport | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        filters: FilterSet | None = None,
        cache: QueryCache | None = None,
    ) -> InvestmentPortfolioSnapshot:
        """Return latest portfolio state, class allocation, and ISIN holdings."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        run_health, has_success = OverviewService.load_run_health(
            profile,
            run_id=run_id,
            provenance=provenance,
            compatibility=compatibility,
        )
        if not has_success:
            return InvestmentPortfolioSnapshot(
                state="no_run",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )

        duck_reader = DuckDBReader(profile)
        wh_state = duck_reader.get_state()
        if wh_state == WarehouseState.LOCKED:
            return InvestmentPortfolioSnapshot(
                state="locked",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=("PFETL pipeline.lock is active on DuckDB.",),
            )
        if (
            wh_state != WarehouseState.AVAILABLE
            or period_context is None
            or period_context.is_empty
        ):
            return InvestmentPortfolioSnapshot(
                state="empty",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=("DuckDB warehouse or period boundaries are unavailable.",),
            )

        assert period_context.start_date is not None and period_context.end_date is not None
        active_filters = filters or FilterSet()
        eff_run = run_id or (provenance.run_id if provenance else "latest")
        cache_key = build_cache_key(
            profile.profile_id,
            eff_run,
            "investments_portfolio_v1",
            period=period_context,
            filters=active_filters,
        )
        if cache is not None:
            cached = cache.get(cache_key)
            if isinstance(cached, InvestmentPortfolioSnapshot):
                return cached

        start_dt = period_context.start_date
        end_dt = period_context.end_date

        with duck_reader.session() as session_reader:
            tok = use_catalog_snapshot(catalog_snapshot)
            try:
                header = query_portfolio_header(session_reader, start_dt, end_dt)
                holdings = query_portfolio_holdings(
                    session_reader, start_dt, end_dt, filters=active_filters
                )
                class_alloc = query_allocation(
                    session_reader, AllocationDimension.CLASS, start_dt, end_dt
                )
                run_health = enrich_meta_counts(session_reader, run_health)
            finally:
                reset_catalog_snapshot(tok)

        if header is None and not holdings:
            return InvestmentPortfolioSnapshot(
                state="empty",
                currency_id=currency_id,
                period_label=period_context.label,
                run_health=run_health,
                diagnostics=("No investment portfolio rows match the active period.",),
            )

        as_of = (
            as_date(header.get("Closing_Date"))
            if header
            else (holdings[0].closing_date if holdings else None)
        )
        snap = InvestmentPortfolioSnapshot(
            state="ready",
            currency_id=currency_id,
            as_of_date=as_of,
            period_label=period_context.label,
            kpis=build_investment_kpis(header, holdings),
            holdings=holdings,
            class_allocation=class_alloc,
            total_invested=as_float((header or {}).get("Total_Invested_Value")) or 0.0,
            total_current=as_float((header or {}).get("Total_Current_Value")) or 0.0,
            unrealized_pl=as_float((header or {}).get("Unrealized_PL")) or 0.0,
            run_health=run_health,
        )
        if cache is not None:
            cache.set(cache_key, snap)
        return snap

    @classmethod
    def allocation(
        cls,
        profile: PFETLProfile,
        dimension: AllocationDimension,
        period_context: PeriodContext | None,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> AllocationSnapshot:
        """Query one of the 9 whitelisted Gold investment allocation marts."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        duck_reader = DuckDBReader(profile)
        if (
            duck_reader.get_state() != WarehouseState.AVAILABLE
            or period_context is None
            or period_context.is_empty
        ):
            return AllocationSnapshot(
                state="empty",
                dimension=dimension,
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
            )
        assert period_context.start_date is not None and period_context.end_date is not None
        eff_run = run_id or (provenance.run_id if provenance else "latest")
        cache_key = build_cache_key(
            profile.profile_id,
            eff_run,
            f"investments_alloc_{dimension.value}_v1",
            period=period_context,
            filters=FilterSet(),
        )
        if cache is not None:
            cached = cache.get(cache_key)
            if isinstance(cached, AllocationSnapshot):
                return cached

        with duck_reader.session() as session_reader:
            tok = use_catalog_snapshot(catalog_snapshot)
            try:
                rows = query_allocation(
                    session_reader,
                    dimension,
                    period_context.start_date,
                    period_context.end_date,
                )
            finally:
                reset_catalog_snapshot(tok)

        snap = AllocationSnapshot(
            state="ready" if rows else "empty",
            dimension=dimension,
            currency_id=currency_id,
            as_of_date=rows[0].closing_date if rows else None,
            period_label=period_context.label,
            rows=rows,
        )
        if cache is not None:
            cache.set(cache_key, snap)
        return snap

    @classmethod
    def instrument_detail(
        cls,
        profile: PFETLProfile,
        isin: str,
        period_context: PeriodContext | None,
        *,
        catalog_snapshot: CatalogSnapshot | None = None,
    ) -> InstrumentDetailSnapshot | None:
        """Fetch `silver.dim_instrument_master` metadata and monthly history for one ISIN."""
        duck_reader = DuckDBReader(profile)
        if (
            duck_reader.get_state() != WarehouseState.AVAILABLE
            or period_context is None
            or period_context.is_empty
        ):
            return None
        assert period_context.start_date is not None and period_context.end_date is not None
        with duck_reader.session() as session_reader:
            tok = use_catalog_snapshot(catalog_snapshot)
            try:
                return query_instrument_detail(
                    session_reader,
                    isin,
                    period_context.start_date,
                    period_context.end_date,
                )
            finally:
                reset_catalog_snapshot(tok)

    @classmethod
    def performance(
        cls,
        profile: PFETLProfile,
        dimension: AllocationDimension,
        period_context: PeriodContext | None,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        filters: FilterSet | None = None,
        cache: QueryCache | None = None,
    ) -> InvestmentPerformanceSnapshot:
        """Return portfolio performance history, benchmark, FX exposure, and concentration."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        _, has_success = OverviewService.load_run_health(
            profile,
            run_id=run_id,
            provenance=provenance,
            compatibility=None,
        )
        if not has_success:
            return InvestmentPerformanceSnapshot(
                state="no_run",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                dimension=dimension,
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return InvestmentPerformanceSnapshot(
                state="locked",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                dimension=dimension,
                diagnostics=("PFETL pipeline.lock is active on DuckDB.",),
            )
        if period_context is None or period_context.is_empty:
            return InvestmentPerformanceSnapshot(
                state="empty",
                currency_id=currency_id,
                dimension=dimension,
                diagnostics=("No active period selected.",),
            )
        assert period_context.start_date is not None and period_context.end_date is not None
        active_run_id = run_id or (provenance.run_id if provenance else "unknown")

        def _execute() -> InvestmentPerformanceSnapshot:
            assert period_context.start_date is not None and period_context.end_date is not None
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    header = query_portfolio_header(
                        session_reader, period_context.start_date, period_context.end_date
                    )
                    comp = period_context.comparison
                    prior_header = (
                        query_portfolio_header(
                            session_reader,
                            comp.start_date,
                            comp.end_date,
                        )
                        if comp is not None
                        and not comp.is_empty
                        and comp.start_date is not None
                        and comp.end_date is not None
                        else None
                    )
                    history, concentration = query_portfolio_performance_history(
                        session_reader,
                        period_context.start_date,
                        period_context.end_date,
                    )
                    seg_rows = query_allocation(
                        session_reader,
                        dimension,
                        period_context.start_date,
                        period_context.end_date,
                    )
                    isin_perf_rows = (
                        seg_rows
                        if dimension == AllocationDimension.ISIN
                        else query_allocation(
                            session_reader,
                            AllocationDimension.ISIN,
                            period_context.start_date,
                            period_context.end_date,
                        )
                    )
                    fx_country = query_fx_exposure(
                        session_reader,
                        "investment_by_country",
                        "COUNTRY",
                        period_context.end_date,
                    )
                    fx_currency = query_fx_exposure(
                        session_reader,
                        "investment_by_currency",
                        "CURRENCY_ID",
                        period_context.end_date,
                    )
                finally:
                    reset_catalog_snapshot(tok)

            as_of = (
                (as_date(header.get("Closing_Date")) if header else None)
                or (seg_rows[0].closing_date if seg_rows else None)
                or (history[-1].closing_date if history else None)
            )
            kpis = build_performance_kpis(
                header, concentration, isin_rows=isin_perf_rows, prior_header=prior_header
            )
            return InvestmentPerformanceSnapshot(
                state="ready" if (header or history or seg_rows) else "empty",
                currency_id=currency_id,
                as_of_date=as_of,
                period_label=period_context.label,
                dimension=dimension,
                kpis=kpis,
                history=history,
                concentration=concentration,
                segment_rows=seg_rows,
                isin_perf_rows=isin_perf_rows,
                fx_country_rows=fx_country,
                fx_currency_rows=fx_currency,
            )

        key = build_cache_key(
            "inv_performance",
            profile.profile_id,
            active_run_id,
            period_context,
            filters,
            {"dimension": dimension.value},
        )
        if cache is not None:
            cached = cache.get(key)
            if isinstance(cached, InvestmentPerformanceSnapshot):
                return cached
        snap = _execute()
        if cache is not None:
            cache.set(key, snap)
        return snap

    @classmethod
    def lot_rows(
        cls,
        profile: PFETLProfile,
        lot_filter: LotFilter,
        period_context: PeriodContext | None = None,
        *,
        limit: int = 25,
        offset: int = 0,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> LotPageSnapshot:
        """Return paginated `silver.fact_investment_lot_analytics` rows and KPIs."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        _, has_success = OverviewService.load_run_health(
            profile,
            run_id=run_id,
            provenance=provenance,
            compatibility=None,
        )
        if not has_success:
            return LotPageSnapshot(
                state="no_run",
                currency_id=currency_id,
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return LotPageSnapshot(
                state="locked",
                currency_id=currency_id,
                diagnostics=("PFETL pipeline.lock is active on DuckDB.",),
            )
        end_bound = period_context.end_date if period_context else None
        active_run_id = run_id or (provenance.run_id if provenance else "unknown")

        def _execute() -> LotPageSnapshot:
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    resolved_dt, nearest = resolve_lot_closing_date(
                        session_reader, lot_filter.closing_date, end_bound
                    )
                    if resolved_dt is None:
                        return LotPageSnapshot(
                            state="empty",
                            currency_id=currency_id,
                            nearest_dates=nearest,
                            diagnostics=("No lot snapshots exist for the requested closing date.",),
                        )
                    prior_summary = None
                    comp = period_context.comparison if period_context else None
                    if comp is not None and not comp.is_empty and comp.end_date is not None:
                        prior_dt, _ = resolve_lot_closing_date(session_reader, None, comp.end_date)
                        if prior_dt is not None:
                            prior_summary, _ = query_lot_summary_and_kpis(
                                session_reader, lot_filter, prior_dt
                            )
                    summary, kpis = query_lot_summary_and_kpis(
                        session_reader, lot_filter, resolved_dt, prior_summary=prior_summary
                    )
                    total, rows = query_lot_rows(
                        session_reader,
                        lot_filter,
                        resolved_dt,
                        limit=limit,
                        offset=offset,
                    )
                    _, chart_rows = query_lot_rows(
                        session_reader,
                        lot_filter,
                        resolved_dt,
                        limit=500,
                        offset=0,
                    )
                finally:
                    reset_catalog_snapshot(tok)

            return LotPageSnapshot(
                state="ready" if total > 0 else "empty",
                currency_id=currency_id,
                closing_date=resolved_dt,
                total_count=total,
                kpis=kpis,
                summary=summary,
                rows=rows,
                chart_rows=chart_rows,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            "inv_lots",
            profile.profile_id,
            active_run_id,
            period_context,
            None,
            {
                "filter": lot_filter.model_dump(mode="json"),
                "limit": limit,
                "offset": offset,
            },
        )
        cached = cache.get(key)
        if isinstance(cached, LotPageSnapshot):
            return cached
        snap = _execute()
        cache.set(key, snap)
        return snap

    @classmethod
    def lot_detail(
        cls,
        profile: PFETLProfile,
        lot_id: str,
        closing_date: date,
        *,
        catalog_snapshot: CatalogSnapshot | None = None,
    ) -> LotDetailSnapshot | None:
        """Fetch all 88 columns for one `(Lot_ID, Closing_Date)` snapshot."""
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() != WarehouseState.AVAILABLE:
            return None
        with duck_reader.session() as session_reader:
            tok = use_catalog_snapshot(catalog_snapshot)
            try:
                return query_lot_detail(session_reader, lot_id, closing_date)
            finally:
                reset_catalog_snapshot(tok)

    @classmethod
    def realized_and_reconciliation(
        cls,
        profile: PFETLProfile,
        realized_filter: RealizedEventFilter | None = None,
        *,
        recon_search: str | None = None,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> RealizedReconciliationSnapshot:
        """Return FIFO realized events and broker reconciliation evidence."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        _, has_success = OverviewService.load_run_health(
            profile,
            run_id=run_id,
            provenance=provenance,
            compatibility=None,
        )
        if not has_success:
            return RealizedReconciliationSnapshot(
                state="no_run",
                currency_id=currency_id,
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return RealizedReconciliationSnapshot(
                state="locked",
                currency_id=currency_id,
                diagnostics=("PFETL pipeline.lock is active on DuckDB.",),
            )
        rf = realized_filter or RealizedEventFilter()
        active_run_id = run_id or (provenance.run_id if provenance else "unknown")

        def _execute() -> RealizedReconciliationSnapshot:
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    realized_rows, avail_fys = query_realized_events(session_reader, rf)
                    recon_groups, recon_events = query_reconciliation_events(
                        session_reader, recon_search
                    )
                finally:
                    reset_catalog_snapshot(tok)
            kpis = build_realized_recon_kpis(realized_rows, recon_groups, recon_events)
            return RealizedReconciliationSnapshot(
                state="ready" if (realized_rows or recon_events) else "empty",
                currency_id=currency_id,
                kpis=kpis,
                realized_rows=realized_rows,
                reconciliation_rows=recon_events,
                reconciliation_groups=recon_groups,
                available_fys=avail_fys,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            "inv_realized_recon",
            profile.profile_id,
            active_run_id,
            None,
            None,
            {"filter": rf.model_dump(mode="json"), "recon_search": recon_search or ""},
        )
        cached = cache.get(key)
        if isinstance(cached, RealizedReconciliationSnapshot):
            return cached
        snap = _execute()
        cache.set(key, snap)
        return snap

    @classmethod
    def trace_lineage(
        cls,
        profile: PFETLProfile,
        realized_event_id: str,
        *,
        catalog_snapshot: CatalogSnapshot | None = None,
    ) -> TradeLineageTrace | None:
        """Trace exact IDs: Realized Event -> Sale -> Lot -> Purchase."""
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() != WarehouseState.AVAILABLE:
            return None
        with duck_reader.session() as session_reader:
            tok = use_catalog_snapshot(catalog_snapshot)
            try:
                return query_trace_lineage(session_reader, realized_event_id)
            finally:
                reset_catalog_snapshot(tok)

    @classmethod
    def market_evidence(
        cls,
        profile: PFETLProfile,
        period_context: PeriodContext | None,
        *,
        isin: str | None = None,
        benchmark_id: str | None = None,
        currency_id: str | None = None,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> MarketEvidenceSnapshot:
        """Return Market, Benchmark & FX observations with provenance markers."""
        base_cur = OverviewService.resolve_currency_id(profile, provenance)
        _, has_success = OverviewService.load_run_health(
            profile,
            run_id=run_id,
            provenance=provenance,
            compatibility=None,
        )
        if not has_success:
            return MarketEvidenceSnapshot(
                state="no_run",
                currency_id=base_cur,
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return MarketEvidenceSnapshot(
                state="locked",
                currency_id=base_cur,
                diagnostics=("PFETL pipeline.lock is active on DuckDB.",),
            )
        if period_context is None or period_context.is_empty:
            return MarketEvidenceSnapshot(
                state="empty",
                currency_id=base_cur,
                diagnostics=("No active period selected.",),
            )
        assert period_context.start_date is not None and period_context.end_date is not None
        active_run_id = run_id or (provenance.run_id if provenance else "unknown")

        def _execute() -> MarketEvidenceSnapshot:
            assert period_context.start_date is not None and period_context.end_date is not None
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    snap = query_market_evidence(
                        session_reader,
                        isin=isin,
                        benchmark_id=benchmark_id,
                        currency_id=currency_id,
                        start_date=period_context.start_date,
                        end_date=period_context.end_date,
                    )
                finally:
                    reset_catalog_snapshot(tok)
            return snap.model_copy(update={"currency_id": base_cur})

        if cache is None:
            return _execute()
        key = build_cache_key(
            "inv_market_evidence",
            profile.profile_id,
            active_run_id,
            period_context,
            None,
            {
                "isin": isin or "",
                "benchmark_id": benchmark_id or "",
                "currency_id": currency_id or "",
            },
        )
        cached = cache.get(key)
        if isinstance(cached, MarketEvidenceSnapshot):
            return cached
        snap = _execute()
        cache.set(key, snap)
        return snap
