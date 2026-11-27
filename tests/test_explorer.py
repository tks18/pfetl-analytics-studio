"""Tests for Data Catalog, Guided Explorer, chart recommendation, and UI."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest
from nicegui import Client, ui
from nicegui.page import page

from pfetl_analytics_studio.analytics import (
    CatalogFilter,
    ChartRecommendation,
    ExplorerAnalytics,
    GuidedQuery,
    GuidedSortSpec,
    SqlQueryRequest,
    SqlQueryResult,
    SurfaceColumnFilter,
)
from pfetl_analytics_studio.analytics.explorer_queries import (
    compile_guided_query,
    recommend_chart,
    validate_read_only_sql,
)
from pfetl_analytics_studio.context import AppContext
from pfetl_analytics_studio.data import (
    SURFACE_ATLAS,
    CompatibilityReport,
    CompatibilityStatus,
    ControlPlaneReader,
    ControlPlaneState,
    DuckDBReader,
    QueryCache,
    ReadOnlyViolationError,
    SurfaceLayer,
    SurfaceUsageCategory,
    WarehouseState,
)
from pfetl_analytics_studio.pages.explorer import render_explorer_page
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    DEFAULT_DATASETS,
    DEFAULT_METRICS,
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
    MetricAggregation,
    PeriodPreset,
    PeriodRequest,
)
from pfetl_analytics_studio.state import SavedViewKind, SqlDatasource
from pfetl_analytics_studio.ui import (
    TableColumn,
    apply_studio_theme,
    bounded_data_table,
    build_catalog_layer_usage_figure,
    build_catalog_top_tables_figure,
    build_guided_explorer_figure,
    build_tax_events_status_source_figure,
    build_tax_harvesting_exposure_figure,
    build_tax_harvesting_holding_figure,
    open_lot_detail_dialog,
    rows_to_csv_bytes,
)
from tests.fixtures.build_fixture_databases import (
    create_full_atlas_databases,
    seed_calendar_and_gold_months,
    seed_overview_gold_tables,
)


@pytest.fixture(scope="module")
def _base_explorer_dbs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Build one cached 82-surface + investment-seeded fixture pair per test module."""
    base_dir = tmp_path_factory.mktemp("explorer_base")
    duck_path, cp_path = create_full_atlas_databases(base_dir)
    seed_calendar_and_gold_months(duck_path)
    seed_overview_gold_tables(duck_path)

    con = duckdb.connect(str(duck_path))
    try:
        con.execute("DELETE FROM gold.investment_by_class;")
        con.execute(
            """
            INSERT INTO gold.investment_by_class (
                "Closing_Date", "INSTRUMENT_CLASS", "Total_Invested_Value",
                "Total_Current_Value", "Unrealized_PL", "Weight", "XIRR",
                "BM_XIRR", "Active_Return"
            ) VALUES
                (DATE '2026-09-30', 'Equity', 100000.0, 120000.0, 20000.0, 0.69, 14.2, 11.0, 3.2),
                (DATE '2026-10-31', 'Equity', 110000.0, 135000.0, 25000.0, 0.72, 15.8, 11.5, 4.3),
                (DATE '2026-09-30', 'Debt', 50000.0, 53000.0, 3000.0, 0.31, 7.1, 6.8, 0.3),
                (DATE '2026-10-31', 'Debt', 50000.0, 53500.0, 3500.0, 0.28, 7.3, 6.8, 0.5);
            """
        )
        con.execute(
            'INSERT INTO bronze.household_zcategory ("sample_id") '
            'SELECT range FROM range(1, 601);'
        )
    finally:
        con.close()
    return duck_path, cp_path


@pytest.fixture
def explorer_profile(_base_explorer_dbs: tuple[Path, Path], tmp_path: Path) -> PFETLProfile:
    """Provide an isolated PFETLProfile copying the seeded Explorer fixture DBs."""
    src_duck, src_cp = _base_explorer_dbs
    base_dir = tmp_path / "outputs"
    base_dir.mkdir(parents=True, exist_ok=True)
    duck_path = base_dir / src_duck.name
    cp_path = base_dir / src_cp.name
    shutil.copy2(src_duck, duck_path)
    shutil.copy2(src_cp, cp_path)

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

    loaded = PFETLProfile.load(settings_path, rules_path, label="Explorer Test")
    assert loaded.profile is not None
    return loaded.profile


