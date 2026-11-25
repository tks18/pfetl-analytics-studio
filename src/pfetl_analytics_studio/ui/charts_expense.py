"""Dark-theme Plotly figure builders for Household Expense Analytics.

Owns
----
- High-contrast Plotly traces for Core vs Non-Core Trend, Nominal vs Inflation-Adjusted Core,
  Cash vs Non-Cash Spend, Expense Group Treemap, Month × Group Spend Heatmap, Category Bars,
  and Payment Account Mix.

Does not own
------------
- DuckDB queries or `analytics/` services.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol

from pfetl_analytics_studio.ui.charts import (
    TreemapNode,
    build_heatmap_figure,
    build_treemap_figure,
    chart_config,
    currency_ticks,
    numeric_ticks,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.formatting import format_currency, format_percent
from pfetl_analytics_studio.ui.theme import CHART_ROLE_COLORS, ChartColorRole


class ExpenseMonthlyProto(Protocol):
    """Structural protocol for monthly expense composition & real core rows."""

    @property
    def month_end(self) -> date:
        """Month-end date."""
        ...

    @property
    def total_expense(self) -> float:
        """Total monthly expense."""
        ...

    @property
    def core_expense(self) -> float | None:
        """Core essential expense."""
        ...

    @property
    def non_core_expense(self) -> float | None:
        """Non-core lifestyle expense."""
        ...

    @property
    def cash_expense(self) -> float | None:
        """Cash/card settled expense."""
        ...

    @property
    def non_cash_expense(self) -> float | None:
        """Non-cash amortization/depreciation expense."""
        ...

    @property
    def inflation_adjusted_core(self) -> float | None:
        """Inflation-adjusted core expense."""
        ...

    @property
    def real_core_growth_pct(self) -> float | None:
        """Real core expense growth fraction."""
        ...

    @property
    def trailing_3m_avg(self) -> float | None:
        """Trailing 3-month average spend."""
        ...


class ExpenseCategoryProto(Protocol):
    """Structural protocol for category-level expense breakdown rows."""

    @property
    def category_id(self) -> str:
        """Category ID."""
        ...

    @property
    def category_name(self) -> str:
        """Category name."""
        ...

    @property
    def category_group(self) -> str:
        """Expense group name."""
        ...

    @property
    def spend_type(self) -> str:
        """Fixed or Variable."""
        ...

    @property
    def is_core(self) -> bool:
        """Core flag."""
        ...

    @property
    def is_discretionary(self) -> bool:
        """Discretionary flag."""
        ...

    @property
    def amount(self) -> float:
        """Period spend total."""
        ...

    @property
    def share_pct(self) -> float | None:
        """Share of gross positive spend."""
        ...


class ExpenseAccountProto(Protocol):
    """Structural protocol for payment/funding account mix rows."""

    @property
    def account_name(self) -> str:
        """Funding account name."""
        ...

    @property
    def amount(self) -> float:
        """Total spend amount."""
        ...

    @property
    def share_pct(self) -> float | None:
        """Share of total Silver expense."""
        ...

    @property
    def tx_count(self) -> int:
        """Transaction count."""
        ...


class ExpenseHeatmapProto(Protocol):
    """Structural protocol for Month × Expense Group heatmap matrix."""

    @property
    def months(self) -> tuple[str, ...]:
        """Ordered year-month labels."""
        ...

    @property
    def groups(self) -> tuple[str, ...]:
        """Ordered expense group labels."""
        ...

    @property
    def matrix(self) -> tuple[tuple[float | None, ...], ...]:
        """2D spend matrix."""
        ...


def build_expense_core_trend_figure(
    points: Sequence[ExpenseMonthlyProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build stacked Core vs Non-Core Expense bars with Trailing 3M Avg trend line."""
    if not points:
        return None
    dates = [pt.month_end.isoformat() for pt in points]
    core_vals = [round(pt.core_expense or 0.0, 2) for pt in points]
    nc_vals = [round(pt.non_core_expense or 0.0, 2) for pt in points]
    t3m_vals = [
        round(pt.trailing_3m_avg, 2) if pt.trailing_3m_avg is not None else None for pt in points
    ]
    tot_vals = [round(pt.total_expense, 2) for pt in points]

    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": "Core Essentials",
            "x": dates,
            "y": core_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in core_vals],
            "hovertemplate": "Core: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(59, 130, 246, 0.80)"},
        },
        {
            "type": "bar",
            "name": "Non-Core & Lifestyle",
            "x": dates,
            "y": nc_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in nc_vals],
            "hovertemplate": "Non-Core: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(251, 191, 36, 0.82)"},
        },
    ]
    all_y = [*tot_vals, *core_vals]
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
                "line": {"color": CHART_ROLE_COLORS[ChartColorRole.NEGATIVE], "width": 2.4},
                "marker": {"size": 5},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(all_y, currency_id, include_zero=True),
            barmode="stack",
        ),
        "config": chart_config("cockpit"),
    }


