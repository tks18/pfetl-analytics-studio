"""Dark-theme Plotly figure builders with collision-free axes and INR formatting.

Owns
----
- Shared Plotly dark terminal layout with synchronized axis ranges & non-overlapping ticks.
- Reusable `chart_panel()` container cleanly separating source caption and dark info tooltip.
- Figure builders for Net Worth & Attribution, Cashflow Efficiency, Deployment vs Redemptions,
  Portfolio Valuation & XIRR, Income/Expense Breakups, Asset Class Mix, and FIRE Trajectory.

Does not own
------------
- DuckDB queries, `analytics/` services, or data-layer imports.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, Protocol

from nicegui import ui

from pfetl_analytics_studio.ui.formatting import currency_symbol, format_currency, format_percent
from pfetl_analytics_studio.ui.theme import CHART_SERIES_COLORS, StudioColor


class NetWorthPointProto(Protocol):
    """Structural protocol for monthly net worth + attribution chart rows."""

    @property
    def month_end(self) -> date:
        """Month-end date."""
        ...

    @property
    def net_worth_market(self) -> float:
        """Market net worth."""
        ...

    @property
    def net_worth_book(self) -> float | None:
        """Book net worth."""
        ...

    @property
    def net_savings_flow(self) -> float | None:
        """Net savings flow."""
        ...

    @property
    def market_return_flow(self) -> float | None:
        """Market return flow."""
        ...


class CashflowPointProto(Protocol):
    """Structural protocol for monthly cashflow & deployment chart rows."""

    @property
    def month_end(self) -> date:
        """Month-end date."""
        ...

    @property
    def income(self) -> float:
        """Monthly income."""
        ...

    @property
    def expense(self) -> float:
        """Monthly expense."""
        ...

    @property
    def net_cashflow(self) -> float:
        """Monthly net cashflow."""
        ...

    @property
    def deployed(self) -> float | None:
        """Gross investment deployed."""
        ...

    @property
    def redeemed(self) -> float | None:
        """Gross investment redeemed."""
        ...

    @property
    def net_investment_flow(self) -> float | None:
        """Net investment flow."""
        ...


class PortfolioPointProto(Protocol):
    """Structural protocol for portfolio valuation and XIRR chart rows."""

    @property
    def closing_date(self) -> date:
        """Portfolio closing date."""
        ...

    @property
    def current_value(self) -> float:
        """Current market value."""
        ...

    @property
    def invested_value(self) -> float | None:
        """Invested cost basis."""
        ...

    @property
    def xirr(self) -> float | None:
        """Portfolio XIRR."""
        ...

    @property
    def bm_xirr(self) -> float | None:
        """Benchmark XIRR."""
        ...


class FirePointProto(Protocol):
    """Structural protocol for FIRE coverage and MC success trajectory rows."""

    @property
    def month_end(self) -> date:
        """Month-end date."""
        ...

    @property
    def fi_coverage(self) -> float | None:
        """FI coverage fraction."""
        ...

    @property
    def success_probability(self) -> float | None:
        """Monte Carlo success fraction."""
        ...


class CategoryBreakupProto(Protocol):
    """Structural protocol for horizontal income/expense category breakup items."""

    @property
    def label(self) -> str:
        """Category or group display label."""
        ...

    @property
    def group(self) -> str:
        """Parent group or spend type."""
        ...

    @property
    def amount(self) -> float:
        """Aggregated monetary total."""
        ...

    @property
    def bucket(self) -> str:
        """Classification tag (Active/Passive or Core/Non-Core)."""
        ...


class AssetClassProto(Protocol):
    """Structural protocol for instrument class mix rows."""

    @property
    def instrument_class(self) -> str:
        """Instrument class name."""
        ...

    @property
    def current_value(self) -> float:
        """Market value."""
        ...

    @property
    def weight(self) -> float | None:
        """Portfolio weight fraction."""
        ...

    @property
    def xirr(self) -> float | None:
        """Class XIRR fraction."""
        ...

    @property
    def active_return(self) -> float | None:
        """Class active return vs benchmark."""
        ...


class WealthAssetSplitProto(Protocol):
    """Structural protocol for asset-group net worth split rows."""

    @property
    def asset_group(self) -> str:
        """Asset group label."""
        ...

    @property
    def market_value(self) -> float:
        """Market closing balance."""
        ...

    @property
    def book_value(self) -> float:
        """Book closing balance."""
        ...

    @property
    def unrealized_pl(self) -> float | None:
        """Market minus book spread."""
        ...

    @property
    def weight(self) -> float | None:
        """Share of total net worth."""
        ...


def _nav_cb(route: str) -> Callable[[], None]:
    """Return zero-arg route navigation closure cleanly."""
    return lambda: ui.navigate.to(route)


def _currency_ticks(
    values: Sequence[float],
    currency_id: str,
    *,
    include_zero: bool = False,
    steps: int = 5,
) -> tuple[list[float], list[str]] | None:
    """Compute evenly spaced tick values & locale INR (`Cr`/`L`) labels covering full range."""
    if not values:
        return None
    low = min(values)
    high = max(values)
    if include_zero:
        low = min(low, 0.0)
        high = max(high, 0.0)
    if high == low:
        pad = abs(high) * 0.1 or 1.0
        low -= pad
        high += pad
    span = high - low
    low_padded = low - span * 0.08 if (low < 0 or not include_zero) else 0.0
    high_padded = high + span * 0.08
    if high_padded <= low_padded:
        return None
    tick_vals = [
        round(low_padded + (high_padded - low_padded) * i / (steps - 1), 2) for i in range(steps)
    ]
    tick_text = [format_currency(v, currency_id, compact=True, decimals=1) for v in tick_vals]
    return tick_vals, tick_text


def _numeric_ticks(
    values: Sequence[float | None],
    *,
    suffix: str = "%",
    steps: int = 5,
) -> tuple[list[float], list[str]] | None:
    """Compute evenly spaced numeric axis ticks matching explicit axis range."""
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    low = min(clean)
    high = max(clean)
    if high == low:
        low -= 1.0
        high += 1.0
    span = high - low
    low_p = low - span * 0.10
    high_p = high + span * 0.10
    vals = [round(low_p + (high_p - low_p) * i / (steps - 1), 2) for i in range(steps)]
    labels = [f"{v:.1f}{suffix}" for v in vals]
    return vals, labels


def studio_plotly_layout(
    *,
    height: int = 320,
    y_prefix: str = "",
    y_suffix: str = "",
    y_ticks: tuple[list[float], list[str]] | None = None,
    y2_suffix: str = "",
    y2_ticks: tuple[list[float], list[str]] | None = None,
    barmode: str | None = None,
    date_tick_format: str = "%b '%y",
    horizontal_bars: bool = False,
    x_currency_ticks: tuple[list[float], list[str]] | None = None,
) -> dict[str, Any]:
    """Return modern dark Plotly layout with matched ranges and non-overlapping axes."""
    yaxis: dict[str, Any] = {
        "showgrid": not horizontal_bars,
        "gridcolor": "rgba(255, 255, 255, 0.08)",
        "zeroline": False,
        "automargin": True,
        "ticklabelstandoff": 6,
        "tickfont": {"color": StudioColor.TEXT.value, "size": 11.5},
    }
    if not horizontal_bars:
        if y_ticks is not None:
            yaxis["tickmode"] = "array"
            yaxis["tickvals"] = y_ticks[0]
            yaxis["ticktext"] = y_ticks[1]
            yaxis["range"] = [y_ticks[0][0], y_ticks[0][-1]]
        else:
            yaxis["tickprefix"] = y_prefix
            yaxis["ticksuffix"] = y_suffix
            yaxis["nticks"] = 5

    xaxis: dict[str, Any] = {
        "showgrid": horizontal_bars,
        "gridcolor": "rgba(255, 255, 255, 0.08)",
        "automargin": True,
        "tickangle": 0,
        "linecolor": "rgba(255, 255, 255, 0.16)",
        "tickfont": {"color": StudioColor.TEXT.value, "size": 11.5},
    }
    if horizontal_bars:
        if x_currency_ticks is not None:
            xaxis["tickmode"] = "array"
            xaxis["tickvals"] = x_currency_ticks[0]
            xaxis["ticktext"] = x_currency_ticks[1]
            xaxis["range"] = [x_currency_ticks[0][0], x_currency_ticks[0][-1]]
    else:
        xaxis["type"] = "date"
        xaxis["nticks"] = 6
        xaxis["tickformat"] = date_tick_format

    has_y2 = bool(y2_suffix or y2_ticks is not None)
    layout: dict[str, Any] = {
        "height": height,
        "margin": {"l": 20, "r": 24 if not has_y2 else 68, "t": 44, "b": 40},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"color": StudioColor.TEXT.value, "size": 12},
        "hovermode": "closest" if horizontal_bars else "x unified",
        "hoverlabel": {
            "bgcolor": "#0F172A",
            "bordercolor": "rgba(148, 163, 184, 0.42)",
            "font": {"color": StudioColor.TEXT.value, "size": 12},
        },
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.03,
            "xanchor": "left",
            "x": 0.0,
            "font": {"size": 12, "color": StudioColor.TEXT.value},
        },
        "xaxis": xaxis,
        "yaxis": yaxis,
    }
    if barmode:
        layout["barmode"] = barmode
    if has_y2:
        yaxis2: dict[str, Any] = {
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "zeroline": False,
            "automargin": True,
            "ticklabelstandoff": 6,
            "tickfont": {"color": StudioColor.MUTED.value, "size": 11},
        }
        if y2_ticks is not None:
            yaxis2["tickmode"] = "array"
            yaxis2["tickvals"] = y2_ticks[0]
            yaxis2["ticktext"] = y2_ticks[1]
            yaxis2["range"] = [y2_ticks[0][0], y2_ticks[0][-1]]
        else:
            yaxis2["ticksuffix"] = y2_suffix
            yaxis2["nticks"] = 5
        layout["yaxis2"] = yaxis2
    return layout


def chart_panel(
    title: str,
    subtitle: str,
    figure: dict[str, Any] | None,
    *,
    source_caption: str,
    drill_route: str | None = None,
    empty_message: str = "No observations available inside this horizon.",
) -> ui.card:
    """Render a spacious dark card wrapping a high-contrast Plotly chart cleanly."""
    card = ui.card().classes("studio-card w-full p-5 rounded-2xl gap-3")
    with card:
        with ui.row().classes("w-full items-start justify-between gap-2 no-wrap"):
            with ui.column().classes("gap-0.5 min-w-0 flex-1"):
                ui.label(title).classes("text-base font-semibold").style(
                    f"color: {StudioColor.TEXT}"
                )
                ui.label(subtitle).classes("text-xs leading-snug").style(
                    f"color: {StudioColor.MUTED}"
                )
            with ui.row().classes("items-center gap-2 shrink-0"):
                if drill_route:
                    ui.button(
                        "Explore",
                        icon="north_east",
                        on_click=_nav_cb(drill_route),
                    ).props("flat dense size=sm no-caps").style(
                        f"color: {StudioColor.ACC_HOV}"
                    ).classes("px-2 text-xs font-semibold")

        if figure and figure.get("data"):
            ui.plotly(figure).classes("w-full")
        else:
            with (
                ui.column()
                .classes("w-full items-center justify-center rounded-xl py-14")
                .style("background-color: rgba(15, 23, 42, 0.55);")
            ):
                ui.icon("show_chart", size="30px").style(f"color: {StudioColor.DIM}")
                ui.label(empty_message).classes("text-xs mt-1.5").style(
                    f"color: {StudioColor.MUTED}"
                )

        with (
            ui.row()
            .classes("w-full items-center justify-between pt-2.5 text-xs font-mono")
            .style(
                "border-top: 1px solid rgba(255, 255, 255, 0.08); color: " + StudioColor.DIM.value
            )
        ):
            ui.label(source_caption)
            with (
                ui.icon("info_outline", size="15px")
                .classes("cursor-help")
                .style(f"color: {StudioColor.DIM}")
            ):
                ui.tooltip(f"{title}\n{source_caption}").classes("studio-tooltip").props(
                    "delay=200"
                )

    return card


def build_net_worth_attribution_figure(
    points: Sequence[NetWorthPointProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build Net Worth trend lines (Market & Book) + Net Savings / Market Return bars."""
    if not points:
        return None
    dates = [pt.month_end.isoformat() for pt in points]
    nw_vals = [round(pt.net_worth_market, 2) for pt in points]
    book_vals = [
        round(pt.net_worth_book, 2) if pt.net_worth_book is not None else None for pt in points
    ]
    sav_vals = [round(pt.net_savings_flow or 0.0, 2) for pt in points]
    mkt_vals = [round(pt.market_return_flow or 0.0, 2) for pt in points]

    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": "Net Savings",
            "x": dates,
            "y": sav_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in sav_vals],
            "hovertemplate": "Net Savings: <b>%{customdata}</b><extra></extra>",
            "yaxis": "y2",
            "marker": {"color": "rgba(52, 211, 153, 0.58)"},
        },
        {
            "type": "bar",
            "name": "Market Return",
            "x": dates,
            "y": mkt_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in mkt_vals],
            "hovertemplate": "Market Return: <b>%{customdata}</b><extra></extra>",
            "yaxis": "y2",
            "marker": {"color": "rgba(56, 189, 248, 0.58)"},
        },
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": "Market Net Worth",
            "x": dates,
            "y": nw_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in nw_vals],
            "hovertemplate": "Market Net Worth: <b>%{customdata}</b><extra></extra>",
            "line": {"color": CHART_SERIES_COLORS[0], "width": 3},
            "marker": {"size": 6},
        },
    ]
    all_nw_vals = list(nw_vals)
    if any(v is not None for v in book_vals):
        all_nw_vals.extend(v for v in book_vals if v is not None)
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Book Net Worth",
                "x": dates,
                "y": book_vals,
                "customdata": [
                    format_currency(v, currency_id, compact=True) if v is not None else "—"
                    for v in book_vals
                ],
                "hovertemplate": "Book Net Worth: <b>%{customdata}</b><extra></extra>",
                "line": {"color": "#CBD5E1", "width": 2, "dash": "dot"},
            }
        )
    layout = studio_plotly_layout(
        y_ticks=_currency_ticks(all_nw_vals, currency_id),
        y2_ticks=_currency_ticks([*sav_vals, *mkt_vals], currency_id, include_zero=True),
        barmode="relative",
    )
    return {
        "data": traces,
        "layout": layout,
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_cashflow_efficiency_figure(
    points: Sequence[CashflowPointProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build monthly Income vs Expense bar comparison with Net Cashflow trend line."""
    if not points:
        return None
    dates = [pt.month_end.isoformat() for pt in points]
    incomes = [round(pt.income, 2) for pt in points]
    expenses = [round(pt.expense, 2) for pt in points]
    nets = [round(pt.net_cashflow, 2) for pt in points]

    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": "Income",
            "x": dates,
            "y": incomes,
            "customdata": [format_currency(v, currency_id, compact=True) for v in incomes],
            "hovertemplate": "Income: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(96, 165, 250, 0.78)"},
        },
        {
            "type": "bar",
            "name": "Expense",
            "x": dates,
            "y": expenses,
            "customdata": [format_currency(v, currency_id, compact=True) for v in expenses],
            "hovertemplate": "Expense: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(251, 191, 36, 0.78)"},
        },
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": "Net Cashflow",
            "x": dates,
            "y": nets,
            "customdata": [format_currency(v, currency_id, compact=True) for v in nets],
            "hovertemplate": "Net Cashflow: <b>%{customdata}</b><extra></extra>",
            "line": {"color": "#34D399", "width": 2.5},
            "marker": {"size": 6},
        },
    ]
    layout = studio_plotly_layout(
        y_ticks=_currency_ticks([*incomes, *expenses, *nets], currency_id, include_zero=True),
        barmode="group",
    )
    return {
        "data": traces,
        "layout": layout,
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_deployment_flow_figure(
    points: Sequence[CashflowPointProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build monthly Investment Deployed vs Redeemed bars and Net Investment Flow line."""
    valid = [pt for pt in points if pt.deployed is not None or pt.redeemed is not None]
    if not valid:
        return None
    dates = [pt.month_end.isoformat() for pt in valid]
    dep = [round(pt.deployed or 0.0, 2) for pt in valid]
    red_neg = [-abs(round(pt.redeemed or 0.0, 2)) for pt in valid]
    net_inv = [round(pt.net_investment_flow or 0.0, 2) for pt in valid]

    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": "Deployed",
            "x": dates,
            "y": dep,
            "customdata": [format_currency(v, currency_id, compact=True) for v in dep],
            "hovertemplate": "Deployed: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(52, 211, 153, 0.75)"},
        },
        {
            "type": "bar",
            "name": "Redeemed",
            "x": dates,
            "y": red_neg,
            "customdata": [format_currency(abs(v), currency_id, compact=True) for v in red_neg],
            "hovertemplate": "Redeemed: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(248, 113, 113, 0.72)"},
        },
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": "Net Invest Flow",
            "x": dates,
            "y": net_inv,
            "customdata": [format_currency(v, currency_id, compact=True) for v in net_inv],
            "hovertemplate": "Net Invest Flow: <b>%{customdata}</b><extra></extra>",
            "line": {"color": CHART_SERIES_COLORS[0], "width": 2.6},
            "marker": {"size": 6},
        },
    ]
    layout = studio_plotly_layout(
        y_ticks=_currency_ticks([*dep, *red_neg, *net_inv], currency_id, include_zero=True),
        barmode="relative",
    )
    return {
        "data": traces,
        "layout": layout,
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_portfolio_return_figure(
    points: Sequence[PortfolioPointProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build Portfolio Market Value vs Cost Basis curve alongside XIRR & Benchmark XIRR."""
    if not points:
        return None
    _ = currency_symbol(currency_id)
    dates = [pt.closing_date.isoformat() for pt in points]
    cur_vals = [round(pt.current_value, 2) for pt in points]
    inv_vals = [round(pt.invested_value or 0.0, 2) for pt in points]
    xirr_vals = [round(pt.xirr * 100.0, 2) if pt.xirr is not None else None for pt in points]
    bm_vals = [round(pt.bm_xirr * 100.0, 2) if pt.bm_xirr is not None else None for pt in points]

    traces: list[dict[str, Any]] = [
        {
            "type": "scatter",
            "mode": "lines",
            "name": "Market Value",
            "x": dates,
            "y": cur_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in cur_vals],
            "hovertemplate": "Market Value: <b>%{customdata}</b><extra></extra>",
            "line": {"color": CHART_SERIES_COLORS[0], "width": 2.8},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "name": "Cost Basis",
            "x": dates,
            "y": inv_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in inv_vals],
            "hovertemplate": "Cost Basis: <b>%{customdata}</b><extra></extra>",
            "line": {"color": StudioColor.MUTED.value, "width": 1.8, "dash": "dot"},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "name": "XIRR %",
            "x": dates,
            "y": xirr_vals,
            "customdata": [format_percent(pt.xirr, decimals=2) for pt in points],
            "hovertemplate": "Portfolio XIRR: <b>%{customdata}</b><extra></extra>",
            "yaxis": "y2",
            "line": {"color": "#34D399", "width": 2.0},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "name": "Benchmark XIRR %",
            "x": dates,
            "y": bm_vals,
            "customdata": [format_percent(pt.bm_xirr, decimals=2) for pt in points],
            "hovertemplate": "Benchmark XIRR: <b>%{customdata}</b><extra></extra>",
            "yaxis": "y2",
            "line": {"color": "#FBBF24", "width": 1.6, "dash": "dash"},
        },
    ]
    tick_fmt = "%d %b" if len(dates) <= 120 else "%b '%y"
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=_currency_ticks([*cur_vals, *inv_vals], currency_id),
            y2_ticks=_numeric_ticks([*xirr_vals, *bm_vals], suffix="%"),
            date_tick_format=tick_fmt,
        ),
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_category_breakup_figure(
    points: Sequence[CategoryBreakupProto],
    *,
    currency_id: str = "INR_INR",
    bar_color: str = "rgba(96, 165, 250, 0.82)",
) -> dict[str, Any] | None:
    """Build ranked horizontal bar breakup for Income or Expense categories."""
    if not points:
        return None
    ordered = list(reversed(list(points)))
    labels = [pt.label for pt in ordered]
    amounts = [round(pt.amount, 2) for pt in ordered]
    texts = [
        f"{format_currency(pt.amount, currency_id, compact=True)} ({pt.bucket})" for pt in ordered
    ]
    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "orientation": "h",
            "name": "Amount",
            "y": labels,
            "x": amounts,
            "customdata": texts,
            "hovertemplate": "<b>%{y}</b>: %{customdata}<extra></extra>",
            "marker": {"color": bar_color},
        }
    ]
    layout = studio_plotly_layout(
        horizontal_bars=True,
        x_currency_ticks=_currency_ticks(amounts, currency_id, include_zero=True),
    )
    return {
        "data": traces,
        "layout": layout,
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_asset_class_mix_figure(
    points: Sequence[AssetClassProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build horizontal Asset Class valuation chart showing weight, XIRR, and Active Return."""
    if not points:
        return None
    ordered = list(reversed(list(points)))
    labels = [pt.instrument_class for pt in ordered]
    amounts = [round(pt.current_value, 2) for pt in ordered]
    hover_texts = [
        (
            f"{format_currency(pt.current_value, currency_id, compact=True)} | "
            f"Wt {format_percent(pt.weight, decimals=1)} | "
            f"XIRR {format_percent(pt.xirr, decimals=1)} | "
            f"Active {format_percent(pt.active_return, decimals=1, signed=True)}"
        )
        for pt in ordered
    ]
    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "orientation": "h",
            "name": "Market Value",
            "y": labels,
            "x": amounts,
            "customdata": hover_texts,
            "hovertemplate": "<b>%{y}</b>: %{customdata}<extra></extra>",
            "marker": {"color": "rgba(192, 132, 252, 0.78)"},
        }
    ]
    layout = studio_plotly_layout(
        horizontal_bars=True,
        x_currency_ticks=_currency_ticks(amounts, currency_id, include_zero=True),
    )
    return {
        "data": traces,
        "layout": layout,
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_fire_trajectory_figure(points: Sequence[FirePointProto]) -> dict[str, Any] | None:
    """Build FI Coverage % and Monte Carlo Success Probability trajectory chart."""
    if not points:
        return None
    dates = [pt.month_end.isoformat() for pt in points]
    cov_vals = [
        round(pt.fi_coverage * 100.0, 2) if pt.fi_coverage is not None else None for pt in points
    ]
    prob_vals = [
        round(pt.success_probability * 100.0, 2) if pt.success_probability is not None else None
        for pt in points
    ]
    traces: list[dict[str, Any]] = [
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": "FI Coverage %",
            "x": dates,
            "y": cov_vals,
            "customdata": [format_percent(pt.fi_coverage, decimals=1) for pt in points],
            "hovertemplate": "FI Coverage: <b>%{customdata}</b><extra></extra>",
            "line": {"color": "#C084FC", "width": 2.6},
            "marker": {"size": 6},
        },
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": "MC Success %",
            "x": dates,
            "y": prob_vals,
            "customdata": [format_percent(pt.success_probability, decimals=1) for pt in points],
            "hovertemplate": "MC Success: <b>%{customdata}</b><extra></extra>",
            "line": {"color": "#34D399", "width": 2.2, "dash": "dot"},
            "marker": {"size": 5},
        },
    ]
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=_numeric_ticks([*cov_vals, *prob_vals], suffix="%"),
        ),
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_wealth_asset_split_figure(
    points: Sequence[WealthAssetSplitProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build horizontal Market vs Book Net Worth split chart from `wealth_asset_breakdown`."""
    if not points:
        return None
    top_points = list(points[:10])
    ordered = list(reversed(top_points))
    labels = [pt.asset_group for pt in ordered]
    mkt_vals = [round(pt.market_value, 2) for pt in ordered]
    book_vals = [round(pt.book_value, 2) for pt in ordered]
    mkt_colors = [
        "rgba(248, 113, 113, 0.85)" if v < 0 else "rgba(56, 189, 248, 0.85)" for v in mkt_vals
    ]
    hover_mkt = [
        (
            f"{format_currency(pt.market_value, currency_id, compact=True)} | "
            f"Share {format_percent(pt.weight, decimals=1)} | "
            f"Spread {format_currency(pt.unrealized_pl or 0.0, currency_id, compact=True, signed=True)}"
        )
        for pt in ordered
    ]
    hover_book = [format_currency(pt.book_value, currency_id, compact=True) for pt in ordered]
    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "orientation": "h",
            "name": "Market Value",
            "y": labels,
            "x": mkt_vals,
            "customdata": hover_mkt,
            "hovertemplate": "<b>%{y}</b> (Market): %{customdata}<extra></extra>",
            "marker": {"color": mkt_colors},
        },
        {
            "type": "bar",
            "orientation": "h",
            "name": "Book Value",
            "y": labels,
            "x": book_vals,
            "customdata": hover_book,
            "hovertemplate": "<b>%{y}</b> (Book): %{customdata}<extra></extra>",
            "marker": {"color": "rgba(148, 163, 184, 0.45)"},
        },
    ]
    layout = studio_plotly_layout(
        height=340,
        horizontal_bars=True,
        barmode="group",
        x_currency_ticks=_currency_ticks([*mkt_vals, *book_vals], currency_id, include_zero=True),
    )
    return {
        "data": traces,
        "layout": layout,
        "config": {"displayModeBar": False, "responsive": True},
    }
