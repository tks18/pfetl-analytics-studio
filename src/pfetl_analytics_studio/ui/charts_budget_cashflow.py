"""Dark-mode Plotly chart builders for Household Budget & Cashflow Analytics.

Owns
----
- Target vs Actual grouped bars, monthly budget variance trend, emergency runway trajectory,
  cashflow statement waterfall bridge, monthly activity stack, and health flag matrix.

Does not own
------------
- DuckDB queries or `analytics/` services.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pfetl_analytics_studio.ui.charts import (
    WaterfallStep,
    build_heatmap_figure,
    build_waterfall_figure,
    chart_config,
    currency_ticks,
    numeric_ticks,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.formatting import format_currency
from pfetl_analytics_studio.ui.theme import StudioColor


def build_budget_target_vs_actual_figure(
    snapshot: Any,
) -> dict[str, Any] | None:
    """Build grouped bar chart comparing Budget Target vs Actual across all 4 buckets."""
    if getattr(snapshot, "state", "") != "ready" or not getattr(snapshot, "monthly_series", ()):
        return None
    buckets = ["Income", "Core Expense", "Non-Core Expense", "Investment", "Surplus Buffer"]
    targets = [
        float(snapshot.total_budget_income),
        float(snapshot.total_budget_core),
        float(snapshot.total_budget_non_core),
        float(snapshot.total_budget_investment),
        float(getattr(snapshot, "total_budget_buffer", 0.0)),
    ]
    actuals = [
        float(snapshot.total_actual_income),
        float(snapshot.total_actual_core),
        float(snapshot.total_actual_non_core),
        float(snapshot.total_actual_investment),
        float(getattr(snapshot, "total_actual_savings", 0.0)),
    ]
    if not any(abs(v) > 0 for v in (*targets, *actuals)):
        return None

    cid = str(snapshot.currency_id)
    return {
        "data": [
            {
                "type": "bar",
                "name": "Budget Target",
                "x": buckets,
                "y": targets,
                "customdata": [format_currency(v, cid, compact=True) for v in targets],
                "hovertemplate": "%{x}<br>Target: <b>%{customdata}</b><extra></extra>",
                "marker": {"color": StudioColor.DIM.value, "opacity": 0.72},
            },
            {
                "type": "bar",
                "name": "Actual",
                "x": buckets,
                "y": actuals,
                "customdata": [format_currency(v, cid, compact=True) for v in actuals],
                "hovertemplate": "%{x}<br>Actual: <b>%{customdata}</b><extra></extra>",
                "marker": {"color": StudioColor.ACC_HOV.value},
            },
        ],
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks([*targets, *actuals], cid, include_zero=True),
            x_axis_type="category",
            barmode="group",
        ),
        "config": chart_config("cockpit"),
    }


def build_budget_allocation_trend_figure(
    series: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build monthly Core, Non-Core, and Investment actuals vs budget target lines."""
    if not series:
        return None
    dates = [p.month_end.isoformat() for p in series]
    act_core = [round(float(p.actual_core or 0.0), 2) for p in series]
    act_nc = [round(float(p.actual_non_core or 0.0), 2) for p in series]
    act_inv = [round(float(p.actual_investment or 0.0), 2) for p in series]
    bud_core = [
        round(float(p.budget_core), 2) if p.budget_core is not None else None for p in series
    ]
    bud_inv = [
        round(float(p.budget_investment), 2) if p.budget_investment is not None else None
        for p in series
    ]

    all_vals: list[float] = [
        c + nc + inv for c, nc, inv in zip(act_core, act_nc, act_inv, strict=True)
    ]
    all_vals.extend(v for v in bud_core if v is not None)
    all_vals.extend(v for v in bud_inv if v is not None)

    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": "Actual Core",
            "x": dates,
            "y": act_core,
            "customdata": [format_currency(v, currency_id, compact=True) for v in act_core],
            "hovertemplate": "Core: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "#60A5FA"},
        },
        {
            "type": "bar",
            "name": "Actual Non-Core",
            "x": dates,
            "y": act_nc,
            "customdata": [format_currency(v, currency_id, compact=True) for v in act_nc],
            "hovertemplate": "Non-Core: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "#FBBF24"},
        },
        {
            "type": "bar",
            "name": "Actual Investment",
            "x": dates,
            "y": act_inv,
            "customdata": [format_currency(v, currency_id, compact=True) for v in act_inv],
            "hovertemplate": "Investment: <b>%{customdata}</b><extra></extra>",
            "marker": {"color": "#34D399"},
        },
    ]
    if any(v is not None for v in bud_core):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Core Target",
                "x": dates,
                "y": bud_core,
                "line": {"color": "#F87171", "width": 2, "dash": "dash"},
            }
        )
    if any(v is not None for v in bud_inv):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Investment Target",
                "x": dates,
                "y": bud_inv,
                "line": {"color": "#2DD4BF", "width": 2, "dash": "dot"},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(all_vals, currency_id, include_zero=True),
            barmode="stack",
        ),
        "config": chart_config("cockpit"),
    }


