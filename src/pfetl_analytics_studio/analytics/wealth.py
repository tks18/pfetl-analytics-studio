"""Wealth Analytics service backed strictly by Gold marts & Silver asset dims.

Owns
----
- `WealthAnalytics.summary(...)` producing immutable `WealthSummarySnapshot`.
- `WealthAnalytics.net_worth_attribution(...)` reconciling `Asset_Market_Delta`.
- `WealthAnalytics.asset_breakdown(...)` and `WealthAnalytics.asset_history(...)`.

Does not own
------------
- NiceGUI widgets (`pages/wealth.py`, `ui/charts_wealth.py`) or PFETL methodology.
"""

from __future__ import annotations

from pfetl_analytics_studio.analytics.overview import OverviewService
from pfetl_analytics_studio.analytics.overview_models import chart_window_start
from pfetl_analytics_studio.analytics.overview_queries import (
    enrich_meta_counts,
    reset_catalog_snapshot,
    use_catalog_snapshot,
)
from pfetl_analytics_studio.analytics.wealth_models import (
    AssetBreakdownSnapshot,
    CurrencyExposureRow,
    NetWorthAttributionSnapshot,
    WealthAssetRow,
    WealthMonthlyPoint,
    WealthSummarySnapshot,
)
from pfetl_analytics_studio.analytics.wealth_queries import (
    build_wealth_kpis,
    query_wealth_asset_breakdown,
    query_wealth_asset_history,
    query_wealth_monthly_series,
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


def _build_attribution(
    period_rows: tuple[WealthMonthlyPoint, ...],
    currency_id: str,
    period_label: str,
) -> NetWorthAttributionSnapshot:
    if not period_rows:
        return NetWorthAttributionSnapshot(
            state="empty",
            currency_id=currency_id,
            period_label=period_label,
            diagnostics=("No monthly attribution rows in resolved period.",),
        )
    opening_mkt = (
        period_rows[0].opening_market_assets
        if period_rows[0].opening_market_assets is not None
        else (period_rows[0].total_assets_market or 0.0)
    )
    closing_mkt = (
        period_rows[-1].closing_market_assets
        if period_rows[-1].closing_market_assets is not None
        else (period_rows[-1].total_assets_market or 0.0)
    )
    sav_flow = sum(r.net_savings_flow for r in period_rows)
    mkt_flow = sum(r.market_return_flow for r in period_rows)
    res_flow = sum(r.attribution_residual for r in period_rows)
    delta = sum(r.asset_market_delta for r in period_rows)
    reconciles = abs((sav_flow + mkt_flow + res_flow) - delta) <= 1.0
    return NetWorthAttributionSnapshot(
        state="ready",
        currency_id=currency_id,
        period_label=period_label,
        opening_market_assets=opening_mkt,
        closing_market_assets=closing_mkt,
        asset_market_delta=delta,
        net_savings_flow=sav_flow,
        market_return_flow=mkt_flow,
        attribution_residual=res_flow,
        reconciles_within_tolerance=reconciles,
        monthly_attribution=period_rows,
    )


class WealthAnalytics:
    """Read-only analytical query service for the Wealth workspace."""

    @classmethod
    def summary(
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
    ) -> WealthSummarySnapshot:
        """Compute immutable Wealth snapshot (balance sheet, liquidity, attribution, assets)."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        run_health, has_success = OverviewService.load_run_health(
            profile,
            run_id=run_id,
            provenance=provenance,
            compatibility=compatibility,
        )
        if not has_success:
            return WealthSummarySnapshot(
                state="no_run",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )

        duck_reader = DuckDBReader(profile)
        wh_state = duck_reader.get_state()
        if wh_state == WarehouseState.LOCKED:
            return WealthSummarySnapshot(
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
            return WealthSummarySnapshot(
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
            "wealth_summary_v1",
            period=period_context,
            filters=active_filters,
        )
        if cache is not None:
            cached = cache.get(cache_key)
            if isinstance(cached, WealthSummarySnapshot):
                return cached

        start_dt = period_context.start_date
        end_dt = period_context.end_date
        chart_start, _ = chart_window_start(start_dt, end_dt)

        with duck_reader.session() as session_reader:
            tok = use_catalog_snapshot(catalog_snapshot)
            try:
                all_series = query_wealth_monthly_series(session_reader, chart_start, end_dt)
                assets, group_mix = query_wealth_asset_breakdown(
                    session_reader, start_dt, end_dt, filters=active_filters
                )
                run_health = enrich_meta_counts(session_reader, run_health)
            finally:
                reset_catalog_snapshot(tok)

        period_rows = tuple(r for r in all_series if start_dt <= r.month_end <= end_dt)
        if not period_rows:
            return WealthSummarySnapshot(
                state="empty",
                currency_id=currency_id,
                period_label=period_context.label,
                chart_window_label=f"{chart_start.isoformat()} to {end_dt.isoformat()}",
                run_health=run_health,
                diagnostics=(f"No rows in gold.core_monthly_fact for {period_context.label}.",),
            )

        latest = period_rows[-1]
        prior = (
            all_series[all_series.index(period_rows[0]) - 1]
            if all_series.index(period_rows[0]) > 0
            else None
        )
        kpis = build_wealth_kpis(latest, prior)
        attribution = _build_attribution(period_rows, currency_id, period_context.label)

        liq_mkt = sum(g.liquid_value for g in group_mix)
        tot_mkt = sum(g.market_value for g in group_mix)
        curr_agg: dict[str, dict[str, float]] = {}
        for a in assets:
            b = curr_agg.setdefault(
                a.currency_id,
                {"book": 0.0, "mkt": 0.0, "fx": 0.0, "ap": 0.0, "cnt": 0.0},
            )
            b["book"] += a.closing_balance
            b["mkt"] += (
                a.closing_balance_market
                if a.closing_balance_market is not None
                else a.closing_balance
            )
            b["fx"] += a.closing_forex_pnl or 0.0
            b["ap"] += a.closing_asset_pnl or 0.0
            b["cnt"] += 1.0
        tot_curr_mkt = sum(max(0.0, v["mkt"]) for v in curr_agg.values())
        currency_mix = tuple(
            CurrencyExposureRow(
                currency_id=cid_key,
                book_value=vals["book"],
                market_value=vals["mkt"],
                forex_pnl=vals["fx"],
                asset_pnl=vals["ap"],
                share_pct=(max(0.0, vals["mkt"]) / tot_curr_mkt) if tot_curr_mkt > 0 else None,
                asset_count=int(vals["cnt"]),
                is_foreign=(cid_key != currency_id),
            )
            for cid_key, vals in sorted(curr_agg.items(), key=lambda kv: kv[1]["mkt"], reverse=True)
        )
        asset_snap = AssetBreakdownSnapshot(
            state="ready" if assets else "empty",
            currency_id=currency_id,
            as_of_date=latest.month_end,
            period_label=period_context.label,
            group_mix=group_mix,
            currency_mix=currency_mix,
            assets=assets,
            liquid_total_market=liq_mkt,
            illiquid_total_market=tot_mkt - liq_mkt,
        )

        snap = WealthSummarySnapshot(
            state="ready",
            currency_id=currency_id,
            as_of_date=latest.month_end,
            period_label=period_context.label,
            chart_window_label=f"{chart_start.isoformat()} to {end_dt.isoformat()}",
            kpis=kpis,
            monthly_series=all_series,
            attribution=attribution,
            asset_breakdown=asset_snap,
            run_health=run_health,
        )
        if cache is not None:
            cache.set(cache_key, snap)
        return snap

    @classmethod
    def net_worth_attribution(
        cls,
        profile: PFETLProfile,
        period_context: PeriodContext | None,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        compatibility: CompatibilityReport | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> NetWorthAttributionSnapshot:
        """Return canonical Gold net-worth attribution reconciling `Asset_Market_Delta`."""
        snap = cls.summary(
            profile,
            period_context,
            run_id=run_id,
            provenance=provenance,
            compatibility=compatibility,
            catalog_snapshot=catalog_snapshot,
            cache=cache,
        )
        if snap.attribution is not None:
            return snap.attribution
        return NetWorthAttributionSnapshot(
            state=snap.state,
            currency_id=snap.currency_id,
            period_label=snap.period_label,
            diagnostics=snap.diagnostics,
        )

    @classmethod
    def asset_breakdown(
        cls,
        profile: PFETLProfile,
        period_context: PeriodContext | None,
        *,
        filters: FilterSet | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> AssetBreakdownSnapshot:
        """Return filtered asset-subcategory breakdown for the active period."""
        snap = cls.summary(
            profile,
            period_context,
            filters=filters,
            catalog_snapshot=catalog_snapshot,
            cache=cache,
        )
        if snap.asset_breakdown is not None:
            return snap.asset_breakdown
        return AssetBreakdownSnapshot(
            state=snap.state,
            currency_id=snap.currency_id,
            period_label=snap.period_label,
            diagnostics=snap.diagnostics,
        )

    @classmethod
    def asset_history(
        cls,
        profile: PFETLProfile,
        asset_id: str,
        period_context: PeriodContext | None,
        *,
        catalog_snapshot: CatalogSnapshot | None = None,
    ) -> tuple[WealthAssetRow, ...]:
        """Query monthly balance and drawdown trajectory for one asset subcategory."""
        duck_reader = DuckDBReader(profile)
        if (
            duck_reader.get_state() != WarehouseState.AVAILABLE
            or period_context is None
            or period_context.is_empty
        ):
            return ()
        assert period_context.start_date is not None and period_context.end_date is not None
        chart_start, _ = chart_window_start(period_context.start_date, period_context.end_date)
        with duck_reader.session() as session_reader:
            tok = use_catalog_snapshot(catalog_snapshot)
            try:
                return query_wealth_asset_history(
                    session_reader, asset_id, chart_start, period_context.end_date
                )
            finally:
                reset_catalog_snapshot(tok)
