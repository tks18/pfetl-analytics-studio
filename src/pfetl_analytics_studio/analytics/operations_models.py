"""Typed DTOs for Phase 19 Operations Run History, Sync Health & Warehouse Freshness."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.analytics.overview_models import OverviewKPI


class RunSyncSummary(BaseModel):
    """One-to-one sync outcome summary from `cp_run_sync_summaries`."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    actionable_count: int = 0
    synced_count: int = 0
    healed_count: int = 0
    failed_count: int = 0
    deferred_count: int = 0
    sync_status: str = ""
    created_at: str = ""


class OperationRunRecord(BaseModel):
    """Run lifecycle record joined with optional sync summary."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    started_at_raw: str = ""
    finished_at_raw: str = ""
    duration_seconds: float | None = None
    is_unfinished: bool = False
    status: str = ""
    commit_state: str = ""
    application_version: str = ""
    schema_version: str = ""
    settings_snapshot_id: str = ""
    rules_snapshot_id: str = ""
    has_execution_log: bool = False
    sync: RunSyncSummary | None = None


class RunHistoryFilter(BaseModel):
    """Filter criteria for `OperationsAnalytics.run_history` & `sync_history`."""

    model_config = ConfigDict(frozen=True)

    limit: int = 100
    status: str | None = None
    commit_state: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class LayerContractSummary(BaseModel):
    """Contract count summary per warehouse layer from `meta.m_Data_Contracts`."""

    model_config = ConfigDict(frozen=True)

    layer: str
    contract_count: int = 0
    full_replace_count: int = 0
    domains: tuple[str, ...] = ()


class TableRowCountRecord(BaseModel):
    """Latest diagnostic table row count from `meta.m_Table_Row_Counts`."""

    model_config = ConfigDict(frozen=True)

    schema_name: str
    table_name: str
    row_count: int = 0
    generated_at: datetime | None = None


class WarehouseHealthSnapshot(BaseModel):
    """Warehouse contract & row-count freshness snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "locked", "empty"]
    total_contracts: int = 0
    total_tracked_tables: int = 0
    total_tracked_rows: int = 0
    generated_at: datetime | None = None
    layers: tuple[LayerContractSummary, ...] = ()
    tables: tuple[TableRowCountRecord, ...] = ()
    diagnostics: tuple[str, ...] = ()


class OperationsSnapshot(BaseModel):
    """Complete Phase 19 Operations Run History & Sync Health snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "unavailable", "empty"]
    runs: tuple[OperationRunRecord, ...] = ()
    available_statuses: tuple[str, ...] = ()
    available_commit_states: tuple[str, ...] = ()
    kpis: tuple[OverviewKPI, ...] = ()
    diagnostics: tuple[str, ...] = ()


class SourceFileRecord(BaseModel):
    """Source artifact record from `cp_file_registry` with independent freshness fields."""

    model_config = ConfigDict(frozen=True)

    file_id: str
    file_name: str = ""
    relative_path: str = ""
    file_category: str = ""
    file_type: str = ""
    file_hash: str = ""
    file_size_bytes: int = 0
    sync_status: str = ""
    first_seen_run_id: str = ""
    first_seen_at: datetime | None = None
    last_seen_run_id: str = ""
    last_seen_at: datetime | None = None
    last_changed_run_id: str = ""
    last_changed_at: datetime | None = None
    last_synced_run_id: str = ""
    last_synced_at: datetime | None = None


class SourceRegistryFilter(BaseModel):
    """Filter criteria for `OperationsAnalytics.source_registry`."""

    model_config = ConfigDict(frozen=True)

    category: str | None = None
    sync_status: str | None = None
    search: str | None = None
    limit: int = 200


class SourceRegistrySnapshot(BaseModel):
    """Source registry snapshot from `cp_file_registry`."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "unavailable", "empty"]
    files: tuple[SourceFileRecord, ...] = ()
    total_count: int = 0
    available_categories: tuple[str, ...] = ()
    available_statuses: tuple[str, ...] = ()
    category_counts: dict[str, int] = {}
    kpis: tuple[OverviewKPI, ...] = ()
    diagnostics: tuple[str, ...] = ()


