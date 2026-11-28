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
    ConcentrationSummary,
    FxExposureRow,
    InstrumentDetailSnapshot,
    InvestmentHoldingRow,
    LotDetailSection,
    LotDetailSnapshot,
    LotFilter,
    LotKpiSummary,
    LotSummaryRow,
    PerformanceHistoryPoint,
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
    *,
    filters: FilterSet | None = None,
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
    extra_clauses: list[str] = []
    extra_params: list[Any] = []
    if filters and filters.filters:
        dim_cols = {
            AllocationDimension.CLASS: FilterField.INSTRUMENT_CLASS,
            AllocationDimension.SECTOR: FilterField.SECTOR,
            AllocationDimension.CURRENCY: FilterField.CURRENCY_ID,
        }
        col_map: dict[FilterField, str] = {}
        if dimension in dim_cols:
            col_map[dim_cols[dimension]] = label_col
        elif dimension == AllocationDimension.ISIN:
            col_map[FilterField.ISIN] = "ISIN"
            for fld, col_name in (
                (FilterField.CURRENCY_ID, "CURRENCY_ID"),
                (FilterField.INSTRUMENT_CLASS, "INSTRUMENT_CLASS"),
                (FilterField.SECTOR, "SECTOR"),
            ):
                if table_has_column(duck_reader, s, table_name, col_name):
                    col_map[fld] = col_name
        if col_map:
            scoped = FilterSet(filters=tuple(f for f in filters.filters if f.field in col_map))
            if scoped.filters:
                f_sql, f_params = compile_filter_clause(scoped, col_map)
                if f_sql and f_sql != "1 = 1":
                    extra_clauses.append(f"({f_sql})")
                    extra_params.extend(f_params)

    where_sql = '"Closing_Date" = ?' + (
        (" AND " + " AND ".join(extra_clauses)) if extra_clauses else ""
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
        WHERE {where_sql}
        ORDER BY COALESCE("Total_Current_Value", 0.0) DESC;
        """,
        [target_dt, *extra_params],
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
    *,
    prior_header: dict[str, Any] | None = None,
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
        abs(row.harvestable_loss)
        for row in holdings
        if row.harvestable_loss is not None and row.harvestable_loss != 0
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
            breakups=(OverviewBreakupItem(label="Asset P&L", value=asset_pnl),),
        ),
        OverviewKPI(
            kpi_id="inv_xirr",
            label="Portfolio XIRR",
            value=xirr,
            format=MetricFormat.PERCENT,
            delta_value=act_ret,
            delta_format=MetricFormat.PERCENT,
            delta_label="Benchmark Alpha",
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


def query_portfolio_performance_history(
    duck_reader: DuckDBQueryPort,
    start_date: date,
    end_date: date,
) -> tuple[tuple[PerformanceHistoryPoint, ...], ConcentrationSummary]:
    """Query `gold.investment_by_portfolio` history and latest concentration metrics."""
    if not table_exists(duck_reader, "gold", "investment_by_portfolio"):
        return (), ConcentrationSummary()
    rows = duck_reader.fetch_all(
        """
        SELECT *
        FROM gold.investment_by_portfolio
        WHERE "Closing_Date" BETWEEN ? AND ?
        ORDER BY "Closing_Date" ASC;
        """,
        (start_date, end_date),
    )
    if not rows:
        latest = duck_reader.fetch_one(
            """
            SELECT *
            FROM gold.investment_by_portfolio
            WHERE "Closing_Date" <= ?
            ORDER BY "Closing_Date" DESC
            LIMIT 1;
            """,
            (end_date,),
        )
        rows = [latest] if latest else []

    history: list[PerformanceHistoryPoint] = []
    for r in rows:
        dt = as_date(r.get("Closing_Date"))
        if dt is None:
            continue
        history.append(
            PerformanceHistoryPoint(
                closing_date=dt,
                invested_value=as_float(r.get("Total_Invested_Value")) or 0.0,
                current_value=as_float(r.get("Total_Current_Value")) or 0.0,
                unrealized_pl=as_float(r.get("Unrealized_PL")) or 0.0,
                asset_pnl=as_float(r.get("Asset_PnL")),
                forex_pnl=as_float(r.get("Forex_PnL")),
                forex_contribution_pct=as_float(r.get("Forex_Contribution_Pct")),
                absolute_return=as_float(r.get("Absolute_Return")),
                xirr=as_float(r.get("XIRR")),
                after_tax_xirr=as_float(r.get("After_Tax_XIRR")),
                bm_xirr=as_float(r.get("BM_XIRR")),
                active_return=as_float(r.get("Active_Return")),
                max_drawdown=as_float(r.get("Max_Drawdown")),
            )
        )
    latest_row = rows[-1] if rows else {}
    concentration = ConcentrationSummary(
        hhi_score=as_float(latest_row.get("HHI_Score")),
        effective_holdings_count=as_float(latest_row.get("Effective_Holdings_Count")),
        top1_weight=as_float(
            latest_row.get("Top1_Holding_Weight")
            if latest_row.get("Top1_Holding_Weight") is not None
            else latest_row.get("Top1_Holdings_Weight")
        ),
        top5_weight=as_float(latest_row.get("Top5_Holdings_Weight")),
        top10_weight=as_float(latest_row.get("Top10_Holdings_Weight")),
    )
    return tuple(history), concentration


def query_fx_exposure(
    duck_reader: DuckDBQueryPort,
    mart_name: str,
    bucket_col: str,
    end_date: date,
) -> tuple[FxExposureRow, ...]:
    """Query country or currency mart for local vs base valuation and FX XIRR impact."""
    if not table_exists(duck_reader, "gold", mart_name):
        return ()
    latest = duck_reader.fetch_one(
        f'SELECT MAX("Closing_Date") AS max_dt FROM gold.{mart_name} WHERE "Closing_Date" <= ?;',
        (end_date,),
    )
    max_dt = as_date(latest.get("max_dt")) if latest else None
    if max_dt is None:
        return ()
    rows = duck_reader.fetch_all(
        f"""
        SELECT *
        FROM gold.{mart_name}
        WHERE "Closing_Date" = ?
        ORDER BY COALESCE("Total_Current_Value", 0.0) DESC;
        """,
        (max_dt,),
    )
    result: list[FxExposureRow] = []
    for r in rows:
        bucket = str(r.get(bucket_col) or "Unclassified")
        result.append(
            FxExposureRow(
                bucket=bucket,
                local_invested=as_float(r.get("Total_Invested_Value_Local")),
                local_current=as_float(r.get("Total_Current_Value_Local")),
                base_invested=as_float(r.get("Total_Invested_Value")) or 0.0,
                base_current=as_float(r.get("Total_Current_Value")) or 0.0,
                fx_rate=as_float(r.get("FX_Rate")),
                blended_buy_rate=as_float(r.get("Blended_Buy_Rate")),
                currency_appreciation_pct=as_float(r.get("Currency_Appreciation_Pct")),
                local_xirr=as_float(r.get("XIRR_Local")),
                base_xirr=as_float(r.get("XIRR")),
                fx_xirr_impact=as_float(r.get("FX_XIRR_Impact")),
                bm_xirr=as_float(r.get("BM_XIRR")),
                active_return=as_float(r.get("Active_Return")),
            )
        )
    return tuple(result)


def build_performance_kpis(
    header: dict[str, Any] | None,
    concentration: ConcentrationSummary,
    *,
    isin_rows: tuple[AllocationRow, ...] = (),
    prior_header: dict[str, Any] | None = None,
) -> tuple[OverviewKPI, ...]:
    """Build 9 unique KPI cards for the Investments Performance & Benchmarks tab."""
    if header is None:
        return ()
    as_of = as_date(header.get("Closing_Date"))
    abs_ret = as_float(header.get("Absolute_Return"))
    xirr = as_float(header.get("XIRR"))
    after_tax_xirr = as_float(header.get("After_Tax_XIRR"))
    bm_xirr = as_float(header.get("BM_XIRR"))
    act_ret = as_float(header.get("Active_Return"))
    max_dd = as_float(header.get("Max_Drawdown"))
    asset_pnl = as_float(header.get("Asset_PnL"))
    forex_pnl = as_float(header.get("Forex_PnL"))
    fx_contrib = as_float(header.get("Forex_Contribution_Pct"))
    inv_val = as_float(header.get("Total_Invested_Value"))
    cur_val = as_float(header.get("Total_Current_Value"))
    p_xirr = as_float((prior_header or {}).get("XIRR"))
    p_abs = as_float((prior_header or {}).get("Absolute_Return"))

    laggards = sum(1 for r in isin_rows if r.active_return is not None and r.active_return < 0)
    fy_realized_net = as_float(header.get("FY_Realized_Net_PnL"))
    fy_realized_gain = as_float(header.get("FY_Realized_Gain"))
    fy_realized_loss = as_float(header.get("FY_Realized_Loss"))
    ltcg_mature_val = as_float(header.get("LTCG_Mature_Value"))
    cur_val_tmp = as_float(header.get("Total_Current_Value"))
    ltcg_pct = (
        (ltcg_mature_val / cur_val_tmp)
        if (ltcg_mature_val is not None and cur_val_tmp and cur_val_tmp > 0)
        else None
    )

    return (
        OverviewKPI(
            kpi_id="perf_laggards",
            label="Benchmark Laggards",
            value=float(laggards),
            format=MetricFormat.INTEGER,
            subtitle="Holdings trailing their benchmark XIRR",
            source_table="gold.investment_by_isin",
            as_of_date=as_of,
            drill_route="/investments",
            available=bool(isin_rows),
            breakups=(
                OverviewBreakupItem(
                    label="Total ISINs",
                    value=float(len(isin_rows)),
                    format=MetricFormat.INTEGER,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="perf_fy_realized_net",
            label="FY Realized Net P&L",
            value=fy_realized_net,
            format=MetricFormat.CURRENCY,
            subtitle="Fiscal year realized capital gains net of losses",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=fy_realized_net is not None,
            breakups=(
                OverviewBreakupItem(label="Gain", value=fy_realized_gain),
                OverviewBreakupItem(label="Loss", value=fy_realized_loss),
            ),
        ),
        OverviewKPI(
            kpi_id="perf_ltcg_eligible",
            label="LTCG Mature %",
            value=ltcg_pct,
            format=MetricFormat.PERCENT,
            subtitle="Share of portfolio matured for LTCG",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=ltcg_pct is not None,
            breakups=(OverviewBreakupItem(label="Mature Val", value=ltcg_mature_val),),
        ),
        OverviewKPI(
            kpi_id="perf_abs_return",
            label="Absolute Return %",
            value=abs_ret,
            format=MetricFormat.PERCENT,
            delta_value=(abs_ret - p_abs) if (abs_ret is not None and p_abs is not None) else None,
            delta_format=MetricFormat.PERCENT,
            delta_label="vs Prior",
            subtitle="Cumulative return on invested capital",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=abs_ret is not None,
            breakups=(
                OverviewBreakupItem(label="Invested", value=inv_val),
                OverviewBreakupItem(label="Current", value=cur_val),
            ),
        ),
        OverviewKPI(
            kpi_id="perf_xirr",
            label="Portfolio XIRR",
            value=xirr,
            format=MetricFormat.PERCENT,
            delta_value=(xirr - p_xirr) if (xirr is not None and p_xirr is not None) else act_ret,
            delta_format=MetricFormat.PERCENT,
            delta_label="vs Prior"
            if (xirr is not None and p_xirr is not None)
            else "Benchmark Alpha",
            subtitle="Annualized money-weighted return",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=xirr is not None,
        ),
        OverviewKPI(
            kpi_id="perf_after_tax_xirr",
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
        ),
        OverviewKPI(
            kpi_id="perf_bm_xirr",
            label="Benchmark XIRR",
            value=bm_xirr,
            format=MetricFormat.PERCENT,
            subtitle="Blended benchmark annualized return",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=bm_xirr is not None,
        ),
        OverviewKPI(
            kpi_id="perf_active_return",
            label="Active Alpha",
            value=act_ret,
            format=MetricFormat.PERCENT,
            subtitle="Portfolio XIRR minus Benchmark XIRR",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=act_ret is not None,
        ),
        OverviewKPI(
            kpi_id="perf_max_drawdown",
            label="Max Drawdown",
            value=max_dd,
            format=MetricFormat.PERCENT,
            subtitle="Peak-to-trough portfolio drawdown",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=max_dd is not None,
        ),
        OverviewKPI(
            kpi_id="perf_asset_pnl",
            label="Asset Price P&L",
            value=asset_pnl,
            format=MetricFormat.CURRENCY,
            subtitle="Pure underlying asset price gain/loss",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=asset_pnl is not None,
        ),
        OverviewKPI(
            kpi_id="perf_fx_pnl",
            label="Forex P&L & Contribution",
            value=forex_pnl,
            format=MetricFormat.CURRENCY,
            delta_value=fx_contrib,
            delta_format=MetricFormat.PERCENT,
            delta_label="FX Share",
            subtitle="Currency translation impact on return",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=forex_pnl is not None,
        ),
        OverviewKPI(
            kpi_id="perf_hhi",
            label="Concentration (HHI)",
            value=concentration.hhi_score,
            format=MetricFormat.NUMBER,
            subtitle="Herfindahl-Hirschman concentration index",
            source_table="gold.investment_by_portfolio",
            as_of_date=as_of,
            drill_route="/investments",
            available=concentration.hhi_score is not None,
            breakups=(
                OverviewBreakupItem(
                    label="Effective N",
                    value=concentration.effective_holdings_count,
                    format=MetricFormat.NUMBER,
                ),
                OverviewBreakupItem(
                    label="Top 5 Wt",
                    value=concentration.top5_weight,
                    format=MetricFormat.PERCENT,
                ),
            ),
        ),
    )


def resolve_lot_closing_date(
    duck_reader: DuckDBQueryPort,
    requested_date: date | None,
    end_date: date | None = None,
) -> tuple[date | None, tuple[date, ...]]:
    """Resolve exact lot Closing_Date or return nearest available dates."""
    if not table_exists(duck_reader, "silver", "fact_investment_lot_analytics"):
        return None, ()
    if requested_date is not None:
        exact = duck_reader.fetch_one(
            'SELECT MAX("Closing_Date") AS dt FROM silver.fact_investment_lot_analytics WHERE "Closing_Date" = ?;',
            (requested_date,),
        )
        if exact and exact.get("dt") is not None:
            return as_date(exact["dt"]), ()
        before = duck_reader.fetch_one(
            'SELECT MAX("Closing_Date") AS dt FROM silver.fact_investment_lot_analytics WHERE "Closing_Date" < ?;',
            (requested_date,),
        )
        after = duck_reader.fetch_one(
            'SELECT MIN("Closing_Date") AS dt FROM silver.fact_investment_lot_analytics WHERE "Closing_Date" > ?;',
            (requested_date,),
        )
        nearest = tuple(
            dt
            for dt in (
                as_date(before["dt"]) if before else None,
                as_date(after["dt"]) if after else None,
            )
            if dt is not None
        )
        return None, nearest

    bound = end_date or date(2099, 12, 31)
    latest = duck_reader.fetch_one(
        'SELECT MAX("Closing_Date") AS dt FROM silver.fact_investment_lot_analytics WHERE "Closing_Date" <= ?;',
        (bound,),
    )
    dt = as_date(latest["dt"]) if latest else None
    if dt is None:
        fallback = duck_reader.fetch_one(
            'SELECT MAX("Closing_Date") AS dt FROM silver.fact_investment_lot_analytics;'
        )
        dt = as_date(fallback["dt"]) if fallback else None
    return dt, ()


def _build_lot_where_clause(
    lot_filter: LotFilter,
    closing_date: date,
) -> tuple[str, list[Any]]:
    clauses: list[str] = ['f."Closing_Date" = ?']
    params: list[Any] = [closing_date]

    if lot_filter.isin:
        clauses.append('f."ISIN" = ?')
        params.append(lot_filter.isin.strip())
    if lot_filter.instrument_class:
        clauses.append("COALESCE(im.\"INSTRUMENT_CLASS\", 'Unclassified') = ?")
        params.append(lot_filter.instrument_class)
    if lot_filter.sector:
        clauses.append("COALESCE(im.\"SECTOR\", 'Unclassified') = ?")
        params.append(lot_filter.sector)
    if lot_filter.instrument_type:
        clauses.append("COALESCE(im.\"INSTRUMENT_TYPE\", 'Unclassified') = ?")
        params.append(lot_filter.instrument_type)
    if lot_filter.instrument_subtype:
        clauses.append("COALESCE(im.\"INSTRUMENT_SUBTYPE\", 'Unclassified') = ?")
        params.append(lot_filter.instrument_subtype)
    if lot_filter.currency_id:
        clauses.append('f."CURRENCY_ID" = ?')
        params.append(lot_filter.currency_id)
    if lot_filter.holding_type:
        clauses.append('f."Holding_Type" = ?')
        params.append(lot_filter.holding_type)
    if lot_filter.can_harvest_loss is not None:
        clauses.append('COALESCE(f."Can_Harvest_Loss", FALSE) = ?')
        params.append(lot_filter.can_harvest_loss)
    if lot_filter.harvest_recommendation:
        clauses.append('f."Harvest_Recommendation" = ?')
        params.append(lot_filter.harvest_recommendation)
    if lot_filter.days_to_ltcg_min is not None:
        clauses.append('f."Days_To_LTCG" >= ?')
        params.append(lot_filter.days_to_ltcg_min)
    if lot_filter.days_to_ltcg_max is not None:
        clauses.append('f."Days_To_LTCG" <= ?')
        params.append(lot_filter.days_to_ltcg_max)
    if lot_filter.gain_state == "gain":
        clauses.append('COALESCE(f."P/L", 0.0) > 0')
    elif lot_filter.gain_state == "loss":
        clauses.append('COALESCE(f."P/L", 0.0) < 0')
    if lot_filter.search and lot_filter.search.strip():
        pat = f"%{lot_filter.search.strip().lower()}%"
        clauses.append(
            '(LOWER(f."Lot_ID") LIKE ? OR LOWER(f."ISIN") LIKE ? '
            "OR LOWER(COALESCE(im.\"INSTRUMENT_NAME\", '')) LIKE ?)"
        )
        params.extend([pat, pat, pat])

    return " AND ".join(clauses), params


def _lot_from_clause(duck_reader: DuckDBQueryPort) -> str:
    has_im = table_exists(duck_reader, "silver", "dim_instrument_master")
    sql = "FROM silver.fact_investment_lot_analytics f "
    if has_im:
        sql += 'LEFT JOIN silver.dim_instrument_master im ON f."ISIN" = im."ISIN" '
    return sql


def query_lot_summary_and_kpis(
    duck_reader: DuckDBQueryPort,
    lot_filter: LotFilter,
    closing_date: date,
    *,
    prior_summary: LotKpiSummary | None = None,
) -> tuple[LotKpiSummary, tuple[OverviewKPI, ...]]:
    """Compute aggregate lot KPIs for the active `LotFilter`."""
    where_sql, params = _build_lot_where_clause(lot_filter, closing_date)
    has_im = table_exists(duck_reader, "silver", "dim_instrument_master")
    from_sql = "FROM silver.fact_investment_lot_analytics f " + (
        'LEFT JOIN silver.dim_instrument_master im ON f."ISIN" = im."ISIN" ' if has_im else ""
    )
    row = (
        duck_reader.fetch_one(
            f"""
            SELECT
                COUNT(*) AS total_lots,
                COALESCE(SUM(f."Buy_Value"), 0.0) AS total_buy,
                COALESCE(SUM(f."Close_Value"), 0.0) AS total_close,
                COALESCE(SUM(f."P/L"), 0.0) AS total_pl,
                SUM(CASE WHEN f."Holding_Type" = 'LTCG' THEN 1 ELSE 0 END) AS ltcg_lots,
                SUM(CASE WHEN f."Holding_Type" = 'STCG' THEN 1 ELSE 0 END) AS stcg_lots,
                COALESCE(SUM(CASE WHEN f."Holding_Type" = 'LTCG' THEN f."Close_Value" ELSE 0.0 END), 0.0) AS ltcg_val,
                COALESCE(SUM(CASE WHEN f."Holding_Type" = 'STCG' THEN f."Close_Value" ELSE 0.0 END), 0.0) AS stcg_val,
                SUM(CASE WHEN COALESCE(f."Can_Harvest_Loss", FALSE) THEN 1 ELSE 0 END) AS harvest_lots,
                COALESCE(SUM(CASE WHEN COALESCE(f."Can_Harvest_Loss", FALSE) THEN ABS(COALESCE(f."P/L", 0.0)) ELSE 0.0 END), 0.0) AS harvest_val,
                COALESCE(SUM(COALESCE(f."LTCG_Tax_If_Sold", 0.0) + COALESCE(f."STCG_Tax_If_Sold", 0.0)), 0.0) AS tax_if_sold,
                SUM(CASE WHEN COALESCE(f."Is_Lagging_Benchmark", 0) = 1 THEN 1 ELSE 0 END) AS lagging_lots,
                SUM(CASE WHEN f."Holding_Type" = 'STCG' AND f."Days_To_LTCG" BETWEEN 0 AND 30 THEN 1 ELSE 0 END) AS mat_30,
                SUM(CASE WHEN f."Holding_Type" = 'STCG' AND f."Days_To_LTCG" BETWEEN 0 AND 90 THEN 1 ELSE 0 END) AS mat_90
            {from_sql}
            WHERE {where_sql};
            """,
            params,
        )
        or {}
    )
    summary = LotKpiSummary(
        total_lots=int(row.get("total_lots") or 0),
        total_buy_value=float(row.get("total_buy") or 0.0),
        total_close_value=float(row.get("total_close") or 0.0),
        total_unrealized_pl=float(row.get("total_pl") or 0.0),
        ltcg_lots=int(row.get("ltcg_lots") or 0),
        stcg_lots=int(row.get("stcg_lots") or 0),
        ltcg_value=float(row.get("ltcg_val") or 0.0),
        stcg_value=float(row.get("stcg_val") or 0.0),
        harvest_loss_lots=int(row.get("harvest_lots") or 0),
        harvest_loss_value=float(row.get("harvest_val") or 0.0),
        total_tax_if_sold=float(row.get("tax_if_sold") or 0.0),
        lagging_lots=int(row.get("lagging_lots") or 0),
        maturing_30d_lots=int(row.get("mat_30") or 0),
        maturing_90d_lots=int(row.get("mat_90") or 0),
    )
    gain_pct = (
        (summary.total_unrealized_pl / summary.total_buy_value)
        if summary.total_buy_value > 0
        else None
    )
    kpis = (
        OverviewKPI(
            kpi_id="lot_total_count",
            label="Active Lots",
            value=float(summary.total_lots),
            format=MetricFormat.INTEGER,
            delta_value=(
                float(summary.total_lots - prior_summary.total_lots)
                if prior_summary is not None
                else None
            ),
            delta_format=MetricFormat.INTEGER,
            delta_label="vs Prior",
            subtitle="Lots matching active filter",
            source_table="silver.fact_investment_lot_analytics",
            as_of_date=closing_date,
            drill_route="/investments",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="LTCG", value=float(summary.ltcg_lots), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="STCG", value=float(summary.stcg_lots), format=MetricFormat.INTEGER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="lot_buy_value",
            label="Lot Cost Basis",
            value=summary.total_buy_value,
            format=MetricFormat.CURRENCY,
            subtitle="Total acquisition cost across filtered lots",
            source_table="silver.fact_investment_lot_analytics",
            as_of_date=closing_date,
            drill_route="/investments",
            available=True,
        ),
        OverviewKPI(
            kpi_id="lot_close_value",
            label="Lot Market Value",
            value=summary.total_close_value,
            format=MetricFormat.CURRENCY,
            delta_value=gain_pct,
            delta_format=MetricFormat.PERCENT,
            delta_label="Gain %",
            subtitle="Mark-to-market value across filtered lots",
            source_table="silver.fact_investment_lot_analytics",
            as_of_date=closing_date,
            drill_route="/investments",
            available=True,
        ),
        OverviewKPI(
            kpi_id="lot_unrealized_pl",
            label="Lot Unrealized P&L",
            value=summary.total_unrealized_pl,
            format=MetricFormat.CURRENCY,
            subtitle="Aggregate P/L across filtered lots",
            source_table="silver.fact_investment_lot_analytics",
            as_of_date=closing_date,
            drill_route="/investments",
            available=True,
        ),
        OverviewKPI(
            kpi_id="lot_ltcg_value",
            label="LTCG Market Value",
            value=summary.ltcg_value,
            format=MetricFormat.CURRENCY,
            subtitle="Long-term eligible lot valuation",
            source_table="silver.fact_investment_lot_analytics",
            as_of_date=closing_date,
            drill_route="/investments",
            available=True,
            breakups=(OverviewBreakupItem(label="STCG Val", value=summary.stcg_value),),
        ),
        OverviewKPI(
            kpi_id="lot_tax_if_sold",
            label="Est. Tax If Sold",
            value=summary.total_tax_if_sold,
            format=MetricFormat.CURRENCY,
            subtitle="Core LTCG + STCG tax liability if liquidated",
            source_table="silver.fact_investment_lot_analytics",
            as_of_date=closing_date,
            drill_route="/investments",
            available=True,
        ),
        OverviewKPI(
            kpi_id="lot_harvest_loss",
            label="Harvestable Loss Lots",
            value=float(summary.harvest_loss_lots),
            format=MetricFormat.INTEGER,
            subtitle="Lots flagged Can_Harvest_Loss = True",
            source_table="silver.fact_investment_lot_analytics",
            as_of_date=closing_date,
            drill_route="/investments",
            available=True,
            breakups=(OverviewBreakupItem(label="Loss Val", value=summary.harvest_loss_value),),
        ),
        OverviewKPI(
            kpi_id="lot_maturing_90d",
            label="Maturing to LTCG (≤90d)",
            value=float(summary.maturing_90d_lots),
            format=MetricFormat.INTEGER,
            subtitle="STCG lots crossing LTCG threshold in 90 days",
            source_table="silver.fact_investment_lot_analytics",
            as_of_date=closing_date,
            drill_route="/investments",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="≤30d",
                    value=float(summary.maturing_30d_lots),
                    format=MetricFormat.INTEGER,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="lot_lagging",
            label="Benchmark Lagging Lots",
            value=float(summary.lagging_lots),
            format=MetricFormat.INTEGER,
            subtitle="Lots trailing their assigned benchmark",
            source_table="silver.fact_investment_lot_analytics",
            as_of_date=closing_date,
            drill_route="/investments",
            available=True,
        ),
    )
    return summary, kpis


def query_lot_rows(
    duck_reader: DuckDBQueryPort,
    lot_filter: LotFilter,
    closing_date: date,
    *,
    limit: int = 25,
    offset: int = 0,
) -> tuple[int, tuple[LotSummaryRow, ...]]:
    """Query paginated `silver.fact_investment_lot_analytics` summary rows."""
    where_sql, params = _build_lot_where_clause(lot_filter, closing_date)
    from_sql = _lot_from_clause(duck_reader)
    has_im = table_exists(duck_reader, "silver", "dim_instrument_master")

    inst_name_expr = 'COALESCE(im."INSTRUMENT_NAME", f."ISIN")' if has_im else 'f."ISIN"'
    inst_cls_expr = (
        "COALESCE(im.\"INSTRUMENT_CLASS\", 'Unclassified')" if has_im else "'Unclassified'"
    )
    bm_name_expr = "COALESCE(f.\"BENCHMARK_ID\", '—')"

    cnt_row = duck_reader.fetch_one(
        f"SELECT COUNT(*) AS cnt {from_sql} WHERE {where_sql};",
        params,
    )
    total = int(cnt_row["cnt"]) if cnt_row and cnt_row.get("cnt") is not None else 0

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            f."Lot_ID" AS lot_id,
            f."Closing_Date" AS closing_date,
            f."ISIN" AS isin,
            {inst_name_expr} AS inst_name,
            {inst_cls_expr} AS inst_class,
            f."CURRENCY_ID" AS currency_id,
            {bm_name_expr} AS bm_name,
            f."Buy_Date" AS buy_date,
            f."Quantity" AS qty,
            f."Buy_Value" AS buy_val,
            f."Close_Value" AS close_val,
            f."P/L" AS pl,
            f."Absolute_Return" AS abs_ret,
            f."XIRR" AS xirr,
            f."BM_XIRR" AS bm_xirr,
            f."Lot_Alpha" AS lot_alpha,
            f."Holding_Type" AS holding_type,
            f."Age_Days" AS age_days,
            f."Days_To_LTCG" AS days_to_ltcg,
            COALESCE(f."LTCG_Tax_If_Sold", 0.0) + COALESCE(f."STCG_Tax_If_Sold", 0.0) AS tax_if_sold,
            f."After_Tax_PL" AS after_tax_pl,
            COALESCE(f."Can_Harvest_Loss", FALSE) AS can_harvest,
            COALESCE(f."Harvest_Recommendation", 'HOLD') AS harvest_rec,
            COALESCE(f."Is_Lagging_Benchmark", 0) AS is_lagging
        {from_sql}
        WHERE {where_sql}
        ORDER BY COALESCE(f."Close_Value", 0.0) DESC, f."Buy_Date" ASC
        LIMIT ? OFFSET ?;
        """,
        [*params, limit, offset],
    )
    result: list[LotSummaryRow] = []
    for r in rows:
        dt = as_date(r.get("closing_date")) or closing_date
        result.append(
            LotSummaryRow(
                lot_id=str(r.get("lot_id") or ""),
                closing_date=dt,
                isin=str(r.get("isin") or ""),
                instrument_name=str(r.get("inst_name") or ""),
                instrument_class=str(r.get("inst_class") or "Unclassified"),
                currency_id=str(r.get("currency_id") or "INR_INR"),
                benchmark_name=str(r.get("bm_name") or "—"),
                buy_date=as_date(r.get("buy_date")),
                quantity=as_float(r.get("qty")) or 0.0,
                buy_value=as_float(r.get("buy_val")) or 0.0,
                close_value=as_float(r.get("close_val")) or 0.0,
                unrealized_pl=as_float(r.get("pl")) or 0.0,
                absolute_return=as_float(r.get("abs_ret")),
                xirr=as_float(r.get("xirr")),
                bm_xirr=as_float(r.get("bm_xirr")),
                lot_alpha=as_float(r.get("lot_alpha")),
                holding_type=str(r.get("holding_type") or "STCG"),
                age_days=int(r["age_days"]) if r.get("age_days") is not None else None,
                days_to_ltcg=int(r["days_to_ltcg"]) if r.get("days_to_ltcg") is not None else None,
                tax_if_sold=as_float(r.get("tax_if_sold")) or 0.0,
                after_tax_pl=as_float(r.get("after_tax_pl")),
                can_harvest_loss=bool(r.get("can_harvest")),
                harvest_recommendation=str(r.get("harvest_rec") or "HOLD"),
                is_lagging_benchmark=bool(int(r.get("is_lagging") or 0)),
            )
        )
    return total, tuple(result)


