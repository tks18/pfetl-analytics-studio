"""Plotly chart builders for the Investments Overview & Allocation workspace."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pfetl_analytics_studio.ui.charts import (
    TreemapNode,
    build_treemap_figure,
    currency_ticks,
    numeric_ticks,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.formatting import format_currency, format_percent

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


def build_inv_allocation_treemap_figure(
    rows: Sequence[Any],
    currency_id: str,
    *,
    dimension_label: str = "Asset Class",
) -> dict[str, Any] | None:
    """Build a treemap of portfolio allocation (supports 2-level parent_label hierarchy)."""
    _ = dimension_label
    nodes: list[TreemapNode] = []
    parents_seen: dict[str, float] = {}
    for r in rows:
        val = float(getattr(r, "current_value", 0.0))
        if val <= 0:
            continue
        lbl = str(getattr(r, "label", ""))
        parent = str(getattr(r, "parent_label", None) or "")
        if parent:
            parents_seen[parent] = parents_seen.get(parent, 0.0) + val
            nodes.append(
                TreemapNode(
                    node_id=f"{parent}::{lbl}",
                    label=lbl,
                    parent_id=parent,
                    value=val,
                    share_pct=getattr(r, "weight", None),
                )
            )
        else:
            nodes.append(
                TreemapNode(
                    node_id=lbl,
                    label=lbl,
                    parent_id="",
                    value=val,
                    share_pct=getattr(r, "weight", None),
                )
            )
    for parent_lbl, p_val in parents_seen.items():
        nodes.insert(
            0,
            TreemapNode(
                node_id=parent_lbl,
                label=parent_lbl,
                parent_id="",
                value=p_val,
            ),
        )
    if not nodes:
        return None
    return build_treemap_figure(nodes, currency_id=currency_id, height=340)


def build_inv_allocation_bars_figure(
    rows: Sequence[Any],
    currency_id: str,
    *,
    dimension_label: str = "Asset Class",
) -> dict[str, Any] | None:
    """Build horizontal bars comparing Invested vs Current Value across buckets."""
    top_rows = list(rows[:10])
    if not top_rows:
        return None
    top_rows.reverse()
    labels = [str(getattr(r, "label", "")) for r in top_rows]
    invested = [float(getattr(r, "invested_value", 0.0)) for r in top_rows]
    current = [float(getattr(r, "current_value", 0.0)) for r in top_rows]
    weights = [float(getattr(r, "weight", 0.0) or 0.0) for r in top_rows]

    x_ticks = currency_ticks([*invested, *current], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340,
        barmode="group",
        horizontal_bars=True,
        x_currency_ticks=x_ticks,
    )

    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": invested,
                "name": "Invested",
                "orientation": "h",
                "marker": {"color": "#64748B"},
                "hovertemplate": "%{y}<br>Invested: %{customdata}<extra></extra>",
                "customdata": [format_currency(v, currency_id) for v in invested],
            },
            {
                "type": "bar",
                "y": labels,
                "x": current,
                "name": "Current Value",
                "orientation": "h",
                "marker": {"color": "#38BDF8"},
                "hovertemplate": "%{y}<br>Current: %{customdata[0]} (%{customdata[1]})<extra></extra>",
                "customdata": [
                    [format_currency(v, currency_id), format_percent(w)]
                    for v, w in zip(current, weights, strict=True)
                ],
            },
        ],
        "layout": layout,
    }


def build_inv_class_drift_figure(
    rows: Sequence[Any],
    *,
    dimension_label: str = "Asset Class",
) -> dict[str, Any] | None:
    """Build a horizontal bar chart of Market Weight % vs XIRR %."""
    top_rows = list(rows[:10])
    if not top_rows:
        return None
    top_rows.reverse()
    labels = [str(getattr(r, "label", "")) for r in top_rows]
    mkt_w = [float(getattr(r, "weight", 0.0) or 0.0) for r in top_rows]
    xirr_v = [float(getattr(r, "xirr", 0.0) or 0.0) for r in top_rows]

    x_ticks = numeric_ticks([*mkt_w, *xirr_v], suffix="%")
    layout = studio_plotly_layout(
        height=340,
        barmode="group",
        horizontal_bars=True,
        x_currency_ticks=x_ticks,
    )

    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": mkt_w,
                "name": "Weight %",
                "orientation": "h",
                "marker": {"color": "#A78BFA"},
                "hovertemplate": "%{y}<br>Weight: %{x:.2f}%<extra></extra>",
            },
            {
                "type": "bar",
                "y": labels,
                "x": xirr_v,
                "name": "XIRR %",
                "orientation": "h",
                "marker": {"color": "#34D399"},
                "hovertemplate": "%{y}<br>XIRR: %{x:.2f}%<extra></extra>",
            },
        ],
        "layout": layout,
    }


def build_inv_pnl_decomposition_figure(
    rows: Sequence[Any],
    currency_id: str,
    *,
    dimension_label: str = "Asset Class",
) -> dict[str, Any] | None:
    """Build a stacked bar chart of Asset P&L and Forex P&L (or Unrealized P&L)."""
    top_rows = list(rows[:10])
    if not top_rows:
        return None
    labels = [str(getattr(r, "label", "")) for r in top_rows]
    asset_pnl = [
        float(
            val
            if (val := getattr(r, "asset_pnl", None)) is not None
            else getattr(r, "unrealized_pl", 0.0)
        )
        for r in top_rows
    ]
    forex_pnl = [float(getattr(r, "forex_pnl", 0.0) or 0.0) for r in top_rows]

    y_ticks = currency_ticks([*asset_pnl, *forex_pnl], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340,
        barmode="relative",
        y_ticks=y_ticks,
        x_axis_type="category",
    )

    return {
        "data": [
            {
                "type": "bar",
                "x": labels,
                "y": asset_pnl,
                "name": "Asset P&L",
                "marker": {"color": "#38BDF8"},
                "hovertemplate": "%{x}<br>Asset P&L: %{customdata}<extra></extra>",
                "customdata": [format_currency(v, currency_id) for v in asset_pnl],
            },
            {
                "type": "bar",
                "x": labels,
                "y": forex_pnl,
                "name": "Forex P&L",
                "marker": {"color": "#FBBF24"},
                "hovertemplate": "%{x}<br>Forex P&L: %{customdata}<extra></extra>",
                "customdata": [format_currency(v, currency_id) for v in forex_pnl],
            },
        ],
        "layout": layout,
    }


def build_inv_holdings_top_figure(
    holdings: Sequence[Any],
    currency_id: str,
    *,
    limit: int = 12,
) -> dict[str, Any] | None:
    """Build a horizontal bar chart of top holdings by Market Value."""
    top = list(holdings[:limit])
    if not top:
        return None
    top.reverse()
    labels = [str(getattr(h, "instrument_name", "") or getattr(h, "isin", ""))[:28] for h in top]
    values = [float(getattr(h, "market_value", 0.0)) for h in top]
    weights = [float(getattr(h, "isin_weight", 0.0) or 0.0) for h in top]

    x_ticks = currency_ticks(values, currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340,
        horizontal_bars=True,
        x_currency_ticks=x_ticks,
    )
    layout["showlegend"] = False

    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": values,
                "orientation": "h",
                "marker": {"color": [_DIM_PALETTE[i % len(_DIM_PALETTE)] for i in range(len(top))]},
                "hovertemplate": "%{y}<br>Value: %{customdata[0]} (%{customdata[1]})<extra></extra>",
                "customdata": [
                    [format_currency(v, currency_id), format_percent(w)]
                    for v, w in zip(values, weights, strict=True)
                ],
            }
        ],
        "layout": layout,
    }


def build_inv_risk_return_scatter_figure(
    holdings: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build a Weight % vs XIRR % scatter bubble chart across ISIN holdings."""
    pts = [
        h
        for h in holdings
        if getattr(h, "isin_weight", None) is not None
        and getattr(h, "xirr_local", None) is not None
    ]
    if not pts:
        return None
    x_vals = [float(getattr(h, "isin_weight", 0.0) or 0.0) for h in pts]
    y_vals = [float(getattr(h, "xirr_local", 0.0) or 0.0) for h in pts]
    mkt_vals = [max(float(getattr(h, "market_value", 0.0)), 1.0) for h in pts]
    max_mv = max(mkt_vals)
    sizes = [max(10.0, min(38.0, (mv / max_mv) * 38.0)) for mv in mkt_vals]
    names = [str(getattr(h, "instrument_name", "") or getattr(h, "isin", ""))[:28] for h in pts]

    layout = studio_plotly_layout(
        height=340,
        y_ticks=numeric_ticks(y_vals, suffix="%"),
        x_currency_ticks=numeric_ticks(x_vals, suffix="%"),
        x_axis_type="linear",
    )
    layout["showlegend"] = False
    layout["shapes"] = [
        {
            "type": "line",
            "x0": min(x_vals),
            "x1": max(x_vals),
            "y0": 0,
            "y1": 0,
            "line": {"color": "rgba(148, 163, 184, 0.4)", "dash": "dot", "width": 1},
        }
    ]
    return {
        "data": [
            {
                "type": "scatter",
                "mode": "markers",
                "x": x_vals,
                "y": y_vals,
                "marker": {
                    "size": sizes,
                    "color": y_vals,
                    "colorscale": [[0.0, "#F87171"], [0.5, "#FBBF24"], [1.0, "#34D399"]],
                    "opacity": 0.85,
                    "line": {"color": "#0F172A", "width": 1},
                },
                "hovertemplate": "%{customdata[0]}<br>Weight: %{x:.2f}% | XIRR: %{y:.2f}%<br>Value: %{customdata[1]}<extra></extra>",
                "customdata": [
                    [n, format_currency(mv, currency_id)]
                    for n, mv in zip(names, mkt_vals, strict=True)
                ],
            }
        ],
        "layout": layout,
    }


