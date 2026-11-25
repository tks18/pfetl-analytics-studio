"""Read-only Gold & Silver SQL query helpers powering Household Expense Analytics.

Owns
----
- Parameterized Gold expense efficiency, real inflation-adjusted core growth, category/group
  rollups, and Month × Group heatmap queries.
- Parameterized Silver expense transaction drill (`silver.fact_expense_transactions`) joined
  with `dim_expense_subcategory`, `dim_expense_category`, and `dim_asset_subcategory`.

Does not own
------------
- NiceGUI widgets or PFETL expense classification methodology.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pfetl_analytics_studio.analytics.household_models import (
    ExpenseAccountMixPoint,
    ExpenseCategoryRow,
    ExpenseGroupRow,
    ExpenseHeatmapData,
    ExpenseMonthlyPoint,
    ExpenseTransactionPage,
    ExpenseTransactionRow,
)
from pfetl_analytics_studio.analytics.overview_models import as_date, as_float, slice_filters
from pfetl_analytics_studio.analytics.overview_queries import table_exists, table_has_column
from pfetl_analytics_studio.data import DuckDBQueryPort
from pfetl_analytics_studio.semantic import FilterField, FilterSet, compile_filter_clause

_EXPENSE_GOLD_FILTER_MAP: dict[FilterField, str] = {
    FilterField.EXPENSE_GROUP: "CATEGORY_GROUPS",
    FilterField.EXPENSE_CATEGORY: "CATEGORY_NAME",
    FilterField.IS_CORE_EXPENSE: "Is_Core_Expense",
}

_EXPENSE_SILVER_FILTER_MAP: dict[FilterField, str] = {
    FilterField.EXPENSE_GROUP: "CATEGORY_GROUPS",
    FilterField.EXPENSE_CATEGORY: "CATEGORY_NAME",
    FilterField.IS_CORE_EXPENSE: "Is_Core_Expense",
    FilterField.ASSET_ID: "ASSET_ID",
}


def _coalesce_group(value: Any) -> str:
    text = str(value or "").strip()
    return text or "Unclassified"


def query_expense_efficiency(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return summed expense flows and latest non-additive ratios from `cashflow_efficiency_analytics`."""
    if not table_exists(duck_reader, "gold", "cashflow_efficiency_analytics"):
        return None, None
    if not table_has_column(duck_reader, "gold", "cashflow_efficiency_analytics", "Core_Expense"):
        return None, None

    def _opt(col: str, fn: str, alias: str) -> str:
        if table_has_column(duck_reader, "gold", "cashflow_efficiency_analytics", col):
            return f'{fn}("{col}") AS {alias}'
        return f"NULL AS {alias}"

    flow = duck_reader.fetch_one(
        f"""
        SELECT
            SUM("Total_Expense") AS total_exp,
            SUM("Core_Expense") AS core_exp,
            SUM("NonCore_Expense") AS noncore_exp,
            {_opt("Total_Cash_Expense", "SUM", "cash_exp")},
            {_opt("Total_Non_Cash_Expense", "SUM", "non_cash_exp")},
            COUNT(*) AS n_months,
            MAX("MONTH_END_DATE") AS max_end
        FROM gold.cashflow_efficiency_analytics
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?;
        """,
        (start_date, end_date),
    )
    latest = duck_reader.fetch_one(
        f"""
        SELECT
            "MONTH_END_DATE" AS m_end,
            {_opt("Trailing_3M_Avg_Expense", "MAX", "t3m_exp")},
            {_opt("Core_Expense_Share_Pct", "MAX", "core_share")},
            {_opt("NonCore_Expense_Share_Pct", "MAX", "noncore_share")},
            {_opt("Expense_MoM_Delta", "MAX", "exp_mom_delta")},
            {_opt("Expense_MoM_Pct", "MAX", "exp_mom_pct")},
            {_opt("Expense_to_NW_Ratio", "MAX", "exp_to_nw")}
        FROM gold.cashflow_efficiency_analytics
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
        GROUP BY "MONTH_END_DATE"
        ORDER BY "MONTH_END_DATE" DESC
        LIMIT 1;
        """,
        (start_date, end_date),
    )
    return flow, latest


