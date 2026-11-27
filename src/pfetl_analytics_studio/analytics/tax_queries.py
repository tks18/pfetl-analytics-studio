"""DuckDB SQL queries for Tax Analytics."""

from __future__ import annotations

from typing import Any

from pfetl_analytics_studio.analytics.overview_models import (
    OverviewBreakupItem,
    OverviewKPI,
    as_date,
    as_float,
)
from pfetl_analytics_studio.analytics.overview_queries import table_exists
from pfetl_analytics_studio.analytics.tax_models import (
    TaxEventFilter,
    TaxEventRow,
    TaxFYParameters,
    TaxIncomeBreakdownRow,
    TaxReconciliationRow,
)
from pfetl_analytics_studio.data import DuckDBQueryPort
from pfetl_analytics_studio.semantic import MetricFormat


def query_available_tax_fys(duck_reader: DuckDBQueryPort) -> tuple[str, ...]:
    """Return available FY strings ordered descending from Gold/Silver tax tables."""
    if table_exists(duck_reader, "gold", "tax_financial_year_summary"):
        rows = duck_reader.fetch_all(
            'SELECT DISTINCT "FY" AS fy FROM gold.tax_financial_year_summary WHERE "FY" IS NOT NULL ORDER BY "FY" DESC;'
        )
        if rows:
            return tuple(str(r["fy"]) for r in rows if r.get("fy"))
    if table_exists(duck_reader, "silver", "ref_financial_year_parameters"):
        rows = duck_reader.fetch_all(
            'SELECT DISTINCT "FY" AS fy FROM silver.ref_financial_year_parameters WHERE "FY" IS NOT NULL ORDER BY "FY" DESC;'
        )
        return tuple(str(r["fy"]) for r in rows if r.get("fy"))
    return ()


def query_fy_parameters(duck_reader: DuckDBQueryPort, fy: str) -> TaxFYParameters | None:
    """Fetch `silver.ref_financial_year_parameters` row for `fy`, or None if missing."""
    if not fy or not table_exists(duck_reader, "silver", "ref_financial_year_parameters"):
        return None
    r = duck_reader.fetch_one(
        'SELECT * FROM silver.ref_financial_year_parameters WHERE "FY" = ? LIMIT 1;',
        (fy,),
    )
    if r is None:
        return None
    return TaxFYParameters(
        fy=str(r.get("FY") or fy),
        fy_start_date=as_date(r.get("FY_Start_Date")),
        fy_end_date=as_date(r.get("FY_End_Date")),
        debt_mf_cutoff_date=as_date(r.get("Debt_MF_Cutoff_Date")),
        equity_listed_ltcg=as_float(r.get("Equity_Listed_LTCG")),
        equity_listed_stcg=as_float(r.get("Equity_Listed_STCG")),
        equity_ltcg_exemption=int(r["Equity_LTCG_Exemption"])
        if r.get("Equity_LTCG_Exemption") is not None
        else None,
        gold_ltcg=as_float(r.get("Gold_LTCG")),
        gold_stcg=as_float(r.get("Gold_STCG")),
        debt_mf_post_cutoff_ltcg=as_float(r.get("Debt_MF_Post_Cutoff_LTCG")),
        debt_mf_post_cutoff_stcg=as_float(r.get("Debt_MF_Post_Cutoff_STCG")),
        estimated_ordinary_income_tax_rate=as_float(r.get("Estimated_Ordinary_Income_Tax_Rate")),
        remarks=str(r.get("Remarks") or ""),
    )


def query_all_fy_summaries(duck_reader: DuckDBQueryPort) -> tuple[dict[str, Any], ...]:
    """Fetch all `gold.tax_financial_year_summary` rows ordered by FY ASC."""
    if not table_exists(duck_reader, "gold", "tax_financial_year_summary"):
        return ()
    rows = duck_reader.fetch_all('SELECT * FROM gold.tax_financial_year_summary ORDER BY "FY" ASC;')
    return tuple(dict(r) for r in rows)


