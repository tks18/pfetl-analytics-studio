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

from pfetl_analytics_studio.analytics.investments_models import (
    AllocationDimension,
    AllocationSnapshot,
    InstrumentDetailSnapshot,
    InvestmentPortfolioSnapshot,
)
from pfetl_analytics_studio.analytics.investments_queries import (
    build_investment_kpis,
    query_allocation,
    query_instrument_detail,
    query_portfolio_header,
    query_portfolio_holdings,
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
