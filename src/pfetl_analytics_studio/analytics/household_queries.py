"""Read-only Gold & Silver SQL query helpers powering `HouseholdAnalytics`.

Owns
----
- Parameterized Gold income summary, monthly trend, category/group rollup, and budget queries.
- Parameterized Silver transaction drill (`silver.fact_income_transactions`) joined with
  `dim_income_subcategory`, `dim_income_category`, and `dim_asset_subcategory`.

Does not own
------------
- NiceGUI widgets or PFETL financial classification methodology.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pfetl_analytics_studio.analytics.household_models import (
    IncomeAccountMixPoint,
    IncomeCategoryRow,
    IncomeGroupRow,
    IncomeMonthlyPoint,
    IncomeTaxabilitySummary,
    IncomeTransactionPage,
    IncomeTransactionRow,
)
from pfetl_analytics_studio.analytics.overview_models import as_date, as_float, slice_filters
from pfetl_analytics_studio.analytics.overview_queries import table_exists, table_has_column
from pfetl_analytics_studio.data import DuckDBQueryPort
from pfetl_analytics_studio.semantic import FilterField, FilterSet, compile_filter_clause

_INCOME_GOLD_FILTER_MAP: dict[FilterField, str] = {
    FilterField.INCOME_GROUP: "CATEGORY_GROUPS",
    FilterField.INCOME_CATEGORY: "CATEGORY_NAME",
    FilterField.IS_ACTIVE_INCOME: "Is_Active_Income",
    FilterField.IS_PASSIVE_INCOME: "Is_Passive_Income",
}

_INCOME_SILVER_FILTER_MAP: dict[FilterField, str] = {
    FilterField.INCOME_GROUP: "CATEGORY_GROUPS",
    FilterField.INCOME_CATEGORY: "CATEGORY_NAME",
    FilterField.IS_ACTIVE_INCOME: "Is_Active_Income",
    FilterField.IS_PASSIVE_INCOME: "Is_Passive_Income",
    FilterField.ASSET_ID: "ASSET_ID",
}


def _coalesce_group(value: Any) -> str:
    text = str(value or "").strip()
    return text or "Unclassified"


def query_income_efficiency(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return summed income flows and latest non-additive ratios from `cashflow_efficiency_analytics`."""
    if not table_exists(duck_reader, "gold", "cashflow_efficiency_analytics"):
        return None, None
    has_rich = table_has_column(
        duck_reader, "gold", "cashflow_efficiency_analytics", "Active_Income"
    )
    if not has_rich:
        return None, None

    def _opt(col: str, fn: str, alias: str) -> str:
        if table_has_column(duck_reader, "gold", "cashflow_efficiency_analytics", col):
            return f'{fn}("{col}") AS {alias}'
        return f"NULL AS {alias}"

    flow = duck_reader.fetch_one(
        f"""
        SELECT
            SUM("Total_Income") AS total_inc,
            {_opt("Total_Cash_Income", "SUM", "cash_inc")},
            {_opt("Total_Non_Cash_Income", "SUM", "non_cash_inc")},
            SUM("Active_Income") AS act_inc,
            SUM("Passive_Income") AS pass_inc,
            SUM("Dividend_Income") AS div_inc,
            SUM("Interest_Income") AS int_inc,
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
            {_opt("Trailing_3M_Avg_Income", "MAX", "t3m_inc")} ,
            "Savings_Rate_Pct",
            "Passive_Income_Core_Expense_Coverage_Ratio",
            "Active_Income_Dependency_Pct",
            {_opt("Active_Income_Share_Pct", "MAX", "act_share")},
            {_opt("Passive_Income_Share_Pct", "MAX", "pass_share")},
            {_opt("Income_MoM_Delta", "MAX", "inc_mom_delta")},
            {_opt("Income_MoM_Pct", "MAX", "inc_mom_pct")},
            "Is_Surplus_Month"
        FROM gold.cashflow_efficiency_analytics
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
        GROUP BY "MONTH_END_DATE", "Savings_Rate_Pct",
                 "Passive_Income_Core_Expense_Coverage_Ratio",
                 "Active_Income_Dependency_Pct", "Is_Surplus_Month"
        ORDER BY "MONTH_END_DATE" DESC
        LIMIT 1;
        """,
        (start_date, end_date),
    )
    return flow, latest