def build_tax_kpis(row: dict[str, Any]) -> tuple[OverviewKPI, ...]:
    """Build 9 KPIs from one `gold.tax_financial_year_summary` row."""
    fy = str(row.get("FY") or "")
    ledger_inc = as_float(row.get("Total_Ledger_Income")) or 0.0
    inv_gl = as_float(row.get("Total_Investment_Gain_Loss")) or 0.0
    tax_credits = as_float(row.get("Total_Tax_Credits")) or 0.0
    est_inv_tax = as_float(row.get("Total_Estimated_Investment_Tax")) or 0.0
    inv_cnt = int(row.get("Investment_Tax_Estimate_Count") or 0)
    inv_miss = int(row.get("Investment_Tax_Estimate_Missing_Count") or 0)
    inv_comp = bool(row.get("Investment_Tax_Estimate_Complete"))
    est_led_tax = as_float(row.get("Total_Estimated_Ledger_Tax")) or 0.0
    led_cnt = int(row.get("Ledger_Tax_Estimate_Count") or 0)
    led_miss = int(row.get("Ledger_Tax_Estimate_Missing_Count") or 0)
    led_comp = bool(row.get("Ledger_Tax_Estimate_Complete"))
    total_ev = int(row.get("Total_Event_Count") or 0)
    ready_cnt = int(row.get("Ready_Count") or 0)
    check_cnt = int(row.get("Check_Required_Count") or 0)
    unr_tax = as_float(row.get("Indicative_Unrealized_Tax_Liability")) or 0.0
    harv_sav = as_float(row.get("Indicative_Harvestable_Tax_Savings")) or 0.0
    net_exp = as_float(row.get("Indicative_Net_Tax_Exposure")) or 0.0

    return (
        OverviewKPI(
            kpi_id="tax_ledger_income",
            label="Total Ledger Income",
            value=ledger_inc,
            format=MetricFormat.CURRENCY,
            subtitle=f"Taxable ledger inflows for FY {fy}",
            source_table="gold.tax_financial_year_summary",
            drill_route="/tax",
            available=True,
        ),
        OverviewKPI(
            kpi_id="tax_inv_gain_loss",
            label="Investment Realized Gain/Loss",
            value=inv_gl,
            format=MetricFormat.CURRENCY,
            subtitle=f"Canonical FIFO realized P&L for FY {fy}",
            source_table="gold.tax_financial_year_summary",
            drill_route="/tax",
            available=True,
        ),
        OverviewKPI(
            kpi_id="tax_est_ledger",
            label="Est. Ledger Tax",
            value=est_led_tax,
            format=MetricFormat.CURRENCY,
            subtitle="Complete" if led_comp else f"Incomplete ({led_miss} missing)",
            source_table="gold.tax_financial_year_summary",
            drill_route="/tax",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="Estimates", value=float(led_cnt), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="Missing", value=float(led_miss), format=MetricFormat.INTEGER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="tax_est_investment",
            label="Est. Investment Tax",
            value=est_inv_tax,
            format=MetricFormat.CURRENCY,
            subtitle="Complete" if inv_comp else f"Incomplete ({inv_miss} missing)",
            source_table="gold.tax_financial_year_summary",
            drill_route="/tax",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="Estimates", value=float(inv_cnt), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="Missing", value=float(inv_miss), format=MetricFormat.INTEGER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="tax_credits",
            label="Total Tax Credits (TDS)",
            value=tax_credits,
            format=MetricFormat.CURRENCY,
            subtitle="Recorded tax deductions & credits",
            source_table="gold.tax_financial_year_summary",
            drill_route="/tax",
            available=True,
        ),
        OverviewKPI(
            kpi_id="tax_net_exposure",
            label="Indicative Net Exposure",
            value=net_exp,
            format=MetricFormat.CURRENCY,
            subtitle="Indicative total tax net of credits & savings",
            source_table="gold.tax_financial_year_summary",
            drill_route="/tax",
            available=True,
        ),
        OverviewKPI(
            kpi_id="tax_unrealized_liability",
            label="Unrealized Tax-If-Sold",
            value=unr_tax,
            format=MetricFormat.CURRENCY,
            subtitle="Indicative tax if open holdings liquidated",
            source_table="gold.tax_financial_year_summary",
            drill_route="/tax",
            available=True,
        ),
        OverviewKPI(
            kpi_id="tax_harvest_savings",
            label="Harvestable Tax Savings",
            value=harv_sav,
            format=MetricFormat.CURRENCY,
            subtitle="Indicative tax offset from loss harvesting",
            source_table="gold.tax_financial_year_summary",
            drill_route="/tax",
            available=True,
        ),
        OverviewKPI(
            kpi_id="tax_event_readiness",
            label="Tax Events Readiness",
            value=float(total_ev),
            format=MetricFormat.INTEGER,
            subtitle="Ready vs Check Required tax events",
            source_table="gold.tax_financial_year_summary",
            drill_route="/tax",
            available=True,
            breakups=(
                OverviewBreakupItem(
                    label="Ready", value=float(ready_cnt), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="Check Req", value=float(check_cnt), format=MetricFormat.INTEGER
                ),
            ),
        ),
    )


