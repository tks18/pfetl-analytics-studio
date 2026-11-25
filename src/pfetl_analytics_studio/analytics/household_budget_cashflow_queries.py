"""Read-only Gold & Silver SQL query helpers for Household Budget & Cashflow Analytics.

Owns
----
- Parameterized Gold queries against `gold.forecast_budget_variance` and
  `gold.cashflow_activity_summary`.
- Parameterized Silver transfer drill queries against `silver.fact_transfer_transactions`
  joined with `silver.dim_asset_subcategory`.

Does not own
------------
- NiceGUI widgets or PFETL budgeting/cashflow calculation methodology.
"""

from __future__ import annotations

import json
import tomllib
from datetime import date
from typing import Any, cast

from pfetl_analytics_studio.analytics.household_models import (
    BudgetHealthFlags,
    BudgetMonthlyPoint,
    CashflowActivityPoint,
    TransferTransactionPage,
    TransferTransactionRow,
)
from pfetl_analytics_studio.analytics.overview_models import (
    OverviewBreakupItem,
    OverviewKPI,
    as_date,
    as_float,
)
from pfetl_analytics_studio.analytics.overview_queries import table_exists, table_has_column
from pfetl_analytics_studio.data import (
    ControlPlaneReader,
    ControlPlaneState,
    DuckDBQueryPort,
    RunProvenance,
)
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import MetricFormat


def _pick_col(
    duck_reader: DuckDBQueryPort,
    schema: str,
    table: str,
    candidates: tuple[str, ...],
    alias: str,
    *,
    table_alias: str = "",
    fallback: str = "NULL",
) -> str:
    """Return SQL select expression for the first existing column in `candidates`."""
    prefix = f"{table_alias}." if table_alias else ""
    for col in candidates:
        if table_has_column(duck_reader, schema, table, col):
            return f'{prefix}"{col}" AS {alias}'
    return f"{fallback} AS {alias}"