def test_coverage_all_catalog_tables_classified_and_metrics_have_datasets() -> None:
    """Every SURFACE_ATLAS entry is classified and every MetricSpec targets a DatasetSpec."""
    assert len(SURFACE_ATLAS) == 82
    for name, spec in SURFACE_ATLAS.items():
        assert spec.usage in SurfaceUsageCategory, f"Unclassified surface: {name}"
        assert spec.layer in SurfaceLayer
        assert spec.grain
        assert spec.producer

    for metric in DEFAULT_METRICS.list_metrics():
        assert DEFAULT_DATASETS.has(metric.dataset), (
            f"Metric '{metric.id}' references missing dataset '{metric.dataset}'."
        )


def test_catalog_entries_filtering_and_charts(explorer_profile: PFETLProfile) -> None:
    """Filter 82-surface catalog by layer, domain, usage, and search term."""
    cache = QueryCache()
    full = ExplorerAnalytics.catalog_entries(explorer_profile, cache=cache)
    assert full.coverage.total_expected == 82
    assert full.coverage.total_discovered == 82
    assert full.coverage.layer_counts[SurfaceLayer.GOLD] == 22
    assert full.coverage.layer_counts[SurfaceLayer.CONTROL_PLANE] == 10

    gold_only = ExplorerAnalytics.catalog_entries(
        explorer_profile,
        CatalogFilter(layer=SurfaceLayer.GOLD, search="investment_by_class"),
        cache=cache,
    )
    assert len(gold_only.entries) == 1
    assert gold_only.entries[0].physical_name == "gold.investment_by_class"

    fig_layers = build_catalog_layer_usage_figure(full.coverage)
    fig_top = build_catalog_top_tables_figure(full.entries)
    assert len(fig_layers["data"]) == 1
    assert isinstance(fig_top["data"], list)


def test_preview_bounded_bronze_warning_cp_and_unknown_blocked(
    explorer_profile: PFETLProfile,
) -> None:
    """Verify bounded preview for Bronze/CP, limit clamping, and unknown-table blocking."""
    bronze_prev = ExplorerAnalytics.preview(
        explorer_profile, "bronze.household_zcategory", limit=9999
    )
    assert bronze_prev.limit_applied == 500
    assert bronze_prev.is_bronze_warning is True
    assert bronze_prev.is_unclassified_blocked is False
    assert len(bronze_prev.rows) == 500
    assert bronze_prev.truncated is True

    # Per-table column filter (both VARCHAR contains and numeric GTE) + full CSV export (>500 rows)
    filtered_prev = ExplorerAnalytics.preview(
        explorer_profile,
        "bronze.household_zcategory",
        limit=100,
        filters=(SurfaceColumnFilter(column="sample_id", operator=FilterOperator.CONTAINS, value="599"),),
    )
    assert filtered_prev.filtered_row_count == 1
    assert len(filtered_prev.rows) == 1

    numeric_prev = ExplorerAnalytics.preview(
        explorer_profile,
        "gold.investment_by_class",
        limit=100,
        filters=(
            SurfaceColumnFilter(
                column="Total_Invested_Value", operator=FilterOperator.GTE, value="110000"
            ),
        ),
    )
    assert numeric_prev.filtered_row_count == 1
    assert numeric_prev.rows[0]["INSTRUMENT_CLASS"] == "Equity"

    csv_bytes, exported_rows, fname = ExplorerAnalytics.export_surface_csv(
        explorer_profile, "bronze.household_zcategory"
    )
    assert exported_rows == 600
    assert fname == "bronze_household_zcategory.csv"
    assert len(csv_bytes.decode("utf-8").strip().splitlines()) == 601

    with pytest.raises(ValueError, match="not classified"):
        ExplorerAnalytics.export_surface_csv(explorer_profile, "gold.unclassified_future_table")

    cp_prev = ExplorerAnalytics.preview(explorer_profile, "cp_runs", limit=10)
    assert cp_prev.layer == SurfaceLayer.CONTROL_PLANE
    assert cp_prev.is_bronze_warning is False
    assert len(cp_prev.rows) >= 1

    unknown_prev = ExplorerAnalytics.preview(explorer_profile, "gold.unclassified_future_table")
    assert unknown_prev.is_unclassified_blocked is True
    assert unknown_prev.rows == ()
    assert unknown_prev.warning_message is not None


