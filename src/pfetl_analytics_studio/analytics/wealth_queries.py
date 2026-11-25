"""Read-only Gold & Silver SQL query helpers powering Wealth Analytics.

Owns
----
- Parameterized Gold queries against `gold.core_monthly_fact`,
  `gold.cashflow_efficiency_analytics`, and `gold.wealth_asset_breakdown`.
- Joins with `silver.dim_asset_subcategory`, `silver.dim_asset_category`, and
  `silver.fact_opening_balances` for asset labels, liquidity flags, and opening anchors.

Does not own
------------
- NiceGUI widgets or PFETL balance-sheet calculation methodology.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pfetl_analytics_studio.analytics.overview_models import (
    OverviewBreakupItem,
    OverviewKPI,
    as_date,
    as_float,
    slice_filters,
)
from pfetl_analytics_studio.analytics.overview_queries import table_exists, table_has_column
from pfetl_analytics_studio.analytics.wealth_models import (
    AssetGroupMixRow,
    WealthAssetRow,
    WealthMonthlyPoint,
)
from pfetl_analytics_studio.data import DuckDBQueryPort
from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterSet,
    MetricFormat,
    compile_filter_clause,
)

_WEALTH_ASSET_FILTER_MAP: dict[FilterField, str] = {
    FilterField.ASSET_ID: "w.ASSET_SUBCATEGORY_ID",
    FilterField.ASSET_GROUP: "ac.ASSET_GROUP",
    FilterField.IS_LIQUID: "sa.Is_Liquid",
    FilterField.IS_CASH_POOL: "sa.is_cash_pool",
}


def _pick_expr(
    duck_reader: DuckDBQueryPort,
    schema: str,
    table: str,
    candidates: tuple[str, ...],
    alias: str,
    *,
    prefix: str = "",
    fallback: str = "NULL",
) -> str:
    p = f"{prefix}." if prefix else ""
    for col in candidates:
        if table_has_column(duck_reader, schema, table, col):
            return f'{p}"{col}" AS {alias}'
    return f"{fallback} AS {alias}"


def query_wealth_monthly_series(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
) -> tuple[WealthMonthlyPoint, ...]:
    """Query monthly balance sheet, P&L/FX, attribution, and liquidity ratios."""
    if not table_exists(duck_reader, "gold", "core_monthly_fact"):
        return ()

    s_core, t_core = "gold", "core_monthly_fact"
    has_eff = table_exists(duck_reader, "gold", "cashflow_efficiency_analytics")
    eff_join = (
        'LEFT JOIN gold.cashflow_efficiency_analytics e ON c."MONTH_END_DATE" = e."MONTH_END_DATE"'
        if has_eff
        else ""
    )

    def _c(col: str, alias: str, fallback: str = "NULL") -> str:
        return _pick_expr(duck_reader, s_core, t_core, (col,), alias, prefix="c", fallback=fallback)

    def _e(col: str, alias: str) -> str:
        if not has_eff:
            return f"NULL AS {alias}"
        return _pick_expr(
            duck_reader, "gold", "cashflow_efficiency_analytics", (col,), alias, prefix="e"
        )

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            c."MONTH_END_DATE" AS m_end,
            {_c("Total_Net_Worth", "nw_book")},
            {_c("Total_Net_Worth_Market", "nw_mkt")},
            {_c("Total_Assets", "ast_book")},
            {_c("Total_Assets_Market", "ast_mkt")},
            {_c("Total_Liabilities", "liab")},
            {_c("Liquid_Assets", "liq_book")},
            {_c("Liquid_Assets_Market", "liq_mkt")},
            {_c("Opening_Balance_Asset_Market", "op_mkt")},
            {_c("Closing_Balance_Asset_Market", "cl_mkt")},
            {_c("Total_Foreign_Currency_Exposure", "fx_exp")},
            {_c("Foreign_Exposure_Pct", "fx_pct")},
            {_c("Closing_Unrealized_PL", "unr_pl")},
            {_c("Closing_Asset_PnL", "ast_pnl")},
            {_c("Closing_Forex_PnL", "fx_pnl")},
            {_c("Closing_Investment_Book_Value", "inv_book")},
            {_c("Closing_Investment_Market_Value", "inv_mkt")},
            {_c("Closing_Investment_XIRR", "inv_xirr")},
            {_c("Net_Savings_Flow", "sav_flow", fallback="0.0")},
            {_c("Market_Return_Flow", "mkt_flow", fallback="0.0")},
            {_c("Attribution_Residual", "res_flow", fallback="0.0")},
            {_c("Asset_Market_Delta", "mkt_delta", fallback="0.0")},
            {_e("Liquidity_Ratio_Months", "liq_ratio")},
            {_e("Debt_to_Asset_Ratio_Pct", "debt_ratio")},
            {_e("YoY_Net_Worth_Growth_Pct", "yoy_nom")},
            {_e("YoY_Net_Worth_Growth_Pct_Real", "yoy_real")},
            {_e("Emergency_Fund_Coverage", "em_cov")}
        FROM gold.core_monthly_fact c
        {eff_join}
        WHERE c."MONTH_END_DATE" >= ? AND c."MONTH_START_DATE" <= ?
        ORDER BY c."MONTH_END_DATE" ASC;
        """,
        (start_date, end_date),
    )

    points: list[WealthMonthlyPoint] = []
    for row in rows:
        dt = as_date(row.get("m_end"))
        if dt is None:
            continue
        points.append(
            WealthMonthlyPoint(
                month_end=dt,
                book_net_worth=as_float(row.get("nw_book")),
                market_net_worth=as_float(row.get("nw_mkt")),
                total_assets=as_float(row.get("ast_book")),
                total_assets_market=as_float(row.get("ast_mkt")),
                total_liabilities=as_float(row.get("liab")),
                liquid_assets=as_float(row.get("liq_book")),
                liquid_assets_market=as_float(row.get("liq_mkt")),
                opening_market_assets=as_float(row.get("op_mkt")),
                closing_market_assets=as_float(row.get("cl_mkt")),
                foreign_exposure=as_float(row.get("fx_exp")),
                foreign_exposure_pct=as_float(row.get("fx_pct")),
                closing_unrealized_pl=as_float(row.get("unr_pl")),
                closing_asset_pnl=as_float(row.get("ast_pnl")),
                closing_forex_pnl=as_float(row.get("fx_pnl")),
                closing_investment_book=as_float(row.get("inv_book")),
                closing_investment_market=as_float(row.get("inv_mkt")),
                closing_investment_xirr=as_float(row.get("inv_xirr")),
                net_savings_flow=as_float(row.get("sav_flow")) or 0.0,
                market_return_flow=as_float(row.get("mkt_flow")) or 0.0,
                attribution_residual=as_float(row.get("res_flow")) or 0.0,
                asset_market_delta=as_float(row.get("mkt_delta")) or 0.0,
                liquidity_ratio_months=as_float(row.get("liq_ratio")),
                debt_to_asset_pct=as_float(row.get("debt_ratio")),
                yoy_nw_growth_pct=as_float(row.get("yoy_nom")),
                yoy_nw_growth_real_pct=as_float(row.get("yoy_real")),
                emergency_coverage=as_float(row.get("em_cov")),
            )
        )
    return tuple(points)


