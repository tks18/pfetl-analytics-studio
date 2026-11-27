"""Realized, Reconciliation & Market Evidence queries for Investments."""

from __future__ import annotations

from datetime import date
from typing import Any

from pfetl_analytics_studio.analytics.investments_models import (
    BenchmarkObservationPoint,
    FxObservationPoint,
    MarketEvidenceSnapshot,
    MarketObservationPoint,
    RealizedEventFilter,
    RealizedEventRow,
    ReconciliationEventRow,
    ReconciliationGroupRow,
    TradeLineageTrace,
)
from pfetl_analytics_studio.analytics.overview_models import (
    OverviewBreakupItem,
    OverviewKPI,
    as_date,
    as_float,
)
from pfetl_analytics_studio.analytics.overview_queries import table_exists
from pfetl_analytics_studio.data import DuckDBQueryPort
from pfetl_analytics_studio.semantic import MetricFormat


def query_realized_events(
    duck_reader: DuckDBQueryPort,
    filter_spec: RealizedEventFilter,
) -> tuple[tuple[RealizedEventRow, ...], tuple[str, ...]]:
    """Query `silver.fact_investment_realized_events` with optional FY/type/search filters."""
    if not table_exists(duck_reader, "silver", "fact_investment_realized_events"):
        return (), ()
    fy_rows = duck_reader.fetch_all(
        'SELECT DISTINCT "FY" AS fy FROM silver.fact_investment_realized_events WHERE "FY" IS NOT NULL ORDER BY "FY" DESC;'
    )
    available_fys = tuple(str(r["fy"]) for r in fy_rows if r.get("fy"))

    has_im = table_exists(duck_reader, "silver", "dim_instrument_master")
    from_sql = "FROM silver.fact_investment_realized_events r " + (
        'LEFT JOIN silver.dim_instrument_master im ON r."ISIN" = im."ISIN" ' if has_im else ""
    )
    inst_expr = 'COALESCE(im."INSTRUMENT_NAME", r."ISIN")' if has_im else 'r."ISIN"'

    clauses: list[str] = ["1 = 1"]
    params: list[Any] = []
    if filter_spec.fy:
        clauses.append('r."FY" = ?')
        params.append(filter_spec.fy)
    if filter_spec.holding_type:
        clauses.append('r."Holding_Type" = ?')
        params.append(filter_spec.holding_type)
    if filter_spec.isin:
        clauses.append('r."ISIN" = ?')
        params.append(filter_spec.isin.strip())
    if filter_spec.gain_state == "gain":
        clauses.append('COALESCE(r."Realized_Gain_Loss", 0.0) > 0')
    elif filter_spec.gain_state == "loss":
        clauses.append('COALESCE(r."Realized_Gain_Loss", 0.0) < 0')
    if filter_spec.search and filter_spec.search.strip():
        pat = f"%{filter_spec.search.strip().lower()}%"
        clauses.append(
            '(LOWER(r."Realized_Event_ID") LIKE ? OR LOWER(r."Sale_ID") LIKE ? '
            'OR LOWER(r."Lot_ID") LIKE ? OR LOWER(r."ISIN") LIKE ? '
            f"OR LOWER({inst_expr}) LIKE ?)"
        )
        params.extend([pat, pat, pat, pat, pat])

    rows = duck_reader.fetch_all(
        f"""
        SELECT
            r.*,
            {inst_expr} AS inst_name
        {from_sql}
        WHERE {" AND ".join(clauses)}
        ORDER BY r."Disposal_Date" DESC, r."Realized_Event_ID" ASC;
        """,
        params,
    )
    result: list[RealizedEventRow] = []
    for r in rows:
        result.append(
            RealizedEventRow(
                realized_event_id=str(r.get("Realized_Event_ID") or ""),
                sale_id=str(r.get("Sale_ID") or ""),
                lot_id=str(r.get("Lot_ID") or ""),
                purchase_id=str(r.get("Purchase_ID") or ""),
                isin=str(r.get("ISIN") or ""),
                instrument_name=str(r.get("inst_name") or ""),
                currency_id=str(r.get("Currency_ID") or "INR_INR"),
                acquisition_date=as_date(r.get("Acquisition_Date")),
                disposal_date=as_date(r.get("Disposal_Date")),
                fy=str(r.get("FY") or ""),
                quantity_disposed=as_float(r.get("Quantity_Disposed")) or 0.0,
                acquisition_price=as_float(r.get("Acquisition_Price")) or 0.0,
                disposed_cost_basis=as_float(r.get("Disposed_Cost_Basis")) or 0.0,
                sale_price=as_float(r.get("Sale_Price")) or 0.0,
                sale_proceeds=as_float(r.get("Sale_Proceeds")) or 0.0,
                realized_gain_loss=as_float(r.get("Realized_Gain_Loss")) or 0.0,
                holding_type=str(r.get("Holding_Type") or "STCG"),
                tax_type=str(r.get("Tax_Type") or ""),
                tax_subtype=str(r.get("Tax_Subtype") or ""),
                asset_pnl=as_float(r.get("Asset_PnL")),
                forex_pnl=as_float(r.get("Forex_PnL")),
                applied_rate=as_float(r.get("Applied_Rate")),
                estimated_tax=as_float(r.get("Estimated_Tax")) or 0.0,
            )
        )
    return tuple(result), available_fys


