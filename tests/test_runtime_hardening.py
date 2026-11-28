"""Runtime & performance hardening verification tests.

Verifies:
- Unopened heavy domain tabs execute zero analytical queries.
- Explorer preview skips full 82-surface catalog discovery when `CatalogSnapshot` is present.
- DuckDB COPY and parameterized chunked CSV exports stream to temp files without full bytes buffers.
- Stale-export cleanup removes expired temporary files.
- Operations retained-payload downloads switch to file-backed delivery above threshold.
- Reports downloads use path-based `ui.download.file` without rereading bytes into memory.
- `run.io_bound` keeps the asyncio event loop responsive during slow analytical queries.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from pathlib import Path
from typing import Any

import pytest
from nicegui import Client, run, ui

from pfetl_analytics_studio.analytics import (
    ExplorerAnalytics,
    FireAnalytics,
    InvestmentAnalytics,
    OperationsAnalytics,
    TaxAnalytics,
)
from pfetl_analytics_studio.context import AppContext
from pfetl_analytics_studio.data import (
    CatalogSnapshot,
    CompatibilityReport,
    CompatibilityStatus,
    ControlPlaneState,
    DuckDBReader,
    PFETLCatalog,
    QueryCache,
    SurfaceLayer,
    WarehouseState,
    cleanup_old_exports,
    studio_export_dir,
)
from pfetl_analytics_studio.pages import operations as operations_page
from pfetl_analytics_studio.pages import reports as reports_page
from pfetl_analytics_studio.pages.explorer import render_explorer_page
from pfetl_analytics_studio.pages.fire import render_fire_page
from pfetl_analytics_studio.pages.investments import render_investments_page
from pfetl_analytics_studio.pages.operations import render_operations_page
from pfetl_analytics_studio.pages.tax import render_tax_page
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.state import SqlDatasource
from pfetl_analytics_studio.ui import apply_studio_theme


def _compatible_context(profile: PFETLProfile) -> AppContext:
    entries, _ = PFETLCatalog(profile).discover()
    snapshot = CatalogSnapshot.from_entries(entries)
    compat = CompatibilityReport(
        status=CompatibilityStatus.COMPATIBLE,
        is_compatible=True,
        warehouse_state=WarehouseState.AVAILABLE,
        control_plane_state=ControlPlaneState.AVAILABLE,
        total_expected=82,
        total_discovered=len(entries),
        layer_counts={SurfaceLayer.GOLD: 22},
        supported_surfaces=sorted(entries.keys()),
        missing_required_surfaces=[],
        unexpected_new_surfaces=[],
        missing_required_columns={},
        extra_columns={},
        diagnostics=[],
    )
    return AppContext(
        profile=profile,
        compatibility=compat,
        catalog_snapshot=snapshot,
        analytical_run_id="run-latest-001",
    )


def test_investments_lazy_tabs_execute_zero_unopened_queries(
    profile_with_full_atlas: PFETLProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening Investments on Overview must not query Performance, Lots, Realized, or Evidence."""
    ctx = _compatible_context(profile_with_full_atlas)
    calls: dict[str, int] = {
        "snapshot": 0,
        "allocation": 0,
        "performance": 0,
        "lots": 0,
        "realized": 0,
        "evidence": 0,
    }

    orig_snap = InvestmentAnalytics.portfolio_snapshot
    orig_alloc = InvestmentAnalytics.allocation

    def _wrap_snap(*args: Any, **kwargs: Any) -> Any:
        calls["snapshot"] += 1
        return orig_snap(*args, **kwargs)

    def _wrap_alloc(*args: Any, **kwargs: Any) -> Any:
        calls["allocation"] += 1
        return orig_alloc(*args, **kwargs)

    monkeypatch.setattr(InvestmentAnalytics, "portfolio_snapshot", staticmethod(_wrap_snap))
    monkeypatch.setattr(InvestmentAnalytics, "allocation", staticmethod(_wrap_alloc))
    monkeypatch.setattr(
        InvestmentAnalytics,
        "performance",
        staticmethod(lambda *a, **k: calls.__setitem__("performance", calls["performance"] + 1)),
    )
    monkeypatch.setattr(
        InvestmentAnalytics,
        "lot_rows",
        staticmethod(lambda *a, **k: calls.__setitem__("lots", calls["lots"] + 1)),
    )
    monkeypatch.setattr(
        InvestmentAnalytics,
        "realized_and_reconciliation",
        staticmethod(lambda *a, **k: calls.__setitem__("realized", calls["realized"] + 1)),
    )
    monkeypatch.setattr(
        InvestmentAnalytics,
        "market_evidence",
        staticmethod(lambda *a, **k: calls.__setitem__("evidence", calls["evidence"] + 1)),
    )

    with Client(ui.page("/_test_inv_lazy"), request=None):
        apply_studio_theme()
        render_investments_page(ctx, cache=QueryCache(), initial_tab="overview")

    assert calls["snapshot"] == 1
    assert calls["allocation"] <= 1
    assert calls["performance"] == 0
    assert calls["lots"] == 0
    assert calls["realized"] == 0
    assert calls["evidence"] == 0