def query_expense_real_growth(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
) -> dict[str, Any] | None:
    """Fetch canonical inflation-adjusted core spend & real growth from `gold.core_monthly_fact`."""
    if not table_exists(duck_reader, "gold", "core_monthly_fact"):
        return None
    has_real = table_has_column(
        duck_reader, "gold", "core_monthly_fact", "Inflation_Adjusted_Core_Expense"
    )
    has_growth = table_has_column(
        duck_reader, "gold", "core_monthly_fact", "Real_Core_Expense_Growth_Pct"
    )
    has_cpi = table_has_column(duck_reader, "gold", "core_monthly_fact", "INFLATION_YOY_PCT")
    real_expr = '"Inflation_Adjusted_Core_Expense"' if has_real else "NULL"
    growth_expr = '"Real_Core_Expense_Growth_Pct"' if has_growth else "NULL"
    cpi_expr = '"INFLATION_YOY_PCT"' if has_cpi else "NULL"
    return duck_reader.fetch_one(
        f"""
        SELECT
            "MONTH_END_DATE" AS m_end,
            "Total_Core_Expense" AS nominal_core,
            {real_expr} AS real_core,
            {growth_expr} AS real_core_growth,
            {cpi_expr} AS cpi_yoy
        FROM gold.core_monthly_fact
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
        ORDER BY "MONTH_END_DATE" DESC
        LIMIT 1;
        """,
        (start_date, end_date),
    )


