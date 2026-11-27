"""Plotly chart builders for Realized, Reconciliation & Market Evidence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pfetl_analytics_studio.ui.charts import (
    currency_ticks,
    numeric_ticks,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.formatting import format_currency

_DIM_PALETTE = [
    "#38BDF8",
    "#34D399",
    "#A78BFA",
    "#FBBF24",
    "#F472B6",
    "#60A5FA",
    "#2DD4BF",
    "#FB923C",
    "#E879F9",
    "#94A3B8",
]


def build_inv_realized_fy_pnl_figure(
    rows: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Realized LTCG vs STCG P&L stacked by FY."""
    if not rows:
        return None
    ltcg: dict[str, float] = {}
    stcg: dict[str, float] = {}
    for r in rows:
        fy = str(getattr(r, "fy", "") or "Unknown")
        gl = float(getattr(r, "realized_gain_loss", 0.0) or 0.0)
        if str(getattr(r, "holding_type", "")) == "LTCG":
            ltcg[fy] = ltcg.get(fy, 0.0) + gl
        else:
            stcg[fy] = stcg.get(fy, 0.0) + gl
    fys = sorted(set(ltcg.keys()) | set(stcg.keys()))
    l_vals = [ltcg.get(f, 0.0) for f in fys]
    s_vals = [stcg.get(f, 0.0) for f in fys]
    y_ticks = currency_ticks([*l_vals, *s_vals], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340, barmode="relative", y_ticks=y_ticks, x_axis_type="category"
    )
    return {
        "data": [
            {
                "type": "bar",
                "x": fys,
                "y": l_vals,
                "name": "LTCG P&L",
                "marker": {"color": "#34D399"},
            },
            {
                "type": "bar",
                "x": fys,
                "y": s_vals,
                "name": "STCG P&L",
                "marker": {"color": "#38BDF8"},
            },
        ],
        "layout": layout,
    }


def build_inv_realized_tax_drag_figure(
    rows: Sequence[Any],
    currency_id: str,
    *,
    limit: int = 10,
) -> dict[str, Any] | None:
    """Build Top 10 Disposals: Pre-Tax Realized P&L vs Estimated Tax."""
    if not rows:
        return None
    top = sorted(
        rows, key=lambda r: abs(float(getattr(r, "realized_gain_loss", 0.0))), reverse=True
    )[:limit]
    top.reverse()
    labels = [str(getattr(r, "instrument_name", "") or getattr(r, "isin", ""))[:26] for r in top]
    gl = [float(getattr(r, "realized_gain_loss", 0.0) or 0.0) for r in top]
    tax = [float(getattr(r, "estimated_tax", 0.0) or 0.0) for r in top]
    x_ticks = currency_ticks([*gl, *tax], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340, barmode="group", horizontal_bars=True, x_currency_ticks=x_ticks
    )
    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": gl,
                "name": "Realized P&L",
                "orientation": "h",
                "marker": {"color": "#38BDF8"},
            },
            {
                "type": "bar",
                "y": labels,
                "x": tax,
                "name": "Est. Tax",
                "orientation": "h",
                "marker": {"color": "#F87171"},
            },
        ],
        "layout": layout,
    }


