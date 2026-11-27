"""Tests for Report Definition, Provenance, Registry, and Runtime."""

from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
import zipfile

import plotly.io as pio  # type: ignore[import-untyped]
import polars as pl
from pptx import Presentation
import pytest
from nicegui import Client
from nicegui.page import page

from pfetl_analytics_studio.context import AppSessionManager
from pfetl_analytics_studio.pages.reports import render_reports_page
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.reports import (
    DEFAULT_REPORT_REGISTRY,
    ChartBlock,
    DividerBlock,
    DuplicateReportRegistrationError,
    ExportPrerequisiteError,
    KPIGroupBlock,
    NarrativeBlock,
    PageBreakBlock,
    ProvenanceBlock,
    RegisteredBlockSource,
    RenderedBrokenBlock,
    RenderedChartBlock,
    RenderedKPIGroupBlock,
    RenderedProvenanceBlock,
    RenderedStructuralBlock,
    RenderedTableBlock,
    RenderedTextBlock,
    ReportContext,
    ReportContextOverrides,
    ReportDefinition,
    ReportDefinitionDraft,
    ReportExporter,
    ReportKpiItem,
    ReportRegistry,
    ReportResult,
    ReportService,
    ReportSourceKind,
    RequiredReportBlockError,
    SavedViewBlock,
    SubtitleBlock,
    TableBlock,
    TitleBlock,
    clone_definition_as_draft,
    compare_report_provenance,
    sanitize_excel_sheet_name,
    sanitize_filename,
    validate_report_draft,
)
from pfetl_analytics_studio.semantic import PeriodContext, PeriodPreset, PeriodRequest
from pfetl_analytics_studio.state import (
    CURRENT_SCHEMA_VERSION,
    SavedViewKind,
    SqlDatasource,
    StudioStateStore,
)
from tests.fixtures.build_fixture_databases import seed_overview_gold_tables


def test_report_registry_rejects_duplicate_ids() -> None:
    """ReportRegistry enforces strict uniqueness for block sources and definitions."""
    registry = ReportRegistry()
    src = DEFAULT_REPORT_REGISTRY.get_source("overview.kpis")
    assert src is not None
    registry.register_source(src)
    with pytest.raises(DuplicateReportRegistrationError):
        registry.register_source(
            RegisteredBlockSource(
                source_id="overview.kpis",
                kind=ReportSourceKind.KPI_GROUP,
                title="Duplicate",
                executor=src.executor,
            )
        )

    defn = DEFAULT_REPORT_REGISTRY.get_definition("monthly_financial_review")
    assert defn is not None
    registry.register_definition(defn)
    with pytest.raises(DuplicateReportRegistrationError):
        registry.register_definition(defn)


def test_report_definition_json_roundtrip_in_studio_sqlite(
    temp_state_store: StudioStateStore,
) -> None:
    """ReportDefinition round-trips cleanly through StudioStateStore `saved_reports`."""
    assert CURRENT_SCHEMA_VERSION >= 5
    defn = DEFAULT_REPORT_REGISTRY.get_definition("monthly_financial_review")
    assert defn is not None

    custom_defn = defn.model_copy(
        update={
            "id": "custom_exec_01",
            "name": "My Custom Snapshot",
            "builtin": False,
        }
    )
    saved = temp_state_store.save_report_definition(
        profile_id="prof_123",
        name=custom_defn.name,
        description=custom_defn.description,
        definition_json=custom_defn.model_dump_json(),
        report_id=custom_defn.id,
    )
    loaded_rec = temp_state_store.get_report_definition(saved.report_id)
    assert loaded_rec is not None
    restored = ReportDefinition.model_validate_json(loaded_rec.definition_json)
    assert restored == custom_defn
    assert [b.block_id for b in restored.blocks] == [b.block_id for b in custom_defn.blocks]