def build_inv_class_target_drift_figure(
    holdings: Sequence[Any],
) -> dict[str, Any] | None:
    """Build Actual vs Target Weight % grouped bars by Asset Class."""
    by_class: dict[str, tuple[float, float]] = {}
    for h in holdings:
        cls = str(getattr(h, "instrument_class", "Unclassified"))
        cw = getattr(h, "class_weight", None)
        tw = getattr(h, "class_target_weight", None)
        if cw is not None and tw is not None and cls not in by_class:
            by_class[cls] = (float(cw), float(tw))
    if not by_class:
        return None
    labels = list(by_class.keys())
    actual = [by_class[k][0] for k in labels]
    target = [by_class[k][1] for k in labels]
    x_ticks = numeric_ticks([*actual, *target], suffix="%")
    layout = studio_plotly_layout(
        height=340,
        barmode="group",
        horizontal_bars=True,
        x_currency_ticks=x_ticks,
    )
    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": actual,
                "name": "Actual Weight %",
                "orientation": "h",
                "marker": {"color": "#38BDF8"},
                "hovertemplate": "%{y}<br>Actual: %{x:.2f}%<extra></extra>",
            },
            {
                "type": "bar",
                "y": labels,
                "x": target,
                "name": "Target Weight %",
                "orientation": "h",
                "marker": {"color": "#94A3B8"},
                "hovertemplate": "%{y}<br>Target: %{x:.2f}%<extra></extra>",
            },
        ],
        "layout": layout,
    }


