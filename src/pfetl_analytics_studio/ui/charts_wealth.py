"""Dark-mode Plotly chart builders for Wealth: Net Worth, Liquidity & Attribution.

Owns
----
- Book vs Market Net Worth, Net Worth Attribution Waterfall, Asset Group Treemap,
  Unrealized P&L & FX P&L Trend, Liquidity Runway vs Debt/Asset, and Asset Drawdown bars.

Does not own
------------
- DuckDB queries or `analytics/` services.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pfetl_analytics_studio.ui.charts import (
    TreemapNode,
    WaterfallStep,
    build_treemap_figure,
    build_waterfall_figure,
    chart_config,
    currency_ticks,
    numeric_ticks,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.formatting import format_currency


def build_wealth_nw_comparison_figure(
    series: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Book vs Market Net Worth dual-line trajectory."""
    pts = [p for p in series if p.book_net_worth is not None or p.market_net_worth is not None]
    if not pts:
        return None
    dates = [p.month_end.isoformat() for p in pts]
    book_vals = [
        round(float(p.book_net_worth), 2) if p.book_net_worth is not None else None for p in pts
    ]
    mkt_vals = [
        round(float(p.market_net_worth), 2) if p.market_net_worth is not None else None for p in pts
    ]
    all_vals = [v for v in (*book_vals, *mkt_vals) if v is not None]
    traces: list[dict[str, Any]] = []
    if any(v is not None for v in mkt_vals):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Market Net Worth",
                "x": dates,
                "y": mkt_vals,
                "line": {"color": "#38BDF8", "width": 2.6},
            }
        )
    if any(v is not None for v in book_vals):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Book Net Worth",
                "x": dates,
                "y": book_vals,
                "line": {"color": "#CBD5E1", "width": 2.0, "dash": "dash"},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(y_ticks=currency_ticks(all_vals, currency_id)),
        "config": chart_config("cockpit"),
    }


def build_wealth_attribution_waterfall_figure(attribution: Any) -> dict[str, Any] | None:
    """Build Opening Market → Net Savings → Market Return → Residual → Closing Market bridge."""
    if attribution is None or getattr(attribution, "state", "") != "ready":
        return None
    steps = [
        WaterfallStep(
            "Opening Market", float(attribution.opening_market_assets), measure="absolute"
        ),
        WaterfallStep("Net Savings", float(attribution.net_savings_flow), measure="relative"),
        WaterfallStep("Market Return", float(attribution.market_return_flow), measure="relative"),
        WaterfallStep("Residual", float(attribution.attribution_residual), measure="relative"),
        WaterfallStep("Closing Market", float(attribution.closing_market_assets), measure="total"),
    ]
    return build_waterfall_figure(steps, currency_id=str(attribution.currency_id))