def query_reconciliation_events(
    duck_reader: DuckDBQueryPort,
    search: str | None = None,
) -> tuple[tuple[ReconciliationGroupRow, ...], tuple[ReconciliationEventRow, ...]]:
    """Query `silver.fact_investment_reconciliation_events` at event and group levels."""
    if not table_exists(duck_reader, "silver", "fact_investment_reconciliation_events"):
        return (), ()
    has_im = table_exists(duck_reader, "silver", "dim_instrument_master")
    from_sql = "FROM silver.fact_investment_reconciliation_events rec " + (
        'LEFT JOIN silver.dim_instrument_master im ON rec."ISIN" = im."ISIN" ' if has_im else ""
    )
    inst_expr = 'COALESCE(im."INSTRUMENT_NAME", rec."ISIN")' if has_im else 'rec."ISIN"'

    where_sql = "1 = 1"
    params: list[Any] = []
    if search and search.strip():
        pat = f"%{search.strip().lower()}%"
        where_sql = (
            '(LOWER(rec."Reconciliation_Group_ID") LIKE ? OR LOWER(rec."ISIN") LIKE ? '
            "OR LOWER(COALESCE(rec.\"Reason\", '')) LIKE ? "
            f"OR LOWER({inst_expr}) LIKE ?)"
        )
        params.extend([pat, pat, pat, pat])

    rows = duck_reader.fetch_all(
        f"""
        SELECT rec.*, {inst_expr} AS inst_name
        {from_sql}
        WHERE {where_sql}
        ORDER BY rec."Reconciliation_Date" DESC, rec."Reconciliation_Group_ID" ASC;
        """,
        params,
    )
    events: list[ReconciliationEventRow] = []
    grouped: dict[str, list[ReconciliationEventRow]] = {}
    for r in rows:
        ev = ReconciliationEventRow(
            reconciliation_group_id=str(r.get("Reconciliation_Group_ID") or ""),
            reconciliation_event_id=str(r.get("Reconciliation_Event_ID") or ""),
            run_id=str(r.get("Run_ID") or ""),
            isin=str(r.get("ISIN") or ""),
            instrument_name=str(r.get("inst_name") or ""),
            reconciliation_date=as_date(r.get("Reconciliation_Date")),
            lot_id=str(r.get("Lot_ID") or ""),
            purchase_id=str(r.get("Purchase_ID") or ""),
            adjustment_type=str(r.get("Adjustment_Type") or ""),
            reason=str(r.get("Reason") or ""),
            broker_quantity=as_float(r.get("Broker_Quantity")) or 0.0,
            reconstructed_quantity=as_float(r.get("Reconstructed_Quantity")) or 0.0,
            quantity_adjustment=as_float(r.get("Quantity_Adjustment")) or 0.0,
            broker_cost_basis=as_float(r.get("Broker_Cost_Basis")) or 0.0,
            reconstructed_cost_basis=as_float(r.get("Reconstructed_Cost_Basis")) or 0.0,
            cost_basis_adjustment=as_float(r.get("Cost_Basis_Adjustment")) or 0.0,
            original_unit_cost=as_float(r.get("Original_Unit_Cost")),
            adjusted_unit_cost=as_float(r.get("Adjusted_Unit_Cost")),
        )
        events.append(ev)
        grouped.setdefault(ev.reconciliation_group_id, []).append(ev)

    groups: list[ReconciliationGroupRow] = []
    for gid, items in grouped.items():
        first = items[0]
        groups.append(
            ReconciliationGroupRow(
                reconciliation_group_id=gid,
                isin=first.isin,
                instrument_name=first.instrument_name,
                adjustment_type=first.adjustment_type,
                reason=first.reason,
                event_count=len(items),
                broker_quantity=sum(i.broker_quantity for i in items),
                reconstructed_quantity=sum(i.reconstructed_quantity for i in items),
                quantity_adjustment=sum(i.quantity_adjustment for i in items),
                broker_cost_basis=sum(i.broker_cost_basis for i in items),
                reconstructed_cost_basis=sum(i.reconstructed_cost_basis for i in items),
                cost_basis_adjustment=sum(i.cost_basis_adjustment for i in items),
            )
        )
    return tuple(groups), tuple(events)


