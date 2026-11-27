"""Dark Plotly chart builders for FIRE & Simulation Analytics.

Forbidden: Does not import `pfetl_analytics_studio.analytics` or `data/`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol

from pfetl_analytics_studio.ui.charts import currency_ticks, studio_plotly_layout
from pfetl_analytics_studio.ui.theme import CHART_ROLE_COLORS, ChartColorRole, StudioColor


class _FirePointLike(Protocol):
    month_start_date: date
    year_month: str
    total_income: float
    total_cash_income: float
    trailing_6m_avg_spend: float
    trailing_12m_avg_spend: float
    trailing_6m_avg_savings: float
    trailing_12m_avg_savings: float
    target_fi_today: float
    coast_fi_today: float
    lean_fi_today: float
    target_fi_total_future_nominal: float
    fi_gap: float
    fi_gap_monthly_trend: float | None
    terminal_wealth_nominal_p50: float
    real_return_assumed_pct: float | None
    wealth_velocity: float | None
    wealth_acceleration: float | None
    real_nw_cagr_3y: float | None
    current_fi_coverage_pct: float | None
    estimated_months_to_fi_linear: float | None
    months_to_fi_conservative_p90: float | None
    months_to_fi_base_p50: float | None
    months_to_fi_aggressive_p10: float | None
    probability_of_success_pct: float | None
    years_to_fi_p50: float | None
    runway_months_linear: float | None
    runway_months_stressed_p10: float | None
    runway_months_base_p50: float | None
    withdrawal_rate_if_retired_now: float | None
    savings_rate_required: float | None
    savings_rate_actual: float | None
    fi_velocity: float | None


def _pct(val: float | None) -> float | None:
    if val is None:
        return None
    return val * 100.0 if abs(val) <= 1.5 else val


def _months(points: Sequence[_FirePointLike]) -> list[str]:
    return [p.month_start_date.strftime("%b '%y") for p in points]


def build_fire_corpus_targets_figure(
    points: Sequence[_FirePointLike], *, currency_id: str = "INR_INR"
) -> dict[str, Any]:
    """Plot Target FI, Coast FI, Lean FI, and implied current corpus over time."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    implied_nw = [max(p.target_fi_today - p.fi_gap, 0.0) for p in points]
    y_t = currency_ticks(
        implied_nw + [p.target_fi_today for p in points], currency_id, include_zero=True
    )
    return {
        "data": [
            {
                "type": "scatter",
                "x": xs,
                "y": [p.target_fi_today for p in points],
                "name": "Target FI Today",
                "mode": "lines",
                "line": {"color": CHART_ROLE_COLORS[ChartColorRole.TARGET], "width": 2.4},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [p.coast_fi_today for p in points],
                "name": "Coast FI Today",
                "mode": "lines",
                "line": {"color": StudioColor.ACC_HOV.value, "width": 2.0, "dash": "dot"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [p.lean_fi_today for p in points],
                "name": "Lean FI Today",
                "mode": "lines",
                "line": {"color": StudioColor.RECOVERY.value, "width": 2.0, "dash": "dash"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": implied_nw,
                "name": "Current Wealth (FIRE Base)",
                "mode": "lines",
                "fill": "tozeroy",
                "fillcolor": "rgba(16, 185, 129, 0.14)",
                "line": {"color": StudioColor.SUCCESS.value, "width": 2.4},
            },
        ],
        "layout": studio_plotly_layout(height=340, y_ticks=y_t, x_axis_type="category"),
    }


def build_fire_gap_coverage_figure(
    points: Sequence[_FirePointLike], *, currency_id: str = "INR_INR"
) -> dict[str, Any]:
    """Plot monthly FI Corpus Gap bars with FI Coverage % trend on secondary axis."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    gaps = [p.fi_gap for p in points]
    covs = [_pct(p.current_fi_coverage_pct) for p in points]
    y_t = currency_ticks(gaps, currency_id, include_zero=True)
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": gaps,
                "name": "FI Gap",
                "marker": {"color": "rgba(245, 158, 11, 0.55)"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": covs,
                "name": "FI Coverage %",
                "mode": "lines+markers",
                "yaxis": "y2",
                "line": {"color": StudioColor.SUCCESS.value, "width": 2.4},
                "marker": {"size": 5},
            },
        ],
        "layout": studio_plotly_layout(
            height=340, y_ticks=y_t, y2_suffix="%", x_axis_type="category"
        ),
    }


def build_fire_monte_carlo_band_figure(points: Sequence[_FirePointLike]) -> dict[str, Any]:
    """Plot Monte Carlo months-to-FI P10/P50/P90 cone alongside linear estimate."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    layout = studio_plotly_layout(height=340, x_axis_type="category")
    layout["yaxis"]["title"] = {"text": "Months to FI"}
    return {
        "data": [
            {
                "type": "scatter",
                "x": xs,
                "y": [p.months_to_fi_conservative_p90 for p in points],
                "name": "Conservative (P90)",
                "mode": "lines",
                "line": {"color": StudioColor.WARNING.value, "width": 1.6, "dash": "dot"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [p.months_to_fi_aggressive_p10 for p in points],
                "name": "Aggressive (P10)",
                "mode": "lines",
                "fill": "tonexty",
                "fillcolor": "rgba(56, 189, 248, 0.12)",
                "line": {"color": StudioColor.ACC_HOV.value, "width": 1.6, "dash": "dot"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [p.months_to_fi_base_p50 for p in points],
                "name": "Base (P50)",
                "mode": "lines+markers",
                "line": {"color": StudioColor.SUCCESS.value, "width": 2.6},
                "marker": {"size": 5},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [p.estimated_months_to_fi_linear for p in points],
                "name": "Linear Estimate",
                "mode": "lines",
                "line": {"color": StudioColor.DIM.value, "width": 1.6, "dash": "dash"},
            },
        ],
        "layout": layout,
    }


def build_fire_probability_trend_figure(points: Sequence[_FirePointLike]) -> dict[str, Any]:
    """Plot Monte Carlo probability of success % over time."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    probs = [_pct(p.probability_of_success_pct) for p in points]
    layout = studio_plotly_layout(height=340, y_suffix="%", x_axis_type="category")
    layout["yaxis"]["range"] = [0, 105]
    layout["shapes"] = [
        {
            "type": "line",
            "xref": "paper",
            "x0": 0,
            "x1": 1,
            "y0": 85.0,
            "y1": 85.0,
            "line": {"color": StudioColor.WARNING.value, "width": 1.4, "dash": "dot"},
        }
    ]
    return {
        "data": [
            {
                "type": "scatter",
                "x": xs,
                "y": probs,
                "name": "Success Probability %",
                "mode": "lines+markers",
                "fill": "tozeroy",
                "fillcolor": "rgba(16, 185, 129, 0.16)",
                "line": {"color": StudioColor.SUCCESS.value, "width": 2.5},
                "marker": {"size": 5},
            }
        ],
        "layout": layout,
    }


def build_fire_future_terminal_figure(
    points: Sequence[_FirePointLike], *, currency_id: str = "INR_INR"
) -> dict[str, Any]:
    """Compare Terminal Wealth (P50 Nominal) against Future Nominal FI Target."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    term = [p.terminal_wealth_nominal_p50 for p in points]
    fut = [p.target_fi_total_future_nominal for p in points]
    y_t = currency_ticks(term + fut, currency_id, include_zero=True)
    return {
        "data": [
            {
                "type": "scatter",
                "x": xs,
                "y": term,
                "name": "Terminal Wealth (P50)",
                "mode": "lines+markers",
                "fill": "tozeroy",
                "fillcolor": "rgba(56, 189, 248, 0.12)",
                "line": {"color": StudioColor.ACC_HOV.value, "width": 2.4},
                "marker": {"size": 5},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": fut,
                "name": "Future Nominal FI Target",
                "mode": "lines",
                "line": {"color": StudioColor.WARNING.value, "width": 2.2, "dash": "dash"},
            },
        ],
        "layout": studio_plotly_layout(height=340, y_ticks=y_t, x_axis_type="category"),
    }


def build_fire_horizon_years_figure(
    points: Sequence[_FirePointLike], *, currency_id: str = "INR_INR"
) -> dict[str, Any]:
    """Plot Years to FI (P50) line and Monthly FI Gap Compression bars."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    trends = [p.fi_gap_monthly_trend or 0.0 for p in points]
    y_t = currency_ticks(trends, currency_id, include_zero=True)
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": trends,
                "name": "FI Gap Monthly Delta",
                "marker": {
                    "color": [
                        StudioColor.SUCCESS.value if v <= 0 else StudioColor.ERROR.value
                        for v in trends
                    ]
                },
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [p.years_to_fi_p50 for p in points],
                "name": "Years to FI (P50)",
                "mode": "lines+markers",
                "yaxis": "y2",
                "line": {"color": StudioColor.RECOVERY.value, "width": 2.4},
                "marker": {"size": 5},
            },
        ],
        "layout": studio_plotly_layout(
            height=340, y_ticks=y_t, y2_suffix="y", x_axis_type="category"
        ),
    }


def build_fire_runway_comparison_figure(points: Sequence[_FirePointLike]) -> dict[str, Any]:
    """Plot Linear, Base (P50), and Stressed (P10) portfolio runway months."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    layout = studio_plotly_layout(height=340, x_axis_type="category")
    layout["yaxis"]["title"] = {"text": "Runway (Months)"}
    return {
        "data": [
            {
                "type": "scatter",
                "x": xs,
                "y": [p.runway_months_linear for p in points],
                "name": "Linear Runway",
                "mode": "lines",
                "line": {"color": StudioColor.ACC_HOV.value, "width": 2.0, "dash": "dash"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [p.runway_months_base_p50 for p in points],
                "name": "Base Runway (P50)",
                "mode": "lines+markers",
                "line": {"color": StudioColor.SUCCESS.value, "width": 2.5},
                "marker": {"size": 5},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [p.runway_months_stressed_p10 for p in points],
                "name": "Stressed Runway (P10)",
                "mode": "lines",
                "fill": "tozeroy",
                "fillcolor": "rgba(239, 68, 68, 0.12)",
                "line": {"color": StudioColor.ERROR.value, "width": 2.0},
            },
        ],
        "layout": layout,
    }


def build_fire_savings_rate_figure(points: Sequence[_FirePointLike]) -> dict[str, Any]:
    """Plot Actual vs Required Savings Rate % and implied Withdrawal Rate %."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    return {
        "data": [
            {
                "type": "scatter",
                "x": xs,
                "y": [_pct(p.savings_rate_actual) for p in points],
                "name": "Actual Savings Rate %",
                "mode": "lines+markers",
                "line": {"color": StudioColor.SUCCESS.value, "width": 2.4},
                "marker": {"size": 5},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [_pct(p.savings_rate_required) for p in points],
                "name": "Required Savings Rate %",
                "mode": "lines",
                "line": {"color": StudioColor.WARNING.value, "width": 2.2, "dash": "dash"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [_pct(p.withdrawal_rate_if_retired_now) for p in points],
                "name": "SWR if Retired Now %",
                "mode": "lines",
                "yaxis": "y2",
                "line": {"color": StudioColor.RECOVERY.value, "width": 1.8, "dash": "dot"},
            },
        ],
        "layout": studio_plotly_layout(
            height=340, y_suffix="%", y2_suffix="%", x_axis_type="category"
        ),
    }


def build_fire_spend_vs_savings_figure(
    points: Sequence[_FirePointLike], *, currency_id: str = "INR_INR"
) -> dict[str, Any]:
    """Compare trailing 6m/12m average spend against trailing 6m/12m average savings."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    s6 = [p.trailing_6m_avg_spend for p in points]
    sav6 = [p.trailing_6m_avg_savings for p in points]
    y_t = currency_ticks(s6 + sav6, currency_id, include_zero=True)
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": s6,
                "name": "Trailing 6M Spend",
                "marker": {"color": "rgba(239, 68, 68, 0.65)"},
            },
            {
                "type": "bar",
                "x": xs,
                "y": sav6,
                "name": "Trailing 6M Savings",
                "marker": {"color": "rgba(16, 185, 129, 0.72)"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [p.trailing_12m_avg_spend for p in points],
                "name": "Trailing 12M Spend",
                "mode": "lines",
                "line": {"color": StudioColor.WARNING.value, "width": 2.0, "dash": "dot"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [p.trailing_12m_avg_savings for p in points],
                "name": "Trailing 12M Savings",
                "mode": "lines",
                "line": {"color": StudioColor.ACC_HOV.value, "width": 2.0},
            },
        ],
        "layout": studio_plotly_layout(
            height=340, barmode="group", y_ticks=y_t, x_axis_type="category"
        ),
    }


def build_fire_velocity_accel_figure(
    points: Sequence[_FirePointLike], *, currency_id: str = "INR_INR"
) -> dict[str, Any]:
    """Plot monthly Wealth Velocity bars alongside FI Velocity & 3Y Real NW CAGR."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    vels = [p.wealth_velocity or 0.0 for p in points]
    y_t = currency_ticks(vels, currency_id, include_zero=True)
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": vels,
                "name": "Wealth Velocity",
                "marker": {
                    "color": [
                        StudioColor.SUCCESS.value if v >= 0 else StudioColor.ERROR.value
                        for v in vels
                    ]
                },
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [_pct(p.fi_velocity) for p in points],
                "name": "FI Velocity %",
                "mode": "lines+markers",
                "yaxis": "y2",
                "line": {"color": StudioColor.ACC_HOV.value, "width": 2.2},
                "marker": {"size": 4},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [_pct(p.real_nw_cagr_3y) for p in points],
                "name": "Real NW CAGR 3Y %",
                "mode": "lines",
                "yaxis": "y2",
                "line": {"color": StudioColor.WARNING.value, "width": 2.0, "dash": "dash"},
            },
        ],
        "layout": studio_plotly_layout(
            height=340, y_ticks=y_t, y2_suffix="%", x_axis_type="category"
        ),
    }


def build_fire_income_vs_spend_figure(
    points: Sequence[_FirePointLike], *, currency_id: str = "INR_INR"
) -> dict[str, Any]:
    """Plot Total Income & Cash Income against Trailing 6M/12M Spend."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    inc = [p.total_income for p in points]
    c_inc = [p.total_cash_income for p in points]
    s12 = [p.trailing_12m_avg_spend for p in points]
    y_t = currency_ticks(inc + c_inc + s12, currency_id, include_zero=True)
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": inc,
                "name": "Total Income",
                "marker": {"color": "rgba(56, 189, 248, 0.72)"},
            },
            {
                "type": "bar",
                "x": xs,
                "y": c_inc,
                "name": "Cash Income",
                "marker": {"color": "rgba(16, 185, 129, 0.72)"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": s12,
                "name": "Trailing 12M Avg Spend",
                "mode": "lines+markers",
                "line": {"color": StudioColor.ERROR.value, "width": 2.2},
                "marker": {"size": 4},
            },
        ],
        "layout": studio_plotly_layout(
            height=340, barmode="group", y_ticks=y_t, x_axis_type="category"
        ),
    }


def build_fire_acceleration_cagr_figure(
    points: Sequence[_FirePointLike], *, currency_id: str = "INR_INR"
) -> dict[str, Any]:
    """Plot Wealth Acceleration bars alongside Assumed Real Return %."""
    if not points:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = _months(points)
    accels = [p.wealth_acceleration or 0.0 for p in points]
    y_t = currency_ticks(accels, currency_id, include_zero=True)
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": accels,
                "name": "Wealth Acceleration",
                "marker": {
                    "color": [
                        StudioColor.ACC_HOV.value if v >= 0 else StudioColor.WARNING.value
                        for v in accels
                    ]
                },
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [_pct(p.real_return_assumed_pct) for p in points],
                "name": "Assumed Real Return %",
                "mode": "lines+markers",
                "yaxis": "y2",
                "line": {"color": StudioColor.SUCCESS.value, "width": 2.2},
                "marker": {"size": 5},
            },
        ],
        "layout": studio_plotly_layout(
            height=340, y_ticks=y_t, y2_suffix="%", x_axis_type="category"
        ),
    }


