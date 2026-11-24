"""Tests for runtime 82-surface catalog discovery, compatibility, provenance, drift, and logs."""

from __future__ import annotations

import json
import sqlite3

import duckdb

from pfetl_analytics_studio.data.catalog import (
    SURFACE_ATLAS,
    CompatibilityStatus,
    PFETLCatalog,
    SurfaceLayer,
    SurfaceUsageCategory,
    evaluate_profile_drift,
)
from pfetl_analytics_studio.data.errors import ControlPlaneState, WarehouseState
from pfetl_analytics_studio.data.log_reader import ArchivedLogReader, LogSource
from pfetl_analytics_studio.profile import PFETLProfile, ProfileDriftStatus


def test_authoritative_atlas_covers_all_82_surfaces() -> None:
    """Assert exact 82-surface coverage across all 5 layers and usage categories."""
    assert len(SURFACE_ATLAS) == 82

    counts = {layer: 0 for layer in SurfaceLayer}
    for spec in SURFACE_ATLAS.values():
        counts[spec.layer] += 1
        assert spec.usage in SurfaceUsageCategory
        assert spec.domain
        assert spec.grain
        assert spec.producer
        assert spec.purpose

    assert counts[SurfaceLayer.BRONZE] == 20
    assert counts[SurfaceLayer.SILVER] == 24
    assert counts[SurfaceLayer.GOLD] == 22
    assert counts[SurfaceLayer.META] == 6
    assert counts[SurfaceLayer.CONTROL_PLANE] == 10