def build_wealth_group_treemap_figure(
    assets: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Asset Group → Asset Subcategory treemap."""
    pos_assets = [
        a
        for a in assets
        if (a.closing_balance_market if a.closing_balance_market is not None else a.closing_balance)
        > 0
    ]
    if not pos_assets:
        return None
    tot = sum(
        float(
            a.closing_balance_market if a.closing_balance_market is not None else a.closing_balance
        )
        for a in pos_assets
    )
    grp_totals: dict[str, float] = {}
    for a in pos_assets:
        val = float(
            a.closing_balance_market if a.closing_balance_market is not None else a.closing_balance
        )
        grp_totals[a.asset_group] = grp_totals.get(a.asset_group, 0.0) + val

    nodes: list[TreemapNode] = [
        TreemapNode(node_id="root", label="Gross Wealth", parent_id="", value=tot, key="root")
    ]
    for grp, gval in grp_totals.items():
        nodes.append(
            TreemapNode(
                node_id=f"grp:{grp}",
                label=grp,
                parent_id="root",
                value=gval,
                share_pct=(gval / tot) if tot > 0 else None,
                key=grp,
            )
        )
    for a in pos_assets:
        val = float(
            a.closing_balance_market if a.closing_balance_market is not None else a.closing_balance
        )
        nodes.append(
            TreemapNode(
                node_id=f"ast:{a.asset_id}",
                label=a.asset_name,
                parent_id=f"grp:{a.asset_group}",
                value=val,
                share_pct=(val / tot) if tot > 0 else None,
                key=a.asset_id,
            )
        )
    return build_treemap_figure(nodes, currency_id=currency_id)


def build_wealth_pnl_fx_figure(
    series: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Unrealized P&L, Asset P&L, and Forex P&L trajectory."""
    pts = [
        p
        for p in series
        if p.closing_unrealized_pl is not None
        or p.closing_asset_pnl is not None
        or p.closing_forex_pnl is not None
    ]
    if not pts:
        return None
    dates = [p.month_end.isoformat() for p in pts]
    unr = [
        round(float(p.closing_unrealized_pl), 2) if p.closing_unrealized_pl is not None else None
        for p in pts
    ]
    ast_p = [
        round(float(p.closing_asset_pnl), 2) if p.closing_asset_pnl is not None else None
        for p in pts
    ]
    fx_p = [
        round(float(p.closing_forex_pnl), 2) if p.closing_forex_pnl is not None else None
        for p in pts
    ]
    all_vals = [v for v in (*unr, *ast_p, *fx_p) if v is not None]
    traces: list[dict[str, Any]] = []
    if any(v is not None for v in unr):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Total Unrealized P&L",
                "x": dates,
                "y": unr,
                "line": {"color": "#34D399", "width": 2.4},
            }
        )
    if any(v is not None for v in ast_p):
        traces.append(
            {
                "type": "bar",
                "name": "Asset P&L",
                "x": dates,
                "y": ast_p,
                "marker": {"color": "#60A5FA", "opacity": 0.75},
            }
        )
    if any(v is not None for v in fx_p):
        traces.append(
            {
                "type": "bar",
                "name": "Forex P&L",
                "x": dates,
                "y": fx_p,
                "marker": {"color": "#2DD4BF", "opacity": 0.85},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(all_vals, currency_id, include_zero=True),
            barmode="relative",
        ),
        "config": chart_config("cockpit"),
    }


def build_wealth_liquidity_debt_figure(series: Sequence[Any]) -> dict[str, Any] | None:
    """Build Liquidity Runway (Months) vs Debt/Asset % and YoY Real NW Growth %."""
    pts = [
        p
        for p in series
        if p.liquidity_ratio_months is not None
        or p.debt_to_asset_pct is not None
        or p.yoy_nw_growth_real_pct is not None
    ]
    if not pts:
        return None
    dates = [p.month_end.isoformat() for p in pts]
    liq = [
        round(float(p.liquidity_ratio_months), 2) if p.liquidity_ratio_months is not None else None
        for p in pts
    ]
    yoy_real = [
        round(float(p.yoy_nw_growth_real_pct) * 100.0, 2)
        if p.yoy_nw_growth_real_pct is not None
        else None
        for p in pts
    ]
    traces: list[dict[str, Any]] = []
    if any(v is not None for v in liq):
        traces.append(
            {
                "type": "bar",
                "name": "Liquidity (Mos)",
                "x": dates,
                "y": liq,
                "marker": {"color": "#38BDF8", "opacity": 0.78},
            }
        )
    if any(v is not None for v in yoy_real):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "YoY Real NW Growth %",
                "x": dates,
                "y": yoy_real,
                "yaxis": "y2",
                "line": {"color": "#34D399", "width": 2.4},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=numeric_ticks(liq, suffix=" mos") if any(v is not None for v in liq) else None,
            y2_ticks=numeric_ticks(yoy_real, suffix="%")
            if any(v is not None for v in yoy_real)
            else None,
        ),
        "config": chart_config("cockpit"),
    }


