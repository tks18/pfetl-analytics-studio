"""Dark-theme Plotly chart builders for Data Catalog & Guided Explorer.

Forbidden: Does not import `pfetl_analytics_studio.analytics` or `data/`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pfetl_analytics_studio.ui.charts import (
    chart_config,
    currency_ticks,
    numeric_ticks,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.theme import CHART_SERIES_COLORS, StudioColor


class _CoverageLike(Protocol):
    @property
    def layer_counts(self) -> Mapping[Any, int]: ...
    @property
    def usage_counts(self) -> Mapping[Any, int]: ...


class _CatalogEntryLike(Protocol):
    @property
    def physical_name(self) -> str: ...
    @property
    def layer(self) -> Any: ...
    @property
    def row_count(self) -> int | None: ...


class _MetricSpecLike(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def label(self) -> str: ...
    @property
    def format(self) -> Any: ...


class _ChartSuggestionLike(Protocol):
    @property
    def chart_type(self) -> Any: ...
    @property
    def x_field(self) -> str | None: ...
    @property
    def y_fields(self) -> Sequence[str]: ...
    @property
    def series_field(self) -> str | None: ...


class _GuidedResultLike(Protocol):
    @property
    def rows(self) -> Sequence[Mapping[str, Any]]: ...
    @property
    def metric_specs(self) -> Sequence[_MetricSpecLike]: ...
    @property
    def chart_suggestion(self) -> _ChartSuggestionLike: ...


def build_catalog_layer_usage_figure(coverage: _CoverageLike) -> dict[str, Any]:
    """Plot upstream surface counts by storage layer."""
    if not coverage.layer_counts:
        return {
            "data": [],
            "layout": studio_plotly_layout(height=300, x_axis_type="category"),
            "config": chart_config("analytical"),
        }

    layers = [str(getattr(k, "value", k)).replace("_", " ").title() for k in coverage.layer_counts]
    counts = [int(v) for v in coverage.layer_counts.values()]
    y_t = numeric_ticks([0.0, *(float(c) for c in counts)], suffix="")
    return {
        "data": [
            {
                "type": "bar",
                "x": layers,
                "y": counts,
                "name": "Surfaces",
                "marker": {"color": list(CHART_SERIES_COLORS[: len(layers)])},
                "text": [str(c) for c in counts],
                "textposition": "outside",
            }
        ],
        "layout": studio_plotly_layout(height=300, y_ticks=y_t, x_axis_type="category"),
        "config": chart_config("analytical"),
    }


def build_catalog_top_tables_figure(entries: Sequence[_CatalogEntryLike]) -> dict[str, Any]:
    """Plot top 12 largest discovered PFETL tables by row count."""
    populated = [e for e in entries if e.row_count is not None and e.row_count > 0]
    top = sorted(populated, key=lambda e: e.row_count or 0, reverse=True)[:12]
    if not top:
        return {
            "data": [],
            "layout": studio_plotly_layout(height=300, x_axis_type="linear"),
            "config": chart_config("analytical"),
        }

    ordered = list(reversed(top))
    names = [e.physical_name for e in ordered]
    counts = [int(e.row_count or 0) for e in ordered]
    return {
        "data": [
            {
                "type": "bar",
                "orientation": "h",
                "x": counts,
                "y": names,
                "name": "Rows",
                "marker": {"color": StudioColor.ACCENT.value},
            }
        ],
        "layout": {
            **studio_plotly_layout(height=300, horizontal_bars=True, x_axis_type="linear"),
            "margin": {"l": 210, "r": 24, "t": 18, "b": 42},
        },
        "config": chart_config("analytical"),
    }


def _to_float(val: Any) -> float:
    if val is None or isinstance(val, bool):
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _metric_fmt_str(m_spec: _MetricSpecLike | None) -> str:
    if m_spec is None:
        return "number"
    return str(getattr(m_spec.format, "value", m_spec.format)).lower()


def _dynamic_y_ticks(
    values: Sequence[float],
    m_spec: _MetricSpecLike | None,
    currency_id: str = "INR_INR",
) -> tuple[list[float], list[str]] | None:
    """Compute axis ticks matching the selected metric's actual format (never hardcoded %)."""
    fmt = _metric_fmt_str(m_spec)
    if fmt == "currency":
        return currency_ticks(values, currency_id, include_zero=True)
    if fmt == "percent":
        return numeric_ticks(values, suffix="%")
    if fmt == "ratio":
        return numeric_ticks(values, suffix="x")
    return numeric_ticks(values, suffix="")


def _apply_readable_x_axis(layout: dict[str, Any], x_labels: Sequence[str]) -> None:
    """Prevent X-axis label cramming via angled ticks, nticks cap, and automargin."""
    xaxis = layout.setdefault("xaxis", {})
    xaxis["automargin"] = True
    max_len = max((len(str(x)) for x in x_labels), default=0)
    if len(x_labels) > 8 or max_len > 10:
        xaxis["tickangle"] = -35
        xaxis["nticks"] = min(len(x_labels), 14)
        margin = layout.setdefault("margin", {})
        margin["b"] = max(int(margin.get("b", 40)), 68)