class ArtifactEventRecord(BaseModel):
    """Verbatim artifact lifecycle event from `cp_artifact_run_events`."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    run_id: str = ""
    file_id: str = ""
    event_type: str = ""
    event_at: datetime | None = None
    event_at_raw: str = ""
    observed_path: str = ""
    content_hash: str = ""
    previous_status: str | None = None
    new_status: str = ""
    event_reason: str = ""
    affected_table: str = ""
    detecting_run_id: str = ""
    replay_run_id: str | None = None
    convergence_result: str = ""


class ArtifactEventFilter(BaseModel):
    """Filter criteria for `OperationsAnalytics.artifact_events`."""

    model_config = ConfigDict(frozen=True)

    run_id: str | None = None
    file_id: str | None = None
    event_type: str | None = None
    convergence_result: str | None = None
    limit: int = 200


class ArtifactEventsSnapshot(BaseModel):
    """Artifact event timeline snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "unavailable", "empty"]
    events: tuple[ArtifactEventRecord, ...] = ()
    available_event_types: tuple[str, ...] = ()
    available_convergence: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


class ProviderRequestRecord(BaseModel):
    """External provider request evidence from `cp_external_provider_requests`."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    run_id: str = ""
    provider: str = ""
    entity: str = ""
    requested_start: date | None = None
    requested_end: date | None = None
    attempt_count: int = 0
    status: str = ""
    failure_class: str | None = None
    final_error: str | None = None
    created_at: datetime | None = None
    created_at_raw: str = ""


class ProviderRequestFilter(BaseModel):
    """Filter criteria for `OperationsAnalytics.provider_requests`."""

    model_config = ConfigDict(frozen=True)

    run_id: str | None = None
    provider: str | None = None
    status: str | None = None
    limit: int = 200


class ProviderRequestsSnapshot(BaseModel):
    """External provider requests snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "unavailable", "empty"]
    requests: tuple[ProviderRequestRecord, ...] = ()
    available_providers: tuple[str, ...] = ()
    available_statuses: tuple[str, ...] = ()
    kpis: tuple[OverviewKPI, ...] = ()
    diagnostics: tuple[str, ...] = ()


class RunFailureRecord(BaseModel):
    """Run failure diagnostic record from `cp_run_failures`."""

    model_config = ConfigDict(frozen=True)

    id: int
    run_id: str = ""
    failed_isin: str | None = None
    stage: str = ""
    error_type: str = ""
    error_message: str = ""
    traceback_log: str | None = None
    failure_domain: str = ""
    created_at: datetime | None = None
    created_at_raw: str = ""


class RunFailuresSnapshot(BaseModel):
    """Run failures snapshot."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "unavailable", "empty"]
    failures: tuple[RunFailureRecord, ...] = ()
    diagnostics: tuple[str, ...] = ()


class SnapshotPayloadRecord(BaseModel):
    """Canonical settings or rules snapshot payload from Control Plane."""

    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    kind: Literal["settings", "rules"]
    content_hash: str = ""
    canonical_payload_raw: str = ""
    parsed_payload: dict[str, Any] | None = None
    parse_warning: str | None = None
    created_at: str = ""


class SnapshotDiffEntry(BaseModel):
    """Deterministic structural diff entry between two canonical snapshot payloads."""

    model_config = ConfigDict(frozen=True)

    path: str
    change_type: Literal["added", "removed", "changed"]
    value_a: Any = None
    value_b: Any = None


class SnapshotDiffResult(BaseModel):
    """Structural diff result comparing stored canonical snapshots of two runs."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "unavailable", "parse_error"]
    kind: Literal["settings", "rules"]
    run_a: str
    run_b: str
    snapshot_a_id: str = ""
    snapshot_b_id: str = ""
    payload_a: dict[str, Any] | None = None
    payload_b: dict[str, Any] | None = None
    raw_text_a: str = ""
    raw_text_b: str = ""
    parse_warning_a: str | None = None
    parse_warning_b: str | None = None
    diffs: tuple[SnapshotDiffEntry, ...] = ()
    diagnostics: tuple[str, ...] = ()
