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


def build_ops_source_categories_figure(category_counts: dict[str, int]) -> dict[str, Any]:
    """Plot registered source file counts by `file_category`."""
    if not category_counts:
        return {"data": [], "layout": studio_plotly_layout(height=340, horizontal_bars=True)}
    sorted_items = sorted(category_counts.items(), key=lambda kv: kv[1])[-12:]
    cats = [k for k, _ in sorted_items]
    vals = [v for _, v in sorted_items]
    return {
        "data": [
            {
                "type": "bar",
                "orientation": "h",
                "y": cats,
                "x": vals,
                "name": "Files",
                "marker": {"color": StudioColor.ACC_HOV.value},
            }
        ],
        "layout": studio_plotly_layout(height=340, horizontal_bars=True),
    }


def build_ops_artifact_events_figure(events: Sequence[Any]) -> dict[str, Any]:
    """Plot artifact run events stacked by `event_type` and `convergence_result`."""
    if not events:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    ev_types = sorted({str(e.event_type or "UNKNOWN") for e in events})
    convs = sorted({str(e.convergence_result or "UNKNOWN") for e in events})
    counts = Counter(
        (str(e.event_type or "UNKNOWN"), str(e.convergence_result or "UNKNOWN")) for e in events
    )
    traces: list[dict[str, Any]] = []
    for idx, cv in enumerate(convs):
        traces.append(
            {
                "type": "bar",
                "x": ev_types,
                "y": [counts.get((et, cv), 0) for et in ev_types],
                "name": cv,
                "marker": {"color": CHART_SERIES_COLORS[idx % len(CHART_SERIES_COLORS)]},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(height=340, barmode="stack", x_axis_type="category"),
    }


def build_ops_source_freshness_timeline_figure(files: Sequence[Any]) -> dict[str, Any]:
    """Plot distribution of `sync_status` across registered source files."""
    if not files:
        return {"data": [], "layout": studio_plotly_layout(height=340)}
    counts = Counter(str(f.sync_status or "UNKNOWN") for f in files)
    labels = list(counts.keys())
    vals = [counts[k] for k in labels]
    return {
        "data": [
            {
                "type": "pie",
                "labels": labels,
                "values": vals,
                "hole": 0.55,
                "marker": {"colors": list(CHART_SERIES_COLORS[: len(labels)])},
                "textinfo": "label+value",
            }
        ],
        "layout": studio_plotly_layout(height=340),
    }


def build_ops_provider_status_figure(requests: Sequence[Any]) -> dict[str, Any]:
    """Plot external provider requests grouped by `provider` and `status`."""
    if not requests:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    provs = sorted({str(r.provider or "UNKNOWN") for r in requests})
    statuses = sorted({str(r.status or "UNKNOWN") for r in requests})
    counts = Counter((str(r.provider or "UNKNOWN"), str(r.status or "UNKNOWN")) for r in requests)
    traces: list[dict[str, Any]] = []
    for idx, st in enumerate(statuses):
        traces.append(
            {
                "type": "bar",
                "x": provs,
                "y": [counts.get((pr, st), 0) for pr in provs],
                "name": st,
                "marker": {"color": CHART_SERIES_COLORS[idx % len(CHART_SERIES_COLORS)]},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(height=340, barmode="stack", x_axis_type="category"),
    }


def build_ops_provider_attempts_figure(requests: Sequence[Any]) -> dict[str, Any]:
    """Plot histogram/bar of request `attempt_count` across provider requests."""
    if not requests:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    counts = Counter(int(r.attempt_count) for r in requests)
    keys = sorted(counts.keys())
    xs = [f"{k} attempts" if k != 1 else "1 attempt" for k in keys]
    ys = [counts[k] for k in keys]
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": ys,
                "name": "Requests",
                "marker": {"color": StudioColor.SUCCESS.value},
            }
        ],
        "layout": studio_plotly_layout(
            height=340, y_ticks=numeric_ticks([0, *ys]), x_axis_type="category"
        ),
    }


def build_ops_failure_domains_figure(failures: Sequence[Any]) -> dict[str, Any]:
    """Plot recorded run failures grouped by `failure_domain` and `stage`."""
    if not failures:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    domains = sorted({str(f.failure_domain or "UNKNOWN") for f in failures})
    stages = sorted({str(f.stage or "UNKNOWN") for f in failures})
    counts = Counter(
        (str(f.failure_domain or "UNKNOWN"), str(f.stage or "UNKNOWN")) for f in failures
    )
    traces: list[dict[str, Any]] = []
    for idx, sg in enumerate(stages):
        traces.append(
            {
                "type": "bar",
                "x": domains,
                "y": [counts.get((dm, sg), 0) for dm in domains],
                "name": sg,
                "marker": {"color": CHART_SERIES_COLORS[idx % len(CHART_SERIES_COLORS)]},
            }
        )
    return {
        "data": traces,
        "layout": studio_plotly_layout(height=340, barmode="stack", x_axis_type="category"),
    }


