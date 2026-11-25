"""Household & Income analytics service backed strictly by Gold marts & Silver drill.

Owns
----
- `HouseholdAnalytics.income_summary(...)` producing immutable `HouseholdIncomeSnapshot`.
- `HouseholdAnalytics.income_transactions(...)` producing bounded/paged `IncomeTransactionPage`.

Does not own
------------
- NiceGUI widgets (`pages/household.py`, `ui/charts.py`) or PFETL classification methodology.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pfetl_analytics_studio.analytics.household_expense_queries import (
    query_expense_account_mix,
    query_expense_category_breakdown,
    query_expense_efficiency,
    query_expense_heatmap,
    query_expense_monthly_series,
    query_expense_real_growth,
    query_expense_transactions,
)
from pfetl_analytics_studio.analytics.household_models import (
    ExpenseAccountMixPoint,
    ExpenseCategoryRow,
    ExpenseGroupRow,
    ExpenseTransactionPage,
    HouseholdExpenseSnapshot,
    HouseholdIncomeSnapshot,
    IncomeCategoryRow,
    IncomeGroupRow,
    IncomeTaxabilitySummary,
    IncomeTransactionPage,
)
from pfetl_analytics_studio.analytics.household_queries import (
    query_income_account_mix_and_taxability,
    query_income_budget,
    query_income_category_breakdown,
    query_income_efficiency,
    query_income_monthly_series,
    query_income_transactions,
)
from pfetl_analytics_studio.analytics.overview import OverviewService
from pfetl_analytics_studio.analytics.overview_models import (
    OverviewBreakupItem,
    OverviewKPI,
    OverviewRunHealth,
    as_date,
    as_float,
    chart_window_start,
)
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
from pfetl_analytics_studio.semantic import FilterSet, MetricFormat, PeriodContext


@dataclass(frozen=True)
class _IncomeBundle:
    """Internal parameter bundle for Household Income KPI builders."""

    end_date: date
    eff_flow: dict[str, Any] | None
    eff_latest: dict[str, Any] | None
    budget: dict[str, Any] | None
    comp_inc: float | None
    comp_gross: float | None
    comp_active: float | None
    comp_passive: float | None
    comp_cash: float | None
    comp_label: str | None
    cat_rows: tuple[IncomeCategoryRow, ...]
    group_rows: tuple[IncomeGroupRow, ...]
    gross_pos: float
    deductions: float
    taxability: IncomeTaxabilitySummary


class HouseholdAnalytics:
    """Read-only analytical query service for the Household workspace."""

    @classmethod
    def income_summary(
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
    ) -> HouseholdIncomeSnapshot:
        """Compute immutable Household Income snapshot for the active profile and period."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        run_health, has_success = OverviewService.load_run_health(
            profile,
            run_id=run_id,
            provenance=provenance,
            compatibility=compatibility,
        )
        if not has_success:
            return HouseholdIncomeSnapshot(
                state="no_run",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )

        duck_reader = DuckDBReader(profile)
        wh_state = duck_reader.get_state()
        if wh_state == WarehouseState.LOCKED:
            return HouseholdIncomeSnapshot(
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
            return HouseholdIncomeSnapshot(
                state="empty",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=("Warehouse or resolved period unavailable.",),
            )

        assert period_context.start_date is not None and period_context.end_date is not None
        active_filters = filters if filters is not None else FilterSet()
        eff_run = run_id or run_health.latest_run_id or "latest"
        cache_key = build_cache_key(
            profile.profile_id,
            eff_run,
            "household_income_v2",
            period=period_context,
            filters=active_filters,
        )
        if cache is not None:
            cached = cache.get(cache_key)
            if isinstance(cached, HouseholdIncomeSnapshot):
                return cached

        snapshot = cls._query_income_snapshot(
            duck_reader=duck_reader,
            period_context=period_context,
            active_filters=active_filters,
            currency_id=currency_id,
            run_health=run_health,
            catalog_snapshot=catalog_snapshot,
        )
        if cache is not None and snapshot.state == "ready":
            cache.set(cache_key, snapshot)
        return snapshot

    @staticmethod
    def income_transactions(
        profile: PFETLProfile,
        period_context: PeriodContext | None,
        *,
        catalog_snapshot: CatalogSnapshot | None = None,
        filters: FilterSet | None = None,
        drill_group: str | None = None,
        drill_category: str | None = None,
        flag_filter: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> IncomeTransactionPage:
        """Query bounded Silver income transactions for drill-down."""
        if period_context is None or period_context.is_empty:
            return IncomeTransactionPage(page=page, page_size=page_size)
        assert period_context.start_date is not None and period_context.end_date is not None
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() != WarehouseState.AVAILABLE:
            return IncomeTransactionPage(page=page, page_size=page_size)
        token = use_catalog_snapshot(catalog_snapshot)
        try:
            with duck_reader.session() as db:
                return query_income_transactions(
                    db,
                    period_context.start_date,
                    period_context.end_date,
                    filters=filters if filters is not None else FilterSet(),
                    drill_group=drill_group,
                    drill_category=drill_category,
                    flag_filter=flag_filter,
                    page=page,
                    page_size=page_size,
                )
        finally:
            reset_catalog_snapshot(token)

    @classmethod
    def _query_income_snapshot(
        cls,
        *,
        duck_reader: DuckDBReader,
        period_context: PeriodContext,
        active_filters: FilterSet,
        currency_id: str,
        run_health: OverviewRunHealth,
        catalog_snapshot: CatalogSnapshot | None = None,
    ) -> HouseholdIncomeSnapshot:
        """Execute Gold & Silver queries and assemble the Income cockpit snapshot."""
        assert period_context.start_date is not None and period_context.end_date is not None
        start_date = period_context.start_date
        end_date = period_context.end_date
        chart_start, chart_label = chart_window_start(start_date, end_date)

        token = use_catalog_snapshot(catalog_snapshot)
        try:
            with duck_reader.session() as db:
                enriched_health = enrich_meta_counts(db, run_health)
                eff_flow, eff_latest = query_income_efficiency(db, start_date, end_date)
                budget = query_income_budget(db, start_date, end_date)
                cat_rows, group_rows, gross_pos, deductions = query_income_category_breakdown(
                    db, start_date, end_date, filters=active_filters
                )
                acc_mix, taxability = query_income_account_mix_and_taxability(
                    db, start_date, end_date, filters=active_filters
                )
                monthly_series = query_income_monthly_series(
                    db, chart_start=chart_start, end_date=end_date
                )

                comp_inc: float | None = None
                comp_gross: float | None = None
                comp_active: float | None = None
                comp_passive: float | None = None
                comp_cash: float | None = None
                comp_label: str | None = None
                comp = period_context.comparison
                if comp is not None and not comp.is_empty and comp.start_date and comp.end_date:
                    c_flow, _ = query_income_efficiency(db, comp.start_date, comp.end_date)
                    _, _, c_gross, _ = query_income_category_breakdown(
                        db, comp.start_date, comp.end_date, filters=active_filters
                    )
                    comp_gross = c_gross if c_gross > 0 else None
                    if c_flow is not None:
                        comp_inc = as_float(c_flow.get("total_inc"))
                        comp_active = as_float(c_flow.get("act_inc"))
                        comp_passive = as_float(c_flow.get("pass_inc"))
                        comp_cash = as_float(c_flow.get("cash_inc"))
                        comp_label = "vs Prior Period"
                elif (
                    eff_latest is not None and as_float(eff_latest.get("inc_mom_delta")) is not None
                ):
                    tot = as_float(eff_flow.get("total_inc")) if eff_flow else None
                    mom_d = as_float(eff_latest.get("inc_mom_delta"))
                    if tot is not None and mom_d is not None:
                        comp_inc = tot - mom_d
                        comp_label = "MoM"
        finally:
            reset_catalog_snapshot(token)

        bundle = _IncomeBundle(
            end_date=end_date,
            eff_flow=eff_flow,
            eff_latest=eff_latest,
            budget=budget,
            comp_inc=comp_inc,
            comp_gross=comp_gross,
            comp_active=comp_active,
            comp_passive=comp_passive,
            comp_cash=comp_cash,
            comp_label=comp_label,
            cat_rows=cat_rows,
            group_rows=group_rows,
            gross_pos=gross_pos,
            deductions=deductions,
            taxability=taxability,
        )
        primary_kpis, secondary_kpis = cls._build_income_kpis(bundle)
        as_of = as_date((eff_latest or {}).get("m_end")) or end_date
        net_total = (
            as_float((eff_flow or {}).get("total_inc"))
            if eff_flow and as_float(eff_flow.get("total_inc")) is not None
            else (gross_pos + deductions)
        )
        return HouseholdIncomeSnapshot(
            state="ready",
            currency_id=currency_id,
            as_of_date=as_of,
            period_label=period_context.label,
            chart_window_label=chart_label,
            primary_kpis=primary_kpis,
            secondary_kpis=secondary_kpis,
            monthly_series=monthly_series,
            category_rows=cat_rows,
            group_rows=group_rows,
            account_mix=acc_mix,
            taxability=taxability,
            gross_positive_income=gross_pos,
            salary_deductions=deductions,
            net_income_total=net_total or 0.0,
            run_health=enriched_health,
            diagnostics=(),
        )

    @staticmethod
    def _build_income_kpis(
        bundle: _IncomeBundle,
    ) -> tuple[tuple[OverviewKPI, ...], tuple[OverviewKPI, ...]]:
        """Build 6 primary Income composition cards and 3 resilience/concentration cards."""
        ef = bundle.eff_flow or {}
        el = bundle.eff_latest or {}
        bud = bundle.budget or {}
        as_of = as_date(el.get("m_end")) or bundle.end_date

        net_inc = as_float(ef.get("total_inc"))
        if net_inc is None and (bundle.gross_pos > 0 or bundle.deductions < 0):
            net_inc = bundle.gross_pos + bundle.deductions
        inc_delta = (
            ((net_inc - bundle.comp_inc) / abs(bundle.comp_inc))
            if net_inc is not None and bundle.comp_inc not in (None, 0)
            else None
        )
        total_card = OverviewKPI(
            kpi_id="income_total",
            label="Total Net Income",
            value=net_inc,
            format=MetricFormat.CURRENCY,
            subtitle=f"As of {as_of.isoformat()}" if as_of else "Net household income",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=as_of,
            drill_route="/household",
            delta_value=inc_delta,
            delta_format=MetricFormat.PERCENT,
            delta_label=bundle.comp_label,
            available=net_inc is not None,
            tooltip="Canonical net household income (cash + non-cash - salary deductions).",
            breakups=(
                OverviewBreakupItem(label="Cash", value=as_float(ef.get("cash_inc"))),
                OverviewBreakupItem(label="Non-Cash", value=as_float(ef.get("non_cash_inc"))),
                OverviewBreakupItem(label="3M Avg", value=as_float(el.get("t3m_inc"))),
            ),
        )

        deduct_pct = (
            (abs(bundle.deductions) / bundle.gross_pos)
            if bundle.gross_pos > 0 and bundle.deductions < 0
            else None
        )
        gross_val = bundle.gross_pos if bundle.gross_pos > 0 else net_inc
        gross_delta = (
            ((gross_val - bundle.comp_gross) / abs(bundle.comp_gross))
            if gross_val is not None and bundle.comp_gross not in (None, 0)
            else None
        )
        gross_card = OverviewKPI(
            kpi_id="income_gross_deductions",
            label="Gross vs Deductions",
            value=gross_val,
            format=MetricFormat.CURRENCY,
            subtitle="Gross positive inflows before TDS & PF",
            source_table="gold.cashflow_income_breakdown",
            as_of_date=as_of,
            drill_route="/household",
            delta_value=gross_delta,
            delta_format=MetricFormat.PERCENT,
            delta_label=bundle.comp_label,
            status_badge=f"{deduct_pct * 100:.1f}% Deduct" if deduct_pct is not None else None,
            status_tone="warning" if deduct_pct and deduct_pct > 0.15 else "info",
            available=bundle.gross_pos > 0 or net_inc is not None,
            tooltip="Sum of positive income categories vs negative salary deductions (TDS, PF).",
            breakups=(
                OverviewBreakupItem(label="Deductions", value=bundle.deductions),
                OverviewBreakupItem(label="Net", value=net_inc),
                OverviewBreakupItem(
                    label="Deduct %", value=deduct_pct, format=MetricFormat.PERCENT
                ),
            ),
        )

        act_inc = as_float(ef.get("act_inc"))
        act_share = as_float(el.get("act_share"))
        act_delta = (
            ((act_inc - bundle.comp_active) / abs(bundle.comp_active))
            if act_inc is not None and bundle.comp_active not in (None, 0)
            else None
        )
        top_active = [c for c in bundle.cat_rows if c.is_active and c.amount > 0][:2]
        act_breakups = tuple(
            OverviewBreakupItem(label=c.category_name[:14], value=c.amount) for c in top_active
        )
        active_card = OverviewKPI(
            kpi_id="income_active",
            label="Active Income",
            value=act_inc,
            format=MetricFormat.CURRENCY,
            subtitle="Salary, allowances & earned inflows",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=as_of,
            drill_route="/household",
            delta_value=act_delta,
            delta_format=MetricFormat.PERCENT,
            delta_label=bundle.comp_label,
            status_badge=f"{act_share * 100:.1f}% Share" if act_share is not None else None,
            status_tone="info",
            available=act_inc is not None,
            tooltip="Additive active earned income summed across period.",
            breakups=act_breakups,
        )

        pass_inc = as_float(ef.get("pass_inc"))
        pass_share = as_float(el.get("pass_share"))
        pass_delta = (
            ((pass_inc - bundle.comp_passive) / abs(bundle.comp_passive))
            if pass_inc is not None and bundle.comp_passive not in (None, 0)
            else None
        )
        passive_card = OverviewKPI(
            kpi_id="income_passive",
            label="Passive Income",
            value=pass_inc,
            format=MetricFormat.CURRENCY,
            subtitle="Dividends & interest inflows",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=as_of,
            drill_route="/household",
            delta_value=pass_delta,
            delta_format=MetricFormat.PERCENT,
            delta_label=bundle.comp_label,
            status_badge=f"{pass_share * 100:.1f}% Share" if pass_share is not None else None,
            status_tone="success" if (pass_inc or 0.0) > 0 else "neutral",
            available=pass_inc is not None,
            tooltip="Additive passive income (dividends + interest).",
            breakups=(
                OverviewBreakupItem(label="Dividends", value=as_float(ef.get("div_inc"))),
                OverviewBreakupItem(label="Interest", value=as_float(ef.get("int_inc"))),
                OverviewBreakupItem(
                    label="Core Cov.",
                    value=as_float(el.get("Passive_Income_Core_Expense_Coverage_Ratio")),
                    format=MetricFormat.PERCENT,
                ),
            ),
        )

        actual_bud = as_float(bud.get("actual_inc"))
        target_bud = as_float(bud.get("budget_inc"))
        bud_var = (
            ((actual_bud - target_bud) / abs(target_bud))
            if actual_bud is not None and target_bud not in (None, 0)
            else None
        )
        is_vol = bud.get("is_volatile")
        budget_card = OverviewKPI(
            kpi_id="income_budget",
            label="Budget vs Actual Income",
            value=actual_bud if actual_bud is not None else net_inc,
            format=MetricFormat.CURRENCY,
            subtitle="Cash income vs smoothed budget target",
            source_table="gold.forecast_budget_variance",
            as_of_date=as_of,
            drill_route="/household",
            delta_value=bud_var,
            delta_format=MetricFormat.PERCENT,
            delta_label="vs Target",
            status_badge="Volatile" if is_vol is True else ("Steady" if is_vol is False else None),
            status_tone="warning" if is_vol is True else "success",
            available=actual_bud is not None or net_inc is not None,
            tooltip="Actual cash income compared against FinancialRules smoothed budget target.",
            breakups=(
                OverviewBreakupItem(label="Target", value=target_bud),
                OverviewBreakupItem(
                    label="Variance",
                    value=(actual_bud - target_bud)
                    if actual_bud is not None and target_bud is not None
                    else None,
                ),
                OverviewBreakupItem(label="MoM Δ", value=as_float(el.get("inc_mom_delta"))),
            ),
        )

        tax_sum = bundle.taxability
        ytd_sum = sum(
            (c.cumulative_ytd or 0.0) for c in bundle.cat_rows if c.cumulative_ytd is not None
        )
        tax_breakups: list[OverviewBreakupItem] = [
            OverviewBreakupItem(label="Taxable", value=tax_sum.taxable_amount),
            OverviewBreakupItem(label="Non-Taxable", value=tax_sum.non_taxable_amount),
        ]
        if abs(tax_sum.unclassified_amount) >= 0.01:
            tax_breakups.append(
                OverviewBreakupItem(label="Unclassified", value=tax_sum.unclassified_amount)
            )
        tax_breakups.append(
            OverviewBreakupItem(label="YTD Net", value=ytd_sum if ytd_sum != 0.0 else None)
        )
        tax_card = OverviewKPI(
            kpi_id="income_taxability",
            label="Income Taxability Mix",
            value=tax_sum.taxable_share_pct,
            format=MetricFormat.PERCENT,
            subtitle="Taxable vs exempt/EPF classification",
            source_table="silver.dim_income_subcategory",
            as_of_date=as_of,
            drill_route="/tax",
            available=tax_sum.taxable_share_pct is not None,
            tooltip="Share of classified income tagged taxable vs non-taxable in PFETL dimensions.",
            breakups=tuple(tax_breakups),
        )

        act_dep = as_float(el.get("Active_Income_Dependency_Pct"))
        dep_card = OverviewKPI(
            kpi_id="income_active_dependency",
            label="Active Dependency",
            value=act_dep,
            format=MetricFormat.PERCENT,
            subtitle="Share of income reliant on active work",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=as_of,
            drill_route="/household",
            available=act_dep is not None,
            tooltip="Active income dependency ratio (non-additive).",
            breakups=(
                OverviewBreakupItem(
                    label="Passive/Core",
                    value=as_float(el.get("Passive_Income_Core_Expense_Coverage_Ratio")),
                    format=MetricFormat.PERCENT,
                ),
                OverviewBreakupItem(
                    label="Savings Rate",
                    value=as_float(el.get("Savings_Rate_Pct")),
                    format=MetricFormat.PERCENT,
                ),
            ),
        )

        pos_cats = [c for c in bundle.cat_rows if c.amount > 0]
        top1_share = pos_cats[0].share_pct if pos_cats else None
        top3_share = sum((c.share_pct or 0.0) for c in pos_cats[:3]) if pos_cats else None
        top_grp = bundle.group_rows[0] if bundle.group_rows else None
        conc_card = OverviewKPI(
            kpi_id="income_concentration",
            label="Source Concentration",
            value=top1_share,
            format=MetricFormat.PERCENT,
            subtitle=(
                f"Top: {pos_cats[0].category_name}" if pos_cats else "No positive categories"
            ),
            source_table="gold.cashflow_income_breakdown",
            as_of_date=as_of,
            drill_route="/household",
            available=top1_share is not None,
            tooltip="Concentration of gross positive income in the single largest category.",
            breakups=(
                OverviewBreakupItem(
                    label="Top 3 Share", value=top3_share, format=MetricFormat.PERCENT
                ),
                OverviewBreakupItem(
                    label="Active Cats", value=float(len(pos_cats)), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="Top Group",
                    value=top_grp.share_pct if top_grp else None,
                    format=MetricFormat.PERCENT,
                ),
            ),
        )

        cash_inc = as_float(ef.get("cash_inc"))
        non_cash_inc = as_float(ef.get("non_cash_inc"))
        cash_delta = (
            ((cash_inc - bundle.comp_cash) / abs(bundle.comp_cash))
            if cash_inc is not None and bundle.comp_cash not in (None, 0)
            else None
        )
        cash_share = (
            (cash_inc / net_inc) if cash_inc is not None and net_inc not in (None, 0) else None
        )
        cash_card = OverviewKPI(
            kpi_id="income_cash_mix",
            label="Cash vs Non-Cash",
            value=cash_inc,
            format=MetricFormat.CURRENCY,
            subtitle="Immediately spendable cash vs EPF/accrued",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=as_of,
            drill_route="/household",
            delta_value=cash_delta,
            delta_format=MetricFormat.PERCENT,
            delta_label=bundle.comp_label,
            status_badge=f"{cash_share * 100:.0f}% Cash" if cash_share is not None else None,
            status_tone="info",
            available=cash_inc is not None,
            tooltip="Cash-realized income vs non-cash EPF/accrued inflows.",
            breakups=(
                OverviewBreakupItem(label="Non-Cash", value=non_cash_inc),
                OverviewBreakupItem(label="Net Total", value=net_inc),
            ),
        )

        return (
            (total_card, gross_card, active_card, passive_card, budget_card, tax_card),
            (dep_card, conc_card, cash_card),
        )

    @classmethod
    def expense_summary(
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
    ) -> HouseholdExpenseSnapshot:
        """Compute immutable Household Expense snapshot for the active profile and period."""
        currency_id = OverviewService.resolve_currency_id(profile, provenance)
        run_health, has_success = OverviewService.load_run_health(
            profile,
            run_id=run_id,
            provenance=provenance,
            compatibility=compatibility,
        )
        if not has_success:
            return HouseholdExpenseSnapshot(
                state="no_run",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=("No completed PFETL run found in Control Plane.",),
            )

        duck_reader = DuckDBReader(profile)
        wh_state = duck_reader.get_state()
        if wh_state == WarehouseState.LOCKED:
            return HouseholdExpenseSnapshot(
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
            return HouseholdExpenseSnapshot(
                state="empty",
                currency_id=currency_id,
                period_label=period_context.label if period_context else "",
                run_health=run_health,
                diagnostics=("Warehouse or resolved period unavailable.",),
            )

        assert period_context.start_date is not None and period_context.end_date is not None
        active_filters = filters if filters is not None else FilterSet()
        eff_run = run_id or run_health.latest_run_id or "latest"
        cache_key = build_cache_key(
            profile.profile_id,
            eff_run,
            "household_expense_v1",
            period=period_context,
            filters=active_filters,
        )
        if cache is not None:
            cached = cache.get(cache_key)
            if isinstance(cached, HouseholdExpenseSnapshot):
                return cached

        snapshot = cls._query_expense_snapshot(
            duck_reader=duck_reader,
            period_context=period_context,
            active_filters=active_filters,
            currency_id=currency_id,
            run_health=run_health,
            catalog_snapshot=catalog_snapshot,
        )
        if cache is not None and snapshot.state == "ready":
            cache.set(cache_key, snapshot)
        return snapshot

    @staticmethod
    def expense_transactions(
        profile: PFETLProfile,
        period_context: PeriodContext | None,
        *,
        catalog_snapshot: CatalogSnapshot | None = None,
        filters: FilterSet | None = None,
        drill_group: str | None = None,
        drill_category: str | None = None,
        flag_filter: str | None = None,
        account_filter: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> ExpenseTransactionPage:
        """Query bounded Silver expense transactions for drill-down."""
        if period_context is None or period_context.is_empty:
            return ExpenseTransactionPage(page=page, page_size=page_size)
        assert period_context.start_date is not None and period_context.end_date is not None
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() != WarehouseState.AVAILABLE:
            return ExpenseTransactionPage(page=page, page_size=page_size)
        token = use_catalog_snapshot(catalog_snapshot)
        try:
            with duck_reader.session() as db:
                return query_expense_transactions(
                    db,
                    period_context.start_date,
                    period_context.end_date,
                    filters=filters if filters is not None else FilterSet(),
                    drill_group=drill_group,
                    drill_category=drill_category,
                    flag_filter=flag_filter,
                    account_filter=account_filter,
                    page=page,
                    page_size=page_size,
                )
        finally:
            reset_catalog_snapshot(token)

    @classmethod
    def _query_expense_snapshot(
        cls,
        *,
        duck_reader: DuckDBReader,
        period_context: PeriodContext,
        active_filters: FilterSet,
        currency_id: str,
        run_health: OverviewRunHealth,
        catalog_snapshot: CatalogSnapshot | None = None,
    ) -> HouseholdExpenseSnapshot:
        """Execute Gold & Silver Expense queries and assemble the Expense cockpit snapshot."""
        assert period_context.start_date is not None and period_context.end_date is not None
        start_date = period_context.start_date
        end_date = period_context.end_date
        chart_start, chart_label = chart_window_start(start_date, end_date)

        token = use_catalog_snapshot(catalog_snapshot)
        try:
            with duck_reader.session() as db:
                enriched_health = enrich_meta_counts(db, run_health)
                eff_flow, eff_latest = query_expense_efficiency(db, start_date, end_date)
                real_core_row = query_expense_real_growth(db, start_date, end_date)
                cat_rows, group_rows, gross_pos = query_expense_category_breakdown(
                    db, start_date, end_date, filters=active_filters
                )
                acc_mix = query_expense_account_mix(
                    db, start_date, end_date, filters=active_filters
                )
                monthly_series = query_expense_monthly_series(
                    db, chart_start=chart_start, end_date=end_date
                )
                heatmap = query_expense_heatmap(
                    db,
                    chart_start=chart_start,
                    end_date=end_date,
                    top_groups=tuple(g.category_group for g in group_rows[:8]),
                    filters=active_filters,
                )

                comp_tot: float | None = None
                comp_core: float | None = None
                comp_noncore: float | None = None
                comp_cash: float | None = None
                comp_label: str | None = None
                comp = period_context.comparison
                if comp is not None and not comp.is_empty and comp.start_date and comp.end_date:
                    c_flow, _ = query_expense_efficiency(db, comp.start_date, comp.end_date)
                    if c_flow is not None:
                        comp_tot = as_float(c_flow.get("total_exp"))
                        comp_core = as_float(c_flow.get("core_exp"))
                        comp_noncore = as_float(c_flow.get("noncore_exp"))
                        comp_cash = as_float(c_flow.get("cash_exp"))
                        comp_label = "vs Prior Period"
                elif (
                    eff_latest is not None and as_float(eff_latest.get("exp_mom_delta")) is not None
                ):
                    tot = as_float(eff_flow.get("total_exp")) if eff_flow else None
                    mom_d = as_float(eff_latest.get("exp_mom_delta"))
                    if tot is not None and mom_d is not None:
                        comp_tot = tot - mom_d
                        comp_label = "MoM"
        finally:
            reset_catalog_snapshot(token)

        primary_kpis, secondary_kpis = cls._build_expense_kpis(
            end_date=end_date,
            eff_flow=eff_flow,
            eff_latest=eff_latest,
            real_core_row=real_core_row,
            cat_rows=cat_rows,
            group_rows=group_rows,
            acc_mix=acc_mix,
            gross_pos=gross_pos,
            comp_tot=comp_tot,
            comp_core=comp_core,
            comp_noncore=comp_noncore,
            comp_cash=comp_cash,
            comp_label=comp_label,
        )
        as_of = as_date((eff_latest or {}).get("m_end")) or end_date
        total_sp = as_float((eff_flow or {}).get("total_exp")) or sum(c.amount for c in cat_rows)
        core_sp = as_float((eff_flow or {}).get("core_exp")) or sum(
            c.amount for c in cat_rows if c.is_core
        )
        noncore_sp = as_float((eff_flow or {}).get("noncore_exp")) or sum(
            c.amount for c in cat_rows if not c.is_core
        )
        cash_sp = as_float((eff_flow or {}).get("cash_exp")) or sum(
            (c.cash_amount or 0.0) for c in cat_rows
        )
        nc_sp = as_float((eff_flow or {}).get("non_cash_exp")) or sum(
            (c.non_cash_amount or 0.0) for c in cat_rows
        )
        return HouseholdExpenseSnapshot(
            state="ready",
            currency_id=currency_id,
            as_of_date=as_of,
            period_label=period_context.label,
            chart_window_label=chart_label,
            primary_kpis=primary_kpis,
            secondary_kpis=secondary_kpis,
            monthly_series=monthly_series,
            category_rows=cat_rows,
            group_rows=group_rows,
            account_mix=acc_mix,
            heatmap=heatmap,
            total_spend=total_sp,
            core_spend=core_sp,
            non_core_spend=noncore_sp,
            cash_spend=cash_sp,
            non_cash_spend=nc_sp,
            run_health=enriched_health,
            diagnostics=(),
        )

    @staticmethod
    def _build_expense_kpis(
        *,
        end_date: date,
        eff_flow: dict[str, Any] | None,
        eff_latest: dict[str, Any] | None,
        real_core_row: dict[str, Any] | None,
        cat_rows: tuple[ExpenseCategoryRow, ...],
        group_rows: tuple[ExpenseGroupRow, ...],
        acc_mix: tuple[ExpenseAccountMixPoint, ...],
        gross_pos: float,
        comp_tot: float | None,
        comp_core: float | None,
        comp_noncore: float | None,
        comp_cash: float | None,
        comp_label: str | None,
    ) -> tuple[tuple[OverviewKPI, ...], tuple[OverviewKPI, ...]]:
        """Build 6 primary Expense KPI cards and 3 lifestyle/concentration cards."""
        ef = eff_flow or {}
        el = eff_latest or {}
        rc = real_core_row or {}
        as_of = as_date(el.get("m_end")) or end_date

        def _pct_delta(cur: float | None, prev: float | None) -> float | None:
            if cur is None or prev in (None, 0):
                return None
            return (cur - float(prev)) / abs(float(prev))

        tot_exp = as_float(ef.get("total_exp")) or (
            sum(c.amount for c in cat_rows) if cat_rows else None
        )
        cash_exp = as_float(ef.get("cash_exp"))
        nc_exp = as_float(ef.get("non_cash_exp"))
        total_card = OverviewKPI(
            kpi_id="expense_total",
            label="Total Expense",
            value=tot_exp,
            format=MetricFormat.CURRENCY,
            subtitle=f"As of {as_of.isoformat()}" if as_of else "Total household spend",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=as_of,
            drill_route="/household",
            delta_value=_pct_delta(tot_exp, comp_tot),
            delta_format=MetricFormat.PERCENT,
            delta_label=comp_label,
            delta_positive_is_good=False,
            available=tot_exp is not None,
            tooltip="Total monthly/period household expenses (Cash + Non-Cash Amortizations).",
            breakups=(
                OverviewBreakupItem(label="Cash", value=cash_exp),
                OverviewBreakupItem(label="Non-Cash", value=nc_exp),
                OverviewBreakupItem(label="3M Avg", value=as_float(el.get("t3m_exp"))),
            ),
        )

        core_exp = as_float(ef.get("core_exp"))
        core_share = as_float(el.get("core_share"))
        fixed_core = sum(
            c.amount for c in cat_rows if c.is_core and c.spend_type.lower() == "fixed"
        )
        var_core = sum(c.amount for c in cat_rows if c.is_core and c.spend_type.lower() != "fixed")
        core_card = OverviewKPI(
            kpi_id="expense_core",
            label="Core Essentials",
            value=core_exp,
            format=MetricFormat.CURRENCY,
            subtitle="Essential rent, utilities, groceries & recurring core",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=as_of,
            drill_route="/household",
            delta_value=_pct_delta(core_exp, comp_core),
            delta_format=MetricFormat.PERCENT,
            delta_label=comp_label,
            delta_positive_is_good=False,
            status_badge=f"{core_share * 100:.1f}% Share" if core_share is not None else None,
            status_tone="info",
            available=core_exp is not None,
            tooltip="Additive Core essential spend summed across period.",
            breakups=(
                OverviewBreakupItem(label="Fixed", value=fixed_core if fixed_core else None),
                OverviewBreakupItem(label="Var Core", value=var_core if var_core else None),
                OverviewBreakupItem(label="Real Core", value=as_float(rc.get("real_core"))),
            ),
        )

        noncore_exp = as_float(ef.get("noncore_exp"))
        noncore_share = as_float(el.get("noncore_share"))
        disc_amt = sum(c.amount for c in cat_rows if c.is_discretionary and not c.is_core)
        inv_amt = sum(c.amount for c in cat_rows if c.is_investment)
        noncore_nc = sum((c.non_cash_amount or 0.0) for c in cat_rows if not c.is_core)
        noncore_breakups: list[OverviewBreakupItem] = [
            OverviewBreakupItem(label="Discretionary", value=disc_amt if disc_amt else None),
            OverviewBreakupItem(label="Amort/Dep", value=noncore_nc if noncore_nc else None),
        ]
        if abs(inv_amt) >= 0.01:
            noncore_breakups.append(OverviewBreakupItem(label="Inv-Tagged", value=inv_amt))
        noncore_card = OverviewKPI(
            kpi_id="expense_non_core",
            label="Non-Core & Lifestyle",
            value=noncore_exp,
            format=MetricFormat.CURRENCY,
            subtitle="Discretionary lifestyle, dining, gadgets & travel",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=as_of,
            drill_route="/household",
            delta_value=_pct_delta(noncore_exp, comp_noncore),
            delta_format=MetricFormat.PERCENT,
            delta_label=comp_label,
            delta_positive_is_good=False,
            status_badge=f"{noncore_share * 100:.1f}% Share" if noncore_share is not None else None,
            status_tone="warning" if noncore_share and noncore_share > 0.35 else "info",
            available=noncore_exp is not None,
            tooltip="Additive Non-Core lifestyle & discretionary spend summed across period.",
            breakups=tuple(noncore_breakups),
        )

        cash_share = (
            (cash_exp / tot_exp) if cash_exp is not None and tot_exp not in (None, 0) else None
        )
        cash_card = OverviewKPI(
            kpi_id="expense_cash_mix",
            label="Cash vs Non-Cash Spend",
            value=cash_exp,
            format=MetricFormat.CURRENCY,
            subtitle="Bank/card cash outflows vs asset amortizations",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=as_of,
            drill_route="/household",
            delta_value=_pct_delta(cash_exp, comp_cash),
            delta_format=MetricFormat.PERCENT,
            delta_label=comp_label,
            delta_positive_is_good=False,
            status_badge=f"{cash_share * 100:.0f}% Cash" if cash_share is not None else None,
            status_tone="info",
            available=cash_exp is not None,
            tooltip="Actual cash/card outflows vs non-cash amortizations and depreciations.",
            breakups=(
                OverviewBreakupItem(label="Non-Cash", value=nc_exp),
                OverviewBreakupItem(label="Total", value=tot_exp),
            ),
        )

        real_growth = as_float(rc.get("real_core_growth"))
        cpi_yoy = as_float(rc.get("cpi_yoy"))
        real_card = OverviewKPI(
            kpi_id="expense_real_growth",
            label="Real Core Growth",
            value=real_growth,
            format=MetricFormat.PERCENT,
            subtitle="Inflation-adjusted core spend trajectory",
            source_table="gold.core_monthly_fact",
            as_of_date=as_date(rc.get("m_end")) or as_of,
            drill_route="/household",
            status_badge=f"CPI {cpi_yoy * 100:.1f}%" if cpi_yoy is not None else None,
            status_tone="warning" if real_growth is not None and real_growth > 0.1 else "success",
            available=real_growth is not None,
            tooltip="Canonical Real_Core_Expense_Growth_Pct from gold.core_monthly_fact.",
            breakups=(
                OverviewBreakupItem(label="Nominal Core", value=as_float(rc.get("nominal_core"))),
                OverviewBreakupItem(label="Real Core", value=as_float(rc.get("real_core"))),
            ),
        )

        fixed_amt = sum(c.amount for c in cat_rows if c.spend_type.lower() == "fixed")
        var_amt = sum(c.amount for c in cat_rows if c.spend_type.lower() != "fixed")
        fixed_share = (fixed_amt / gross_pos) if gross_pos > 0 else None
        ytd_sum = sum((c.cumulative_ytd or 0.0) for c in cat_rows if c.cumulative_ytd is not None)
        fv_card = OverviewKPI(
            kpi_id="expense_fixed_variable",
            label="Fixed vs Variable Mix",
            value=fixed_share,
            format=MetricFormat.PERCENT,
            subtitle="Committed fixed bills vs flexible variable spend",
            source_table="gold.cashflow_expense_breakdown",
            as_of_date=as_of,
            drill_route="/household",
            available=fixed_share is not None,
            tooltip="Share of positive spend tagged Spend_Type = Fixed vs Variable.",
            breakups=(
                OverviewBreakupItem(label="Fixed", value=fixed_amt),
                OverviewBreakupItem(label="Variable", value=var_amt),
                OverviewBreakupItem(label="YTD Net", value=ytd_sum if ytd_sum != 0.0 else None),
            ),
        )

        exp_to_nw = as_float(el.get("exp_to_nw"))
        n_months = int(ef.get("n_months") or 1)
        ann_burn = (tot_exp / n_months * 12.0) if tot_exp is not None and n_months > 0 else None
        nw_card = OverviewKPI(
            kpi_id="expense_to_nw",
            label="Expense / Net Worth",
            value=exp_to_nw,
            format=MetricFormat.PERCENT,
            subtitle="Monthly spend relative to market net worth",
            source_table="gold.cashflow_efficiency_analytics",
            as_of_date=as_of,
            drill_route="/wealth",
            available=exp_to_nw is not None,
            tooltip="Canonical Expense_to_NW_Ratio (non-additive).",
            breakups=(
                OverviewBreakupItem(label="Ann. Burn", value=ann_burn),
                OverviewBreakupItem(label="MoM Δ", value=as_float(el.get("exp_mom_delta"))),
                OverviewBreakupItem(
                    label="MoM %",
                    value=as_float(el.get("exp_mom_pct")),
                    format=MetricFormat.PERCENT,
                ),
            ),
        )

        pos_cats = [c for c in cat_rows if c.amount > 0]
        top1_share = pos_cats[0].share_pct if pos_cats else None
        top3_share = sum((c.share_pct or 0.0) for c in pos_cats[:3]) if pos_cats else None
        top_grp = group_rows[0] if group_rows else None
        conc_card = OverviewKPI(
            kpi_id="expense_concentration",
            label="Spend Concentration",
            value=top1_share,
            format=MetricFormat.PERCENT,
            subtitle=f"Top: {pos_cats[0].category_name}" if pos_cats else "No positive spend",
            source_table="gold.cashflow_expense_breakdown",
            as_of_date=as_of,
            drill_route="/household",
            available=top1_share is not None,
            tooltip="Concentration of positive spend in the single largest expense category.",
            breakups=(
                OverviewBreakupItem(
                    label="Top 3 Share", value=top3_share, format=MetricFormat.PERCENT
                ),
                OverviewBreakupItem(
                    label="Active Cats", value=float(len(pos_cats)), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="Top Group",
                    value=top_grp.share_pct if top_grp else None,
                    format=MetricFormat.PERCENT,
                ),
            ),
        )

        top_acc = acc_mix[0] if acc_mix else None
        acc_card = OverviewKPI(
            kpi_id="expense_funding_source",
            label="Top Funding Source",
            value=top_acc.share_pct if top_acc else None,
            format=MetricFormat.PERCENT,
            subtitle=f"Top: {top_acc.account_name}" if top_acc else "No Silver expense txns",
            source_table="silver.fact_expense_transactions",
            as_of_date=as_of,
            drill_route="/household",
            available=top_acc is not None and top_acc.share_pct is not None,
            tooltip="Share of period Silver expenses settled via the top funding account.",
            breakups=(
                OverviewBreakupItem(label="Top Amt", value=top_acc.amount if top_acc else None),
                OverviewBreakupItem(
                    label="Accounts",
                    value=float(len(acc_mix)) if acc_mix else None,
                    format=MetricFormat.INTEGER,
                ),
            ),
        )

        return (
            (total_card, core_card, noncore_card, cash_card, real_card, fv_card),
            (nw_card, conc_card, acc_card),
        )
