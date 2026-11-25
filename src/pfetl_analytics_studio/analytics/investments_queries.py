"""Read-only Gold & Silver SQL query helpers powering Investment Overview & Allocation.

Owns
----
- Whitelisted 9-mart allocation dispatch (`_ALLOCATION_MART_MAP`).
- Latest portfolio snapshot (`gold.investment_by_portfolio` + `gold.investment_portfolio_summary`).
- Per-ISIN instrument metadata & monthly history (`silver.dim_instrument_master`).

Does not own
------------
- NiceGUI widgets or PFETL portfolio/XIRR calculation methodology.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pfetl_analytics_studio.analytics.investments_models import (
    AllocationDimension,
    AllocationRow,
    InstrumentDetailSnapshot,
    InvestmentHoldingRow,
)
from pfetl_analytics_studio.analytics.overview_models import (
    OverviewBreakupItem,
    OverviewKPI,
    as_date,
    as_float,
    slice_filters,
)
from pfetl_analytics_studio.analytics.overview_queries import table_exists, table_has_column
from pfetl_analytics_studio.data import DuckDBQueryPort
from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterSet,
    MetricFormat,
    compile_filter_clause,
)

_ALLOCATION_MART_MAP: dict[AllocationDimension, tuple[str, str, str | None]] = {
    AllocationDimension.CLASS: ("investment_by_class", "INSTRUMENT_CLASS", None),
    AllocationDimension.INSTRUMENT_TYPE: ("investment_by_instrument_type", "INSTRUMENT_TYPE", None),
    AllocationDimension.SUBTYPE: (
        "investment_by_subtype",
        "INSTRUMENT_SUBTYPE",
        "INSTRUMENT_CLASS",
    ),
    AllocationDimension.SECTOR: ("investment_by_sector", "SECTOR", None),
    AllocationDimension.INDUSTRY: ("investment_by_industry", "INDUSTRY", None),
    AllocationDimension.GEO: ("investment_by_geo", "GEO", None),
    AllocationDimension.COUNTRY: ("investment_by_country", "COUNTRY", None),
    AllocationDimension.CURRENCY: ("investment_by_currency", "CURRENCY_ID", None),
    AllocationDimension.ISIN: ("investment_by_isin", "ISIN", None),
}

_PORTFOLIO_SUMMARY_FILTER_MAP: dict[FilterField, str] = {
    FilterField.ISIN: "ISIN",
    FilterField.CURRENCY_ID: "CURRENCY_ID",
    FilterField.INSTRUMENT_CLASS: "INSTRUMENT_CLASS",
    FilterField.INSTRUMENT_TYPE: "INSTRUMENT_TYPE",
    FilterField.INSTRUMENT_SUBTYPE: "INSTRUMENT_SUBTYPE",
    FilterField.SECTOR: "SECTOR",
}


def _opt(
    duck_reader: DuckDBQueryPort,
    schema: str,
    table: str,
    col: str,
    alias: str,
    fallback: str = "NULL",
) -> str:
    if table_has_column(duck_reader, schema, table, col):
        return f'"{col}" AS {alias}'
    return f"{fallback} AS {alias}"


def query_portfolio_header(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
) -> dict[str, Any] | None:
    """Return latest `gold.investment_by_portfolio` row inside `[start_date, end_date]`."""
    if not table_exists(duck_reader, "gold", "investment_by_portfolio"):
        return None
    row = duck_reader.fetch_one(
        """
        SELECT *
        FROM gold.investment_by_portfolio
        WHERE "Closing_Date" BETWEEN ? AND ?
        ORDER BY "Closing_Date" DESC
        LIMIT 1;
        """,
        (start_date, end_date),
    )
    if row is not None:
        return row
    return duck_reader.fetch_one(
        """
        SELECT *
        FROM gold.investment_by_portfolio
        WHERE "Closing_Date" <= ?
        ORDER BY "Closing_Date" DESC
        LIMIT 1;
        """,
        (end_date,),
    )


def query_portfolio_holdings(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
    *,
    filters: FilterSet,
) -> tuple[InvestmentHoldingRow, ...]:
    """Query latest `Max_Closing_Date` holdings from `gold.investment_portfolio_summary`."""
    if not table_exists(duck_reader, "gold", "investment_portfolio_summary"):
        return ()

    s, t = "gold", "investment_portfolio_summary"
    latest_row = duck_reader.fetch_one(
        """
        SELECT MAX("Max_Closing_Date") AS max_dt
        FROM gold.investment_portfolio_summary
        WHERE "Max_Closing_Date" BETWEEN ? AND ?;
        """,
        (start_date, end_date),
    )
    target_dt = as_date(latest_row.get("max_dt")) if latest_row else None
    if target_dt is None:
        fallback_row = duck_reader.fetch_one(
            """
            SELECT MAX("Max_Closing_Date") AS max_dt
            FROM gold.investment_portfolio_summary
            WHERE "Max_Closing_Date" <= ?;
            """,
            (end_date,),
        )
        target_dt = as_date(fallback_row.get("max_dt")) if fallback_row else None
    if target_dt is None:
        return ()

    sub_filter = slice_filters(filters, _PORTFOLIO_SUMMARY_FILTER_MAP)
    where_extra, f_params = compile_filter_clause(sub_filter, _PORTFOLIO_SUMMARY_FILTER_MAP)

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            "ISIN" AS isin,
            {_opt(duck_reader, s, t, "INSTRUMENT_NAME", "inst_name", fallback='"ISIN"')},
            {_opt(duck_reader, s, t, "CURRENCY_ID", "currency_id", fallback="'INR_INR'")},
            {_opt(duck_reader, s, t, "INSTRUMENT_CLASS", "inst_class", fallback="'Unclassified'")},
            {_opt(duck_reader, s, t, "INSTRUMENT_TYPE", "inst_type", fallback="'Unclassified'")},
            {_opt(duck_reader, s, t, "INSTRUMENT_SUBTYPE", "inst_sub", fallback="'Unclassified'")},
            {_opt(duck_reader, s, t, "SECTOR", "sector", fallback="'Unclassified'")},
            "Max_Closing_Date" AS m_dt,
            {_opt(duck_reader, s, t, "ISIN_Market_Value", "mkt_val", fallback="0.0")},
            {_opt(duck_reader, s, t, "ISIN_Book_Value", "book_val", fallback="0.0")},
            {_opt(duck_reader, s, t, "ISIN_Market_Value_Local", "mkt_loc")},
            {_opt(duck_reader, s, t, "ISIN_Book_Value_Local", "book_loc")},
            {_opt(duck_reader, s, t, "ISIN_Unrealized_PnL", "unr_pnl", fallback="0.0")},
            {_opt(duck_reader, s, t, "ISIN_Asset_PnL", "ast_pnl")},
            {_opt(duck_reader, s, t, "ISIN_Forex_PnL", "fx_pnl")},
            {_opt(duck_reader, s, t, "ISIN_XIRR_Local", "xirr_loc")},
            {_opt(duck_reader, s, t, "ISIN_BM_XIRR", "bm_xirr")},
            {_opt(duck_reader, s, t, "ISIN_Active_Return", "act_ret")},
            {_opt(duck_reader, s, t, "ISIN_Weight", "isin_w")},
            {_opt(duck_reader, s, t, "Class_Weight", "cls_w")},
            {_opt(duck_reader, s, t, "Class_Target_Weight", "cls_tw")},
            {_opt(duck_reader, s, t, "Class_Drift", "cls_drift")},
            {_opt(duck_reader, s, t, "Rebalance_Required", "rebal", fallback="FALSE")},
            {_opt(duck_reader, s, t, "ISIN_Harvestable_Loss", "harv_loss")},
            {_opt(duck_reader, s, t, "Tax_Harvesting_Priority_Score", "harv_score")}
        FROM gold.investment_portfolio_summary
        WHERE "Max_Closing_Date" = ?
          AND ({where_extra})
        ORDER BY COALESCE("ISIN_Market_Value", 0.0) DESC;
        """,
        (target_dt, *f_params),
    )

    out: list[InvestmentHoldingRow] = []
    for r in rows:
        dt = as_date(r.get("m_dt"))
        if dt is None:
            continue
        out.append(
            InvestmentHoldingRow(
                isin=str(r.get("isin") or ""),
                instrument_name=str(r.get("inst_name") or r.get("isin") or ""),
                currency_id=str(r.get("currency_id") or "INR_INR"),
                instrument_class=str(r.get("inst_class") or "Unclassified"),
                instrument_type=str(r.get("inst_type") or "Unclassified"),
                instrument_subtype=str(r.get("inst_sub") or "Unclassified"),
                sector=str(r.get("sector") or "Unclassified"),
                closing_date=dt,
                market_value=as_float(r.get("mkt_val")) or 0.0,
                book_value=as_float(r.get("book_val")) or 0.0,
                market_value_local=as_float(r.get("mkt_loc")),
                book_value_local=as_float(r.get("book_loc")),
                unrealized_pnl=as_float(r.get("unr_pnl")) or 0.0,
                asset_pnl=as_float(r.get("ast_pnl")),
                forex_pnl=as_float(r.get("fx_pnl")),
                xirr_local=as_float(r.get("xirr_loc")),
                bm_xirr=as_float(r.get("bm_xirr")),
                active_return=as_float(r.get("act_ret")),
                isin_weight=as_float(r.get("isin_w")),
                class_weight=as_float(r.get("cls_w")),
                class_target_weight=as_float(r.get("cls_tw")),
                class_drift=as_float(r.get("cls_drift")),
                rebalance_required=bool(r.get("rebal")),
                harvestable_loss=as_float(r.get("harv_loss")),
                harvest_priority_score=as_float(r.get("harv_score")),
            )
        )
    return tuple(out)