def build_wealth_drawdown_figure(
    assets: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build horizontal bar chart of Top 12 assets by market balance & drawdown from peak."""
    ranked = sorted(
        [
            a
            for a in assets
            if abs(
                float(
                    a.closing_balance_market
                    if a.closing_balance_market is not None
                    else a.closing_balance
                )
            )
            > 0.01
        ],
        key=lambda a: float(
            a.closing_balance_market if a.closing_balance_market is not None else a.closing_balance
        ),
        reverse=True,
    )[:12]
    if not ranked:
        return None
    ranked_rev = list(reversed(ranked))
    names = [a.asset_name for a in ranked_rev]
    vals = [
        round(
            float(
                a.closing_balance_market
                if a.closing_balance_market is not None
                else a.closing_balance
            ),
            2,
        )
        for a in ranked_rev
    ]
    return {
        "data": [
            {
                "type": "bar",
                "orientation": "h",
                "x": vals,
                "y": names,
                "customdata": [
                    {
                        "key": a.asset_id,
                        "display": format_currency(v, currency_id, compact=True),
                        "dd": (
                            f"{a.drawdown_from_peak * 100.0:.1f}%"
                            if a.drawdown_from_peak is not None
                            else "0.0%"
                        ),
                    }
                    for a, v in zip(ranked_rev, vals, strict=True)
                ],
                "hovertemplate": (
                    "<b>%{y}</b><br>Balance: %{customdata.display}<br>"
                    "Drawdown: %{customdata.dd}<extra></extra>"
                ),
                "marker": {"color": "#60A5FA"},
            }
        ],
        "layout": studio_plotly_layout(
            x_currency_ticks=currency_ticks(vals, currency_id, include_zero=True),
            horizontal_bars=True,
        ),
        "config": chart_config("cockpit"),
    }


def build_wealth_currency_donut_figure(
    currency_mix: Sequence[Any],
    base_currency_id: str,
) -> dict[str, Any] | None:
    """Build Donut chart of Base vs Foreign Currency wealth exposure."""
    pos = [c for c in currency_mix if float(c.market_value) > 0]
    if not pos:
        return None
    labels = [
        f"{c.currency_id} (Foreign)" if c.is_foreign else f"{c.currency_id} (Base)" for c in pos
    ]
    values = [round(float(c.market_value), 2) for c in pos]
    custom = [
        f"{format_currency(v, base_currency_id, compact=True)} | FX P&L: "
        f"{format_currency(float(c.forex_pnl), base_currency_id, compact=True, signed=True)}"
        for c, v in zip(pos, values, strict=True)
    ]
    return {
        "data": [
            {
                "type": "pie",
                "labels": labels,
                "values": values,
                "hole": 0.58,
                "customdata": custom,
                "hovertemplate": "<b>%{label}</b><br>%{customdata}<br>%{percent}<extra></extra>",
                "textinfo": "label+percent",
            }
        ],
        "layout": {
            "height": 320,
            "margin": {"l": 12, "r": 12, "t": 16, "b": 12},
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "font": {"color": "#F8FAFC", "size": 11},
            "showlegend": True,
            "legend": {"orientation": "h", "y": -0.05},
        },
        "config": chart_config("cockpit"),
    }


def build_wealth_foreign_exposure_trend_figure(
    series: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build dual-axis Foreign Currency Exposure amount vs % share trend."""
    pts = [
        p for p in series if p.foreign_exposure is not None or p.foreign_exposure_pct is not None
    ]
    if not pts:
        return None
    dates = [p.month_end.isoformat() for p in pts]
    amt = [
        round(float(p.foreign_exposure), 2) if p.foreign_exposure is not None else None for p in pts
    ]
    pct = [
        round(float(p.foreign_exposure_pct) * 100.0, 2)
        if p.foreign_exposure_pct is not None
        else None
        for p in pts
    ]
    traces: list[dict[str, Any]] = []
    if any(v is not None for v in amt):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "fill": "tozeroy",
                "name": "Foreign Exposure",
                "x": dates,
                "y": amt,
                "line": {"color": "#2DD4BF", "width": 2.0},
                "fillcolor": "rgba(45, 212, 191, 0.18)",
            }
        )
    if any(v is not None for v in pct):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "FX Share %",
                "x": dates,
                "y": pct,
                "yaxis": "y2",
                "line": {"color": "#FBBF24", "width": 2.2},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(
                [v for v in amt if v is not None], currency_id, include_zero=True
            )
            if any(v is not None for v in amt)
            else None,
            y2_ticks=numeric_ticks(pct, suffix="%") if any(v is not None for v in pct) else None,
        ),
        "config": chart_config("cockpit"),
    }