def query_expense_monthly_series(
    duck_reader: DuckDBQueryPort,
    *,
    chart_start: date,
    end_date: date,
) -> tuple[ExpenseMonthlyPoint, ...]:
    """Fetch chronological expense composition, cash/non-cash, and real core trajectory."""
    if not table_exists(duck_reader, "gold", "cashflow_efficiency_analytics"):
        return ()
    if not table_has_column(duck_reader, "gold", "cashflow_efficiency_analytics", "Core_Expense"):
        return ()

    has_core_tbl = table_exists(duck_reader, "gold", "core_monthly_fact")
    has_real = has_core_tbl and table_has_column(
        duck_reader, "gold", "core_monthly_fact", "Inflation_Adjusted_Core_Expense"
    )
    has_growth = has_core_tbl and table_has_column(
        duck_reader, "gold", "core_monthly_fact", "Real_Core_Expense_Growth_Pct"
    )
    has_cash = table_has_column(
        duck_reader, "gold", "cashflow_efficiency_analytics", "Total_Cash_Expense"
    )
    has_t3m = table_has_column(
        duck_reader, "gold", "cashflow_efficiency_analytics", "Trailing_3M_Avg_Expense"
    )
    has_nw_ratio = table_has_column(
        duck_reader, "gold", "cashflow_efficiency_analytics", "Expense_to_NW_Ratio"
    )

    cash_expr = 'e."Total_Cash_Expense"' if has_cash else 'e."Total_Expense"'
    non_cash_expr = (
        'e."Total_Non_Cash_Expense"'
        if table_has_column(
            duck_reader, "gold", "cashflow_efficiency_analytics", "Total_Non_Cash_Expense"
        )
        else "0.0"
    )
    t3m_expr = 'e."Trailing_3M_Avg_Expense"' if has_t3m else "NULL"
    nw_expr = 'e."Expense_to_NW_Ratio"' if has_nw_ratio else "NULL"
    core_join = (
        'LEFT JOIN gold.core_monthly_fact c ON e."MONTH_END_DATE" = c."MONTH_END_DATE"'
        if has_core_tbl and (has_real or has_growth)
        else ""
    )
    real_expr = 'c."Inflation_Adjusted_Core_Expense"' if has_real else "NULL"
    growth_expr = 'c."Real_Core_Expense_Growth_Pct"' if has_growth else "NULL"

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            e."MONTH_END_DATE" AS m_end,
            e."Total_Expense" AS total_exp,
            e."Core_Expense" AS core_exp,
            e."NonCore_Expense" AS noncore_exp,
            {cash_expr} AS cash_exp,
            {non_cash_expr} AS non_cash_exp,
            {real_expr} AS real_core,
            {growth_expr} AS real_growth,
            {t3m_expr} AS t3m_exp,
            {nw_expr} AS exp_to_nw
        FROM gold.cashflow_efficiency_analytics e
        {core_join}
        WHERE e."MONTH_END_DATE" >= ? AND e."MONTH_START_DATE" <= ?
        ORDER BY e."MONTH_END_DATE" ASC;
        """,
        (chart_start, end_date),
    )
    points: list[ExpenseMonthlyPoint] = []
    for row in rows:
        dt = as_date(row.get("m_end"))
        tot = as_float(row.get("total_exp"))
        if dt is None or tot is None:
            continue
        points.append(
            ExpenseMonthlyPoint(
                month_end=dt,
                total_expense=tot,
                core_expense=as_float(row.get("core_exp")),
                non_core_expense=as_float(row.get("noncore_exp")),
                cash_expense=as_float(row.get("cash_exp")),
                non_cash_expense=as_float(row.get("non_cash_exp")),
                inflation_adjusted_core=as_float(row.get("real_core")),
                real_core_growth_pct=as_float(row.get("real_growth")),
                trailing_3m_avg=as_float(row.get("t3m_exp")),
                expense_to_nw_ratio=as_float(row.get("exp_to_nw")),
            )
        )
    return tuple(points)


def query_expense_category_breakdown(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
    *,
    filters: FilterSet,
) -> tuple[tuple[ExpenseCategoryRow, ...], tuple[ExpenseGroupRow, ...], float]:
    """Return Gold expense category & group rollups plus gross positive spend total."""
    if not table_exists(duck_reader, "gold", "cashflow_expense_breakdown"):
        return (), (), 0.0

    sub_filter = slice_filters(filters, _EXPENSE_GOLD_FILTER_MAP)
    where_extra, f_params = compile_filter_clause(sub_filter, _EXPENSE_GOLD_FILTER_MAP)

    has_cash = table_has_column(duck_reader, "gold", "cashflow_expense_breakdown", "Cash_Spend")
    has_t3m = table_has_column(
        duck_reader, "gold", "cashflow_expense_breakdown", "Trailing_3M_Avg_Spend"
    )
    has_ytd = table_has_column(
        duck_reader, "gold", "cashflow_expense_breakdown", "Cumulative_YTD_Spend"
    )
    cash_expr = 'SUM(COALESCE("Cash_Spend", 0.0))' if has_cash else 'SUM("Total_Monthly_Spend")'
    non_cash_expr = 'SUM(COALESCE("Non_Cash_Spend", 0.0))' if has_cash else "0.0"
    t3m_expr = 'ARG_MAX("Trailing_3M_Avg_Spend", "MONTH_END_DATE")' if has_t3m else "NULL"
    ytd_expr = 'ARG_MAX("Cumulative_YTD_Spend", "MONTH_END_DATE")' if has_ytd else "NULL"

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            COALESCE(CAST("CATEGORY_ID" AS VARCHAR), "CATEGORY_NAME") AS cat_id,
            "CATEGORY_NAME" AS cat_name,
            COALESCE(NULLIF(TRIM("CATEGORY_GROUPS"), ''), 'Unclassified') AS cat_grp,
            COALESCE(MAX("Spend_Type"), 'Variable') AS spend_type,
            BOOL_OR(COALESCE("Is_Core_Expense", FALSE)) AS is_core,
            BOOL_OR(COALESCE("Is_Investment", FALSE)) AS is_inv,
            BOOL_OR(COALESCE("Is_Discretionary", FALSE)) AS is_disc,
            SUM("Total_Monthly_Spend") AS amt,
            {cash_expr} AS cash_amt,
            {non_cash_expr} AS nc_amt,
            {t3m_expr} AS t3m,
            {ytd_expr} AS ytd
        FROM gold.cashflow_expense_breakdown
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
          AND ({where_extra})
        GROUP BY 1, 2, 3
        HAVING ABS(SUM("Total_Monthly_Spend")) >= 0.01
        ORDER BY amt DESC;
        """,
        [start_date, end_date, *f_params],
    )

    gross_pos = sum(float(r["amt"]) for r in rows if (as_float(r.get("amt")) or 0.0) > 0)
    cat_rows: list[ExpenseCategoryRow] = []
    grp_acc: dict[str, dict[str, Any]] = {}
    for r in rows:
        amt = as_float(r.get("amt"))
        if amt is None:
            continue
        grp = _coalesce_group(r.get("cat_grp"))
        stype = str(r.get("spend_type") or "Variable")
        is_core = bool(r.get("is_core"))
        cash_amt = as_float(r.get("cash_amt")) or 0.0
        nc_amt = as_float(r.get("nc_amt")) or 0.0
        cat_rows.append(
            ExpenseCategoryRow(
                category_id=str(r["cat_id"]),
                category_name=str(r["cat_name"]),
                category_group=grp,
                spend_type=stype,
                is_core=is_core,
                is_investment=bool(r.get("is_inv")),
                is_discretionary=bool(r.get("is_disc")),
                amount=amt,
                cash_amount=cash_amt,
                non_cash_amount=nc_amt,
                trailing_3m_avg=as_float(r.get("t3m")),
                cumulative_ytd=as_float(r.get("ytd")),
                share_pct=(amt / gross_pos) if gross_pos > 0 and amt > 0 else None,
            )
        )
        entry = grp_acc.setdefault(
            grp,
            {
                "amt": 0.0,
                "cash": 0.0,
                "nc": 0.0,
                "cnt": 0,
                "core": False,
                "spend_type": stype,
            },
        )
        entry["amt"] += amt
        entry["cash"] += cash_amt
        entry["nc"] += nc_amt
        entry["cnt"] += 1
        entry["core"] = entry["core"] or is_core

    group_rows = tuple(
        ExpenseGroupRow(
            category_group=grp,
            spend_type=str(info["spend_type"]),
            is_core=bool(info["core"]),
            amount=float(info["amt"]),
            cash_amount=float(info["cash"]),
            non_cash_amount=float(info["nc"]),
            share_pct=(float(info["amt"]) / gross_pos)
            if gross_pos > 0 and float(info["amt"]) > 0
            else None,
            category_count=int(info["cnt"]),
        )
        for grp, info in sorted(grp_acc.items(), key=lambda kv: float(kv[1]["amt"]), reverse=True)
    )
    return tuple(cat_rows), group_rows, gross_pos