def build_expense_real_vs_nominal_figure(
    points: Sequence[ExpenseMonthlyProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build Nominal vs Inflation-Adjusted Core Expense lines with Real Growth % overlay."""
    real_pts = [pt for pt in points if pt.inflation_adjusted_core is not None]
    if not real_pts:
        return None
    dates = [pt.month_end.isoformat() for pt in real_pts]
    nom_vals = [round(pt.core_expense or 0.0, 2) for pt in real_pts]
    real_vals = [round(float(pt.inflation_adjusted_core or 0.0), 2) for pt in real_pts]
    growth_vals = [
        round(pt.real_core_growth_pct * 100.0, 2) if pt.real_core_growth_pct is not None else None
        for pt in real_pts
    ]

    traces: list[dict[str, Any]] = [
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": "Nominal Core",
            "x": dates,
            "y": nom_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in nom_vals],
            "hovertemplate": "Nominal Core: <b>%{customdata}</b><extra></extra>",
            "line": {"color": CHART_ROLE_COLORS[ChartColorRole.ACTUAL], "width": 2.6},
            "marker": {"size": 5},
        },
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": "Inflation-Adjusted Core",
            "x": dates,
            "y": real_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in real_vals],
            "hovertemplate": "Real Core: <b>%{customdata}</b><extra></extra>",
            "line": {
                "color": CHART_ROLE_COLORS[ChartColorRole.POSITIVE],
                "width": 2.4,
                "dash": "dot",
            },
            "marker": {"size": 5},
        },
    ]
    y2_ticks = (
        numeric_ticks(growth_vals, suffix="%") if any(v is not None for v in growth_vals) else None
    )
    if y2_ticks is not None:
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Real Core Growth %",
                "x": dates,
                "y": growth_vals,
                "yaxis": "y2",
                "customdata": [
                    format_percent(pt.real_core_growth_pct, decimals=1, signed=True)
                    for pt in real_pts
                ],
                "hovertemplate": "Real Growth: <b>%{customdata}</b><extra></extra>",
                "line": {"color": CHART_ROLE_COLORS[ChartColorRole.TARGET], "width": 2.0},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks([*nom_vals, *real_vals], currency_id),
            y2_ticks=y2_ticks,
        ),
        "config": chart_config("cockpit"),
    }


def build_expense_cash_vs_noncash_figure(
    points: Sequence[ExpenseMonthlyProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build monthly Cash Spend vs Non-Cash Amortization/Depreciation stacked bars."""
    if not points:
        return None
    dates = [pt.month_end.isoformat() for pt in points]
    cash_vals = [
        round(pt.cash_expense if pt.cash_expense is not None else pt.total_expense, 2)
        for pt in points
    ]
    nc_vals = [round(pt.non_cash_expense or 0.0, 2) for pt in points]
    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": "Cash / Card Spend",
            "x": dates,
            "y": cash_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in cash_vals],
            "hovertemplate": "Cash Spend: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(56, 189, 248, 0.80)"},
        },
        {
            "type": "bar",
            "name": "Non-Cash (Amort / Dep)",
            "x": dates,
            "y": nc_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in nc_vals],
            "hovertemplate": "Non-Cash: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "rgba(192, 132, 252, 0.78)"},
        },
    ]
    totals = [c + n for c, n in zip(cash_vals, nc_vals, strict=True)]
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(totals, currency_id, include_zero=True),
            barmode="stack",
        ),
        "config": chart_config("cockpit"),
    }


