"""Shared period-comparison helpers for built-in and custom report templates.

Owns
----
- `comparison_period_context`: converts `PeriodContext.comparison` into a standalone
  `PeriodContext` so any analytics service can be queried for the baseline window.
- `format_delta_kpi_fields`: computes uniform `(delta_text, delta_tone, breakups)`
  for `ReportKpiItem` from current vs prior scalar values.
- `kpi_items_from_overview_kpis`: maps `OverviewKPI` sequences (including deltas &
  breakup pills) into rich `ReportKpiItem` tuples.
- `build_period_comparison_bar_figure`: builds grouped Current-vs-Comparison Plotly
  bars with `studio_plotly_layout(..., x_axis_type="category", x_categories=...)`.
- `enrich_table_with_comparison`: appends Prior, Delta, and Delta % columns to
  tabular report blocks when a comparison window is active.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pfetl_analytics_studio.semantic import (
    ComparisonMode,
    MetricFormat,
    PeriodContext,
    PeriodPreset,
)
from pfetl_analytics_studio.ui import (
    format_currency,
    format_metric_value,
    format_percent,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.charts import currency_ticks
from pfetl_analytics_studio.ui.theme import StudioColor


def comparison_period_context(period: PeriodContext) -> PeriodContext | None:
    """Return a standalone `PeriodContext` for `period.comparison`, or `None`."""
    comp = period.comparison
    if comp is None or comp.mode == ComparisonMode.NONE:
        return None
    return PeriodContext(
        start_date=comp.start_date,
        end_date=comp.end_date,
        label=comp.label,
        preset=PeriodPreset.CUSTOM,
        as_of_date=comp.end_date,
        is_empty=False,
    )


def format_delta_kpi_fields(
    current: float | None,
    prior: float | None,
    fmt: MetricFormat,
    *,
    currency_id: str = "INR_INR",
    comparison_label: str = "Prior",
    positive_is_good: bool = True,
    unit_suffix: str = "",
) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    """Compute `(delta_text, delta_tone, breakups)` for a comparison-enabled KPI."""
    if current is None or prior is None:
        return "", "neutral", ()
    diff = float(current) - float(prior)
    if fmt == MetricFormat.CURRENCY:
        delta_str = format_currency(diff, currency_id, compact=True, signed=True, decimals=1)
        prior_str = format_currency(prior, currency_id, compact=True, decimals=1)
    elif fmt == MetricFormat.PERCENT:
        delta_str = format_percent(diff, decimals=1, signed=True)
        prior_str = format_percent(prior, decimals=1)
    else:
        sign = "+" if diff >= 0 else ""
        delta_str = f"{sign}{diff:,.2f}{unit_suffix}"
        prior_str = format_metric_value(
            prior, fmt, currency_id=currency_id, unit_suffix=unit_suffix
        )

    pct_change = ""
    if abs(prior) > 1e-9 and fmt != MetricFormat.PERCENT:
        pct = (diff / abs(prior)) * 100.0
        pct_change = f" ({pct:+.1f}%)"

    delta_text = f"{delta_str}{pct_change} vs {comparison_label}"
    if abs(diff) < 1e-9:
        delta_tone = "neutral"
    else:
        is_good = (diff >= 0) == positive_is_good
        delta_tone = "positive" if is_good else "warning"
    breakups = ((comparison_label, prior_str),)
    return delta_text, delta_tone, breakups


def kpi_items_from_overview_kpis(
    kpis: Sequence[Any],
    currency_id: str,
    *,
    limit: int = 12,
) -> list[Any]:
    """Convert `OverviewKPI`-like objects into rich `ReportKpiItem` instances."""
    from pfetl_analytics_studio.reports.models import ReportKpiItem  # noqa: PLC0415

    items: list[ReportKpiItem] = []
    for kpi in list(kpis)[:limit]:
        delta_text = ""
        delta_tone = "neutral"
        delta_val = getattr(kpi, "delta_value", None)
        if delta_val is not None:
            delta_fmt = getattr(kpi, "delta_format", MetricFormat.CURRENCY)
            if delta_fmt == MetricFormat.CURRENCY:
                d_str = format_currency(
                    float(delta_val), currency_id, compact=True, signed=True, decimals=1
                )
            else:
                d_str = format_percent(float(delta_val), decimals=1, signed=True)
            d_lbl = str(getattr(kpi, "delta_label", "") or "")
            delta_text = f"{d_str} {d_lbl}".strip()
            good_when_pos = bool(getattr(kpi, "delta_positive_is_good", True))
            delta_tone = "positive" if (float(delta_val) >= 0) == good_when_pos else "warning"

        breakup_pairs: list[tuple[str, str]] = []
        for br in getattr(kpi, "breakups", ()) or ():
            b_val = getattr(br, "value", None)
            if b_val is None:
                continue
            breakup_pairs.append(
                (
                    str(getattr(br, "label", "")),
                    format_metric_value(
                        b_val,
                        getattr(br, "format", MetricFormat.NUMBER),
                        currency_id=currency_id,
                        compact=True,
                        unit_suffix=str(getattr(br, "unit_suffix", "") or ""),
                    ),
                )
            )

        raw_val = getattr(kpi, "value", None)
        items.append(
            ReportKpiItem(
                label=str(getattr(kpi, "label", "")),
                value=str(raw_val) if not isinstance(raw_val, (int, float)) else float(raw_val),
                formatted_value=format_metric_value(
                    raw_val,
                    getattr(kpi, "format", MetricFormat.NUMBER),
                    currency_id=currency_id,
                    unit_suffix=str(getattr(kpi, "unit_suffix", "") or ""),
                ),
                subtitle=str(getattr(kpi, "subtitle", "") or ""),
                tone=str(getattr(kpi, "status_tone", "neutral") or "neutral"),
                source_table=str(getattr(kpi, "source_table", "") or ""),
                delta_text=delta_text,
                delta_tone=delta_tone,
                breakups=tuple(breakup_pairs),
                tooltip=str(getattr(kpi, "tooltip", "") or ""),
            )
        )
    return items


def build_period_comparison_bar_figure(
    categories: Sequence[str],
    current_vals: Sequence[float],
    prior_vals: Sequence[float],
    *,
    currency_id: str = "INR_INR",
    current_label: str = "Current Period",
    prior_label: str = "Comparison Period",
    is_percent: bool = False,
) -> dict[str, Any] | None:
    """Build grouped Current vs Comparison bar chart with collision-free categorical axes."""
    if not categories:
        return None
    cats = [str(c) for c in categories]
    cur = [float(v) for v in current_vals]
    pri = [float(v) for v in prior_vals]
    traces = [
        {
            "type": "bar",
            "name": current_label,
            "x": cats,
            "y": cur,
            "marker": {"color": StudioColor.ACCENT.value},
        },
        {
            "type": "bar",
            "name": prior_label,
            "x": cats,
            "y": pri,
            "marker": {"color": "rgba(148, 163, 184, 0.72)"},
        },
    ]
    all_vals = [*cur, *pri]
    y_ticks = None if is_percent else currency_ticks(all_vals, currency_id, include_zero=True)
    layout = studio_plotly_layout(
        height=330,
        barmode="group",
        x_axis_type="category",
        x_categories=cats,
        y_ticks=y_ticks,
        y_suffix="%" if is_percent else "",
        interaction_mode="analytical",
    )
    return {"data": traces, "layout": layout}


def enrich_table_with_comparison(
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
    prior_by_key: dict[str, dict[str, Any]],
    *,
    key_col: str,
    value_col: str,
    comparison_label: str = "Prior",
) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    """Append Prior, Delta, and Delta % columns to `rows` when `prior_by_key` is non-empty."""
    if not prior_by_key or not rows:
        return columns, rows
    prior_col = f"Prior ({comparison_label})"
    delta_col = "Delta"
    pct_col = "Delta %"
    out_cols = (*columns, prior_col, delta_col, pct_col)
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get(key_col) or "")
        p_row = prior_by_key.get(key, {})
        cur_raw = row.get(value_col)
        pri_raw = p_row.get(value_col)
        cur_val = float(cur_raw) if isinstance(cur_raw, (int, float)) else None
        pri_val = float(pri_raw) if isinstance(pri_raw, (int, float)) else None
        delta_val = (cur_val - pri_val) if (cur_val is not None and pri_val is not None) else None
        pct_val = (
            ((delta_val / abs(pri_val)) * 100.0)
            if (delta_val is not None and pri_val is not None and abs(pri_val) > 1e-9)
            else None
        )
        out_rows.append(
            {
                **row,
                prior_col: pri_val,
                delta_col: delta_val,
                pct_col: f"{pct_val:+.1f}%" if pct_val is not None else "—",
            }
        )
    return out_cols, out_rows