def query_expense_heatmap(
    duck_reader: DuckDBQueryPort,
    *,
    chart_start: date,
    end_date: date,
    top_groups: tuple[str, ...],
    filters: FilterSet,
) -> ExpenseHeatmapData:
    """Fetch Month × Expense Group spend matrix for top groups."""
    if not top_groups or not table_exists(duck_reader, "gold", "cashflow_expense_breakdown"):
        return ExpenseHeatmapData()
    sub_filter = slice_filters(filters, _EXPENSE_GOLD_FILTER_MAP)
    where_extra, f_params = compile_filter_clause(sub_filter, _EXPENSE_GOLD_FILTER_MAP)
    grp_list = list(top_groups[:8])
    placeholders = ", ".join("?" for _ in grp_list)
    rows = duck_reader.fetch_all(
        f"""
        SELECT
            "YEAR_MONTH" AS ym,
            COALESCE(NULLIF(TRIM("CATEGORY_GROUPS"), ''), 'Unclassified') AS grp,
            SUM("Total_Monthly_Spend") AS amt
        FROM gold.cashflow_expense_breakdown
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
          AND COALESCE(NULLIF(TRIM("CATEGORY_GROUPS"), ''), 'Unclassified') IN ({placeholders})
          AND ({where_extra})
        GROUP BY 1, 2
        ORDER BY 1 ASC;
        """,
        [chart_start, end_date, *grp_list, *f_params],
    )
    months = tuple(sorted({str(r["ym"]) for r in rows if r.get("ym")}))
    if not months:
        return ExpenseHeatmapData()
    cell_map: dict[tuple[str, str], float] = {
        (str(r["grp"]), str(r["ym"])): float(r["amt"])
        for r in rows
        if as_float(r.get("amt")) is not None
    }
    ordered_groups = tuple(reversed(grp_list))
    matrix = tuple(tuple(cell_map.get((g, m)) for m in months) for g in ordered_groups)
    return ExpenseHeatmapData(months=months, groups=ordered_groups, matrix=matrix)