def query_allocation(
    duck_reader: DuckDBQueryPort,
    dimension: AllocationDimension,
    start_date: date,
    end_date: date,
) -> tuple[AllocationRow, ...]:
    """Query one of the 9 whitelisted Gold investment allocation marts at latest Closing_Date."""
    if dimension not in _ALLOCATION_MART_MAP:
        raise ValueError(f"Unsupported allocation dimension: {dimension!r}")
    table_name, label_col, parent_col = _ALLOCATION_MART_MAP[dimension]
    if not table_exists(duck_reader, "gold", table_name):
        return ()

    latest_row = duck_reader.fetch_one(
        f"""
        SELECT MAX("Closing_Date") AS max_dt
        FROM gold.{table_name}
        WHERE "Closing_Date" BETWEEN ? AND ?;
        """,
        (start_date, end_date),
    )
    target_dt = as_date(latest_row.get("max_dt")) if latest_row else None
    if target_dt is None:
        fallback_row = duck_reader.fetch_one(
            f"""
            SELECT MAX("Closing_Date") AS max_dt
            FROM gold.{table_name}
            WHERE "Closing_Date" <= ?;
            """,
            (end_date,),
        )
        target_dt = as_date(fallback_row.get("max_dt")) if fallback_row else None
    if target_dt is None:
        return ()

    s = "gold"
    parent_expr = (
        f"COALESCE(CAST(\"{parent_col}\" AS VARCHAR), 'Unclassified') AS parent_lbl"
        if parent_col and table_has_column(duck_reader, s, table_name, parent_col)
        else "NULL AS parent_lbl"
    )
    rows = duck_reader.fetch_all(
        f"""
        SELECT
            COALESCE(NULLIF(TRIM(CAST("{label_col}" AS VARCHAR)), ''), 'Unclassified') AS lbl,
            {parent_expr},
            "Closing_Date" AS c_dt,
            {_opt(duck_reader, s, table_name, "Total_Invested_Value", "inv_val", fallback="0.0")},
            {_opt(duck_reader, s, table_name, "Total_Current_Value", "cur_val", fallback="0.0")},
            {_opt(duck_reader, s, table_name, "Unrealized_PL", "unr_pl", fallback="0.0")},
            {_opt(duck_reader, s, table_name, "Asset_PnL", "ast_pnl")},
            {_opt(duck_reader, s, table_name, "Forex_PnL", "fx_pnl")},
            {_opt(duck_reader, s, table_name, "Absolute_Return", "abs_ret")},
            {_opt(duck_reader, s, table_name, "XIRR", "xirr")},
            {_opt(duck_reader, s, table_name, "BM_XIRR", "bm_xirr")},
            {_opt(duck_reader, s, table_name, "Active_Return", "act_ret")},
            {_opt(duck_reader, s, table_name, "Max_Drawdown", "max_dd")},
            {_opt(duck_reader, s, table_name, "Weight", "weight")},
            {_opt(duck_reader, s, table_name, "Total_Stocks", "stocks")}
        FROM gold.{table_name}
        WHERE "Closing_Date" = ?
        ORDER BY COALESCE("Total_Current_Value", 0.0) DESC;
        """,
        (target_dt,),
    )

    out: list[AllocationRow] = []
    for r in rows:
        dt = as_date(r.get("c_dt"))
        if dt is None:
            continue
        stocks_raw = r.get("stocks")
        out.append(
            AllocationRow(
                label=str(r.get("lbl") or "Unclassified"),
                parent_label=str(r["parent_lbl"]) if r.get("parent_lbl") else None,
                closing_date=dt,
                invested_value=as_float(r.get("inv_val")) or 0.0,
                current_value=as_float(r.get("cur_val")) or 0.0,
                unrealized_pl=as_float(r.get("unr_pl")) or 0.0,
                asset_pnl=as_float(r.get("ast_pnl")),
                forex_pnl=as_float(r.get("fx_pnl")),
                absolute_return=as_float(r.get("abs_ret")),
                xirr=as_float(r.get("xirr")),
                bm_xirr=as_float(r.get("bm_xirr")),
                active_return=as_float(r.get("act_ret")),
                max_drawdown=as_float(r.get("max_dd")),
                weight=as_float(r.get("weight")),
                stock_count=int(stocks_raw) if stocks_raw is not None else None,
            )
        )
    return tuple(out)


