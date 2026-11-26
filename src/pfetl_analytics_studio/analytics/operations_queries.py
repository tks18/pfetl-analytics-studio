"""SQLite Control Plane & DuckDB meta queries for Phase 19 Operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pfetl_analytics_studio.analytics.operations_models import (
    LayerContractSummary,
    OperationRunRecord,
    RunHistoryFilter,
    RunSyncSummary,
    TableRowCountRecord,
    WarehouseHealthSnapshot,
)
from pfetl_analytics_studio.analytics.overview_models import (
    OverviewBreakupItem,
    OverviewKPI,
)
from pfetl_analytics_studio.analytics.overview_queries import table_exists
from pfetl_analytics_studio.data import ControlPlaneReader, DuckDBQueryPort
from pfetl_analytics_studio.semantic import MetricFormat


def _parse_ts(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    text = str(val).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _compute_duration(started: datetime | None, finished: datetime | None) -> float | None:
    if started is None or finished is None:
        return None
    s = started.replace(tzinfo=UTC) if started.tzinfo is None else started
    f = finished.replace(tzinfo=UTC) if finished.tzinfo is None else finished
    delta = (f - s).total_seconds()
    return delta if delta >= 0.0 else None


def _cp_table_exists(cp_reader: ControlPlaneReader, table_name: str) -> bool:
    row = cp_reader.fetch_one(
        "SELECT 1 AS ok FROM sqlite_master WHERE type = 'table' AND name = ?;",
        (table_name,),
    )
    return row is not None


def query_cp_runs_with_sync(
    cp_reader: ControlPlaneReader,
    filters: RunHistoryFilter,
) -> tuple[tuple[OperationRunRecord, ...], tuple[str, ...], tuple[str, ...]]:
    """Query `cp_runs` joined 1:1 with `cp_run_sync_summaries` on `run_id`."""
    if not _cp_table_exists(cp_reader, "cp_runs"):
        return (), (), ()

    has_sync = _cp_table_exists(cp_reader, "cp_run_sync_summaries")
    where_clauses: list[str] = []
    params: list[Any] = []
    if filters.status:
        where_clauses.append("r.status = ?")
        params.append(filters.status)
    if filters.commit_state:
        where_clauses.append("r.commit_state = ?")
        params.append(filters.commit_state)
    if filters.start_date is not None:
        where_clauses.append("substr(r.started_at, 1, 10) >= ?")
        params.append(filters.start_date.isoformat())
    if filters.end_date is not None:
        where_clauses.append("substr(r.started_at, 1, 10) <= ?")
        params.append(filters.end_date.isoformat())

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    limit_val = max(1, min(int(filters.limit), 500))
    params.append(limit_val)

    sync_cols = (
        """
        , s.actionable_count, s.synced_count, s.healed_count,
        s.failed_count, s.deferred_count, s.sync_status, s.created_at AS sync_created_at
        """
        if has_sync
        else """
        , NULL AS actionable_count, NULL AS synced_count, NULL AS healed_count,
        NULL AS failed_count, NULL AS deferred_count, NULL AS sync_status, NULL AS sync_created_at
        """
    )
    sync_join = "LEFT JOIN cp_run_sync_summaries s ON s.run_id = r.run_id" if has_sync else ""

    sql = f"""
        SELECT
            r.run_id,
            r.started_at,
            r.finished_at,
            r.status,
            r.commit_state,
            r.application_version,
            r.schema_version,
            r.settings_snapshot_id,
            r.rules_snapshot_id,
            (r.execution_log IS NOT NULL) AS has_execution_log
            {sync_cols}
        FROM cp_runs r
        {sync_join}
        {where_sql}
        ORDER BY r.started_at DESC
        LIMIT ?;
    """
    rows = cp_reader.fetch_all(sql, params)
    records: list[OperationRunRecord] = []
    for r in rows:
        run_id = str(r.get("run_id") or "")
        started_raw = str(r.get("started_at") or "")
        finished_raw = str(r.get("finished_at") or "")
        started_dt = _parse_ts(r.get("started_at"))
        finished_dt = _parse_ts(r.get("finished_at"))
        duration = _compute_duration(started_dt, finished_dt)
        sync_status = r.get("sync_status")
        sync_obj: RunSyncSummary | None = None
        if sync_status is not None or r.get("actionable_count") is not None:
            sync_obj = RunSyncSummary(
                run_id=run_id,
                actionable_count=int(r.get("actionable_count") or 0),
                synced_count=int(r.get("synced_count") or 0),
                healed_count=int(r.get("healed_count") or 0),
                failed_count=int(r.get("failed_count") or 0),
                deferred_count=int(r.get("deferred_count") or 0),
                sync_status=str(sync_status or ""),
                created_at=str(r.get("sync_created_at") or ""),
            )
        records.append(
            OperationRunRecord(
                run_id=run_id,
                started_at=started_dt,
                finished_at=finished_dt,
                started_at_raw=started_raw,
                finished_at_raw=finished_raw,
                duration_seconds=duration,
                is_unfinished=(finished_dt is None),
                status=str(r.get("status") or ""),
                commit_state=str(r.get("commit_state") or ""),
                application_version=str(r.get("application_version") or ""),
                schema_version=str(r.get("schema_version") or ""),
                settings_snapshot_id=str(r.get("settings_snapshot_id") or ""),
                rules_snapshot_id=str(r.get("rules_snapshot_id") or ""),
                has_execution_log=bool(r.get("has_execution_log")),
                sync=sync_obj,
            )
        )

    statuses = tuple(
        str(x["status"])
        for x in cp_reader.fetch_all(
            "SELECT DISTINCT status FROM cp_runs WHERE status IS NOT NULL ORDER BY status;"
        )
    )
    commit_states = tuple(
        str(x["commit_state"])
        for x in cp_reader.fetch_all(
            "SELECT DISTINCT commit_state FROM cp_runs WHERE commit_state IS NOT NULL ORDER BY commit_state;"
        )
    )
    return tuple(records), statuses, commit_states


def query_warehouse_contracts(
    duck_reader: DuckDBQueryPort,
) -> tuple[LayerContractSummary, ...]:
    """Query `meta.m_Data_Contracts` grouped by classified warehouse layer."""
    if not table_exists(duck_reader, "meta", "m_Data_Contracts"):
        return ()
    rows = duck_reader.fetch_all(
        """
        SELECT
            LOWER(COALESCE(layer, 'unknown')) AS layer,
            COUNT(*) AS contract_count,
            SUM(CASE WHEN COALESCE(is_full_replace, FALSE) THEN 1 ELSE 0 END) AS full_replace_count,
            STRING_AGG(DISTINCT COALESCE(domain, ''), ',') AS domains_csv
        FROM meta.m_Data_Contracts
        GROUP BY LOWER(COALESCE(layer, 'unknown'))
        ORDER BY layer ASC;
        """
    )
    out: list[LayerContractSummary] = []
    for r in rows:
        domains_raw = str(r.get("domains_csv") or "")
        domains = tuple(sorted({d.strip() for d in domains_raw.split(",") if d.strip()}))
        out.append(
            LayerContractSummary(
                layer=str(r.get("layer") or "unknown"),
                contract_count=int(r.get("contract_count") or 0),
                full_replace_count=int(r.get("full_replace_count") or 0),
                domains=domains,
            )
        )
    return tuple(out)


def query_warehouse_row_counts(
    duck_reader: DuckDBQueryPort,
) -> tuple[tuple[TableRowCountRecord, ...], datetime | None]:
    """Query `meta.m_Table_Row_Counts` ordered by schema and row count."""
    if not table_exists(duck_reader, "meta", "m_Table_Row_Counts"):
        return (), None
    rows = duck_reader.fetch_all(
        """
        SELECT schema_name, table_name, row_count, generated_at
        FROM meta.m_Table_Row_Counts
        ORDER BY row_count DESC, schema_name ASC, table_name ASC;
        """
    )
    records: list[TableRowCountRecord] = []
    latest_gen: datetime | None = None
    for r in rows:
        gen = _parse_ts(r.get("generated_at"))
        if gen is not None and (latest_gen is None or gen > latest_gen):
            latest_gen = gen
        records.append(
            TableRowCountRecord(
                schema_name=str(r.get("schema_name") or ""),
                table_name=str(r.get("table_name") or ""),
                row_count=int(r.get("row_count") or 0),
                generated_at=gen,
            )
        )
    return tuple(records), latest_gen


def build_operations_kpis(
    runs: tuple[OperationRunRecord, ...],
    warehouse: WarehouseHealthSnapshot,
) -> tuple[OverviewKPI, ...]:
    """Build 8 operational KPIs covering run lifecycle, sync health & warehouse."""
    latest = runs[0] if runs else None
    durations = [r.duration_seconds for r in runs if r.duration_seconds is not None]
    avg_dur = (sum(durations) / len(durations)) if durations else None
    success_cnt = sum(1 for r in runs if r.status.upper() == "SUCCESS")
    success_rate = (success_cnt / len(runs)) if runs else None

    total_actionable = sum(r.sync.actionable_count for r in runs if r.sync is not None)
    total_synced = sum(r.sync.synced_count for r in runs if r.sync is not None)
    total_healed = sum(r.sync.healed_count for r in runs if r.sync is not None)
    total_failed = sum(r.sync.failed_count for r in runs if r.sync is not None)

    latest_dur_str = (
        f"{latest.duration_seconds:.1f}s"
        if (latest and latest.duration_seconds is not None)
        else ("In Progress / Stale" if latest and latest.is_unfinished else "—")
    )
    as_of = latest.started_at.date() if (latest and latest.started_at) else None

    return (
        OverviewKPI(
            kpi_id="ops_latest_status",
            label=f"Latest Run: {latest.status if latest else 'N/A'}",
            value=1.0 if (latest and latest.status.upper() == "SUCCESS") else 0.0,
            format=MetricFormat.INTEGER,
            subtitle=f"Commit: {latest.commit_state if latest else '—'} · Core {latest.application_version if latest else '—'}",
            source_table="cp_runs",
            as_of_date=as_of,
            drill_route="/operations",
            available=latest is not None,
        ),
        OverviewKPI(
            kpi_id="ops_latest_duration",
            label=f"Latest Duration ({latest_dur_str})",
            value=latest.duration_seconds if latest else None,
            format=MetricFormat.NUMBER,
            unit_suffix="s",
            subtitle="Computed strictly from finished_at - started_at",
            source_table="cp_runs",
            as_of_date=as_of,
            drill_route="/operations",
            available=latest is not None and latest.duration_seconds is not None,
            breakups=(
                OverviewBreakupItem(
                    label="Avg Duration", value=avg_dur, format=MetricFormat.NUMBER, unit_suffix="s"
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="ops_run_success_rate",
            label="Run Success Rate",
            value=success_rate,
            format=MetricFormat.PERCENT,
            subtitle=f"{success_cnt} of {len(runs)} filtered runs succeeded",
            source_table="cp_runs",
            as_of_date=as_of,
            drill_route="/operations",
            available=success_rate is not None,
            breakups=(
                OverviewBreakupItem(
                    label="Total Runs", value=float(len(runs)), format=MetricFormat.INTEGER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="ops_latest_sync",
            label=f"Latest Sync: {latest.sync.sync_status if (latest and latest.sync) else 'N/A'}",
            value=float(latest.sync.synced_count) if (latest and latest.sync) else None,
            format=MetricFormat.INTEGER,
            subtitle="One-to-one sync summary for latest run",
            source_table="cp_run_sync_summaries",
            as_of_date=as_of,
            drill_route="/operations",
            available=latest is not None and latest.sync is not None,
            breakups=(
                OverviewBreakupItem(
                    label="Actionable",
                    value=float(latest.sync.actionable_count) if (latest and latest.sync) else None,
                    format=MetricFormat.INTEGER,
                ),
                OverviewBreakupItem(
                    label="Failed",
                    value=float(latest.sync.failed_count) if (latest and latest.sync) else None,
                    format=MetricFormat.INTEGER,
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="ops_sync_totals",
            label="Cumulative Synced Files",
            value=float(total_synced),
            format=MetricFormat.INTEGER,
            subtitle="Across filtered historical runs",
            source_table="cp_run_sync_summaries",
            as_of_date=as_of,
            drill_route="/operations",
            available=bool(runs),
            breakups=(
                OverviewBreakupItem(
                    label="Actionable", value=float(total_actionable), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="Healed", value=float(total_healed), format=MetricFormat.INTEGER
                ),
                OverviewBreakupItem(
                    label="Failed", value=float(total_failed), format=MetricFormat.INTEGER
                ),
            ),
        ),
        OverviewKPI(
            kpi_id="ops_contracts",
            label="Data Contracts",
            value=float(warehouse.total_contracts),
            format=MetricFormat.INTEGER,
            subtitle="Classified across Bronze / Silver / Gold",
            source_table="meta.m_Data_Contracts",
            drill_route="/operations",
            available=warehouse.state == "ready",
            breakups=tuple(
                OverviewBreakupItem(
                    label=layer.layer.title(),
                    value=float(layer.contract_count),
                    format=MetricFormat.INTEGER,
                )
                for layer in warehouse.layers
            ),
        ),
        OverviewKPI(
            kpi_id="ops_tracked_rows",
            label="Warehouse Tracked Rows",
            value=float(warehouse.total_tracked_rows),
            format=MetricFormat.INTEGER,
            subtitle=f"Diagnostic count across {warehouse.total_tracked_tables} tables",
            source_table="meta.m_Table_Row_Counts",
            drill_route="/operations",
            available=warehouse.state == "ready",
        ),
        OverviewKPI(
            kpi_id="ops_freshness",
            label="Latest Schema Version",
            value=float(len({r.schema_version for r in runs if r.schema_version})),
            format=MetricFormat.INTEGER,
            subtitle=(
                f"Warehouse generated: {warehouse.generated_at.strftime('%Y-%m-%d %H:%M')}"
                if warehouse.generated_at
                else "Warehouse timestamp unavailable"
            ),
            source_table="meta.m_Table_Row_Counts",
            drill_route="/operations",
            available=warehouse.generated_at is not None,
        ),
    )