def build_guided_explorer_figure(
    result: _GuidedResultLike,
    *,
    chart_override: str | None = None,
    currency_id: str = "INR_INR",
) -> dict[str, Any]:
    """Build a dark Plotly figure from a `GuidedQueryResult` with dynamic axis formatting."""
    suggestion = result.chart_suggestion
    chart_type = (
        chart_override
        if chart_override and chart_override != "auto"
        else str(getattr(suggestion.chart_type, "value", suggestion.chart_type))
    )
    if not result.rows or not suggestion.y_fields or chart_type == "table":
        return {
            "data": [],
            "layout": studio_plotly_layout(height=380, x_axis_type="category"),
            "config": chart_config("analytical"),
        }

    metric_by_id = {m.id: m for m in result.metric_specs}
    primary_metric = metric_by_id.get(suggestion.y_fields[0])
    x_field = suggestion.x_field

    # Zero-dimension summary bar chart (single-row KPI comparison)
    if x_field is None:
        row0 = result.rows[0]
        labels = [metric_by_id[y].label if y in metric_by_id else y for y in suggestion.y_fields]
        vals = [_to_float(row0.get(y)) for y in suggestion.y_fields]
        y_t = _dynamic_y_ticks([0.0, *vals], primary_metric, currency_id)
        layout = studio_plotly_layout(height=380, y_ticks=y_t, x_axis_type="category")
        _apply_readable_x_axis(layout, labels)
        return {
            "data": [
                {
                    "type": "bar",
                    "x": labels,
                    "y": vals,
                    "marker": {"color": list(CHART_SERIES_COLORS[: len(labels)])},
                }
            ],
            "layout": layout,
            "config": chart_config("analytical"),
        }

    all_ys: list[float] = [0.0]
    traces: list[dict[str, Any]] = []

    if suggestion.series_field:
        y_col = suggestion.y_fields[0]
        m_spec = metric_by_id.get(y_col)
        series_map: dict[str, tuple[list[str], list[float]]] = defaultdict(lambda: ([], []))
        all_xs: list[str] = []
        for row in result.rows:
            s_val = str(row.get(suggestion.series_field, "Unknown"))
            x_val = str(row.get(x_field, ""))
            y_val = _to_float(row.get(y_col))
            xs, ys = series_map[s_val]
            xs.append(x_val)
            ys.append(y_val)
            all_xs.append(x_val)
            all_ys.append(y_val)

        for idx, (s_name, (xs, ys)) in enumerate(sorted(series_map.items())[:12]):
            color = CHART_SERIES_COLORS[idx % len(CHART_SERIES_COLORS)]
            if chart_type == "time_series":
                traces.append(
                    {
                        "type": "scatter",
                        "mode": "lines+markers",
                        "name": s_name[:28],
                        "x": xs,
                        "y": ys,
                        "line": {"color": color, "width": 2.2},
                        "marker": {"size": 5},
                    }
                )
            else:
                traces.append(
                    {
                        "type": "bar",
                        "name": s_name[:28],
                        "x": xs,
                        "y": ys,
                        "marker": {"color": color},
                    }
                )
        y_t = _dynamic_y_ticks(all_ys, m_spec, currency_id)
        barmode = (
            "stack"
            if chart_type == "stacked_bar"
            else ("group" if chart_type == "grouped_bar" else None)
        )
        layout = studio_plotly_layout(
            height=400, barmode=barmode, y_ticks=y_t, x_axis_type="category"
        )
        _apply_readable_x_axis(layout, list(dict.fromkeys(all_xs)))
        return {"data": traces, "layout": layout, "config": chart_config("analytical")}

    xs = [str(row.get(x_field, "")) for row in result.rows]
    if chart_type == "horizontal_bar":
        y_col = suggestion.y_fields[0]
        m_spec = metric_by_id.get(y_col)
        label = m_spec.label if m_spec is not None else y_col
        ys = [_to_float(row.get(y_col)) for row in result.rows]
        pairs = list(reversed(list(zip(xs, ys, strict=True))[:25]))
        x_t = _dynamic_y_ticks([0.0, *(p[1] for p in pairs)], m_spec, currency_id)
        return {
            "data": [
                {
                    "type": "bar",
                    "orientation": "h",
                    "name": label,
                    "x": [p[1] for p in pairs],
                    "y": [p[0][:34] for p in pairs],
                    "marker": {"color": StudioColor.ACCENT.value},
                }
            ],
            "layout": {
                **studio_plotly_layout(
                    height=420,
                    horizontal_bars=True,
                    x_currency_ticks=x_t,
                    x_axis_type="linear",
                ),
                "margin": {"l": 200, "r": 24, "t": 18, "b": 42},
            },
            "config": chart_config("analytical"),
        }

    for idx, y_col in enumerate(suggestion.y_fields):
        m_spec = metric_by_id.get(y_col)
        label = m_spec.label if m_spec is not None else y_col
        ys = [_to_float(row.get(y_col)) for row in result.rows]
        all_ys.extend(ys)
        color = CHART_SERIES_COLORS[idx % len(CHART_SERIES_COLORS)]
        if chart_type == "time_series":
            traces.append(
                {
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": label,
                    "x": xs,
                    "y": ys,
                    "line": {"color": color, "width": 2.2},
                    "marker": {"size": 5},
                }
            )
        else:
            traces.append(
                {
                    "type": "bar",
                    "name": label,
                    "x": xs,
                    "y": ys,
                    "marker": {"color": color},
                }
            )

    y_t = _dynamic_y_ticks(all_ys, primary_metric, currency_id)
    barmode = (
        "stack"
        if chart_type == "stacked_bar"
        else (
            "group"
            if (chart_type in ("grouped_bar", "bar") and len(suggestion.y_fields) > 1)
            else None
        )
    )
    layout = studio_plotly_layout(height=380, barmode=barmode, y_ticks=y_t, x_axis_type="category")
    _apply_readable_x_axis(layout, xs)
    return {"data": traces, "layout": layout, "config": chart_config("analytical")}