def query_wealth_asset_breakdown(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
    *,
    filters: FilterSet,
) -> tuple[tuple[WealthAssetRow, ...], tuple[AssetGroupMixRow, ...]]:
    """Query latest asset-subcategory stocks + summed period flows joined with Silver dims."""
    if not table_exists(duck_reader, "gold", "wealth_asset_breakdown"):
        return (), ()

    s_w, t_w = "gold", "wealth_asset_breakdown"
    has_sub = table_exists(duck_reader, "silver", "dim_asset_subcategory")
    has_cat = has_sub and table_exists(duck_reader, "silver", "dim_asset_category")
    has_ob = table_exists(duck_reader, "silver", "fact_opening_balances")

    name_col = (
        "ASSET_NAME"
        if has_sub
        and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "ASSET_NAME")
        else (
            "NAME"
            if has_sub and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "NAME")
            else None
        )
    )
    sub_join = (
        'LEFT JOIN silver.dim_asset_subcategory sa ON w."ASSET_SUBCATEGORY_ID" = sa."UID"'
        if has_sub
        else ""
    )
    cat_join = (
        'LEFT JOIN silver.dim_asset_category ac ON sa."ASSET_GROUP_ID" = ac."UID"'
        if has_cat
        and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "ASSET_GROUP_ID")
        else (
            'LEFT JOIN silver.dim_asset_category ac ON sa."CATEGORY_ID" = ac."UID"'
            if has_cat
            and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "CATEGORY_ID")
            else ""
        )
    )
    ob_join = (
        """
        LEFT JOIN (
            SELECT "ZASSETUID", SUM("ZAMOUNTACCOUNT") AS ob_amt, MIN("ZTXDATESTR") AS ob_dt
            FROM silver.fact_opening_balances
            GROUP BY "ZASSETUID"
        ) ob ON w."ASSET_SUBCATEGORY_ID" = ob."ZASSETUID"
        """
        if has_ob
        else ""
    )

    sub_filter = slice_filters(filters, _WEALTH_ASSET_FILTER_MAP)
    where_extra, f_params = compile_filter_clause(sub_filter, _WEALTH_ASSET_FILTER_MAP)

    asset_name_expr = (
        f'COALESCE(sa."{name_col}", w."ASSET_SUBCATEGORY_ID")'
        if name_col
        else 'w."ASSET_SUBCATEGORY_ID"'
    )
    group_expr = "COALESCE(ac.\"ASSET_GROUP\", 'Unclassified')" if cat_join else "'Unclassified'"
    curr_expr = (
        "COALESCE(sa.\"CURRENCY_ID\", 'INR_INR')"
        if has_sub
        and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "CURRENCY_ID")
        else "'INR_INR'"
    )
    role_expr = (
        "COALESCE(sa.\"ledger_role\", '')"
        if has_sub
        and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "ledger_role")
        else "''"
    )
    act_expr = (
        "COALESCE(sa.\"cashflow_activity_type\", '')"
        if has_sub
        and table_has_column(
            duck_reader, "silver", "dim_asset_subcategory", "cashflow_activity_type"
        )
        else "''"
    )
    liq_expr = (
        'COALESCE(sa."Is_Liquid", FALSE)'
        if has_sub and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "Is_Liquid")
        else "FALSE"
    )
    pool_expr = (
        'COALESCE(sa."is_cash_pool", FALSE)'
        if has_sub
        and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "is_cash_pool")
        else "FALSE"
    )

    def _w(col: str, alias: str, fallback: str = "NULL") -> str:
        return _pick_expr(duck_reader, s_w, t_w, (col,), alias, prefix="w", fallback=fallback)

    rows = duck_reader.fetch_all(
        f"""
        WITH ranked AS (
            SELECT
                w."ASSET_SUBCATEGORY_ID" AS asset_id,
                {asset_name_expr} AS asset_name,
                {group_expr} AS asset_group,
                {curr_expr} AS currency_id,
                {role_expr} AS ledger_role,
                {act_expr} AS cashflow_activity_type,
                {liq_expr} AS is_liquid,
                {pool_expr} AS is_cash_pool,
                w."MONTH_END_DATE" AS m_end,
                {_w("Opening_Balance", "op_bal", fallback="0.0")},
                {_w("Closing_Balance", "cl_bal", fallback="0.0")},
                {_w("Closing_Balance_Market", "cl_mkt")},
                {_w("All_Time_High_Balance", "ath")},
                {_w("Drawdown_From_Peak", "dd")},
                {_w("Closing_Asset_PnL", "ast_pnl")},
                {_w("Closing_Forex_PnL", "fx_pnl")},
                {_w("MoM_Balance_Growth_%", "mom_pct")},
                {_w("YoY_Balance_Growth_%", "yoy_pct")},
                {_w("Organic_Yield_%", "org_yield")},
                {_w("Cumulative_Net_Savings", "cum_sav")},
                {_w("Months_of_Runway", "runway")},
                {"ob.ob_amt" if has_ob else "NULL"} AS ob_amt,
                {"ob.ob_dt" if has_ob else "NULL"} AS ob_dt,
                ROW_NUMBER() OVER (
                    PARTITION BY w."ASSET_SUBCATEGORY_ID"
                    ORDER BY w."MONTH_END_DATE" DESC
                ) AS rn
            FROM gold.wealth_asset_breakdown w
            {sub_join}
            {cat_join}
            {ob_join}
            WHERE w."MONTH_END_DATE" >= ? AND w."MONTH_START_DATE" <= ?
              AND ({where_extra})
        ),
        flows AS (
            SELECT
                w."ASSET_SUBCATEGORY_ID" AS asset_id,
                COALESCE(SUM(w."Net_Cashflow_Month"), 0.0) AS sum_ncf,
                COALESCE(SUM(w."Organic_Growth_Value"), 0.0) AS sum_org
            FROM gold.wealth_asset_breakdown w
            {sub_join}
            {cat_join}
            WHERE w."MONTH_END_DATE" >= ? AND w."MONTH_START_DATE" <= ?
              AND ({where_extra})
            GROUP BY w."ASSET_SUBCATEGORY_ID"
        )
        SELECT r.*, f.sum_ncf, f.sum_org
        FROM ranked r
        LEFT JOIN flows f ON r.asset_id = f.asset_id
        WHERE r.rn = 1
        ORDER BY COALESCE(r.cl_mkt, r.cl_bal) DESC;
        """,
        (start_date, end_date, *f_params, start_date, end_date, *f_params),
    )

    assets: list[WealthAssetRow] = []
    grp_agg: dict[str, dict[str, Any]] = {}
    for row in rows:
        dt = as_date(row.get("m_end"))
        if dt is None:
            continue
        cl_book = as_float(row.get("cl_bal")) or 0.0
        cl_mkt = as_float(row.get("cl_mkt"))
        is_liq = bool(row.get("is_liquid"))
        grp = str(row.get("asset_group") or "Unclassified")
        assets.append(
            WealthAssetRow(
                asset_id=str(row.get("asset_id") or ""),
                asset_name=str(row.get("asset_name") or row.get("asset_id") or ""),
                asset_group=grp,
                currency_id=str(row.get("currency_id") or "INR_INR"),
                ledger_role=str(row.get("ledger_role") or ""),
                cashflow_activity_type=str(row.get("cashflow_activity_type") or ""),
                is_liquid=is_liq,
                is_cash_pool=bool(row.get("is_cash_pool")),
                month_end=dt,
                opening_balance=as_float(row.get("op_bal")) or 0.0,
                closing_balance=cl_book,
                closing_balance_market=cl_mkt,
                all_time_high=as_float(row.get("ath")),
                drawdown_from_peak=as_float(row.get("dd")),
                closing_asset_pnl=as_float(row.get("ast_pnl")),
                closing_forex_pnl=as_float(row.get("fx_pnl")),
                mom_growth_pct=as_float(row.get("mom_pct")),
                yoy_growth_pct=as_float(row.get("yoy_pct")),
                organic_yield_pct=as_float(row.get("org_yield")),
                cumulative_net_savings=as_float(row.get("cum_sav")),
                months_of_runway=as_float(row.get("runway")),
                net_cashflow_month=as_float(row.get("sum_ncf")) or 0.0,
                organic_growth_value=as_float(row.get("sum_org")) or 0.0,
                opening_anchor_amount=as_float(row.get("ob_amt")),
                opening_anchor_date=as_date(row.get("ob_dt")),
            )
        )
        bucket = grp_agg.setdefault(grp, {"book": 0.0, "mkt": 0.0, "liq": 0.0, "cnt": 0})
        val_mkt = cl_mkt if cl_mkt is not None else cl_book
        bucket["book"] += cl_book
        bucket["mkt"] += val_mkt
        if is_liq:
            bucket["liq"] += val_mkt
        bucket["cnt"] += 1

    total_pos_mkt = sum(max(0.0, g["mkt"]) for g in grp_agg.values())
    group_mix = tuple(
        AssetGroupMixRow(
            asset_group=grp_name,
            book_value=vals["book"],
            market_value=vals["mkt"],
            liquid_value=vals["liq"],
            share_pct=(max(0.0, vals["mkt"]) / total_pos_mkt) if total_pos_mkt > 0 else None,
            asset_count=vals["cnt"],
        )
        for grp_name, vals in sorted(grp_agg.items(), key=lambda kv: kv[1]["mkt"], reverse=True)
    )
    return tuple(assets), group_mix