def test_guided_query_sums_additive_values_and_stays_read_only(
    explorer_profile: PFETLProfile,
) -> None:
    """GuidedQuery sums additive measures across periods/dimensions without mutating DB."""
    mtime_before = explorer_profile.duckdb_path.stat().st_mtime_ns
    query = GuidedQuery(
        dataset_id="investment_by_class",
        dimensions=("INSTRUMENT_CLASS",),
        metrics=("inv_class_invested_value", "inv_class_xirr"),
        sort=(GuidedSortSpec(field="inv_class_invested_value", descending=True),),
    )
    res = ExplorerAnalytics.execute_guided(explorer_profile, query, cache=QueryCache())
    assert res.row_count == 2
    by_class = {r["INSTRUMENT_CLASS"]: r for r in res.rows}
    # Equity: 100k + 110k = 210k; latest XIRR (2026-10-31) = 15.8
    assert by_class["Equity"]["inv_class_invested_value"] == pytest.approx(210000.0)
    assert by_class["Equity"]["inv_class_xirr"] == pytest.approx(15.8)
    assert by_class["Debt"]["inv_class_invested_value"] == pytest.approx(100000.0)
    assert by_class["Debt"]["inv_class_xirr"] == pytest.approx(7.3)
    assert "Dataset:" in res.semantic_summary
    assert res.chart_suggestion.chart_type == ChartRecommendation.GROUPED_BAR

    fig = build_guided_explorer_figure(res)
    assert len(fig["data"]) == 2
    assert "config" in fig
    # Primary metric is CURRENCY -> Y-axis ticks must use currency formatting, never '%'
    tick_texts = fig["layout"]["yaxis"].get("ticktext", [])
    assert tick_texts and all("%" not in str(t) for t in tick_texts)
    assert explorer_profile.duckdb_path.stat().st_mtime_ns == mtime_before

    # Full Guided CSV dump ignores UI row limit
    limited_q = GuidedQuery(
        dataset_id="investment_by_class",
        dimensions=("INSTRUMENT_CLASS",),
        metrics=("inv_class_invested_value",),
        limit=1,
    )
    g_csv, g_cnt, g_name = ExplorerAnalytics.export_guided_csv(explorer_profile, limited_q)
    assert g_cnt == 2
    assert g_name == "guided_investment_by_class.csv"
    assert len(g_csv.decode("utf-8").strip().splitlines()) == 3

    # Multi-date default dimensions (MONTH_START_DATE + MONTH_END_DATE) still produce TIME_SERIES
    ds_core = DEFAULT_DATASETS.get("core_monthly_fact")
    m_inc = DEFAULT_METRICS.get("total_income")
    multi_date_sug = recommend_chart(
        GuidedQuery(
            dataset_id="core_monthly_fact",
            dimensions=("MONTH_START_DATE", "MONTH_END_DATE"),
            metrics=("total_income",),
        ),
        ds_core,
        (m_inc,),
        [
            {
                "MONTH_START_DATE": date(2026, 10, 1),
                "MONTH_END_DATE": date(2026, 10, 31),
                "total_income": 100.0,
            }
        ],
    )
    assert multi_date_sug.chart_type == ChartRecommendation.TIME_SERIES
    assert multi_date_sug.x_field == "MONTH_END_DATE"