def build_inv_realized_asset_vs_fx_figure(
    rows: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Realized Asset P&L vs Forex P&L stacked by FY."""
    if not rows:
        return None
    asset_map: dict[str, float] = {}
    fx_map: dict[str, float] = {}
    for r in rows:
        fy = str(getattr(r, "fy", "") or "Unknown")
        ap = getattr(r, "asset_pnl", None)
        fp = getattr(r, "forex_pnl", None)
        asset_map[fy] = asset_map.get(fy, 0.0) + float(
            ap if ap is not None else getattr(r, "realized_gain_loss", 0.0)
        )
        fx_map[fy] = fx_map.get(fy, 0.0) + float(fp or 0.0)
    fys = sorted(asset_map.keys())
    a_vals = [asset_map[f] for f in fys]
    f_vals = [fx_map[f] for f in fys]
    y_ticks = currency_ticks([*a_vals, *f_vals], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340, barmode="relative", y_ticks=y_ticks, x_axis_type="category"
    )
    return {
        "data": [
            {
                "type": "bar",
                "x": fys,
                "y": a_vals,
                "name": "Asset P&L",
                "marker": {"color": "#A78BFA"},
            },
            {
                "type": "bar",
                "x": fys,
                "y": f_vals,
                "name": "Forex P&L",
                "marker": {"color": "#FBBF24"},
            },
        ],
        "layout": layout,
    }


def build_inv_realized_tax_type_donut_figure(
    rows: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Donut chart of Sale Proceeds by Tax Type & Holding Type."""
    if not rows:
        return None
    buckets: dict[str, float] = {}
    for r in rows:
        key = f"{getattr(r, 'tax_type', '') or 'Other'} ({getattr(r, 'holding_type', '')})"
        buckets[key] = buckets.get(key, 0.0) + float(getattr(r, "sale_proceeds", 0.0) or 0.0)
    labels = list(buckets.keys())
    vals = [max(0.0, buckets[k]) for k in labels]
    if sum(vals) <= 0:
        return None
    layout = studio_plotly_layout(height=340)
    return {
        "data": [
            {
                "type": "pie",
                "labels": labels,
                "values": vals,
                "hole": 0.55,
                "marker": {"colors": _DIM_PALETTE[: len(labels)]},
                "hovertemplate": "%{label}<br>Proceeds: %{customdata}<br>(%{percent})<extra></extra>",
                "customdata": [format_currency(v, currency_id) for v in vals],
            }
        ],
        "layout": layout,
    }


def build_inv_recon_adjustments_figure(
    groups: Sequence[Any],
    currency_id: str,
    *,
    limit: int = 10,
) -> dict[str, Any] | None:
    """Build diverging bars of Cost_Basis_Adjustment across reconciled groups."""
    pts = [g for g in groups if abs(float(getattr(g, "cost_basis_adjustment", 0.0))) > 0]
    if not pts:
        return None
    pts = sorted(
        pts, key=lambda g: abs(float(getattr(g, "cost_basis_adjustment", 0.0))), reverse=True
    )[:limit]
    pts.reverse()
    labels = [str(getattr(g, "instrument_name", "") or getattr(g, "isin", ""))[:26] for g in pts]
    adjs = [float(getattr(g, "cost_basis_adjustment", 0.0) or 0.0) for g in pts]
    colors = ["#34D399" if v >= 0 else "#F87171" for v in adjs]
    x_ticks = currency_ticks(adjs, currency_id, include_zero=True)
    layout = studio_plotly_layout(height=340, horizontal_bars=True, x_currency_ticks=x_ticks)
    layout["showlegend"] = False
    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": adjs,
                "orientation": "h",
                "marker": {"color": colors},
                "hovertemplate": "%{y}<br>Cost Adj: %{customdata}<extra></extra>",
                "customdata": [format_currency(v, currency_id) for v in adjs],
            }
        ],
        "layout": layout,
    }


def build_inv_recon_qty_bridge_figure(
    groups: Sequence[Any],
    *,
    limit: int = 10,
) -> dict[str, Any] | None:
    """Build grouped horizontal bars comparing Broker vs Reconstructed Quantity."""
    pts = [g for g in groups if abs(float(getattr(g, "quantity_adjustment", 0.0))) > 0]
    if not pts:
        return None
    pts = sorted(
        pts, key=lambda g: abs(float(getattr(g, "quantity_adjustment", 0.0))), reverse=True
    )[:limit]
    pts.reverse()
    labels = [str(getattr(g, "instrument_name", "") or getattr(g, "isin", ""))[:26] for g in pts]
    broker_q = [float(getattr(g, "broker_quantity", 0.0) or 0.0) for g in pts]
    recon_q = [float(getattr(g, "reconstructed_quantity", 0.0) or 0.0) for g in pts]
    x_ticks = numeric_ticks([*broker_q, *recon_q], suffix="")
    layout = studio_plotly_layout(
        height=340, barmode="group", horizontal_bars=True, x_currency_ticks=x_ticks
    )
    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": broker_q,
                "name": "Broker Qty",
                "orientation": "h",
                "marker": {"color": "#94A3B8"},
            },
            {
                "type": "bar",
                "y": labels,
                "x": recon_q,
                "name": "Reconstructed Qty",
                "orientation": "h",
                "marker": {"color": "#38BDF8"},
            },
        ],
        "layout": layout,
    }