def _as_bool(value: Any) -> bool | None:
    """Safely convert DuckDB boolean/int values to `bool | None`."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _normalize_pct(val: float | None) -> float | None:
    """Convert decimal rule ratios (0.4 -> 40.0) to percentage points."""
    if val is None:
        return None
    return val * 100.0 if abs(val) <= 1.0 else val


def _classify_activity_bucket(
    transfer_type: str,
    src_activity: str,
    dst_activity: str,
) -> str:
    """Map Silver transfer type / asset activity metadata to canonical drill buckets."""
    combined = f"{src_activity} {dst_activity} {transfer_type}".lower()
    if "investing" in combined or any(
        tok in combined for tok in ("sip", "equity", "mf", "stock", "broker")
    ):
        return "Investing"
    if "financing" in combined or any(
        tok in combined for tok in ("loan", "emi", "debt", "mortgage", "credit")
    ):
        return "Financing"
    return "Internal Transfers"


def query_budget_variance_series(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
) -> tuple[BudgetMonthlyPoint, ...]:
    """Query `gold.forecast_budget_variance` monthly series over `[start_date, end_date]`."""
    if not table_exists(duck_reader, "gold", "forecast_budget_variance"):
        return ()

    schema, table = "gold", "forecast_budget_variance"
    ym_expr = _pick_col(
        duck_reader,
        schema,
        table,
        ("YEAR_MONTH",),
        "ym",
        fallback="strftime(\"MONTH_END_DATE\", '%Y-%m')",
    )

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            "MONTH_END_DATE" AS m_end,
            {ym_expr},
            {_pick_col(duck_reader, schema, table, ("Actual_Income", "Total_Income"), "act_inc")},
            {_pick_col(duck_reader, schema, table, ("Budget_Income",), "bud_inc")},
            {_pick_col(duck_reader, schema, table, ("Income_Variance",), "var_inc")},
            {_pick_col(duck_reader, schema, table, ("Actual_Core_Expense", "Core_Expense"), "act_core")},
            {_pick_col(duck_reader, schema, table, ("Budget_Core_Expense_Target", "Budget_Core_Expense"), "bud_core")},
            {_pick_col(duck_reader, schema, table, ("Core_Variance", "Core_Expense_Variance"), "var_core")},
            {_pick_col(duck_reader, schema, table, ("Actual_NonCore_Expense", "Actual_Non_Core_Expense", "NonCore_Expense"), "act_noncore")},
            {_pick_col(duck_reader, schema, table, ("Budget_NonCore_Expense_Target", "Budget_Non_Core_Expense", "Budget_NonCore_Expense"), "bud_noncore")},
            {_pick_col(duck_reader, schema, table, ("Non_Core_Variance", "NonCore_Expense_Variance"), "var_noncore")},
            {_pick_col(duck_reader, schema, table, ("Actual_Investment", "Actual_Investments"), "act_inv")},
            {_pick_col(duck_reader, schema, table, ("Budget_Investment_Target", "Budget_Investment", "Budget_Investments"), "bud_inv")},
            {_pick_col(duck_reader, schema, table, ("Investment_Variance",), "var_inv")},
            {_pick_col(duck_reader, schema, table, ("Investment_Deployed",), "inv_dep")},
            {_pick_col(duck_reader, schema, table, ("Investment_Redeemed",), "inv_red")},
            {_pick_col(duck_reader, schema, table, ("Budget_Surplus_Buffer",), "bud_buf")},
            {_pick_col(duck_reader, schema, table, ("Actual_Savings",), "act_sav")},
            {_pick_col(duck_reader, schema, table, ("Rule_Core_Pct_Budget", "Rule_Core_Pct"), "r_core")},
            {_pick_col(duck_reader, schema, table, ("Rule_NonCore_Pct_Budget", "Rule_Non_Core_Pct", "Rule_NonCore_Pct"), "r_noncore")},
            {_pick_col(duck_reader, schema, table, ("Rule_Investment_Pct_Budget", "Rule_Investment_Pct"), "r_inv")},
            {_pick_col(duck_reader, schema, table, ("Emergency_Fund_Target",), "em_target")},
            {_pick_col(duck_reader, schema, table, ("Emergency_Fund_Actual", "Liquid_Cash_Balance"), "em_actual")},
            {_pick_col(duck_reader, schema, table, ("Emergency_Fund_Gap",), "em_gap")},
            {_pick_col(duck_reader, schema, table, ("Zero_Income_Runway_Months", "Emergency_Fund_Runway_Months", "Runway_Months"), "runway")},
            {_pick_col(duck_reader, schema, table, ("Emergency_Target_Months",), "target_runway")},
            {_pick_col(duck_reader, schema, table, ("Is_Core_Overspent", "Is_Overspent"), "f_core_over")},
            {_pick_col(duck_reader, schema, table, ("Is_NonCore_Overspent", "Is_Non_Core_Overspent"), "f_nc_over")},
            {_pick_col(duck_reader, schema, table, ("Is_Investment_Underfunded", "Is_Underfunded"), "f_inv_under")},
            {_pick_col(duck_reader, schema, table, ("Is_Income_Volatile",), "f_inc_vol")},
            {_pick_col(duck_reader, schema, table, ("Is_Budget_Month_Healthy", "Is_Budget_Healthy", "Is_Healthy"), "f_healthy")}
        FROM gold.forecast_budget_variance
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
        ORDER BY "MONTH_END_DATE" ASC;
        """,
        (start_date, end_date),
    )

    points: list[BudgetMonthlyPoint] = []
    for row in rows:
        dt = as_date(row.get("m_end"))
        if dt is None:
            continue
        act_inc = as_float(row.get("act_inc"))
        bud_inc = as_float(row.get("bud_inc"))
        act_core = as_float(row.get("act_core"))
        bud_core = as_float(row.get("bud_core"))
        act_nc = as_float(row.get("act_noncore"))
        bud_nc = as_float(row.get("bud_noncore"))
        act_inv = as_float(row.get("act_inv"))
        bud_inv = as_float(row.get("bud_inv"))

        r_core = _normalize_pct(as_float(row.get("r_core")))
        r_nc = _normalize_pct(as_float(row.get("r_noncore")))
        r_inv = _normalize_pct(as_float(row.get("r_inv")))
        r_buf = (
            max(0.0, 100.0 - (r_core + r_nc + r_inv))
            if r_core is not None and r_nc is not None and r_inv is not None
            else None
        )
        points.append(
            BudgetMonthlyPoint(
                month_end=dt,
                year_month=str(row.get("ym") or dt.strftime("%Y-%m")),
                actual_income=act_inc,
                budget_income=bud_inc,
                income_variance=(
                    as_float(row.get("var_inc"))
                    if row.get("var_inc") is not None
                    else (
                        act_inc - bud_inc if act_inc is not None and bud_inc is not None else None
                    )
                ),
                actual_core=act_core,
                budget_core=bud_core,
                core_variance=(
                    as_float(row.get("var_core"))
                    if row.get("var_core") is not None
                    else (
                        act_core - bud_core
                        if act_core is not None and bud_core is not None
                        else None
                    )
                ),
                actual_non_core=act_nc,
                budget_non_core=bud_nc,
                non_core_variance=(
                    as_float(row.get("var_noncore"))
                    if row.get("var_noncore") is not None
                    else (act_nc - bud_nc if act_nc is not None and bud_nc is not None else None)
                ),
                actual_investment=act_inv,
                budget_investment=bud_inv,
                investment_variance=(
                    as_float(row.get("var_inv"))
                    if row.get("var_inv") is not None
                    else (
                        act_inv - bud_inv if act_inv is not None and bud_inv is not None else None
                    )
                ),
                investment_deployed=as_float(row.get("inv_dep")),
                investment_redeemed=as_float(row.get("inv_red")),
                budget_surplus_buffer=as_float(row.get("bud_buf")),
                actual_savings=as_float(row.get("act_sav")),
                rule_core_pct=r_core,
                rule_non_core_pct=r_nc,
                rule_investment_pct=r_inv,
                rule_buffer_pct=r_buf,
                emergency_target=as_float(row.get("em_target")),
                emergency_actual=as_float(row.get("em_actual")),
                emergency_gap=as_float(row.get("em_gap")),
                runway_months=as_float(row.get("runway")),
                target_runway_months=as_float(row.get("target_runway")),
                flags=BudgetHealthFlags(
                    is_core_overspent=_as_bool(row.get("f_core_over")),
                    is_non_core_overspent=_as_bool(row.get("f_nc_over")),
                    is_investment_underfunded=_as_bool(row.get("f_inv_under")),
                    is_income_volatile=_as_bool(row.get("f_inc_vol")),
                    is_budget_healthy=_as_bool(row.get("f_healthy")),
                ),
            )
        )
    return tuple(points)


