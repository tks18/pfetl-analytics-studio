"""SQLite Control Plane & DuckDB meta queries for Phase 19 Operations."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, cast

from pfetl_analytics_studio.analytics.operations_models import (
    ArtifactEventFilter,
    ArtifactEventRecord,
    ForensicContext,
    ForensicError,
    ForensicEvent,
    ForensicEventFilter,
    ForensicSummarySnapshot,
    LayerContractSummary,
    OperationRunRecord,
    OperationTreeNode,
    ProviderRequestFilter,
    ProviderRequestRecord,
    RunFailureRecord,
    RunHistoryFilter,
    RunSyncSummary,
    SnapshotDiffEntry,
    SnapshotPayloadRecord,
    SourceFileRecord,
    SourcePayloadResult,
    SourceRegistryFilter,
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
            LOWER(COALESCE(CAST(layer AS VARCHAR), 'unknown')) AS layer,
            COUNT(*) AS contract_count,
            SUM(CASE WHEN COALESCE(CAST(is_full_replace AS BOOLEAN), FALSE) THEN 1 ELSE 0 END) AS full_replace_count,
            STRING_AGG(DISTINCT COALESCE(CAST(domain AS VARCHAR), ''), ',') AS domains_csv
        FROM meta.m_Data_Contracts
        GROUP BY LOWER(COALESCE(CAST(layer AS VARCHAR), 'unknown'))
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


def _parse_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    dt = _parse_ts(val)
    return dt.date() if dt is not None else None


def query_source_registry(
    cp_reader: ControlPlaneReader,
    filters: SourceRegistryFilter,
) -> tuple[
    tuple[SourceFileRecord, ...],
    int,
    tuple[str, ...],
    tuple[str, ...],
    dict[str, int],
]:
    """Query `cp_file_registry` with independent freshness fields."""
    if not _cp_table_exists(cp_reader, "cp_file_registry"):
        return (), 0, (), (), {}

    where: list[str] = []
    params: list[Any] = []
    if filters.category:
        where.append("file_category = ?")
        params.append(filters.category)
    if filters.sync_status:
        where.append("sync_status = ?")
        params.append(filters.sync_status)
    if filters.search:
        where.append("(LOWER(file_name) LIKE ? OR LOWER(relative_path) LIKE ?)")
        pat = f"%{filters.search.strip().lower()}%"
        params.extend([pat, pat])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    cnt_row = cp_reader.fetch_one(
        f"SELECT COUNT(*) AS cnt FROM cp_file_registry {where_sql};", params
    )
    total_count = int(cnt_row["cnt"]) if cnt_row else 0

    limit_val = max(1, min(int(filters.limit), 500))
    rows = cp_reader.fetch_all(
        f"""
        SELECT
            file_id, file_name, relative_path, file_category, file_type,
            file_hash, file_size_bytes, sync_status,
            first_seen_run_id, first_seen_at,
            last_seen_run_id, last_seen_at,
            last_changed_run_id, last_changed_at,
            last_synced_run_id, last_synced_at
        FROM cp_file_registry
        {where_sql}
        ORDER BY COALESCE(last_synced_at, last_seen_at, first_seen_at) DESC, file_name ASC
        LIMIT ?;
        """,
        [*params, limit_val],
    )
    records = tuple(
        SourceFileRecord(
            file_id=str(r.get("file_id") or ""),
            file_name=str(r.get("file_name") or ""),
            relative_path=str(r.get("relative_path") or ""),
            file_category=str(r.get("file_category") or ""),
            file_type=str(r.get("file_type") or ""),
            file_hash=str(r.get("file_hash") or ""),
            file_size_bytes=int(r.get("file_size_bytes") or 0),
            sync_status=str(r.get("sync_status") or ""),
            first_seen_run_id=str(r.get("first_seen_run_id") or ""),
            first_seen_at=_parse_ts(r.get("first_seen_at")),
            last_seen_run_id=str(r.get("last_seen_run_id") or ""),
            last_seen_at=_parse_ts(r.get("last_seen_at")),
            last_changed_run_id=str(r.get("last_changed_run_id") or ""),
            last_changed_at=_parse_ts(r.get("last_changed_at")),
            last_synced_run_id=str(r.get("last_synced_run_id") or ""),
            last_synced_at=_parse_ts(r.get("last_synced_at")),
        )
        for r in rows
    )
    cats = tuple(
        str(x["file_category"])
        for x in cp_reader.fetch_all(
            "SELECT DISTINCT file_category FROM cp_file_registry WHERE file_category IS NOT NULL ORDER BY file_category;"
        )
    )
    statuses = tuple(
        str(x["sync_status"])
        for x in cp_reader.fetch_all(
            "SELECT DISTINCT sync_status FROM cp_file_registry WHERE sync_status IS NOT NULL ORDER BY sync_status;"
        )
    )
    cat_counts = {
        str(x["file_category"]): int(x["cnt"])
        for x in cp_reader.fetch_all(
            "SELECT file_category, COUNT(*) AS cnt FROM cp_file_registry GROUP BY file_category ORDER BY cnt DESC;"
        )
    }
    return records, total_count, cats, statuses, cat_counts


def query_artifact_events(
    cp_reader: ControlPlaneReader,
    filters: ArtifactEventFilter,
) -> tuple[tuple[ArtifactEventRecord, ...], tuple[str, ...], tuple[str, ...]]:
    """Query `cp_artifact_run_events` verbatim."""
    if not _cp_table_exists(cp_reader, "cp_artifact_run_events"):
        return (), (), ()

    where: list[str] = []
    params: list[Any] = []
    if filters.run_id:
        where.append("run_id = ?")
        params.append(filters.run_id)
    if filters.file_id:
        where.append("file_id = ?")
        params.append(filters.file_id)
    if filters.event_type:
        where.append("event_type = ?")
        params.append(filters.event_type)
    if filters.convergence_result:
        where.append("convergence_result = ?")
        params.append(filters.convergence_result)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit_val = max(1, min(int(filters.limit), 500))
    rows = cp_reader.fetch_all(
        f"""
        SELECT
            event_id, run_id, file_id, event_type, event_at, observed_path,
            content_hash, previous_status, new_status, event_reason,
            affected_table, detecting_run_id, replay_run_id, convergence_result
        FROM cp_artifact_run_events
        {where_sql}
        ORDER BY event_at DESC, rowid DESC
        LIMIT ?;
        """,
        [*params, limit_val],
    )
    events = tuple(
        ArtifactEventRecord(
            event_id=str(r.get("event_id") or ""),
            run_id=str(r.get("run_id") or ""),
            file_id=str(r.get("file_id") or ""),
            event_type=str(r.get("event_type") or ""),
            event_at=_parse_ts(r.get("event_at")),
            event_at_raw=str(r.get("event_at") or ""),
            observed_path=str(r.get("observed_path") or ""),
            content_hash=str(r.get("content_hash") or ""),
            previous_status=str(r["previous_status"])
            if r.get("previous_status") is not None
            else None,
            new_status=str(r.get("new_status") or ""),
            event_reason=str(r.get("event_reason") or ""),
            affected_table=str(r.get("affected_table") or ""),
            detecting_run_id=str(r.get("detecting_run_id") or ""),
            replay_run_id=str(r["replay_run_id"]) if r.get("replay_run_id") is not None else None,
            convergence_result=str(r.get("convergence_result") or ""),
        )
        for r in rows
    )
    ev_types = tuple(
        str(x["event_type"])
        for x in cp_reader.fetch_all(
            "SELECT DISTINCT event_type FROM cp_artifact_run_events WHERE event_type IS NOT NULL ORDER BY event_type;"
        )
    )
    conv_types = tuple(
        str(x["convergence_result"])
        for x in cp_reader.fetch_all(
            "SELECT DISTINCT convergence_result FROM cp_artifact_run_events WHERE convergence_result IS NOT NULL ORDER BY convergence_result;"
        )
    )
    return events, ev_types, conv_types


def query_provider_requests(
    cp_reader: ControlPlaneReader,
    filters: ProviderRequestFilter,
) -> tuple[tuple[ProviderRequestRecord, ...], tuple[str, ...], tuple[str, ...]]:
    """Query `cp_external_provider_requests`."""
    if not _cp_table_exists(cp_reader, "cp_external_provider_requests"):
        return (), (), ()

    where: list[str] = []
    params: list[Any] = []
    if filters.run_id:
        where.append("run_id = ?")
        params.append(filters.run_id)
    if filters.provider:
        where.append("provider = ?")
        params.append(filters.provider)
    if filters.status:
        where.append("status = ?")
        params.append(filters.status)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit_val = max(1, min(int(filters.limit), 500))
    rows = cp_reader.fetch_all(
        f"""
        SELECT
            request_id, run_id, provider, entity, requested_start, requested_end,
            attempt_count, status, failure_class, final_error, created_at
        FROM cp_external_provider_requests
        {where_sql}
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?;
        """,
        [*params, limit_val],
    )
    reqs = tuple(
        ProviderRequestRecord(
            request_id=str(r.get("request_id") or ""),
            run_id=str(r.get("run_id") or ""),
            provider=str(r.get("provider") or ""),
            entity=str(r.get("entity") or ""),
            requested_start=_parse_date(r.get("requested_start")),
            requested_end=_parse_date(r.get("requested_end")),
            attempt_count=int(r.get("attempt_count") or 0),
            status=str(r.get("status") or ""),
            failure_class=str(r["failure_class"]) if r.get("failure_class") is not None else None,
            final_error=str(r["final_error"]) if r.get("final_error") is not None else None,
            created_at=_parse_ts(r.get("created_at")),
            created_at_raw=str(r.get("created_at") or ""),
        )
        for r in rows
    )
    provs = tuple(
        str(x["provider"])
        for x in cp_reader.fetch_all(
            "SELECT DISTINCT provider FROM cp_external_provider_requests WHERE provider IS NOT NULL ORDER BY provider;"
        )
    )
    statuses = tuple(
        str(x["status"])
        for x in cp_reader.fetch_all(
            "SELECT DISTINCT status FROM cp_external_provider_requests WHERE status IS NOT NULL ORDER BY status;"
        )
    )
    return reqs, provs, statuses


def query_run_failures(
    cp_reader: ControlPlaneReader,
    *,
    run_id: str | None = None,
    limit: int = 200,
) -> tuple[RunFailureRecord, ...]:
    """Query `cp_run_failures`."""
    if not _cp_table_exists(cp_reader, "cp_run_failures"):
        return ()
    where_sql = "WHERE run_id = ?" if run_id else ""
    params: list[Any] = [run_id] if run_id else []
    params.append(max(1, min(int(limit), 500)))
    rows = cp_reader.fetch_all(
        f"""
        SELECT
            id, run_id, failed_isin, stage, error_type, error_message,
            traceback_log, failure_domain, created_at
        FROM cp_run_failures
        {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT ?;
        """,
        params,
    )
    return tuple(
        RunFailureRecord(
            id=int(r.get("id") or 0),
            run_id=str(r.get("run_id") or ""),
            failed_isin=str(r["failed_isin"]) if r.get("failed_isin") is not None else None,
            stage=str(r.get("stage") or ""),
            error_type=str(r.get("error_type") or ""),
            error_message=str(r.get("error_message") or ""),
            traceback_log=str(r["traceback_log"]) if r.get("traceback_log") is not None else None,
            failure_domain=str(r.get("failure_domain") or ""),
            created_at=_parse_ts(r.get("created_at")),
            created_at_raw=str(r.get("created_at") or ""),
        )
        for r in rows
    )


def query_snapshot_payload(
    cp_reader: ControlPlaneReader,
    run_id: str,
    kind: str,
) -> SnapshotPayloadRecord | None:
    """Load canonical settings or rules snapshot payload for a specific `run_id`."""
    if not _cp_table_exists(cp_reader, "cp_runs"):
        return None
    col = "settings_snapshot_id" if kind == "settings" else "rules_snapshot_id"
    snap_table = "cp_settings_snapshots" if kind == "settings" else "cp_rules_snapshots"
    if not _cp_table_exists(cp_reader, snap_table):
        return None
    run_row = cp_reader.fetch_one(
        f"SELECT {col} AS snap_id FROM cp_runs WHERE run_id = ?;", (run_id,)
    )
    if run_row is None or not run_row.get("snap_id"):
        return None
    snap_id = str(run_row["snap_id"])
    snap_row = cp_reader.fetch_one(
        f"SELECT snapshot_id, content_hash, canonical_payload, created_at FROM {snap_table} WHERE snapshot_id = ?;",
        (snap_id,),
    )
    if snap_row is None:
        return None
    raw = str(snap_row.get("canonical_payload") or "")
    parsed: dict[str, Any] | None = None
    warning: str | None = None
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            parsed = cast(dict[str, Any], loaded)
        else:
            warning = f"Expected JSON object, got {type(loaded).__name__}."
    except json.JSONDecodeError as exc:
        warning = f"Malformed JSON payload: {exc.msg} (line {exc.lineno}, col {exc.colno})."
    return SnapshotPayloadRecord(
        snapshot_id=snap_id,
        kind="settings" if kind == "settings" else "rules",
        content_hash=str(snap_row.get("content_hash") or ""),
        canonical_payload_raw=raw,
        parsed_payload=parsed,
        parse_warning=warning,
        created_at=str(snap_row.get("created_at") or ""),
    )


def compute_structural_json_diff(
    payload_a: dict[str, Any],
    payload_b: dict[str, Any],
    *,
    prefix: str = "",
) -> tuple[SnapshotDiffEntry, ...]:
    """Compute deterministic sorted structural JSON diff between two canonical dicts."""
    entries: list[SnapshotDiffEntry] = []
    all_keys = sorted(set(payload_a.keys()) | set(payload_b.keys()))
    for k in all_keys:
        path = f"{prefix}.{k}" if prefix else str(k)
        in_a = k in payload_a
        in_b = k in payload_b
        if not in_a:
            entries.append(
                SnapshotDiffEntry(
                    path=path, change_type="added", value_a=None, value_b=payload_b[k]
                )
            )
        elif not in_b:
            entries.append(
                SnapshotDiffEntry(
                    path=path, change_type="removed", value_a=payload_a[k], value_b=None
                )
            )
        else:
            va = payload_a[k]
            vb = payload_b[k]
            if isinstance(va, dict) and isinstance(vb, dict):
                entries.extend(
                    compute_structural_json_diff(
                        cast(dict[str, Any], va), cast(dict[str, Any], vb), prefix=path
                    )
                )
            elif va != vb:
                entries.append(
                    SnapshotDiffEntry(path=path, change_type="changed", value_a=va, value_b=vb)
                )
    return tuple(entries)


def build_sources_kpis(
    files: tuple[SourceFileRecord, ...],
    total_count: int,
    cat_counts: dict[str, int],
    event_count: int,
) -> tuple[OverviewKPI, ...]:
    """Build 4 KPIs for Sources & Artifacts tab."""
    total_bytes = sum(f.file_size_bytes for f in files)
    synced_cnt = sum(1 for f in files if f.sync_status.upper() == "SYNCED")
    removed_cnt = sum(1 for f in files if f.sync_status.upper() == "REMOVED")
    return (
        OverviewKPI(
            kpi_id="ops_src_files",
            label="Registered Source Files",
            value=float(total_count),
            format=MetricFormat.INTEGER,
            subtitle=f"Showing {len(files)} across {len(cat_counts)} categories",
            source_table="cp_file_registry",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="ops_src_size",
            label="Vault Size (Filtered MB)",
            value=total_bytes / (1024.0 * 1024.0),
            format=MetricFormat.NUMBER,
            unit_suffix=" MB",
            subtitle=f"{total_bytes:,} bytes across filtered artifacts",
            source_table="cp_file_registry",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="ops_src_synced",
            label="Synced vs Removed",
            value=float(synced_cnt),
            format=MetricFormat.INTEGER,
            subtitle=f"{removed_cnt} tombstoned / removed in view",
            source_table="cp_file_registry",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="ops_src_events",
            label="Artifact Run Events",
            value=float(event_count),
            format=MetricFormat.INTEGER,
            subtitle="Lifecycle transitions loaded from cp_artifact_run_events",
            source_table="cp_artifact_run_events",
            drill_route="/operations",
            available=True,
        ),
    )


def build_providers_kpis(
    reqs: tuple[ProviderRequestRecord, ...],
    failures: tuple[RunFailureRecord, ...],
) -> tuple[OverviewKPI, ...]:
    """Build 4 KPIs for Providers & Failures tab."""
    total_reqs = len(reqs)
    cached_cnt = sum(1 for r in reqs if r.status.upper() == "CACHED")
    cache_rate = (cached_cnt / total_reqs) if total_reqs else None
    total_attempts = sum(r.attempt_count for r in reqs)
    return (
        OverviewKPI(
            kpi_id="ops_prov_reqs",
            label="External Provider Requests",
            value=float(total_reqs),
            format=MetricFormat.INTEGER,
            subtitle="Filtered records from cp_external_provider_requests",
            source_table="cp_external_provider_requests",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="ops_prov_cache_hit",
            label="Cache Hit Rate %",
            value=cache_rate,
            format=MetricFormat.PERCENT,
            subtitle=f"{cached_cnt} cached of {total_reqs} requests",
            source_table="cp_external_provider_requests",
            drill_route="/operations",
            available=cache_rate is not None,
        ),
        OverviewKPI(
            kpi_id="ops_prov_attempts",
            label="Network Fetch Attempts",
            value=float(total_attempts),
            format=MetricFormat.INTEGER,
            subtitle="Cumulative attempt_count across filtered requests",
            source_table="cp_external_provider_requests",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="ops_run_failures",
            label="Recorded Run Failures",
            value=float(len(failures)),
            format=MetricFormat.INTEGER,
            subtitle="Diagnostic rows in cp_run_failures",
            source_table="cp_run_failures",
            drill_route="/operations",
            available=True,
        ),
    )


def _opt_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _opt_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def normalize_forensic_events(raw_events: list[dict[str, Any]]) -> tuple[ForensicEvent, ...]:
    """Normalize decoded JSONL dicts into `ForensicEvent` sorted by `(sequence, timestamp)`."""
    normalized: list[ForensicEvent] = []
    for raw in raw_events:
        ctx_raw = raw.get("context")
        ctx_dict: dict[str, Any] = (
            cast(dict[str, Any], ctx_raw) if isinstance(ctx_raw, dict) else {}
        )
        ctx = ForensicContext(
            run_id=str(ctx_dict.get("run_id") or ""),
            stage=str(ctx_dict.get("stage") or ""),
            component=str(ctx_dict.get("component") or ""),
            operation=str(ctx_dict.get("operation") or ""),
            operation_id=str(ctx_dict.get("operation_id") or ""),
            parent_operation_id=str(ctx_dict.get("parent_operation_id") or ""),
            entity_type=str(ctx_dict.get("entity_type") or ""),
            entity_id=str(ctx_dict.get("entity_id") or ""),
            duration_ms=_opt_float(ctx_dict.get("duration_ms")),
            rows=_opt_int(ctx_dict.get("rows")),
            attempt=_opt_int(ctx_dict.get("attempt")),
            provider=str(ctx_dict.get("provider") or ""),
            cache_hit=ctx_dict["cache_hit"]
            if isinstance(ctx_dict.get("cache_hit"), bool)
            else None,
            worker_id=str(ctx_dict.get("worker_id") or ""),
            batch_id=str(ctx_dict.get("batch_id") or ""),
            commit_state=str(ctx_dict.get("commit_state") or ""),
            settings_fingerprint=str(ctx_dict.get("settings_fingerprint") or ""),
            rules_fingerprint=str(ctx_dict.get("rules_fingerprint") or ""),
        )
        err_raw = raw.get("error")
        err_obj: ForensicError | None = None
        if isinstance(err_raw, dict) and err_raw:
            ed = cast(dict[str, Any], err_raw)
            cv_raw = ed.get("context_variables")
            err_obj = ForensicError(
                exception_type=str(ed.get("exception_type") or ""),
                exception_message=str(ed.get("exception_message") or ""),
                failure_domain=str(ed.get("failure_domain") or ""),
                traceback_summary=str(ed.get("traceback_summary") or ""),
                retryable=ed["retryable"] if isinstance(ed.get("retryable"), bool) else None,
                context_variables=cast(dict[str, Any], cv_raw) if isinstance(cv_raw, dict) else {},
                remediation_hint=str(ed.get("remediation_hint") or ""),
                failing_stage=str(ed.get("failing_stage") or ""),
                failing_entity=str(ed.get("failing_entity") or ""),
            )
        prog_raw = raw.get("progress")
        ts_raw = str(raw.get("timestamp") or "")
        sv_raw = raw.get("schema_version")
        sv: int | str = sv_raw if isinstance(sv_raw, (int, str)) else 1
        normalized.append(
            ForensicEvent(
                schema_version=sv,
                event_id=str(raw.get("event_id") or ""),
                sequence=_opt_int(raw.get("sequence")) or 0,
                timestamp=_parse_ts(ts_raw),
                timestamp_raw=ts_raw,
                event_code=str(raw.get("event_code") or ""),
                severity=str(raw.get("severity") or ""),
                presentation_kind=str(raw.get("presentation_kind") or ""),
                outcome=str(raw.get("outcome") or ""),
                message=str(raw.get("message") or ""),
                progress=cast(dict[str, Any], prog_raw) if isinstance(prog_raw, dict) else None,
                context=ctx,
                error=err_obj,
                raw_payload=raw,
            )
        )
    normalized.sort(key=lambda e: (e.sequence, e.timestamp_raw))
    return tuple(normalized)


def filter_forensic_events(
    events: tuple[ForensicEvent, ...],
    filters: ForensicEventFilter,
) -> tuple[ForensicEvent, ...]:
    """Filter normalized forensic events by context, error, and text search."""
    q = filters.search.strip().lower() if filters.search else ""
    out: list[ForensicEvent] = []
    for ev in events:
        c = ev.context
        if filters.severity and ev.severity != filters.severity:
            continue
        if filters.event_code and ev.event_code != filters.event_code:
            continue
        if filters.outcome and ev.outcome != filters.outcome:
            continue
        if filters.stage and c.stage != filters.stage:
            continue
        if filters.component and c.component != filters.component:
            continue
        if filters.operation and c.operation != filters.operation:
            continue
        if filters.entity_type and c.entity_type != filters.entity_type:
            continue
        if filters.entity_id and c.entity_id != filters.entity_id:
            continue
        if filters.provider and c.provider != filters.provider:
            continue
        if filters.worker_id and c.worker_id != filters.worker_id:
            continue
        if filters.batch_id and c.batch_id != filters.batch_id:
            continue
        if filters.failure_domain and (
            ev.error is None or ev.error.failure_domain != filters.failure_domain
        ):
            continue
        if q:
            haystack = " ".join(
                [
                    ev.message,
                    ev.event_code,
                    c.stage,
                    c.component,
                    c.operation,
                    c.entity_id,
                    c.provider,
                    ev.error.exception_message if ev.error else "",
                ]
            ).lower()
            if q not in haystack:
                continue
        out.append(ev)
        if len(out) >= max(1, filters.limit):
            break
    return tuple(out)


def build_operation_tree(events: tuple[ForensicEvent, ...]) -> tuple[OperationTreeNode, ...]:
    """Build explicit `parent_operation_id` -> `operation_id` tree retaining orphans at root."""
    agg: dict[str, dict[str, Any]] = {}
    for ev in events:
        op_id = ev.context.operation_id
        if not op_id:
            continue
        entry = agg.setdefault(
            op_id,
            {
                "operation_id": op_id,
                "parent_operation_id": ev.context.parent_operation_id,
                "operation": ev.context.operation,
                "stage": ev.context.stage,
                "component": ev.context.component,
                "event_count": 0,
                "duration_ms": None,
                "rows": None,
                "outcome": ev.outcome,
            },
        )
        entry["event_count"] += 1
        if ev.context.parent_operation_id and not entry["parent_operation_id"]:
            entry["parent_operation_id"] = ev.context.parent_operation_id
        if ev.context.operation and not entry["operation"]:
            entry["operation"] = ev.context.operation
        if ev.context.duration_ms is not None:
            entry["duration_ms"] = ev.context.duration_ms
        if ev.context.rows is not None:
            entry["rows"] = ev.context.rows
        if ev.outcome:
            entry["outcome"] = ev.outcome

    children_map: dict[str, list[str]] = {}
    roots: list[str] = []
    for op_id, info in agg.items():
        pid = str(info["parent_operation_id"] or "")
        if pid and pid != op_id:
            children_map.setdefault(pid, []).append(op_id)
        else:
            roots.append(op_id)

    # Retain orphan nodes whose parent_operation_id is not present in `agg`
    orphans = {
        op_id
        for op_id, info in agg.items()
        if info["parent_operation_id"]
        and info["parent_operation_id"] != op_id
        and info["parent_operation_id"] not in agg
    }
    ordered_roots = roots + sorted(orphans)

    def _build(op_id: str, seen: set[str]) -> OperationTreeNode:
        info = agg[op_id]
        next_seen = seen | {op_id}
        kids = tuple(
            _build(cid, next_seen) for cid in children_map.get(op_id, []) if cid not in next_seen
        )
        return OperationTreeNode(
            operation_id=op_id,
            parent_operation_id=str(info["parent_operation_id"] or ""),
            operation=str(info["operation"] or ""),
            stage=str(info["stage"] or ""),
            component=str(info["component"] or ""),
            event_count=int(info["event_count"]),
            duration_ms=cast(float | None, info["duration_ms"]),
            rows=cast(int | None, info["rows"]),
            outcome=str(info["outcome"] or ""),
            is_orphan=(op_id in orphans),
            children=kids,
        )

    return tuple(_build(rid, set()) for rid in ordered_roots)


def build_forensic_summary(
    run_id: str,
    source: str,
    events: tuple[ForensicEvent, ...],
    malformed_lines: int,
    tree_nodes: tuple[OperationTreeNode, ...],
) -> ForensicSummarySnapshot:
    """Compute operational forensic summaries strictly from emitted fields."""
    if not events and malformed_lines == 0:
        return ForensicSummarySnapshot(
            state="empty" if source != "unavailable" else "unavailable",
            run_id=run_id,
            source=source,
            diagnostics=(f"No forensic events decoded for run '{run_id}'.",),
        )

    sev_cnt: dict[str, int] = {}
    out_cnt: dict[str, int] = {}
    stg_cnt: dict[str, int] = {}
    stg_rows: dict[str, int] = {}
    prov_cnt: dict[str, int] = {}
    work_cnt: dict[str, int] = {}
    batch_cnt: dict[str, int] = {}
    fdom_cnt: dict[str, int] = {}
    unknown_schemas: set[str] = set()

    total_rows = 0
    total_retries = 0
    cache_hits = 0
    cache_misses = 0

    for ev in events:
        if str(ev.schema_version) != "1":
            unknown_schemas.add(str(ev.schema_version))
        if ev.severity:
            sev_cnt[ev.severity] = sev_cnt.get(ev.severity, 0) + 1
        if ev.outcome:
            out_cnt[ev.outcome] = out_cnt.get(ev.outcome, 0) + 1
        c = ev.context
        if c.stage:
            stg_cnt[c.stage] = stg_cnt.get(c.stage, 0) + 1
        if c.rows is not None:
            total_rows += c.rows
            if c.stage:
                stg_rows[c.stage] = stg_rows.get(c.stage, 0) + c.rows
        if c.attempt is not None and c.attempt > 1:
            total_retries += 1
        if c.cache_hit is True:
            cache_hits += 1
        elif c.cache_hit is False:
            cache_misses += 1
        if c.provider:
            prov_cnt[c.provider] = prov_cnt.get(c.provider, 0) + 1
        if c.worker_id:
            work_cnt[c.worker_id] = work_cnt.get(c.worker_id, 0) + 1
        if c.batch_id:
            batch_cnt[c.batch_id] = batch_cnt.get(c.batch_id, 0) + 1
        if ev.error is not None and ev.error.failure_domain:
            fd = ev.error.failure_domain
            fdom_cnt[fd] = fdom_cnt.get(fd, 0) + 1

    flat_ops: list[OperationTreeNode] = []

    def _collect(nodes: tuple[OperationTreeNode, ...]) -> None:
        for n in nodes:
            if n.duration_ms is not None:
                flat_ops.append(n)
            if n.children:
                _collect(n.children)

    _collect(tree_nodes)
    slowest = tuple(sorted(flat_ops, key=lambda n: n.duration_ms or 0.0, reverse=True)[:10])
    err_count = sev_cnt.get("ERROR", 0) + sev_cnt.get("CRITICAL", 0)

    kpis = (
        OverviewKPI(
            kpi_id="for_total_events",
            label="Total Forensic Events",
            value=float(len(events)),
            format=MetricFormat.INTEGER,
            subtitle=f"Source: {source}",
            source_table="cp_runs.execution_log",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="for_malformed",
            label="Malformed JSONL Lines",
            value=float(malformed_lines),
            format=MetricFormat.INTEGER,
            subtitle="Skipped cleanly with diagnostics",
            source_table="cp_runs.execution_log",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="for_rows",
            label="Emitted Row Count",
            value=float(total_rows),
            format=MetricFormat.INTEGER,
            subtitle="Sum of emitted context.rows",
            source_table="cp_runs.execution_log",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="for_cache",
            label="Cache Hits / Misses",
            value=float(cache_hits),
            format=MetricFormat.INTEGER,
            subtitle=f"{cache_misses} misses · {total_retries} retries (attempt > 1)",
            source_table="cp_runs.execution_log",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="for_ops",
            label="Root / Orphan Operations",
            value=float(len(tree_nodes)),
            format=MetricFormat.INTEGER,
            subtitle=f"{len(slowest)} ops with emitted duration_ms",
            source_table="cp_runs.execution_log",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="for_workers",
            label="Active Workers",
            value=float(len(work_cnt)),
            format=MetricFormat.INTEGER,
            subtitle=f"{len(prov_cnt)} providers · {len(batch_cnt)} batches",
            source_table="cp_runs.execution_log",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="for_stages",
            label="Observed Pipeline Stages",
            value=float(len(stg_cnt)),
            format=MetricFormat.INTEGER,
            subtitle="Distinct emitted context.stage values",
            source_table="cp_runs.execution_log",
            drill_route="/operations",
            available=True,
        ),
        OverviewKPI(
            kpi_id="for_errors",
            label="Error / Critical Events",
            value=float(err_count),
            format=MetricFormat.INTEGER,
            subtitle=f"{len(fdom_cnt)} distinct failure domains",
            source_table="cp_runs.execution_log",
            drill_route="/operations",
            available=True,
        ),
    )

    return ForensicSummarySnapshot(
        state="ready",
        run_id=run_id,
        source=source,
        total_events=len(events),
        malformed_lines=malformed_lines,
        unknown_schema_versions=tuple(sorted(unknown_schemas)),
        severity_counts=sev_cnt,
        outcome_counts=out_cnt,
        stage_counts=stg_cnt,
        stage_rows=stg_rows,
        provider_counts=prov_cnt,
        worker_counts=work_cnt,
        batch_counts=batch_cnt,
        failure_domain_counts=fdom_cnt,
        total_rows_emitted=total_rows,
        total_retries=total_retries,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        slowest_operations=slowest,
        kpis=kpis,
    )


def query_payload_availability(cp_reader: ControlPlaneReader, file_id: str) -> tuple[bool, int]:
    """Check whether `cp_file_payloads` holds retained bytes for `file_id` without loading bytes."""
    if not _cp_table_exists(cp_reader, "cp_file_payloads"):
        return False, 0
    row = cp_reader.fetch_one(
        "SELECT length(file_bytes) AS byte_len FROM cp_file_payloads WHERE file_id = ?;",
        (file_id,),
    )
    if row is None or row.get("byte_len") is None:
        return False, 0
    return True, int(row["byte_len"])


def query_payload_availability_map(
    cp_reader: ControlPlaneReader,
    file_ids: Sequence[str] | None = None,
) -> dict[str, int]:
    """Return `{file_id: byte_len}` for retained payloads without loading `file_bytes`."""
    if not _cp_table_exists(cp_reader, "cp_file_payloads"):
        return {}
    if file_ids is not None:
        unique_ids = tuple(dict.fromkeys(fid for fid in file_ids if fid))
        if not unique_ids:
            return {}
        placeholders = ", ".join("?" for _ in unique_ids)
        rows = cp_reader.fetch_all(
            f"SELECT file_id, length(file_bytes) AS byte_len FROM cp_file_payloads "
            f"WHERE file_id IN ({placeholders});",
            unique_ids,
        )
    else:
        rows = cp_reader.fetch_all(
            "SELECT file_id, length(file_bytes) AS byte_len FROM cp_file_payloads;",
        )
    return {
        str(row["file_id"]): int(row["byte_len"])
        for row in rows
        if row.get("file_id") is not None and row.get("byte_len") is not None
    }


def query_source_payload_bytes(cp_reader: ControlPlaneReader, file_id: str) -> SourcePayloadResult:
    """Fetch raw retained bytes from `cp_file_payloads` strictly on explicit user action."""
    if not _cp_table_exists(cp_reader, "cp_file_payloads"):
        return SourcePayloadResult(
            file_id=file_id,
            available=False,
            diagnostics=("Table cp_file_payloads is unavailable.",),
        )
    name_row = (
        cp_reader.fetch_one("SELECT file_name FROM cp_file_registry WHERE file_id = ?;", (file_id,))
        if _cp_table_exists(cp_reader, "cp_file_registry")
        else None
    )
    file_name = (
        str(name_row["file_name"]) if name_row and name_row.get("file_name") else f"{file_id}.bin"
    )
    row = cp_reader.fetch_one(
        "SELECT file_bytes FROM cp_file_payloads WHERE file_id = ?;",
        (file_id,),
    )
    if row is None or row.get("file_bytes") is None:
        return SourcePayloadResult(
            file_id=file_id,
            file_name=file_name,
            available=False,
            diagnostics=(f"No retained payload bytes found for file_id '{file_id}'.",),
        )
    raw = row["file_bytes"]
    if isinstance(raw, memoryview):
        payload_bytes = raw.tobytes()
    elif isinstance(raw, (bytes, bytearray)):
        payload_bytes = bytes(raw)
    else:
        payload_bytes = str(raw).encode("utf-8")
    return SourcePayloadResult(
        file_id=file_id,
        file_name=file_name,
        byte_length=len(payload_bytes),
        available=True,
        file_bytes=payload_bytes,
    )