def test_guided_query_rejects_invalid_xirr_sum_and_disallowed_dimension() -> None:
    """Non-additive XIRR cannot be summed or averaged, and disallowed dimensions fail."""
    bad_sum = GuidedQuery(
        dataset_id="investment_by_class",
        dimensions=("INSTRUMENT_CLASS",),
        metrics=("inv_class_xirr",),
        metric_aggregations={"inv_class_xirr": MetricAggregation.SUM},
    )
    with pytest.raises(ValueError, match="non-additive"):
        compile_guided_query(bad_sum)

    bad_avg = GuidedQuery(
        dataset_id="investment_by_class",
        dimensions=("INSTRUMENT_CLASS",),
        metrics=("inv_class_xirr",),
        metric_aggregations={"inv_class_xirr": MetricAggregation.AVG},
    )
    with pytest.raises(ValueError, match="non-additive"):
        compile_guided_query(bad_avg)

    bad_dim = GuidedQuery(
        dataset_id="investment_by_class",
        dimensions=("NON_EXISTENT_DIM",),
        metrics=("inv_class_invested_value",),
    )
    with pytest.raises(ValueError, match="Disallowed dimension"):
        compile_guided_query(bad_dim)


def test_guided_query_limit_clamping_empty_result_and_chart_suggestions(
    explorer_profile: PFETLProfile,
) -> None:
    """Verify limit bounds (default 500, max 10,000), empty-result preservation, and chart rules."""
    default_q = GuidedQuery(dataset_id="core_monthly_fact", dimensions=("MONTH_END_DATE",))
    assert default_q.limit == 500

    clamped_q = GuidedQuery(
        dataset_id="core_monthly_fact",
        dimensions=("MONTH_END_DATE",),
        metrics=("total_income",),
        limit=99999,
    )
    assert clamped_q.limit == 10_000

    empty_q = GuidedQuery(
        dataset_id="investment_by_class",
        dimensions=("Closing_Date", "INSTRUMENT_CLASS"),
        metrics=("inv_class_invested_value",),
        filters=FilterSet(
            filters=(
                FilterSpec(
                    field=FilterField.INSTRUMENT_CLASS,
                    operator=FilterOperator.EQ,
                    value="CryptoThatDoesNotExist",
                ),
            )
        ),
        period=PeriodRequest(preset=PeriodPreset.SINCE_INCEPTION),
    )
    empty_res = ExplorerAnalytics.execute_guided(explorer_profile, empty_q)
    assert empty_res.row_count == 0
    assert empty_res.query == empty_q
    assert empty_res.chart_suggestion.chart_type == ChartRecommendation.TABLE

    ds_core = DEFAULT_DATASETS.get("core_monthly_fact")
    m_inc = DEFAULT_METRICS.get("total_income")
    ts_suggestion = recommend_chart(
        GuidedQuery(
            dataset_id="core_monthly_fact",
            dimensions=("MONTH_END_DATE",),
            metrics=("total_income",),
        ),
        ds_core,
        (m_inc,),
        [{"MONTH_END_DATE": date(2026, 10, 31), "total_income": 100.0}],
    )
    assert ts_suggestion.chart_type == ChartRecommendation.TIME_SERIES

    ds_cls = DEFAULT_DATASETS.get("investment_by_class")
    m_val = DEFAULT_METRICS.get("inv_class_invested_value")
    stacked_suggestion = recommend_chart(
        GuidedQuery(
            dataset_id="investment_by_class",
            dimensions=("Closing_Date", "INSTRUMENT_CLASS"),
            metrics=("inv_class_invested_value",),
        ),
        ds_cls,
        (m_val,),
        [
            {
                "Closing_Date": date(2026, 10, 31),
                "INSTRUMENT_CLASS": "Equity",
                "inv_class_invested_value": 100.0,
            }
        ],
    )
    assert stacked_suggestion.chart_type == ChartRecommendation.STACKED_BAR