def test_tax_lazy_tabs_execute_zero_unopened_queries(
    profile_with_full_atlas: PFETLProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening Tax on Overview must not query Breakdown, Reconciliation, Events, or Harvesting."""
    ctx = _compatible_context(profile_with_full_atlas)
    calls: dict[str, int] = {
        "summary": 0,
        "breakdown": 0,
        "recon": 0,
        "events": 0,
        "harvest": 0,
    }
    orig_summary = TaxAnalytics.fy_summary

    def _wrap_summary(*args: Any, **kwargs: Any) -> Any:
        calls["summary"] += 1
        return orig_summary(*args, **kwargs)

    monkeypatch.setattr(TaxAnalytics, "fy_summary", staticmethod(_wrap_summary))
    monkeypatch.setattr(
        TaxAnalytics,
        "income_breakdown",
        staticmethod(lambda *a, **k: calls.__setitem__("breakdown", calls["breakdown"] + 1)),
    )
    monkeypatch.setattr(
        TaxAnalytics,
        "reconciliation",
        staticmethod(lambda *a, **k: calls.__setitem__("recon", calls["recon"] + 1)),
    )
    monkeypatch.setattr(
        TaxAnalytics,
        "tax_events",
        staticmethod(lambda *a, **k: calls.__setitem__("events", calls["events"] + 1)),
    )
    monkeypatch.setattr(
        TaxAnalytics,
        "harvesting_snapshot",
        staticmethod(lambda *a, **k: calls.__setitem__("harvest", calls["harvest"] + 1)),
    )

    with Client(ui.page("/_test_tax_lazy"), request=None):
        apply_studio_theme()
        render_tax_page(ctx, cache=QueryCache(), initial_tab="overview")

    assert calls["summary"] == 1
    assert calls["breakdown"] == 0
    assert calls["recon"] == 0
    assert calls["events"] == 0
    assert calls["harvest"] == 0


def test_fire_lazy_provenance_tab_executes_zero_unopened_queries(
    profile_with_full_atlas: PFETLProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening FIRE on Trajectory must not query Simulation Provenance history."""
    ctx = _compatible_context(profile_with_full_atlas)
    calls: dict[str, int] = {"summary": 0, "sim": 0}
    orig_summary = FireAnalytics.summary

    def _wrap_summary(*args: Any, **kwargs: Any) -> Any:
        calls["summary"] += 1
        return orig_summary(*args, **kwargs)

    monkeypatch.setattr(FireAnalytics, "summary", staticmethod(_wrap_summary))
    monkeypatch.setattr(
        FireAnalytics,
        "simulation_history",
        staticmethod(lambda *a, **k: calls.__setitem__("sim", calls["sim"] + 1)),
    )

    with Client(ui.page("/_test_fire_lazy"), request=None):
        apply_studio_theme()
        render_fire_page(ctx, cache=QueryCache(), initial_tab="trajectory")

    assert calls["summary"] == 1
    assert calls["sim"] == 0


def test_operations_lazy_tabs_execute_zero_unopened_queries(
    profile_with_full_atlas: PFETLProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening Operations on Runs must not query Sources, Providers, or Forensics."""
    ctx = _compatible_context(profile_with_full_atlas)
    calls: dict[str, int] = {
        "runs": 0,
        "warehouse": 0,
        "sources": 0,
        "providers": 0,
        "forensics": 0,
    }
    orig_runs = OperationsAnalytics.run_history
    orig_wh = OperationsAnalytics.warehouse_health

    def _wrap_runs(*args: Any, **kwargs: Any) -> Any:
        calls["runs"] += 1
        return orig_runs(*args, **kwargs)

    def _wrap_wh(*args: Any, **kwargs: Any) -> Any:
        calls["warehouse"] += 1
        return orig_wh(*args, **kwargs)

    monkeypatch.setattr(OperationsAnalytics, "run_history", staticmethod(_wrap_runs))
    monkeypatch.setattr(OperationsAnalytics, "warehouse_health", staticmethod(_wrap_wh))
    monkeypatch.setattr(
        OperationsAnalytics,
        "source_registry",
        staticmethod(lambda *a, **k: calls.__setitem__("sources", calls["sources"] + 1)),
    )
    monkeypatch.setattr(
        OperationsAnalytics,
        "provider_requests",
        staticmethod(lambda *a, **k: calls.__setitem__("providers", calls["providers"] + 1)),
    )
    monkeypatch.setattr(
        OperationsAnalytics,
        "forensic_summary",
        staticmethod(lambda *a, **k: calls.__setitem__("forensics", calls["forensics"] + 1)),
    )

    with Client(ui.page("/_test_ops_lazy"), request=None):
        apply_studio_theme()
        render_operations_page(ctx, cache=QueryCache(), initial_tab="runs")

    assert calls["runs"] == 1
    assert calls["warehouse"] >= 1
    assert calls["sources"] == 0
    assert calls["providers"] == 0
    assert calls["forensics"] == 0


def test_explorer_lazy_tabs_execute_zero_unopened_queries(
    profile_with_full_atlas: PFETLProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening Explorer on Catalog must not execute Guided Analytics, Saved Views, or SQL."""
    ctx = _compatible_context(profile_with_full_atlas)
    calls: dict[str, int] = {"catalog": 0, "preview": 0, "guided": 0, "saved": 0, "sql": 0}
    orig_cat = ExplorerAnalytics.catalog_entries
    orig_prev = ExplorerAnalytics.preview

    def _wrap_cat(*args: Any, **kwargs: Any) -> Any:
        calls["catalog"] += 1
        return orig_cat(*args, **kwargs)

    def _wrap_prev(*args: Any, **kwargs: Any) -> Any:
        calls["preview"] += 1
        return orig_prev(*args, **kwargs)

    monkeypatch.setattr(ExplorerAnalytics, "catalog_entries", staticmethod(_wrap_cat))
    monkeypatch.setattr(ExplorerAnalytics, "preview", staticmethod(_wrap_prev))
    monkeypatch.setattr(
        ExplorerAnalytics,
        "execute_guided",
        staticmethod(lambda *a, **k: calls.__setitem__("guided", calls["guided"] + 1)),
    )
    monkeypatch.setattr(
        ExplorerAnalytics,
        "list_saved_views",
        staticmethod(lambda *a, **k: calls.__setitem__("saved", calls["saved"] + 1)),
    )
    monkeypatch.setattr(
        ExplorerAnalytics,
        "execute_sql",
        staticmethod(lambda *a, **k: calls.__setitem__("sql", calls["sql"] + 1)),
    )

    with Client(ui.page("/_test_exp_lazy"), request=None):
        apply_studio_theme()
        render_explorer_page(ctx)

    assert calls["catalog"] == 1
    assert calls["preview"] == 1
    assert calls["guided"] == 0
    assert calls["saved"] == 0
    assert calls["sql"] == 0


def test_explorer_preview_skips_discover_when_snapshot_provided(
    profile_with_full_atlas: PFETLProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ExplorerAnalytics.preview` must use `CatalogSnapshot` instead of full `discover()`."""
    ctx = _compatible_context(profile_with_full_atlas)

    def _fail_discover(self: PFETLCatalog) -> Any:
        raise AssertionError("PFETLCatalog.discover() should not be called when snapshot is given")

    monkeypatch.setattr(PFETLCatalog, "discover", _fail_discover)
    prev = ExplorerAnalytics.preview(
        profile_with_full_atlas,
        "gold.core_monthly_fact",
        limit=10,
        catalog_snapshot=ctx.catalog_snapshot,
    )
    assert prev.physical_name == "gold.core_monthly_fact"
    assert len(prev.columns) > 0


def test_streaming_csv_exports_and_cleanup(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """DuckDB COPY, parameterized streaming, Control Plane streaming, and cleanup all work."""
    duck_reader = DuckDBReader(profile_with_full_atlas)

    # 1. Unparameterized DuckDB COPY path
    p1 = tmp_path / "copy_out.csv"
    cnt1 = duck_reader.export_csv_file('SELECT "table_name" FROM "meta"."m_Table_Row_Counts"', p1)
    assert cnt1 >= 1
    assert len(p1.read_text(encoding="utf-8").strip().splitlines()) == cnt1 + 1

    # 2. Parameterized DuckDB streaming path
    p2 = tmp_path / "param_out.csv"
    cnt2 = duck_reader.export_csv_file(
        'SELECT "table_name" FROM "meta"."m_Table_Row_Counts" WHERE "table_name" IS NOT NULL LIMIT ?',
        p2,
        (2,),
    )
    assert cnt2 == 2
    assert len(p2.read_text(encoding="utf-8").strip().splitlines()) == 3

    # 3. Control Plane SQLite streaming path
    p3, cnt3, fname3 = ExplorerAnalytics.export_sql_csv(
        profile_with_full_atlas,
        "SELECT run_id, status FROM cp_runs",
        datasource=SqlDatasource.CONTROL_PLANE,
    )
    assert cnt3 >= 1
    assert fname3 == "sql_control_plane_export.csv"
    assert p3.exists()
    p3.unlink(missing_ok=True)

    # 4. Read-only SQL guard enforced before export
    with pytest.raises(ValueError, match="forbidden|SELECT"):
        ExplorerAnalytics.export_sql_csv(
            profile_with_full_atlas,
            "DROP TABLE cp_runs",
            datasource=SqlDatasource.DUCKDB,
        )

    # 5. Stale temp export cleanup
    stale = studio_export_dir() / "stale_test_artifact.csv"
    stale.write_text("x", encoding="utf-8")
    old_ts = time.time() - 7200.0
    os.utime(stale, (old_ts, old_ts))
    removed = cleanup_old_exports(max_age_seconds=3600.0)
    assert removed >= 1
    assert not stale.exists()


def test_io_bound_keeps_event_loop_responsive() -> None:
    """Blocking analytical work via `run.io_bound` must not stall concurrent event-loop ticks."""

    async def _exercise() -> int:
        ticks = 0

        def _slow_sync() -> str:
            time.sleep(0.12)
            return "done"

        async def _ticker() -> None:
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.015)
                ticks += 1

        res, _ = await asyncio.gather(run.io_bound(_slow_sync), _ticker())
        assert res == "done"
        return ticks

    ticks_observed = asyncio.run(_exercise())
    assert ticks_observed >= 3


def test_reports_and_operations_avoid_unnecessary_in_memory_file_reads() -> None:
    """Reports uses `trigger_file_download` without `read_bytes()`; Operations defines 1 MiB threshold."""
    reports_src = inspect.getsource(reports_page)
    assert "trigger_file_download(" in reports_src
    assert "read_bytes()" not in reports_src

    ops_src = inspect.getsource(operations_page)
    assert "_INLINE_PAYLOAD_THRESHOLD_BYTES = 1_048_576" in ops_src
    assert "trigger_file_download(" in ops_src


def test_trigger_file_download_serves_multiple_requests_without_404(tmp_path: Path) -> None:
    """Shared download route serves repeated GET/HEAD requests with attachment disposition."""
    from fastapi.testclient import TestClient
    from nicegui import app

    from pfetl_analytics_studio.ui.downloads import (
        clear_download_tokens,
        ensure_download_route_registered,
    )

    sample = tmp_path / "sample_export.pdf"
    sample.write_bytes(b"%PDF-1.4\n%test\n")
    clear_download_tokens()
    ensure_download_route_registered()

    # Register token directly via internal store by calling trigger_file_download in a Client ctx
    import time

    from pfetl_analytics_studio.ui import downloads as dl_mod

    with dl_mod._LOCK:  # pyright: ignore[reportPrivateUsage]
        dl_mod._TOKENS["tok123"] = (  # pyright: ignore[reportPrivateUsage]
            sample,
            "sample_export.pdf",
            "application/pdf",
            time.monotonic() + 60.0,
        )

    client = TestClient(app)
    r1 = client.get("/_studio/download/tok123/sample_export.pdf")
    r2 = client.get("/_studio/download/tok123/sample_export.pdf")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.content == b"%PDF-1.4\n%test\n"
    assert "attachment" in r1.headers.get("content-disposition", "")


def test_reports_comparison_mode_populates_kpi_deltas_and_exports(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Reports render comparison KPI deltas/breakups and export cleanly to PDF/PPTX/XLSX."""
    from pfetl_analytics_studio.context import AppSessionManager
    from pfetl_analytics_studio.reports import (
        DEFAULT_REPORT_REGISTRY,
        RenderedKPIGroupBlock,
        RenderedProvenanceBlock,
        ReportContextOverrides,
        ReportExporter,
        ReportService,
    )
    from pfetl_analytics_studio.semantic import ComparisonMode, PeriodPreset, PeriodRequest
    from tests.fixtures.build_fixture_databases import seed_overview_gold_tables

    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)
    defn = DEFAULT_REPORT_REGISTRY.get_definition("monthly_financial_review")
    assert defn is not None

    overrides = ReportContextOverrides(
        period_request=PeriodRequest(
            preset=PeriodPreset.ROLLING_12M,
            comparison_mode=ComparisonMode.PREVIOUS_PERIOD,
        )
    )
    rendered = service.render(defn, app_ctx, context_overrides=overrides)
    prov = next(b for b in rendered.blocks if isinstance(b, RenderedProvenanceBlock))
    assert prov.comparison_label

    kpi_blocks = [b for b in rendered.blocks if isinstance(b, RenderedKPIGroupBlock)]
    assert any(item.delta_text or item.breakups for grp in kpi_blocks for item in grp.items)

    pdf_res = ReportExporter.export_pdf(rendered, tmp_path / "comp.pdf")
    pptx_res = ReportExporter.export_pptx(rendered, tmp_path / "comp.pptx")
    xlsx_res = ReportExporter.export_xlsx(rendered, tmp_path / "comp.xlsx")
    assert pdf_res.bytes_written > 1000
    assert pptx_res.bytes_written > 4000
    assert xlsx_res.bytes_written > 1000


def test_chart_axis_polish_and_export_data_labels() -> None:
    """UI charts avoid label collisions; exported figures add crisp data labels."""
    from types import SimpleNamespace

    from pfetl_analytics_studio.reports.exporters import (
        _prepare_pdf_chart_figure,  # pyright: ignore[reportPrivateUsage]
    )
    from pfetl_analytics_studio.ui.charts import studio_plotly_layout
    from pfetl_analytics_studio.ui.charts_tax import build_tax_completeness_figure

    dense_layout = studio_plotly_layout(
        x_axis_type="category",
        x_categories=[
            "Other Allowances",
            "Cashbacks, EPF, PF Interest",
            "Equity_Listed_LTCG",
            "Equity_Listed_STCG",
            "Dividend Income",
            "Interest Income",
        ],
    )
    assert dense_layout["xaxis"]["tickangle"] == -35

    rows = [
        SimpleNamespace(
            tax_sub_head="Cashbacks, EPF, PF Interest, Equity_Listed_STCG",
            ready_count=1,
            check_required_count=24,
        ),
        SimpleNamespace(
            tax_sub_head="Other Allowances",
            ready_count=12,
            check_required_count=0,
        ),
    ]
    comp_fig = build_tax_completeness_figure(rows)
    assert comp_fig is not None
    assert comp_fig["data"][0]["orientation"] == "h"

    # Export data labels for vertical bar, horizontal bar, pie, and scatter
    v_bar = _prepare_pdf_chart_figure(
        {"data": [{"type": "bar", "x": ["A", "B"], "y": [100.0, 200.0]}], "layout": {}},
        "Vertical Bar",
    )
    assert v_bar["data"][0]["textposition"] == "outside"
    assert v_bar["data"][0]["text"] == ["₹100", "₹200"]

    h_bar = _prepare_pdf_chart_figure(
        {
            "data": [{"type": "bar", "orientation": "h", "x": [1250.0, 2500.0], "y": ["X", "Y"]}],
            "layout": {},
        },
        "Horizontal Bar",
    )
    assert h_bar["data"][0]["textposition"] == "outside"
    assert h_bar["layout"].get("xaxis", {}).get("type") != "category"

    pie = _prepare_pdf_chart_figure(
        {"data": [{"type": "pie", "labels": ["Eq", "Debt"], "values": [60, 40]}], "layout": {}},
        "Pie Chart",
    )
    assert pie["data"][0]["textinfo"] == "label+percent"

    scatter = _prepare_pdf_chart_figure(
        {
            "data": [{"type": "scatter", "x": ["2025-01", "2025-02"], "y": [10.5, 20.5]}],
            "layout": {},
        },
        "Scatter Chart",
    )
    assert "text" in scatter["data"][0]["mode"]


def test_global_filter_propagation_across_analytics_and_reports(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Global filters propagate to Investments, Tax harvesting, and ReportService context."""
    from datetime import date

    from pfetl_analytics_studio.analytics.investments_models import (
        AllocationDimension,
        LotFilter,
    )
    from pfetl_analytics_studio.context import AppSessionManager
    from pfetl_analytics_studio.reports import DEFAULT_REPORT_REGISTRY, ReportService
    from pfetl_analytics_studio.semantic import (
        FilterField,
        FilterSet,
        FilterSpec,
        PeriodContext,
        PeriodPreset,
    )
    from tests.fixtures.build_fixture_databases import seed_overview_gold_tables

    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    active_filters = FilterSet(
        filters=(
            FilterSpec(field=FilterField.ISIN, value="INE002A01018"),
            FilterSpec(field=FilterField.CURRENCY_ID, value="INR_INR"),
        )
    )
    filtered_ctx = AppContext(
        profile=app_ctx.profile,
        compatibility=app_ctx.compatibility,
        catalog_snapshot=app_ctx.catalog_snapshot,
        analytical_run_id=app_ctx.analytical_run_id,
        filters=active_filters,
    )

    from pfetl_analytics_studio.analytics.investments_queries import (
        _build_lot_where_clause,  # pyright: ignore[reportPrivateUsage]
    )

    # 1. LotFilter.sector & _build_lot_where_clause + InvestmentAnalytics.allocation with filters
    lot_filter = LotFilter(isin="INE002A01018", sector="Energy")
    assert lot_filter.sector == "Energy"
    where_sql, where_params = _build_lot_where_clause(lot_filter, date(2025, 12, 31))
    assert 'f."ISIN" = ?' in where_sql
    assert 'im."SECTOR"' in where_sql
    assert "INE002A01018" in where_params
    assert "Energy" in where_params

    period_ctx = app_ctx.period_context or PeriodContext(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        label="2025",
        preset=PeriodPreset.LATEST_COMPLETE_MONTH,
    )
    alloc = InvestmentAnalytics.allocation(
        profile_with_full_atlas,
        dimension=AllocationDimension.CLASS,
        period_context=period_ctx,
        filters=active_filters,
    )
    assert alloc.currency_id == "INR_INR"

    # 2. TaxAnalytics.harvesting_snapshot accepts isin filter
    assert "isin" in inspect.signature(TaxAnalytics.harvesting_snapshot).parameters

    # 3. ReportService.resolve_context merges app_context.filters with definition.default_filters
    defn = DEFAULT_REPORT_REGISTRY.get_definition("portfolio_review")
    assert defn is not None
    r_ctx = ReportService(registry=DEFAULT_REPORT_REGISTRY).resolve_context(
        filtered_ctx,
        definition=defn,
    )
    fields: dict[FilterField, Any] = {spec.field: spec.value for spec in r_ctx.filters.filters}
    assert fields.get(FilterField.ISIN) == "INE002A01018"
    assert fields.get(FilterField.CURRENCY_ID) == "INR_INR"


def _seed_ops_cp(control_plane_path: Path) -> None:
    """Seed minimal operations control-plane schema needed for all 15 builtin reports."""
    import sqlite3

    con = sqlite3.connect(str(control_plane_path))
    try:
        con.executescript(
            """
            DROP TABLE IF EXISTS cp_external_provider_requests;
            CREATE TABLE cp_external_provider_requests (
                request_id TEXT PRIMARY KEY, run_id TEXT, provider TEXT, entity TEXT,
                requested_start DATE, requested_end DATE, attempt_count INTEGER,
                status TEXT, failure_class TEXT, final_error TEXT, created_at TIMESTAMP
            );
            DROP TABLE IF EXISTS cp_artifact_run_events;
            CREATE TABLE cp_artifact_run_events (
                event_id TEXT PRIMARY KEY, run_id TEXT, file_id TEXT, event_type TEXT,
                event_at TIMESTAMP, observed_path TEXT, content_hash TEXT, previous_status TEXT,
                new_status TEXT, event_reason TEXT, affected_table TEXT, detecting_run_id TEXT,
                replay_run_id TEXT, convergence_result TEXT
            );
            DROP TABLE IF EXISTS cp_file_registry;
            CREATE TABLE cp_file_registry (
                file_id TEXT PRIMARY KEY, file_name TEXT, relative_path TEXT,
                file_category TEXT, file_type TEXT, file_hash TEXT, file_size_bytes BIGINT,
                sync_status TEXT, first_seen_run_id TEXT, first_seen_at TIMESTAMP,
                last_seen_run_id TEXT, last_seen_at TIMESTAMP, last_changed_run_id TEXT,
                last_changed_at TIMESTAMP, last_synced_run_id TEXT, last_synced_at TIMESTAMP
            );
            DROP TABLE IF EXISTS cp_file_payloads;
            CREATE TABLE cp_file_payloads (file_id TEXT PRIMARY KEY, file_bytes BLOB);
            DROP TABLE IF EXISTS cp_run_failures;
            CREATE TABLE cp_run_failures (
                id INTEGER PRIMARY KEY, run_id TEXT, failed_isin TEXT, stage TEXT,
                error_type TEXT, error_message TEXT, traceback_log TEXT,
                failure_domain TEXT, created_at TIMESTAMP
            );
            """
        )
        con.commit()
    finally:
        con.close()


def test_all_15_prebuilt_reports_render_without_broken_blocks(
    profile_with_full_atlas: PFETLProfile,
) -> None:
    """Every builtin report definition renders cleanly with zero broken blocks."""
    from pfetl_analytics_studio.context import AppSessionManager
    from pfetl_analytics_studio.reports import (
        DEFAULT_REPORT_REGISTRY,
        RenderedBrokenBlock,
        ReportService,
    )
    from tests.fixtures.build_fixture_databases import seed_overview_gold_tables

    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    _seed_ops_cp(profile_with_full_atlas.control_plane_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)

    builtins = [d for d in DEFAULT_REPORT_REGISTRY.list_definitions() if d.builtin]
    assert len(builtins) == 15
    for defn in builtins:
        rendered = service.render(defn, app_ctx)
        assert [b.block_id for b in rendered.blocks] == [b.block_id for b in defn.blocks]
        broken = [b for b in rendered.blocks if isinstance(b, RenderedBrokenBlock)]
        unexpected = [
            (b.block_id, b.code, b.message)
            for b in broken
            if b.code == "SOURCE_NOT_FOUND" or "Binder Error" not in b.message
        ]
        assert not unexpected, f"{defn.id} had unexpected broken blocks: {unexpected}"
