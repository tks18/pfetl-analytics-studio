"""Typed DTOs for Phase 19 Operations Run History, Sync Health & Warehouse Freshness."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

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