def test_full_catalog_discovery_and_classification(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Verify full 82-surface discovery, meta hydration, and clean compatibility status."""
    catalog = PFETLCatalog(profile_with_full_atlas)
    entries, report = catalog.discover()

    assert report.status == CompatibilityStatus.COMPATIBLE
    assert report.is_compatible is True
    assert report.warehouse_state == WarehouseState.AVAILABLE
    assert report.control_plane_state == ControlPlaneState.AVAILABLE
    assert report.total_expected == 82
    assert report.total_discovered == 82
    assert report.missing_required_surfaces == []
    assert report.unexpected_new_surfaces == []
    assert report.missing_required_columns == {}

    assert report.layer_counts[SurfaceLayer.BRONZE] == 20
    assert report.layer_counts[SurfaceLayer.SILVER] == 24
    assert report.layer_counts[SurfaceLayer.GOLD] == 22
    assert report.layer_counts[SurfaceLayer.META] == 6
    assert report.layer_counts[SurfaceLayer.CONTROL_PLANE] == 10

    cal_entry = entries["silver.dim_calendar"]
    assert cal_entry.classification == SurfaceUsageCategory.DRILL
    assert cal_entry.grain == "Day"
    assert cal_entry.producer == "CoreEngine"
    assert cal_entry.row_count is not None

    fire_entry = entries["gold.wealth_fire_analytics"]
    assert fire_entry.classification == SurfaceUsageCategory.ANALYTICAL
    assert fire_entry.producer == "WealthPresentationEngine"


def test_coverage_gate_fails_on_unclassified_surfaces(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Verify unexpected unclassified Gold or Control Plane tables fail compatibility."""
    dcon = duckdb.connect(str(profile_with_full_atlas.duckdb_path))
    try:
        dcon.execute("CREATE TABLE gold.unclassified_future_mart (id INTEGER);")
    finally:
        dcon.close()

    scon = sqlite3.connect(profile_with_full_atlas.control_plane_path)
    try:
        scon.execute("CREATE TABLE cp_unclassified_audit (id TEXT PRIMARY KEY);")
        scon.commit()
    finally:
        scon.close()

    _, report = PFETLCatalog(profile_with_full_atlas).discover()
    assert report.status == CompatibilityStatus.INCOMPATIBLE
    assert report.is_compatible is False
    assert "gold.unclassified_future_mart" in report.unexpected_new_surfaces
    assert "cp_unclassified_audit" in report.unexpected_new_surfaces


def test_missing_required_surface_and_column_detected(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Verify missing required tables and missing required columns fail compatibility."""
    dcon = duckdb.connect(str(profile_with_full_atlas.duckdb_path))
    try:
        dcon.execute("DROP TABLE gold.tax_reconciliation;")
        dcon.execute("ALTER TABLE gold.core_monthly_fact DROP COLUMN Total_Net_Worth;")
    finally:
        dcon.close()

    _, report = PFETLCatalog(profile_with_full_atlas).discover()
    assert report.status == CompatibilityStatus.INCOMPATIBLE
    assert report.is_compatible is False
    assert "gold.tax_reconciliation" in report.missing_required_surfaces
    assert "gold.core_monthly_fact" in report.missing_required_columns
    assert "Total_Net_Worth" in report.missing_required_columns["gold.core_monthly_fact"]


def test_additive_unknown_columns_are_tolerated(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Verify additive future columns appear in catalog entries without breaking compatibility."""
    dcon = duckdb.connect(str(profile_with_full_atlas.duckdb_path))
    try:
        dcon.execute("ALTER TABLE gold.core_monthly_fact ADD COLUMN Future_Metric VARCHAR;")
    finally:
        dcon.close()

    entries, report = PFETLCatalog(profile_with_full_atlas).discover()
    assert report.status == CompatibilityStatus.COMPATIBLE
    assert report.is_compatible is True
    assert "gold.core_monthly_fact" in report.extra_columns
    assert "Future_Metric" in report.extra_columns["gold.core_monthly_fact"]
    col_names = [c.name for c in entries["gold.core_monthly_fact"].columns]
    assert "Future_Metric" in col_names


def test_run_provenance_latest_and_historical(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Verify provenance chooses latest committed SUCCESS run and preserves historical snapshots."""
    catalog = PFETLCatalog(profile_with_full_atlas)

    latest = catalog.get_latest_provenance()
    assert latest is not None
    assert latest.run_id == "run-latest-002"
    assert latest.status == "SUCCESS"
    assert latest.commit_state == "FULLY_COMMITTED"
    assert latest.application_version == "v12.0.0"
    assert latest.schema_version == "v3"
    assert latest.is_complete is True
    assert latest.settings_payload is not None
    assert latest.rules_payload is not None
    assert latest.rules_payload["DEFAULT_CURRENCY_ID"] == "INR_INR"

    historical = catalog.get_run_provenance("run-hist-001")
    assert historical is not None
    assert historical.application_version == "v11.3.1"
    assert historical.rules_payload is not None
    assert historical.rules_payload["DEFAULT_CURRENCY_ID"] == "USD_USD"
    assert historical.settings_payload is not None
    assert historical.settings_payload["DEFAULT_MARKER"] == "old"


def test_run_provenance_empty_and_corrupt_snapshot_handling(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Verify missing runs return None and corrupt/missing snapshot JSON degrades cleanly."""
    scon = sqlite3.connect(profile_with_full_atlas.control_plane_path)
    try:
        scon.execute(
            "UPDATE cp_rules_snapshots SET canonical_payload = '{bad json' "
            "WHERE snapshot_id = 'snap_rule_latest';"
        )
        scon.commit()
    finally:
        scon.close()

    catalog = PFETLCatalog(profile_with_full_atlas)
    corrupt_prov = catalog.get_latest_provenance()
    assert corrupt_prov is not None
    assert corrupt_prov.is_complete is False
    assert corrupt_prov.rules_payload is None
    assert any("Malformed Rules snapshot" in d for d in corrupt_prov.diagnostics)

    scon = sqlite3.connect(profile_with_full_atlas.control_plane_path)
    try:
        scon.execute("DELETE FROM cp_runs;")
        scon.commit()
    finally:
        scon.close()

    assert catalog.get_latest_provenance() is None


def test_profile_drift_evaluation_states(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Verify MATCH, DRIFTED, and UNVERIFIED_DEFAULTS against latest snapshots."""
    catalog = PFETLCatalog(profile_with_full_atlas)
    prov = catalog.get_latest_provenance()
    assert prov is not None

    # 1. Exact MATCH
    match_res = evaluate_profile_drift(profile_with_full_atlas, prov)
    assert match_res.status == ProfileDriftStatus.MATCH

    # 2. UNVERIFIED DEFAULTS (snapshot includes defaulted keys absent from raw TOML)
    scon = sqlite3.connect(profile_with_full_atlas.control_plane_path)
    try:
        assert prov.settings_payload is not None
        extended_settings = {
            **prov.settings_payload,
            "performance": {"duckdb_threads": 4},
        }
        scon.execute(
            "UPDATE cp_settings_snapshots SET canonical_payload = ? WHERE snapshot_id = ?;",
            (json.dumps(extended_settings), prov.settings_snapshot_id),
        )
        scon.commit()
    finally:
        scon.close()

    updated_prov = catalog.get_latest_provenance()
    defaults_res = evaluate_profile_drift(profile_with_full_atlas, updated_prov)
    assert defaults_res.status == ProfileDriftStatus.UNVERIFIED_DEFAULTS
    assert any("performance.duckdb_threads" in d for d in defaults_res.details)

    # 3. DRIFTED (user modified Rules TOML locally without re-running Core)
    profile_with_full_atlas.rules_path.write_text(
        'DEFAULT_CURRENCY_ID = "EUR_EUR"\n',
        encoding="utf-8",
    )
    drifted_res = evaluate_profile_drift(profile_with_full_atlas, updated_prov)
    assert drifted_res.status == ProfileDriftStatus.DRIFTED
    assert any("DEFAULT_CURRENCY_ID" in d for d in drifted_res.details)


def test_archived_log_reader_zlib_decompression(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Verify ArchivedLogReader decompresses zlib BLOB and preserves chronological order."""
    log_reader = ArchivedLogReader(profile_with_full_atlas)
    assert log_reader.has_log("run-latest-002") is True

    res = log_reader.read_run("run-latest-002")
    assert res.source == LogSource.SQLITE_BLOB
    assert res.malformed_lines == 0
    assert res.total_lines == 3
    assert [e["sequence"] for e in res.events] == [1, 2, 3]
    assert [e["event_code"] for e in res.events] == [
        "RUN_START",
        "SCHEMA_VALIDATION",
        "RUN_COMPLETED",
    ]
    assert res.events[1]["context"]["duration_ms"] == 12.5


def test_archived_log_reader_fallback_and_malformed_lines(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Verify fallback trace_<run_id>.jsonl loading and malformed line tolerance."""
    assert profile_with_full_atlas.logs_path is not None
    profile_with_full_atlas.logs_path.mkdir(parents=True, exist_ok=True)
    trace_file = profile_with_full_atlas.logs_path / "trace_run-failed-003.jsonl"
    trace_file.write_text(
        "\n".join(
            [
                json.dumps({"sequence": 1, "event_code": "RUN_START", "message": "ok"}),
                "{corrupted_json_line",
                "[1, 2, 3]",
                json.dumps({"sequence": 2, "event_code": "RUN_FAILED", "message": "boom"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    log_reader = ArchivedLogReader(profile_with_full_atlas)
    assert log_reader.has_log("run-failed-003") is True
    res = log_reader.read_run("run-failed-003")

    assert res.source == LogSource.FILESYSTEM_FALLBACK
    assert res.total_lines == 4
    assert res.malformed_lines == 2
    assert len(res.events) == 2
    assert [e["event_code"] for e in res.events] == ["RUN_START", "RUN_FAILED"]


def test_catalog_degrades_gracefully_under_pipeline_lock(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Verify warehouse lock blocks DuckDB access while Control Plane and provenance work."""
    lock_file = profile_with_full_atlas.lock_path
    lock_file.write_text("LOCKED", encoding="utf-8")
    try:
        catalog = PFETLCatalog(profile_with_full_atlas)
        entries, report = catalog.discover()

        assert report.warehouse_state == WarehouseState.LOCKED
        assert report.control_plane_state == ControlPlaneState.AVAILABLE
        assert report.status == CompatibilityStatus.DEGRADED
        assert report.is_compatible is False
        assert report.layer_counts[SurfaceLayer.CONTROL_PLANE] == 10
        assert report.layer_counts[SurfaceLayer.GOLD] == 0
        assert "cp_runs" in entries

        prov = catalog.get_latest_provenance()
        assert prov is not None
        assert prov.run_id == "run-latest-002"
    finally:
        if lock_file.exists():
            lock_file.unlink()