def query_cashflow_activity_series(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
) -> tuple[CashflowActivityPoint, ...]:
    """Query `gold.cashflow_activity_summary` monthly statement over `[start_date, end_date]`."""
    if not table_exists(duck_reader, "gold", "cashflow_activity_summary"):
        return ()

    schema, table = "gold", "cashflow_activity_summary"
    ym_expr = _pick_col(
        duck_reader,
        schema,
        table,
        ("YEAR_MONTH",),
        "ym",
        fallback="strftime(\"MONTH_END_DATE\", '%Y-%m')",
    )

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            "MONTH_END_DATE" AS m_end,
            {ym_expr},
            {_pick_col(duck_reader, schema, table, ("Opening_Cash_Balance",), "op_cash")},
            {_pick_col(duck_reader, schema, table, ("Closing_Cash_Balance",), "cl_cash")},
            {_pick_col(duck_reader, schema, table, ("Cash_Inflow_Operating", "Operating_Inflows"), "op_in")},
            {_pick_col(duck_reader, schema, table, ("Cash_Outflow_Operating", "Operating_Outflows"), "op_out")},
            {_pick_col(duck_reader, schema, table, ("Net_Cashflow_Operating", "Net_Operating_Cashflow", "Operating_Cashflow"), "net_op")},
            {_pick_col(duck_reader, schema, table, ("Cash_Inflow_Investing", "Investing_Inflows"), "inv_in")},
            {_pick_col(duck_reader, schema, table, ("Cash_Outflow_Investing", "Investing_Outflows"), "inv_out")},
            {_pick_col(duck_reader, schema, table, ("Net_Cashflow_Investing", "Net_Investing_Cashflow", "Investing_Cashflow"), "net_inv")},
            {_pick_col(duck_reader, schema, table, ("Cash_Inflow_Financing", "Financing_Inflows"), "fin_in")},
            {_pick_col(duck_reader, schema, table, ("Cash_Outflow_Financing", "Financing_Outflows"), "fin_out")},
            {_pick_col(duck_reader, schema, table, ("Net_Cashflow_Financing", "Net_Financing_Cashflow", "Financing_Cashflow"), "net_fin")},
            {_pick_col(duck_reader, schema, table, ("Internal_Transfer_Inflow", "Internal_Transfer_Inflows"), "tr_in")},
            {_pick_col(duck_reader, schema, table, ("Internal_Transfer_Outflow", "Internal_Transfer_Outflows"), "tr_out")},
            {_pick_col(duck_reader, schema, table, ("Net_Internal_Transfers", "Internal_Transfers"), "net_tr")},
            {_pick_col(duck_reader, schema, table, ("Total_Cash_Inflow",), "tot_in")},
            {_pick_col(duck_reader, schema, table, ("Total_Cash_Outflow",), "tot_out")},
            {_pick_col(duck_reader, schema, table, ("Non_Cash_Adjustments", "Non_Cash_PnL", "Total_Non_Cash_Expenses"), "non_cash")},
            {_pick_col(duck_reader, schema, table, ("Calculated_Net_Cashflow",), "calc_net")},
            {_pick_col(duck_reader, schema, table, ("Actual_Cash_Change", "Net_Cash_Movement"), "act_chg")},
            {_pick_col(duck_reader, schema, table, ("Unreconciled_Difference",), "unrec")},
            {_pick_col(duck_reader, schema, table, ("Is_Reconciled",), "is_rec")}
        FROM gold.cashflow_activity_summary
        WHERE "MONTH_END_DATE" >= ? AND "MONTH_START_DATE" <= ?
        ORDER BY "MONTH_END_DATE" ASC;
        """,
        (start_date, end_date),
    )

    points: list[CashflowActivityPoint] = []
    for row in rows:
        dt = as_date(row.get("m_end"))
        if dt is None:
            continue
        opening = as_float(row.get("op_cash")) or 0.0
        closing = as_float(row.get("cl_cash")) or 0.0
        net_op = as_float(row.get("net_op")) or 0.0
        net_inv = as_float(row.get("net_inv")) or 0.0
        net_fin = as_float(row.get("net_fin")) or 0.0
        net_tr = as_float(row.get("net_tr")) or 0.0
        calc_net = (
            as_float(row.get("calc_net"))
            if row.get("calc_net") is not None
            else (net_op + net_inv + net_fin + net_tr)
        )
        assert calc_net is not None
        act_chg = (
            as_float(row.get("act_chg")) if row.get("act_chg") is not None else (closing - opening)
        )
        assert act_chg is not None
        unrec = as_float(row.get("unrec")) if row.get("unrec") is not None else (act_chg - calc_net)
        assert unrec is not None
        rec_flag = _as_bool(row.get("is_rec"))
        is_reconciled = rec_flag if rec_flag is not None else abs(unrec) < 0.01

        points.append(
            CashflowActivityPoint(
                month_end=dt,
                year_month=str(row.get("ym") or dt.strftime("%Y-%m")),
                opening_cash=opening,
                closing_cash=closing,
                operating_inflows=as_float(row.get("op_in")),
                operating_outflows=as_float(row.get("op_out")),
                net_operating=net_op,
                investing_inflows=as_float(row.get("inv_in")),
                investing_outflows=as_float(row.get("inv_out")),
                net_investing=net_inv,
                financing_inflows=as_float(row.get("fin_in")),
                financing_outflows=as_float(row.get("fin_out")),
                net_financing=net_fin,
                transfer_inflows=as_float(row.get("tr_in")),
                transfer_outflows=as_float(row.get("tr_out")),
                net_transfers=net_tr,
                total_cash_inflow=as_float(row.get("tot_in")),
                total_cash_outflow=as_float(row.get("tot_out")),
                non_cash_adjustments=as_float(row.get("non_cash")),
                calculated_net_cashflow=calc_net,
                actual_cash_change=act_chg,
                unreconciled_difference=unrec,
                is_reconciled=is_reconciled,
            )
        )
    return tuple(points)


def query_transfer_transactions(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
    *,
    activity_filter: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> TransferTransactionPage:
    """Query bounded Silver transfer transactions for cashflow activity drill."""
    if not table_exists(duck_reader, "silver", "fact_transfer_transactions"):
        return TransferTransactionPage(page=page, page_size=page_size)

    schema, table = "silver", "fact_transfer_transactions"
    has_adj = table_has_column(duck_reader, schema, table, "ADJUSTED_DATE_FOR_ANALYSIS")
    date_expr = 'COALESCE(f."ADJUSTED_DATE_FOR_ANALYSIS", f."DATE")' if has_adj else 'f."DATE"'
    adj_select = 'f."ADJUSTED_DATE_FOR_ANALYSIS" AS adj_date' if has_adj else "NULL AS adj_date"

    has_asset_sub = table_exists(duck_reader, "silver", "dim_asset_subcategory")
    name_col = (
        "ASSET_NAME"
        if has_asset_sub
        and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "ASSET_NAME")
        else (
            "NAME"
            if has_asset_sub
            and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "NAME")
            else None
        )
    )
    has_sub_act = has_asset_sub and table_has_column(
        duck_reader, "silver", "dim_asset_subcategory", "cashflow_activity_type"
    )
    has_asset_cat = (
        has_asset_sub
        and table_has_column(duck_reader, "silver", "dim_asset_subcategory", "CATEGORY_ID")
        and table_exists(duck_reader, "silver", "dim_asset_category")
        and table_has_column(duck_reader, "silver", "dim_asset_category", "cashflow_activity_type")
    )

    src_join = (
        'LEFT JOIN silver.dim_asset_subcategory sa ON f."ASSET_ID" = sa."UID"'
        if name_col is not None or has_sub_act or has_asset_cat
        else ""
    )
    dst_join = (
        'LEFT JOIN silver.dim_asset_subcategory da ON f."TO_ASSET_ID" = da."UID"'
        if (name_col is not None or has_sub_act)
        and table_has_column(duck_reader, schema, table, "TO_ASSET_ID")
        else ""
    )
    cat_join = (
        'LEFT JOIN silver.dim_asset_category ac ON sa."CATEGORY_ID" = ac."UID"'
        if has_asset_cat
        else ""
    )
    src_name_expr = f'COALESCE(sa."{name_col}", f."ASSET_ID")' if name_col else 'f."ASSET_ID"'
    dst_name_expr = (
        f'COALESCE(da."{name_col}", f."TO_ASSET_ID")'
        if dst_join and name_col
        else (
            'f."TO_ASSET_ID"'
            if table_has_column(duck_reader, schema, table, "TO_ASSET_ID")
            else "NULL"
        )
    )
    src_act_expr = (
        "COALESCE(sa.\"cashflow_activity_type\", '')"
        if has_sub_act
        else ("COALESCE(ac.\"cashflow_activity_type\", '')" if cat_join else "''")
    )
    dst_act_expr = (
        "COALESCE(da.\"cashflow_activity_type\", '')" if dst_join and has_sub_act else "''"
    )

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            f."UID" AS uid,
            f."DATE" AS tx_date,
            {adj_select},
            {_pick_col(duck_reader, schema, table, ("ASSET_ID",), "asset_id", table_alias="f")},
            {src_name_expr} AS asset_name,
            {_pick_col(duck_reader, schema, table, ("TO_ASSET_ID",), "to_asset_id", table_alias="f")},
            {dst_name_expr} AS to_asset_name,
            {_pick_col(duck_reader, schema, table, ("TRANSFER_TYPE",), "transfer_type", table_alias="f", fallback="''")},
            {_pick_col(duck_reader, schema, table, ("TRANSACTION_TYPE",), "tx_type", table_alias="f", fallback="''")},
            {src_act_expr} AS src_activity,
            {dst_act_expr} AS dst_activity,
            {_pick_col(duck_reader, schema, table, ("BASE_AMOUNT",), "amount", table_alias="f", fallback="0.0")},
            {_pick_col(duck_reader, schema, table, ("CURRENCY_ID",), "currency_id", table_alias="f", fallback="'INR_INR'")},
            {_pick_col(duck_reader, schema, table, ("PAID",), "paid", table_alias="f", fallback="''")},
            {_pick_col(duck_reader, schema, table, ("DESCRIPTION",), "description", table_alias="f", fallback="''")}
        FROM silver.fact_transfer_transactions f
        {src_join}
        {dst_join}
        {cat_join}
        WHERE {date_expr} BETWEEN ? AND ?
        ORDER BY {date_expr} DESC, f."UID" DESC;
        """,
        (start_date, end_date),
    )

    normalized_filter = (activity_filter or "").strip().lower()
    search_term = (search or "").strip().lower()

    filtered: list[TransferTransactionRow] = []
    for row in rows:
        tx_dt = as_date(row.get("tx_date")) or as_date(row.get("adj_date"))
        if tx_dt is None:
            continue
        t_type = str(row.get("transfer_type") or "")
        bucket = _classify_activity_bucket(
            t_type,
            str(row.get("src_activity") or ""),
            str(row.get("dst_activity") or ""),
        )
        if normalized_filter and normalized_filter != "all":
            if normalized_filter not in bucket.lower() and normalized_filter not in t_type.lower():
                continue

        asset_name = str(row.get("asset_name") or row.get("asset_id") or "")
        to_asset_name = str(row.get("to_asset_name") or row.get("to_asset_id") or "")
        desc = str(row.get("description") or "")
        paid = str(row.get("paid") or "")
        if search_term:
            haystack = f"{asset_name} {to_asset_name} {t_type} {bucket} {paid} {desc}".lower()
            if search_term not in haystack:
                continue

        raw_amt = as_float(row.get("amount")) or 0.0
        t_lower = t_type.strip().lower()
        signed_amt = (
            -abs(raw_amt) if t_lower == "out" else (abs(raw_amt) if t_lower == "in" else raw_amt)
        )

        filtered.append(
            TransferTransactionRow(
                uid=str(row.get("uid") or ""),
                tx_date=tx_dt,
                adjusted_date=as_date(row.get("adj_date")),
                asset_id=str(row.get("asset_id") or ""),
                asset_name=asset_name,
                to_asset_id=str(row["to_asset_id"]) if row.get("to_asset_id") else None,
                to_asset_name=to_asset_name or None,
                transfer_type=t_type,
                transaction_type=str(row.get("tx_type") or ""),
                activity_bucket=bucket,
                amount=signed_amt,
                currency_id=str(row.get("currency_id") or "INR_INR"),
                paid=paid,
                description=desc,
            )
        )

    safe_page = max(1, page)
    safe_size = max(1, min(page_size, 200))
    offset = (safe_page - 1) * safe_size
    total_count = len(filtered)
    total_base = sum(r.amount for r in filtered)
    return TransferTransactionPage(
        rows=tuple(filtered[offset : offset + safe_size]),
        total_count=total_count,
        total_base_amount=total_base,
        page=safe_page,
        page_size=safe_size,
    )