_LOT_DETAIL_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Identity",
        (
            "Lot_ID",
            "Closing_Date",
            "ISIN",
            "CURRENCY_ID",
            "Buy_Date",
            "Purchase_ID",
            "Lot_Source_Type",
            "BENCHMARK_ID",
            "TAX_TYPE",
            "TAX_SUBTYPE",
        ),
    ),
    (
        "Cost & Market",
        (
            "Quantity",
            "Execution_Residual",
            "Buy_Price",
            "Market_Price",
            "Buy_Value",
            "Close_Value",
            "P/L",
            "Absolute_Return",
        ),
    ),
    (
        "Local / Base / FX",
        (
            "Buy_Value_Local",
            "Close_Value_Local",
            "Blended_FX_Buy_Rate",
            "Buy_Price_Local",
            "Market_Price_Local",
            "FX_Rate_Buy",
            "FX_Rate_Snap",
            "Currency_Appreciation_Pct",
            "Absolute_Return_Local",
            "Asset_PnL",
            "Forex_PnL",
            "Forex_Contribution_Pct",
            "Asset_Return_Pct",
            "Forex_Return_Pct",
        ),
    ),
    (
        "Performance",
        tuple(
            "Lot_CAGR Lot_CAGR_Status Lot_CAGR_Local CAGR CAGR_Status "
            "XIRR XIRR_Status XIRR_Local FX_XIRR_Impact After_Tax_XIRR".split()
        ),
    ),
    (
        "Benchmark / Alpha",
        tuple(
            "BM_Buy_Price BM_Market_Price Lot_BM_Return Lot_BM_CAGR "
            "Lot_BM_CAGR_Status Lot_BM_CAGR_Local BM_CAGR BM_CAGR_Status "
            "BM_XIRR BM_XIRR_Local Active_Return Active_Return_Local "
            "Lot_Alpha Is_Lagging_Benchmark".split()
        ),
    ),
    (
        "Risk / Weight",
        (
            "Max_Drawdown",
            "Lot_Weight",
            "Portfolio_Weight",
            "Outperforming_Lot_Ratio",
            "Dietz_Day_Weight",
        ),
    ),
    (
        "Holding / Tax",
        (
            "Age_Days",
            "LTCG_Threshold_Days",
            "Days_To_LTCG",
            "Holding_Type",
            "Tax_Rate",
            "Unrealized_LTCG",
            "Unrealized_STCG",
            "Unrealized_Gain",
            "Unrealized_LTCL",
            "Unrealized_STCL",
            "Unrealized_Loss",
            "LTCG_Tax_If_Sold",
            "STCG_Tax_If_Sold",
            "After_Tax_PL",
            "After_Tax_Close_Value",
            "Equity_LTCG_Exemption",
            "Stepup_Eligible",
        ),
    ),
    (
        "FY Realized",
        (
            "FY",
            "FY_Realized_LTCG",
            "FY_Realized_STCG",
            "FY_Realized_Gain",
            "FY_Realized_LTCL",
            "FY_Realized_STCL",
            "FY_Realized_Loss",
            "FY_Realized_Net_PnL",
        ),
    ),
    ("Harvesting", ("Can_Harvest_Loss", "Harvest_Recommendation")),
)