def build_realized_recon_kpis(
    realized_rows: tuple[RealizedEventRow, ...],
    recon_groups: tuple[ReconciliationGroupRow, ...],
    recon_events: tuple[ReconciliationEventRow, ...],
) -> tuple[OverviewKPI, ...]:
    """Build 9 KPIs for the Realized & Reconciliation sub-tab."""
    proceeds = sum(r.sale_proceeds for r in realized_rows)
    cost = sum(r.disposed_cost_basis for r in realized_rows)
    net_gl = sum(r.realized_gain_loss for r in realized_rows)
    ltcg_gl = sum(r.realized_gain_loss for r in realized_rows if r.holding_type == "LTCG")
    stcg_gl = sum(r.realized_gain_loss for r in realized_rows if r.holding_type == "STCG")
    est_tax = sum(r.estimated_tax for r in realized_rows)
    sale_count = len({r.sale_id for r in realized_rows if r.sale_id})
    net_cost_adj = sum(g.cost_basis_adjustment for g in recon_groups)
    net_qty_adj = sum(g.quantity_adjustment for g in recon_groups)

    return (
        OverviewKPI(
            kpi_id="real_proceeds",
            label="Realized Sale Proceeds",
            value=proceeds,
            format=MetricFormat.CURRENCY,
            subtitle="Gross disposal proceeds across realized events",
            source_table="silver.fact_investment_realized_events",
            drill_route="/investments",
            available=True,
        ),
        OverviewKPI(
            kpi_id="real_cost_basis",
            label="Disposed Cost Basis",
            value=cost,
            format=MetricFormat.CURRENCY,
            subtitle="FIFO acquisition cost of disposed units",
            source_table="silver.fact_investment_realized_events",
            drill_route="/investments",
            available=True,
        ),
        OverviewKPI(
            kpi_id="real_net_pnl",
            label="Net Realized Gain/Loss",
            value=net_gl,
            format=MetricFormat.CURRENCY,
            subtitle="Canonical realized P&L before tax",
            source_table="silver.fact_investment_realized_events",
            drill_route="/investments",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="Asset P&L",
                    value=sum(r.asset_pnl for r in realized_rows if r.asset_pnl is not None),
                ),
                OverviewBreakupItem(
                    label="Forex P&L",
                    value=sum(r.forex_pnl for r in realized_rows if r.forex_pnl is not None),
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="real_ltcg",
            label="LTCG Realized P&L",
            value=ltcg_gl,
            format=MetricFormat.CURRENCY,
            subtitle="Long-term realized capital gains/losses",
            source_table="silver.fact_investment_realized_events",
            drill_route="/investments",
            available=True,
        ),
        OverviewKPI(
            kpi_id="real_stcg",
            label="STCG Realized P&L",
            value=stcg_gl,
            format=MetricFormat.CURRENCY,
            subtitle="Short-term realized capital gains/losses",
            source_table="silver.fact_investment_realized_events",
            drill_route="/investments",
            available=True,
        ),
        OverviewKPI(
            kpi_id="real_est_tax",
            label="Estimated Realized Tax",
            value=est_tax,
            format=MetricFormat.CURRENCY,
            subtitle="Core estimated tax across realized disposals",
            source_table="silver.fact_investment_realized_events",
            drill_route="/investments",
            available=True,
        ),
        OverviewKPI(
            kpi_id="real_events_count",
            label="Realized Events / Sales",
            value=float(len(realized_rows)),
            format=MetricFormat.INTEGER,
            subtitle="FIFO disposal events linked to sales",
            source_table="silver.fact_investment_realized_events",
            drill_route="/investments",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="Unique Sales", value=float(sale_count), format=MetricFormat.INTEGER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="recon_groups",
            label="Reconciliation Groups",
            value=float(len(recon_groups)),
            format=MetricFormat.INTEGER,
            subtitle="Audit groups across broker/reconstructed diffs",
            source_table="silver.fact_investment_reconciliation_events",
            drill_route="/investments",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="Events", value=float(len(recon_events)), format=MetricFormat.INTEGER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="recon_cost_adj",
            label="Net Cost Basis Adj.",
            value=net_cost_adj,
            format=MetricFormat.CURRENCY,
            subtitle="Audit evidence cost basis adjustment total",
            source_table="silver.fact_investment_reconciliation_events",
            drill_route="/investments",
            available=True,
            breakups=(
                OverviewBreakupItem(label="Qty Adj", value=net_qty_adj, format=MetricFormat.NUMBER),
            ),
        ),
    )


