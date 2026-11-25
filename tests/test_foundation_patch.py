"""Regression tests for Pre-Phase-9 foundation fixes (session, catalog, runs, and MetricOrigin)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb
import pytest

from pfetl_analytics_studio.analytics import OverviewService
from pfetl_analytics_studio.context import AppSessionManager, RunSelectionMode
from pfetl_analytics_studio.data import CatalogSnapshot, DuckDBReader, DuckDBSession, PFETLCatalog
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    DEFAULT_DATASETS,
    MetricAggregation,
    MetricFormat,
    MetricOrigin,
    MetricSpec,
    PeriodPreset,
    PeriodRequest,
    PeriodResolver,
)
from tests.fixtures.build_fixture_databases import (
    create_full_atlas_databases,
    seed_overview_gold_tables,
)


def _isolated_profile(tmp_path: Path) -> PFETLProfile:
    base_dir = tmp_path / "outputs"
    duck_path, cp_path = create_full_atlas_databases(base_dir)
    seed_overview_gold_tables(duck_path)
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
        f"""
[database]
target_db_base_path = "{base_dir.as_posix()}"
target_db_name = "{duck_path.name}"
raw_document_store_name = "{cp_path.name}"
disable_file_discoverer = false
""",
        encoding="utf-8",
    )
    rules_path = tmp_path / "rules.toml"
    rules_path.write_text('DEFAULT_CURRENCY_ID = "INR_INR"\n', encoding="utf-8")
    loaded = PFETLProfile.load(settings_path, rules_path, label="Foundation Test")
    assert loaded.profile is not None
    return loaded.profile


def test_duckdb_session_reuses_single_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `DuckDBReader.session()` opens DuckDB once across multiple snapshot queries."""
    prof = _isolated_profile(tmp_path)
    reader = DuckDBReader(prof)
    open_calls = 0
    orig_connect = duckdb.connect

    def _counted_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        nonlocal open_calls
        open_calls += 1
        return orig_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", _counted_connect)
    with reader.session() as db:
        assert db.table_exists("gold", "core_monthly_fact") is True
        assert db.fetch_one("SELECT COUNT(*) AS n FROM gold.core_monthly_fact;") is not None
        assert db.fetch_all('SELECT "MONTH_END_DATE" FROM gold.core_monthly_fact LIMIT 2;')
    assert open_calls == 1


def test_catalog_snapshot_avoids_information_schema_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `CatalogSnapshot` answers capability checks without querying `information_schema`."""
    prof = _isolated_profile(tmp_path)
    entries, _ = PFETLCatalog(prof).discover()
    snap = CatalogSnapshot.from_entries(entries)
    assert snap.has_surface("gold.core_monthly_fact") is True
    assert snap.has_column("gold.core_monthly_fact", "Total_Net_Worth_Market") is True
    assert snap.has_column("gold.core_monthly_fact", "NonExistentCol") is False

    period = PeriodResolver(DuckDBReader(prof)).resolve(
        PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH)
    )

    orig_fetch = DuckDBSession.fetch_one

    def _guarded_fetch(
        self: DuckDBSession,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> dict[str, Any] | None:
        assert "information_schema" not in sql.lower()
        return orig_fetch(self, sql, params)

    monkeypatch.setattr(DuckDBSession, "fetch_one", _guarded_fetch)
    ov = OverviewService.get_snapshot(prof, period, catalog_snapshot=snap)
    assert ov.state == "ready"


def test_historical_run_separation_and_freshness_invalidation(tmp_path: Path) -> None:
    """Verify historical inspection never alters analytical_run_id and new runs invalidate cache."""
    prof = _isolated_profile(tmp_path)
    session = AppSessionManager(initial_profile=prof)
    ctx = session.ensure_hydrated()
    assert ctx.analytical_run_id == "run-latest-002"
    assert ctx.catalog_snapshot is not None

    # Selecting an older run for Operations inspection leaves analytical_run_id untouched
    ctx_hist = session.select_run("run-001")
    assert ctx_hist.run_mode == RunSelectionMode.HISTORICAL_RUN
    assert ctx_hist.historical_inspection_run_id == "run-001"
    assert ctx_hist.analytical_run_id == "run-latest-002"
    assert ctx_hist.effective_run_id == "run-latest-002"

    # Insert a newer SUCCESS run in SQLite Control Plane -> check_run_freshness invalidates cache
    scon = sqlite3.connect(prof.control_plane_path)
    try:
        scon.execute(
            """
            INSERT INTO cp_runs (
                run_id, started_at, finished_at, status, commit_state, application_version
            ) VALUES (
                'run-latest-003', '2026-11-25T20:00:00Z', '2026-11-25T20:05:00Z',
                'SUCCESS', 'COMMITTED', 'v12.0.0'
            );
            """
        )
        scon.commit()
    finally:
        scon.close()

    assert session.check_run_freshness() is True
    assert session.context.analytical_run_id == "run-latest-003"


def test_metric_origin_distinguishes_core_and_derived() -> None:
    """Verify `MetricSpec` carries explicit `MetricOrigin` (`CORE` vs `STUDIO_DERIVED`)."""
    ds = DEFAULT_DATASETS.get("core_monthly_fact")
    assert ds.id == "core_monthly_fact"
    derived = MetricSpec(
        id="derived_share",
        label="Derived Share",
        dataset="core_monthly_fact",
        expression='"Total_Income"',
        aggregation=MetricAggregation.LATEST,
        format=MetricFormat.PERCENT,
        unit="percent",
        allowed_dimensions=("MONTH_START_DATE", "MONTH_END_DATE"),
        additive=False,
        description="Presentation-only derived share.",
        origin=MetricOrigin.STUDIO_DERIVED,
    )
    assert derived.origin == MetricOrigin.STUDIO_DERIVED
