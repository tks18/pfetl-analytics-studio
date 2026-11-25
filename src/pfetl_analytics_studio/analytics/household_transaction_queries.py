"""Read-only Silver UNION ALL & per-UID lineage queries for Household Transactions.

Owns
----
- Parameterized `UNION ALL` queries across `silver.fact_income_transactions`,
  `silver.fact_expense_transactions`, and `silver.fact_transfer_transactions` with
  server-side `LIMIT`/`OFFSET` pagination and `GROUP BY` summary aggregates.
- Per-UID raw detail fetch with full classification lineage and `TRANSFER_UID` linkage.

Does not own
------------
- NiceGUI widgets or PFETL classification methodology.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Literal, cast

from pfetl_analytics_studio.analytics.household_models import (
    HouseholdTransactionDetail,
    HouseholdTransactionPage,
    HouseholdTransactionQuery,
    HouseholdTransactionRow,
    HouseholdTransactionSummary,
    TxAccountPoint,
    TxDailyPoint,
)
from pfetl_analytics_studio.analytics.overview_models import as_date, as_float
from pfetl_analytics_studio.analytics.overview_queries import table_exists, table_has_column
from pfetl_analytics_studio.data import DuckDBQueryPort


def _asset_name_col(duck_reader: DuckDBQueryPort) -> str | None:
    if not table_exists(duck_reader, "silver", "dim_asset_subcategory"):
        return None
    for cand in ("ASSET_NAME", "NAME"):
        if table_has_column(duck_reader, "silver", "dim_asset_subcategory", cand):
            return cand
    return None


def _opt_col(
    duck_reader: DuckDBQueryPort,
    schema: str,
    table: str,
    col: str,
    alias_prefix: str = "f",
    fallback: str = "NULL",
) -> str:
    if table_has_column(duck_reader, schema, table, col):
        return f'{alias_prefix}."{col}"'
    return fallback


def _build_union_sql(duck_reader: DuckDBQueryPort) -> str | None:
    """Build normalized UNION ALL SQL across existing Silver income/expense/transfer facts."""
    name_col = _asset_name_col(duck_reader)
    branches: list[str] = []

    if table_exists(duck_reader, "silver", "fact_income_transactions"):
        tbl = "fact_income_transactions"
        has_sub = table_exists(duck_reader, "silver", "dim_income_subcategory")
        has_cat = has_sub and table_exists(duck_reader, "silver", "dim_income_category")
        sub_join = (
            'LEFT JOIN silver.dim_income_subcategory s ON f."CATEGORY_ID" = s."UID"'
            if has_sub
            else ""
        )
        cat_join = (
            'LEFT JOIN silver.dim_income_category c ON s."CATEGORY_ID" = c."UID"' if has_cat else ""
        )
        src_join = (
            'LEFT JOIN silver.dim_asset_subcategory a ON f."ASSET_ID" = a."UID"' if name_col else ""
        )
        asset_expr = (
            f'COALESCE(CAST(a."{name_col}" AS VARCHAR), CAST(f."ASSET_ID" AS VARCHAR), \'\')'
            if name_col
            else "COALESCE(CAST(f.\"ASSET_ID\" AS VARCHAR), '')"
        )
        cat_expr = "COALESCE(CAST(c.\"CATEGORY_NAME\" AS VARCHAR), '')" if has_cat else "''"
        sub_expr = (
            'COALESCE(CAST(s."CATEGORY_NAME" AS VARCHAR), CAST(f."CATEGORY_ID" AS VARCHAR), \'\')'
            if has_sub
            else "COALESCE(CAST(f.\"CATEGORY_ID\" AS VARCHAR), '')"
        )
        is_act = _opt_col(duck_reader, "silver", tbl, "Is_Active_Income", fallback="FALSE")
        is_pas = (
            'COALESCE(s."Is_Passive_Income", FALSE)'
            if has_sub
            and table_has_column(
                duck_reader, "silver", "dim_income_subcategory", "Is_Passive_Income"
            )
            else "FALSE"
        )
        is_div = _opt_col(duck_reader, "silver", tbl, "Is_Dividend_Income", fallback="FALSE")
        is_int = _opt_col(duck_reader, "silver", tbl, "Is_Interest_Income", fallback="FALSE")
        is_nc = _opt_col(duck_reader, "silver", tbl, "Is_Non_Cash_Income", fallback="FALSE")
        branches.append(
            f"""
            SELECT
                CAST(f."UID" AS VARCHAR) AS uid,
                'income' AS transaction_kind,
                CAST(f."DATE" AS DATE) AS tx_date,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "TIME", fallback="''")} AS VARCHAR), '') AS tx_time,
                COALESCE(CAST(f."ASSET_ID" AS VARCHAR), '') AS asset_id,
                CAST({asset_expr} AS VARCHAR) AS asset_name,
                CAST({_opt_col(duck_reader, "silver", tbl, "TO_ASSET_ID")} AS VARCHAR) AS to_asset_id,
                CAST(NULL AS VARCHAR) AS to_asset_name,
                CAST({cat_expr} AS VARCHAR) AS category_name,
                CAST({sub_expr} AS VARCHAR) AS subcategory_name,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "CURRENCY_ID", fallback="'INR_INR'")} AS VARCHAR), 'INR_INR') AS currency_id,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "BASE_AMOUNT", fallback="0.0")} AS DOUBLE), 0.0) AS base_amount,
                CAST({_opt_col(duck_reader, "silver", tbl, "AMOUNT_ACCOUNT")} AS DOUBLE) AS amount_account,
                CAST({_opt_col(duck_reader, "silver", tbl, "LOCAL_AMOUNT")} AS DOUBLE) AS local_amount,
                CAST({_opt_col(duck_reader, "silver", tbl, "EXCH_RATE")} AS DOUBLE) AS exch_rate,
                CAST({_opt_col(duck_reader, "silver", tbl, "TRANSFER_UID")} AS VARCHAR) AS transfer_uid,
                CAST(NULL AS VARCHAR) AS transfer_type,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "PAID", fallback="''")} AS VARCHAR), '') AS paid,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "DESCRIPTION", fallback="''")} AS VARCHAR), '') AS description,
                FALSE AS is_core,
                COALESCE(CAST({is_act} AS BOOLEAN), FALSE) AS is_active,
                COALESCE(CAST({is_pas} AS BOOLEAN), FALSE) AS is_passive,
                COALESCE(CAST({is_div} AS BOOLEAN), FALSE) AS is_dividend,
                COALESCE(CAST({is_int} AS BOOLEAN), FALSE) AS is_interest,
                COALESCE(CAST({is_nc} AS BOOLEAN), FALSE) AS is_non_cash
            FROM silver.fact_income_transactions f
            {sub_join}
            {cat_join}
            {src_join}
            """
        )

    if table_exists(duck_reader, "silver", "fact_expense_transactions"):
        tbl = "fact_expense_transactions"
        has_sub = table_exists(duck_reader, "silver", "dim_expense_subcategory")
        has_cat = has_sub and table_exists(duck_reader, "silver", "dim_expense_category")
        sub_join = (
            'LEFT JOIN silver.dim_expense_subcategory s ON f."CATEGORY_ID" = s."UID"'
            if has_sub
            else ""
        )
        cat_join = (
            'LEFT JOIN silver.dim_expense_category c ON s."CATEGORY_ID" = c."UID"'
            if has_cat
            else ""
        )
        src_join = (
            'LEFT JOIN silver.dim_asset_subcategory a ON f."ASSET_ID" = a."UID"' if name_col else ""
        )
        asset_expr = (
            f'COALESCE(CAST(a."{name_col}" AS VARCHAR), CAST(f."ASSET_ID" AS VARCHAR), \'\')'
            if name_col
            else "COALESCE(CAST(f.\"ASSET_ID\" AS VARCHAR), '')"
        )
        cat_expr = "COALESCE(CAST(c.\"CATEGORY_NAME\" AS VARCHAR), '')" if has_cat else "''"
        sub_expr = (
            'COALESCE(CAST(s."CATEGORY_NAME" AS VARCHAR), CAST(f."CATEGORY_ID" AS VARCHAR), \'\')'
            if has_sub
            else "COALESCE(CAST(f.\"CATEGORY_ID\" AS VARCHAR), '')"
        )
        is_core = _opt_col(duck_reader, "silver", tbl, "Is_Core_Expense", fallback="FALSE")
        branches.append(
            f"""
            SELECT
                CAST(f."UID" AS VARCHAR) AS uid,
                'expense' AS transaction_kind,
                CAST(f."DATE" AS DATE) AS tx_date,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "TIME", fallback="''")} AS VARCHAR), '') AS tx_time,
                COALESCE(CAST(f."ASSET_ID" AS VARCHAR), '') AS asset_id,
                CAST({asset_expr} AS VARCHAR) AS asset_name,
                CAST({_opt_col(duck_reader, "silver", tbl, "TO_ASSET_ID")} AS VARCHAR) AS to_asset_id,
                CAST(NULL AS VARCHAR) AS to_asset_name,
                CAST({cat_expr} AS VARCHAR) AS category_name,
                CAST({sub_expr} AS VARCHAR) AS subcategory_name,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "CURRENCY_ID", fallback="'INR_INR'")} AS VARCHAR), 'INR_INR') AS currency_id,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "BASE_AMOUNT", fallback="0.0")} AS DOUBLE), 0.0) AS base_amount,
                CAST({_opt_col(duck_reader, "silver", tbl, "AMOUNT_ACCOUNT")} AS DOUBLE) AS amount_account,
                CAST({_opt_col(duck_reader, "silver", tbl, "LOCAL_AMOUNT")} AS DOUBLE) AS local_amount,
                CAST({_opt_col(duck_reader, "silver", tbl, "EXCH_RATE")} AS DOUBLE) AS exch_rate,
                CAST({_opt_col(duck_reader, "silver", tbl, "TRANSFER_UID")} AS VARCHAR) AS transfer_uid,
                CAST(NULL AS VARCHAR) AS transfer_type,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "PAID", fallback="''")} AS VARCHAR), '') AS paid,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "DESCRIPTION", fallback="''")} AS VARCHAR), '') AS description,
                COALESCE(CAST({is_core} AS BOOLEAN), FALSE) AS is_core,
                FALSE AS is_active,
                FALSE AS is_passive,
                FALSE AS is_dividend,
                FALSE AS is_interest,
                FALSE AS is_non_cash
            FROM silver.fact_expense_transactions f
            {sub_join}
            {cat_join}
            {src_join}
            """
        )

    if table_exists(duck_reader, "silver", "fact_transfer_transactions"):
        tbl = "fact_transfer_transactions"
        src_join = (
            'LEFT JOIN silver.dim_asset_subcategory a ON f."ASSET_ID" = a."UID"' if name_col else ""
        )
        dst_join = (
            'LEFT JOIN silver.dim_asset_subcategory da ON f."TO_ASSET_ID" = da."UID"'
            if name_col and table_has_column(duck_reader, "silver", tbl, "TO_ASSET_ID")
            else ""
        )
        asset_expr = (
            f'COALESCE(a."{name_col}", f."ASSET_ID", \'\')'
            if name_col
            else "COALESCE(f.\"ASSET_ID\", '')"
        )
        to_asset_expr = (
            f'COALESCE(CAST(da."{name_col}" AS VARCHAR), CAST(f."TO_ASSET_ID" AS VARCHAR))'
            if dst_join and name_col
            else f"CAST({_opt_col(duck_reader, 'silver', tbl, 'TO_ASSET_ID')} AS VARCHAR)"
        )
        act_expr = (
            "COALESCE(a.\"cashflow_activity_type\", 'Transfer')"
            if name_col
            and table_has_column(
                duck_reader, "silver", "dim_asset_subcategory", "cashflow_activity_type"
            )
            else "'Transfer'"
        )
        branches.append(
            f"""
            SELECT
                CAST(f."UID" AS VARCHAR) AS uid,
                'transfer' AS transaction_kind,
                CAST(f."DATE" AS DATE) AS tx_date,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "TIME", fallback="''")} AS VARCHAR), '') AS tx_time,
                COALESCE(CAST(f."ASSET_ID" AS VARCHAR), '') AS asset_id,
                CAST({asset_expr} AS VARCHAR) AS asset_name,
                CAST({_opt_col(duck_reader, "silver", tbl, "TO_ASSET_ID")} AS VARCHAR) AS to_asset_id,
                CAST({to_asset_expr} AS VARCHAR) AS to_asset_name,
                CAST({act_expr} AS VARCHAR) AS category_name,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "TRANSFER_TYPE", fallback="''")} AS VARCHAR), '') AS subcategory_name,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "CURRENCY_ID", fallback="'INR_INR'")} AS VARCHAR), 'INR_INR') AS currency_id,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "BASE_AMOUNT", fallback="0.0")} AS DOUBLE), 0.0) AS base_amount,
                CAST({_opt_col(duck_reader, "silver", tbl, "AMOUNT_ACCOUNT")} AS DOUBLE) AS amount_account,
                CAST({_opt_col(duck_reader, "silver", tbl, "LOCAL_AMOUNT")} AS DOUBLE) AS local_amount,
                CAST({_opt_col(duck_reader, "silver", tbl, "EXCH_RATE")} AS DOUBLE) AS exch_rate,
                CAST({_opt_col(duck_reader, "silver", tbl, "TRANSFER_UID")} AS VARCHAR) AS transfer_uid,
                CAST({_opt_col(duck_reader, "silver", tbl, "TRANSFER_TYPE")} AS VARCHAR) AS transfer_type,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "PAID", fallback="''")} AS VARCHAR), '') AS paid,
                COALESCE(CAST({_opt_col(duck_reader, "silver", tbl, "DESCRIPTION", fallback="''")} AS VARCHAR), '') AS description,
                FALSE AS is_core,
                FALSE AS is_active,
                FALSE AS is_passive,
                FALSE AS is_dividend,
                FALSE AS is_interest,
                FALSE AS is_non_cash
            FROM silver.fact_transfer_transactions f
            {src_join}
            {dst_join}
            """
        )

    if not branches:
        return None
    return "\nUNION ALL\n".join(branches)


def _compile_where(query: HouseholdTransactionQuery) -> tuple[str, list[Any]]:
    """Compile bound WHERE clauses for `HouseholdTransactionQuery`."""
    clauses: list[str] = ["1 = 1"]
    params: list[Any] = []

    if query.transaction_kind != "all":
        clauses.append("transaction_kind = ?")
        params.append(query.transaction_kind)
    if query.start_date is not None:
        clauses.append("tx_date >= ?")
        params.append(query.start_date)
    if query.end_date is not None:
        clauses.append("tx_date <= ?")
        params.append(query.end_date)
    if query.asset_id:
        clauses.append("asset_id = ?")
        params.append(query.asset_id)
    if query.category_name:
        clauses.append("category_name = ?")
        params.append(query.category_name)
    if query.subcategory_name:
        clauses.append("subcategory_name = ?")
        params.append(query.subcategory_name)
    if query.currency_id:
        clauses.append("currency_id = ?")
        params.append(query.currency_id)
    if query.transfer_type:
        clauses.append("LOWER(COALESCE(transfer_type, '')) = ?")
        params.append(query.transfer_type.strip().lower())
    if query.min_amount is not None:
        clauses.append("ABS(base_amount) >= ?")
        params.append(query.min_amount)
    if query.max_amount is not None:
        clauses.append("ABS(base_amount) <= ?")
        params.append(query.max_amount)
    if query.text_search and query.text_search.strip():
        pattern = f"%{query.text_search.strip().lower()}%"
        clauses.append("(LOWER(description) LIKE ? OR LOWER(paid) LIKE ?)")
        params.extend((pattern, pattern))
    if query.has_transfer_only:
        clauses.append("COALESCE(transfer_uid, '') <> ''")

    flag_map = {
        "core": "transaction_kind = 'expense' AND is_core",
        "non_core": "transaction_kind = 'expense' AND NOT is_core",
        "active": "transaction_kind = 'income' AND is_active",
        "passive": "transaction_kind = 'income' AND is_passive",
        "dividend": "transaction_kind = 'income' AND is_dividend",
        "interest": "transaction_kind = 'income' AND is_interest",
        "non_cash": "transaction_kind = 'income' AND is_non_cash",
    }
    if query.classification_flag != "all":
        clauses.append(flag_map[query.classification_flag])

    return " AND ".join(clauses), params


def _classification_summary(row: dict[str, Any]) -> str:
    kind = str(row.get("transaction_kind") or "")
    if kind == "income":
        tags: list[str] = []
        if row.get("is_active"):
            tags.append("Active")
        if row.get("is_passive"):
            tags.append("Passive")
        if row.get("is_dividend"):
            tags.append("Dividend")
        if row.get("is_interest"):
            tags.append("Interest")
        if row.get("is_non_cash"):
            tags.append("Non-Cash")
        return ", ".join(tags) or "Income"
    if kind == "expense":
        return "Core" if row.get("is_core") else "Non-Core"
    t_type = str(row.get("transfer_type") or "")
    return f"Transfer ({t_type})" if t_type else "Transfer"


def _row_from_dict(row: dict[str, Any]) -> HouseholdTransactionRow | None:
    dt = as_date(row.get("tx_date"))
    if dt is None:
        return None
    kind = cast(Literal["income", "expense", "transfer"], str(row.get("transaction_kind")))
    return HouseholdTransactionRow(
        uid=str(row.get("uid") or ""),
        transaction_kind=kind,
        tx_date=dt,
        tx_time=str(row.get("tx_time") or ""),
        asset_id=str(row.get("asset_id") or ""),
        asset_name=str(row.get("asset_name") or row.get("asset_id") or ""),
        to_asset_id=str(row["to_asset_id"]) if row.get("to_asset_id") else None,
        to_asset_name=str(row["to_asset_name"]) if row.get("to_asset_name") else None,
        category_name=str(row.get("category_name") or ""),
        subcategory_name=str(row.get("subcategory_name") or ""),
        currency_id=str(row.get("currency_id") or "INR_INR"),
        base_amount=as_float(row.get("base_amount")) or 0.0,
        amount_account=as_float(row.get("amount_account")),
        local_amount=as_float(row.get("local_amount")),
        exch_rate=as_float(row.get("exch_rate")),
        transfer_uid=str(row["transfer_uid"]) if row.get("transfer_uid") else None,
        transfer_type=str(row["transfer_type"]) if row.get("transfer_type") else None,
        paid=str(row.get("paid") or ""),
        description=str(row.get("description") or ""),
        classification_summary=_classification_summary(row),
    )


def query_household_transactions(
    duck_reader: DuckDBQueryPort,
    query: HouseholdTransactionQuery,
    *,
    limit: int = 50,
    offset: int = 0,
    currency_id: str = "INR_INR",
) -> HouseholdTransactionPage:
    """Query server-paginated Silver transactions across income, expense, and transfers."""
    union_sql = _build_union_sql(duck_reader)
    if union_sql is None:
        return HouseholdTransactionPage(limit=limit, offset=offset, currency_id=currency_id)

    where_sql, params = _compile_where(query)
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)

    agg_row = duck_reader.fetch_one(
        f"""
        WITH all_tx AS ({union_sql})
        SELECT
            COUNT(*) AS total_count,
            COALESCE(SUM(CASE WHEN transaction_kind = 'income' THEN base_amount ELSE 0.0 END), 0.0) AS inc_tot,
            COALESCE(SUM(CASE WHEN transaction_kind = 'expense' THEN base_amount ELSE 0.0 END), 0.0) AS exp_tot,
            COALESCE(SUM(CASE WHEN transaction_kind = 'transfer' THEN ABS(base_amount) ELSE 0.0 END), 0.0) AS tr_vol,
            SUM(CASE WHEN transaction_kind = 'income' THEN 1 ELSE 0 END) AS inc_cnt,
            SUM(CASE WHEN transaction_kind = 'expense' THEN 1 ELSE 0 END) AS exp_cnt,
            SUM(CASE WHEN transaction_kind = 'transfer' THEN 1 ELSE 0 END) AS tr_cnt
        FROM all_tx
        WHERE {where_sql};
        """,
        tuple(params),
    )

    daily_rows = duck_reader.fetch_all(
        f"""
        WITH all_tx AS ({union_sql})
        SELECT
            tx_date,
            COALESCE(SUM(CASE WHEN transaction_kind = 'income' THEN base_amount ELSE 0.0 END), 0.0) AS inc_amt,
            COALESCE(SUM(CASE WHEN transaction_kind = 'expense' THEN base_amount ELSE 0.0 END), 0.0) AS exp_amt,
            COALESCE(SUM(CASE WHEN transaction_kind = 'transfer' THEN ABS(base_amount) ELSE 0.0 END), 0.0) AS tr_amt
        FROM all_tx
        WHERE {where_sql}
        GROUP BY tx_date
        ORDER BY tx_date ASC;
        """,
        tuple(params),
    )

    acc_rows = duck_reader.fetch_all(
        f"""
        WITH all_tx AS ({union_sql})
        SELECT
            asset_name,
            COALESCE(SUM(ABS(base_amount)), 0.0) AS vol,
            COUNT(*) AS cnt
        FROM all_tx
        WHERE {where_sql} AND asset_name <> ''
        GROUP BY asset_name
        ORDER BY vol DESC
        LIMIT 10;
        """,
        tuple(params),
    )

    asset_opts = duck_reader.fetch_all(
        f"""
        WITH all_tx AS ({union_sql})
        SELECT DISTINCT asset_id, asset_name
        FROM all_tx
        WHERE asset_id <> ''
        ORDER BY asset_name ASC
        LIMIT 200;
        """
    )
    curr_opts = duck_reader.fetch_all(
        f"""
        WITH all_tx AS ({union_sql})
        SELECT DISTINCT currency_id
        FROM all_tx
        WHERE currency_id <> ''
        ORDER BY currency_id ASC;
        """
    )
    cat_opts = duck_reader.fetch_all(
        f"""
        WITH all_tx AS ({union_sql})
        SELECT DISTINCT category_name
        FROM all_tx
        WHERE category_name <> ''
        ORDER BY category_name ASC
        LIMIT 200;
        """
    )
    sub_opts = duck_reader.fetch_all(
        f"""
        WITH all_tx AS ({union_sql})
        SELECT DISTINCT subcategory_name
        FROM all_tx
        WHERE subcategory_name <> ''
        ORDER BY subcategory_name ASC
        LIMIT 250;
        """
    )

    page_rows_raw = duck_reader.fetch_all(
        f"""
        WITH all_tx AS ({union_sql})
        SELECT *
        FROM all_tx
        WHERE {where_sql}
        ORDER BY tx_date DESC, COALESCE(tx_time, '') DESC, uid DESC
        LIMIT ? OFFSET ?;
        """,
        (*params, safe_limit, safe_offset),
    )

    rows: list[HouseholdTransactionRow] = []
    for raw in page_rows_raw:
        parsed = _row_from_dict(raw)
        if parsed is not None:
            rows.append(parsed)

    summary = HouseholdTransactionSummary(
        total_count=int((agg_row or {}).get("total_count") or 0),
        income_total=as_float((agg_row or {}).get("inc_tot")) or 0.0,
        expense_total=as_float((agg_row or {}).get("exp_tot")) or 0.0,
        transfer_volume=as_float((agg_row or {}).get("tr_vol")) or 0.0,
        income_count=int((agg_row or {}).get("inc_cnt") or 0),
        expense_count=int((agg_row or {}).get("exp_cnt") or 0),
        transfer_count=int((agg_row or {}).get("tr_cnt") or 0),
        daily_series=tuple(
            TxDailyPoint(
                tx_date=dt,
                income_amount=as_float(r.get("inc_amt")) or 0.0,
                expense_amount=as_float(r.get("exp_amt")) or 0.0,
                transfer_volume=as_float(r.get("tr_amt")) or 0.0,
            )
            for r in daily_rows
            if (dt := as_date(r.get("tx_date"))) is not None
        ),
        account_breakdown=tuple(
            TxAccountPoint(
                account_name=str(r.get("asset_name") or ""),
                volume=as_float(r.get("vol")) or 0.0,
                tx_count=int(r.get("cnt") or 0),
            )
            for r in acc_rows
        ),
        available_assets=tuple(
            (str(r["asset_id"]), str(r["asset_name"]))
            for r in asset_opts
            if r.get("asset_id") and r.get("asset_name")
        ),
        available_currencies=tuple(
            str(r["currency_id"]) for r in curr_opts if r.get("currency_id")
        ),
        available_categories=tuple(
            str(r["category_name"]) for r in cat_opts if r.get("category_name")
        ),
        available_subcategories=tuple(
            str(r["subcategory_name"]) for r in sub_opts if r.get("subcategory_name")
        ),
    )
    return HouseholdTransactionPage(
        rows=tuple(rows),
        summary=summary,
        limit=safe_limit,
        offset=safe_offset,
        currency_id=currency_id,
    )


def query_household_transaction_detail(
    duck_reader: DuckDBQueryPort,
    transaction_kind: Literal["income", "expense", "transfer"],
    uid: str,
) -> HouseholdTransactionDetail | None:
    """Fetch full raw Silver row, dimension lineage, and linked `TRANSFER_UID` rows."""
    union_sql = _build_union_sql(duck_reader)
    if union_sql is None:
        return None

    base_raw = duck_reader.fetch_one(
        f"""
        WITH all_tx AS ({union_sql})
        SELECT * FROM all_tx WHERE transaction_kind = ? AND uid = ? LIMIT 1;
        """,
        (transaction_kind, uid),
    )
    if not base_raw:
        return None
    row_obj = _row_from_dict(base_raw)
    if row_obj is None:
        return None

    table_map = {
        "income": "fact_income_transactions",
        "expense": "fact_expense_transactions",
        "transfer": "fact_transfer_transactions",
    }
    fact_tbl = table_map[transaction_kind]
    raw_fact: dict[str, Any] = {}
    if table_exists(duck_reader, "silver", fact_tbl):
        raw_fact = (
            duck_reader.fetch_one(
                f'SELECT * FROM silver.{fact_tbl} WHERE CAST("UID" AS VARCHAR) = ? LIMIT 1;',
                (uid,),
            )
            or {}
        )

    lineage: dict[str, str] = {
        "Kind": transaction_kind.title(),
        "Category": row_obj.category_name or "—",
        "Subcategory": row_obj.subcategory_name or "—",
        "Account": row_obj.asset_name or row_obj.asset_id or "—",
        "Currency": row_obj.currency_id,
    }
    if row_obj.to_asset_name or row_obj.to_asset_id:
        lineage["To Account"] = row_obj.to_asset_name or row_obj.to_asset_id or "—"

    if row_obj.asset_id and table_exists(duck_reader, "silver", "dim_asset_subcategory"):
        asset_dim = duck_reader.fetch_one(
            'SELECT * FROM silver.dim_asset_subcategory WHERE "UID" = ? LIMIT 1;',
            (row_obj.asset_id,),
        )
        if asset_dim:
            for k, lbl in (
                ("ledger_role", "Ledger Role"),
                ("cashflow_activity_type", "Cashflow Activity"),
                ("settlement_timing", "Settlement Timing"),
            ):
                if asset_dim.get(k):
                    lineage[lbl] = str(asset_dim[k])

    if (
        transaction_kind == "income"
        and raw_fact.get("CATEGORY_ID")
        and table_exists(duck_reader, "silver", "dim_income_subcategory")
    ):
        inc_dim = duck_reader.fetch_one(
            'SELECT * FROM silver.dim_income_subcategory WHERE "UID" = ? LIMIT 1;',
            (str(raw_fact["CATEGORY_ID"]),),
        )
        if inc_dim:
            for k, lbl in (
                ("CATEGORY_GROUPS", "Group"),
                ("Tax_Income_Head", "Tax Income Head"),
                ("Tax_Sub_Head", "Tax Sub-Head"),
                ("Taxability", "Taxability"),
                ("Tax_Method", "Tax Method"),
            ):
                if inc_dim.get(k):
                    lineage[lbl] = str(inc_dim[k])

    flags: list[str] = [s.strip() for s in row_obj.classification_summary.split(",") if s.strip()]

    linked: list[HouseholdTransactionRow] = []
    if row_obj.transfer_uid:
        linked_raw = duck_reader.fetch_all(
            f"""
            WITH all_tx AS ({union_sql})
            SELECT *
            FROM all_tx
            WHERE transfer_uid = ? AND NOT (transaction_kind = ? AND uid = ?)
            ORDER BY tx_date DESC, COALESCE(tx_time, '') DESC, uid DESC
            LIMIT 25;
            """,
            (row_obj.transfer_uid, transaction_kind, uid),
        )
        for lr in linked_raw:
            parsed = _row_from_dict(lr)
            if parsed is not None:
                linked.append(parsed)

    clean_raw = {str(k): (str(v) if v is not None else "—") for k, v in raw_fact.items()}
    return HouseholdTransactionDetail(
        row=row_obj,
        raw_fields=clean_raw,
        lineage=lineage,
        flags=tuple(flags),
        linked_transfers=tuple(linked),
    )


def export_transactions_csv(page: HouseholdTransactionPage) -> str:
    """Export current paged `HouseholdTransactionPage` rows to RFC-4180 CSV."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "UID",
            "Kind",
            "Date",
            "Time",
            "Account",
            "To Account",
            "Category",
            "Subcategory",
            "Classification",
            "Currency",
            "Base Amount",
            "Account Amount",
            "Local Amount",
            "Exchange Rate",
            "Transfer UID",
            "Transfer Type",
            "Paid",
            "Description",
        ]
    )
    for r in page.rows:
        writer.writerow(
            [
                r.uid,
                r.transaction_kind,
                r.tx_date.isoformat(),
                r.tx_time,
                r.asset_name,
                r.to_asset_name or "",
                r.category_name,
                r.subcategory_name,
                r.classification_summary,
                r.currency_id,
                f"{r.base_amount:.2f}",
                f"{r.amount_account:.2f}" if r.amount_account is not None else "",
                f"{r.local_amount:.2f}" if r.local_amount is not None else "",
                f"{r.exch_rate:.6f}" if r.exch_rate is not None else "",
                r.transfer_uid or "",
                r.transfer_type or "",
                r.paid,
                r.description,
            ]
        )
    return buf.getvalue()
