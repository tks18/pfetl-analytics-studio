"""Plotly chart builders for the Tax Analytics workspace."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pfetl_analytics_studio.ui.charts import (
    currency_ticks,
    numeric_ticks,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.formatting import format_currency

_TAX_PALETTE = ["#38BDF8", "#A78BFA", "#34D399", "#FBBF24", "#F472B6", "#60A5FA"]


def build_tax_fy_trend_figure(
    all_fy_rows: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build multi-FY grouped bars of Ledger Income, Realized Gain/Loss, Est. Tax & Credits."""
    if not all_fy_rows:
        return None
    fys = [str(r.get("FY", "")) for r in all_fy_rows]
    led_inc = [float(r.get("Total_Ledger_Income") or 0.0) for r in all_fy_rows]
    inv_gl = [float(r.get("Total_Investment_Gain_Loss") or 0.0) for r in all_fy_rows]
    est_tax = [
        float(r.get("Total_Estimated_Ledger_Tax") or 0.0)
        + float(r.get("Total_Estimated_Investment_Tax") or 0.0)
        for r in all_fy_rows
    ]
    tax_credits = [float(r.get("Total_Tax_Credits") or 0.0) for r in all_fy_rows]
    y_ticks = currency_ticks(
        [*led_inc, *inv_gl, *est_tax, *tax_credits], currency_id, include_zero=True
    )
    layout = studio_plotly_layout(
        height=340,
        barmode="group",
        y_ticks=y_ticks,
        x_axis_type="category",
        x_categories=fys,
    )
    return {
        "data": [
            {
                "type": "bar",
                "x": fys,
                "y": led_inc,
                "name": "Ledger Income",
                "marker": {"color": "#38BDF8"},
            },
            {
                "type": "bar",
                "x": fys,
                "y": inv_gl,
                "name": "Inv. Gain/Loss",
                "marker": {"color": "#34D399"},
            },
            {
                "type": "bar",
                "x": fys,
                "y": est_tax,
                "name": "Est. Total Tax",
                "marker": {"color": "#F87171"},
            },
            {
                "type": "bar",
                "x": fys,
                "y": tax_credits,
                "name": "Tax Credits",
                "marker": {"color": "#FBBF24"},
            },
        ],
        "layout": layout,
    }


def build_tax_composition_donut_figure(
    summary: Any,
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Donut of Est. Ledger Tax vs Est. Investment Tax vs Tax Credits."""
    led = max(0.0, float(getattr(summary, "total_estimated_ledger_tax", 0.0) or 0.0))
    inv = max(0.0, float(getattr(summary, "total_estimated_investment_tax", 0.0) or 0.0))
    cred = max(0.0, float(getattr(summary, "total_tax_credits", 0.0) or 0.0))
    if led + inv + cred <= 0:
        return None
    labels = ["Est. Ledger Tax", "Est. Investment Tax", "Tax Credits (TDS)"]
    vals = [led, inv, cred]
    layout = studio_plotly_layout(height=340)
    return {
        "data": [
            {
                "type": "pie",
                "labels": labels,
                "values": vals,
                "hole": 0.55,
                "marker": {"colors": ["#38BDF8", "#A78BFA", "#34D399"]},
                "hovertemplate": "%{label}<br>Amount: %{customdata} (%{percent})<extra></extra>",
                "customdata": [format_currency(v, currency_id) for v in vals],
            }
        ],
        "layout": layout,
    }


def build_tax_indicative_exposure_figure(
    summary: Any,
    currency_id: str,
) -> dict[str, Any] | None:
    """Build bar comparison of Unrealized Tax-If-Sold, Harvest Savings & Net Exposure."""
    unr = float(getattr(summary, "indicative_unrealized_tax_liability", 0.0) or 0.0)
    sav = float(getattr(summary, "indicative_harvestable_tax_savings", 0.0) or 0.0)
    net = float(getattr(summary, "indicative_net_tax_exposure", 0.0) or 0.0)
    if unr == 0 and sav == 0 and net == 0:
        return None
    labels = ["Unrealized Tax-If-Sold", "Harvest Tax Savings", "Indicative Net Exposure"]
    vals = [unr, sav, net]
    y_ticks = currency_ticks(vals, currency_id, include_zero=True)
    layout = studio_plotly_layout(height=340, y_ticks=y_ticks, x_axis_type="category")
    layout["showlegend"] = False
    return {
        "data": [
            {
                "type": "bar",
                "x": labels,
                "y": vals,
                "marker": {"color": ["#F87171", "#34D399", "#FBBF24"]},
                "hovertemplate": "%{x}<br>%{customdata}<extra></extra>",
                "customdata": [format_currency(v, currency_id) for v in vals],
            }
        ],
        "layout": layout,
    }


def build_tax_head_breakdown_figure(
    rows: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build horizontal grouped bars of Event Amount vs Estimated Tax by Tax Sub-Head."""
    top = list(rows[:10])
    if not top:
        return None
    top.reverse()
    labels = [
        f"{getattr(r, 'tax_sub_head', '')} ({getattr(r, 'source_type', '')})"[:32] for r in top
    ]
    amt = [float(getattr(r, "event_amount_inr", 0.0) or 0.0) for r in top]
    tax = [float(getattr(r, "estimated_tax_inr", 0.0) or 0.0) for r in top]
    x_ticks = currency_ticks([*amt, *tax], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340, barmode="group", horizontal_bars=True, x_currency_ticks=x_ticks
    )
    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": amt,
                "name": "Event Amount",
                "orientation": "h",
                "marker": {"color": "#38BDF8"},
            },
            {
                "type": "bar",
                "y": labels,
                "x": tax,
                "name": "Estimated Tax",
                "orientation": "h",
                "marker": {"color": "#F87171"},
            },
        ],
        "layout": layout,
    }