def query_trace_lineage(
    duck_reader: DuckDBQueryPort,
    realized_event_id: str,
) -> TradeLineageTrace | None:
    """Trace exact IDs: Realized Event -> Sale -> Lot -> Purchase."""
    rows, _ = query_realized_events(duck_reader, RealizedEventFilter())
    ev = next((r for r in rows if r.realized_event_id == realized_event_id), None)
    if ev is None:
        return None

    missing: list[str] = []
    sale_rec: dict[str, Any] | None = None
    if ev.sale_id and table_exists(duck_reader, "silver", "fact_investment_sales"):
        sale_rec = duck_reader.fetch_one(
            'SELECT * FROM silver.fact_investment_sales WHERE "Sale_ID" = ? LIMIT 1;',
            (ev.sale_id,),
        )
    if sale_rec is None and ev.sale_id:
        missing.append(f"Sale_ID:{ev.sale_id}")

    lot_rec: dict[str, Any] | None = None
    if ev.lot_id and table_exists(duck_reader, "silver", "fact_investment_lot_analytics"):
        lot_rec = duck_reader.fetch_one(
            'SELECT * FROM silver.fact_investment_lot_analytics WHERE "Lot_ID" = ? ORDER BY "Closing_Date" DESC LIMIT 1;',
            (ev.lot_id,),
        )
    if lot_rec is None and ev.lot_id:
        missing.append(f"Lot_ID:{ev.lot_id}")

    purch_rec: dict[str, Any] | None = None
    if ev.purchase_id and table_exists(duck_reader, "silver", "fact_investment_purchases"):
        purch_rec = duck_reader.fetch_one(
            'SELECT * FROM silver.fact_investment_purchases WHERE "Purchase_ID" = ? LIMIT 1;',
            (ev.purchase_id,),
        )
    if purch_rec is None and ev.purchase_id:
        missing.append(f"Purchase_ID:{ev.purchase_id}")

    return TradeLineageTrace(
        realized_event=ev,
        sale_record=dict(sale_rec) if sale_rec else None,
        lot_record=dict(lot_rec) if lot_rec else None,
        purchase_record=dict(purch_rec) if purch_rec else None,
        missing_links=tuple(missing),
    )


