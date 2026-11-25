"""Dark-theme Plotly figure builders for Household & Income Analytics.

Owns
----
- High-contrast Plotly traces for Income Trend, Active/Passive Composition, Category/Deduction
  Breakdown, Cumulative YTD Trajectory, Passive Income Engine, and Receiving Account Mix.

Does not own
------------
- DuckDB queries or `analytics/` services.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol

from pfetl_analytics_studio.ui.charts import (
    currency_ticks,
    numeric_ticks,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.formatting import format_currency, format_percent


class IncomeMonthlyProto(Protocol):
    """Structural protocol for monthly income composition & smoothing rows."""

    @property
    def month_end(self) -> date:
        """Month-end date."""
        ...

    @property
    def total_income(self) -> float:
        """Total net income."""
        ...

    @property
    def cash_income(self) -> float | None:
        """Cash-realized income."""
        ...

    @property
    def non_cash_income(self) -> float | None:
        """Non-cash income."""
        ...

    @property
    def active_income(self) -> float | None:
        """Active earned income."""
        ...

    @property
    def passive_income(self) -> float | None:
        """Passive dividend and interest income."""
        ...

    @property
    def dividend_income(self) -> float | None:
        """Dividend income."""
        ...

    @property
    def interest_income(self) -> float | None:
        """Interest income."""
        ...

    @property
    def trailing_3m_avg(self) -> float | None:
        """Trailing 3-month average income."""
        ...

    @property
    def budget_income(self) -> float | None:
        """Smoothed budget income target."""
        ...

    @property
    def cumulative_ytd(self) -> float | None:
        """Cumulative YTD income."""
        ...

    @property
    def active_dependency_pct(self) -> float | None:
        """Active income dependency fraction."""
        ...

    @property
    def passive_core_coverage(self) -> float | None:
        """Passive income to core expense coverage fraction."""
        ...


class IncomeCategoryProto(Protocol):
    """Structural protocol for category-level income breakdown rows."""

    @property
    def category_name(self) -> str:
        """Income category label."""
        ...

    @property
    def category_group(self) -> str:
        """Income group label."""
        ...

    @property
    def amount(self) -> float:
        """Period income total."""
        ...

    @property
    def share_pct(self) -> float | None:
        """Share of gross positive income."""
        ...

    @property
    def taxability(self) -> str:
        """Taxability classification."""
        ...


class IncomeAccountProto(Protocol):
    """Structural protocol for receiving account income mix rows."""

    @property
    def account_name(self) -> str:
        """Receiving account name."""
        ...

    @property
    def amount(self) -> float:
        """Total inflow amount."""
        ...

    @property
    def share_pct(self) -> float | None:
        """Share of total inflows."""
        ...

    @property
    def tx_count(self) -> int:
        """Number of transactions."""
        ...


def build_income_trend_figure(
    points: Sequence[IncomeMonthlyProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build monthly Cash vs Non-Cash Income bars with 3M Avg and Budget Target lines."""
    if not points:
        return None
    dates = [pt.month_end.isoformat() for pt in points]
    cash_vals = [
        round(pt.cash_income if pt.cash_income is not None else pt.total_income, 2) for pt in points
    ]
    nc_vals = [round(pt.non_cash_income or 0.0, 2) for pt in points]
    t3m_vals = [
        round(pt.trailing_3m_avg, 2) if pt.trailing_3m_avg is not None else None for pt in points
    ]
    bud_vals = [
        round(pt.budget_income, 2) if pt.budget_income is not None else None for pt in points
    ]
    tot_vals = [round(pt.total_income, 2) for pt in points]

    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": "Cash Income",
            "x": dates,
            "y": cash_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in cash_vals],
            "hovertemplate": "Cash Income: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(59, 130, 246, 0.80)"},
        },
        {
            "type": "bar",
            "name": "Non-Cash (EPF/Int)",
            "x": dates,
            "y": nc_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in nc_vals],
            "hovertemplate": "Non-Cash: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(192, 132, 252, 0.78)"},
        },
    ]
    all_y = [*tot_vals, *cash_vals]
    if any(v is not None for v in t3m_vals):
        all_y.extend(v for v in t3m_vals if v is not None)
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Trailing 3M Avg",
                "x": dates,
                "y": t3m_vals,
                "customdata": [
                    format_currency(v, currency_id, compact=True) if v is not None else "—"
                    for v in t3m_vals
                ],
                "hovertemplate": "3M Avg: <b>%{customdata}</b><extra></extra>",
                "line": {"color": "#34D399", "width": 2.6},
                "marker": {"size": 5},
            }
        )
    if any(v is not None for v in bud_vals):
        all_y.extend(v for v in bud_vals if v is not None)
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Budget Target",
                "x": dates,
                "y": bud_vals,
                "customdata": [
                    format_currency(v, currency_id, compact=True) if v is not None else "—"
                    for v in bud_vals
                ],
                "hovertemplate": "Budget Target: <b>%{customdata}</b><extra></extra>",
                "line": {"color": "#FBBF24", "width": 2.0, "dash": "dash"},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(all_y, currency_id, include_zero=True),
            barmode="stack",
        ),
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_income_composition_figure(
    points: Sequence[IncomeMonthlyProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build Active vs Passive vs Non-Cash stacked bars with Active Dependency % line."""
    if not points:
        return None
    dates = [pt.month_end.isoformat() for pt in points]
    act_vals = [round(pt.active_income or 0.0, 2) for pt in points]
    pass_vals = [round(pt.passive_income or 0.0, 2) for pt in points]
    nc_vals = [round(pt.non_cash_income or 0.0, 2) for pt in points]
    dep_vals = [
        round(pt.active_dependency_pct * 100.0, 2) if pt.active_dependency_pct is not None else None
        for pt in points
    ]
    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": "Active Income",
            "x": dates,
            "y": act_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in act_vals],
            "hovertemplate": "Active: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(56, 189, 248, 0.78)"},
        },
        {
            "type": "bar",
            "name": "Passive Income",
            "x": dates,
            "y": pass_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in pass_vals],
            "hovertemplate": "Passive: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(52, 211, 153, 0.82)"},
        },
        {
            "type": "bar",
            "name": "Non-Cash",
            "x": dates,
            "y": nc_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in nc_vals],
            "hovertemplate": "Non-Cash: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(192, 132, 252, 0.72)"},
        },
    ]
    y2_ticks = numeric_ticks(dep_vals, suffix="%") if any(v is not None for v in dep_vals) else None
    if y2_ticks is not None:
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Active Dep %",
                "x": dates,
                "y": dep_vals,
                "yaxis": "y2",
                "customdata": [
                    format_percent(pt.active_dependency_pct, decimals=1) for pt in points
                ],
                "hovertemplate": "Active Dependency: <b>%{customdata}</b><extra></extra>",
                "line": {"color": "#FBBF24", "width": 2.4},
                "marker": {"size": 5},
            }
        )
    stacked_totals = [a + p + n for a, p, n in zip(act_vals, pass_vals, nc_vals, strict=True)]
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(stacked_totals, currency_id, include_zero=True),
            y2_ticks=y2_ticks,
            barmode="stack",
        ),
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_income_category_bars_figure(
    rows: Sequence[IncomeCategoryProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build horizontal Income Category bars highlighting positive inflows vs deductions."""
    if not rows:
        return None
    pos = [r for r in rows if r.amount > 0][:8]
    neg = [r for r in rows if r.amount < 0][-4:]
    selected = [*pos, *neg]
    ordered = list(reversed(selected))
    labels = [r.category_name for r in ordered]
    amounts = [round(r.amount, 2) for r in ordered]
    colors = ["rgba(248, 113, 113, 0.85)" if v < 0 else "rgba(52, 211, 153, 0.80)" for v in amounts]
    hovers = [
        (
            f"{format_currency(r.amount, currency_id, compact=True, signed=r.amount < 0)} | "
            f"{r.category_group} | {r.taxability.title()}"
            + (f" ({format_percent(r.share_pct, decimals=1)})" if r.share_pct is not None else "")
        )
        for r in ordered
    ]
    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "orientation": "h",
            "name": "Amount",
            "y": labels,
            "x": amounts,
            "customdata": hovers,
            "hovertemplate": "<b>%{y}</b>: %{customdata}<extra></extra>",
            "marker": {"color": colors},
        }
    ]
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            height=340,
            horizontal_bars=True,
            x_currency_ticks=currency_ticks(amounts, currency_id, include_zero=True),
        ),
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_income_ytd_figure(
    points: Sequence[IncomeMonthlyProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build Cumulative YTD Income trajectory across the active window."""
    ytd_pts = [pt for pt in points if pt.cumulative_ytd is not None]
    if not ytd_pts:
        return None
    dates = [pt.month_end.isoformat() for pt in ytd_pts]
    vals = [round(float(pt.cumulative_ytd or 0.0), 2) for pt in ytd_pts]
    traces: list[dict[str, Any]] = [
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": "Cumulative YTD",
            "x": dates,
            "y": vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in vals],
            "hovertemplate": "YTD Income: <b>%{customdata}</b><extra></extra>",
            "line": {"color": "#60A5FA", "width": 3.0},
            "marker": {"size": 6},
        }
    ]
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(vals, currency_id, include_zero=True),
        ),
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_passive_income_engine_figure(
    points: Sequence[IncomeMonthlyProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build Dividends vs Interest bars with Passive / Core Coverage % trend line."""
    if not points:
        return None
    dates = [pt.month_end.isoformat() for pt in points]
    divs = [round(pt.dividend_income or 0.0, 2) for pt in points]
    ints = [round(pt.interest_income or 0.0, 2) for pt in points]
    covs = [
        round(pt.passive_core_coverage * 100.0, 2) if pt.passive_core_coverage is not None else None
        for pt in points
    ]
    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": "Dividends",
            "x": dates,
            "y": divs,
            "customdata": [format_currency(v, currency_id, compact=True) for v in divs],
            "hovertemplate": "Dividends: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(52, 211, 153, 0.82)"},
        },
        {
            "type": "bar",
            "name": "Interest",
            "x": dates,
            "y": ints,
            "customdata": [format_currency(v, currency_id, compact=True) for v in ints],
            "hovertemplate": "Interest: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(56, 189, 248, 0.82)"},
        },
    ]
    y2_ticks = numeric_ticks(covs, suffix="%") if any(v is not None for v in covs) else None
    if y2_ticks is not None:
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Core Cov %",
                "x": dates,
                "y": covs,
                "yaxis": "y2",
                "customdata": [
                    format_percent(pt.passive_core_coverage, decimals=1) for pt in points
                ],
                "hovertemplate": "Passive/Core Cov: <b>%{customdata}</b><extra></extra>",
                "line": {"color": "#C084FC", "width": 2.4},
                "marker": {"size": 5},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks([*divs, *ints], currency_id, include_zero=True),
            y2_ticks=y2_ticks,
            barmode="group",
        ),
        "config": {"displayModeBar": False, "responsive": True},
    }


def build_income_account_mix_figure(
    points: Sequence[IncomeAccountProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build horizontal Receiving Account Mix bar chart from Silver income inflows."""
    if not points:
        return None
    ordered = list(reversed(list(points[:8])))
    labels = [pt.account_name for pt in ordered]
    amounts = [round(pt.amount, 2) for pt in ordered]
    hovers = [
        (
            f"{format_currency(pt.amount, currency_id, compact=True)} | "
            f"{pt.tx_count} txns"
            + (f" ({format_percent(pt.share_pct, decimals=1)})" if pt.share_pct is not None else "")
        )
        for pt in ordered
    ]
    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "orientation": "h",
            "name": "Inflow",
            "y": labels,
            "x": amounts,
            "customdata": hovers,
            "hovertemplate": "<b>%{y}</b>: %{customdata}<extra></extra>",
            "marker": {"color": "rgba(96, 165, 250, 0.82)"},
        }
    ]
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            horizontal_bars=True,
            x_currency_ticks=currency_ticks(amounts, currency_id, include_zero=True),
        ),
        "config": {"displayModeBar": False, "responsive": True},
    }