def load_rules_budget_overlay(
    profile: PFETLProfile,
    provenance: RunProvenance | None,
) -> str | None:
    """Format optional FinancialRules budget allocation overlay string for tooltips."""
    raw_rules: dict[str, Any] = {}
    if provenance is not None and provenance.rules_snapshot_id:
        try:
            cp = ControlPlaneReader(profile)
            if cp.get_state() == ControlPlaneState.AVAILABLE and cp.table_exists(
                "cp_rules_snapshots"
            ):
                row = cp.fetch_one(
                    "SELECT content_json FROM cp_rules_snapshots WHERE snapshot_id = ? LIMIT 1;",
                    (provenance.rules_snapshot_id,),
                )
                if row and row.get("content_json"):
                    parsed = json.loads(str(row["content_json"]))
                    if isinstance(parsed, dict):
                        raw_rules = cast(dict[str, Any], parsed)
        except Exception:  # noqa: BLE001
            raw_rules = {}
    if not raw_rules and profile.rules_path.exists():
        try:
            raw_rules = tomllib.loads(profile.rules_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            raw_rules = {}
    budget_cfg_raw = raw_rules.get("budget")
    if not isinstance(budget_cfg_raw, dict):
        return None
    budget_cfg = cast(dict[str, Any], budget_cfg_raw)
    alloc_raw = budget_cfg.get("income_allocation")
    if not isinstance(alloc_raw, dict):
        return None
    alloc = cast(dict[str, Any], alloc_raw)
    core_pct = _normalize_pct(as_float(alloc.get("core_pct") or alloc.get("core_expense_pct")))
    nc_pct = _normalize_pct(
        as_float(alloc.get("non_core_pct") or alloc.get("non_core_expense_pct"))
    )
    inv_pct = _normalize_pct(as_float(alloc.get("investment_pct")))
    buf_pct = (
        max(0.0, 100.0 - (core_pct + nc_pct + inv_pct))
        if core_pct is not None and nc_pct is not None and inv_pct is not None
        else None
    )
    parts = [
        f"Core {core_pct:.0f}%" if core_pct is not None else "",
        f"Non-Core {nc_pct:.0f}%" if nc_pct is not None else "",
        f"Invest {inv_pct:.0f}%" if inv_pct is not None else "",
        f"Buffer {buf_pct:.0f}%" if buf_pct is not None else "",
    ]
    summary = " / ".join(p for p in parts if p)
    return f"Rule Split: {summary}" if summary else None


def build_budget_kpis(
    rows: tuple[BudgetMonthlyPoint, ...],
    rules_overlay: str | None,
) -> tuple[OverviewKPI, ...]:
    """Build Budget Variance KPI cards from Gold `forecast_budget_variance` rows."""
    latest = rows[-1]
    as_of = latest.month_end
    act_inc = sum(r.actual_income or 0.0 for r in rows)
    bud_inc = sum(r.budget_income or 0.0 for r in rows)
    inc_var = act_inc - bud_inc if any(r.budget_income is not None for r in rows) else None
    inc_var_pct = (inc_var / bud_inc) if inc_var is not None and bud_inc else None

    act_core = sum(r.actual_core or 0.0 for r in rows)
    bud_core = sum(r.budget_core or 0.0 for r in rows)
    core_var = act_core - bud_core if any(r.budget_core is not None for r in rows) else None

    act_nc = sum(r.actual_non_core or 0.0 for r in rows)
    bud_nc = sum(r.budget_non_core or 0.0 for r in rows)
    nc_var = act_nc - bud_nc if any(r.budget_non_core is not None for r in rows) else None

    act_inv = sum(r.actual_investment or 0.0 for r in rows)
    bud_inv = sum(r.budget_investment or 0.0 for r in rows)
    inv_var = act_inv - bud_inv if any(r.budget_investment is not None for r in rows) else None

    overlay_tip = f" {rules_overlay}" if rules_overlay else ""

    inc_card = OverviewKPI(
        kpi_id="budget_actual_income",
        label="Actual vs Budget Income",
        value=act_inc if any(r.actual_income is not None for r in rows) else None,
        format=MetricFormat.CURRENCY,
        delta_value=inc_var_pct,
        delta_format=MetricFormat.PERCENT,
        delta_label="vs Target",
        subtitle="Volatile Income" if latest.flags.is_income_volatile else "Stable vs Target",
        source_table="gold.forecast_budget_variance",
        as_of_date=as_of,
        drill_route="/household",
        available=any(r.actual_income is not None for r in rows),
        tooltip=f"Canonical Actual_Income vs Budget_Income.{overlay_tip}",
        breakups=(
            OverviewBreakupItem(label="Target", value=bud_inc if bud_inc else None),
            OverviewBreakupItem(label="Variance", value=inc_var),
        ),
    )
    core_card = OverviewKPI(
        kpi_id="budget_core_spend",
        label="Core Expense vs Target",
        value=act_core if any(r.actual_core is not None for r in rows) else None,
        format=MetricFormat.CURRENCY,
        subtitle=(
            "Core Overspent"
            if latest.flags.is_core_overspent
            else f"Rule: {latest.rule_core_pct:.1f}%"
            if latest.rule_core_pct is not None
            else "Within Core Target"
        ),
        source_table="gold.forecast_budget_variance",
        as_of_date=as_of,
        drill_route="/household",
        available=any(r.actual_core is not None for r in rows),
        tooltip=f"Canonical Actual_Core_Expense vs Budget_Core_Expense.{overlay_tip}",
        breakups=(
            OverviewBreakupItem(label="Target", value=bud_core if bud_core else None),
            OverviewBreakupItem(label="Variance", value=core_var),
            OverviewBreakupItem(
                label="Rule %",
                value=latest.rule_core_pct / 100.0 if latest.rule_core_pct is not None else None,
                format=MetricFormat.PERCENT,
            ),
        ),
    )
    nc_card = OverviewKPI(
        kpi_id="budget_non_core_spend",
        label="Non-Core vs Target",
        value=act_nc if any(r.actual_non_core is not None for r in rows) else None,
        format=MetricFormat.CURRENCY,
        subtitle=(
            "Non-Core Overspent"
            if latest.flags.is_non_core_overspent
            else f"Rule: {latest.rule_non_core_pct:.1f}%"
            if latest.rule_non_core_pct is not None
            else "Within Lifestyle Target"
        ),
        source_table="gold.forecast_budget_variance",
        as_of_date=as_of,
        drill_route="/household",
        available=any(r.actual_non_core is not None for r in rows),
        tooltip=f"Canonical Actual_Non_Core_Expense vs Budget_Non_Core_Expense.{overlay_tip}",
        breakups=(
            OverviewBreakupItem(label="Target", value=bud_nc if bud_nc else None),
            OverviewBreakupItem(label="Variance", value=nc_var),
            OverviewBreakupItem(
                label="Rule %",
                value=(
                    latest.rule_non_core_pct / 100.0
                    if latest.rule_non_core_pct is not None
                    else None
                ),
                format=MetricFormat.PERCENT,
            ),
        ),
    )
    inv_card = OverviewKPI(
        kpi_id="budget_investment_funding",
        label="Investment vs Target",
        value=act_inv if any(r.actual_investment is not None for r in rows) else None,
        format=MetricFormat.CURRENCY,
        subtitle=(
            "Underfunded"
            if latest.flags.is_investment_underfunded
            else f"Rule: {latest.rule_investment_pct:.1f}%"
            if latest.rule_investment_pct is not None
            else "Funded on Target"
        ),
        source_table="gold.forecast_budget_variance",
        as_of_date=as_of,
        drill_route="/household",
        available=any(r.actual_investment is not None for r in rows),
        tooltip=f"Canonical Actual_Investment vs Budget_Investment.{overlay_tip}",
        breakups=(
            OverviewBreakupItem(label="Target", value=bud_inv if bud_inv else None),
            OverviewBreakupItem(label="Variance", value=inv_var),
            OverviewBreakupItem(
                label="Rule %",
                value=(
                    latest.rule_investment_pct / 100.0
                    if latest.rule_investment_pct is not None
                    else None
                ),
                format=MetricFormat.PERCENT,
            ),
        ),
    )
    em_card = OverviewKPI(
        kpi_id="budget_emergency_runway",
        label="Emergency Runway",
        value=latest.runway_months,
        format=MetricFormat.NUMBER,
        unit_suffix=" mos",
        subtitle=(
            f"Target: {latest.target_runway_months:.1f} mos"
            if latest.target_runway_months is not None
            else "Liquid core coverage"
        ),
        source_table="gold.forecast_budget_variance",
        as_of_date=as_of,
        drill_route="/household",
        available=latest.runway_months is not None,
        tooltip="Canonical Emergency_Fund_Runway_Months and Emergency_Fund_Gap.",
        breakups=(
            OverviewBreakupItem(label="Reserve", value=latest.emergency_actual),
            OverviewBreakupItem(label="Target", value=latest.emergency_target),
            OverviewBreakupItem(label="Gap", value=latest.emergency_gap),
        ),
    )
    return (inc_card, core_card, nc_card, inv_card, em_card)


def build_cashflow_kpis(rows: tuple[CashflowActivityPoint, ...]) -> tuple[OverviewKPI, ...]:
    """Build Cashflow Activity Statement KPI cards from `gold.cashflow_activity_summary`."""
    as_of = rows[-1].month_end
    opening = rows[0].opening_cash
    closing = rows[-1].closing_cash
    net_op = sum(r.net_operating for r in rows)
    net_inv = sum(r.net_investing for r in rows)
    net_fin = sum(r.net_financing for r in rows)
    net_tr = sum(r.net_transfers for r in rows)
    calc_net = sum(r.calculated_net_cashflow for r in rows)
    unrec = sum(r.unreconciled_difference for r in rows)
    is_rec = abs(unrec) < 0.01

    op_in = sum(r.operating_inflows or 0.0 for r in rows)
    op_out = sum(r.operating_outflows or 0.0 for r in rows)

    op_card = OverviewKPI(
        kpi_id="cashflow_operating",
        label="Net Operating Cashflow",
        value=net_op,
        format=MetricFormat.CURRENCY,
        subtitle="Operating inflows minus outflows",
        source_table="gold.cashflow_activity_summary",
        as_of_date=as_of,
        drill_route="/household",
        available=True,
        tooltip="Canonical Net_Operating_Cashflow excluding internal transfers.",
        breakups=(
            OverviewBreakupItem(
                label="Inflows",
                value=op_in if any(r.operating_inflows is not None for r in rows) else None,
            ),
            OverviewBreakupItem(
                label="Outflows",
                value=op_out if any(r.operating_outflows is not None for r in rows) else None,
            ),
        ),
    )
    inv_fin_card = OverviewKPI(
        kpi_id="cashflow_investing_financing",
        label="Investing & Financing",
        value=net_inv + net_fin,
        format=MetricFormat.CURRENCY,
        subtitle="Net capital deployment & debt flows",
        source_table="gold.cashflow_activity_summary",
        as_of_date=as_of,
        drill_route="/household",
        available=True,
        tooltip="Net_Investing_Cashflow + Net_Financing_Cashflow; internal transfers tracked separately.",
        breakups=(
            OverviewBreakupItem(label="Investing", value=net_inv),
            OverviewBreakupItem(label="Financing", value=net_fin),
            OverviewBreakupItem(label="Transfers", value=net_tr),
        ),
    )
    rec_card = OverviewKPI(
        kpi_id="cashflow_reconciliation",
        label="Cash Reconciliation",
        value=closing,
        format=MetricFormat.CURRENCY,
        subtitle="Reconciled" if is_rec else "Unreconciled Diff Detected",
        source_table="gold.cashflow_activity_summary",
        as_of_date=as_of,
        drill_route="/household",
        available=True,
        tooltip="Opening Cash + Calculated Net Cashflow vs Closing Cash; Unreconciled_Difference is never forced to 0.",
        breakups=(
            OverviewBreakupItem(label="Opening", value=opening),
            OverviewBreakupItem(label="Calc Net", value=calc_net),
            OverviewBreakupItem(label="Unrec Diff", value=unrec),
        ),
    )
    tot_in = sum(r.total_cash_inflow or 0.0 for r in rows)
    tot_out = sum(r.total_cash_outflow or 0.0 for r in rows)
    gross_tr = sum(r.transfer_inflows or 0.0 for r in rows)
    net_move = closing - opening

    move_card = OverviewKPI(
        kpi_id="cashflow_net_movement",
        label="Net Cash Movement",
        value=net_move,
        format=MetricFormat.CURRENCY,
        subtitle="Period liquidity delta & gross volume",
        source_table="gold.cashflow_activity_summary",
        as_of_date=as_of,
        drill_route="/household",
        available=True,
        tooltip="Closing minus Opening cash with gross inflows, outflows, and internal transfer pool volume.",
        breakups=(
            OverviewBreakupItem(
                label="Inflow",
                value=tot_in if any(r.total_cash_inflow is not None for r in rows) else None,
            ),
            OverviewBreakupItem(
                label="Outflow",
                value=tot_out if any(r.total_cash_outflow is not None for r in rows) else None,
            ),
            OverviewBreakupItem(
                label="Pool Vol",
                value=gross_tr if any(r.transfer_inflows is not None for r in rows) else None,
            ),
        ),
    )
    return (op_card, inv_fin_card, rec_card, move_card)