def query_tax_income_breakdown(
    duck_reader: DuckDBQueryPort,
    fy: str,
    *,
    source_type: str | None = None,
    income_head: str | None = None,
) -> tuple[tuple[TaxIncomeBreakdownRow, ...], tuple[str, ...], tuple[str, ...]]:
    """Query `gold.tax_income_breakdown` for `fy`."""
    if not table_exists(duck_reader, "gold", "tax_income_breakdown"):
        return (), (), ()
    all_rows = duck_reader.fetch_all(
        'SELECT * FROM gold.tax_income_breakdown WHERE "FY" = ? ORDER BY ABS(COALESCE("Event_Amount_INR", 0.0)) DESC;',
        (fy,),
    )
    src_types = tuple(sorted({str(r["Source_Type"]) for r in all_rows if r.get("Source_Type")}))
    heads = tuple(sorted({str(r["Income_Head"]) for r in all_rows if r.get("Income_Head")}))
    filtered: list[TaxIncomeBreakdownRow] = []
    for r in all_rows:
        st = str(r.get("Source_Type") or "")
        ih = str(r.get("Income_Head") or "")
        if source_type and st != source_type:
            continue
        if income_head and ih != income_head:
            continue
        filtered.append(
            TaxIncomeBreakdownRow(
                fy=str(r.get("FY") or fy),
                income_head=ih,
                tax_sub_head=str(r.get("Tax_Sub_Head") or ""),
                source_type=st,
                event_amount_inr=as_float(r.get("Event_Amount_INR")) or 0.0,
                estimated_tax_inr=as_float(r.get("Estimated_Tax_INR")) or 0.0,
                estimated_tax_event_count=int(r.get("Estimated_Tax_Event_Count") or 0),
                estimated_tax_missing_count=int(r.get("Estimated_Tax_Missing_Count") or 0),
                estimated_tax_complete=bool(r.get("Estimated_Tax_Complete")),
                event_count=int(r.get("Event_Count") or 0),
                ready_count=int(r.get("Ready_Count") or 0),
                check_required_count=int(r.get("Check_Required_Count") or 0),
            )
        )
    return tuple(filtered), src_types, heads


