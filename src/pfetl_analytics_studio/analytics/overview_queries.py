"""Read-only Gold & Meta SQL query helpers powering `OverviewService`.

Owns
----
- Parameterized Gold/Meta SQL read queries for monthly cashflow, efficiency, budget variance,
  portfolio valuation/alpha, FIRE, Tax FY, income/expense breakups, and asset class mix.

Does not own
------------
- NiceGUI presentation or PFETL calculation methodology.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pfetl_analytics_studio.analytics.overview_models import (
    AssetClassPoint,
    CashflowPoint,
    CategoryBreakupPoint,
    FirePoint,
    NetWorthPoint,
    OverviewRunHealth,
    PortfolioPoint,
    WealthAssetSplitPoint,
    as_date,
    as_float,
    slice_filters,
)
from pfetl_analytics_studio.data import DuckDBReader
from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterOperator,
    FilterSet,
    compile_filter_clause,
)

_INCOME_FILTER_MAP: dict[FilterField, str] = {
    FilterField.INCOME_GROUP: "CATEGORY_GROUPS",
    FilterField.INCOME_CATEGORY: "CATEGORY_NAME",
    FilterField.IS_ACTIVE_INCOME: "Is_Active_Income",
    FilterField.IS_PASSIVE_INCOME: "Is_Passive_Income",
}

_EXPENSE_FILTER_MAP: dict[FilterField, str] = {
    FilterField.EXPENSE_GROUP: "CATEGORY_GROUPS",
    FilterField.EXPENSE_CATEGORY: "CATEGORY_NAME",
    FilterField.IS_CORE_EXPENSE: "Is_Core_Expense",
}

_PORTFOLIO_CLASS_FILTER_MAP: dict[FilterField, str] = {
    FilterField.INSTRUMENT_CLASS: "INSTRUMENT_CLASS",
}

_WEALTH_SPLIT_FILTER_MAP: dict[FilterField, str] = {
    FilterField.ASSET_GROUP: "ASSET_GROUP",
    FilterField.ASSET_ID: "ASSET_SUBCATEGORY_ID",
    FilterField.IS_LIQUID: "Is_Liquid",
}


def enrich_meta_counts(
    duck_reader: DuckDBReader,
    run_health: OverviewRunHealth,
) -> OverviewRunHealth:
    """Populate row-count aggregates from `meta.m_Table_Row_Counts` safely."""
    if not duck_reader.table_exists("meta", "m_Table_Row_Counts"):
        return run_health
    row = duck_reader.fetch_one(
        """
        SELECT
            COALESCE(SUM(COALESCE(TRY_CAST("row_count" AS BIGINT), 0)), 0) AS total_rows,
            SUM(
                CASE
                    WHEN schema_name = 'gold'
                     AND COALESCE(TRY_CAST("row_count" AS BIGINT), 0) > 0
                    THEN 1
                    ELSE 0
                END
            ) AS gold_populated
        FROM meta.m_Table_Row_Counts;
        """
    )
    if row is None:
        return run_health
    return run_health.model_copy(
        update={
            "warehouse_rows_total": int(row.get("total_rows") or 0),
            "gold_tables_populated": int(row.get("gold_populated") or 0),
        }
    )


def table_has_column(duck_reader: DuckDBReader, schema: str, table: str, column: str) -> bool:
    """Check column existence safely so partial test fixtures never fail."""
    hit = duck_reader.fetch_one(
        """
        SELECT 1 AS ok
        FROM information_schema.columns
        WHERE table_schema = ? AND table_name = ? AND column_name = ?
        LIMIT 1;
        """,
        (schema, table, column),
    )
    return hit is not None


def query_core_monthly(
    duck_reader: DuckDBReader,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fetch summed flows & latest stock balances from `gold.core_monthly_fact`."""
    if not duck_reader.table_exists("gold", "core_monthly_fact"):
        return None, None

    def _opt_flow(col: str, fn: str, alias: str) -> str:
        if table_has_column(duck_reader, "gold", "core_monthly_fact", col):
            return f'{fn}("{col}") AS {alias}'
        return f"NULL AS {alias}"

    def _opt_stock(col: str, alias: str) -> str:
        if table_has_column(duck_reader, "gold", "core_monthly_fact", col):
            return f'"{col}" AS {alias}'
        return f"NULL AS {alias}"

    non_cash_expr = _opt_flow("Total_Non_Cash_Income", "SUM", "non_cash_inc")
    res_expr = _opt_flow("Attribution_Residual", "SUM", "res_flow")
    open_nw_expr = (
        'ARG_MIN("Opening_Balance_Asset_Market", "MONTH_END_DATE") AS open_nw_mkt'
        if table_has_column(
            duck_reader, "gold", "core_monthly_fact", "Opening_Balance_Asset_Market"
        )
        else "NULL AS open_nw_mkt"
    )
    open_upl_expr = (
        'ARG_MIN("Opening_Unrealized_PL", "MONTH_END_DATE") AS open_upl'
        if table_has_column(duck_reader, "gold", "core_monthly_fact", "Opening_Unrealized_PL")
        else "NULL AS open_upl"
    )

    cpi_expr = _opt_stock("INFLATION_YOY_PCT", "cpi_yoy")
    fx_expr = _opt_stock("Foreign_Exposure_Pct", "fx_pct")
    upl_expr = _opt_stock("Closing_Unrealized_PL", "upl")
    asset_pnl_expr = _opt_stock("Closing_Asset_PnL", "asset_pnl")
    fx_pnl_expr = _opt_stock("Closing_Forex_PnL", "fx_pnl")
    assets_book_expr = _opt_stock("Total_Assets", "assets_book")

    flow = duck_reader.fetch_one(
        f"""
        SELECT
            SUM("Total_Income") AS inc,
            {non_cash_expr},
            SUM("Total_Expense") AS exp,
            SUM("Total_Core_Expense") AS core_exp,
            SUM("Net_Cashflow_Month") AS net_cf,
            SUM("Net_Savings_Flow") AS sav_flow,
            SUM("Market_Return_Flow") AS mkt_flow,
            {res_expr},
            {open_nw_expr},
            {open_upl_expr},
            COUNT(*) AS n_months,
            MAX("MONTH_END_DATE") AS max_end
        FROM gold.core_monthly_fact
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?;
        """,
        (start_date, end_date),
    )
    stock = duck_reader.fetch_one(
        f"""
        SELECT
            "MONTH_END_DATE" AS m_end,
            "Total_Net_Worth_Market" AS nw_mkt,
            "Total_Net_Worth" AS nw_book,
            "Liquid_Assets_Market" AS liq_mkt,
            "Total_Assets_Market" AS assets_mkt,
            {assets_book_expr},
            "Total_Liabilities" AS liab,
            {upl_expr},
            {asset_pnl_expr},
            {fx_pnl_expr},
            {cpi_expr},
            {fx_expr}
        FROM gold.core_monthly_fact
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
        ORDER BY "MONTH_END_DATE" DESC
        LIMIT 1;
        """,
        (start_date, end_date),
    )
    return flow, stock