def build_tax_source_type_figure(
    rows: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build bar chart of Event Amount & Estimated Tax grouped by Source_Type."""
    if not rows:
        return None
    by_src: dict[str, tuple[float, float]] = {}
    for r in rows:
        st = str(getattr(r, "source_type", "") or "OTHER")
        prev_a, prev_t = by_src.get(st, (0.0, 0.0))
        by_src[st] = (
            prev_a + float(getattr(r, "event_amount_inr", 0.0) or 0.0),
            prev_t + float(getattr(r, "estimated_tax_inr", 0.0) or 0.0),
        )
    labels = list(by_src.keys())
    amts = [by_src[k][0] for k in labels]
    taxes = [by_src[k][1] for k in labels]
    y_ticks = currency_ticks([*amts, *taxes], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340,
        barmode="group",
        y_ticks=y_ticks,
        x_axis_type="category",
        x_categories=labels,
    )
    return {
        "data": [
            {
                "type": "bar",
                "x": labels,
                "y": amts,
                "name": "Event Amount",
                "marker": {"color": "#A78BFA"},
            },
            {
                "type": "bar",
                "x": labels,
                "y": taxes,
                "name": "Estimated Tax",
                "marker": {"color": "#FBBF24"},
            },
        ],
        "layout": layout,
    }


def build_tax_recon_bridge_figure(
    rows: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build grouped horizontal bars comparing Ledger Tax Event vs Quant Realized Gain/Loss."""
    top = list(rows[:10])
    if not top:
        return None
    top.reverse()
    labels = [str(getattr(r, "tax_sub_head", ""))[:28] for r in top]
    led = [float(getattr(r, "ledger_tax_event_amount", 0.0) or 0.0) for r in top]
    qnt = [float(getattr(r, "quant_realized_gain_loss", 0.0) or 0.0) for r in top]
    diff = [float(getattr(r, "investment_source_difference_inr", 0.0) or 0.0) for r in top]
    x_ticks = currency_ticks([*led, *qnt, *diff], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340, barmode="group", horizontal_bars=True, x_currency_ticks=x_ticks
    )
    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": led,
                "name": "Ledger Amount",
                "orientation": "h",
                "marker": {"color": "#38BDF8"},
            },
            {
                "type": "bar",
                "y": labels,
                "x": qnt,
                "name": "Quant Realized P&L",
                "orientation": "h",
                "marker": {"color": "#34D399"},
            },
            {
                "type": "bar",
                "y": labels,
                "x": diff,
                "name": "Source Diff",
                "orientation": "h",
                "marker": {"color": "#F87171"},
            },
        ],
        "layout": layout,
    }


