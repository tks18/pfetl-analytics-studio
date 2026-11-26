"""Tests for Phase 19 OperationsAnalytics, Control Plane sync join & warehouse health."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from pathlib import Path

import duckdb

from pfetl_analytics_studio.analytics import (
    ArtifactEventFilter,
    ForensicEventFilter,
    OperationsAnalytics,
    ProviderRequestFilter,
    RunHistoryFilter,
    SourceRegistryFilter,
)
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.ui import (
    build_ops_artifact_events_figure,
    build_ops_duration_trend_figure,
    build_ops_failure_domains_figure,
    build_ops_forensic_severity_figure,
    build_ops_forensic_slowest_ops_figure,
    build_ops_forensic_stage_figure,
    build_ops_forensic_workers_figure,
    build_ops_layer_contracts_figure,
    build_ops_provider_attempts_figure,
    build_ops_provider_status_figure,
    build_ops_source_categories_figure,
    build_ops_source_freshness_timeline_figure,
    build_ops_status_breakdown_figure,
    build_ops_sync_outcomes_figure,
    build_ops_table_row_counts_figure,
    build_ops_version_timeline_figure,
)


def _create_cp_db(cp_path: Path) -> None:
    con = sqlite3.connect(str(cp_path))
    try:
        con.execute(
            """
            CREATE TABLE cp_runs (
                run_id TEXT PRIMARY KEY,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                status TEXT,
                commit_state TEXT,
                application_version TEXT,
                schema_version TEXT,
                settings_snapshot_id TEXT,
                rules_snapshot_id TEXT,
                execution_log BLOB
            );
            """
        )
        con.execute(
            """
            CREATE TABLE cp_run_sync_summaries (
                run_id TEXT PRIMARY KEY,
                actionable_count INTEGER,
                synced_count INTEGER,
                healed_count INTEGER,
                failed_count INTEGER,
                deferred_count INTEGER,
                sync_status TEXT,
                created_at TIMESTAMP
            );
            """
        )
        con.execute(
            """
            INSERT INTO cp_runs VALUES
                ('run-3', '2026-11-26T10:00:00', NULL, 'RUNNING', 'UNCOMMITTED', 'v12.1.3', 'v3', 'set-3', 'rul-3', NULL),
                ('run-2', '2026-11-26T00:02:39', '2026-11-26T00:03:37', 'SUCCESS', 'FULLY_COMMITTED', 'v12.1.3', 'v3', 'set-2', 'rul-2', X'789c'),
                ('run-1', '2026-11-25T20:00:00', '2026-11-25T20:00:30', 'FAILED', 'PARTIAL', 'v12.0.0', 'v3', 'set-1', 'rul-1', X'789c');
            """
        )
        con.execute(
            """
            INSERT INTO cp_run_sync_summaries VALUES
                ('run-2', 10, 8, 1, 0, 1, 'COMPLETE', '2026-11-26T00:02:41'),
                ('run-1', 5, 2, 0, 3, 0, 'FAILED', '2026-11-25T20:00:05');
            """
        )
        con.executescript(
            """
            CREATE TABLE cp_file_registry (
                file_id TEXT PRIMARY KEY,
                file_name TEXT,
                relative_path TEXT,
                file_category TEXT,
                file_type TEXT,
                file_hash TEXT,
                file_size_bytes BIGINT,
                sync_status TEXT,
                first_seen_run_id TEXT,
                first_seen_at TIMESTAMP,
                last_seen_run_id TEXT,
                last_seen_at TIMESTAMP,
                last_changed_run_id TEXT,
                last_changed_at TIMESTAMP,
                last_synced_run_id TEXT,
                last_synced_at TIMESTAMP
            );
            INSERT INTO cp_file_registry VALUES
                ('f1', 'mf.csv', 'in/mf.csv', 'mf_holdings', 'csv', 'h1', 4096, 'SYNCED', 'run-1', '2026-11-25T20:00:00', 'run-2', '2026-11-26T00:02:40', 'run-1', '2026-11-25T20:00:01', 'run-2', '2026-11-26T00:02:41');
            """
        )
        con.executescript(
            """
            CREATE TABLE cp_artifact_run_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT,
                file_id TEXT,
                event_type TEXT,
                event_at TIMESTAMP,
                observed_path TEXT,
                content_hash TEXT,
                previous_status TEXT,
                new_status TEXT,
                event_reason TEXT,
                affected_table TEXT,
                detecting_run_id TEXT,
                replay_run_id TEXT,
                convergence_result TEXT
            );
            INSERT INTO cp_artifact_run_events VALUES
                ('e1', 'run-2', 'f1', 'SYNCED', '2026-11-26T00:02:41', 'in/mf.csv', 'h1', 'PENDING_BRONZE', 'SYNCED', 'Loaded into Bronze', 'bronze.mf', 'run-2', 'run-2', 'CONVERGED');
            """
        )
        con.executescript(
            """
            CREATE TABLE cp_external_provider_requests (
                request_id TEXT PRIMARY KEY,
                run_id TEXT,
                provider TEXT,
                entity TEXT,
                requested_start DATE,
                requested_end DATE,
                attempt_count INTEGER,
                status TEXT,
                failure_class TEXT,
                final_error TEXT,
                created_at TIMESTAMP
            );
            INSERT INTO cp_external_provider_requests VALUES
                ('pr1', 'run-2', 'Yahoo Finance', '^NDX', '2026-11-20', '2026-11-26', 0, 'CACHED', NULL, NULL, '2026-11-26T00:02:42'),
                ('pr2', 'run-1', 'Yahoo Finance', 'BAD.NS', '2026-11-20', '2026-11-25', 3, 'FAILED', 'HTTP_404', 'Not found', '2026-11-25T20:00:10');
            """
        )
        con.executescript(
            """
            CREATE TABLE cp_run_failures (
                id INTEGER PRIMARY KEY,
                run_id TEXT,
                failed_isin TEXT,
                stage TEXT,
                error_type TEXT,
                error_message TEXT,
                traceback_log TEXT,
                failure_domain TEXT,
                created_at TIMESTAMP
            );
            INSERT INTO cp_run_failures VALUES
                (1, 'run-1', 'INE001A01036', 'market_sync', 'ValueError', 'Price missing', 'Traceback line 1', 'market_data', '2026-11-25T20:00:15');
            """
        )
        con.executescript(
            """
            CREATE TABLE cp_settings_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                content_hash TEXT,
                canonical_payload TEXT,
                created_at TIMESTAMP
            );
            INSERT INTO cp_settings_snapshots VALUES
                ('set-1', 'sh1', '{"performance":{"workers":2}}', '2026-11-25T20:00:00'),
                ('set-2', 'sh2', '{"performance":{"workers":4},"new_flag":true}', '2026-11-26T00:02:39'),
                ('set-3', 'sh3', '{malformed-json', '2026-11-26T10:00:00');
            """
        )
        con.executescript(
            """
            CREATE TABLE cp_rules_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                content_hash TEXT,
                canonical_payload TEXT,
                created_at TIMESTAMP
            );
            INSERT INTO cp_rules_snapshots VALUES
                ('rul-1', 'rh1', '{"fire":{"swr":0.04}}', '2026-11-25T20:00:00'),
                ('rul-2', 'rh2', '{"fire":{"swr":0.035}}', '2026-11-26T00:02:39');
            """
        )
        con.commit()
    finally:
        con.close()


def _create_meta_db(db_path: Path) -> None:
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA meta;")
        con.execute(
            """
            CREATE TABLE meta.m_Data_Contracts (
                contract_id VARCHAR,
                layer VARCHAR,
                physical_table VARCHAR,
                domain VARCHAR,
                grain VARCHAR,
                producer VARCHAR,
                is_full_replace BOOLEAN,
                publication_order INTEGER
            );
            INSERT INTO meta.m_Data_Contracts VALUES
                ('c1', 'bronze', 'bronze.b1', 'Raw', 'File', 'CP', TRUE, 0),
                ('c2', 'silver', 'silver.s1', 'Household', 'Tx', 'ETL', FALSE, 1),
                ('c3', 'gold', 'gold.g1', 'Wealth', 'Month', 'ETL', TRUE, 2);
            """
        )
        con.execute(
            """
            CREATE TABLE meta.m_Table_Row_Counts (
                schema_name VARCHAR,
                table_name VARCHAR,
                row_count BIGINT,
                generated_at TIMESTAMP
            );
            INSERT INTO meta.m_Table_Row_Counts VALUES
                ('silver', 's1', 1200, '2026-11-26 00:03:00'),
                ('gold', 'g1', 350, '2026-11-26 00:03:00');
            """
        )
    finally:
        con.close()


def _make_profile(db_path: Path, cp_path: Path) -> PFETLProfile:
    return PFETLProfile(
        profile_id="ops-test",
        label="Ops Test",
        settings_path=db_path.parent / "s.toml",
        rules_path=db_path.parent / "r.toml",
        target_db_base_path=db_path.parent,
        duckdb_path=db_path,
        control_plane_path=cp_path,
        lock_path=db_path.parent / "p.lock",
    )


def test_operations_run_sync_and_warehouse_health(tmp_path: Path) -> None:
    db_path = tmp_path / "ops.duckdb"
    cp_path = tmp_path / "cp.sqlite"
    _create_meta_db(db_path)
    _create_cp_db(cp_path)
    profile = _make_profile(db_path, cp_path)
    cache = QueryCache()

    snap = OperationsAnalytics.run_history(profile, cache=cache)
    assert snap.state == "ready"
    assert len(snap.runs) == 3
    assert len(snap.kpis) == 8

    by_id = {r.run_id: r for r in snap.runs}
    # Unfinished run has None duration and is_unfinished=True
    assert by_id["run-3"].is_unfinished is True
    assert by_id["run-3"].duration_seconds is None
    assert by_id["run-3"].sync is None  # missing sync summary allowed
    # Finished run computes duration strictly from timestamps
    assert by_id["run-2"].is_unfinished is False
    assert by_id["run-2"].duration_seconds == 58.0
    assert by_id["run-2"].sync is not None
    assert by_id["run-2"].sync.actionable_count == 10
    assert by_id["run-2"].sync.synced_count == 8
    assert by_id["run-2"].sync.healed_count == 1
    assert by_id["run-2"].sync.deferred_count == 1

    # Independent status & commit_state filters
    failed_runs = OperationsAnalytics.sync_history(
        profile, filters=RunHistoryFilter(status="FAILED", commit_state="PARTIAL")
    )
    assert len(failed_runs) == 1
    assert failed_runs[0].run_id == "run-1"
    assert failed_runs[0].duration_seconds == 30.0
    assert failed_runs[0].sync is not None
    assert failed_runs[0].sync.failed_count == 3

    # Warehouse health includes every classified layer
    wh = OperationsAnalytics.warehouse_health(profile, cache=cache)
    assert wh.state == "ready"
    assert wh.total_contracts == 3
    assert wh.total_tracked_tables == 2
    assert wh.total_tracked_rows == 1550
    assert {layer.layer for layer in wh.layers} == {"bronze", "silver", "gold"}

    # All 6 Plotly chart builders produce non-empty traces
    figs = [
        build_ops_duration_trend_figure(snap.runs),
        build_ops_status_breakdown_figure(snap.runs),
        build_ops_sync_outcomes_figure(snap.runs),
        build_ops_version_timeline_figure(snap.runs),
        build_ops_layer_contracts_figure(wh.layers),
        build_ops_table_row_counts_figure(wh.tables),
    ]
    for fig in figs:
        assert len(fig["data"]) >= 1


def test_phase20_sources_providers_failures_and_snapshot_diff(tmp_path: Path) -> None:
    db_path = tmp_path / "ops20.duckdb"
    cp_path = tmp_path / "cp20.sqlite"
    _create_meta_db(db_path)
    _create_cp_db(cp_path)
    profile = _make_profile(db_path, cp_path)
    cache = QueryCache()

    src = OperationsAnalytics.source_registry(
        profile, SourceRegistryFilter(category="mf_holdings"), cache=cache
    )
    assert src.state == "ready"
    assert len(src.files) == 1
    f1 = src.files[0]
    assert (
        f1.last_seen_at is not None
        and f1.last_changed_at is not None
        and f1.last_synced_at is not None
    )
    assert f1.last_seen_at != f1.last_changed_at

    evs = OperationsAnalytics.artifact_events(
        profile, ArtifactEventFilter(file_id="f1"), cache=cache
    )
    assert evs.state == "ready"
    assert evs.events[0].event_reason == "Loaded into Bronze"
    assert evs.events[0].convergence_result == "CONVERGED"

    provs = OperationsAnalytics.provider_requests(
        profile, ProviderRequestFilter(provider="Yahoo Finance"), cache=cache
    )
    assert provs.state == "ready"
    assert len(provs.requests) == 2
    assert any(r.attempt_count == 3 and r.failure_class == "HTTP_404" for r in provs.requests)

    fails = OperationsAnalytics.run_failures(profile, run_id="run-1", cache=cache)
    assert fails.state == "ready"
    assert fails.failures[0].failure_domain == "market_data"
    assert fails.failures[0].stage == "market_sync"
    assert fails.failures[0].traceback_log == "Traceback line 1"

    # Deterministic structural diff
    diff_rules = OperationsAnalytics.snapshot_diff(
        profile, "run-1", "run-2", kind="rules", cache=cache
    )
    assert diff_rules.state == "ready"
    assert len(diff_rules.diffs) == 1
    assert diff_rules.diffs[0].path == "fire.swr"
    assert diff_rules.diffs[0].change_type == "changed"
    assert diff_rules.diffs[0].value_a == 0.04
    assert diff_rules.diffs[0].value_b == 0.035

    diff_set = OperationsAnalytics.snapshot_diff(
        profile, "run-1", "run-2", kind="settings", cache=cache
    )
    assert diff_set.state == "ready"
    assert [d.path for d in diff_set.diffs] == ["new_flag", "performance.workers"]

    # Malformed JSON preserves raw text + parse warning
    bad = OperationsAnalytics.snapshot_diff(profile, "run-2", "run-3", kind="settings", cache=cache)
    assert bad.state == "parse_error"
    assert bad.parse_warning_b is not None
    assert bad.raw_text_b == "{malformed-json"

    for fig in (
        build_ops_source_categories_figure(src.category_counts),
        build_ops_artifact_events_figure(evs.events),
        build_ops_source_freshness_timeline_figure(src.files),
        build_ops_provider_status_figure(provs.requests),
        build_ops_provider_attempts_figure(provs.requests),
        build_ops_failure_domains_figure(fails.failures),
    ):
        assert len(fig["data"]) >= 1


def test_phase21_forensic_jsonl_lineage_and_payload_download(tmp_path: Path) -> None:
    db_path = tmp_path / "ops21.duckdb"
    cp_path = tmp_path / "cp21.sqlite"
    _create_meta_db(db_path)
    _create_cp_db(cp_path)

    raw_bytes = b"date,isin,amount\n2026-11-26,INE001A01036,12500.50\n"
    events_jsonl = "\n".join(
        [
            json.dumps(
                {
                    "schema_version": 2,
                    "event_id": "e2",
                    "sequence": 20,
                    "timestamp": "2026-11-26T00:02:42+00:00",
                    "event_code": "FUTURE_CODE",
                    "severity": "ERROR",
                    "outcome": "FAILED",
                    "message": "Child op failed",
                    "context": {
                        "run_id": "run-2",
                        "stage": "MARKET_SYNC",
                        "operation": "fetch_nav",
                        "operation_id": "op-child",
                        "parent_operation_id": "op-root",
                        "duration_ms": 420.5,
                        "rows": 15,
                        "attempt": 2,
                        "provider": "Yahoo Finance",
                        "cache_hit": False,
                        "worker_id": "w1",
                    },
                    "error": {
                        "exception_type": "TimeoutError",
                        "exception_message": "Timed out",
                        "failure_domain": "network",
                        "remediation_hint": "Retry later",
                    },
                }
            ),
            "{malformed-jsonl-line",
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": "e1",
                    "sequence": 10,
                    "timestamp": "2026-11-26T00:02:40+00:00",
                    "event_code": "OP_START",
                    "severity": "INFO",
                    "outcome": "COMPLETED",
                    "message": "Root op started",
                    "context": {
                        "run_id": "run-2",
                        "stage": "INITIALIZING",
                        "operation": "pipeline_run",
                        "operation_id": "op-root",
                        "cache_hit": True,
                    },
                }
            ),
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": "e3",
                    "sequence": 30,
                    "timestamp": "2026-11-26T00:02:45+00:00",
                    "event_code": "ORPHAN_EVT",
                    "severity": "INFO",
                    "outcome": "COMPLETED",
                    "message": "Orphan op",
                    "context": {
                        "run_id": "run-2",
                        "stage": "GOLD",
                        "operation": "orphan_step",
                        "operation_id": "op-orphan",
                        "parent_operation_id": "missing-parent",
                        "rows": 100,
                    },
                }
            ),
        ]
    )
    compressed = zlib.compress(events_jsonl.encode("utf-8"))
    con = sqlite3.connect(str(cp_path))
    try:
        con.execute("UPDATE cp_runs SET execution_log = ? WHERE run_id = 'run-2';", (compressed,))
        con.executescript(
            """
            CREATE TABLE cp_file_payloads (
                file_id TEXT PRIMARY KEY,
                file_bytes BLOB
            );
            """
        )
        con.execute("INSERT INTO cp_file_payloads VALUES ('f1', ?);", (raw_bytes,))
        con.commit()
    finally:
        con.close()

    profile = _make_profile(db_path, cp_path)

    # Sequence ordering preserved & malformed line counted without failing
    ev_snap = OperationsAnalytics.forensic_events(profile, "run-2")
    assert ev_snap.state == "ready"
    assert ev_snap.malformed_lines == 1
    assert [e.sequence for e in ev_snap.events] == [10, 20, 30]
    assert ev_snap.unknown_schema_versions == ("2",)
    assert ev_snap.events[1].error is not None
    assert ev_snap.events[1].error.failure_domain == "network"

    # Filtered query
    err_only = OperationsAnalytics.forensic_events(
        profile, "run-2", ForensicEventFilter(failure_domain="network", search="Timed out")
    )
    assert len(err_only.events) == 1
    assert err_only.events[0].event_id == "e2"

    # Parent -> child linkage + orphan node retention
    tree = OperationsAnalytics.operation_tree(profile, "run-2")
    assert len(tree) == 2
    root_node = next(n for n in tree if n.operation_id == "op-root")
    orphan_node = next(n for n in tree if n.operation_id == "op-orphan")
    assert root_node.is_orphan is False
    assert len(root_node.children) == 1
    assert root_node.children[0].operation_id == "op-child"
    assert root_node.duration_ms is None  # never synthesized
    assert root_node.children[0].duration_ms == 420.5
    assert orphan_node.is_orphan is True

    # Operational summary uses only emitted fields
    summary = OperationsAnalytics.forensic_summary(profile, "run-2")
    assert summary.state == "ready"
    assert summary.total_events == 3
    assert summary.malformed_lines == 1
    assert summary.total_rows_emitted == 115
    assert summary.total_retries == 1
    assert summary.cache_hits == 1
    assert summary.cache_misses == 1
    assert len(summary.slowest_operations) == 1
    assert summary.slowest_operations[0].operation_id == "op-child"

    # Retained payload availability & byte-identical download
    avail, byte_len = OperationsAnalytics.has_retained_payload(profile, "f1")
    assert avail is True
    assert byte_len == len(raw_bytes)
    dl = OperationsAnalytics.source_payload(profile, "f1")
    assert dl.available is True
    assert dl.file_bytes == raw_bytes
    assert hashlib.sha256(dl.file_bytes or b"").hexdigest() == hashlib.sha256(raw_bytes).hexdigest()

    for fig in (
        build_ops_forensic_severity_figure(summary.severity_counts, summary.outcome_counts),
        build_ops_forensic_stage_figure(summary.stage_counts, summary.stage_rows),
        build_ops_forensic_slowest_ops_figure(summary.slowest_operations),
        build_ops_forensic_workers_figure(
            summary.worker_counts, summary.provider_counts, summary.cache_hits, summary.cache_misses
        ),
    ):
        assert len(fig["data"]) >= 1