def _provenance_traces(
    dates: list[str],
    vals: list[float],
    imputed: list[bool],
    gaps: list[bool],
    name: str,
    color: str,
) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = [
        {
            "type": "scatter",
            "mode": "lines",
            "x": dates,
            "y": vals,
            "name": name,
            "line": {"color": color, "width": 2.2},
        }
    ]
    imp_x = [d for d, f in zip(dates, imputed, strict=True) if f]
    imp_y = [v for v, f in zip(vals, imputed, strict=True) if f]
    if imp_x:
        traces.append(
            {
                "type": "scatter",
                "mode": "markers",
                "x": imp_x,
                "y": imp_y,
                "name": "Imputed",
                "marker": {"color": "#FBBF24", "symbol": "diamond", "size": 7},
            }
        )
    gap_x = [d for d, f in zip(dates, gaps, strict=True) if f]
    gap_y = [v for v, f in zip(vals, gaps, strict=True) if f]
    if gap_x:
        traces.append(
            {
                "type": "scatter",
                "mode": "markers",
                "x": gap_x,
                "y": gap_y,
                "name": "Closure Gap",
                "marker": {"color": "#F87171", "symbol": "x", "size": 7},
            }
        )
    return traces


def build_inv_evidence_price_figure(
    points: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Closing Price vs Buy Price with Imputed/Gap provenance markers."""
    if not points:
        return None
    dates = [str(getattr(p, "obs_date", "")) for p in points]
    close_p = [float(getattr(p, "closing_price", 0.0) or 0.0) for p in points]
    buy_p = [float(getattr(p, "buy_price", 0.0) or 0.0) for p in points]
    imp = [bool(getattr(p, "is_imputed", False)) for p in points]
    gaps = [bool(getattr(p, "is_closure_gap", False)) for p in points]
    y_ticks = currency_ticks([*close_p, *buy_p], currency_id)
    layout = studio_plotly_layout(
        height=340, y_ticks=y_ticks, x_axis_type="date", date_tick_format="%d %b"
    )
    traces = _provenance_traces(dates, close_p, imp, gaps, "Closing Price", "#38BDF8")
    traces.append(
        {
            "type": "scatter",
            "mode": "lines",
            "x": dates,
            "y": buy_p,
            "name": "Avg Buy Price",
            "line": {"color": "#94A3B8", "dash": "dot", "width": 1.8},
        }
    )
    return {"data": traces, "layout": layout}


def build_inv_evidence_value_area_figure(
    points: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build filled area chart of Closing Value vs Book Value."""
    if not points:
        return None
    dates = [str(getattr(p, "obs_date", "")) for p in points]
    c_val = [float(getattr(p, "closing_value", 0.0) or 0.0) for p in points]
    b_val = [float(getattr(p, "buy_value", 0.0) or 0.0) for p in points]
    y_ticks = currency_ticks([*c_val, *b_val], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340, y_ticks=y_ticks, x_axis_type="date", date_tick_format="%d %b"
    )
    return {
        "data": [
            {
                "type": "scatter",
                "mode": "lines",
                "fill": "tozeroy",
                "x": dates,
                "y": c_val,
                "name": "Market Value",
                "line": {"color": "#34D399", "width": 2.2},
            },
            {
                "type": "scatter",
                "mode": "lines",
                "x": dates,
                "y": b_val,
                "name": "Cost Basis",
                "line": {"color": "#94A3B8", "dash": "dot", "width": 1.8},
            },
        ],
        "layout": layout,
    }


def build_inv_evidence_benchmark_figure(
    points: Sequence[Any],
) -> dict[str, Any] | None:
    """Build Benchmark Close line with Imputed/Gap provenance markers."""
    if not points:
        return None
    dates = [str(getattr(p, "obs_date", "")) for p in points]
    closes = [float(getattr(p, "close", 0.0) or 0.0) for p in points]
    imp = [bool(getattr(p, "is_imputed", False)) for p in points]
    gaps = [bool(getattr(p, "is_closure_gap", False)) for p in points]
    y_ticks = numeric_ticks(closes, suffix="")
    layout = studio_plotly_layout(
        height=340, y_ticks=y_ticks, x_axis_type="date", date_tick_format="%d %b"
    )
    return {
        "data": _provenance_traces(dates, closes, imp, gaps, "Benchmark Close", "#A78BFA"),
        "layout": layout,
    }


def build_inv_evidence_fx_figure(
    points: Sequence[Any],
) -> dict[str, Any] | None:
    """Build Daily FX Rate line with Imputed/Gap provenance markers."""
    if not points:
        return None
    dates = [str(getattr(p, "obs_date", "")) for p in points]
    rates = [float(getattr(p, "fx_rate", 1.0) or 1.0) for p in points]
    imp = [bool(getattr(p, "is_imputed", False)) for p in points]
    gaps = [bool(getattr(p, "is_closure_gap", False)) for p in points]
    y_ticks = numeric_ticks(rates, suffix="")
    layout = studio_plotly_layout(
        height=340, y_ticks=y_ticks, x_axis_type="date", date_tick_format="%d %b"
    )
    return {
        "data": _provenance_traces(dates, rates, imp, gaps, "FX Rate", "#FBBF24"),
        "layout": layout,
    }


def build_inv_evidence_normalized_index_figure(
    market_pts: Sequence[Any],
    bm_pts: Sequence[Any],
) -> dict[str, Any] | None:
    """Build Rebased Index (= 100) comparing Instrument Price vs Benchmark Close."""
    if not market_pts or not bm_pts:
        return None
    m_base = float(getattr(market_pts[0], "closing_price", 0.0) or 0.0)
    b_base = float(getattr(bm_pts[0], "close", 0.0) or 0.0)
    if m_base <= 0 or b_base <= 0:
        return None
    m_dates = [str(getattr(p, "obs_date", "")) for p in market_pts]
    m_idx = [float(getattr(p, "closing_price", 0.0) or 0.0) / m_base * 100.0 for p in market_pts]
    b_dates = [str(getattr(p, "obs_date", "")) for p in bm_pts]
    b_idx = [float(getattr(p, "close", 0.0) or 0.0) / b_base * 100.0 for p in bm_pts]
    y_ticks = numeric_ticks([*m_idx, *b_idx], suffix="")
    layout = studio_plotly_layout(
        height=340, y_ticks=y_ticks, x_axis_type="date", date_tick_format="%d %b"
    )
    return {
        "data": [
            {
                "type": "scatter",
                "mode": "lines",
                "x": m_dates,
                "y": m_idx,
                "name": "Instrument (Base 100)",
                "line": {"color": "#38BDF8", "width": 2.2},
            },
            {
                "type": "scatter",
                "mode": "lines",
                "x": b_dates,
                "y": b_idx,
                "name": "Benchmark (Base 100)",
                "line": {"color": "#A78BFA", "width": 2.0, "dash": "dot"},
            },
        ],
        "layout": layout,
    }


def build_inv_evidence_provenance_bar_figure(
    market_pts: Sequence[Any],
    bm_pts: Sequence[Any],
    fx_pts: Sequence[Any],
) -> dict[str, Any] | None:
    """Build Stacked Bar of Observed vs Imputed vs Closure Gap counts."""
    series = [("Market Data", market_pts), ("Benchmark", bm_pts), ("FX Rates", fx_pts)]
    labels = [s[0] for s in series]
    obs_c: list[float] = []
    imp_c: list[float] = []
    gap_c: list[float] = []
    for _, pts in series:
        imp_n = sum(1 for p in pts if getattr(p, "is_imputed", False))
        gap_n = sum(1 for p in pts if getattr(p, "is_closure_gap", False))
        obs_n = max(0, len(pts) - imp_n - gap_n)
        obs_c.append(float(obs_n))
        imp_c.append(float(imp_n))
        gap_c.append(float(gap_n))
    if sum(obs_c) + sum(imp_c) + sum(gap_c) == 0:
        return None
    y_ticks = numeric_ticks(
        [o + i + g for o, i, g in zip(obs_c, imp_c, gap_c, strict=True)], suffix=""
    )
    layout = studio_plotly_layout(
        height=340, barmode="stack", y_ticks=y_ticks, x_axis_type="category"
    )
    return {
        "data": [
            {
                "type": "bar",
                "x": labels,
                "y": obs_c,
                "name": "Observed",
                "marker": {"color": "#34D399"},
            },
            {
                "type": "bar",
                "x": labels,
                "y": imp_c,
                "name": "Imputed",
                "marker": {"color": "#FBBF24"},
            },
            {
                "type": "bar",
                "x": labels,
                "y": gap_c,
                "name": "Closure Gap",
                "marker": {"color": "#F87171"},
            },
        ],
        "layout": layout,
    }