def build_tax_completeness_figure(
    rows: Sequence[Any],
) -> dict[str, Any] | None:
    """Build horizontal stacked bar of Ready vs Check Required event counts across Tax Sub-Heads."""
    ordered = sorted(
        rows,
        key=lambda r: (
            float(getattr(r, "ready_count", 0) or 0)
            + float(getattr(r, "check_required_count", 0) or 0)
        ),
        reverse=True,
    )[:10]
    if not ordered:
        return None
    ordered.reverse()
    labels = [
        (
            f"{str(getattr(r, 'tax_sub_head', ''))[:26]}…"
            if len(str(getattr(r, "tax_sub_head", ""))) > 26
            else str(getattr(r, "tax_sub_head", ""))
        )
        for r in ordered
    ]
    ready = [float(getattr(r, "ready_count", 0) or 0) for r in ordered]
    check = [float(getattr(r, "check_required_count", 0) or 0) for r in ordered]
    x_ticks = numeric_ticks([0.0, *(r + c for r, c in zip(ready, check, strict=True))], suffix="")
    layout = studio_plotly_layout(
        height=340,
        barmode="stack",
        horizontal_bars=True,
        x_currency_ticks=x_ticks,
    )
    return {
        "data": [
            {
                "type": "bar",
                "orientation": "h",
                "y": labels,
                "x": ready,
                "name": "Ready",
                "marker": {"color": "#34D399"},
            },
            {
                "type": "bar",
                "orientation": "h",
                "y": labels,
                "x": check,
                "name": "Check Required",
                "marker": {"color": "#FBBF24"},
            },
        ],
        "layout": layout,
    }