def test_report_service_renders_fixture_in_exact_order_and_pins_provenance(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """ReportService preserves block order, pins run snapshots, and replays Saved Views."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    session = AppSessionManager(initial_profile=profile_with_full_atlas)
    app_ctx = session.ensure_hydrated()
    state_db = tmp_path / "studio_reports.sqlite"

    with StudioStateStore(db_path=state_db) as store:
        sv = store.create_saved_view(
            profile_with_full_atlas.profile_id,
            "Monthly Core Fact SQL",
            SavedViewKind.SQL,
            datasource=SqlDatasource.DUCKDB,
            sql_text=(
                'SELECT "MONTH_END_DATE", "Total_Net_Worth_Market" '
                'FROM "gold"."core_monthly_fact" ORDER BY "MONTH_END_DATE" DESC'
            ),
        )

    fixture_defn = ReportDefinition(
        id="fixture.full_order",
        name="Fixture Order & Provenance Report",
        default_period=PeriodRequest(preset=PeriodPreset.SINCE_INCEPTION),
        blocks=(
            TitleBlock(block_id="b1_title", title="Fixture Title", subtitle="Subtitle"),
            ProvenanceBlock(block_id="b2_prov"),
            NarrativeBlock(block_id="b3_note", heading="Note", body="Static commentary."),
            SubtitleBlock(block_id="b4_sub", text="Metrics"),
            KPIGroupBlock(block_id="b5_kpi", source_id="overview.kpis"),
            DividerBlock(block_id="b6_div", label="Charts"),
            ChartBlock(block_id="b7_chart", source_id="overview.net_worth_trend"),
            PageBreakBlock(block_id="b8_break"),
            TableBlock(block_id="b9_tbl", source_id="overview.monthly_summary_table", max_rows=5),
            SavedViewBlock(block_id="b10_sv", view_id=sv.view_id, max_rows=5),
        ),
    )

    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)
    prov = app_ctx.analytical_provenance or app_ctx.provenance
    assert prov is not None

    result = service.render(
        fixture_defn,
        app_ctx,
        context_overrides=ReportContextOverrides(pfetl_run_id=prov.run_id),
        record_history=True,
        state_db_path=state_db,
    )

    assert result.warnings == ()
    assert [b.block_id for b in result.blocks] == [
        "b1_title",
        "b2_prov",
        "b3_note",
        "b4_sub",
        "b5_kpi",
        "b6_div",
        "b7_chart",
        "b8_break",
        "b9_tbl",
        "b10_sv",
    ]
    assert isinstance(result.blocks[0], RenderedTextBlock)
    assert isinstance(result.blocks[1], RenderedProvenanceBlock)
    assert result.blocks[1].pfetl_run_id == prov.run_id
    assert result.blocks[1].settings_snapshot_id == prov.settings_snapshot_id
    assert result.blocks[1].rules_snapshot_id == prov.rules_snapshot_id
    assert isinstance(result.blocks[4], RenderedKPIGroupBlock)
    assert len(result.blocks[4].items) > 0
    assert isinstance(result.blocks[5], RenderedStructuralBlock)
    assert isinstance(result.blocks[6], RenderedChartBlock)
    assert "data" in result.blocks[6].figure_dict
    assert isinstance(result.blocks[7], RenderedStructuralBlock)
    assert isinstance(result.blocks[8], RenderedTableBlock)
    assert len(result.blocks[8].rows) > 0
    assert isinstance(result.blocks[9], RenderedTableBlock)
    assert result.blocks[9].source_id == f"saved_view:{sv.view_id}"

    with StudioStateStore(db_path=state_db) as store:
        runs = store.list_report_runs(report_id="fixture.full_order")
        assert len(runs) == 1
        assert runs[0].pfetl_run_id == prov.run_id
        assert runs[0].output_format == "preview"


def test_missing_saved_view_and_unknown_source_produce_broken_blocks_unless_required(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Missing saved views/sources degrade to RenderedBrokenBlock + warning unless required=True."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    state_db = tmp_path / "studio_missing.sqlite"
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)

    soft_defn = ReportDefinition(
        id="fixture.broken_soft",
        name="Soft Broken Report",
        blocks=(
            SavedViewBlock(block_id="deleted_sv", view_id="nonexistent_view_id", required=False),
            ChartBlock(block_id="missing_src", source_id="unknown.chart", required=False),
        ),
    )
    soft_res = service.render(soft_defn, app_ctx, state_db_path=state_db)
    assert len(soft_res.blocks) == 2
    assert isinstance(soft_res.blocks[0], RenderedBrokenBlock)
    assert soft_res.blocks[0].code == "SAVED_VIEW_NOT_FOUND"
    assert isinstance(soft_res.blocks[1], RenderedBrokenBlock)
    assert soft_res.blocks[1].code == "MISSING_SOURCE"
    assert len(soft_res.warnings) == 2

    strict_defn = ReportDefinition(
        id="fixture.broken_strict",
        name="Strict Broken Report",
        blocks=(
            SavedViewBlock(block_id="req_sv", view_id="nonexistent_view_id", required=True),
        ),
    )
    with pytest.raises(RequiredReportBlockError):
        service.render(strict_defn, app_ctx, state_db_path=state_db)


def test_reports_page_renders_headless(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Reports page constructs Built-in/Custom catalog and Preview pane without errors."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    state_db = tmp_path / "studio_ui.sqlite"

    with Client(page("/reports_test")):
        render_reports_page(app_ctx, state_db_path=state_db)


def _make_fixture_report_result() -> ReportResult:
    """Build a pure in-memory `ReportResult` requiring zero database access."""
    now = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)
    ctx = ReportContext(
        profile_id="prof_fixture_01",
        pfetl_run_id="run_fixture_abc123",
        core_version="v12.1.3",
        schema_version="v3",
        settings_snapshot_id="snap_set_1111",
        rules_snapshot_id="snap_rule_2222",
        period=PeriodContext(
            preset=PeriodPreset.LATEST_COMPLETE_MONTH,
            label="Nov 2026",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
        ),
        studio_version="0.1.26",
        generated_at=now,
        currency_mode="base",
    )
    table_block = RenderedTableBlock(
        block_id="tbl_monthly",
        source_id="overview.monthly_summary_table",
        title="Monthly: Summary / 2026*",
        columns=("Month End", "Market Net Worth", "Net Savings Flow"),
        rows=(
            {"Month End": "2026-10-31", "Market Net Worth": 1250000.0, "Net Savings Flow": 85000.0},
            {"Month End": "2026-11-30", "Market Net Worth": 1345000.0, "Net Savings Flow": 95000.0},
        ),
        total_rows=2,
        truncated=False,
        source_tables=("gold.core_monthly_fact",),
    )
    chart_block = RenderedChartBlock(
        block_id="chart_nw",
        source_id="overview.net_worth_trend",
        title="Net Worth Trend",
        figure_dict={
            "data": [
                {
                    "type": "scatter",
                    "mode": "lines+markers",
                    "x": ["2026-10-31", "2026-11-30"],
                    "y": [1250000.0, 1345000.0],
                    "name": "Market Net Worth",
                }
            ],
            "layout": {"title": "Net Worth Trend"},
        },
        source_tables=("gold.core_monthly_fact",),
        caption="Source: gold.core_monthly_fact",
    )
    return ReportResult(
        definition_id="builtin.fixture_export",
        definition_name="Fixture Export Report",
        context=ctx,
        blocks=(
            RenderedTextBlock(
                block_type="title",
                block_id="t1",
                heading="Fixture Export Report",
                body="Deterministic Offline Fixture",
            ),
            RenderedProvenanceBlock(
                block_id="p1",
                profile_id=ctx.profile_id,
                pfetl_run_id=ctx.pfetl_run_id,
                core_version=ctx.core_version,
                schema_version=ctx.schema_version,
                settings_snapshot_id=ctx.settings_snapshot_id,
                rules_snapshot_id=ctx.rules_snapshot_id,
                period_label=ctx.period.label,
                studio_version=ctx.studio_version,
                generated_at=ctx.generated_at,
                disclaimer_text="Indicative / estimated figures only.",
            ),
            RenderedKPIGroupBlock(
                block_id="k1",
                source_id="overview.kpis",
                title="Executive KPIs",
                items=(
                    ReportKpiItem(
                        label="Market Net Worth",
                        value=1345000.0,
                        formatted_value="₹13.45L",
                        subtitle="Nov 2026",
                        source_table="gold.core_monthly_fact",
                    ),
                ),
            ),
            chart_block,
            RenderedStructuralBlock(block_type="page_break", block_id="pb1"),
            table_block,
        ),
        duration_ms=12.5,
    )


def test_exporters_pdf_xlsx_csv_and_chart_images_without_db_access(tmp_path: Path) -> None:
    """Exporters generate valid PDF, XLSX, CSV, PNG, and SVG files from fixture ReportResult."""
    result = _make_fixture_report_result()
    state_db = tmp_path / "export_history.sqlite"

    pdf_res = ReportExporter.export_pdf(
        result,
        tmp_path / "out" / "fixture_report.pdf",
        record_history=True,
        state_db_path=state_db,
    )
    assert pdf_res.path.exists()
    assert pdf_res.bytes_written > 1000
    assert pdf_res.path.read_bytes().startswith(b"%PDF-")

    xlsx_res = ReportExporter.export_xlsx(
        result,
        tmp_path / "out" / "fixture_report.xlsx",
        record_history=True,
        state_db_path=state_db,
    )
    assert xlsx_res.path.exists()
    assert xlsx_res.bytes_written > 1000
    assert xlsx_res.warnings == ()
    with zipfile.ZipFile(xlsx_res.path, "r") as zf:
        names = set(zf.namelist())
        assert any(name.startswith("xl/media/") for name in names)
        workbook_xml = zf.read("xl/workbook.xml").decode("utf-8")
        assert "Summary &amp; Provenance" in workbook_xml or "Summary & Provenance" in workbook_xml
        assert "Charts" in workbook_xml
        shared_or_sheet = "".join(
            zf.read(n).decode("utf-8", errors="ignore")
            for n in names
            if n.startswith("xl/worksheets/") or n == "xl/sharedStrings.xml"
        )
        assert "run_fixture_abc123" in shared_or_sheet
        assert "snap_set_1111" in shared_or_sheet

    table_block = result.blocks[-1]
    assert isinstance(table_block, RenderedTableBlock)
    csv_res = ReportExporter.export_csv(table_block, tmp_path / "out" / "monthly.csv")
    loaded_df = pl.read_csv(csv_res.path)
    assert loaded_df.columns == list(table_block.columns)
    assert loaded_df.height == len(table_block.rows)
    assert loaded_df["Market Net Worth"].to_list() == [1250000.0, 1345000.0]

    chart_block = result.blocks[3]
    assert isinstance(chart_block, RenderedChartBlock)
    png_res = ReportExporter.export_chart_png(chart_block.figure_dict, tmp_path / "out" / "c.png")
    assert png_res.bytes_written > 500
    assert png_res.path.read_bytes().startswith(b"\x89PNG")

    svg_res = ReportExporter.export_chart_svg(chart_block.figure_dict, tmp_path / "out" / "c.svg")
    assert svg_res.bytes_written > 200
    assert "<svg" in svg_res.path.read_text(encoding="utf-8")

    with StudioStateStore(db_path=state_db) as store:
        runs = store.list_report_runs()
        assert {r.output_format for r in runs} == {"pdf", "xlsx"}
        assert all(r.pfetl_run_id == "run_fixture_abc123" for r in runs)


def test_exporter_pptx_builds_editable_16_9_deck_and_records_history(tmp_path: Path) -> None:
    """ReportExporter.export_pptx produces an editable 16:9 deck with slides, tables, and chart."""
    result = _make_fixture_report_result()
    state_db = tmp_path / "pptx_history.sqlite"

    pptx_res = ReportExporter.export_pptx(
        result,
        tmp_path / "out" / "fixture_deck.pptx",
        record_history=True,
        state_db_path=state_db,
    )
    assert pptx_res.path.exists()
    assert pptx_res.bytes_written > 5000
    assert pptx_res.format == "pptx"

    prs: Any = Presentation(str(pptx_res.path))
    assert len(prs.slides) >= 4
    slide_texts: list[str] = [
        str(shape.text_frame.text)
        for slide in prs.slides
        for shape in slide.shapes
        if shape.has_text_frame
    ]
    joined = "\n".join(slide_texts)
    assert "Fixture Export Report" in joined
    assert "run_fixture_abc123" in joined
    assert "₹13.45L" in joined
    assert any(shape.has_table for slide in prs.slides for shape in slide.shapes)
    assert any(shape.has_chart for slide in prs.slides for shape in slide.shapes)

    with StudioStateStore(db_path=state_db) as store:
        runs = store.list_report_runs()
        assert [r.output_format for r in runs] == ["pptx"]


def test_prebuilt_monthly_fy_wealth_reports_render_and_export(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Monthly, FY, and Wealth built-ins render deterministically and export PDF/PPTX/XLSX."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)

    for report_id in (
        "monthly_financial_review",
        "financial_year_review",
        "net_worth_wealth_attribution",
    ):
        defn = DEFAULT_REPORT_REGISTRY.get_definition(report_id)
        assert defn is not None
        assert defn.builtin is True

        res = service.render(defn, app_ctx)
        assert [b.block_id for b in res.blocks] == [b.block_id for b in defn.blocks]
        assert any(isinstance(b, RenderedProvenanceBlock) for b in res.blocks)
        if report_id in ("monthly_financial_review", "financial_year_review"):
            assert any(isinstance(b, RenderedKPIGroupBlock) for b in res.blocks)

        if report_id == "monthly_financial_review":
            charts = [b for b in res.blocks if isinstance(b, RenderedChartBlock)]
            wf = next((c for c in charts if c.source_id == "monthly.attribution_waterfall"), None)
            assert wf is not None
            wf_data: list[dict[str, Any]] = list(wf.figure_dict.get("data") or [])
            assert wf_data and "Residual" in wf_data[0].get("x", [])

        if report_id == "financial_year_review":
            prov_blocks = [b for b in res.blocks if isinstance(b, RenderedProvenanceBlock)]
            assert prov_blocks and "Indicative" in prov_blocks[0].disclaimer_text

        pdf_out = ReportExporter.export_pdf(res, tmp_path / f"{report_id}.pdf")
        pptx_out = ReportExporter.export_pptx(res, tmp_path / f"{report_id}.pptx")
        xlsx_out = ReportExporter.export_xlsx(res, tmp_path / f"{report_id}.xlsx")
        assert pdf_out.bytes_written > 1000
        assert pptx_out.bytes_written > 5000
        assert xlsx_out.bytes_written > 1000


def test_prebuilt_cashflow_expense_income_reports_render_and_export(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Cashflow, Expense, and Income built-ins render deterministically and export PDF/PPTX/XLSX."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)

    for report_id in (
        "cashflow_budget_review",
        "expense_lifestyle_review",
        "income_resilience_review",
    ):
        defn = DEFAULT_REPORT_REGISTRY.get_definition(report_id)
        assert defn is not None
        assert defn.builtin is True

        res = service.render(defn, app_ctx)
        assert [b.block_id for b in res.blocks] == [b.block_id for b in defn.blocks]
        assert any(isinstance(b, RenderedProvenanceBlock) for b in res.blocks)

        pdf_out = ReportExporter.export_pdf(res, tmp_path / f"{report_id}.pdf")
        pptx_out = ReportExporter.export_pptx(res, tmp_path / f"{report_id}.pptx")
        xlsx_out = ReportExporter.export_xlsx(res, tmp_path / f"{report_id}.xlsx")
        assert pdf_out.bytes_written > 1000
        assert pptx_out.bytes_written > 4000
        assert xlsx_out.bytes_written > 1000



def test_prebuilt_portfolio_performance_currency_reports_render_and_export(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Portfolio, Performance, and Currency built-ins render and export PDF/PPTX/XLSX."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)

    for report_id in (
        "portfolio_review",
        "investment_performance_benchmark",
        "multi_currency_exposure_review",
    ):
        defn = DEFAULT_REPORT_REGISTRY.get_definition(report_id)
        assert defn is not None
        assert defn.builtin is True

        res = service.render(defn, app_ctx)
        assert [b.block_id for b in res.blocks] == [b.block_id for b in defn.blocks]
        assert any(isinstance(b, RenderedProvenanceBlock) for b in res.blocks)

        pdf_out = ReportExporter.export_pdf(res, tmp_path / f"{report_id}.pdf")
        pptx_out = ReportExporter.export_pptx(res, tmp_path / f"{report_id}.pptx")
        xlsx_out = ReportExporter.export_xlsx(res, tmp_path / f"{report_id}.xlsx")
        assert pdf_out.bytes_written > 1000
        assert pptx_out.bytes_written > 4000
        assert xlsx_out.bytes_written > 1000



def test_prebuilt_tax_and_fire_reports_render_and_export(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Investment Tax, Tax Position, and FIRE built-ins render and export PDF/PPTX/XLSX."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)

    for report_id in (
        "investment_tax_harvesting",
        "tax_position_review",
        "fire_planning_review",
    ):
        defn = DEFAULT_REPORT_REGISTRY.get_definition(report_id)
        assert defn is not None
        assert defn.builtin is True

        res = service.render(defn, app_ctx)
        assert [b.block_id for b in res.blocks] == [b.block_id for b in defn.blocks]
        prov_blocks = [b for b in res.blocks if isinstance(b, RenderedProvenanceBlock)]
        assert prov_blocks
        if report_id in ("investment_tax_harvesting", "tax_position_review"):
            assert "Indicative" in prov_blocks[0].disclaimer_text

        pdf_out = ReportExporter.export_pdf(res, tmp_path / f"{report_id}.pdf")
        pptx_out = ReportExporter.export_pptx(res, tmp_path / f"{report_id}.pptx")
        xlsx_out = ReportExporter.export_xlsx(res, tmp_path / f"{report_id}.xlsx")
        assert pdf_out.bytes_written > 1000
        assert pptx_out.bytes_written > 4000
        assert xlsx_out.bytes_written > 1000



def _seed_operations_control_plane(cp_path: Path) -> None:
    """Populate full Control Plane schema & forensic JSONL blob on fixture SQLite."""
    events_jsonl = "\n".join(
        [
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": "ev-2",
                    "sequence": 2,
                    "timestamp": "2026-11-26T00:02:41Z",
                    "event_code": "ANALYTICS_COMPLETED",
                    "severity": "INFO",
                    "outcome": "COMPLETED",
                    "message": "Analytics phase finished",
                    "context": {
                        "run_id": "run-latest-002",
                        "stage": "ANALYZING",
                        "component": "pipeline",
                        "operation": "phase_4_analytics",
                        "operation_id": "op-2",
                        "parent_operation_id": "op-root",
                        "duration_ms": 1245.5,
                        "rows": 46,
                    },
                }
            ),
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": "ev-1",
                    "sequence": 1,
                    "timestamp": "2026-11-26T00:02:40Z",
                    "event_code": "PIPELINE_START",
                    "severity": "INFO",
                    "outcome": "COMPLETED",
                    "message": "Pipeline initialized",
                    "context": {
                        "run_id": "run-latest-002",
                        "stage": "INITIALIZING",
                        "component": "lifecycle",
                        "operation": "phase_0_init",
                        "operation_id": "op-root",
                    },
                }
            ),
        ]
    )
    con = sqlite3.connect(str(cp_path))
    try:
        con.executescript(
            """
            DROP TABLE IF EXISTS cp_external_provider_requests;
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
                ('pr1', 'run-latest-002', 'Yahoo Finance', '^NDX', '2026-11-20', '2026-11-26', 1, 'CACHED', NULL, NULL, '2026-11-26T00:02:42');

            DROP TABLE IF EXISTS cp_file_registry;
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
                ('f1', 'mf.csv', 'in/mf.csv', 'mf_holdings', 'csv', 'h1', 4096, 'SYNCED', 'run-latest-002', '2026-11-26T00:02:40', 'run-latest-002', '2026-11-26T00:02:40', 'run-latest-002', '2026-11-26T00:02:40', 'run-latest-002', '2026-11-26T00:02:41');

            DROP TABLE IF EXISTS cp_artifact_run_events;
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
                ('e1', 'run-latest-002', 'f1', 'SYNCED', '2026-11-26T00:02:41', 'in/mf.csv', 'h1', 'PENDING_BRONZE', 'SYNCED', 'Loaded', 'bronze.mf', 'run-latest-002', 'run-latest-002', 'CONVERGED');

            DROP TABLE IF EXISTS cp_file_payloads;
            CREATE TABLE cp_file_payloads (
                file_id TEXT PRIMARY KEY,
                file_bytes BLOB
            );
            INSERT INTO cp_file_payloads VALUES ('f1', X'DEADBEEF');

            DROP TABLE IF EXISTS cp_run_failures;
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
                (1, 'run-latest-002', 'INE001A01036', 'market_sync', 'ValueError', 'Price missing', 'Traceback', 'market_data', '2026-11-26T00:02:41');
            """
        )
        con.execute(
            "UPDATE cp_runs SET execution_log = ? WHERE run_id = 'run-latest-002';",
            (events_jsonl,),
        )
        con.commit()
    finally:
        con.close()


def test_prebuilt_operations_forensic_evidence_reports_render_and_export(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Data Health, Run Forensic, and Source Evidence built-ins render and export cleanly."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    _seed_operations_control_plane(profile_with_full_atlas.control_plane_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)

    assert len(DEFAULT_REPORT_REGISTRY.list_definitions()) == 15

    for report_id in (
        "data_health_operations",
        "run_forensic",
        "source_evidence_change",
    ):
        defn = DEFAULT_REPORT_REGISTRY.get_definition(report_id)
        assert defn is not None
        assert defn.builtin is True

        res = service.render(defn, app_ctx)
        assert [b.block_id for b in res.blocks] == [b.block_id for b in defn.blocks]
        assert any(isinstance(b, RenderedProvenanceBlock) for b in res.blocks)

        if report_id == "run_forensic":
            appendix = next(
                b
                for b in res.blocks
                if isinstance(b, RenderedTableBlock) and b.block_id == "forensic_appendix_table"
            )
            seqs = [int(r["Seq"]) for r in appendix.rows]
            assert seqs == sorted(seqs)

        if report_id == "source_evidence_change":
            payload_tbl = next(
                b
                for b in res.blocks
                if isinstance(b, RenderedTableBlock) and b.block_id == "evidence_payload_table"
            )
            for row in payload_tbl.rows:
                assert not isinstance(row.get("Retained Payload"), (bytes, bytearray))

        pdf_out = ReportExporter.export_pdf(res, tmp_path / f"{report_id}.pdf")
        pptx_out = ReportExporter.export_pptx(res, tmp_path / f"{report_id}.pptx")
        xlsx_out = ReportExporter.export_xlsx(res, tmp_path / f"{report_id}.xlsx")
        assert pdf_out.bytes_written > 1000
        assert pptx_out.bytes_written > 4000
        assert xlsx_out.bytes_written > 1000


def test_custom_report_draft_clone_reorder_and_persistence_roundtrip(
    tmp_path: Path,
) -> None:
    """Clone built-in, reorder blocks, persist, rename, and delete preserving report_runs."""
    catalog = DEFAULT_REPORT_REGISTRY.list_editor_catalog()
    assert catalog[ReportSourceKind.KPI_GROUP]
    assert catalog[ReportSourceKind.CHART]
    assert catalog[ReportSourceKind.TABLE]

    src_builtin = DEFAULT_REPORT_REGISTRY.get_definition("monthly_financial_review")
    assert src_builtin is not None
    orig_json = src_builtin.model_dump_json()

    draft = clone_definition_as_draft(src_builtin, existing_names=set(), suffix="Copy")
    assert draft.id != src_builtin.id
    assert draft.name == "Monthly Financial Review (Copy)"

    first_id = draft.blocks[0].block_id
    second_id = draft.blocks[1].block_id
    assert draft.move_block(0, 1) is True
    assert draft.blocks[0].block_id == second_id
    assert draft.blocks[1].block_id == first_id

    defn = draft.to_definition()
    assert defn.builtin is False
    # Built-in registry remains untouched.
    builtin_after = DEFAULT_REPORT_REGISTRY.get_definition("monthly_financial_review")
    assert builtin_after is not None
    assert builtin_after.model_dump_json() == orig_json

    db_path = tmp_path / "studio.sqlite"
    with StudioStateStore(db_path=db_path) as store:
        saved = store.save_report_definition(
            "prof-1",
            defn.name,
            defn.model_dump_json(),
            report_id=defn.id,
            description=defn.description,
        )
        reloaded = ReportDefinition.model_validate_json(saved.definition_json)
        assert reloaded.model_dump_json() == defn.model_dump_json()

        # Profile-scoped name uniqueness enforced.
        with pytest.raises(ValueError, match="already exists"):
            store.save_report_definition("prof-1", defn.name, defn.model_dump_json())

        renamed = store.rename_report_definition(saved.report_id, "My Monthly Custom")
        assert renamed.name == "My Monthly Custom"
        assert ReportDefinition.model_validate_json(renamed.definition_json).name == "My Monthly Custom"

        store.record_report_run(
            saved.report_id,
            "run-001",
            '{"preset":"latest_complete_month"}',
            "pdf",
            output_path="/tmp/out.pdf",
        )
        store.delete_report_definition(saved.report_id)
        assert store.get_report_definition(saved.report_id) is None
        runs = store.list_report_runs(report_id=saved.report_id)
        assert len(runs) == 1


def test_broken_saved_view_and_source_validation() -> None:
    """Broken SavedView or source references stay visible and block save only when required."""
    defn = ReportDefinition(
        id="custom_broken_test",
        name="Broken Ref Check",
        blocks=(
            SavedViewBlock(block_id="sv_blk", view_id="deleted-view-id", required=False),
            TableBlock(block_id="tbl_blk", source_id="nonexistent.source", required=True),
        ),
    )
    draft = ReportDefinitionDraft.from_definition(defn)
    res = validate_report_draft(
        draft,
        registry=DEFAULT_REPORT_REGISTRY,
        saved_views_by_id={},
    )
    assert res.is_valid is False
    assert res.has_blocking_errors is True

    sv_issues = res.issues_for_block("sv_blk")
    assert len(sv_issues) == 1
    assert sv_issues[0].code == "BROKEN_SAVED_VIEW_REF"
    assert sv_issues[0].blocking is False
    assert "Saved View" in sv_issues[0].remediation

    tbl_issues = res.issues_for_block("tbl_blk")
    assert len(tbl_issues) == 1
    assert tbl_issues[0].code == "MISSING_BLOCK_SOURCE"
    assert tbl_issues[0].blocking is True


def test_custom_report_lifecycle_export_and_rerun_across_runs(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Saved custom report previews, exports PDF/PPTX/XLSX, and reruns across PFETL runs."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)

    # Seed an earlier historical PFETL run in Control Plane SQLite for rerun comparison.
    con = sqlite3.connect(str(profile_with_full_atlas.control_plane_path))
    try:
        con.execute(
            """
            INSERT OR IGNORE INTO cp_settings_snapshots (snapshot_id, content_hash, canonical_payload, created_at)
            VALUES ('snap-set-prev', 'hash-sp', '{}', '2026-10-01T00:00:00Z');
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO cp_rules_snapshots (snapshot_id, content_hash, canonical_payload, created_at)
            VALUES ('snap-rul-prev', 'hash-rp', '{}', '2026-10-01T00:00:00Z');
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO cp_runs (
                run_id, started_at, finished_at, status, commit_state,
                application_version, schema_version, settings_snapshot_id, rules_snapshot_id
            ) VALUES (
                'run-prev-001', '2026-10-01T00:00:00Z', '2026-10-01T00:01:00Z',
                'SUCCESS', 'FULLY_COMMITTED', 'v11.3.1', 'v2.9.0',
                'snap-set-prev', 'snap-rul-prev'
            );
            """
        )
        con.commit()
    finally:
        con.close()

    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    state_db = tmp_path / "studio_custom_rerun.sqlite"
    with StudioStateStore(db_path=state_db) as store:
        saved_view = store.create_saved_view(
            profile_id=profile_with_full_atlas.profile_id,
            name="Core Monthly Explorer",
            kind=SavedViewKind.SQL,
            datasource=SqlDatasource.DUCKDB,
            sql_text='SELECT "MONTH_END_DATE", "Total_Net_Worth_Market" FROM gold.core_monthly_fact LIMIT 12',
        )
        builtin = DEFAULT_REPORT_REGISTRY.get_definition("monthly_financial_review")
        assert builtin is not None
        draft = clone_definition_as_draft(builtin, existing_names=set(), suffix="Custom")
        draft.add_block(
            ReportDefinitionDraft.from_definition(
                ReportDefinition(
                    id="tmp",
                    name="tmp",
                    blocks=(
                        NarrativeBlock(block_id="cust_note", heading="Note", body="Custom rerun check"),
                        SavedViewBlock(block_id="cust_sv", view_id=saved_view.view_id, max_rows=10),
                    ),
                )
            ).blocks[0]
        )
        draft.add_block(
            ReportDefinitionDraft.from_definition(
                ReportDefinition(
                    id="tmp2",
                    name="tmp2",
                    blocks=(
                        SavedViewBlock(block_id="cust_sv", view_id=saved_view.view_id, max_rows=10),
                    ),
                )
            ).blocks[0]
        )
        custom_def = draft.to_definition()
        saved_rec = store.save_report_definition(
            profile_with_full_atlas.profile_id,
            custom_def.name,
            custom_def.model_dump_json(),
            report_id=custom_def.id,
            description=custom_def.description,
        )

    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)
    res_prev = service.render_saved_report(
        saved_rec.report_id,
        app_ctx,
        state_db_path=state_db,
        context_overrides=ReportContextOverrides(pfetl_run_id="run-prev-001"),
        record_history=True,
    )
    assert res_prev.context.pfetl_run_id == "run-prev-001"
    assert res_prev.context.core_version == "v11.3.1"

    pdf_out = ReportExporter.export_pdf(res_prev, tmp_path / "custom.pdf")
    pptx_out = ReportExporter.export_pptx(res_prev, tmp_path / "custom.pptx")
    xlsx_out = ReportExporter.export_xlsx(res_prev, tmp_path / "custom.xlsx")
    assert pdf_out.bytes_written > 1000
    assert pptx_out.bytes_written > 4000
    assert xlsx_out.bytes_written > 1000

    # Rerun same saved custom definition against the newer PFETL run.
    res_latest = service.render_saved_report(
        saved_rec.report_id,
        app_ctx,
        state_db_path=state_db,
        context_overrides=ReportContextOverrides(pfetl_run_id="run-latest-002"),
        record_history=True,
    )
    assert res_latest.context.pfetl_run_id == "run-latest-002"
    changes = compare_report_provenance(res_prev.context, res_latest.context)
    changed_fields = {c.field for c in changes}
    assert "pfetl_run_id" in changed_fields
    assert "core_version" in changed_fields

    with StudioStateStore(db_path=state_db) as store:
        after_rec = store.get_report_definition(saved_rec.report_id)
        assert after_rec is not None
        assert after_rec.definition_json == saved_rec.definition_json
        runs = store.list_report_runs(report_id=saved_rec.report_id)
        assert len(runs) == 2
        assert {r.pfetl_run_id for r in runs} == {"run-prev-001", "run-latest-002"}


def test_exporters_handle_missing_kaleido_prerequisite_gracefully(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Kaleido/Chrome raises ExportPrerequisiteError on PDF/PNG but degrades XLSX cleanly."""
    result = _make_fixture_report_result()

    def _fail_to_image(*_args: object, **_kwargs: object) -> bytes:
        raise RuntimeError("Chrome executable not found")

    monkeypatch.setattr(pio, "to_image", _fail_to_image)

    with pytest.raises(ExportPrerequisiteError, match="Chrome/Chromium"):
        ReportExporter.export_pdf(result, tmp_path / "fail.pdf")

    xlsx_res = ReportExporter.export_xlsx(result, tmp_path / "tables_only.xlsx")
    assert xlsx_res.path.exists()
    assert len(xlsx_res.warnings) == 1
    assert "skipped in XLSX" in xlsx_res.warnings[0]


def test_sanitize_excel_sheet_name_and_filename() -> None:
    """Sheet and filename sanitizers enforce length/character limits and uniqueness."""
    used: set[str] = set()
    s1 = sanitize_excel_sheet_name("Very Long [Sheet]: Name / With * Bad ? Chars 2026", used)
    s2 = sanitize_excel_sheet_name("Very Long [Sheet]: Name / With * Bad ? Chars 2026", used)
    assert len(s1) <= 31
    assert len(s2) <= 31
    assert s1 != s2
    assert sanitize_filename("bad:file/name?.pdf") == "bad_file_name_.pdf"