def query_expense_account_mix(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
    *,
    filters: FilterSet | None = None,
) -> tuple[ExpenseAccountMixPoint, ...]:
    """Summarize payment/funding accounts from Silver expense transactions with full denominator."""
    if not table_exists(duck_reader, "silver", "fact_expense_transactions"):
        return ()
    if not table_has_column(duck_reader, "silver", "fact_expense_transactions", "BASE_AMOUNT"):
        return ()

    has_acc = table_exists(duck_reader, "silver", "dim_asset_subcategory")
    has_sub = table_exists(duck_reader, "silver", "dim_expense_subcategory")
    has_cat = table_exists(duck_reader, "silver", "dim_expense_category")

    joins: list[str] = []
    if has_acc:
        joins.append('LEFT JOIN silver.dim_asset_subcategory a ON t."ASSET_ID" = a."UID"')
    if has_sub:
        joins.append('LEFT JOIN silver.dim_expense_subcategory sc ON t."CATEGORY_ID" = sc."UID"')
    if has_cat and has_sub:
        joins.append(
            'LEFT JOIN silver.dim_expense_category c ON COALESCE(sc."CATEGORY_ID", t."CATEGORY_ID") = c."UID"'
        )
    elif has_cat:
        joins.append('LEFT JOIN silver.dim_expense_category c ON t."CATEGORY_ID" = c."UID"')

    join_sql = "\n            ".join(joins)
    acc_expr = (
        'COALESCE(a."ASSET_NAME", t."ASSET_ID", \'Unknown Account\')'
        if has_acc
        else "COALESCE(t.\"ASSET_ID\", 'Unknown Account')"
    )
    cat_expr = (
        'COALESCE(sc."CATEGORY_NAME", c."CATEGORY_NAME", t."CATEGORY_ID", \'Unclassified\')'
        if has_sub and has_cat
        else (
            'COALESCE(sc."CATEGORY_NAME", t."CATEGORY_ID", \'Unclassified\')'
            if has_sub
            else "COALESCE(t.\"CATEGORY_ID\", 'Unclassified')"
        )
    )
    grp_expr = (
        "COALESCE(NULLIF(TRIM(c.\"CATEGORY_NAME\"), ''), 'Unclassified')"
        if has_cat
        else "'Unclassified'"
    )

    sub_filter = slice_filters(filters or FilterSet(), _EXPENSE_SILVER_FILTER_MAP)
    if sub_filter.filters:
        where_extra, f_params = compile_filter_clause(sub_filter, _EXPENSE_SILVER_FILTER_MAP)
    else:
        where_extra, f_params = "1=1", []

    rows = duck_reader.fetch_all(
        f"""
        WITH joined AS (
            SELECT
                {acc_expr} AS acc_name,
                {cat_expr} AS CATEGORY_NAME,
                {grp_expr} AS CATEGORY_GROUPS,
                CAST(t."ASSET_ID" AS VARCHAR) AS ASSET_ID,
                COALESCE(t."Is_Core_Expense", FALSE) AS Is_Core_Expense,
                t."BASE_AMOUNT" AS base_amount
            FROM silver.fact_expense_transactions t
            {join_sql}
            WHERE TRY_CAST(t."DATE" AS DATE) BETWEEN ? AND ?
        )
        SELECT
            acc_name,
            SUM(base_amount) AS amt,
            COUNT(*) AS tx_cnt,
            SUM(SUM(base_amount)) OVER () AS total_acc_amt
        FROM joined
        WHERE ({where_extra})
        GROUP BY 1
        HAVING ABS(SUM(base_amount)) >= 0.01
        ORDER BY amt DESC
        LIMIT 10;
        """,
        [start_date, end_date, *f_params],
    )
    total_acc = (as_float(rows[0].get("total_acc_amt")) if rows else None) or sum(
        as_float(r.get("amt")) or 0.0 for r in rows
    )
    return tuple(
        ExpenseAccountMixPoint(
            account_name=str(r["acc_name"]),
            amount=float(r["amt"]),
            share_pct=(float(r["amt"]) / total_acc) if total_acc > 0 else None,
            tx_count=int(r.get("tx_cnt") or 0),
        )
        for r in rows
        if as_float(r.get("amt")) is not None
    )