def query_efficiency(
    duck_reader: DuckDBReader,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return summed efficiency flows plus latest ratio row inside the active period."""
    if not duck_reader.table_exists("gold", "cashflow_efficiency_analytics"):
        return None, None
    has_rich = table_has_column(
        duck_reader, "gold", "cashflow_efficiency_analytics", "Active_Income"
    )
    if has_rich:
        flow = duck_reader.fetch_one(
            """
            SELECT
                SUM("Active_Income") AS act_inc,
                SUM("Passive_Income") AS pass_inc,
                SUM("Interest_Income") AS int_inc,
                SUM("Dividend_Income") AS div_inc,
                SUM("Core_Expense") AS core_exp,
                SUM("NonCore_Expense") AS noncore_exp,
                SUM("Total_Investment_Deployed") AS deployed,
                SUM("Total_Investment_Redeemed") AS redeemed,
                SUM("Redemption_Gain_Loss_Value") AS redemp_pnl,
                SUM("Net_Investment_Flow") AS net_inv,
                SUM("Net_Surplus_After_Invest") AS post_surplus
            FROM gold.cashflow_efficiency_analytics
            WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?;
            """,
            (start_date, end_date),
        )
    else:
        flow = None

    sr_3m_expr = (
        '"Trailing_3M_Avg_Savings_Rate"'
        if table_has_column(
            duck_reader, "gold", "cashflow_efficiency_analytics", "Trailing_3M_Avg_Savings_Rate"
        )
        else "NULL AS Trailing_3M_Avg_Savings_Rate"
    )
    yoy_real_expr = (
        '"YoY_Net_Worth_Growth_Pct_Real" AS yoy_nw_real'
        if table_has_column(
            duck_reader,
            "gold",
            "cashflow_efficiency_analytics",
            "YoY_Net_Worth_Growth_Pct_Real",
        )
        else "NULL AS yoy_nw_real"
    )
    latest = duck_reader.fetch_one(
        f"""
        SELECT
            "MONTH_END_DATE" AS m_end,
            "Savings_Rate_Pct",
            "Investment_Rate_Pct",
            "Passive_Income_Core_Expense_Coverage_Ratio",
            "Active_Income_Dependency_Pct",
            "Liquidity_Ratio_Months",
            "Debt_to_Asset_Ratio_Pct",
            {sr_3m_expr},
            {yoy_real_expr}
        FROM gold.cashflow_efficiency_analytics
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
        ORDER BY "MONTH_END_DATE" DESC
        LIMIT 1;
        """,
        (start_date, end_date),
    )
    return flow, latest


def query_budget_variance(
    duck_reader: DuckDBReader,
    start_date: date,
    end_date: date,
) -> dict[str, Any] | None:
    """Query budget targets, overspend flags, and zero-income runway safely."""
    if not duck_reader.table_exists("gold", "forecast_budget_variance"):
        return None
    if not table_has_column(
        duck_reader, "gold", "forecast_budget_variance", "Budget_Total_Expense_Target"
    ):
        return None
    return duck_reader.fetch_one(
        """
        SELECT
            SUM("Budget_Total_Expense_Target") AS exp_target,
            BOOL_AND(COALESCE("Is_Budget_Month_Healthy", TRUE)) AS all_healthy,
            BOOL_OR(COALESCE("Is_Investment_Underfunded", FALSE)) AS inv_underfunded,
            MAX("Zero_Income_Runway_Months") AS zero_inc_runway
        FROM gold.forecast_budget_variance
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?;
        """,
        (start_date, end_date),
    )


def query_portfolio_dual(
    duck_reader: DuckDBReader,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return both period-anchored portfolio valuation and absolute latest DB valuation."""
    if not duck_reader.table_exists("gold", "investment_by_portfolio"):
        return None, None

    opt_cols = (
        "Asset_PnL",
        "Forex_PnL",
        "After_Tax_XIRR",
        "Max_Drawdown",
        "FY_Realized_Net_PnL",
        "Top5_Holdings_Weight",
        "LTCG_Mature_Value",
        "LTCG_Maturing_365D",
    )
    select_extra = ", ".join(
        f'"{c}"'
        if table_has_column(duck_reader, "gold", "investment_by_portfolio", c)
        else f'NULL AS "{c}"'
        for c in opt_cols
    )
    base_cols = f"""
        "Closing_Date" AS c_date,
        "Total_Current_Value",
        "Total_Invested_Value",
        "Unrealized_PL",
        "XIRR",
        "BM_XIRR",
        "Active_Return",
        "HHI_Score",
        "Effective_Holdings_Count",
        {select_extra}
    """

    period_row = duck_reader.fetch_one(
        f"""
        SELECT {base_cols}
        FROM gold.investment_by_portfolio
        WHERE "Closing_Date" BETWEEN ? AND ?
        ORDER BY "Closing_Date" DESC
        LIMIT 1;
        """,
        (start_date, end_date),
    )
    if period_row is None:
        period_row = duck_reader.fetch_one(
            f"""
            SELECT {base_cols}
            FROM gold.investment_by_portfolio
            WHERE "Closing_Date" <= ?
            ORDER BY "Closing_Date" DESC
            LIMIT 1;
            """,
            (end_date,),
        )

    latest_db_row = duck_reader.fetch_one(
        f"""
        SELECT {base_cols}
        FROM gold.investment_by_portfolio
        ORDER BY "Closing_Date" DESC
        LIMIT 1;
        """
    )
    return period_row, latest_db_row


def query_fire_latest(
    duck_reader: DuckDBReader,
    start_date: date,
    end_date: date,
) -> dict[str, Any] | None:
    """Return latest FIRE trajectory row strictly inside the active period."""
    if not duck_reader.table_exists("gold", "wealth_fire_analytics"):
        return None
    opt = (
        '"Coast_FI_Today", "Savings_Rate_Required"'
        if table_has_column(duck_reader, "gold", "wealth_fire_analytics", "Coast_FI_Today")
        else 'NULL AS "Coast_FI_Today", NULL AS "Savings_Rate_Required"'
    )
    return duck_reader.fetch_one(
        f"""
        SELECT
            "MONTH_END_DATE" AS m_end,
            "Current_FI_Coverage_Pct",
            "Target_FI_Today",
            "FI_Gap",
            "Probability_Of_Success_Pct",
            "Projected_FI_Date_P50",
            "Years_To_FI_P50",
            {opt}
        FROM gold.wealth_fire_analytics
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
        ORDER BY "MONTH_END_DATE" DESC
        LIMIT 1;
        """,
        (start_date, end_date),
    )


def query_tax_fy(
    duck_reader: DuckDBReader,
    *,
    default_fy: str | None,
    filters: FilterSet,
) -> dict[str, Any] | None:
    """Fetch FY tax exposure summary honoring active `FilterField.FY` predicates."""
    if not duck_reader.table_exists("gold", "tax_financial_year_summary"):
        return None

    target_fys: list[str] = []
    for spec in filters.filters:
        if spec.field != FilterField.FY:
            continue
        if spec.operator == FilterOperator.EQ and spec.value is not None:
            target_fys = [str(spec.value)]
        elif spec.operator == FilterOperator.IN and isinstance(spec.value, tuple):
            target_fys = [str(v) for v in spec.value]

    if not target_fys and default_fy:
        target_fys = [default_fy]
    if not target_fys:
        return None

    placeholders = ", ".join("?" for _ in target_fys)
    has_credits = table_has_column(
        duck_reader, "gold", "tax_financial_year_summary", "Total_Tax_Credits"
    )
    credits_expr = 'SUM("Total_Tax_Credits")' if has_credits else "NULL"
    row = duck_reader.fetch_one(
        f"""
        SELECT
            SUM("Indicative_Net_Tax_Exposure") AS exposure,
            SUM("Indicative_Unrealized_Tax_Liability") AS unrealized_tax,
            SUM("Indicative_Harvestable_Tax_Savings") AS harvest_save,
            {credits_expr} AS tax_credits,
            BOOL_AND(COALESCE("Investment_Tax_Estimate_Complete", FALSE)) AS inv_ok,
            BOOL_AND(COALESCE("Ledger_Tax_Estimate_Complete", FALSE)) AS led_ok,
            SUM(COALESCE("Investment_Tax_Estimate_Missing_Count", 0)) AS inv_miss,
            SUM(COALESCE("Ledger_Tax_Estimate_Missing_Count", 0)) AS led_miss,
            SUM(COALESCE("Total_Event_Count", 0)) AS event_count,
            COUNT(*) AS row_count
        FROM gold.tax_financial_year_summary
        WHERE "FY" IN ({placeholders});
        """,
        tuple(target_fys),
    )
    if row is None or int(row.get("row_count") or 0) == 0:
        return None

    is_complete = (
        bool(row.get("inv_ok"))
        and bool(row.get("led_ok"))
        and int(row.get("inv_miss") or 0) == 0
        and int(row.get("led_miss") or 0) == 0
    )
    return {
        "exposure": row.get("exposure"),
        "unrealized_tax": row.get("unrealized_tax"),
        "harvest_save": row.get("harvest_save"),
        "tax_credits": row.get("tax_credits"),
        "is_complete": is_complete,
        "event_count": int(row.get("event_count") or 0),
        "fy_label": ", ".join(target_fys),
    }


def query_income_breakup(
    duck_reader: DuckDBReader,
    start_date: date,
    end_date: date,
    *,
    filters: FilterSet,
) -> tuple[CategoryBreakupPoint, ...]:
    """Aggregate top positive income categories from `gold.cashflow_income_breakdown`."""
    if not duck_reader.table_exists("gold", "cashflow_income_breakdown"):
        return ()
    if not table_has_column(
        duck_reader, "gold", "cashflow_income_breakdown", "Total_Monthly_Income"
    ):
        return ()
    sub_filter = slice_filters(filters, _INCOME_FILTER_MAP)
    where_extra, f_params = compile_filter_clause(sub_filter, _INCOME_FILTER_MAP)
    rows = duck_reader.fetch_all(
        f"""
        SELECT
            COALESCE("CATEGORY_NAME", 'Other') AS cat,
            COALESCE("CATEGORY_GROUPS", 'Other') AS grp,
            SUM("Total_Monthly_Income") AS amt,
            BOOL_OR(COALESCE("Is_Active_Income", FALSE)) AS is_act,
            BOOL_OR(COALESCE("Is_Passive_Income", FALSE)) AS is_pass,
            BOOL_OR(COALESCE("Is_Non_Cash_Income", FALSE)) AS is_nc
        FROM gold.cashflow_income_breakdown
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
          AND ({where_extra})
        GROUP BY 1, 2
        HAVING SUM("Total_Monthly_Income") > 0
        ORDER BY amt DESC
        LIMIT 10;
        """,
        [start_date, end_date, *f_params],
    )
    points: list[CategoryBreakupPoint] = []
    for row in rows:
        amt = as_float(row.get("amt"))
        if amt is None:
            continue
        bucket = (
            "Active"
            if row.get("is_act")
            else (
                "Passive" if row.get("is_pass") else ("Non-Cash" if row.get("is_nc") else "Other")
            )
        )
        points.append(
            CategoryBreakupPoint(
                label=str(row["cat"]),
                group=str(row["grp"]),
                amount=amt,
                bucket=bucket,
            )
        )
    return tuple(points)


def query_expense_breakup(
    duck_reader: DuckDBReader,
    start_date: date,
    end_date: date,
    *,
    filters: FilterSet,
) -> tuple[CategoryBreakupPoint, ...]:
    """Aggregate top expense groups & Core vs Non-Core split from `cashflow_expense_breakdown`."""
    if not duck_reader.table_exists("gold", "cashflow_expense_breakdown"):
        return ()
    if not table_has_column(
        duck_reader, "gold", "cashflow_expense_breakdown", "Total_Monthly_Spend"
    ):
        return ()
    sub_filter = slice_filters(filters, _EXPENSE_FILTER_MAP)
    where_extra, f_params = compile_filter_clause(sub_filter, _EXPENSE_FILTER_MAP)
    rows = duck_reader.fetch_all(
        f"""
        SELECT
            COALESCE("CATEGORY_GROUPS", COALESCE("CATEGORY_NAME", 'Other')) AS grp,
            COALESCE("Spend_Type", 'Variable') AS stype,
            SUM("Total_Monthly_Spend") AS amt,
            BOOL_OR(COALESCE("Is_Core_Expense", FALSE)) AS is_core
        FROM gold.cashflow_expense_breakdown
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
          AND ({where_extra})
        GROUP BY 1, 2
        HAVING SUM("Total_Monthly_Spend") > 0
        ORDER BY amt DESC
        LIMIT 10;
        """,
        [start_date, end_date, *f_params],
    )
    points: list[CategoryBreakupPoint] = []
    for row in rows:
        amt = as_float(row.get("amt"))
        if amt is None:
            continue
        bucket = f"{'Core' if row.get('is_core') else 'Non-Core'} · {row.get('stype')}"
        points.append(
            CategoryBreakupPoint(
                label=str(row["grp"]),
                group=str(row.get("stype") or "Variable"),
                amount=amt,
                bucket=bucket,
            )
        )
    return tuple(points)


def query_asset_class_mix(
    duck_reader: DuckDBReader,
    end_date: date,
    *,
    filters: FilterSet,
) -> tuple[AssetClassPoint, ...]:
    """Fetch latest instrument class allocation, XIRR, and benchmark alpha."""
    if not duck_reader.table_exists("gold", "investment_by_class"):
        return ()
    if not table_has_column(duck_reader, "gold", "investment_by_class", "INSTRUMENT_CLASS"):
        return ()
    sub_filter = slice_filters(filters, _PORTFOLIO_CLASS_FILTER_MAP)
    where_extra, f_params = compile_filter_clause(sub_filter, _PORTFOLIO_CLASS_FILTER_MAP)
    date_row = duck_reader.fetch_one(
        'SELECT MAX("Closing_Date") AS c_dt FROM gold.investment_by_class WHERE "Closing_Date" <= ?;',
        (end_date,),
    )
    target_dt = as_date(date_row["c_dt"]) if date_row else None
    if target_dt is None:
        return ()
    rows = duck_reader.fetch_all(
        f"""
        SELECT
            "Closing_Date" AS c_date,
            "INSTRUMENT_CLASS",
            "Total_Current_Value",
            "Total_Invested_Value",
            "Unrealized_PL",
            "Weight",
            "XIRR",
            "BM_XIRR",
            "Active_Return"
        FROM gold.investment_by_class
        WHERE "Closing_Date" = ?
          AND ({where_extra})
        ORDER BY "Total_Current_Value" DESC;
        """,
        [target_dt, *f_params],
    )
    items: list[AssetClassPoint] = []
    for row in rows:
        dt = as_date(row.get("c_date"))
        cur = as_float(row.get("Total_Current_Value"))
        if dt is None or cur is None:
            continue
        items.append(
            AssetClassPoint(
                instrument_class=str(row["INSTRUMENT_CLASS"]),
                closing_date=dt,
                current_value=cur,
                invested_value=as_float(row.get("Total_Invested_Value")),
                unrealized_pl=as_float(row.get("Unrealized_PL")),
                weight=as_float(row.get("Weight")),
                xirr=as_float(row.get("XIRR")),
                bm_xirr=as_float(row.get("BM_XIRR")),
                active_return=as_float(row.get("Active_Return")),
            )
        )
    return tuple(items)


def query_wealth_aggregates(
    duck_reader: DuckDBReader,
    end_date: date,
) -> dict[str, Any] | None:
    """Fetch aggregate ATH, cumulative organic savings, and drawdown from `gold.wealth_asset_breakdown`."""
    if not duck_reader.table_exists("gold", "wealth_asset_breakdown"):
        return None
    if not table_has_column(
        duck_reader, "gold", "wealth_asset_breakdown", "Closing_Balance_Market"
    ):
        return None
    date_row = duck_reader.fetch_one(
        'SELECT MAX(TRY_CAST("MONTH_END_DATE" AS DATE)) AS m_end '
        'FROM gold.wealth_asset_breakdown WHERE TRY_CAST("MONTH_END_DATE" AS DATE) <= ?;',
        (end_date,),
    )
    target_dt = as_date(date_row["m_end"]) if date_row else None
    if target_dt is None:
        return None
    return duck_reader.fetch_one(
        """
        SELECT
            TRY_CAST("MONTH_END_DATE" AS DATE) AS m_end,
            SUM("Closing_Balance_Market") AS wab_mkt,
            SUM("Closing_Balance") AS wab_book,
            SUM("All_Time_High_Balance") AS wab_ath,
            SUM("Cumulative_Net_Savings") AS wab_cum_sav,
            MIN("Drawdown_From_Peak") AS wab_min_dd
        FROM gold.wealth_asset_breakdown
        WHERE TRY_CAST("MONTH_END_DATE" AS DATE) = ?
        GROUP BY 1;
        """,
        (target_dt,),
    )


def query_wealth_asset_split(
    duck_reader: DuckDBReader,
    end_date: date,
    *,
    filters: FilterSet,
) -> tuple[WealthAssetSplitPoint, ...]:
    """Fetch net worth split by asset group from `gold.wealth_asset_breakdown` + asset dims."""
    if not duck_reader.table_exists("gold", "wealth_asset_breakdown"):
        return ()
    if not table_has_column(
        duck_reader, "gold", "wealth_asset_breakdown", "Closing_Balance_Market"
    ):
        return ()
    date_row = duck_reader.fetch_one(
        'SELECT MAX(TRY_CAST("MONTH_END_DATE" AS DATE)) AS m_end '
        'FROM gold.wealth_asset_breakdown WHERE TRY_CAST("MONTH_END_DATE" AS DATE) <= ?;',
        (end_date,),
    )
    target_dt = as_date(date_row["m_end"]) if date_row else None
    if target_dt is None:
        return ()

    has_dims = duck_reader.table_exists(
        "silver", "dim_asset_subcategory"
    ) and duck_reader.table_exists("silver", "dim_asset_category")
    if has_dims:
        sub_filter = slice_filters(filters, _WEALTH_SPLIT_FILTER_MAP)
        where_extra, f_params = compile_filter_clause(sub_filter, _WEALTH_SPLIT_FILTER_MAP)
        sql = f"""
            SELECT
                COALESCE(c."ASSET_GROUP", 'Unclassified') AS asset_group,
                SUM(w."Closing_Balance_Market") AS mkt_val,
                SUM(w."Closing_Balance") AS book_val,
                SUM(w."Cumulative_Net_Savings") AS cum_sav,
                SUM(w."All_Time_High_Balance") AS ath_val
            FROM gold.wealth_asset_breakdown w
            LEFT JOIN silver.dim_asset_subcategory s ON w."ASSET_SUBCATEGORY_ID" = s."UID"
            LEFT JOIN silver.dim_asset_category c ON s."ASSET_GROUP_ID" = c."UID"
            WHERE TRY_CAST(w."MONTH_END_DATE" AS DATE) = ?
              AND ({where_extra})
            GROUP BY 1
            HAVING ABS(SUM(w."Closing_Balance_Market")) >= 1.0
                OR ABS(SUM(w."Closing_Balance")) >= 1.0
            ORDER BY mkt_val DESC;
        """
        params: list[Any] = [target_dt, *f_params]
    else:
        sql = """
            SELECT
                CAST("ASSET_SUBCATEGORY_ID" AS VARCHAR) AS asset_group,
                SUM("Closing_Balance_Market") AS mkt_val,
                SUM("Closing_Balance") AS book_val,
                SUM("Cumulative_Net_Savings") AS cum_sav,
                SUM("All_Time_High_Balance") AS ath_val
            FROM gold.wealth_asset_breakdown
            WHERE TRY_CAST("MONTH_END_DATE" AS DATE) = ?
            GROUP BY 1
            HAVING ABS(SUM("Closing_Balance_Market")) >= 1.0
                OR ABS(SUM("Closing_Balance")) >= 1.0
            ORDER BY mkt_val DESC;
        """
        params = [target_dt]

    rows = duck_reader.fetch_all(sql, params)
    total_mkt = sum(as_float(r.get("mkt_val")) or 0.0 for r in rows)
    items: list[WealthAssetSplitPoint] = []
    for row in rows:
        mkt_val = as_float(row.get("mkt_val"))
        book_val = as_float(row.get("book_val"))
        if mkt_val is None or book_val is None:
            continue
        items.append(
            WealthAssetSplitPoint(
                asset_group=str(row["asset_group"]),
                market_value=mkt_val,
                book_value=book_val,
                unrealized_pl=mkt_val - book_val,
                cumulative_savings=as_float(row.get("cum_sav")),
                all_time_high=as_float(row.get("ath_val")),
                weight=(mkt_val / total_mkt) if total_mkt != 0 else None,
            )
        )
    return tuple(items)


def query_chart_series(
    duck_reader: DuckDBReader,
    *,
    chart_start: date,
    end_date: date,
) -> tuple[
    tuple[NetWorthPoint, ...],
    tuple[CashflowPoint, ...],
    tuple[PortfolioPoint, ...],
    tuple[FirePoint, ...],
]:
    """Fetch chronological series for the Overview cockpit charts."""
    nw_pts: list[NetWorthPoint] = []
    if duck_reader.table_exists("gold", "core_monthly_fact"):
        rows = duck_reader.fetch_all(
            """
            SELECT
                "MONTH_END_DATE" AS m_end,
                "Total_Net_Worth_Market" AS nw_mkt,
                "Total_Net_Worth" AS nw_book,
                "Net_Savings_Flow" AS sav_flow,
                "Market_Return_Flow" AS mkt_flow,
                "Attribution_Residual" AS res_flow
            FROM gold.core_monthly_fact
            WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
            ORDER BY "MONTH_END_DATE" ASC;
            """,
            (chart_start, end_date),
        )
        for row in rows:
            dt = as_date(row.get("m_end"))
            nw_mkt = as_float(row.get("nw_mkt"))
            if dt is None or nw_mkt is None:
                continue
            nw_pts.append(
                NetWorthPoint(
                    month_end=dt,
                    net_worth_market=nw_mkt,
                    net_worth_book=as_float(row.get("nw_book")),
                    net_savings_flow=as_float(row.get("sav_flow")),
                    market_return_flow=as_float(row.get("mkt_flow")),
                    attribution_residual=as_float(row.get("res_flow")),
                )
            )

    cf_pts: list[CashflowPoint] = []
    if duck_reader.table_exists("gold", "core_monthly_fact"):
        has_eff = duck_reader.table_exists("gold", "cashflow_efficiency_analytics")
        has_dep = has_eff and table_has_column(
            duck_reader, "gold", "cashflow_efficiency_analytics", "Total_Investment_Deployed"
        )
        if has_dep:
            cf_sql = """
                SELECT
                    c."MONTH_END_DATE" AS m_end,
                    c."Total_Income" AS inc,
                    c."Total_Expense" AS exp,
                    c."Total_Core_Expense" AS core_exp,
                    c."Net_Cashflow_Month" AS net_cf,
                    e."Savings_Rate_Pct" AS sav_rate,
                    e."Investment_Rate_Pct" AS inv_rate,
                    e."Total_Investment_Deployed" AS deployed,
                    e."Total_Investment_Redeemed" AS redeemed,
                    e."Net_Investment_Flow" AS net_inv
                FROM gold.core_monthly_fact c
                LEFT JOIN gold.cashflow_efficiency_analytics e
                  ON c."MONTH_END_DATE" = e."MONTH_END_DATE"
                WHERE c."MONTH_END_DATE" >= ? AND c."MONTH_START_DATE" <= ?
                ORDER BY c."MONTH_END_DATE" ASC;
            """
        elif has_eff:
            cf_sql = """
                SELECT
                    c."MONTH_END_DATE" AS m_end,
                    c."Total_Income" AS inc,
                    c."Total_Expense" AS exp,
                    c."Total_Core_Expense" AS core_exp,
                    c."Net_Cashflow_Month" AS net_cf,
                    e."Savings_Rate_Pct" AS sav_rate,
                    e."Investment_Rate_Pct" AS inv_rate,
                    NULL AS deployed,
                    NULL AS redeemed,
                    NULL AS net_inv
                FROM gold.core_monthly_fact c
                LEFT JOIN gold.cashflow_efficiency_analytics e
                  ON c."MONTH_END_DATE" = e."MONTH_END_DATE"
                WHERE c."MONTH_END_DATE" >= ? AND c."MONTH_START_DATE" <= ?
                ORDER BY c."MONTH_END_DATE" ASC;
            """
        else:
            cf_sql = """
                SELECT
                    "MONTH_END_DATE" AS m_end,
                    "Total_Income" AS inc,
                    "Total_Expense" AS exp,
                    "Total_Core_Expense" AS core_exp,
                    "Net_Cashflow_Month" AS net_cf,
                    NULL AS sav_rate,
                    NULL AS inv_rate,
                    NULL AS deployed,
                    NULL AS redeemed,
                    NULL AS net_inv
                FROM gold.core_monthly_fact
                WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
                ORDER BY "MONTH_END_DATE" ASC;
            """
        for row in duck_reader.fetch_all(cf_sql, (chart_start, end_date)):
            dt = as_date(row.get("m_end"))
            inc = as_float(row.get("inc"))
            exp = as_float(row.get("exp"))
            ncf = as_float(row.get("net_cf"))
            if dt is None or inc is None or exp is None or ncf is None:
                continue
            cf_pts.append(
                CashflowPoint(
                    month_end=dt,
                    income=inc,
                    expense=exp,
                    core_expense=as_float(row.get("core_exp")),
                    net_cashflow=ncf,
                    savings_rate=as_float(row.get("sav_rate")),
                    investment_rate=as_float(row.get("inv_rate")),
                    deployed=as_float(row.get("deployed")),
                    redeemed=as_float(row.get("redeemed")),
                    net_investment_flow=as_float(row.get("net_inv")),
                )
            )

    port_pts: list[PortfolioPoint] = []
    if duck_reader.table_exists("gold", "investment_by_portfolio"):
        p_rows = duck_reader.fetch_all(
            """
            SELECT
                "Closing_Date" AS c_date,
                "Total_Current_Value" AS cur_val,
                "Total_Invested_Value" AS inv_val,
                "XIRR" AS xirr,
                "BM_XIRR" AS bm_xirr,
                "Active_Return" AS active_ret
            FROM gold.investment_by_portfolio
            WHERE "Closing_Date" BETWEEN ? AND ?
            ORDER BY "Closing_Date" ASC;
            """,
            (chart_start, end_date),
        )
        for row in p_rows:
            dt = as_date(row.get("c_date"))
            cur_val = as_float(row.get("cur_val"))
            if dt is None or cur_val is None:
                continue
            port_pts.append(
                PortfolioPoint(
                    closing_date=dt,
                    current_value=cur_val,
                    invested_value=as_float(row.get("inv_val")),
                    xirr=as_float(row.get("xirr")),
                    bm_xirr=as_float(row.get("bm_xirr")),
                    active_return=as_float(row.get("active_ret")),
                )
            )

    fire_pts: list[FirePoint] = []
    if duck_reader.table_exists("gold", "wealth_fire_analytics"):
        f_rows = duck_reader.fetch_all(
            """
            SELECT
                "MONTH_END_DATE" AS m_end,
                "Current_FI_Coverage_Pct" AS fi_cov,
                "Probability_Of_Success_Pct" AS prob,
                "Target_FI_Today" AS target_fi,
                "Projected_FI_Date_P50" AS p50_date
            FROM gold.wealth_fire_analytics
            WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
            ORDER BY "MONTH_END_DATE" ASC;
            """,
            (chart_start, end_date),
        )
        for row in f_rows:
            dt = as_date(row.get("m_end"))
            if dt is None:
                continue
            fire_pts.append(
                FirePoint(
                    month_end=dt,
                    fi_coverage=as_float(row.get("fi_cov")),
                    success_probability=as_float(row.get("prob")),
                    target_fi_today=as_float(row.get("target_fi")),
                    projected_fi_date=as_date(row.get("p50_date")),
                )
            )

    return tuple(nw_pts), tuple(cf_pts), tuple(port_pts), tuple(fire_pts)
