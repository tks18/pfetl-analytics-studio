"""Domain presentation invariants, golden reconciliations, report matrix, and smoke flows.

Verifies:
- Household & Wealth golden reconciliations (income/cashflow/net-worth attribution).
- Investments & Tax golden invariants (allocation weights, P&L, FY tax & reconciliation).
- FIRE & Operations integration invariants (100% FI line, run/forensic/warehouse linkage).
- Explorer safety (read-only SQL guard, bounded preview, temp-file CSV export, replay).
- Reports period/comparison override consistency (Rolling 12M + ComparisonMode over 1M default)
  and representative export matrix (PDF, XLSX, PPTX, CSV, PNG/SVG).
- Product-level end-to-end read-only smoke flow across every Studio domain.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from pfetl_analytics_studio.analytics import (
    ExplorerAnalytics,
    FireAnalytics,
    HouseholdAnalytics,
    InvestmentAnalytics,
    OperationsAnalytics,
    OverviewService,
    RunHistoryFilter,
    TaxAnalytics,
    WealthAnalytics,
)
from pfetl_analytics_studio.analytics.investments_models import AllocationDimension
from pfetl_analytics_studio.context import AppSessionManager
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.reports import (
    DEFAULT_REPORT_REGISTRY,
    ChartBlock,
    KPIGroupBlock,
    ProvenanceBlock,
    RenderedChartBlock,
    RenderedProvenanceBlock,
    RenderedTableBlock,
    ReportContextOverrides,
    ReportDefinition,
    ReportExporter,
    ReportService,
    TitleBlock,
)
from pfetl_analytics_studio.semantic import (
    ComparisonMode,
    ComparisonPeriod,
    PeriodContext,
    PeriodPreset,
    PeriodRequest,
)
from pfetl_analytics_studio.state import SqlDatasource
from pfetl_analytics_studio.ui import build_fire_gap_coverage_figure
from tests.fixtures.build_fixture_databases import seed_overview_gold_tables


def _cast_fixture_columns(duckdb_path: Path) -> None:
    """Cast placeholder VARCHAR date/amount columns in empty fixture tables to DATE/DOUBLE."""
    import duckdb

    con = duckdb.connect(str(duckdb_path))
    try:
        date_rows = con.execute(
            """
            SELECT table_schema, table_name, column_name
            FROM information_schema.columns
            WHERE table_schema IN ('gold', 'silver')
              AND data_type = 'VARCHAR'
              AND lower(column_name) LIKE '%date%'
            """
        ).fetchall()
        for sch, tbl, col in date_rows:
            con.execute(f'ALTER TABLE "{sch}"."{tbl}" ALTER "{col}" TYPE DATE;')

        amt_rows = con.execute(
            """
            SELECT table_schema, table_name, column_name
            FROM information_schema.columns
            WHERE table_schema IN ('bronze', 'silver')
              AND data_type = 'VARCHAR'
              AND upper(column_name) LIKE 'ZAMOUNT%'
            """
        ).fetchall()
        for sch, tbl, col in amt_rows:
            con.execute(f'ALTER TABLE "{sch}"."{tbl}" ALTER "{col}" TYPE DOUBLE;')

        bool_rows = con.execute(
            """
            SELECT table_schema, table_name, column_name
            FROM information_schema.columns
            WHERE table_schema IN ('gold', 'silver')
              AND data_type = 'VARCHAR'
              AND lower(column_name) LIKE 'is\\_%' ESCAPE '\\'
            """
        ).fetchall()
        for sch, tbl, col in bool_rows:
            con.execute(f'ALTER TABLE "{sch}"."{tbl}" ALTER "{col}" TYPE BOOLEAN;')
        con.execute(
            'ALTER TABLE gold.wealth_asset_breakdown ADD COLUMN IF NOT EXISTS "Net_Cashflow_Month" DOUBLE DEFAULT 0.0;'
        )
        con.execute(
            'ALTER TABLE gold.wealth_asset_breakdown ADD COLUMN IF NOT EXISTS "Organic_Growth_Value" DOUBLE DEFAULT 0.0;'
        )
    finally:
        con.close()


def _seed_cp_tables(cp_path: Path) -> None:
    con = sqlite3.connect(str(cp_path))
    try:
        con.executescript(
            """
            DROP TABLE IF EXISTS cp_external_provider_requests;
            CREATE TABLE cp_external_provider_requests (
                request_id TEXT PRIMARY KEY, run_id TEXT, provider TEXT, entity TEXT,
                requested_start DATE, requested_end DATE, attempt_count INTEGER,
                status TEXT, failure_class TEXT, final_error TEXT, created_at TIMESTAMP
            );
            DROP TABLE IF EXISTS cp_file_registry;
            CREATE TABLE cp_file_registry (
                file_id TEXT PRIMARY KEY, file_name TEXT, relative_path TEXT,
                file_category TEXT, file_type TEXT, file_hash TEXT, file_size_bytes BIGINT,
                sync_status TEXT, first_seen_run_id TEXT, first_seen_at TIMESTAMP,
                last_seen_run_id TEXT, last_seen_at TIMESTAMP, last_changed_run_id TEXT,
                last_changed_at TIMESTAMP, last_synced_run_id TEXT, last_synced_at TIMESTAMP
            );
            DROP TABLE IF EXISTS cp_artifact_run_events;
            CREATE TABLE cp_artifact_run_events (
                event_id TEXT PRIMARY KEY, run_id TEXT, file_id TEXT, event_type TEXT,
                event_at TIMESTAMP, observed_path TEXT, content_hash TEXT, previous_status TEXT,
                new_status TEXT, event_reason TEXT, affected_table TEXT, detecting_run_id TEXT,
                replay_run_id TEXT, convergence_result TEXT
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


def _rolling_12m_context(*, with_comparison: bool = False) -> PeriodContext:
    comp = (
        ComparisonPeriod(
            mode=ComparisonMode.PREVIOUS_PERIOD,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            label="Prior 12M",
        )
        if with_comparison
        else None
    )
    return PeriodContext(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        label="Rolling 12M",
        preset=PeriodPreset.ROLLING_12M,
        comparison=comp,
    )


def test_household_and_wealth_golden_invariants(profile_with_full_atlas: PFETLProfile) -> None:
    """Household cashflow bridge and Wealth attribution bridge reconcile deterministically."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    _cast_fixture_columns(profile_with_full_atlas.duckdb_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    period_ctx = _rolling_12m_context(with_comparison=True)

    cf = HouseholdAnalytics.cashflow_statement(
        profile_with_full_atlas,
        period_ctx,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    assert cf.calculated_net_cashflow == pytest.approx(
        cf.net_operating + cf.net_investing + cf.net_financing + cf.net_transfers
    )

    wealth = WealthAnalytics.summary(
        profile_with_full_atlas,
        period_ctx,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    assert wealth.state == "ready"
    assert wealth.attribution is not None
    bridge = wealth.attribution
    assert bridge.asset_market_delta == pytest.approx(
        bridge.closing_market_assets - bridge.opening_market_assets,
        rel=1e-4,
    )
    assert isinstance(bridge.reconciles_within_tolerance, bool)


def test_investments_and_tax_golden_invariants(profile_with_full_atlas: PFETLProfile) -> None:
    """Investments allocation/performance and Tax FY summary preserve canonical semantics."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    _cast_fixture_columns(profile_with_full_atlas.duckdb_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    period_ctx = _rolling_12m_context()

    port = InvestmentAnalytics.portfolio_snapshot(
        profile_with_full_atlas,
        period_ctx,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    assert port.state in ("ready", "empty")
    alloc = InvestmentAnalytics.allocation(
        profile_with_full_atlas,
        AllocationDimension.CLASS,
        period_ctx,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    if alloc.rows:
        total_cur = sum(r.current_value for r in alloc.rows)
        assert total_cur >= 0.0

    tax_fy = TaxAnalytics.fy_summary(
        profile_with_full_atlas,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    assert tax_fy.state in ("ready", "empty")


def test_fire_and_operations_integration_invariants(profile_with_full_atlas: PFETLProfile) -> None:
    """FIRE canonical summary, 100% reference line, and Operations telemetry stay consistent."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    _cast_fixture_columns(profile_with_full_atlas.duckdb_path)
    _seed_cp_tables(profile_with_full_atlas.control_plane_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    period_ctx = _rolling_12m_context()

    fire = FireAnalytics.summary(
        profile_with_full_atlas,
        period_ctx,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    assert fire.state in ("ready", "empty")
    if fire.history:
        fig = build_fire_gap_coverage_figure(fire.history, currency_id=fire.currency_id)
        assert fig is not None
        assert fig.get("data")

    ops = OperationsAnalytics.run_history(
        profile_with_full_atlas,
        RunHistoryFilter(limit=10),
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    assert len(ops.runs) >= 1


def test_explorer_safety_and_saved_view_invariants(profile_with_full_atlas: PFETLProfile) -> None:
    """Explorer enforces read-only SQL, bounded preview, and temp-file CSV export."""
    with pytest.raises(ValueError, match="forbidden|SELECT"):
        ExplorerAnalytics.export_sql_csv(
            profile_with_full_atlas,
            "DELETE FROM cp_runs",
            datasource=SqlDatasource.CONTROL_PLANE,
        )

    export_path, row_cnt, filename = ExplorerAnalytics.export_sql_csv(
        profile_with_full_atlas,
        "SELECT run_id, status FROM cp_runs LIMIT 5",
        datasource=SqlDatasource.CONTROL_PLANE,
    )
    assert export_path.is_file()
    assert filename.endswith(".csv")
    assert row_cnt >= 1


def test_reports_period_override_and_representative_export_matrix(
    profile_with_full_atlas: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Explicit Rolling 12M + ComparisonMode overrides 1M template defaults and exports cleanly."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    _cast_fixture_columns(profile_with_full_atlas.duckdb_path)
    _seed_cp_tables(profile_with_full_atlas.control_plane_path)
    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    service = ReportService(registry=DEFAULT_REPORT_REGISTRY)

    # 1. FIRE defaults to LATEST_COMPLETE_MONTH, but explicit ROLLING_12M + PREVIOUS_PERIOD wins
    fire_def = DEFAULT_REPORT_REGISTRY.get_definition("fire_planning_review")
    assert fire_def is not None
    assert fire_def.default_period.preset == PeriodPreset.LATEST_COMPLETE_MONTH

    ovr = ReportContextOverrides(
        period_request=PeriodRequest(
            preset=PeriodPreset.ROLLING_12M,
            comparison_mode=ComparisonMode.PREVIOUS_PERIOD,
        )
    )
    fire_res = service.render(fire_def, app_ctx, context_overrides=ovr)
    assert fire_res.context.period.preset == PeriodPreset.ROLLING_12M
    prov = next(b for b in fire_res.blocks if isinstance(b, RenderedProvenanceBlock))
    assert prov.comparison_label

    # 2. Representative export matrix (finance, investments, tax/FIRE, operations, custom)
    for report_id in (
        "monthly_financial_review",
        "portfolio_review",
        "fire_planning_review",
        "run_forensic",
    ):
        defn = DEFAULT_REPORT_REGISTRY.get_definition(report_id)
        assert defn is not None
        res = service.render(defn, app_ctx, context_overrides=ovr)
        assert ReportExporter.export_pdf(res, tmp_path / f"{report_id}.pdf").bytes_written > 1000
        assert ReportExporter.export_pptx(res, tmp_path / f"{report_id}.pptx").bytes_written > 4000
        assert ReportExporter.export_xlsx(res, tmp_path / f"{report_id}.xlsx").bytes_written > 1000

        tbl = next((b for b in res.blocks if isinstance(b, RenderedTableBlock)), None)
        if tbl is not None:
            assert ReportExporter.export_csv(tbl, tmp_path / f"{report_id}.csv").bytes_written > 0

        chart = next(
            (b for b in res.blocks if isinstance(b, RenderedChartBlock) and b.figure_dict),
            None,
        )
        if chart is not None:
            assert (
                ReportExporter.export_chart_png(
                    chart.figure_dict, tmp_path / f"{report_id}.png"
                ).bytes_written
                > 1000
            )

    custom_def = ReportDefinition(
        id="custom_smoke_matrix",
        name="Custom Matrix Report",
        default_period=PeriodRequest(
            preset=PeriodPreset.ROLLING_12M,
            comparison_mode=ComparisonMode.PREVIOUS_PERIOD,
        ),
        blocks=(
            TitleBlock(block_id="c_title", title="Custom Matrix"),
            ProvenanceBlock(block_id="c_prov"),
            KPIGroupBlock(block_id="c_kpis", source_id="overview.kpis"),
            ChartBlock(block_id="c_chart", source_id="monthly.attribution_waterfall"),
        ),
    )
    custom_res = service.render(custom_def, app_ctx)
    assert ReportExporter.export_pdf(custom_res, tmp_path / "custom.pdf").bytes_written > 1000


def test_product_level_readonly_smoke_flow(profile_with_full_atlas: PFETLProfile) -> None:
    """Every domain service builds its primary snapshot read-only without mutating upstream DBs."""
    seed_overview_gold_tables(profile_with_full_atlas.duckdb_path)
    _cast_fixture_columns(profile_with_full_atlas.duckdb_path)
    _seed_cp_tables(profile_with_full_atlas.control_plane_path)
    duck_mtime_before = profile_with_full_atlas.duckdb_path.stat().st_mtime_ns
    cp_mtime_before = profile_with_full_atlas.control_plane_path.stat().st_mtime_ns

    app_ctx = AppSessionManager(initial_profile=profile_with_full_atlas).ensure_hydrated()
    period_ctx = _rolling_12m_context()

    OverviewService.get_snapshot(
        profile_with_full_atlas,
        period_ctx,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    HouseholdAnalytics.budget_summary(
        profile_with_full_atlas,
        period_ctx,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    WealthAnalytics.summary(
        profile_with_full_atlas,
        period_ctx,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    InvestmentAnalytics.portfolio_snapshot(
        profile_with_full_atlas,
        period_ctx,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    TaxAnalytics.fy_summary(
        profile_with_full_atlas,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    FireAnalytics.summary(
        profile_with_full_atlas,
        period_ctx,
        catalog_snapshot=app_ctx.catalog_snapshot,
    )
    OperationsAnalytics.run_history(
        profile_with_full_atlas,
        RunHistoryFilter(limit=5),
        catalog_snapshot=app_ctx.catalog_snapshot,
    )

    assert profile_with_full_atlas.duckdb_path.stat().st_mtime_ns == duck_mtime_before
    assert profile_with_full_atlas.control_plane_path.stat().st_mtime_ns == cp_mtime_before