def query_instrument_detail(
    duck_reader: DuckDBQueryPort,
    isin: str,
    start_date: date,
    end_date: date,
) -> InstrumentDetailSnapshot | None:
    """Query `silver.dim_instrument_master` metadata and monthly history for one ISIN."""
    master: dict[str, Any] = {}
    if table_exists(duck_reader, "silver", "dim_instrument_master"):
        master = (
            duck_reader.fetch_one(
                'SELECT * FROM silver.dim_instrument_master WHERE "ISIN" = ? LIMIT 1;',
                (isin,),
            )
            or {}
        )
    hist: list[InvestmentHoldingRow] = []
    if (end_date - start_date).days < 60:
        from datetime import timedelta

        start_date = end_date - timedelta(days=365)
    if table_exists(duck_reader, "gold", "investment_portfolio_summary"):
        rows = duck_reader.fetch_all(
            """
            SELECT
                "ISIN" AS isin,
                "INSTRUMENT_NAME" AS inst_name,
                "CURRENCY_ID" AS currency_id,
                "Max_Closing_Date" AS m_dt,
                "ISIN_Market_Value" AS mkt_val,
                "ISIN_Book_Value" AS book_val,
                "ISIN_Unrealized_PnL" AS unr_pnl,
                "ISIN_Weight" AS isin_w
            FROM gold.investment_portfolio_summary
            WHERE "ISIN" = ? AND "Max_Closing_Date" BETWEEN ? AND ?
            ORDER BY "Max_Closing_Date" ASC;
            """,
            (isin, start_date, end_date),
        )
        for r in rows:
            dt = as_date(r.get("m_dt"))
            if dt is None:
                continue
            hist.append(
                InvestmentHoldingRow(
                    isin=isin,
                    instrument_name=str(r.get("inst_name") or isin),
                    currency_id=str(r.get("currency_id") or "INR_INR"),
                    closing_date=dt,
                    market_value=as_float(r.get("mkt_val")) or 0.0,
                    book_value=as_float(r.get("book_val")) or 0.0,
                    unrealized_pnl=as_float(r.get("unr_pnl")) or 0.0,
                    isin_weight=as_float(r.get("isin_w")),
                )
            )
    if not master and not hist:
        return None
    inst_name = str(master.get("INSTRUMENT_NAME") or (hist[-1].instrument_name if hist else isin))
    clean_master = {str(k): (str(v) if v is not None else "—") for k, v in master.items()}
    return InstrumentDetailSnapshot(
        isin=isin,
        instrument_name=inst_name,
        master_metadata=clean_master,
        history=tuple(hist),
    )