def build_emergency_runway_figure(
    series: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build Emergency Fund Reserve vs Target chart with Runway Months secondary axis."""
    pts = [
        p
        for p in series
        if p.emergency_actual is not None
        or p.emergency_gap is not None
        or p.emergency_target is not None
        or p.runway_months is not None
    ]
    if not pts:
        return None
    dates = [p.month_end.isoformat() for p in pts]
    has_actual = any(p.emergency_actual is not None for p in pts)
    bar_vals = [
        round(float(p.emergency_actual if has_actual else (p.emergency_gap or 0.0)), 2) for p in pts
    ]
    bar_name = "Emergency Actual" if has_actual else "Emergency Fund Gap"
    targets = [
        round(float(p.emergency_target), 2) if p.emergency_target is not None else None for p in pts
    ]
    runways = [
        round(float(p.runway_months), 2) if p.runway_months is not None else None for p in pts
    ]

    all_curr: list[float] = [*bar_vals, *(v for v in targets if v is not None)]
    traces: list[dict[str, Any]] = [
        {
            "type": "bar",
            "name": bar_name,
            "x": dates,
            "y": bar_vals,
            "customdata": [format_currency(v, currency_id, compact=True) for v in bar_vals],
            "hovertemplate": f"{bar_name}: <b>%{{customdata}}</b><extra></extra>",
            "marker": {"color": "#38BDF8" if has_actual else "#F59E0B"},
        }
    ]
    if any(v is not None for v in targets):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Emergency Target",
                "x": dates,
                "y": targets,
                "line": {"color": "#FBBF24", "width": 2, "dash": "dash"},
            }
        )
    if any(v is not None for v in runways):
        traces.append(
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Runway (Months)",
                "x": dates,
                "y": runways,
                "yaxis": "y2",
                "line": {"color": "#34D399", "width": 2.4},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(all_curr, currency_id, include_zero=True),
            y2_ticks=numeric_ticks(runways, suffix=" mos")
            if any(v is not None for v in runways)
            else None,
        ),
        "config": chart_config("cockpit"),
    }


def build_cashflow_waterfall_figure(snapshot: Any) -> dict[str, Any] | None:
    """Build Opening Cash → Operating/Investing/Financing/Unreconciled → Closing Cash bridge."""
    if getattr(snapshot, "state", "") != "ready" or not getattr(snapshot, "monthly_series", ()):
        return None
    steps = [
        WaterfallStep("Opening Cash", float(snapshot.opening_cash), measure="absolute"),
        WaterfallStep("Operating", float(snapshot.net_operating), measure="relative"),
        WaterfallStep("Investing", float(snapshot.net_investing), measure="relative"),
        WaterfallStep("Financing", float(snapshot.net_financing), measure="relative"),
        WaterfallStep("Transfers", float(snapshot.net_transfers), measure="relative"),
        WaterfallStep("Unreconciled", float(snapshot.unreconciled_difference), measure="relative"),
        WaterfallStep("Closing Cash", float(snapshot.closing_cash), measure="total"),
    ]
    return build_waterfall_figure(steps, currency_id=str(snapshot.currency_id))


def build_cashflow_activity_trend_figure(
    series: Sequence[Any],
    currency_id: str,
) -> dict[str, Any] | None:
    """Build monthly Operating, Investing, Financing, and Internal Transfers grouped bars."""
    if not series:
        return None
    dates = [p.month_end.isoformat() for p in series]
    op_vals = [round(float(p.net_operating), 2) for p in series]
    inv_vals = [round(float(p.net_investing), 2) for p in series]
    fin_vals = [round(float(p.net_financing), 2) for p in series]
    tr_vals = [round(float(p.net_transfers), 2) for p in series]

    all_vals = [*op_vals, *inv_vals, *fin_vals, *tr_vals]
    return {
        "data": [
            {
                "type": "bar",
                "name": "Operating",
                "x": dates,
                "y": op_vals,
                "customdata": ["Operating" for _ in dates],
                "hovertemplate": "Operating: <b>%{y:,.0f}</b><extra></extra>",
                "marker": {"color": "#34D399"},
            },
            {
                "type": "bar",
                "name": "Investing",
                "x": dates,
                "y": inv_vals,
                "customdata": ["Investing" for _ in dates],
                "hovertemplate": "Investing (Click to drill): <b>%{y:,.0f}</b><extra></extra>",
                "marker": {"color": "#60A5FA"},
            },
            {
                "type": "bar",
                "name": "Financing",
                "x": dates,
                "y": fin_vals,
                "customdata": ["Financing" for _ in dates],
                "hovertemplate": "Financing (Click to drill): <b>%{y:,.0f}</b><extra></extra>",
                "marker": {"color": "#FBBF24"},
            },
            {
                "type": "bar",
                "name": "Internal Transfers",
                "x": dates,
                "y": tr_vals,
                "customdata": ["Internal Transfers" for _ in dates],
                "hovertemplate": "Transfers (Click to drill): <b>%{y:,.0f}</b><extra></extra>",
                "marker": {"color": "#C084FC"},
            },
        ],
        "layout": studio_plotly_layout(
            y_ticks=currency_ticks(all_vals, currency_id, include_zero=True),
            barmode="group",
        ),
        "config": chart_config("cockpit"),
    }


def build_budget_health_heatmap_figure(
    budget_series: Sequence[Any],
    cashflow_series: Sequence[Any],
) -> dict[str, Any] | None:
    """Build Month × Health Flag matrix visualizing overspend, underfunding, and reconciliation."""
    if not budget_series and not cashflow_series:
        return None
    cf_by_ym = {c.year_month: c for c in cashflow_series}
    b_by_ym = {b.year_month: b for b in budget_series}
    months = sorted(set(b_by_ym) | set(cf_by_ym))
    if not months:
        return None

    flag_labels = [
        "Budget Healthy",
        "Core Overspent",
        "Non-Core Overspent",
        "Inv Underfunded",
        "Income Volatile",
        "Unreconciled Cash",
    ]
    z_matrix: list[list[float]] = []
    for label in flag_labels:
        row_vals: list[float] = []
        for ym in months:
            b = b_by_ym.get(ym)
            c = cf_by_ym.get(ym)
            if label == "Budget Healthy":
                row_vals.append(1.0 if (b and b.flags.is_budget_healthy) else 0.0)
            elif label == "Core Overspent":
                row_vals.append(1.0 if (b and b.flags.is_core_overspent) else 0.0)
            elif label == "Non-Core Overspent":
                row_vals.append(1.0 if (b and b.flags.is_non_core_overspent) else 0.0)
            elif label == "Inv Underfunded":
                row_vals.append(1.0 if (b and b.flags.is_investment_underfunded) else 0.0)
            elif label == "Income Volatile":
                row_vals.append(1.0 if (b and b.flags.is_income_volatile) else 0.0)
            else:
                row_vals.append(1.0 if (c and not c.is_reconciled) else 0.0)
        z_matrix.append(row_vals)

    return build_heatmap_figure(
        months,
        flag_labels,
        z_matrix,
        is_currency=False,
    )