def build_inv_tax_harvest_figure(
    holdings: Sequence[Any],
    currency_id: str,
    *,
    limit: int = 10,
) -> dict[str, Any] | None:
    """Build horizontal bar chart of top tax-loss harvesting candidates."""
    candidates = [
        h
        for h in holdings
        if getattr(h, "harvestable_loss", None) is not None
        and float(getattr(h, "harvestable_loss", 0.0) or 0.0) > 0
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda h: float(
            getattr(h, "harvest_priority_score", None) or getattr(h, "harvestable_loss", 0.0) or 0.0
        ),
        reverse=True,
    )
    top = candidates[:limit]
    top.reverse()
    labels = [str(getattr(h, "instrument_name", "") or getattr(h, "isin", ""))[:28] for h in top]
    losses = [float(getattr(h, "harvestable_loss", 0.0) or 0.0) for h in top]
    x_ticks = currency_ticks(losses, currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340,
        horizontal_bars=True,
        x_currency_ticks=x_ticks,
    )
    layout["showlegend"] = False
    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": losses,
                "orientation": "h",
                "marker": {"color": "#F87171"},
                "hovertemplate": "%{y}<br>Harvestable Loss: %{customdata}<extra></extra>",
                "customdata": [format_currency(v, currency_id) for v in losses],
            }
        ],
        "layout": layout,
    }


def build_inv_isin_history_figure(
    history: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build monthly Book Value vs Market Value line chart for the ISIN Detail Modal."""
    if not history:
        return None
    dates = [str(getattr(h, "closing_date", "")) for h in history]
    book = [float(getattr(h, "book_value", 0.0)) for h in history]
    mkt = [float(getattr(h, "market_value", 0.0)) for h in history]
    y_ticks = currency_ticks([*book, *mkt], currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=240,
        y_ticks=y_ticks,
        x_axis_type="date",
    )
    return {
        "data": [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "x": dates,
                "y": book,
                "name": "Book Value",
                "line": {"color": "#94A3B8", "width": 2, "dash": "dot"},
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "x": dates,
                "y": mkt,
                "name": "Market Value",
                "line": {"color": "#38BDF8", "width": 2.5},
            },
        ],
        "layout": layout,
    }


def build_inv_gainers_losers_figure(
    holdings: Sequence[Any],
    currency_id: str,
    *,
    limit: int = 5,
) -> dict[str, Any] | None:
    """Build a diverging horizontal bar chart of top Unrealized Gainers vs Losers."""
    if not holdings:
        return None
    sorted_h = sorted(holdings, key=lambda h: float(getattr(h, "unrealized_pnl", 0.0)))
    losers = [h for h in sorted_h[:limit] if float(getattr(h, "unrealized_pnl", 0.0)) < 0]
    gainers = [h for h in sorted_h[-limit:] if float(getattr(h, "unrealized_pnl", 0.0)) > 0]
    combined = losers + gainers
    if not combined:
        combined = list(sorted_h[-limit:])
    labels = [
        str(getattr(h, "instrument_name", "") or getattr(h, "isin", ""))[:28] for h in combined
    ]
    vals = [float(getattr(h, "unrealized_pnl", 0.0)) for h in combined]
    colors = ["#34D399" if v >= 0 else "#F87171" for v in vals]
    x_ticks = currency_ticks(vals, currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=340,
        horizontal_bars=True,
        x_currency_ticks=x_ticks,
    )
    layout["showlegend"] = False
    return {
        "data": [
            {
                "type": "bar",
                "y": labels,
                "x": vals,
                "orientation": "h",
                "marker": {"color": colors},
                "hovertemplate": "%{y}<br>Unrealized P&L: %{customdata}<extra></extra>",
                "customdata": [format_currency(v, currency_id) for v in vals],
            }
        ],
        "layout": layout,
    }