def build_expense_treemap_figure(
    category_rows: Sequence[ExpenseCategoryProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build Expense Group → Category hierarchy treemap for positive period spend."""
    pos_rows = [r for r in category_rows if r.amount > 0]
    if not pos_rows:
        return None
    total_pos = sum(r.amount for r in pos_rows)
    grp_sums: dict[str, float] = {}
    for r in pos_rows:
        grp_sums[r.category_group] = grp_sums.get(r.category_group, 0.0) + r.amount

    nodes: list[TreemapNode] = [
        TreemapNode(
            node_id="exp_root",
            label="Total Spend",
            parent_id="",
            value=total_pos,
            share_pct=1.0,
            key="ALL",
        )
    ]
    for grp, gval in sorted(grp_sums.items(), key=lambda kv: kv[1], reverse=True):
        nodes.append(
            TreemapNode(
                node_id=f"grp:{grp}",
                label=grp,
                parent_id="exp_root",
                value=gval,
                share_pct=(gval / total_pos) if total_pos > 0 else None,
                key=grp,
            )
        )
    for r in pos_rows:
        nodes.append(
            TreemapNode(
                node_id=f"cat:{r.category_group}:{r.category_id}",
                label=r.category_name,
                parent_id=f"grp:{r.category_group}",
                value=r.amount,
                share_pct=r.share_pct,
                key=r.category_name,
            )
        )
    return build_treemap_figure(nodes, currency_id=currency_id, height=340)


def build_expense_heatmap_figure(
    heatmap: ExpenseHeatmapProto,
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build Month × Expense Group spend intensity heatmap."""
    if not heatmap.months or not heatmap.groups or not heatmap.matrix:
        return None
    return build_heatmap_figure(
        heatmap.months,
        heatmap.groups,
        heatmap.matrix,
        currency_id=currency_id,
        is_currency=True,
        height=340,
        interaction_mode="analytical",
    )


def build_expense_category_bars_figure(
    rows: Sequence[ExpenseCategoryProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build ranked horizontal Expense Category bars with drill keys in `customdata`."""
    if not rows:
        return None
    pos = [r for r in rows if r.amount > 0][:10]
    neg = [r for r in rows if r.amount < 0][-2:]
    ordered = list(reversed([*pos, *neg]))
    labels = [r.category_name for r in ordered]
    amounts = [round(r.amount, 2) for r in ordered]
    colors = [
        "rgba(52, 211, 153, 0.80)"
        if r.amount < 0
        else ("rgba(59, 130, 246, 0.82)" if r.is_core else "rgba(251, 191, 36, 0.82)")
        for r in ordered
    ]
    customdata = [
        {
            "key": r.category_name,
            "group": r.category_group,
            "display": (
                f"{format_currency(r.amount, currency_id, compact=True, signed=r.amount < 0)} | "
                f"{r.category_group} · {'Core' if r.is_core else 'Non-Core'} · {r.spend_type}"
                + (
                    f" ({format_percent(r.share_pct, decimals=1)})"
                    if r.share_pct is not None
                    else ""
                )
            ),
        }
        for r in ordered
    ]
    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "orientation": "h",
            "name": "Spend",
            "y": labels,
            "x": amounts,
            "customdata": customdata,
            "hovertemplate": "<b>%{y}</b>: %{customdata.display}<extra></extra>",
            "marker": {"color": colors},
        }
    ]
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            height=350,
            horizontal_bars=True,
            x_currency_ticks=currency_ticks(amounts, currency_id, include_zero=True),
        ),
        "config": chart_config("cockpit"),
    }


def build_expense_account_mix_figure(
    points: Sequence[ExpenseAccountProto],
    *,
    currency_id: str = "INR_INR",
) -> dict[str, Any] | None:
    """Build horizontal Payment Source / Funding Account bar chart from Silver expenses."""
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
            "name": "Outflow",
            "y": labels,
            "x": amounts,
            "customdata": hovers,
            "hovertemplate": "<b>%{y}</b>: %{customdata}<extra></extra>",
            "marker": {"color": "rgba(248, 113, 113, 0.80)"},
        }
    ]
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            horizontal_bars=True,
            x_currency_ticks=currency_ticks(amounts, currency_id, include_zero=True),
        ),
        "config": chart_config("cockpit"),
    }
