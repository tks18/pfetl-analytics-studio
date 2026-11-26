"""Phase 19 dark Plotly chart builders for Operations Run History & Sync Health.

Forbidden: Does not import `pfetl_analytics_studio.analytics` or `data/`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from pfetl_analytics_studio.ui.charts import numeric_ticks, studio_plotly_layout
from pfetl_analytics_studio.ui.theme import CHART_SERIES_COLORS, StudioColor


class _RunLike(Protocol):
    run_id: str
    started_at: datetime | None
    started_at_raw: str
    duration_seconds: float | None
    is_unfinished: bool
    status: str
    commit_state: str
    application_version: str
    schema_version: str
    sync: Any


class _LayerLike(Protocol):
    layer: str
    contract_count: int
    full_replace_count: int


class _TableRowLike(Protocol):
    schema_name: str
    table_name: str
    row_count: int


def _run_label(run: _RunLike) -> str:
    if run.started_at is not None:
        return run.started_at.strftime("%b %d %H:%M")
    return run.started_at_raw[:16] or run.run_id[:8]


def build_ops_duration_trend_figure(runs: Sequence[_RunLike]) -> dict[str, Any]:
    """Plot chronological run duration (seconds) across historical PFETL runs."""
    if not runs:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    ordered = list(reversed(runs))
    xs = [_run_label(r) for r in ordered]
    ys = [r.duration_seconds if r.duration_seconds is not None else 0.0 for r in ordered]
    colors = [
        StudioColor.WARNING.value
        if r.is_unfinished
        else (
            StudioColor.SUCCESS.value if r.status.upper() == "SUCCESS" else StudioColor.ERROR.value
        )
        for r in ordered
    ]
    y_t = numeric_ticks([0.0, *ys])
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": ys,
                "name": "Duration (s)",
                "marker": {"color": colors},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": ys,
                "name": "Trend",
                "mode": "lines+markers",
                "line": {"color": StudioColor.ACC_HOV.value, "width": 2.0},
                "marker": {"size": 5},
            },
        ],
        "layout": studio_plotly_layout(
            height=340, y_ticks=y_t, y_suffix="s", x_axis_type="category"
        ),
    }


def build_ops_status_breakdown_figure(runs: Sequence[_RunLike]) -> dict[str, Any]:
    """Plot run counts grouped by status and commit_state."""
    if not runs:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    statuses = sorted({r.status or "UNKNOWN" for r in runs})
    commits = sorted({r.commit_state or "UNKNOWN" for r in runs})
    counts = Counter((r.status or "UNKNOWN", r.commit_state or "UNKNOWN") for r in runs)
    traces: list[dict[str, Any]] = []
    for idx, cs in enumerate(commits):
        traces.append(
            {
                "type": "bar",
                "x": statuses,
                "y": [counts.get((st, cs), 0) for st in statuses],
                "name": cs,
                "marker": {"color": CHART_SERIES_COLORS[idx % len(CHART_SERIES_COLORS)]},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(height=340, barmode="stack", x_axis_type="category"),
    }


def build_ops_sync_outcomes_figure(runs: Sequence[_RunLike]) -> dict[str, Any]:
    """Plot per-run sync outcomes (synced, healed, failed, deferred) from cp_run_sync_summaries."""
    if not runs:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    ordered = list(reversed(runs))
    xs = [_run_label(r) for r in ordered]
    synced = [int(r.sync.synced_count) if r.sync is not None else 0 for r in ordered]
    healed = [int(r.sync.healed_count) if r.sync is not None else 0 for r in ordered]
    failed = [int(r.sync.failed_count) if r.sync is not None else 0 for r in ordered]
    deferred = [int(r.sync.deferred_count) if r.sync is not None else 0 for r in ordered]
    y_t = numeric_ticks(
        [0, *[s + h + f + d for s, h, f, d in zip(synced, healed, failed, deferred, strict=True)]]
    )
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": synced,
                "name": "Synced",
                "marker": {"color": StudioColor.SUCCESS.value},
            },
            {
                "type": "bar",
                "x": xs,
                "y": healed,
                "name": "Healed",
                "marker": {"color": StudioColor.ACC_HOV.value},
            },
            {
                "type": "bar",
                "x": xs,
                "y": failed,
                "name": "Failed",
                "marker": {"color": StudioColor.ERROR.value},
            },
            {
                "type": "bar",
                "x": xs,
                "y": deferred,
                "name": "Deferred",
                "marker": {"color": StudioColor.WARNING.value},
            },
        ],
        "layout": studio_plotly_layout(
            height=340, barmode="stack", y_ticks=y_t, x_axis_type="category"
        ),
    }


def build_ops_version_timeline_figure(runs: Sequence[_RunLike]) -> dict[str, Any]:
    """Plot run distribution by PFETL Core application_version."""
    if not runs:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    counts = Counter(r.application_version or "unknown" for r in runs)
    versions = list(counts.keys())
    vals = [counts[v] for v in versions]
    return {
        "data": [
            {
                "type": "pie",
                "labels": versions,
                "values": vals,
                "hole": 0.55,
                "marker": {"colors": list(CHART_SERIES_COLORS[: len(versions)])},
                "textinfo": "label+value",
            }
        ],
        "layout": studio_plotly_layout(height=340),
    }


def build_ops_layer_contracts_figure(layers: Sequence[_LayerLike]) -> dict[str, Any]:
    """Plot classified contract counts (Full Replace vs Incremental) by layer."""
    if not layers:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    xs = [layer.layer.title() for layer in layers]
    full_rep = [layer.full_replace_count for layer in layers]
    inc = [max(layer.contract_count - layer.full_replace_count, 0) for layer in layers]
    y_t = numeric_ticks([0, *[layer.contract_count for layer in layers]])
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": full_rep,
                "name": "Full Replace",
                "marker": {"color": StudioColor.ACC_HOV.value},
            },
            {
                "type": "bar",
                "x": xs,
                "y": inc,
                "name": "Incremental / Append",
                "marker": {"color": StudioColor.RECOVERY.value},
            },
        ],
        "layout": studio_plotly_layout(
            height=340, barmode="stack", y_ticks=y_t, x_axis_type="category"
        ),
    }


def build_ops_table_row_counts_figure(
    tables: Sequence[_TableRowLike], *, top_n: int = 12
) -> dict[str, Any]:
    """Plot top N warehouse tables by diagnostic row count colored by schema."""
    if not tables:
        return {"data": [], "layout": studio_plotly_layout(height=340, horizontal_bars=True)}
    top = list(tables[:top_n])[::-1]
    labels = [f"{t.schema_name}.{t.table_name}" for t in top]
    counts = [t.row_count for t in top]
    colors = [
        StudioColor.SUCCESS.value if t.schema_name.lower() == "gold" else StudioColor.ACC_HOV.value
        for t in top
    ]
    return {
        "data": [
            {
                "type": "bar",
                "orientation": "h",
                "y": labels,
                "x": counts,
                "name": "Row Count",
                "marker": {"color": colors},
            }
        ],
        "layout": studio_plotly_layout(height=340, horizontal_bars=True),
    }