def test_explorer_page_renders_headless_across_states(explorer_profile: PFETLProfile) -> None:
    """Headless NiceGUI render succeeds for nominal, no-profile, locked, and incompatible states."""

    def _compat(
        status: CompatibilityStatus,
        wh: WarehouseState,
    ) -> CompatibilityReport:
        return CompatibilityReport(
            status=status,
            is_compatible=(status == CompatibilityStatus.COMPATIBLE),
            warehouse_state=wh,
            control_plane_state=ControlPlaneState.AVAILABLE,
            total_expected=82,
            total_discovered=82,
            layer_counts={layer: 0 for layer in SurfaceLayer},
            supported_surfaces=[],
            missing_required_surfaces=[],
            unexpected_new_surfaces=[],
            missing_required_columns={},
            extra_columns={},
            diagnostics=[],
        )

    contexts = (
        AppContext(
            profile=explorer_profile,
            compatibility=_compat(CompatibilityStatus.COMPATIBLE, WarehouseState.AVAILABLE),
            analytical_run_id="run-latest-002",
        ),
        AppContext(profile=None),
        AppContext(
            profile=explorer_profile,
            compatibility=_compat(CompatibilityStatus.DEGRADED, WarehouseState.LOCKED),
        ),
        AppContext(
            profile=explorer_profile,
            compatibility=_compat(CompatibilityStatus.INCOMPATIBLE, WarehouseState.AVAILABLE),
        ),
    )

    for ctx in contexts:
        with Client(page("/_test_explorer"), request=None) as client:
            apply_studio_theme()
            render_explorer_page(ctx)
            assert len(client.elements) > 0


def test_bounded_data_table_caps_ui_at_500_while_csv_exports_all_rows() -> None:
    """Verify UI DOM is capped at 500 rows while `rows_to_csv_bytes` exports all 1,200 rows."""
    cols = [
        TableColumn(name="id", label="ID", field="id"),
        TableColumn(name="val", label="Value", field="val", align="right"),
    ]
    all_rows = [{"id": f"row-{i}", "val": i * 10} for i in range(1200)]

    csv_bytes = rows_to_csv_bytes(cols, all_rows)
    lines = csv_bytes.decode("utf-8").strip().splitlines()
    assert len(lines) == 1201  # header + all 1,200 rows

    with Client(page("/_test_table_cap"), request=None) as client:
        apply_studio_theme()
        bounded_data_table(cols, all_rows, max_rows=5000, title="Large Table")
        table_elems = [el for el in client.elements.values() if isinstance(el, ui.table)]
        assert len(table_elems) == 1
        rendered_rows = cast(list[dict[str, Any]], getattr(table_elems[0], "rows"))
        assert len(rendered_rows) == 500  # hard UI cap at 500


def test_tax_events_harvesting_charts_and_lot_detail_dialog() -> None:
    """Verify new Tax Events/Harvesting charts and shared 88-col Lot Detail dialog."""
    from types import SimpleNamespace

    ev_rows = [
        SimpleNamespace(source_type="CapitalGains", amount_inr=50000.0, tax_status="READY"),
        SimpleNamespace(source_type="Interest", amount_inr=12000.0, tax_status="CHECK_REQUIRED"),
    ]
    fig_ev = build_tax_events_status_source_figure(ev_rows, "INR_INR")
    assert fig_ev is not None and len(fig_ev["data"]) == 2

    harv_lots = [
        SimpleNamespace(
            instrument_name="Test Equity ETF",
            isin="INE000000001",
            lot_id="LOT-123456",
            holding_type="STCG",
            close_value=45000.0,
            unrealized_pl=-15000.0,
            tax_if_sold=-2250.0,
        )
    ]
    fig_harv = build_tax_harvesting_exposure_figure(harv_lots, "INR_INR")
    assert fig_harv is not None and len(fig_harv["data"]) == 2
    fig_hold = build_tax_harvesting_holding_figure(harv_lots, harv_lots, "INR_INR")
    assert fig_hold is not None and len(fig_hold["data"]) == 2

    with Client(page("/_test_lot_dlg"), request=None) as client:
        apply_studio_theme()
        open_lot_detail_dialog(None, "LOT-MISSING")
        assert len(client.elements) > 0