def query_wealth_asset_history(
    duck_reader: DuckDBQueryPort,
    asset_id: str,
    start_date: date,
    end_date: date,
) -> tuple[WealthAssetRow, ...]:
    """Query monthly balance and drawdown trajectory for a single asset subcategory."""
    if not table_exists(duck_reader, "gold", "wealth_asset_breakdown"):
        return ()
    rows = duck_reader.fetch_all(
        """
        SELECT
            "ASSET_SUBCATEGORY_ID" AS asset_id,
            "MONTH_END_DATE" AS m_end,
            "Opening_Balance" AS op_bal,
            "Closing_Balance" AS cl_bal,
            "Closing_Balance_Market" AS cl_mkt,
            "All_Time_High_Balance" AS ath,
            "Drawdown_From_Peak" AS dd,
            "Net_Cashflow_Month" AS ncf,
            "Months_of_Runway" AS runway
        FROM gold.wealth_asset_breakdown
        WHERE "ASSET_SUBCATEGORY_ID" = ?
          AND "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
        ORDER BY "MONTH_END_DATE" ASC;
        """,
        (asset_id, start_date, end_date),
    )
    out: list[WealthAssetRow] = []
    for row in rows:
        dt = as_date(row.get("m_end"))
        if dt is None:
            continue
        out.append(
            WealthAssetRow(
                asset_id=asset_id,
                asset_name=asset_id,
                month_end=dt,
                opening_balance=as_float(row.get("op_bal")) or 0.0,
                closing_balance=as_float(row.get("cl_bal")) or 0.0,
                closing_balance_market=as_float(row.get("cl_mkt")),
                all_time_high=as_float(row.get("ath")),
                drawdown_from_peak=as_float(row.get("dd")),
                net_cashflow_month=as_float(row.get("ncf")) or 0.0,
                months_of_runway=as_float(row.get("runway")),
            )
        )
    return tuple(out)