def build_wealth_investment_book_vs_market_figure(
    series: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Investment Book vs Market Value dual-line with Closing XIRR % overlay."""
    pts = [
        p
        for p in series
        if p.closing_investment_book is not None or p.closing_investment_market is not None
    ]
    if not pts:
        return None
    dates = [p.month_end.isoformat() for p in pts]
    book = [
        round(float(p.closing_investment_book), 2)
        if p.closing_investment_book is not None
        else None
        for p in pts
    ]
    mkt = [
        round(float(p.closing_investment_market), 2)
        if p.closing_investment_market is not None
        else None
        for p in pts
    ]
    xirr = [
        round(float(p.closing_investment_xirr) * 100.0, 2)
        if p.closing_investment_xirr is not None
        else None
        for p in pts
    ]
    all_v = [v for v in (*book, *mkt) if v is not None]
    traces: list[dict[str, Any]] = []
    if any(v is not None for v in mkt):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Investment Market",
                "x": dates,
                "y": mkt,
                "line": {"color": "#34D399", "width": 2.4},
            }
        )
    if any(v is not None for v in book):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Investment Book",
                "x": dates,
                "y": book,
                "line": {"color": "#CBD5E1", "width": 2.0, "dash": "dash"},
            }
        )
    if any(v is not None for v in xirr):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Inv XIRR %",
                "x": dates,
                "y": xirr,
                "yaxis": "y2",
                "line": {"color": "#FBBF24", "width": 1.8, "dash": "dot"},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(all_v, currency_id),
            y2_ticks=numeric_ticks(xirr, suffix="%") if any(v is not None for v in xirr) else None,
        ),
        "config": chart_config("cockpit"),
    }


def build_wealth_attribution_trend_figure(
    series: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build monthly stacked Net Savings, Market Return & Residual bars + Asset Delta line."""
    if not series:
        return None
    dates = [p.month_end.isoformat() for p in series]
    sav = [round(float(p.net_savings_flow), 2) for p in series]
    mkt = [round(float(p.market_return_flow), 2) for p in series]
    res = [round(float(p.attribution_residual), 2) for p in series]
    delta = [round(float(p.asset_market_delta), 2) for p in series]
    all_v = [*sav, *mkt, *res, *delta]
    return {
        "data": [
            {
                "type": "bar",
                "name": "Net Savings",
                "x": dates,
                "y": sav,
                "marker": {"color": "#60A5FA"},
            },
            {
                "type": "bar",
                "name": "Market Return",
                "x": dates,
                "y": mkt,
                "marker": {"color": "#34D399"},
            },
            {
                "type": "bar",
                "name": "Residual",
                "x": dates,
                "y": res,
                "marker": {"color": "#C084FC"},
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Asset Market Δ",
                "x": dates,
                "y": delta,
                "line": {"color": "#FBBF24", "width": 2.0},
            },
        ],
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(all_v, currency_id, include_zero=True),
            barmode="relative",
        ),
        "config": chart_config("cockpit"),
    }


