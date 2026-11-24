"""Headless page rendering tests covering all nine navigation routes and non-happy states."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from nicegui import Client
from nicegui.page import page

from pfetl_analytics_studio.context import AppContext
from pfetl_analytics_studio.data import (
    CompatibilityReport,
    CompatibilityStatus,
    ControlPlaneState,
    RunProvenance,
    SurfaceLayer,
    WarehouseState,
)
from pfetl_analytics_studio.pages.explorer import render_explorer_page
from pfetl_analytics_studio.pages.fire import render_fire_page
from pfetl_analytics_studio.pages.household import render_household_page
from pfetl_analytics_studio.pages.investments import render_investments_page
from pfetl_analytics_studio.pages.operations import render_operations_page
from pfetl_analytics_studio.pages.overview import render_overview_page
from pfetl_analytics_studio.pages.reports import render_reports_page
from pfetl_analytics_studio.pages.tax import render_tax_page
from pfetl_analytics_studio.pages.wealth import render_wealth_page
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
    PeriodContext,
    PeriodPreset,
)
from pfetl_analytics_studio.ui import apply_studio_theme

_ALL_RENDERERS: tuple[tuple[str, Callable[[AppContext], None]], ...] = (
    ("overview", render_overview_page),
    ("household", render_household_page),
    ("wealth", render_wealth_page),
    ("investments", render_investments_page),
    ("tax", render_tax_page),
    ("fire", render_fire_page),
    ("operations", render_operations_page),
    ("explorer", render_explorer_page),
    ("reports", render_reports_page),
)


def _collect_client_texts(client: Client) -> list[str]:
    """Collect text strings rendered on NiceGUI elements inside a headless client."""
    texts: list[str] = []
    for elem in client.elements.values():
        text_val = getattr(elem, "text", None)
        if isinstance(text_val, str) and text_val:
            texts.append(text_val)
    return texts


def _make_fake_context(
    tmp_path: Path,
    *,
    locked: bool = False,
    incompatible: bool = False,
    with_profile: bool = True,
) -> AppContext:
    """Build pure in-memory `AppContext` requiring zero DuckDB or SQLite files on disk."""
    if not with_profile:
        return AppContext(profile=None)

    subdir = "locked" if locked else ("incompat" if incompatible else "nominal")
    base = tmp_path / f"fake_outputs_{subdir}"
    base.mkdir(parents=True, exist_ok=True)
    lock_file = base / "pipeline.lock"
    if locked:
        lock_file.write_text("CORE RUNNING", encoding="utf-8")

    fake_profile = PFETLProfile(
        profile_id="fake1234567890ab",
        label="Fake Profile",
        settings_path=tmp_path / "settings.toml",
        rules_path=tmp_path / "rules.toml",
        target_db_base_path=base,
        duckdb_path=base / "Personal_Finance_DB.duckdb",
        control_plane_path=base / "Personal_Finance_DB_Raw_Store.sqlite",
        logs_path=base / "logs" / "runs",
        lock_path=lock_file,
    )
    status = CompatibilityStatus.COMPATIBLE
    wh_state = WarehouseState.LOCKED if locked else WarehouseState.AVAILABLE
    diags: list[str] = []
    missing_surfs: list[str] = []
    if incompatible:
        status = CompatibilityStatus.INCOMPATIBLE
        missing_surfs = ["gold.core_monthly_fact"]
        diags = ["Required upstream surface missing: 'gold.core_monthly_fact'."]
    elif locked:
        status = CompatibilityStatus.DEGRADED

    compat = CompatibilityReport(
        status=status,
        is_compatible=(status == CompatibilityStatus.COMPATIBLE),
        warehouse_state=wh_state,
        control_plane_state=ControlPlaneState.AVAILABLE,
        total_expected=82,
        total_discovered=82 if not incompatible else 81,
        layer_counts={layer: 0 for layer in SurfaceLayer},
        supported_surfaces=[],
        missing_required_surfaces=missing_surfs,
        unexpected_new_surfaces=[],
        missing_required_columns={},
        extra_columns={},
        diagnostics=diags,
    )
    prov = RunProvenance(
        run_id="run-fake-0001",
        started_at=datetime(2026, 11, 24, 14, 0, 0),
        finished_at=datetime(2026, 11, 24, 14, 0, 10),
        status="SUCCESS",
        commit_state="FULLY_COMMITTED",
        application_version="v12.0.0",
        schema_version="v3",
        settings_snapshot_id="snap_s",
        rules_snapshot_id="snap_r",
        settings_payload={},
        rules_payload={},
        has_settings_snapshot=True,
        has_rules_snapshot=True,
        has_execution_log=True,
        is_complete=True,
    )
    period_ctx = PeriodContext(
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31),
        label="Oct 2026",
        preset=PeriodPreset.LATEST_COMPLETE_MONTH,
        financial_year="2026-27",
        as_of_date=date(2026, 11, 24),
    )
    filters = FilterSet(
        filters=(FilterSpec(field=FilterField.ISIN, operator=FilterOperator.EQ, value="INF001"),)
    )
    return AppContext(
        profile=fake_profile,
        provenance=prov,
        compatibility=compat,
        period_context=period_ctx,
        filters=filters,
        refresh_token=1,
    )


def test_all_nine_pages_render_without_database_via_fake_context(tmp_path: Path) -> None:
    """Render every page builder cleanly inside headless NiceGUI Client with fake contexts."""
    nominal_ctx = _make_fake_context(tmp_path)
    empty_ctx = _make_fake_context(tmp_path, with_profile=False)

    for ctx in (nominal_ctx, empty_ctx):
        for route_id, renderer in _ALL_RENDERERS:
            client = Client(page(f"/test_{route_id}"))
            try:
                with client:
                    apply_studio_theme()
                    renderer(ctx)
                rendered_text = " ".join(_collect_client_texts(client))
                assert len(client.elements) > 3
                assert "TODO" not in rendered_text
            finally:
                client.delete()


def test_locked_and_incompatible_pages_render_safely(tmp_path: Path) -> None:
    """Verify locked warehouse blocks analytical views while Operations stays accessible."""
    locked_ctx = _make_fake_context(tmp_path, locked=True)
    incompat_ctx = _make_fake_context(tmp_path, incompatible=True)

    for ctx, expected_fragment in (
        (locked_ctx, "Paused While PFETL Lock Is Active"),
        (incompat_ctx, "Blocked by Compatibility Check"),
    ):
        c_ov = Client(page("/test_blocked_ov"))
        try:
            with c_ov:
                render_overview_page(ctx)
            texts = " ".join(_collect_client_texts(c_ov))
            assert expected_fragment in texts
        finally:
            c_ov.delete()

        c_ops = Client(page("/test_accessible_ops"))
        try:
            with c_ops:
                render_operations_page(ctx)
            texts = " ".join(_collect_client_texts(c_ops))
            assert expected_fragment not in texts
            assert "Operations Workspace Connected" in texts
        finally:
            c_ops.delete()