def query_expense_transactions(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
    *,
    filters: FilterSet,
    drill_group: str | None = None,
    drill_category: str | None = None,
    flag_filter: str | None = None,
    account_filter: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> ExpenseTransactionPage:
    """Fetch bounded, paginated Silver expense transactions with joined labels & audit fields."""
    if not table_exists(duck_reader, "silver", "fact_expense_transactions"):
        return ExpenseTransactionPage(page=page, page_size=page_size)
    if not table_has_column(duck_reader, "silver", "fact_expense_transactions", "BASE_AMOUNT"):
        return ExpenseTransactionPage(page=page, page_size=page_size)

    has_sub = table_exists(duck_reader, "silver", "dim_expense_subcategory")
    has_cat = table_exists(duck_reader, "silver", "dim_expense_category")
    has_acc = table_exists(duck_reader, "silver", "dim_asset_subcategory")
    has_paid = table_has_column(duck_reader, "silver", "fact_expense_transactions", "PAID")

    joins: list[str] = []
    if has_sub:
        joins.append('LEFT JOIN silver.dim_expense_subcategory sc ON t."CATEGORY_ID" = sc."UID"')
    if has_cat and has_sub:
        joins.append(
            'LEFT JOIN silver.dim_expense_category c ON COALESCE(sc."CATEGORY_ID", t."CATEGORY_ID") = c."UID"'
        )
    elif has_cat:
        joins.append('LEFT JOIN silver.dim_expense_category c ON t."CATEGORY_ID" = c."UID"')
    if has_acc:
        joins.append('LEFT JOIN silver.dim_asset_subcategory a ON t."ASSET_ID" = a."UID"')

    join_sql = "\n        ".join(joins)
    cat_expr = (
        'COALESCE(sc."CATEGORY_NAME", c."CATEGORY_NAME", t."CATEGORY_ID", \'Unclassified\')'
        if has_sub and has_cat
        else (
            'COALESCE(sc."CATEGORY_NAME", t."CATEGORY_ID", \'Unclassified\')'
            if has_sub
            else "COALESCE(t.\"CATEGORY_ID\", 'Unclassified')"
        )
    )
    parent_expr = "COALESCE(c.\"CATEGORY_NAME\", 'Unclassified')" if has_cat else "'Unclassified'"
    grp_expr = parent_expr
    acc_expr = (
        'COALESCE(a."ASSET_NAME", t."ASSET_ID", \'Unknown\')'
        if has_acc
        else "COALESCE(t.\"ASSET_ID\", 'Unknown')"
    )
    paid_expr = "COALESCE(CAST(t.\"PAID\" AS VARCHAR), '')" if has_paid else "''"

    where_clauses = ['TRY_CAST(t."DATE" AS DATE) BETWEEN ? AND ?']
    params: list[Any] = [start_date, end_date]

    sub_filter = slice_filters(filters, _EXPENSE_SILVER_FILTER_MAP)
    if sub_filter.filters:
        f_sql, f_params = compile_filter_clause(sub_filter, _EXPENSE_SILVER_FILTER_MAP)
    else:
        f_sql, f_params = "1=1", []

    if drill_group:
        where_clauses.append(f"{grp_expr} = ?")
        params.append(drill_group)
    if drill_category:
        where_clauses.append(f"{cat_expr} = ?")
        params.append(drill_category)
    if account_filter:
        where_clauses.append(f"{acc_expr} = ?")
        params.append(account_filter)

    if flag_filter == "core":
        where_clauses.append('COALESCE(t."Is_Core_Expense", FALSE) = TRUE')
    elif flag_filter == "non_core":
        where_clauses.append('COALESCE(t."Is_Core_Expense", FALSE) = FALSE')
    elif flag_filter == "refunds":
        where_clauses.append('t."BASE_AMOUNT" < 0')

    where_sql = " AND ".join(where_clauses)
    base_cte = f"""
        WITH joined AS (
            SELECT
                CAST(t."UID" AS VARCHAR) AS uid,
                TRY_CAST(t."DATE" AS DATE) AS tx_date,
                {cat_expr} AS CATEGORY_NAME,
                {grp_expr} AS CATEGORY_GROUPS,
                {parent_expr} AS parent_category,
                {acc_expr} AS account_name,
                CAST(t."ASSET_ID" AS VARCHAR) AS ASSET_ID,
                COALESCE(t."DESCRIPTION", '') AS description,
                {paid_expr} AS paid,
                COALESCE(t."CURRENCY_ID", 'INR_INR') AS currency_id,
                t."BASE_AMOUNT" AS base_amount,
                t."AMOUNT_ACCOUNT" AS account_amount,
                t."LOCAL_AMOUNT" AS local_amount,
                t."EXCH_RATE" AS exch_rate,
                COALESCE(t."Is_Core_Expense", FALSE) AS Is_Core_Expense
            FROM silver.fact_expense_transactions t
            {join_sql}
            WHERE {where_sql}
        )
    """
    agg_row = duck_reader.fetch_one(
        f"""
        {base_cte}
        SELECT
            COUNT(*) AS total_cnt,
            COALESCE(SUM(base_amount), 0.0) AS total_base
        FROM joined
        WHERE {f_sql};
        """,
        [*params, *f_params],
    )
    total_count = int(agg_row.get("total_cnt") or 0) if agg_row else 0
    total_base = (as_float(agg_row.get("total_base")) or 0.0) if agg_row else 0.0

    safe_page = max(1, page)
    safe_size = max(1, min(page_size, 200))
    offset = (safe_page - 1) * safe_size

    rows = duck_reader.fetch_all(
        f"""
        {base_cte}
        SELECT *
        FROM joined
        WHERE {f_sql}
        ORDER BY tx_date DESC, ABS(base_amount) DESC
        LIMIT ? OFFSET ?;
        """,
        [*params, *f_params, safe_size, offset],
    )
    items: list[ExpenseTransactionRow] = []
    for r in rows:
        dt = as_date(r.get("tx_date"))
        amt = as_float(r.get("base_amount"))
        if dt is None or amt is None:
            continue
        items.append(
            ExpenseTransactionRow(
                uid=str(r["uid"]),
                tx_date=dt,
                category_name=str(r["CATEGORY_NAME"]),
                category_group=str(r["CATEGORY_GROUPS"]),
                parent_category=str(r["parent_category"]),
                account_name=str(r["account_name"]),
                description=str(r["description"]),
                paid=str(r.get("paid") or ""),
                currency_id=str(r["currency_id"]),
                base_amount=amt,
                account_amount=as_float(r.get("account_amount")),
                local_amount=as_float(r.get("local_amount")),
                exch_rate=as_float(r.get("exch_rate")),
                is_core=bool(r.get("Is_Core_Expense")),
            )
        )
    return ExpenseTransactionPage(
        rows=tuple(items),
        total_count=total_count,
        total_base_amount=total_base,
        page=safe_page,
        page_size=safe_size,
    )