def query_income_budget(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
) -> dict[str, Any] | None:
    """Return budget income target, actual income, and volatility flag cleanly."""
    if not table_exists(duck_reader, "gold", "forecast_budget_variance"):
        return None
    if not table_has_column(duck_reader, "gold", "forecast_budget_variance", "Budget_Income"):
        return None
    has_vol = table_has_column(
        duck_reader, "gold", "forecast_budget_variance", "Is_Income_Volatile"
    )
    vol_expr = 'BOOL_OR(COALESCE("Is_Income_Volatile", FALSE))' if has_vol else "FALSE"
    return duck_reader.fetch_one(
        f"""
        SELECT
            SUM("Budget_Income") AS budget_inc,
            SUM("Actual_Income") AS actual_inc,
            SUM("Total_Income") AS total_inc,
            {vol_expr} AS is_volatile
        FROM gold.forecast_budget_variance
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?;
        """,
        (start_date, end_date),
    )


def query_income_monthly_series(
    duck_reader: DuckDBQueryPort,
    *,
    chart_start: date,
    end_date: date,
) -> tuple[IncomeMonthlyPoint, ...]:
    """Fetch chronological income composition, 3M smoothing, budget, and YTD series."""
    if not table_exists(duck_reader, "gold", "cashflow_efficiency_analytics"):
        return ()
    has_rich = table_has_column(
        duck_reader, "gold", "cashflow_efficiency_analytics", "Active_Income"
    )
    if not has_rich:
        return ()

    has_budget = table_exists(duck_reader, "gold", "forecast_budget_variance") and table_has_column(
        duck_reader, "gold", "forecast_budget_variance", "Budget_Income"
    )
    has_cash = table_has_column(
        duck_reader, "gold", "cashflow_efficiency_analytics", "Total_Cash_Income"
    )
    has_t3m = table_has_column(
        duck_reader, "gold", "cashflow_efficiency_analytics", "Trailing_3M_Avg_Income"
    )

    cash_expr = 'e."Total_Cash_Income"' if has_cash else 'e."Total_Income"'
    non_cash_expr = (
        'e."Total_Non_Cash_Income"'
        if table_has_column(
            duck_reader, "gold", "cashflow_efficiency_analytics", "Total_Non_Cash_Income"
        )
        else "0.0"
    )
    t3m_expr = 'e."Trailing_3M_Avg_Income"' if has_t3m else "NULL"
    bud_join = (
        'LEFT JOIN gold.forecast_budget_variance b ON e."MONTH_END_DATE" = b."MONTH_END_DATE"'
        if has_budget
        else ""
    )
    bud_expr = 'b."Budget_Income"' if has_budget else "NULL"

    ytd_map: dict[date, float] = {}
    if table_exists(duck_reader, "gold", "cashflow_income_breakdown") and table_has_column(
        duck_reader, "gold", "cashflow_income_breakdown", "Cumulative_YTD_Income"
    ):
        ytd_rows = duck_reader.fetch_all(
            """
            SELECT
                "MONTH_END_DATE" AS m_end,
                SUM("Cumulative_YTD_Income") AS ytd_inc
            FROM gold.cashflow_income_breakdown
            WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
            GROUP BY "MONTH_END_DATE";
            """,
            (chart_start, end_date),
        )
        for yr in ytd_rows:
            dt = as_date(yr.get("m_end"))
            val = as_float(yr.get("ytd_inc"))
            if dt is not None and val is not None:
                ytd_map[dt] = val

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            e."MONTH_END_DATE" AS m_end,
            e."Total_Income" AS total_inc,
            {cash_expr} AS cash_inc,
            {non_cash_expr} AS non_cash_inc,
            e."Active_Income" AS act_inc,
            e."Passive_Income" AS pass_inc,
            e."Dividend_Income" AS div_inc,
            e."Interest_Income" AS int_inc,
            {t3m_expr} AS t3m_inc,
            {bud_expr} AS budget_inc,
            e."Active_Income_Dependency_Pct" AS act_dep,
            e."Passive_Income_Core_Expense_Coverage_Ratio" AS pass_cov
        FROM gold.cashflow_efficiency_analytics e
        {bud_join}
        WHERE e."MONTH_END_DATE" >= ? AND e."MONTH_START_DATE" <= ?
        ORDER BY e."MONTH_END_DATE" ASC;
        """,
        (chart_start, end_date),
    )
    points: list[IncomeMonthlyPoint] = []
    for row in rows:
        dt = as_date(row.get("m_end"))
        tot = as_float(row.get("total_inc"))
        if dt is None or tot is None:
            continue
        points.append(
            IncomeMonthlyPoint(
                month_end=dt,
                total_income=tot,
                cash_income=as_float(row.get("cash_inc")),
                non_cash_income=as_float(row.get("non_cash_inc")),
                active_income=as_float(row.get("act_inc")),
                passive_income=as_float(row.get("pass_inc")),
                dividend_income=as_float(row.get("div_inc")),
                interest_income=as_float(row.get("int_inc")),
                trailing_3m_avg=as_float(row.get("t3m_inc")),
                budget_income=as_float(row.get("budget_inc")),
                cumulative_ytd=ytd_map.get(dt),
                active_dependency_pct=as_float(row.get("act_dep")),
                passive_core_coverage=as_float(row.get("pass_cov")),
            )
        )
    return tuple(points)


def query_income_category_breakdown(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
    *,
    filters: FilterSet,
) -> tuple[tuple[IncomeCategoryRow, ...], tuple[IncomeGroupRow, ...], float, float]:
    """Return Gold category & group rollups plus gross positive and deduction totals."""
    if not table_exists(duck_reader, "gold", "cashflow_income_breakdown"):
        return (), (), 0.0, 0.0

    sub_filter = slice_filters(filters, _INCOME_GOLD_FILTER_MAP)
    where_extra, f_params = compile_filter_clause(sub_filter, _INCOME_GOLD_FILTER_MAP)

    has_sub_dim = table_exists(
        duck_reader, "silver", "dim_income_subcategory"
    ) and table_has_column(duck_reader, "silver", "dim_income_subcategory", "Taxability")
    tax_join = (
        'LEFT JOIN silver.dim_income_subcategory sc ON g."CATEGORY_ID" = sc."UID"'
        if has_sub_dim
        else ""
    )
    tax_expr = (
        "COALESCE(MAX(sc.\"Taxability\"), 'unclassified')" if has_sub_dim else "'unclassified'"
    )

    has_t3m = table_has_column(
        duck_reader, "gold", "cashflow_income_breakdown", "Trailing_3M_Avg_Income"
    )
    has_ytd = table_has_column(
        duck_reader, "gold", "cashflow_income_breakdown", "Cumulative_YTD_Income"
    )
    t3m_expr = 'ARG_MAX(g."Trailing_3M_Avg_Income", g."MONTH_END_DATE")' if has_t3m else "NULL"
    ytd_expr = 'ARG_MAX(g."Cumulative_YTD_Income", g."MONTH_END_DATE")' if has_ytd else "NULL"

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            COALESCE(CAST(g."CATEGORY_ID" AS VARCHAR), g."CATEGORY_NAME") AS cat_id,
            g."CATEGORY_NAME" AS cat_name,
            COALESCE(NULLIF(TRIM(g."CATEGORY_GROUPS"), ''), 'Unclassified') AS cat_grp,
            SUM(g."Total_Monthly_Income") AS amt,
            {t3m_expr} AS t3m,
            {ytd_expr} AS ytd,
            BOOL_OR(COALESCE(g."Is_Active_Income", FALSE)) AS is_act,
            BOOL_OR(COALESCE(g."Is_Passive_Income", FALSE)) AS is_pass,
            BOOL_OR(COALESCE(g."Is_Dividend_Income", FALSE)) AS is_div,
            BOOL_OR(COALESCE(g."Is_Interest_Income", FALSE)) AS is_int,
            BOOL_OR(COALESCE(g."Is_Non_Cash_Income", FALSE)) AS is_nc,
            {tax_expr} AS taxability
        FROM gold.cashflow_income_breakdown g
        {tax_join}
        WHERE g."MONTH_END_DATE" >= ? AND g."MONTH_START_DATE" <= ?
          AND ({where_extra})
        GROUP BY 1, 2, 3
        HAVING ABS(SUM(g."Total_Monthly_Income")) >= 0.01
        ORDER BY amt DESC;
        """,
        [start_date, end_date, *f_params],
    )

    gross_pos = sum(float(r["amt"]) for r in rows if (as_float(r.get("amt")) or 0.0) > 0)
    deductions = sum(float(r["amt"]) for r in rows if (as_float(r.get("amt")) or 0.0) < 0)

    cat_rows: list[IncomeCategoryRow] = []
    grp_totals: dict[str, tuple[float, int]] = {}
    for r in rows:
        amt = as_float(r.get("amt"))
        if amt is None:
            continue
        grp = _coalesce_group(r.get("cat_grp"))
        share = (amt / gross_pos) if gross_pos > 0 and amt > 0 else None
        cat_rows.append(
            IncomeCategoryRow(
                category_id=str(r["cat_id"]),
                category_name=str(r["cat_name"]),
                category_group=grp,
                amount=amt,
                trailing_3m_avg=as_float(r.get("t3m")),
                cumulative_ytd=as_float(r.get("ytd")),
                share_pct=share,
                is_active=bool(r.get("is_act")),
                is_passive=bool(r.get("is_pass")),
                is_dividend=bool(r.get("is_div")),
                is_interest=bool(r.get("is_int")),
                is_non_cash=bool(r.get("is_nc")),
                taxability=str(r.get("taxability") or "unclassified"),
            )
        )
        prev_amt, prev_cnt = grp_totals.get(grp, (0.0, 0))
        grp_totals[grp] = (prev_amt + amt, prev_cnt + 1)

    group_rows = tuple(
        IncomeGroupRow(
            category_group=grp,
            amount=tot,
            share_pct=(tot / gross_pos) if gross_pos > 0 and tot > 0 else None,
            category_count=cnt,
        )
        for grp, (tot, cnt) in sorted(grp_totals.items(), key=lambda kv: kv[1][0], reverse=True)
    )
    return tuple(cat_rows), group_rows, gross_pos, deductions