def build_ops_forensic_severity_figure(
    severity_counts: dict[str, int],
    outcome_counts: dict[str, int],
) -> dict[str, Any]:
    """Plot forensic event severity and outcome distributions side-by-side."""
    if not severity_counts and not outcome_counts:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    sev_keys = sorted(severity_counts.keys())
    out_keys = sorted(outcome_counts.keys())
    return {
        "data": [
            {
                "type": "bar",
                "x": sev_keys,
                "y": [severity_counts[k] for k in sev_keys],
                "name": "Severity",
                "marker": {"color": StudioColor.ACC_HOV.value},
            },
            {
                "type": "bar",
                "x": out_keys,
                "y": [outcome_counts[k] for k in out_keys],
                "name": "Outcome",
                "marker": {"color": StudioColor.SUCCESS.value},
            },
        ],
        "layout": studio_plotly_layout(
            height=340,
            barmode="group",
            y_ticks=numeric_ticks([0, *severity_counts.values(), *outcome_counts.values()]),
            x_axis_type="category",
        ),
    }


def build_ops_forensic_stage_figure(
    stage_counts: dict[str, int],
    stage_rows: dict[str, int],
) -> dict[str, Any]:
    """Plot forensic event counts and emitted row counts by pipeline stage."""
    if not stage_counts:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    raw_stages = list(stage_counts.keys())
    xs = [s.replace("_", " ").title() for s in raw_stages]
    ev_vals = [stage_counts[s] for s in raw_stages]
    row_vals = [stage_rows.get(s, 0) for s in raw_stages]
    layout = studio_plotly_layout(
        height=340,
        y_ticks=numeric_ticks([0, *ev_vals]),
        y2_ticks=numeric_ticks([0, *row_vals]),
        y2_suffix="",
        x_axis_type="category",
    )
    layout["xaxis"]["tickangle"] = -25
    layout["margin"]["b"] = 64
    layout["margin"]["r"] = 56
    return {
        "data": [
            {
                "type": "bar",
                "x": xs,
                "y": ev_vals,
                "name": "Events",
                "marker": {"color": StudioColor.ACC_HOV.value},
            },
            {
                "type": "scatter",
                "x": xs,
                "y": row_vals,
                "name": "Emitted Rows",
                "mode": "lines+markers",
                "yaxis": "y2",
                "line": {"color": StudioColor.WARNING.value, "width": 2.2},
                "marker": {"size": 5},
            },
        ],
        "layout": layout,
    }


def build_ops_forensic_slowest_ops_figure(slowest_ops: Sequence[Any]) -> dict[str, Any]:
    """Plot slowest operations by emitted duration formatted in seconds."""
    if not slowest_ops:
        return {"data": [], "layout": studio_plotly_layout(height=340, horizontal_bars=True)}
    top = list(slowest_ops[:10])[::-1]
    labels = [
        f"{op.operation or op.operation_id[:12]} ({str(op.stage).replace('_', ' ').title()})"
        for op in top
    ]
    vals = [round(float(op.duration_ms or 0.0) / 1000.0, 2) for op in top]
    layout = studio_plotly_layout(height=340, horizontal_bars=True)
    layout["xaxis"]["ticksuffix"] = "s"
    return {
        "data": [
            {
                "type": "bar",
                "orientation": "h",
                "y": labels,
                "x": vals,
                "name": "Duration (s)",
                "hovertemplate": "%{y}: <b>%{x:.2f}s</b><extra></extra>",
                "marker": {"color": StudioColor.WARNING.value},
            }
        ],
        "layout": layout,
    }


def build_ops_forensic_workers_figure(
    worker_counts: dict[str, int],
    provider_counts: dict[str, int],
    cache_hits: int,
    cache_misses: int,
) -> dict[str, Any]:
    """Plot worker/provider activity and cache-hit vs miss counts."""
    labels: list[str] = []
    vals: list[int] = []
    for k, v in sorted(worker_counts.items()):
        labels.append(f"Worker: {k}")
        vals.append(v)
    for k, v in sorted(provider_counts.items()):
        labels.append(f"Provider: {k}")
        vals.append(v)
    if cache_hits or cache_misses:
        labels.extend(["Cache Hits", "Cache Misses"])
        vals.extend([cache_hits, cache_misses])
    if not labels:
        return {"data": [], "layout": studio_plotly_layout(height=340, x_axis_type="category")}
    return {
        "data": [
            {
                "type": "bar",
                "x": labels,
                "y": vals,
                "name": "Count",
                "marker": {"color": StudioColor.RECOVERY.value},
            }
        ],
        "layout": studio_plotly_layout(
            height=340, y_ticks=numeric_ticks([0, *vals]), x_axis_type="category"
        ),
    }