def build_wealth_liquid_vs_illiquid_figure(
    series: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build stacked bar chart of Liquid vs Illiquid Market Assets over time."""
    pts = [
        p
        for p in series
        if p.liquid_assets_market is not None and p.total_assets_market is not None
    ]
    if not pts:
        return None
    dates = [p.month_end.isoformat() for p in pts]
    liq = [round(float(p.liquid_assets_market or 0.0), 2) for p in pts]
    illiq = [
        round(
            max(0.0, float(p.total_assets_market or 0.0) - float(p.liquid_assets_market or 0.0)), 2
        )
        for p in pts
    ]
    tot = [l_v + i_v for l_v, i_v in zip(liq, illiq, strict=True)]
    return {
        "data": [
            {
                "type": "bar",
                "name": "Liquid Market",
                "x": dates,
                "y": liq,
                "marker": {"color": "#38BDF8"},
            },
            {
                "type": "bar",
                "name": "Illiquid Market",
                "x": dates,
                "y": illiq,
                "marker": {"color": "#64748B"},
            },
        ],
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(tot, currency_id, include_zero=True),
            barmode="stack",
        ),
        "config": chart_config("cockpit"),
    }


def build_wealth_growth_drawdown_bubble_figure(
    assets: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Asset YoY Growth vs Drawdown from Peak bubble chart (size = Market Balance)."""
    pts = [
        a
        for a in assets
        if abs(
            float(
                a.closing_balance_market
                if a.closing_balance_market is not None
                else a.closing_balance
            )
        )
        > 0.01
    ]
    if not pts:
        return None
    max_bal = max(
        abs(
            float(
                a.closing_balance_market
                if a.closing_balance_market is not None
                else a.closing_balance
            )
        )
        for a in pts
    )
    traces: list[dict[str, Any]] = []
    for is_liq, label, color in (
        (True, "Liquid", "#38BDF8"),
        (False, "Illiquid", "#94A3B8"),
    ):
        group = [a for a in pts if bool(a.is_liquid) is is_liq]
        if not group:
            continue
        x_vals = [round(float(a.drawdown_from_peak or 0.0) * 100.0, 2) for a in group]
        y_vals = [
            round(
                float(
                    a.yoy_growth_pct if a.yoy_growth_pct is not None else (a.mom_growth_pct or 0.0)
                )
                * 100.0,
                2,
            )
            for a in group
        ]
        sizes = [
            max(
                10.0,
                min(
                    42.0,
                    (
                        abs(
                            float(
                                a.closing_balance_market
                                if a.closing_balance_market is not None
                                else a.closing_balance
                            )
                        )
                        / max_bal
                    )
                    * 42.0,
                ),
            )
            for a in group
        ]
        custom = [
            {
                "key": a.asset_id,
                "name": a.asset_name,
                "bal": format_currency(
                    float(
                        a.closing_balance_market
                        if a.closing_balance_market is not None
                        else a.closing_balance
                    ),
                    currency_id,
                    compact=True,
                ),
            }
            for a in group
        ]
        traces.append(
            {
                "type": "scatter",
                "mode": "markers",
                "name": label,
                "x": x_vals,
                "y": y_vals,
                "customdata": custom,
                "marker": {
                    "size": sizes,
                    "color": color,
                    "opacity": 0.8,
                    "line": {"width": 1.2, "color": "#0F172A"},
                },
                "hovertemplate": (
                    "<b>%{customdata.name}</b><br>"
                    "Balance: %{customdata.bal}<br>"
                    "Drawdown: %{x:.1f}%<br>"
                    "Growth: %{y:.1f}%<extra></extra>"
                ),
            }
        )
    all_y = [
        round(
            float(a.yoy_growth_pct if a.yoy_growth_pct is not None else (a.mom_growth_pct or 0.0))
            * 100.0,
            2,
        )
        for a in pts
    ]
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=numeric_ticks(all_y, suffix="%"),
            x_axis_type="linear",
        ),
        "config": chart_config("cockpit"),
    }
