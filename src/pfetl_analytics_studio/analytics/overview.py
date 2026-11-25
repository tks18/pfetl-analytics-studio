"""Overview cockpit typed query service backed exclusively by Gold, Meta, and Control Plane.

Owns
----
- `OverviewService` producing immutable `OverviewSnapshot` projections across wealth, cashflow,
  income/expense breakups, capital deployment, portfolio returns, asset classes, tax, and FIRE.
- Additive-vs-stock period semantics across Gold marts while respecting active domain filters.
- Period-close vs latest-close portfolio XIRR/active-return dual reporting cleanly without
  violating historical period fidelity.

Does not own
------------
- NiceGUI widgets or Plotly traces (`pages/overview.py`, `ui/charts.py`).
- PFETL financial calculation methodology.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from typing import Any

from pfetl_analytics_studio.analytics.overview_models import (
    AssetClassPoint,
    AttributionBridgeSummary,
    CashflowPoint,
    CategoryBreakupPoint,
    FirePoint,
    NetWorthPoint,
    OverviewBreakupItem,
    OverviewKPI,
    OverviewRunHealth,
    OverviewSnapshot,
    PortfolioPoint,
    WealthAssetSplitPoint,
)
from pfetl_analytics_studio.analytics.overview_models import (
    as_date as _as_date,
)
from pfetl_analytics_studio.analytics.overview_models import (
    as_float as _as_float,
)
from pfetl_analytics_studio.analytics.overview_models import (
    chart_window_start as _chart_window_start,
)
from pfetl_analytics_studio.analytics.overview_queries import (
    enrich_meta_counts,
    query_asset_class_mix,
    query_budget_variance,
    query_chart_series,
    query_core_monthly,
    query_efficiency,
    query_expense_breakup,
    query_fire_latest,
    query_income_breakup,
    query_portfolio_dual,
    query_tax_fy,
    query_wealth_aggregates,
    query_wealth_asset_split,
)
from pfetl_analytics_studio.data import (
    CompatibilityReport,
    CompatibilityStatus,
    ControlPlaneReader,
    ControlPlaneState,
    DuckDBReader,
    PFETLDataAccessError,
    QueryCache,
    RunProvenance,
    WarehouseState,
    build_cache_key,
)
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import FilterSet, MetricFormat, PeriodContext

__all__ = [
    "AssetClassPoint",
    "AttributionBridgeSummary",
    "CashflowPoint",
    "CategoryBreakupPoint",
    "FirePoint",
    "NetWorthPoint",
    "OverviewBreakupItem",
    "OverviewKPI",
    "OverviewRunHealth",
    "OverviewService",
    "OverviewSnapshot",
    "PortfolioPoint",
    "WealthAssetSplitPoint",
]


@dataclass(frozen=True)
class _OverviewQueryBundle:
    """Internal parameter bundle keeping KPI builder signatures compact and clean."""

    end_date: date
    default_fy: str | None
    core_flow: dict[str, Any] | None
    core_stock: dict[str, Any] | None
    eff_flow: dict[str, Any] | None
    eff_latest: dict[str, Any] | None
    budget_summary: dict[str, Any] | None
    inv_period: dict[str, Any] | None
    inv_latest_db: dict[str, Any] | None
    fire_latest: dict[str, Any] | None
    tax_summary: dict[str, Any] | None
    comp_nw: float | None
    comp_cf: float | None
    comp_inc: float | None
    comp_exp: float | None
    comp_label: str | None
    enriched_health: OverviewRunHealth
    wealth_agg: dict[str, Any] | None = None


class OverviewService:
    """Read-only analytical query service building `OverviewSnapshot` from Gold marts."""

    @classmethod
    def get_snapshot(
        cls,
        profile: PFETLProfile,
        period_context: PeriodContext | None,
        *,
        run_id: str | None = None,
        provenance: RunProvenance | None = None,
        compatibility: CompatibilityReport | None = None,
        filters: FilterSet | None = None,
        cache: QueryCache | None = None,
    ) -> OverviewSnapshot:
        """Load cached or freshly queried Overview KPIs, breakups, charts, and health.

        Parameters
        ----------
        profile : PFETLProfile
            Resolved read-only PFETL profile configuration.
        period_context : PeriodContext | None
            Resolved calendar period boundaries and optional comparison period.
        run_id : str | None, optional
            Selected historical run ID or `None` for latest successful run.
        provenance : RunProvenance | None, optional
            Resolved run provenance carrying historical `FinancialRules` payload.
        compatibility : CompatibilityReport | None, optional
            Startup surface discovery report for health strip metadata.
        filters : FilterSet | None, optional
            Active normalized global domain filters (`FY`, income/expense categories, class).
        cache : QueryCache | None, optional
            Bounded session query cache.

        Returns
        -------
        OverviewSnapshot
            Typed cockpit payload handling `ready`, `no_run`, `locked`, and `empty` states cleanly.
        """
        currency_id = cls._resolve_currency_id(profile, provenance)
        run_health, has_completed_run = cls._load_run_health(
            profile,
            run_id=run_id,
            provenance=provenance,
            compatibility=compatibility,
        )

        if not has_completed_run:
            return OverviewSnapshot(
                state="no_run",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=(
                    "No completed PFETL pipeline run exists in Control Plane yet. "
                    "Execute PFETL Core to populate headline analytics.",
                ),
            )

        duck_reader = DuckDBReader(profile)
        wh_state = duck_reader.get_state()
        if wh_state == WarehouseState.LOCKED:
            return OverviewSnapshot(
                state="locked",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=("PFETL pipeline.lock is active on DuckDB.",),
            )
        if wh_state != WarehouseState.AVAILABLE:
            return OverviewSnapshot(
                state="empty",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=(f"Warehouse unavailable ({wh_state.value}).",),
            )

        if period_context is None or period_context.is_empty:
            return OverviewSnapshot(
                state="empty",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=("Resolved period contains no warehouse calendar dates.",),
            )

        assert period_context.start_date is not None and period_context.end_date is not None
        active_filters = filters if filters is not None else FilterSet()
        eff_run = run_id or run_health.latest_run_id or "latest"
        cache_key = build_cache_key(
            profile.profile_id,
            eff_run,
            "overview_snapshot_v3",
            period=period_context,
            filters=active_filters,
        )
        if cache is not None:
            cached = cache.get(cache_key)
            if isinstance(cached, OverviewSnapshot):
                return cached

        snapshot = cls._query_snapshot(
            duck_reader=duck_reader,
            period_context=period_context,
            active_filters=active_filters,
            currency_id=currency_id,
            run_health=run_health,
        )
        if cache is not None and snapshot.state == "ready":
            cache.set(cache_key, snapshot)
        return snapshot

    @staticmethod
    def _resolve_currency_id(
        profile: PFETLProfile,
        provenance: RunProvenance | None,
    ) -> str:
        """Resolve display currency strictly preferring run snapshot rules over disk TOML."""
        if provenance and provenance.rules_payload:
            val = provenance.rules_payload.get("DEFAULT_CURRENCY_ID")
            if isinstance(val, str) and val.strip():
                return val.strip()
        if profile.rules_path.exists():
            try:
                with profile.rules_path.open("rb") as fh:
                    parsed = tomllib.load(fh)
                disk_val = parsed.get("DEFAULT_CURRENCY_ID")
                if isinstance(disk_val, str) and disk_val.strip():
                    return disk_val.strip()
            except (OSError, tomllib.TOMLDecodeError):
                pass
        return "INR_INR"

    @staticmethod
    def _load_run_health(
        profile: PFETLProfile,
        *,
        run_id: str | None,
        provenance: RunProvenance | None,
        compatibility: CompatibilityReport | None,
    ) -> tuple[OverviewRunHealth, bool]:
        """Inspect Control Plane `cp_runs` safely without opening DuckDB."""
        compat_status = (
            compatibility.status.value
            if compatibility is not None
            else CompatibilityStatus.COMPATIBLE.value
        )
        discovered = compatibility.total_discovered if compatibility is not None else 0
        expected = compatibility.total_expected if compatibility is not None else 82

        cp_reader = ControlPlaneReader(profile)
        cp_row: dict[str, Any] | None = None
        if cp_reader.get_state() == ControlPlaneState.AVAILABLE and cp_reader.table_exists(
            "cp_runs"
        ):
            try:
                if run_id:
                    cp_row = cp_reader.fetch_one(
                        """
                        SELECT run_id, status, commit_state, started_at, application_version
                        FROM cp_runs
                        WHERE run_id = ?
                        LIMIT 1;
                        """,
                        (run_id,),
                    )
                if cp_row is None:
                    cp_row = cp_reader.fetch_one(
                        """
                        SELECT run_id, status, commit_state, started_at, application_version
                        FROM cp_runs
                        WHERE UPPER(COALESCE(status, '')) = 'SUCCESS'
                        ORDER BY started_at DESC
                        LIMIT 1;
                        """
                    )
            except PFETLDataAccessError:
                cp_row = None

        if cp_row is not None:
            status_val = str(cp_row.get("status") or "")
            health = OverviewRunHealth(
                latest_run_id=str(cp_row.get("run_id") or "") or None,
                status=status_val or None,
                commit_state=str(cp_row.get("commit_state") or "") or None,
                started_at=str(cp_row.get("started_at") or "")[:19] or None,
                application_version=str(cp_row.get("application_version") or "") or None,
                compatibility_status=compat_status,
                surfaces_discovered=discovered,
                surfaces_expected=expected,
            )
            return health, status_val.upper() == "SUCCESS"

        if provenance is not None and (provenance.status or "").upper() == "SUCCESS":
            health = OverviewRunHealth(
                latest_run_id=provenance.run_id,
                status=provenance.status,
                commit_state=provenance.commit_state,
                started_at=provenance.started_at.isoformat()[:19]
                if provenance.started_at
                else None,
                application_version=provenance.application_version,
                compatibility_status=compat_status,
                surfaces_discovered=discovered,
                surfaces_expected=expected,
            )
            return health, True

        return (
            OverviewRunHealth(
                compatibility_status=compat_status,
                surfaces_discovered=discovered,
                surfaces_expected=expected,
            ),
            False,
        )

    @classmethod
    def _resolve_comparison_deltas(
        cls,
        duck_reader: DuckDBReader,
        *,
        period_context: PeriodContext,
        start_date: date,
        has_core_stock: bool,
    ) -> tuple[float | None, float | None, float | None, float | None, str | None]:
        """Compute comparison or MoM baseline values for NW, Cashflow, Income, and Expense."""
        comp = period_context.comparison
        if comp is not None and not comp.is_empty and comp.start_date and comp.end_date:
            c_flow, c_stock = query_core_monthly(duck_reader, comp.start_date, comp.end_date)
            comp_nw = _as_float(c_stock["nw_mkt"]) if c_stock else None
            comp_cf = _as_float(c_flow["net_cf"]) if c_flow else None
            comp_inc = _as_float(c_flow["inc"]) if c_flow else None
            comp_exp = _as_float(c_flow["exp"]) if c_flow else None
            return comp_nw, comp_cf, comp_inc, comp_exp, f"vs {comp.label}"

        if has_core_stock and duck_reader.table_exists("gold", "core_monthly_fact"):
            prev_row = duck_reader.fetch_one(
                """
                SELECT
                    "Total_Net_Worth_Market" AS nw_mkt,
                    "Net_Cashflow_Month" AS net_cf,
                    "Total_Income" AS inc,
                    "Total_Expense" AS exp
                FROM gold.core_monthly_fact
                WHERE "MONTH_END_DATE" < ?
                ORDER BY "MONTH_END_DATE" DESC
                LIMIT 1;
                """,
                (start_date,),
            )
            if prev_row is not None:
                return (
                    _as_float(prev_row.get("nw_mkt")),
                    _as_float(prev_row.get("net_cf")),
                    _as_float(prev_row.get("inc")),
                    _as_float(prev_row.get("exp")),
                    "MoM",
                )
        return None, None, None, None, None

    @staticmethod
    def _build_primary_kpis(bundle: _OverviewQueryBundle) -> tuple[OverviewKPI, ...]:
        """Build symmetric 3x3 grid covering NW, Income/Expense breakups, Deployment, Portfolio, Tax, FIRE."""
        cs = bundle.core_stock or {}
        cf = bundle.core_flow or {}
        ef_flow = bundle.eff_flow or {}
        ef_lat = bundle.eff_latest or {}
        bud = bundle.budget_summary or {}
        inv = bundle.inv_period or {}
        fire = bundle.fire_latest or {}
        tax = bundle.tax_summary

        # 1. Market Net Worth (with Book Net Worth & Unrealized Cushion)
        nw_val = _as_float(cs.get("nw_mkt"))
        nw_book = _as_float(cs.get("nw_book"))
        nw_as_of = _as_date(cs.get("m_end"))
        nw_delta = (
            ((nw_val - bundle.comp_nw) / abs(bundle.comp_nw))
            if nw_val is not None and bundle.comp_nw is not None and bundle.comp_nw != 0
            else None
        )
        cushion_val = (
            _as_float(cs.get("upl"))
            if _as_float(cs.get("upl")) is not None
            else (nw_val - nw_book if nw_val is not None and nw_book is not None else None)
        )
        nw_card = OverviewKPI(
            kpi_id="market_net_worth",
            label="Market Net Worth",
            value=nw_val,
            format=MetricFormat.CURRENCY,
            subtitle=f"As of {nw_as_of.isoformat()}" if nw_as_of else "No monthly balance",
            source_table="gold.core_monthly_fact",
            as_of_date=nw_as_of,
            drill_route="/wealth",
            delta_value=nw_delta,
            delta_format=MetricFormat.PERCENT,
            delta_label=bundle.comp_label,
            available=nw_val is not None,
            tooltip="Point-in-time market net worth vs book value (non-additive).\nSource: gold.core_monthly_fact",
            breakups=(
                OverviewBreakupItem(label="Book NW", value=nw_book),
                OverviewBreakupItem(label="Cushion", value=cushion_val),
                OverviewBreakupItem(label="Assets", value=_as_float(cs.get("assets_mkt"))),
                OverviewBreakupItem(label="Debt", value=_as_float(cs.get("liab"))),
                OverviewBreakupItem(
                    label="YoY Real",
                    value=_as_float(ef_lat.get("yoy_nw_real")),
                    format=MetricFormat.PERCENT,
                ),
            ),
        )

        # 2. Total Income & Breakup
        inc_val = _as_float(cf.get("inc"))
        inc_delta = (
            ((inc_val - bundle.comp_inc) / abs(bundle.comp_inc))
            if inc_val is not None and bundle.comp_inc is not None and bundle.comp_inc != 0
            else None
        )
        inc_card = OverviewKPI(
            kpi_id="total_income",
            label="Total Income",
            value=inc_val,
            format=MetricFormat.CURRENCY,
            subtitle="Active salary, passive & non-cash EPF",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=_as_date(cf.get("max_end")),
            drill_route="/household",
            delta_value=inc_delta,
            delta_format=MetricFormat.PERCENT,
            delta_label=bundle.comp_label,
            available=inc_val is not None,
            tooltip="Additive household income summed across period.\nSource: gold.core_monthly_fact + cashflow_efficiency",
            breakups=(
                OverviewBreakupItem(label="Active", value=_as_float(ef_flow.get("act_inc"))),
                OverviewBreakupItem(label="Passive", value=_as_float(ef_flow.get("pass_inc"))),
                OverviewBreakupItem(label="Non-Cash", value=_as_float(cf.get("non_cash_inc"))),
            ),
        )

        # 3. Total Expense & Budget Guardrails
        exp_val = _as_float(cf.get("exp"))
        exp_delta = (
            ((exp_val - bundle.comp_exp) / abs(bundle.comp_exp))
            if exp_val is not None and bundle.comp_exp is not None and bundle.comp_exp != 0
            else None
        )
        healthy_flag = bud.get("all_healthy")
        exp_badge = (
            "Budget Healthy"
            if healthy_flag is True
            else ("Overspent" if healthy_flag is False else None)
        )
        exp_card = OverviewKPI(
            kpi_id="total_expense",
            label="Total Expense",
            value=exp_val,
            format=MetricFormat.CURRENCY,
            subtitle="Core vs non-core & budget guardrail",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=_as_date(cf.get("max_end")),
            drill_route="/household",
            delta_value=exp_delta,
            delta_format=MetricFormat.PERCENT,
            delta_label=bundle.comp_label,
            delta_positive_is_good=False,
            status_badge=exp_badge,
            status_tone="success" if healthy_flag is True else "warning",
            available=exp_val is not None,
            tooltip="Additive expenses summed across period.\nSource: gold.cashflow_efficiency_analytics + budget_variance",
            breakups=(
                OverviewBreakupItem(label="Core", value=_as_float(ef_flow.get("core_exp"))),
                OverviewBreakupItem(label="Non-Core", value=_as_float(ef_flow.get("noncore_exp"))),
                OverviewBreakupItem(label="Target", value=_as_float(bud.get("exp_target"))),
            ),
        )

        # 4. Net Cashflow
        cf_val = _as_float(cf.get("net_cf"))
        cf_months = int(cf.get("n_months") or 0)
        cf_as_of = _as_date(cf.get("max_end"))
        cf_delta = (
            (cf_val - bundle.comp_cf) if cf_val is not None and bundle.comp_cf is not None else None
        )
        cf_card = OverviewKPI(
            kpi_id="net_cashflow",
            label="Net Cashflow",
            value=cf_val,
            format=MetricFormat.CURRENCY,
            subtitle=f"{cf_months}m cumulative net flow"
            if cf_months > 1
            else "Income minus expenses",
            source_table="gold.core_monthly_fact",
            as_of_date=cf_as_of,
            drill_route="/household",
            delta_value=cf_delta,
            delta_format=MetricFormat.CURRENCY,
            delta_label=bundle.comp_label,
            available=cf_val is not None,
            tooltip="Additive net cashflow summed across active window.\nSource: gold.core_monthly_fact",
            breakups=(
                OverviewBreakupItem(label="Savings Flow", value=_as_float(cf.get("sav_flow"))),
                OverviewBreakupItem(label="Market Flow", value=_as_float(cf.get("mkt_flow"))),
            ),
        )

        # 5. Savings Rate
        sav_val = _as_float(ef_lat.get("Savings_Rate_Pct"))
        eff_as_of = _as_date(ef_lat.get("m_end"))
        sav_card = OverviewKPI(
            kpi_id="savings_rate",
            label="Savings Rate",
            value=sav_val,
            format=MetricFormat.PERCENT,
            subtitle=f"As of {eff_as_of.isoformat()}" if eff_as_of else "Latest efficiency row",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=eff_as_of,
            drill_route="/household",
            available=sav_val is not None,
            tooltip="Canonical latest monthly savings rate (non-additive).\nSource: gold.cashflow_efficiency_analytics",
            breakups=(
                OverviewBreakupItem(
                    label="Deploy Rate",
                    value=_as_float(ef_lat.get("Investment_Rate_Pct")),
                    format=MetricFormat.PERCENT,
                ),
                OverviewBreakupItem(
                    label="Target SR",
                    value=_as_float(fire.get("Savings_Rate_Required")),
                    format=MetricFormat.PERCENT,
                ),
                OverviewBreakupItem(
                    label="3M Avg SR",
                    value=_as_float(ef_lat.get("Trailing_3M_Avg_Savings_Rate")),
                    format=MetricFormat.PERCENT,
                ),
            ),
        )

        # 6. Investment Deployment & Redemptions
        net_inv = _as_float(ef_flow.get("net_inv"))
        inv_rate = _as_float(ef_lat.get("Investment_Rate_Pct"))
        underfunded = bud.get("inv_underfunded")
        dep_card = OverviewKPI(
            kpi_id="net_investment_flow",
            label="Capital Deployed (Net)",
            value=net_inv,
            format=MetricFormat.CURRENCY,
            subtitle="Gross deployed less redemptions",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=eff_as_of,
            drill_route="/investments",
            status_badge="Underfunded"
            if underfunded is True
            else (f"{inv_rate * 100:.0f}% Rate" if inv_rate is not None else None),
            status_tone="warning" if underfunded is True else "info",
            available=net_inv is not None,
            tooltip="Capital deployed into investments minus redemptions.\nSource: gold.cashflow_efficiency_analytics",
            breakups=(
                OverviewBreakupItem(label="Deployed", value=_as_float(ef_flow.get("deployed"))),
                OverviewBreakupItem(label="Redeemed", value=_as_float(ef_flow.get("redeemed"))),
                OverviewBreakupItem(label="Redemp P&L", value=_as_float(ef_flow.get("redemp_pnl"))),
                OverviewBreakupItem(
                    label="Post-Inv Surplus", value=_as_float(ef_flow.get("post_surplus"))
                ),
            ),
        )

        # 7. Portfolio Value
        port_val = _as_float(inv.get("Total_Current_Value"))
        port_cost = _as_float(inv.get("Total_Invested_Value"))
        port_pnl = _as_float(inv.get("Unrealized_PL"))
        port_as_of = _as_date(inv.get("c_date"))
        port_delta = (
            (port_pnl / port_cost)
            if port_pnl is not None and port_cost is not None and port_cost != 0
            else None
        )
        port_card = OverviewKPI(
            kpi_id="portfolio_value",
            label="Portfolio Value",
            value=port_val,
            format=MetricFormat.CURRENCY,
            subtitle=f"Closing {port_as_of.isoformat()}" if port_as_of else "No portfolio snapshot",
            source_table="gold.investment_by_portfolio",
            as_of_date=port_as_of,
            drill_route="/investments",
            delta_value=port_delta,
            delta_format=MetricFormat.PERCENT,
            delta_label="Unrealized",
            available=port_val is not None,
            tooltip="Point-in-time investment portfolio mark-to-market value.\nSource: gold.investment_by_portfolio",
            breakups=(
                OverviewBreakupItem(label="Cost Basis", value=port_cost),
                OverviewBreakupItem(label="Asset P&L", value=_as_float(inv.get("Asset_PnL"))),
                OverviewBreakupItem(label="FX P&L", value=_as_float(inv.get("Forex_PnL"))),
                OverviewBreakupItem(
                    label="FY Realized", value=_as_float(inv.get("FY_Realized_Net_PnL"))
                ),
            ),
        )

        # 8. Indicative Tax Exposure
        tax_val = _as_float(tax.get("exposure")) if tax else None
        tax_avail = tax is not None and tax_val is not None
        tax_incomplete = bool(tax and not tax.get("is_complete", True))
        tax_fy = str((tax.get("fy_label") if tax else None) or bundle.default_fy or "FY")
        tax_card = OverviewKPI(
            kpi_id="indicative_tax_exposure",
            label="Indicative Tax Exposure",
            value=tax_val if tax_avail else None,
            format=MetricFormat.CURRENCY,
            subtitle=(
                f"FY {tax_fy} | {int(tax.get('event_count') or 0)} tax events"
                if tax_avail and tax
                else f"No tax summary row for FY {tax_fy}"
            ),
            source_table="gold.tax_financial_year_summary",
            as_of_date=bundle.end_date,
            drill_route="/tax",
            status_badge=(
                "Unavailable"
                if not tax_avail
                else ("Incomplete Estimate" if tax_incomplete else "Estimate Ready")
            ),
            status_tone="warning" if (not tax_avail or tax_incomplete) else "success",
            available=tax_avail,
            tooltip="Indicative net tax exposure for associated FY.\nSource: gold.tax_financial_year_summary",
            breakups=(
                OverviewBreakupItem(
                    label="Unrealized",
                    value=_as_float(tax.get("unrealized_tax")) if tax else None,
                ),
                OverviewBreakupItem(
                    label="Harvest Save",
                    value=_as_float(tax.get("harvest_save")) if tax else None,
                ),
                OverviewBreakupItem(
                    label="Tax Credits",
                    value=_as_float(tax.get("tax_credits")) if tax else None,
                ),
            ),
        )

        # 9. FI Coverage
        fi_val = _as_float(fire.get("Current_FI_Coverage_Pct"))
        fi_avail = bool(fire) and fi_val is not None
        fi_as_of = _as_date(fire.get("m_end"))
        fi_p50 = _as_date(fire.get("Projected_FI_Date_P50"))
        fi_prob = _as_float(fire.get("Probability_Of_Success_Pct"))
        fi_sub = f"P50 FI {fi_p50.strftime('%b %Y')}" if fi_p50 else "FIRE trajectory"
        if not fi_avail:
            fi_sub = "No FIRE row in period"
        elif fi_prob is not None:
            fi_sub = f"{fi_sub} | MC Success {fi_prob * 100:.0f}%"
        fi_card = OverviewKPI(
            kpi_id="fi_coverage",
            label="FI Coverage",
            value=fi_val if fi_avail else None,
            format=MetricFormat.PERCENT,
            subtitle=fi_sub,
            source_table="gold.wealth_fire_analytics",
            as_of_date=fi_as_of,
            drill_route="/fire",
            status_badge="Unavailable" if not fi_avail else None,
            status_tone="warning" if not fi_avail else "neutral",
            available=fi_avail,
            tooltip="Current wealth vs Today's FI corpus target.\nSource: gold.wealth_fire_analytics",
            breakups=(
                OverviewBreakupItem(
                    label="Target FI", value=_as_float(fire.get("Target_FI_Today"))
                ),
                OverviewBreakupItem(label="Coast FI", value=_as_float(fire.get("Coast_FI_Today"))),
                OverviewBreakupItem(label="FI Gap", value=_as_float(fire.get("FI_Gap"))),
            ),
        )

        return (
            nw_card,
            inc_card,
            exp_card,
            cf_card,
            sav_card,
            dep_card,
            port_card,
            tax_card,
            fi_card,
        )

    @staticmethod
    def _build_secondary_kpis(bundle: _OverviewQueryBundle) -> tuple[OverviewKPI, ...]:
        """Build 6 secondary return, concentration, liquidity, efficiency, and pipeline cards."""
        ef_lat = bundle.eff_latest or {}
        ef_flow = bundle.eff_flow or {}
        cs = bundle.core_stock or {}
        inv = bundle.inv_period or {}
        inv_db = bundle.inv_latest_db or {}
        bud = bundle.budget_summary or {}
        rh = bundle.enriched_health

        eff_as_of = _as_date(ef_lat.get("m_end"))
        port_as_of = _as_date(inv.get("c_date"))
        db_as_of = _as_date(inv_db.get("c_date"))

        # Period vs Latest Portfolio XIRR + Active Return
        xirr_period = _as_float(inv.get("XIRR"))
        active_period = _as_float(inv.get("Active_Return"))
        xirr_latest = _as_float(inv_db.get("XIRR"))
        active_latest = _as_float(inv_db.get("Active_Return"))
        has_newer_close = db_as_of is not None and port_as_of is not None and db_as_of > port_as_of

        xirr_breakups: list[OverviewBreakupItem] = [
            OverviewBreakupItem(
                label="BM XIRR",
                value=_as_float(inv.get("BM_XIRR")),
                format=MetricFormat.PERCENT,
            ),
            OverviewBreakupItem(
                label="After-Tax",
                value=_as_float(inv.get("After_Tax_XIRR")),
                format=MetricFormat.PERCENT,
            ),
            OverviewBreakupItem(
                label="Max DD",
                value=_as_float(inv.get("Max_Drawdown")),
                format=MetricFormat.PERCENT,
            ),
        ]
        if has_newer_close and db_as_of is not None:
            xirr_breakups.append(
                OverviewBreakupItem(
                    label=f"Latest ({db_as_of.strftime('%d %b')})",
                    value=xirr_latest,
                    format=MetricFormat.PERCENT,
                )
            )
            xirr_breakups.append(
                OverviewBreakupItem(
                    label="Latest Alpha",
                    value=active_latest,
                    format=MetricFormat.PERCENT,
                )
            )

        xirr_card = OverviewKPI(
            kpi_id="portfolio_xirr",
            label="Portfolio XIRR & Alpha",
            value=xirr_period,
            format=MetricFormat.PERCENT,
            subtitle=(
                f"Period {port_as_of.isoformat()} | Latest {db_as_of.isoformat()}"
                if has_newer_close and port_as_of and db_as_of
                else f"As of {port_as_of.isoformat()}"
                if port_as_of
                else "Money-weighted XIRR"
            ),
            source_table="gold.investment_by_portfolio",
            as_of_date=port_as_of,
            drill_route="/investments",
            delta_value=active_period,
            delta_format=MetricFormat.PERCENT,
            delta_label="Active",
            available=xirr_period is not None,
            tooltip="Annualized money-weighted XIRR & active alpha vs benchmark.\nSource: gold.investment_by_portfolio",
            breakups=tuple(xirr_breakups),
        )

        hhi_val = _as_float(inv.get("HHI_Score"))
        eff_holdings = _as_float(inv.get("Effective_Holdings_Count"))
        hhi_card = OverviewKPI(
            kpi_id="portfolio_hhi",
            label="Concentration & LTCG",
            value=hhi_val,
            format=MetricFormat.RATIO,
            subtitle=(
                f"{eff_holdings:.1f} effective positions"
                if eff_holdings is not None
                else "Herfindahl index (0–1)"
            ),
            source_table="gold.investment_by_portfolio",
            as_of_date=port_as_of,
            drill_route="/investments",
            available=hhi_val is not None,
            tooltip="Portfolio concentration (HHI) and LTCG maturity ladder.\nSource: gold.investment_by_portfolio",
            breakups=(
                OverviewBreakupItem(
                    label="Top 5 Wt",
                    value=_as_float(inv.get("Top5_Holdings_Weight")),
                    format=MetricFormat.PERCENT,
                ),
                OverviewBreakupItem(
                    label="LTCG Mature", value=_as_float(inv.get("LTCG_Mature_Value"))
                ),
                OverviewBreakupItem(
                    label="365D Ladder", value=_as_float(inv.get("LTCG_Maturing_365D"))
                ),
            ),
        )

        liq_mos = _as_float(ef_lat.get("Liquidity_Ratio_Months"))
        liq_card = OverviewKPI(
            kpi_id="liquidity_runway",
            label="Liquidity & Runway",
            value=liq_mos,
            format=MetricFormat.NUMBER,
            unit_suffix=" mos",
            subtitle="Core expense coverage by liquid wealth",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=eff_as_of,
            drill_route="/wealth",
            available=liq_mos is not None,
            tooltip="Months of core expense funded by liquid market assets.\nSource: gold.cashflow_efficiency_analytics + budget_variance",
            breakups=(
                OverviewBreakupItem(label="Liquid Bal", value=_as_float(cs.get("liq_mkt"))),
                OverviewBreakupItem(
                    label="0-Inc Run",
                    value=_as_float(bud.get("zero_inc_runway")),
                    format=MetricFormat.NUMBER,
                    unit_suffix=" mos",
                ),
                OverviewBreakupItem(
                    label="Debt/Asset",
                    value=_as_float(ef_lat.get("Debt_to_Asset_Ratio_Pct")),
                    format=MetricFormat.PERCENT,
                ),
            ),
        )

        pass_cov = _as_float(ef_lat.get("Passive_Income_Core_Expense_Coverage_Ratio"))
        pass_card = OverviewKPI(
            kpi_id="passive_coverage",
            label="Passive / Core Cov.",
            value=pass_cov,
            format=MetricFormat.PERCENT,
            subtitle="Passive income vs core household spend",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=eff_as_of,
            drill_route="/household",
            available=pass_cov is not None,
            tooltip="Share of core expenses funded by interest & dividends.\nSource: gold.cashflow_efficiency_analytics",
            breakups=(
                OverviewBreakupItem(label="Interest", value=_as_float(ef_flow.get("int_inc"))),
                OverviewBreakupItem(label="Dividends", value=_as_float(ef_flow.get("div_inc"))),
            ),
        )

        act_dep = _as_float(ef_lat.get("Active_Income_Dependency_Pct"))
        act_card = OverviewKPI(
            kpi_id="active_dependency",
            label="Active Dependency",
            value=act_dep,
            format=MetricFormat.PERCENT,
            subtitle="Share of income from active work",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=eff_as_of,
            drill_route="/household",
            available=act_dep is not None,
            tooltip="Active salary dependence vs inflation rate.\nSource: gold.cashflow_efficiency_analytics + core_monthly_fact",
            breakups=(
                OverviewBreakupItem(
                    label="CPI YoY",
                    value=_as_float(cs.get("cpi_yoy")),
                    format=MetricFormat.PERCENT,
                ),
                OverviewBreakupItem(
                    label="FX Exp",
                    value=_as_float(cs.get("fx_pct")),
                    format=MetricFormat.PERCENT,
                ),
            ),
        )

        health_card = OverviewKPI(
            kpi_id="latest_pfetl_run",
            label="PFETL Pipeline Health",
            value=rh.status or "UNKNOWN",
            format=MetricFormat.NUMBER,
            subtitle=f"{rh.surfaces_discovered}/{rh.surfaces_expected} surfaces verified",
            source_table="cp_runs + meta.m_Table_Row_Counts",
            as_of_date=_as_date(rh.started_at),
            drill_route="/operations",
            status_badge=rh.compatibility_status,
            status_tone=(
                "success"
                if rh.compatibility_status == CompatibilityStatus.COMPATIBLE.value
                else "warning"
            ),
            available=rh.latest_run_id is not None,
            tooltip=f"Latest run {rh.latest_run_id or '—'} ({rh.commit_state or '—'}).",
            breakups=(
                OverviewBreakupItem(
                    label="Run",
                    value=rh.latest_run_id[:8] if rh.latest_run_id else "None",
                    format=MetricFormat.NUMBER,
                ),
                OverviewBreakupItem(
                    label="Rows",
                    value=float(rh.warehouse_rows_total),
                    format=MetricFormat.INTEGER,
                ),
                OverviewBreakupItem(
                    label="Gold",
                    value=float(rh.gold_tables_populated),
                    format=MetricFormat.INTEGER,
                    unit_suffix=" tbls",
                ),
            ),
        )

        return (
            xirr_card,
            hhi_card,
            liq_card,
            pass_card,
            act_card,
            health_card,
            *OverviewService._build_wealth_cards(bundle),
        )

    @staticmethod
    def _build_wealth_cards(bundle: _OverviewQueryBundle) -> tuple[OverviewKPI, ...]:
        """Build Book-vs-Market Cushion, Wealth Origin & ATH, and Liquid vs Illiquid cards."""
        cs = bundle.core_stock or {}
        cf = bundle.core_flow or {}
        wab = bundle.wealth_agg or {}

        nw_mkt = _as_float(cs.get("nw_mkt"))
        nw_book = _as_float(cs.get("nw_book"))
        nw_as_of = _as_date(cs.get("m_end"))
        upl = _as_float(cs.get("upl"))
        if upl is None and nw_mkt is not None and nw_book is not None:
            upl = nw_mkt - nw_book
        open_upl = _as_float(cf.get("open_upl"))
        upl_delta = (upl - open_upl) if upl is not None and open_upl is not None else None
        upl_pct = (upl / abs(nw_book)) if upl is not None and nw_book not in (None, 0) else None

        cushion_card = OverviewKPI(
            kpi_id="book_vs_market",
            label="Book vs Market Cushion",
            value=upl,
            format=MetricFormat.CURRENCY,
            subtitle=(
                f"{upl_pct * 100:.2f}% mark-to-market spread"
                if upl_pct is not None
                else "Unrealized valuation cushion"
            ),
            source_table="gold.core_monthly_fact",
            as_of_date=nw_as_of,
            drill_route="/wealth",
            delta_value=upl_delta,
            delta_format=MetricFormat.CURRENCY,
            delta_label="vs Open",
            status_badge=f"{upl_pct * 100:+.1f}% vs Book" if upl_pct is not None else None,
            status_tone="success" if (upl or 0.0) >= 0 else "warning",
            available=upl is not None,
            tooltip=(
                "Unrealized valuation cushion (Total_Net_Worth_Market - Total_Net_Worth) "
                "split into Asset P&L and Forex P&L.\nSource: gold.core_monthly_fact"
            ),
            breakups=(
                OverviewBreakupItem(label="Book NW", value=nw_book),
                OverviewBreakupItem(label="Asset P&L", value=_as_float(cs.get("asset_pnl"))),
                OverviewBreakupItem(label="FX P&L", value=_as_float(cs.get("fx_pnl"))),
                OverviewBreakupItem(label="Open Cushion", value=open_upl),
            ),
        )

        cum_sav = _as_float(wab.get("wab_cum_sav"))
        ath_val = _as_float(wab.get("wab_ath"))
        wab_mkt = _as_float(wab.get("wab_mkt")) or nw_mkt
        self_funded_pct = (
            (cum_sav / wab_mkt) if cum_sav is not None and wab_mkt not in (None, 0) else None
        )
        compounded_gain = (
            (wab_mkt - cum_sav) if wab_mkt is not None and cum_sav is not None else None
        )
        ath_gap_pct = (
            ((wab_mkt - ath_val) / ath_val)
            if wab_mkt is not None and ath_val not in (None, 0)
            else None
        )
        ath_badge = (
            "At ATH"
            if ath_gap_pct is not None and ath_gap_pct >= -0.005
            else (f"{ath_gap_pct * 100:.1f}% vs ATH" if ath_gap_pct is not None else None)
        )
        origin_card = OverviewKPI(
            kpi_id="wealth_origin",
            label="Wealth Origin & ATH",
            value=self_funded_pct,
            format=MetricFormat.PERCENT,
            subtitle="Self-funded savings vs market compounding",
            source_table="gold.wealth_asset_breakdown",
            as_of_date=_as_date(wab.get("m_end")) or nw_as_of,
            drill_route="/wealth",
            status_badge=ath_badge,
            status_tone="success" if ath_gap_pct is not None and ath_gap_pct >= -0.005 else "info",
            available=self_funded_pct is not None,
            tooltip=(
                "Share of market net worth contributed by cumulative net savings vs cumulative "
                "market compounding, plus distance from aggregate ATH.\n"
                "Source: gold.wealth_asset_breakdown"
            ),
            breakups=(
                OverviewBreakupItem(label="Cum. Savings", value=cum_sav),
                OverviewBreakupItem(label="Market Gain", value=compounded_gain),
                OverviewBreakupItem(label="Peak ATH", value=ath_val),
            ),
        )

        liq_mkt = _as_float(cs.get("liq_mkt"))
        assets_mkt = _as_float(cs.get("assets_mkt"))
        illiquid_mkt = (
            max(assets_mkt - liq_mkt, 0.0)
            if assets_mkt is not None and liq_mkt is not None
            else None
        )
        liq_share = (
            (liq_mkt / assets_mkt) if liq_mkt is not None and assets_mkt not in (None, 0) else None
        )
        liq_mix_card = OverviewKPI(
            kpi_id="liquid_illiquid_mix",
            label="Liquid vs Illiquid",
            value=liq_mkt,
            format=MetricFormat.CURRENCY,
            subtitle="Deployable liquid wealth vs locked-in assets",
            source_table="gold.core_monthly_fact",
            as_of_date=nw_as_of,
            drill_route="/wealth",
            status_badge=f"{liq_share * 100:.0f}% Liquid" if liq_share is not None else None,
            status_tone="info",
            available=liq_mkt is not None,
            tooltip=(
                "Liquid market assets vs illiquid assets (Total_Assets_Market - Liquid_Assets_Market).\n"
                "Source: gold.core_monthly_fact"
            ),
            breakups=(
                OverviewBreakupItem(label="Illiquid", value=illiquid_mkt),
                OverviewBreakupItem(label="Total Assets", value=assets_mkt),
                OverviewBreakupItem(label="Liabilities", value=_as_float(cs.get("liab"))),
            ),
        )

        return (cushion_card, origin_card, liq_mix_card)

    @classmethod
    def _query_snapshot(
        cls,
        *,
        duck_reader: DuckDBReader,
        period_context: PeriodContext,
        active_filters: FilterSet,
        currency_id: str,
        run_health: OverviewRunHealth,
    ) -> OverviewSnapshot:
        """Execute batched Gold & Meta queries to populate KPIs, breakups, and chart series."""
        assert period_context.start_date is not None and period_context.end_date is not None
        start_date = period_context.start_date
        end_date = period_context.end_date
        chart_start, chart_label = _chart_window_start(start_date, end_date)

        enriched_health = enrich_meta_counts(duck_reader, run_health)
        core_flow, core_stock = query_core_monthly(duck_reader, start_date, end_date)
        eff_flow, eff_latest = query_efficiency(duck_reader, start_date, end_date)
        budget_summary = query_budget_variance(duck_reader, start_date, end_date)
        inv_period, inv_latest_db = query_portfolio_dual(duck_reader, start_date, end_date)
        fire_latest = query_fire_latest(duck_reader, start_date, end_date)
        tax_summary = query_tax_fy(
            duck_reader,
            default_fy=period_context.financial_year,
            filters=active_filters,
        )
        wealth_agg = query_wealth_aggregates(duck_reader, end_date)
        comp_nw, comp_cf, comp_inc, comp_exp, comp_label = cls._resolve_comparison_deltas(
            duck_reader,
            period_context=period_context,
            start_date=start_date,
            has_core_stock=core_stock is not None,
        )

        bundle = _OverviewQueryBundle(
            end_date=end_date,
            default_fy=period_context.financial_year,
            core_flow=core_flow,
            core_stock=core_stock,
            eff_flow=eff_flow,
            eff_latest=eff_latest,
            budget_summary=budget_summary,
            inv_period=inv_period,
            inv_latest_db=inv_latest_db,
            fire_latest=fire_latest,
            tax_summary=tax_summary,
            comp_nw=comp_nw,
            comp_cf=comp_cf,
            comp_inc=comp_inc,
            comp_exp=comp_exp,
            comp_label=comp_label,
            enriched_health=enriched_health,
            wealth_agg=wealth_agg,
        )
        primary_kpis = cls._build_primary_kpis(bundle)
        secondary_kpis = cls._build_secondary_kpis(bundle)
        nw_series, cf_series, port_series, fire_series = query_chart_series(
            duck_reader,
            chart_start=chart_start,
            end_date=end_date,
        )
        income_breakup = query_income_breakup(
            duck_reader, start_date, end_date, filters=active_filters
        )
        expense_breakup = query_expense_breakup(
            duck_reader, start_date, end_date, filters=active_filters
        )
        asset_class_mix = query_asset_class_mix(duck_reader, end_date, filters=active_filters)
        wealth_split = query_wealth_asset_split(duck_reader, end_date, filters=active_filters)
        nw_as_of = _as_date(core_stock.get("m_end")) if core_stock else None

        bridge: AttributionBridgeSummary | None = None
        if core_flow and core_stock:
            open_nw = _as_float(core_flow.get("open_nw_mkt"))
            sav_flow = _as_float(core_flow.get("sav_flow"))
            mkt_flow = _as_float(core_flow.get("mkt_flow"))
            res_flow = _as_float(core_flow.get("res_flow")) or 0.0
            close_nw = _as_float(core_stock.get("nw_mkt"))
            close_book = _as_float(core_stock.get("nw_book"))
            upl_val = _as_float(core_stock.get("upl"))
            if upl_val is None and close_nw is not None and close_book is not None:
                upl_val = close_nw - close_book
            if (
                open_nw is not None
                and sav_flow is not None
                and mkt_flow is not None
                and close_nw is not None
            ):
                bridge = AttributionBridgeSummary(
                    opening_net_worth=open_nw,
                    net_savings_flow=sav_flow,
                    market_return_flow=mkt_flow,
                    attribution_residual=res_flow,
                    closing_net_worth=close_nw,
                    closing_book_worth=close_book,
                    unrealized_cushion=upl_val,
                )

        return OverviewSnapshot(
            state="ready",
            currency_id=currency_id,
            as_of_date=nw_as_of or end_date,
            period_label=period_context.label,
            chart_window_label=chart_label,
            primary_kpis=primary_kpis,
            secondary_kpis=secondary_kpis,
            net_worth_series=nw_series,
            cashflow_series=cf_series,
            portfolio_series=port_series,
            fire_series=fire_series,
            income_breakup=income_breakup,
            expense_breakup=expense_breakup,
            asset_class_mix=asset_class_mix,
            wealth_split=wealth_split,
            attribution_bridge=bridge,
            run_health=enriched_health,
            diagnostics=(),
        )
