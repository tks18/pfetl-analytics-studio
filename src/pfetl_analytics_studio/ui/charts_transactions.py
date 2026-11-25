"""Dark-mode Plotly chart builders for Household Transaction & Classification Explorer.

Owns
----
- Transaction volume by kind, daily cashflow activity timeline, and top account breakdown.

Does not own
------------
- DuckDB queries or `analytics/` services.
"""

from __future__ import annotations

from typing import Any

from pfetl_analytics_studio.ui.charts import (
    chart_config,
    currency_ticks,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.formatting import format_currency


def build_tx_kind_volume_figure(summary: Any, currency_id: str) -> dict[str, Any] | None:
    """Build bar chart comparing Income, Expense, and Transfer volumes."""
    if getattr(summary, "total_count", 0) <= 0:
        return None
    kinds = ["Income", "Expense", "Transfer"]
    vols = [
        round(float(summary.income_total), 2),
        round(float(summary.expense_total), 2),
        round(float(summary.transfer_volume), 2),
    ]
    counts = [int(summary.income_count), int(summary.expense_count), int(summary.transfer_count)]
    return {
        "data": [
            {
                "type": "bar",
                "x": kinds,
                "y": vols,
                "customdata": [
                    f"{format_currency(v, currency_id, compact=True)} ({c} txns)"
                    for v, c in zip(vols, counts, strict=True)
                ],
                "hovertemplate": "%{x}: <b>%{customdata}</b><extra></extra>",
                "marker": {"color": ["#34D399", "#F87171", "#60A5FA"]},
            }
        ],
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(vols, currency_id, include_zero=True),
            x_axis_type="category",
        ),
        "config": chart_config("cockpit"),
    }


def build_tx_daily_activity_figure(summary: Any, currency_id: str) -> dict[str, Any] | None:
    """Build daily timeline of matching Income, Expense, and Transfer activity."""
    pts = getattr(summary, "daily_series", ())
    if not pts:
        return None
    dates = [p.tx_date.isoformat() for p in pts]
    inc_vals = [round(float(p.income_amount), 2) for p in pts]
    exp_vals = [round(float(p.expense_amount), 2) for p in pts]
    tr_vals = [round(float(p.transfer_volume), 2) for p in pts]
    all_vals = [*inc_vals, *exp_vals, *tr_vals]
    return {
        "data": [
            {
                "type": "bar",
                "name": "Income",
                "x": dates,
                "y": inc_vals,
                "marker": {"color": "#34D399"},
            },
            {
                "type": "bar",
                "name": "Expense",
                "x": dates,
                "y": exp_vals,
                "marker": {"color": "#F87171"},
            },
            {
                "type": "bar",
                "name": "Transfers",
                "x": dates,
                "y": tr_vals,
                "marker": {"color": "#60A5FA"},
            },
        ],
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(all_vals, currency_id, include_zero=True),
            barmode="group",
            date_tick_format="%d %b",
        ),
        "config": chart_config("cockpit"),
    }


def build_tx_account_breakdown_figure(summary: Any, currency_id: str) -> dict[str, Any] | None:
    """Build horizontal bar chart of top 10 accounts by matching transaction volume."""
    pts = list(getattr(summary, "account_breakdown", ()))
    if not pts:
        return None
    pts_rev = list(reversed(pts[:10]))
    names = [p.account_name for p in pts_rev]
    vols = [round(float(p.volume), 2) for p in pts_rev]
    return {
        "data": [
            {
                "type": "bar",
                "orientation": "h",
                "x": vols,
                "y": names,
                "customdata": [
                    f"{format_currency(v, currency_id, compact=True)} ({p.tx_count} txns)"
                    for v, p in zip(vols, pts_rev, strict=True)
                ],
                "hovertemplate": "%{y}: <b>%{customdata}</b><extra></extra>",
                "marker": {"color": "#38BDF8"},
            }
        ],
        "layout": studio_plotly_layout(
            x_currency_ticks=currency_ticks(vols, currency_id, include_zero=True),
            horizontal_bars=True,
        ),
        "config": chart_config("cockpit"),
    }
