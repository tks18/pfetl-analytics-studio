"""Tax Analytics service backed by Gold tax marts & Silver tax events.

All outputs carry `indicative=True` metadata for non-statutory disclaimers.
"""

from __future__ import annotations

from pfetl_analytics_studio.analytics.investments import InvestmentAnalytics
from pfetl_analytics_studio.analytics.investments_models import LotFilter
from pfetl_analytics_studio.analytics.overview import OverviewService
from pfetl_analytics_studio.analytics.overview_models import as_float
from pfetl_analytics_studio.analytics.overview_queries import (
    reset_catalog_snapshot,
    use_catalog_snapshot,
)
from pfetl_analytics_studio.analytics.tax_models import (
    TaxEventFilter,
    TaxEventsSnapshot,
    TaxFYSummarySnapshot,
    TaxHarvestingSnapshot,
    TaxIncomeBreakdownSnapshot,
    TaxReconciliationSnapshot,
)
from pfetl_analytics_studio.analytics.tax_queries import (
    build_tax_kpis,
    query_all_fy_summaries,
    query_available_tax_fys,
    query_fy_parameters,
    query_tax_events,
    query_tax_income_breakdown,
    query_tax_reconciliation,
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


class TaxAnalytics:
    """Read-only analytical query service for the Tax workspace."""

    @classmethod
    def fy_summary(
        cls,
        profile: PFETLProfile,
        fy: str | None = None,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> TaxFYSummarySnapshot:
        """Return `gold.tax_financial_year_summary` + `ref_financial_year_parameters`."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        _, has_success = OverviewService.load_run_health(
            profile, run_id=run_id, provenance=provenance, compatibility=None
        )
        if not has_success:
            return TaxFYSummarySnapshot(
                state="no_run",
                currency_id=currency_id,
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return TaxFYSummarySnapshot(
                state="locked",
                currency_id=currency_id,
                diagnostics=("PFETL pipeline.lock is active on DuckDB.",),
            )
        active_run_id = run_id or (provenance.run_id if provenance else "unknown")

        def _execute() -> TaxFYSummarySnapshot:
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    avail_fys = query_available_tax_fys(session_reader)
                    all_rows = query_all_fy_summaries(session_reader)
                    sel_fy = (
                        fy
                        if (fy and fy in avail_fys)
                        else (avail_fys[0] if avail_fys else (fy or ""))
                    )
                    row = next((r for r in all_rows if str(r.get("FY")) == sel_fy), None)
                    params = query_fy_parameters(session_reader, sel_fy) if sel_fy else None
                finally:
                    reset_catalog_snapshot(tok)

            if row is None:
                return TaxFYSummarySnapshot(
                    state="empty",
                    currency_id=currency_id,
                    fy=sel_fy,
                    available_fys=avail_fys,
                    fy_params=params,
                    all_fy_rows=all_rows,
                    diagnostics=(f"No Gold tax summary row found for FY {sel_fy}.",),
                )
            kpis = build_tax_kpis(row)
            return TaxFYSummarySnapshot(
                state="ready",
                currency_id=currency_id,
                fy=sel_fy,
                available_fys=avail_fys,
                indicative=True,
                total_ledger_income=as_float(row.get("Total_Ledger_Income")) or 0.0,
                total_investment_gain_loss=as_float(row.get("Total_Investment_Gain_Loss")) or 0.0,
                total_tax_credits=as_float(row.get("Total_Tax_Credits")) or 0.0,
                total_estimated_investment_tax=as_float(row.get("Total_Estimated_Investment_Tax"))
                or 0.0,
                investment_tax_estimate_count=int(row.get("Investment_Tax_Estimate_Count") or 0),
                investment_tax_estimate_missing_count=int(
                    row.get("Investment_Tax_Estimate_Missing_Count") or 0
                ),
                investment_tax_estimate_complete=bool(row.get("Investment_Tax_Estimate_Complete")),
                total_estimated_ledger_tax=as_float(row.get("Total_Estimated_Ledger_Tax")) or 0.0,
                ledger_tax_estimate_count=int(row.get("Ledger_Tax_Estimate_Count") or 0),
                ledger_tax_estimate_missing_count=int(
                    row.get("Ledger_Tax_Estimate_Missing_Count") or 0
                ),
                ledger_tax_estimate_complete=bool(row.get("Ledger_Tax_Estimate_Complete")),
                total_event_count=int(row.get("Total_Event_Count") or 0),
                ready_count=int(row.get("Ready_Count") or 0),
                check_required_count=int(row.get("Check_Required_Count") or 0),
                indicative_unrealized_tax_liability=as_float(
                    row.get("Indicative_Unrealized_Tax_Liability")
                )
                or 0.0,
                indicative_harvestable_tax_savings=as_float(
                    row.get("Indicative_Harvestable_Tax_Savings")
                )
                or 0.0,
                indicative_net_tax_exposure=as_float(row.get("Indicative_Net_Tax_Exposure")) or 0.0,
                fy_params=params,
                kpis=kpis,
                all_fy_rows=all_rows,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            "tax_fy_summary", profile.profile_id, active_run_id, None, None, {"fy": fy or ""}
        )
        cached = cache.get(key)
        if isinstance(cached, TaxFYSummarySnapshot):
            return cached
        snap = _execute()
        cache.set(key, snap)
        return snap

    @classmethod
    def income_breakdown(
        cls,
        profile: PFETLProfile,
        fy: str,
        *,
        source_type: str | None = None,
        income_head: str | None = None,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> TaxIncomeBreakdownSnapshot:
        """Return `gold.tax_income_breakdown` rows for `fy`."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        _, has_success = OverviewService.load_run_health(
            profile, run_id=run_id, provenance=provenance, compatibility=None
        )
        if not has_success:
            return TaxIncomeBreakdownSnapshot(state="no_run", currency_id=currency_id, fy=fy)
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return TaxIncomeBreakdownSnapshot(state="locked", currency_id=currency_id, fy=fy)
        active_run_id = run_id or (provenance.run_id if provenance else "unknown")

        def _execute() -> TaxIncomeBreakdownSnapshot:
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    rows, srcs, heads = query_tax_income_breakdown(
                        session_reader, fy, source_type=source_type, income_head=income_head
                    )
                finally:
                    reset_catalog_snapshot(tok)
            return TaxIncomeBreakdownSnapshot(
                state="ready" if rows else "empty",
                currency_id=currency_id,
                fy=fy,
                rows=rows,
                available_source_types=srcs,
                available_income_heads=heads,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            "tax_income_breakdown",
            profile.profile_id,
            active_run_id,
            None,
            None,
            {"fy": fy, "source_type": source_type or "", "income_head": income_head or ""},
        )
        cached = cache.get(key)
        if isinstance(cached, TaxIncomeBreakdownSnapshot):
            return cached
        snap = _execute()
        cache.set(key, snap)
        return snap

    @classmethod
    def reconciliation(
        cls,
        profile: PFETLProfile,
        fy: str,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> TaxReconciliationSnapshot:
        """Return `gold.tax_reconciliation` rows for `fy`."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        _, has_success = OverviewService.load_run_health(
            profile, run_id=run_id, provenance=provenance, compatibility=None
        )
        if not has_success:
            return TaxReconciliationSnapshot(state="no_run", currency_id=currency_id, fy=fy)
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return TaxReconciliationSnapshot(state="locked", currency_id=currency_id, fy=fy)
        active_run_id = run_id or (provenance.run_id if provenance else "unknown")

        def _execute() -> TaxReconciliationSnapshot:
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    rows = query_tax_reconciliation(session_reader, fy)
                finally:
                    reset_catalog_snapshot(tok)
            return TaxReconciliationSnapshot(
                state="ready" if rows else "empty",
                currency_id=currency_id,
                fy=fy,
                rows=rows,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            "tax_recon", profile.profile_id, active_run_id, None, None, {"fy": fy}
        )
        cached = cache.get(key)
        if isinstance(cached, TaxReconciliationSnapshot):
            return cached
        snap = _execute()
        cache.set(key, snap)
        return snap

    @classmethod
    def tax_events(
        cls,
        profile: PFETLProfile,
        fy: str,
        filter_spec: TaxEventFilter | None = None,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> TaxEventsSnapshot:
        """Return `silver.fact_tax_events` rows for `fy`."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        _, has_success = OverviewService.load_run_health(
            profile, run_id=run_id, provenance=provenance, compatibility=None
        )
        if not has_success:
            return TaxEventsSnapshot(state="no_run", currency_id=currency_id, fy=fy)
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() == WarehouseState.LOCKED:
            return TaxEventsSnapshot(state="locked", currency_id=currency_id, fy=fy)
        fs = filter_spec or TaxEventFilter()
        active_run_id = run_id or (provenance.run_id if provenance else "unknown")

        def _execute() -> TaxEventsSnapshot:
            with duck_reader.session() as session_reader:
                tok = use_catalog_snapshot(catalog_snapshot)
                try:
                    rows = query_tax_events(session_reader, fy, fs)
                finally:
                    reset_catalog_snapshot(tok)
            return TaxEventsSnapshot(
                state="ready" if rows else "empty",
                currency_id=currency_id,
                fy=fy,
                rows=rows,
            )

        if cache is None:
            return _execute()
        key = build_cache_key(
            "tax_events",
            profile.profile_id,
            active_run_id,
            None,
            None,
            {"fy": fy, "filter": fs.model_dump(mode="json")},
        )
        cached = cache.get(key)
        if isinstance(cached, TaxEventsSnapshot):
            return cached
        snap = _execute()
        cache.set(key, snap)
        return snap

    @classmethod
    def harvesting_snapshot(
        cls,
        profile: PFETLProfile,
        period_context: PeriodContext | None = None,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        catalog_snapshot: CatalogSnapshot | None = None,
        cache: QueryCache | None = None,
    ) -> TaxHarvestingSnapshot:
        """Reuse `InvestmentAnalytics.lot_rows` for indicative harvesting & LTCG maturity."""
        harvest_page = InvestmentAnalytics.lot_rows(
            profile,
            LotFilter(can_harvest_loss=True),
            period_context,
            limit=200,
            offset=0,
            run_id=run_id,
            provenance=provenance,
            catalog_snapshot=catalog_snapshot,
            cache=cache,
        )
        maturing_page = InvestmentAnalytics.lot_rows(
            profile,
            LotFilter(holding_type="STCG", days_to_ltcg_max=90),
            period_context,
            limit=200,
            offset=0,
            run_id=run_id,
            provenance=provenance,
            catalog_snapshot=catalog_snapshot,
            cache=cache,
        )
        if harvest_page.state in ("no_run", "locked"):
            return TaxHarvestingSnapshot(
                state=harvest_page.state, currency_id=harvest_page.currency_id
            )
        return TaxHarvestingSnapshot(
            state="ready" if (harvest_page.rows or maturing_page.rows) else "empty",
            currency_id=harvest_page.currency_id,
            as_of_date=harvest_page.closing_date or maturing_page.closing_date,
            harvest_lots=harvest_page.rows,
            maturing_lots=maturing_page.rows,
            total_harvestable_loss=harvest_page.summary.harvest_loss_value,
            total_tax_if_sold=harvest_page.summary.total_tax_if_sold,
        )