def query_market_evidence(
    duck_reader: DuckDBQueryPort,
    *,
    isin: str | None = None,
    benchmark_id: str | None = None,
    currency_id: str | None = None,
    start_date: date,
    end_date: date,
) -> MarketEvidenceSnapshot:
    """Query Market, Benchmark & FX evidence tables strictly within `start_date..end_date`."""
    has_mkt = table_exists(duck_reader, "silver", "fact_investment_market_data")
    has_bm = table_exists(duck_reader, "silver", "fact_investment_benchmark_data")
    has_fx = table_exists(duck_reader, "silver", "fact_currency_fx_rates")
    has_im = table_exists(duck_reader, "silver", "dim_instrument_master")

    avail_isins: tuple[tuple[str, str], ...] = ()
    if has_mkt:
        im_join = (
            'LEFT JOIN silver.dim_instrument_master im ON m."ISIN" = im."ISIN" ' if has_im else ""
        )
        name_expr = 'COALESCE(im."INSTRUMENT_NAME", m."ISIN")' if has_im else 'm."ISIN"'
        i_rows = duck_reader.fetch_all(
            f"""
            SELECT m."ISIN" AS isin, {name_expr} AS nm, MAX(m."Date") AS max_dt, COUNT(*) AS cnt
            FROM silver.fact_investment_market_data m {im_join}
            GROUP BY 1, 2
            ORDER BY max_dt DESC, cnt DESC, 2 ASC
            LIMIT 200;
            """
        )
        avail_isins = tuple((str(r["isin"]), str(r["nm"])) for r in i_rows if r.get("isin"))

    avail_bms: tuple[tuple[str, str], ...] = ()
    if has_bm:
        b_rows = duck_reader.fetch_all(
            'SELECT DISTINCT "ID" AS bid, COALESCE("Benchmark_Name", "ID") AS nm FROM silver.fact_investment_benchmark_data ORDER BY 2;'
        )
        avail_bms = tuple((str(r["bid"]), str(r["nm"])) for r in b_rows if r.get("bid"))

    avail_curs: tuple[str, ...] = ()
    if has_fx:
        c_rows = duck_reader.fetch_all(
            'SELECT DISTINCT "Currency_ID" AS cid FROM silver.fact_currency_fx_rates ORDER BY 1;'
        )
        avail_curs = tuple(str(r["cid"]) for r in c_rows if r.get("cid"))

    sel_isin = (
        isin
        if (isin and any(i[0] == isin for i in avail_isins))
        else (avail_isins[0][0] if avail_isins else "")
    )
    sel_bm = (
        benchmark_id
        if (benchmark_id and any(b[0] == benchmark_id for b in avail_bms))
        else (avail_bms[0][0] if avail_bms else "")
    )
    sel_cur = (
        currency_id
        if (currency_id and currency_id in avail_curs)
        else (avail_curs[0] if avail_curs else "")
    )

    mkt_pts: list[MarketObservationPoint] = []
    if has_mkt and sel_isin:
        m_rows = duck_reader.fetch_all(
            """
            SELECT
                "Date",
                COALESCE(SUM("Quantity"), 0.0) AS qty,
                MAX("Closing_Price") AS close_px,
                COALESCE(SUM("Buy_Value") / NULLIF(SUM("Quantity"), 0.0), MAX("Buy_Price")) AS buy_px,
                COALESCE(SUM("Closing_Value"), 0.0) AS close_val,
                COALESCE(SUM("Buy_Value"), 0.0) AS buy_val,
                MAX("FX_Rate") AS fx_rate,
                MAX("Data_Provider") AS provider,
                MAX("Extraction_Time") AS ext_time,
                BOOL_OR(COALESCE("Is_Closure_Gap", FALSE)) AS is_gap,
                BOOL_OR(COALESCE("Is_Imputed", FALSE)) AS is_imp
            FROM silver.fact_investment_market_data
            WHERE "ISIN" = ? AND "Date" BETWEEN ? AND ?
            GROUP BY "Date"
            ORDER BY "Date" ASC;
            """,
            (sel_isin, start_date, end_date),
        )
        for r in m_rows:
            dt = as_date(r.get("Date"))
            if dt is None:
                continue
            mkt_pts.append(
                MarketObservationPoint(
                    obs_date=dt,
                    isin=sel_isin,
                    quantity=as_float(r.get("qty")) or 0.0,
                    closing_price=as_float(r.get("close_px")) or 0.0,
                    buy_price=as_float(r.get("buy_px")) or 0.0,
                    closing_value=as_float(r.get("close_val")) or 0.0,
                    buy_value=as_float(r.get("buy_val")) or 0.0,
                    fx_rate=as_float(r.get("fx_rate")),
                    data_provider=str(r.get("provider") or ""),
                    extraction_time=str(r.get("ext_time") or ""),
                    is_closure_gap=bool(r.get("is_gap")),
                    is_imputed=bool(r.get("is_imp")),
                )
            )

    bm_pts: list[BenchmarkObservationPoint] = []
    if has_bm and sel_bm:
        b_obs = duck_reader.fetch_all(
            """
            SELECT * FROM silver.fact_investment_benchmark_data
            WHERE "ID" = ? AND "Date" BETWEEN ? AND ?
            ORDER BY "Date" ASC;
            """,
            (sel_bm, start_date, end_date),
        )
        for r in b_obs:
            dt = as_date(r.get("Date"))
            if dt is None:
                continue
            bm_pts.append(
                BenchmarkObservationPoint(
                    obs_date=dt,
                    benchmark_id=sel_bm,
                    benchmark_name=str(r.get("Benchmark_Name") or sel_bm),
                    yf_ticker=str(r.get("yF_Ticker") or ""),
                    close=as_float(r.get("Close")) or 0.0,
                    data_provider=str(r.get("Data_Provider") or ""),
                    extraction_time=str(r.get("Extraction_Time") or ""),
                    is_closure_gap=bool(r.get("Is_Closure_Gap")),
                    is_imputed=bool(r.get("Is_Imputed")),
                )
            )

    fx_pts: list[FxObservationPoint] = []
    if has_fx and sel_cur:
        f_obs = duck_reader.fetch_all(
            """
            SELECT * FROM silver.fact_currency_fx_rates
            WHERE "Currency_ID" = ? AND "Date" BETWEEN ? AND ?
            ORDER BY "Date" ASC;
            """,
            (sel_cur, start_date, end_date),
        )
        for r in f_obs:
            dt = as_date(r.get("Date"))
            if dt is None:
                continue
            fx_pts.append(
                FxObservationPoint(
                    obs_date=dt,
                    currency_id=sel_cur,
                    currency_code=str(r.get("Currency_Code") or ""),
                    target_currency_code=str(r.get("Target_Currency_Code") or ""),
                    yf_ticker=str(r.get("yF_Ticker") or ""),
                    fx_rate=as_float(r.get("FX_Rate")) or 1.0,
                    data_provider=str(r.get("Data_Provider") or ""),
                    extraction_time=str(r.get("Extraction_Time") or ""),
                    is_closure_gap=bool(r.get("Is_Closure_Gap")),
                    is_imputed=bool(r.get("Is_Imputed")),
                )
            )

    latest_mkt = mkt_pts[-1] if mkt_pts else None
    latest_bm = bm_pts[-1] if bm_pts else None
    latest_fx = fx_pts[-1] if fx_pts else None
    imputed_cnt = (
        sum(1 for p in mkt_pts if p.is_imputed)
        + sum(1 for p in bm_pts if p.is_imputed)
        + sum(1 for p in fx_pts if p.is_imputed)
    )
    gap_cnt = (
        sum(1 for p in mkt_pts if p.is_closure_gap)
        + sum(1 for p in bm_pts if p.is_closure_gap)
        + sum(1 for p in fx_pts if p.is_closure_gap)
    )

    total_obs = len(mkt_pts) + len(bm_pts) + len(fx_pts)
    imp_pct = (imputed_cnt / total_obs) if total_obs > 0 else None
    gap_pct = (gap_cnt / total_obs) if total_obs > 0 else None

    kpis = (
        OverviewKPI(
            kpi_id="ev_latest_price",
            label="Latest Closing Price",
            value=latest_mkt.closing_price if latest_mkt else None,
            format=MetricFormat.CURRENCY,
            subtitle=f"Provider: {latest_mkt.data_provider or '—'}"
            if latest_mkt
            else "No observations",
            source_table="silver.fact_investment_market_data",
            as_of_date=latest_mkt.obs_date if latest_mkt else None,
            drill_route="/investments",
            available=latest_mkt is not None,
            breakups=(
                OverviewBreakupItem(
                    label="Avg Buy", value=latest_mkt.buy_price if latest_mkt else None
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="ev_latest_bm",
            label="Latest Benchmark Close",
            value=latest_bm.close if latest_bm else None,
            format=MetricFormat.NUMBER,
            subtitle=f"Ticker: {latest_bm.yf_ticker or '—'}" if latest_bm else "No observations",
            source_table="silver.fact_investment_benchmark_data",
            as_of_date=latest_bm.obs_date if latest_bm else None,
            drill_route="/investments",
            available=latest_bm is not None,
            breakups=(
                OverviewBreakupItem(
                    label="Obs", value=float(len(bm_pts)), format=MetricFormat.INTEGER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="ev_latest_fx",
            label="Latest FX Rate",
            value=latest_fx.fx_rate if latest_fx else None,
            format=MetricFormat.NUMBER,
            subtitle=f"Pair: {sel_cur or '—'}",
            source_table="silver.fact_currency_fx_rates",
            as_of_date=latest_fx.obs_date if latest_fx else None,
            drill_route="/investments",
            available=latest_fx is not None,
            breakups=(
                OverviewBreakupItem(
                    label="Obs", value=float(len(fx_pts)), format=MetricFormat.INTEGER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="ev_obs_count",
            label="Total Observations",
            value=float(total_obs),
            format=MetricFormat.INTEGER,
            subtitle="Market + Benchmark + FX data points",
            source_table="silver.fact_investment_market_data",
            drill_route="/investments",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="Mkt", value=float(len(mkt_pts)), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="BM", value=float(len(bm_pts)), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="FX", value=float(len(fx_pts)), format=MetricFormat.INTEGER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="ev_imputed_count",
            label="Imputed Points",
            value=float(imputed_cnt),
            format=MetricFormat.INTEGER,
            delta_value=imp_pct,
            delta_format=MetricFormat.PERCENT,
            delta_label="Share",
            subtitle="Observations flagged Is_Imputed = True",
            source_table="silver.fact_investment_market_data",
            drill_route="/investments",
            available=True,
        ),
        OverviewKPI(
            kpi_id="ev_gap_count",
            label="Closure Gap Points",
            value=float(gap_cnt),
            format=MetricFormat.INTEGER,
            delta_value=gap_pct,
            delta_format=MetricFormat.PERCENT,
            delta_label="Share",
            subtitle="Observations flagged Is_Closure_Gap = True",
            source_table="silver.fact_investment_market_data",
            drill_route="/investments",
            available=True,
        ),
    )

    return MarketEvidenceSnapshot(
        state="ready" if (mkt_pts or bm_pts or fx_pts) else "empty",
        selected_isin=sel_isin,
        selected_benchmark_id=sel_bm,
        selected_currency_id=sel_cur,
        available_isins=avail_isins,
        available_benchmarks=avail_bms,
        available_currencies=avail_curs,
        kpis=kpis,
        market_points=tuple(mkt_pts),
        benchmark_points=tuple(bm_pts),
        fx_points=tuple(fx_pts),
    )