def build_tax_events_timeline_figure(
    events: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build monthly bar chart of Event Amount & Estimated Tax across `silver.fact_tax_events`."""
    if not events:
        return None
    by_ym: dict[str, tuple[float, float]] = {}
    for ev in events:
        dt = getattr(ev, "event_date", None)
        ym = dt.strftime("%Y-%m") if dt else "Unknown"
        pa, pt = by_ym.get(ym, (0.0, 0.0))
        by_ym[ym] = (
            pa + float(getattr(ev, "amount_inr", 0.0) or 0.0),
            pt + float(getattr(ev, "estimated_tax", 0.0) or 0.0),
        )
    yms = sorted(by_ym.keys())
    amts = [by_ym[k][0] for k in yms]
    taxes = [by_ym[k][1] for k in yms]
    y_ticks = currency_ticks([*amts, *taxes], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340,
        barmode="group",
        y_ticks=y_ticks,
        x_axis_type="category",
        x_categories=yms,
    )
    return {
        "data": [
            {
                "type": "bar",
                "x": yms,
                "y": amts,
                "name": "Event Amount",
                "marker": {"color": "#38BDF8"},
            },
            {
                "type": "bar",
                "x": yms,
                "y": taxes,
                "name": "Estimated Tax",
                "marker": {"color": "#F87171"},
            },
        ],
        "layout": layout,
    }


def build_tax_fy_readiness_figure(
    all_fy_rows: Sequence[Any],
) -> dict[str, Any] | None:
    """Build stacked bar of Ready vs Check Required tax events across all FYs."""
    if not all_fy_rows:
        return None
    fys = [str(r.get("FY", "")) for r in all_fy_rows]
    ready = [float(r.get("Ready_Count") or 0) for r in all_fy_rows]
    check = [float(r.get("Check_Required_Count") or 0) for r in all_fy_rows]
    y_ticks = numeric_ticks([r + c for r, c in zip(ready, check, strict=True)], suffix="")
    layout = studio_plotly_layout(
        height=340, barmode="stack", y_ticks=y_ticks, x_axis_type="category", x_categories=fys
    )
    return {
        "data": [
            {
                "type": "bar",
                "x": fys,
                "y": ready,
                "name": "Ready Events",
                "marker": {"color": "#34D399"},
            },
            {
                "type": "bar",
                "x": fys,
                "y": check,
                "name": "Check Required",
                "marker": {"color": "#FBBF24"},
            },
        ],
        "layout": layout,
    }


def build_tax_events_status_source_figure(
    events: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build stacked bar of Ready vs Check Required amounts grouped by Source Type."""
    if not events:
        return None
    by_src: dict[str, tuple[float, float]] = {}
    for ev in events:
        src = str(getattr(ev, "source_type", "") or "Unknown")
        amt = abs(float(getattr(ev, "amount_inr", 0.0) or 0.0))
        st = str(getattr(ev, "tax_status", "")).upper()
        r_amt, c_amt = by_src.get(src, (0.0, 0.0))
        if "READY" in st:
            by_src[src] = (r_amt + amt, c_amt)
        else:
            by_src[src] = (r_amt, c_amt + amt)
    srcs = sorted(by_src.keys())
    ready_vals = [by_src[s][0] for s in srcs]
    check_vals = [by_src[s][1] for s in srcs]
    y_ticks = currency_ticks(
        [r + c for r, c in zip(ready_vals, check_vals, strict=True)],
        currency_id,
        include_zero=True,
    )
    layout = studio_plotly_layout(
        height=340, barmode="stack", y_ticks=y_ticks, x_axis_type="category", x_categories=srcs
    )
    return {
        "data": [
            {
                "type": "bar",
                "x": srcs,
                "y": ready_vals,
                "name": "Ready",
                "marker": {"color": "#34D399"},
            },
            {
                "type": "bar",
                "x": srcs,
                "y": check_vals,
                "name": "Check Required / Other",
                "marker": {"color": "#FBBF24"},
            },
        ],
        "layout": layout,
    }


def build_tax_harvesting_exposure_figure(
    harvest_lots: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build horizontal bar comparing Unrealized Loss vs Est. Tax Savings across top harvest lots."""
    top = sorted(
        harvest_lots,
        key=lambda r: float(getattr(r, "unrealized_pl", 0.0) or 0.0),
    )[:10]
    if not top:
        return None
    ordered = list(reversed(top))
    labels = [
        f"{str(getattr(r, 'instrument_name', '') or getattr(r, 'isin', ''))[:20]} ({str(getattr(r, 'lot_id', ''))[-5:]})"
        for r in ordered
    ]
    losses = [abs(float(getattr(r, "unrealized_pl", 0.0) or 0.0)) for r in ordered]
    savings = [abs(float(getattr(r, "tax_if_sold", 0.0) or 0.0)) for r in ordered]
    x_ticks = currency_ticks([0.0, *losses, *savings], currency_id, include_zero=True)
    layout = {
        **studio_plotly_layout(
            height=360,
            barmode="group",
            horizontal_bars=True,
            x_currency_ticks=x_ticks,
            x_axis_type="linear",
        ),
        "margin": {"l": 195, "r": 24, "t": 34, "b": 42},
    }
    return {
        "data": [
            {
                "type": "bar",
                "orientation": "h",
                "y": labels,
                "x": losses,
                "name": "Unrealized Loss",
                "marker": {"color": "#F87171"},
            },
            {
                "type": "bar",
                "orientation": "h",
                "y": labels,
                "x": savings,
                "name": "Est. Tax Impact",
                "marker": {"color": "#34D399"},
            },
        ],
        "layout": layout,
    }


def build_tax_harvesting_holding_figure(
    harvest_lots: Sequence[Any],
    maturing_lots: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build stacked bar of Harvestable Loss & Market Value by Holding Type & LTCG Maturity."""
    buckets: dict[str, tuple[float, float]] = {}
    for lot in harvest_lots:
        ht = str(getattr(lot, "holding_type", "") or "Unknown")
        mv, loss = buckets.get(ht, (0.0, 0.0))
        buckets[ht] = (
            mv + abs(float(getattr(lot, "close_value", 0.0) or 0.0)),
            loss + abs(float(getattr(lot, "unrealized_pl", 0.0) or 0.0)),
        )
    if maturing_lots:
        m_mv = sum(abs(float(getattr(m, "close_value", 0.0) or 0.0)) for m in maturing_lots)
        m_pl = sum(abs(float(getattr(m, "unrealized_pl", 0.0) or 0.0)) for m in maturing_lots)
        buckets["Maturing ≤90d"] = (m_mv, m_pl)

    if not buckets:
        return None
    cats = list(buckets.keys())
    mvs = [buckets[c][0] for c in cats]
    losses = [buckets[c][1] for c in cats]
    y_ticks = currency_ticks([0.0, *mvs, *losses], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=360, barmode="group", y_ticks=y_ticks, x_axis_type="category"
    )
    return {
        "data": [
            {
                "type": "bar",
                "x": cats,
                "y": mvs,
                "name": "Market Value",
                "marker": {"color": "#60A5FA"},
            },
            {
                "type": "bar",
                "x": cats,
                "y": losses,
                "name": "Unrealized Loss / Gain",
                "marker": {"color": "#FBBF24"},
            },
        ],
        "layout": layout,
    }
