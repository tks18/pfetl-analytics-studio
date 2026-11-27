"""Tests for Phase 24 Report Definition, Provenance, Registry, and Runtime."""

from __future__ import annotations

from pathlib import Path

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
    ReportContextOverrides,
    ReportDefinition,
    ReportRegistry,
    ReportService,
    ReportSourceKind,
    RequiredReportBlockError,
    SavedViewBlock,
    SubtitleBlock,
    TableBlock,
    TitleBlock,
)
from pfetl_analytics_studio.semantic import PeriodPreset, PeriodRequest
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

    defn = DEFAULT_REPORT_REGISTRY.get_definition("builtin.executive_snapshot")
    assert defn is not None
    registry.register_definition(defn)
    with pytest.raises(DuplicateReportRegistrationError):
        registry.register_definition(defn)


def test_report_definition_json_roundtrip_in_studio_sqlite(
    temp_state_store: StudioStateStore,
) -> None:
    """ReportDefinition round-trips cleanly through StudioStateStore `saved_reports`."""
    assert CURRENT_SCHEMA_VERSION >= 5
    defn = DEFAULT_REPORT_REGISTRY.get_definition("builtin.executive_snapshot")
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