def query_income_account_mix_and_taxability(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
    *,
    filters: FilterSet | None = None,
) -> tuple[tuple[IncomeAccountMixPoint, ...], IncomeTaxabilitySummary]:
    """Summarize receiving accounts and taxability from Silver income transactions."""
    if not table_exists(duck_reader, "silver", "fact_income_transactions"):
        return (), IncomeTaxabilitySummary()
    if not table_has_column(duck_reader, "silver", "fact_income_transactions", "BASE_AMOUNT"):
        return (), IncomeTaxabilitySummary()

    has_acc = table_exists(duck_reader, "silver", "dim_asset_subcategory")
    has_sub = table_exists(duck_reader, "silver", "dim_income_subcategory")

    acc_join = (
        'LEFT JOIN silver.dim_asset_subcategory a ON t."ASSET_ID" = a."UID"' if has_acc else ""
    )
    sub_join = (
        'LEFT JOIN silver.dim_income_subcategory sc ON t."CATEGORY_ID" = sc."UID"'
        if has_sub
        else ""
    )
    acc_expr = (
        'COALESCE(a."ASSET_NAME", t."ASSET_ID", \'Unknown Account\')'
        if has_acc
        else "COALESCE(t.\"ASSET_ID\", 'Unknown Account')"
    )
    cat_expr = (
        'COALESCE(sc."CATEGORY_NAME", t."CATEGORY_ID", \'Unclassified\')'
        if has_sub
        else "COALESCE(t.\"CATEGORY_ID\", 'Unclassified')"
    )
    grp_expr = (
        "COALESCE(NULLIF(TRIM(sc.\"CATEGORY_GROUPS\"), ''), 'Unclassified')"
        if has_sub
        else "'Unclassified'"
    )
    pass_expr = (
        'COALESCE(sc."Is_Passive_Income", (COALESCE(t."Is_Dividend_Income", FALSE) OR COALESCE(t."Is_Interest_Income", FALSE)))'
        if has_sub
        and table_has_column(duck_reader, "silver", "dim_income_subcategory", "Is_Passive_Income")
        else '(COALESCE(t."Is_Dividend_Income", FALSE) OR COALESCE(t."Is_Interest_Income", FALSE))'
    )
    tax_col = (
        "LOWER(COALESCE(sc.\"Taxability\", 'unclassified'))"
        if has_sub
        and table_has_column(duck_reader, "silver", "dim_income_subcategory", "Taxability")
        else "'unclassified'"
    )

    sub_filter = slice_filters(filters or FilterSet(), _INCOME_SILVER_FILTER_MAP)
    if sub_filter.filters:
        where_extra, f_params = compile_filter_clause(sub_filter, _INCOME_SILVER_FILTER_MAP)
    else:
        where_extra, f_params = "1=1", []

    base_cte = f"""
        WITH joined AS (
            SELECT
                {acc_expr} AS acc_name,
                {cat_expr} AS CATEGORY_NAME,
                {grp_expr} AS CATEGORY_GROUPS,
                CAST(t."ASSET_ID" AS VARCHAR) AS ASSET_ID,
                COALESCE(t."Is_Active_Income", FALSE) AS Is_Active_Income,
                {pass_expr} AS Is_Passive_Income,
                {tax_col} AS taxability,
                t."BASE_AMOUNT" AS base_amount
            FROM silver.fact_income_transactions t
            {acc_join}
            {sub_join}
            WHERE TRY_CAST(t."DATE" AS DATE) BETWEEN ? AND ?
        )
    """

    acc_rows = duck_reader.fetch_all(
        f"""
        {base_cte}
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
    total_acc = (as_float(acc_rows[0].get("total_acc_amt")) if acc_rows else None) or sum(
        as_float(r.get("amt")) or 0.0 for r in acc_rows
    )
    acc_mix = tuple(
        IncomeAccountMixPoint(
            account_name=str(r["acc_name"]),
            amount=float(r["amt"]),
            share_pct=(float(r["amt"]) / total_acc) if total_acc > 0 else None,
            tx_count=int(r.get("tx_cnt") or 0),
        )
        for r in acc_rows
        if as_float(r.get("amt")) is not None
    )

    tax_summary = IncomeTaxabilitySummary()
    if has_sub and table_has_column(duck_reader, "silver", "dim_income_subcategory", "Taxability"):
        tax_rows = duck_reader.fetch_all(
            f"""
            {base_cte}
            SELECT
                taxability,
                SUM(base_amount) AS amt
            FROM joined
            WHERE ({where_extra})
            GROUP BY 1;
            """,
            [start_date, end_date, *f_params],
        )
        taxable = 0.0
        non_taxable = 0.0
        unclassified = 0.0
        for tr in tax_rows:
            val = as_float(tr.get("amt")) or 0.0
            key = str(tr.get("taxability") or "unclassified")
            if key == "taxable":
                taxable += val
            elif key == "non_taxable":
                non_taxable += val
            else:
                unclassified += val
        denom = taxable + non_taxable
        tax_summary = IncomeTaxabilitySummary(
            taxable_amount=taxable,
            non_taxable_amount=non_taxable,
            unclassified_amount=unclassified,
            taxable_share_pct=(taxable / denom) if denom > 0 else None,
        )

    return acc_mix, tax_summary


def query_income_transactions(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
    *,
    filters: FilterSet,
    drill_group: str | None = None,
    drill_category: str | None = None,
    flag_filter: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> IncomeTransactionPage:
    """Fetch bounded, paginated Silver income transactions with joined dimension labels."""
    if not table_exists(duck_reader, "silver", "fact_income_transactions"):
        return IncomeTransactionPage(page=page, page_size=page_size)
    if not table_has_column(duck_reader, "silver", "fact_income_transactions", "BASE_AMOUNT"):
        return IncomeTransactionPage(page=page, page_size=page_size)

    has_sub = table_exists(duck_reader, "silver", "dim_income_subcategory")
    has_cat = table_exists(duck_reader, "silver", "dim_income_category")
    has_acc = table_exists(duck_reader, "silver", "dim_asset_subcategory")

    joins: list[str] = []
    if has_sub:
        joins.append('LEFT JOIN silver.dim_income_subcategory sc ON t."CATEGORY_ID" = sc."UID"')
    if has_cat and has_sub:
        joins.append(
            'LEFT JOIN silver.dim_income_category c ON COALESCE(sc."CATEGORY_ID", t."CATEGORY_ID") = c."UID"'
        )
    elif has_cat:
        joins.append('LEFT JOIN silver.dim_income_category c ON t."CATEGORY_ID" = c."UID"')
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
    grp_expr = (
        "COALESCE(NULLIF(TRIM(c.\"CATEGORY_NAME\"), ''), NULLIF(TRIM(sc.\"CATEGORY_GROUPS\"), ''), 'Unclassified')"
        if has_cat and has_sub
        else (
            "COALESCE(NULLIF(TRIM(sc.\"CATEGORY_GROUPS\"), ''), 'Unclassified')"
            if has_sub
            else "'Unclassified'"
        )
    )
    acc_expr = (
        'COALESCE(a."ASSET_NAME", t."ASSET_ID", \'Unknown\')'
        if has_acc
        else "COALESCE(t.\"ASSET_ID\", 'Unknown')"
    )
    tax_expr = (
        "COALESCE(sc.\"Taxability\", 'unclassified')"
        if has_sub
        and table_has_column(duck_reader, "silver", "dim_income_subcategory", "Taxability")
        else "'unclassified'"
    )
    pass_expr = (
        'COALESCE(sc."Is_Passive_Income", (COALESCE(t."Is_Dividend_Income", FALSE) OR COALESCE(t."Is_Interest_Income", FALSE)))'
        if has_sub
        and table_has_column(duck_reader, "silver", "dim_income_subcategory", "Is_Passive_Income")
        else '(COALESCE(t."Is_Dividend_Income", FALSE) OR COALESCE(t."Is_Interest_Income", FALSE))'
    )

    where_clauses = ['TRY_CAST(t."DATE" AS DATE) BETWEEN ? AND ?']
    params: list[Any] = [start_date, end_date]

    sub_filter = slice_filters(filters, _INCOME_SILVER_FILTER_MAP)
    if sub_filter.filters:
        f_sql, f_params = compile_filter_clause(sub_filter, _INCOME_SILVER_FILTER_MAP)
        # Wrap as subquery-safe outer filter below
        inner_filter_sql = f_sql
    else:
        inner_filter_sql = "1=1"
        f_params = []

    if drill_group:
        where_clauses.append(f"{grp_expr} = ?")
        params.append(drill_group)
    if drill_category:
        where_clauses.append(f"{cat_expr} = ?")
        params.append(drill_category)

    flag_map = {
        "active": 'COALESCE(t."Is_Active_Income", FALSE) = TRUE',
        "passive": f"{pass_expr} = TRUE",
        "dividend": 'COALESCE(t."Is_Dividend_Income", FALSE) = TRUE',
        "interest": 'COALESCE(t."Is_Interest_Income", FALSE) = TRUE',
        "non_cash": 'COALESCE(t."Is_Non_Cash_Income", FALSE) = TRUE',
        "deductions": 't."BASE_AMOUNT" < 0',
    }
    if flag_filter and flag_filter in flag_map:
        where_clauses.append(flag_map[flag_filter])

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
                COALESCE(t."CURRENCY_ID", 'INR_INR') AS currency_id,
                t."BASE_AMOUNT" AS base_amount,
                t."AMOUNT_ACCOUNT" AS account_amount,
                t."LOCAL_AMOUNT" AS local_amount,
                t."EXCH_RATE" AS exch_rate,
                {tax_expr} AS taxability,
                COALESCE(t."Is_Active_Income", FALSE) AS Is_Active_Income,
                {pass_expr} AS Is_Passive_Income,
                COALESCE(t."Is_Dividend_Income", FALSE) AS Is_Dividend_Income,
                COALESCE(t."Is_Interest_Income", FALSE) AS Is_Interest_Income,
                COALESCE(t."Is_Non_Cash_Income", FALSE) AS Is_Non_Cash_Income
            FROM silver.fact_income_transactions t
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
        WHERE {inner_filter_sql};
        """,
        [*params, *f_params],
    )
    total_count = int(agg_row.get("total_cnt") or 0) if agg_row else 0
    total_base = as_float(agg_row.get("total_base")) or 0.0 if agg_row else 0.0

    safe_page = max(1, page)
    safe_size = max(1, min(page_size, 200))
    offset = (safe_page - 1) * safe_size

    rows = duck_reader.fetch_all(
        f"""
        {base_cte}
        SELECT *
        FROM joined
        WHERE {inner_filter_sql}
        ORDER BY tx_date DESC, ABS(base_amount) DESC
        LIMIT ? OFFSET ?;
        """,
        [*params, *f_params, safe_size, offset],
    )
    items: list[IncomeTransactionRow] = []
    for r in rows:
        dt = as_date(r.get("tx_date"))
        amt = as_float(r.get("base_amount"))
        if dt is None or amt is None:
            continue
        items.append(
            IncomeTransactionRow(
                uid=str(r["uid"]),
                tx_date=dt,
                category_name=str(r["CATEGORY_NAME"]),
                category_group=str(r["CATEGORY_GROUPS"]),
                parent_category=str(r["parent_category"]),
                account_name=str(r["account_name"]),
                description=str(r["description"]),
                currency_id=str(r["currency_id"]),
                base_amount=amt,
                account_amount=as_float(r.get("account_amount")),
                local_amount=as_float(r.get("local_amount")),
                exch_rate=as_float(r.get("exch_rate")),
                taxability=str(r.get("taxability") or "unclassified"),
                is_active=bool(r.get("Is_Active_Income")),
                is_passive=bool(r.get("Is_Passive_Income")),
                is_dividend=bool(r.get("Is_Dividend_Income")),
                is_interest=bool(r.get("Is_Interest_Income")),
                is_non_cash=bool(r.get("Is_Non_Cash_Income")),
            )
        )
    return IncomeTransactionPage(
        rows=tuple(items),
        total_count=total_count,
        total_base_amount=total_base,
        page=safe_page,
        page_size=safe_size,
    )