def query_tax_reconciliation(
    duck_reader: DuckDBQueryPort,
    fy: str,
) -> tuple[TaxReconciliationRow, ...]:
    """Query `gold.tax_reconciliation` for `fy`."""
    if not table_exists(duck_reader, "gold", "tax_reconciliation"):
        return ()
    rows = duck_reader.fetch_all(
        'SELECT * FROM gold.tax_reconciliation WHERE "FY" = ? ORDER BY ABS(COALESCE("Tax_Event_Amount", 0.0)) DESC;',
        (fy,),
    )
    return tuple(
        TaxReconciliationRow(
            fy=str(r.get("FY") or fy),
            tax_sub_head=str(r.get("Tax_Sub_Head") or ""),
            ledger_tax_event_amount=as_float(r.get("Ledger_Tax_Event_Amount")) or 0.0,
            excluded_non_taxable_amount=as_float(r.get("Excluded_Non_Taxable_Amount")) or 0.0,
            excluded_non_taxable_event_count=int(r.get("Excluded_Non_Taxable_Event_Count") or 0),
            excluded_investment_ledger_amount=as_float(r.get("Excluded_Investment_Ledger_Amount"))
            or 0.0,
            excluded_investment_ledger_event_count=int(
                r.get("Excluded_Investment_Ledger_Event_Count") or 0
            ),
            quant_realized_gain_loss=as_float(r.get("Quant_Realized_Gain_Loss")) or 0.0,
            estimated_investment_tax=as_float(r.get("Estimated_Investment_Tax")) or 0.0,
            estimated_investment_tax_complete=bool(r.get("Estimated_Investment_Tax_Complete")),
            estimated_ledger_tax=as_float(r.get("Estimated_Ledger_Tax")) or 0.0,
            estimated_ledger_tax_complete=bool(r.get("Estimated_Ledger_Tax_Complete")),
            tax_credit_amount=as_float(r.get("Tax_Credit_Amount")) or 0.0,
            tax_event_amount=as_float(r.get("Tax_Event_Amount")) or 0.0,
            investment_source_difference_inr=as_float(r.get("Investment_Source_Difference_INR"))
            or 0.0,
            ledger_event_count=int(r.get("Ledger_Event_Count") or 0),
            quant_event_count=int(r.get("Quant_Event_Count") or 0),
            tax_credit_event_count=int(r.get("Tax_Credit_Event_Count") or 0),
            check_required_count=int(r.get("Check_Required_Count") or 0),
        )
        for r in rows
    )


def query_tax_events(
    duck_reader: DuckDBQueryPort,
    fy: str,
    filter_spec: TaxEventFilter,
) -> tuple[TaxEventRow, ...]:
    """Query `silver.fact_tax_events` for `fy` with optional filters."""
    if not table_exists(duck_reader, "silver", "fact_tax_events"):
        return ()
    clauses: list[str] = ['"FY" = ?']
    params: list[Any] = [fy]
    if filter_spec.source_type:
        clauses.append('"Source_Type" = ?')
        params.append(filter_spec.source_type)
    if filter_spec.income_head:
        clauses.append('"Income_Head" = ?')
        params.append(filter_spec.income_head)
    if filter_spec.tax_sub_head:
        clauses.append('"Tax_Sub_Head" = ?')
        params.append(filter_spec.tax_sub_head)
    if filter_spec.tax_status:
        clauses.append('"Tax_Status" = ?')
        params.append(filter_spec.tax_status)
    if filter_spec.search and filter_spec.search.strip():
        pat = f"%{filter_spec.search.strip().lower()}%"
        clauses.append(
            '(LOWER("Tax_Event_ID") LIKE ? OR LOWER("Source_ID") LIKE ? '
            'OR LOWER("Tax_Sub_Head") LIKE ? OR LOWER(COALESCE("ISIN", \'\')) LIKE ?)'
        )
        params.extend([pat, pat, pat, pat])

    rows = duck_reader.fetch_all(
        f"""
        SELECT * FROM silver.fact_tax_events
        WHERE {" AND ".join(clauses)}
        ORDER BY "Event_Date" DESC, "Tax_Event_ID" ASC;
        """,
        params,
    )
    return tuple(
        TaxEventRow(
            tax_event_id=str(r.get("Tax_Event_ID") or ""),
            fy=str(r.get("FY") or fy),
            event_date=as_date(r.get("Event_Date")),
            source_type=str(r.get("Source_Type") or ""),
            source_id=str(r.get("Source_ID") or ""),
            income_head=str(r.get("Income_Head") or ""),
            tax_sub_head=str(r.get("Tax_Sub_Head") or ""),
            amount_inr=as_float(r.get("Amount_INR")) or 0.0,
            applied_rate=as_float(r.get("Applied_Rate")),
            estimated_tax=as_float(r.get("Estimated_Tax")),
            currency_id=str(r.get("CURRENCY_ID") or "INR_INR"),
            tax_status=str(r.get("Tax_Status") or ""),
            tax_status_reason=str(r.get("Tax_Status_Reason") or ""),
            isin=str(r.get("ISIN") or ""),
            lot_id=str(r.get("Lot_ID") or ""),
        )
        for r in rows
    )