def build_wealth_kpis(
    latest: WealthMonthlyPoint,
    prior: WealthMonthlyPoint | None,
) -> tuple[OverviewKPI, ...]:
    """Build the 9-card Wealth KPI grid from the latest stock row."""
    as_of = latest.month_end

    def _delta(curr: float | None, prev: float | None) -> float | None:
        if curr is None or prev is None or prev == 0:
            return None
        return (curr - prev) / abs(prev)

    book_nw = OverviewKPI(
        kpi_id="wealth_book_nw",
        label="Book Net Worth",
        value=latest.book_net_worth,
        format=MetricFormat.CURRENCY,
        delta_value=_delta(latest.book_net_worth, prior.book_net_worth if prior else None),
        delta_format=MetricFormat.PERCENT,
        delta_label="vs Prior Period",
        subtitle="Historical cost basis assets minus liabilities",
        source_table="gold.core_monthly_fact",
        as_of_date=as_of,
        drill_route="/wealth",
        available=latest.book_net_worth is not None,
        tooltip="Canonical Total_Net_Worth (non-additive).",
    )
    mkt_nw = OverviewKPI(
        kpi_id="wealth_market_nw",
        label="Market Net Worth",
        value=latest.market_net_worth,
        format=MetricFormat.CURRENCY,
        delta_value=_delta(latest.market_net_worth, prior.market_net_worth if prior else None),
        delta_format=MetricFormat.PERCENT,
        delta_label="vs Prior Period",
        subtitle="Mark-to-market net worth",
        source_table="gold.core_monthly_fact",
        as_of_date=as_of,
        drill_route="/wealth",
        available=latest.market_net_worth is not None,
        tooltip="Canonical Total_Net_Worth_Market; never substituted with book value.",
        breakups=(OverviewBreakupItem(label="Unrealized P&L", value=latest.closing_unrealized_pl),),
    )
    tot_assets = OverviewKPI(
        kpi_id="wealth_total_assets",
        label="Total Assets (Market)",
        value=latest.total_assets_market,
        format=MetricFormat.CURRENCY,
        subtitle="Gross market valuation before liabilities",
        source_table="gold.core_monthly_fact",
        as_of_date=as_of,
        drill_route="/wealth",
        available=latest.total_assets_market is not None,
        breakups=(OverviewBreakupItem(label="Book Assets", value=latest.total_assets),),
    )
    liab = OverviewKPI(
        kpi_id="wealth_liabilities",
        label="Total Liabilities",
        value=latest.total_liabilities,
        format=MetricFormat.CURRENCY,
        subtitle="Canonical Core liabilities representation",
        source_table="gold.core_monthly_fact",
        as_of_date=as_of,
        drill_route="/wealth",
        available=latest.total_liabilities is not None,
    )
    liq_assets = OverviewKPI(
        kpi_id="wealth_liquid_assets",
        label="Liquid Assets (Market)",
        value=latest.liquid_assets_market,
        format=MetricFormat.CURRENCY,
        subtitle="Readily accessible liquid wealth",
        source_table="gold.core_monthly_fact",
        as_of_date=as_of,
        drill_route="/wealth",
        available=latest.liquid_assets_market is not None,
        breakups=(OverviewBreakupItem(label="Liquid Book", value=latest.liquid_assets),),
    )
    liq_ratio = OverviewKPI(
        kpi_id="wealth_liquidity_ratio",
        label="Liquidity Ratio",
        value=latest.liquidity_ratio_months,
        format=MetricFormat.NUMBER,
        unit_suffix=" mos",
        subtitle="Months of core expense covered by liquid assets",
        source_table="gold.cashflow_efficiency_analytics",
        as_of_date=as_of,
        drill_route="/wealth",
        available=latest.liquidity_ratio_months is not None,
        breakups=(
            OverviewBreakupItem(
                label="EF Coverage",
                value=latest.emergency_coverage,
                format=MetricFormat.NUMBER,
            ),
        ),
    )
    debt_ratio = OverviewKPI(
        kpi_id="wealth_debt_to_assets",
        label="Debt / Assets",
        value=latest.debt_to_asset_pct,
        format=MetricFormat.PERCENT,
        subtitle="Canonical Debt_to_Asset_Ratio_Pct",
        source_table="gold.cashflow_efficiency_analytics",
        as_of_date=as_of,
        drill_route="/wealth",
        available=latest.debt_to_asset_pct is not None,
    )
    fx_exp = OverviewKPI(
        kpi_id="wealth_foreign_exposure",
        label="Foreign Exposure",
        value=latest.foreign_exposure_pct,
        format=MetricFormat.PERCENT,
        subtitle="Share of wealth in non-base currencies",
        source_table="gold.core_monthly_fact",
        as_of_date=as_of,
        drill_route="/investments",
        available=latest.foreign_exposure_pct is not None,
        breakups=(
            OverviewBreakupItem(label="Amount", value=latest.foreign_exposure),
            OverviewBreakupItem(label="Asset P&L", value=latest.closing_asset_pnl),
            OverviewBreakupItem(label="FX P&L", value=latest.closing_forex_pnl),
            OverviewBreakupItem(
                label="Inv XIRR",
                value=latest.closing_investment_xirr,
                format=MetricFormat.PERCENT,
            ),
        ),
    )
    real_growth = OverviewKPI(
        kpi_id="wealth_real_nw_growth",
        label="YoY Real NW Growth",
        value=latest.yoy_nw_growth_real_pct,
        format=MetricFormat.PERCENT,
        subtitle="Inflation-adjusted annual net worth growth",
        source_table="gold.cashflow_efficiency_analytics",
        as_of_date=as_of,
        drill_route="/wealth",
        available=latest.yoy_nw_growth_real_pct is not None,
        breakups=(
            OverviewBreakupItem(
                label="Nominal YoY",
                value=latest.yoy_nw_growth_pct,
                format=MetricFormat.PERCENT,
            ),
        ),
    )
    return (
        book_nw,
        mkt_nw,
        tot_assets,
        liab,
        liq_assets,
        liq_ratio,
        debt_ratio,
        fx_exp,
        real_growth,
    )