def query_lot_detail(
    duck_reader: DuckDBQueryPort,
    lot_id: str,
    closing_date: date,
) -> LotDetailSnapshot | None:
    """Select all 88 columns for `(Lot_ID, Closing_Date)` grouped into 9 sections."""
    if not table_exists(duck_reader, "silver", "fact_investment_lot_analytics"):
        return None
    row = duck_reader.fetch_one(
        'SELECT * FROM silver.fact_investment_lot_analytics WHERE "Lot_ID" = ? AND "Closing_Date" = ? LIMIT 1;',
        (lot_id, closing_date),
    )
    if row is None:
        return None
    isin = str(row.get("ISIN") or "")
    inst_name = isin
    if isin and table_exists(duck_reader, "silver", "dim_instrument_master"):
        m = duck_reader.fetch_one(
            'SELECT "INSTRUMENT_NAME" FROM silver.dim_instrument_master WHERE "ISIN" = ? LIMIT 1;',
            (isin,),
        )
        if m and m.get("INSTRUMENT_NAME"):
            inst_name = str(m["INSTRUMENT_NAME"])

    sections: list[LotDetailSection] = []
    for title, cols in _LOT_DETAIL_GROUPS:
        fields = tuple((col.replace("_", " "), col, row.get(col)) for col in cols if col in row)
        sections.append(LotDetailSection(title=title, fields=fields))

    return LotDetailSnapshot(
        lot_id=lot_id,
        closing_date=closing_date,
        instrument_name=inst_name,
        isin=isin,
        sections=tuple(sections),
        raw_columns=dict(row),
    )