def build_investment_kpis(
    header: dict[str, Any] | None,
    holdings: tuple[InvestmentHoldingRow, ...],
) -> tuple[OverviewKPI, ...]:
    """Build Investment Overview KPI cards from `gold.investment_by_portfolio`."""
    if header is None and not holdings:
        return ()
    h = header or {}
    as_of = as_date(h.get("Closing_Date")) or (holdings[0].closing_date if holdings else None)
    cur_val = as_float(h.get("Total_Current_Value"))
    inv_val = as_float(h.get("Total_Invested_Value"))
    unr_pl = as_float(h.get("Unrealized_PL"))
    abs_ret = as_float(h.get("Absolute_Return"))
    xirr = as_float(h.get("XIRR"))
    bm_xirr = as_float(h.get("BM_XIRR"))
    act_ret = as_float(h.get("Active_Return"))
    max_dd = as_float(h.get("Max_Drawdown"))
    hhi = as_float(h.get("HHI_Score"))
    stocks = as_float(h.get("Total_Stocks")) or float(len(holdings))

    asset_pnl = as_float(h.get("Asset_PnL"))
    forex_pnl = as_float(h.get("Forex_PnL"))
    after_tax_xirr = as_float(h.get("After_Tax_XIRR"))
    gain_pct = (
        abs_ret
        if abs_ret is not None
        else (
            (unr_pl / inv_val * 100.0) if (unr_pl is not None and inv_val and inv_val > 0) else None
        )
    )

    rebalance_count = sum(1 for row in holdings if row.rebalance_required)
    harvestable_total = sum(
        row.harvestable_loss
        for row in holdings
        if row.harvestable_loss is not None and row.harvestable_loss > 0
    )
    top5_wt = as_float(h.get("Top5_Holdings_Weight"))

    return (
        OverviewKPI(
            kpi_id="inv_invested_value",
            label="Invested Capital",
            value=inv_val,
            format=MetricFormat.CURRENCY,
            subtitle="Total historical cost basis deployed",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=inv_val is not None,
            breakups=(
                OverviewBreakupItem(label="Holdings", value=stocks, format=MetricFormat.INTEGER),
            ),
        ),
        OverviewKPI(
            kpi_id="inv_current_value",
            label="Portfolio Market Value",
            value=cur_val,
            format=MetricFormat.CURRENCY,
            delta_value=gain_pct,
            delta_format=MetricFormat.PERCENT,
            delta_label="Gain %",
            subtitle="Mark-to-market portfolio valuation",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=cur_val is not None,
            breakups=(OverviewBreakupItem(label="Invested", value=inv_val),),
        ),
        OverviewKPI(
            kpi_id="inv_unrealized_pl",
            label="Unrealized P&L",
            value=unr_pl,
            format=MetricFormat.CURRENCY,
            delta_value=gain_pct,
            delta_format=MetricFormat.PERCENT,
            delta_label="Gain %",
            subtitle="Asset price + FX translation P&L",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=unr_pl is not None,
            breakups=(
                OverviewBreakupItem(label="Asset P&L", value=asset_pnl),
                OverviewBreakupItem(label="Forex P&L", value=forex_pnl),
            ),
        ),
        OverviewKPI(
            kpi_id="inv_xirr",
            label="Portfolio XIRR",
            value=xirr,
            format=MetricFormat.PERCENT,
            delta_value=act_ret,
            delta_format=MetricFormat.PERCENT,
            delta_label="vs Benchmark",
            subtitle="Annualized money-weighted return",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=xirr is not None,
            breakups=(
                OverviewBreakupItem(label="BM XIRR", value=bm_xirr, format=MetricFormat.PERCENT),
                OverviewBreakupItem(
                    label="After-Tax", value=after_tax_xirr, format=MetricFormat.PERCENT
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="inv_benchmark",
            label="Benchmark XIRR",
            value=bm_xirr,
            format=MetricFormat.PERCENT,
            delta_value=act_ret,
            delta_format=MetricFormat.PERCENT,
            delta_label="Active Alpha",
            subtitle="Blended benchmark annualized return",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=bm_xirr is not None,
        ),
        OverviewKPI(
            kpi_id="inv_after_tax_xirr",
            label="After-Tax XIRR",
            value=after_tax_xirr,
            format=MetricFormat.PERCENT,
            delta_value=(
                (after_tax_xirr - xirr)
                if (after_tax_xirr is not None and xirr is not None)
                else None
            ),
            delta_format=MetricFormat.PERCENT,
            delta_label="Tax Drag",
            subtitle="Net annualized return post-tax",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=after_tax_xirr is not None,
            breakups=(
                OverviewBreakupItem(label="Pre-Tax", value=xirr, format=MetricFormat.PERCENT),
            ),
        ),
        OverviewKPI(
            kpi_id="inv_fx_pnl",
            label="FX Translation P&L",
            value=forex_pnl,
            format=MetricFormat.CURRENCY,
            subtitle="Currency impact separate from asset return",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=forex_pnl is not None,
            breakups=(OverviewBreakupItem(label="Asset P&L", value=asset_pnl),),
        ),
        OverviewKPI(
            kpi_id="inv_concentration_rebalance",
            label="Concentration & Rebalance",
            value=hhi,
            format=MetricFormat.NUMBER,
            subtitle="HHI concentration, rebalance & tax harvest",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=hhi is not None,
            breakups=(
                OverviewBreakupItem(label="Top 5 Wt", value=top5_wt, format=MetricFormat.PERCENT),
                OverviewBreakupItem(
                    label="Rebalance", value=float(rebalance_count), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="Harvest", value=harvestable_total, format=MetricFormat.CURRENCY
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="inv_risk_concentration",
            label="Max Drawdown",
            value=max_dd,
            format=MetricFormat.PERCENT,
            subtitle="Peak-to-trough portfolio drawdown",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=max_dd is not None,
            breakups=(
                OverviewBreakupItem(label="Holdings", value=stocks, format=MetricFormat.INTEGER),
            ),
        ),
    )