def build_fire_scenario_projection_figure(
    *,
    base_corpus: float,
    base_target: float,
    base_monthly_savings: float,
    base_real_return_pct: float,
    scenario_target: float,
    scenario_monthly_savings: float,
    scenario_real_return_pct: float,
    currency_id: str = "INR_INR",
    horizon_months: int = 120,
) -> dict[str, Any]:
    """Compare arithmetic Canonical vs Scenario wealth trajectory over horizon."""
    xs = [
        f"Y{m // 12}M{m % 12}" if m % 12 else f"Year {m // 12}"
        for m in range(0, horizon_months + 1, 6)
    ]
    r_base = (1.0 + base_real_return_pct / 100.0) ** (1.0 / 12.0) - 1.0
    r_scen = (1.0 + scenario_real_return_pct / 100.0) ** (1.0 / 12.0) - 1.0

    def _sim(r_m: float, contrib: float) -> list[float]:
        pts: list[float] = []
        w = base_corpus
        for m in range(horizon_months + 1):
            if m % 6 == 0:
                pts.append(w)
            w = w * (1.0 + r_m) + contrib
        return pts

    base_pts = _sim(r_base, max(base_monthly_savings, 0.0))
    scen_pts = _sim(r_scen, max(scenario_monthly_savings, 0.0))
    y_t = currency_ticks(
        base_pts + scen_pts + [base_target, scenario_target], currency_id, include_zero=True
    )
    return {
        "data": [
            {
                "type": "scatter",
                "x": xs,
                "y": base_pts,
                "name": "Canonical Baseline Path",
                "mode": "lines",
                "line": {"color": StudioColor.DIM.value, "width": 2.0, "dash": "dash"},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": scen_pts,
                "name": "Scenario Path (Non-Canonical)",
                "mode": "lines",
                "fill": "tonexty",
                "fillcolor": "rgba(245, 158, 11, 0.12)",
                "line": {"color": StudioColor.WARNING.value, "width": 2.6},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": [scenario_target] * len(xs),
                "name": "Scenario FI Target",
                "mode": "lines",
                "line": {"color": StudioColor.SUCCESS.value, "width": 1.8, "dash": "dot"},
            },
        ],
        "layout": studio_plotly_layout(height=280, y_ticks=y_t, x_axis_type="category"),
    }