def test_read_only_sql_validator_and_db_guards(explorer_profile: PFETLProfile) -> None:
    """Verify SQL validator allows SELECT/WITH, blocks DDL/DML/multi-stmt, and DBs reject writes."""
    ok_select = validate_read_only_sql("/* comment */\nSELECT 1 AS x; ")
    assert ok_select == "SELECT 1 AS x"

    ok_cte = validate_read_only_sql(
        "-- lead comment\nWITH t AS (SELECT 1 AS n) SELECT n FROM t;"
    )
    assert ok_cte.startswith("WITH t AS")

    # Semicolon inside string literal is allowed; multi-statement is rejected
    assert "'a;b'" in validate_read_only_sql("SELECT 'a;b' AS s")
    with pytest.raises(ValueError, match="Multiple SQL statements"):
        validate_read_only_sql("SELECT 1; DROP TABLE gold.core_monthly_fact")

    for bad_sql in (
        "CREATE TABLE gold.hack (id INT)",
        "DELETE FROM gold.core_monthly_fact",
        "PRAGMA table_info('cp_runs')",
        "ATTACH 'other.db' AS other",
        "COPY gold.core_monthly_fact TO 'out.csv'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "WITH c AS (SELECT 1) DELETE FROM gold.core_monthly_fact",
    ):
        with pytest.raises(ValueError):
            validate_read_only_sql(bad_sql)

    # Underlying read-only DB gateways also reject write attempts directly
    with pytest.raises(ReadOnlyViolationError):
        DuckDBReader(explorer_profile).fetch_all("CREATE TABLE gold.should_fail (id INT);")
    with pytest.raises(ReadOnlyViolationError):
        ControlPlaneReader(explorer_profile).fetch_all("CREATE TABLE cp_fail (id TEXT);")


def test_sql_execution_truncation_csv_export_and_saved_view_replay(
    explorer_profile: PFETLProfile,
    tmp_path: Path,
) -> None:
    """Verify bounded SQL execution, full CSV export, and Saved View replay fidelity."""
    state_db = tmp_path / "explorer_views.sqlite"

    sql_res = ExplorerAnalytics.execute_sql(
        explorer_profile,
        SqlQueryRequest(
            sql='SELECT "sample_id" FROM "bronze"."household_zcategory"',
            datasource=SqlDatasource.DUCKDB,
            limit=50,
        ),
        cache=QueryCache(),
    )
    assert sql_res.row_count == 50
    assert sql_res.truncated is True

    csv_bytes, full_cnt, fname = ExplorerAnalytics.export_sql_csv(
        explorer_profile,
        'SELECT "sample_id" FROM "bronze"."household_zcategory"',
        datasource=SqlDatasource.DUCKDB,
    )
    assert full_cnt == 600
    assert fname == "sql_duckdb_export.csv"
    assert len(csv_bytes.decode("utf-8").strip().splitlines()) == 601

    cp_res = ExplorerAnalytics.execute_sql(
        explorer_profile,
        SqlQueryRequest(
            sql="SELECT run_id, status FROM cp_runs",
            datasource=SqlDatasource.CONTROL_PLANE,
            limit=10,
        ),
    )
    assert cp_res.row_count >= 1

    # Save and replay a Guided View -> exact match with direct execute_guided
    g_query = GuidedQuery(
        dataset_id="investment_by_class",
        dimensions=("INSTRUMENT_CLASS",),
        metrics=("inv_class_invested_value",),
        sort=(GuidedSortSpec(field="INSTRUMENT_CLASS", descending=False),),
    )
    direct = ExplorerAnalytics.execute_guided(explorer_profile, g_query)
    saved_g = ExplorerAnalytics.save_guided_view(
        explorer_profile.profile_id,
        "Class Cost Basis",
        g_query,
        description="Saved guided test",
        state_db_path=state_db,
    )
    assert saved_g.kind == SavedViewKind.GUIDED
    replayed_g = ExplorerAnalytics.replay_saved_view(explorer_profile, saved_g)
    assert isinstance(replayed_g, type(direct))
    assert replayed_g.rows == direct.rows

    saved_s = ExplorerAnalytics.save_sql_view(
        explorer_profile.profile_id,
        "CP Runs List",
        "SELECT run_id, status FROM cp_runs",
        datasource=SqlDatasource.CONTROL_PLANE,
        state_db_path=state_db,
    )
    replayed_s = ExplorerAnalytics.replay_saved_view(explorer_profile, saved_s, sql_limit=5)
    assert isinstance(replayed_s, SqlQueryResult)
    assert replayed_s.row_count >= 1
