"""Tests for semantic periods, whitelisted filters, dataset/metric registries, and query cache."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from pfetl_analytics_studio.data import (
    SURFACE_ATLAS,
    DuckDBReader,
    QueryCache,
    build_cache_key,
)
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    DEFAULT_DATASETS,
    DEFAULT_METRICS,
    ComparisonMode,
    DatasetRegistry,
    DatasetSpec,
    FilterCatalogResolver,
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
    MetricAggregation,
    MetricFormat,
    MetricRegistry,
    MetricSpec,
    PeriodPreset,
    PeriodRequest,
    PeriodResolver,
    compile_filter_clause,
    format_comparison_label,
    format_period_label,
)
from tests.fixtures.build_fixture_databases import (
    create_full_atlas_databases,
    seed_calendar_and_gold_months,
)


@pytest.fixture(scope="module")
def _base_semantic_dbs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Build one cached 82-surface + 1000-day calendar fixture pair per test module."""
    base_dir = tmp_path_factory.mktemp("semantic_base")
    duckdb_path, cp_path = create_full_atlas_databases(base_dir)
    seed_calendar_and_gold_months(duckdb_path)
    return duckdb_path, cp_path


@pytest.fixture
def semantic_profile(_base_semantic_dbs: tuple[Path, Path], tmp_path: Path) -> PFETLProfile:
    """Provide an isolated PFETLProfile copying the seeded semantic fixture DBs."""
    src_duck, src_cp = _base_semantic_dbs
    base_dir = tmp_path / "outputs"
    base_dir.mkdir(parents=True, exist_ok=True)
    dst_duck = base_dir / src_duck.name
    dst_cp = base_dir / src_cp.name
    shutil.copy2(src_duck, dst_duck)
    shutil.copy2(src_cp, dst_cp)

    settings_file = tmp_path / "semantic_settings.toml"
    settings_file.write_text(
        f"""
[database]
target_db_base_path = "{base_dir.as_posix()}"
target_db_name = "{dst_duck.name}"
raw_document_store_name = "{dst_cp.name}"
disable_file_discoverer = false

[observability]
logs_directory = "logs/runs"
""",
        encoding="utf-8",
    )
    rules_file = tmp_path / "semantic_rules.toml"
    rules_file.write_text('DEFAULT_CURRENCY_ID = "INR_INR"\n', encoding="utf-8")

    load_res = PFETLProfile.load(settings_file, rules_file, label="Semantic Fixture Profile")
    assert load_res.profile is not None
    return load_res.profile


def test_latest_complete_month_excludes_future_and_partial_gold_month(
    semantic_profile: PFETLProfile,
) -> None:
    """Verify `latest_complete_month` resolves 2026-10 when as_of is 2026-11-24 and 2026-11 is partial."""
    resolver = PeriodResolver(DuckDBReader(semantic_profile))
    ctx = resolver.resolve(PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH))

    assert ctx.is_empty is False
    assert ctx.as_of_date == date(2026, 11, 24)
    assert ctx.start_date == date(2026, 10, 1)
    assert ctx.end_date == date(2026, 10, 31)
    assert ctx.financial_year == "2026-27"
    assert "Oct 2026" in format_period_label(ctx)


def test_ytd_fytd_quarters_and_selected_fy_boundaries(
    semantic_profile: PFETLProfile,
) -> None:
    """Verify Calendar YTD vs Indian FYTD, quarters, selected FY, and since_inception."""
    resolver = PeriodResolver(DuckDBReader(semantic_profile))

    cal_ytd = resolver.resolve(PeriodRequest(preset=PeriodPreset.CALENDAR_YTD))
    assert cal_ytd.start_date == date(2026, 1, 1)
    assert cal_ytd.end_date == date(2026, 11, 24)

    fytd = resolver.resolve(PeriodRequest(preset=PeriodPreset.FYTD))
    assert fytd.start_date == date(2026, 4, 1)
    assert fytd.end_date == date(2026, 11, 24)
    assert fytd.financial_year == "2026-27"

    sel_fy = resolver.resolve(
        PeriodRequest(preset=PeriodPreset.SELECTED_FY, financial_year="2025-26")
    )
    assert sel_fy.start_date == date(2025, 4, 1)
    assert sel_fy.end_date == date(2026, 3, 31)
    assert sel_fy.financial_year == "2025-26"

    cal_q = resolver.resolve(
        PeriodRequest(preset=PeriodPreset.CALENDAR_QUARTER, quarter_year="Q1-2026")
    )
    assert cal_q.start_date == date(2026, 1, 1)
    assert cal_q.end_date == date(2026, 3, 31)

    fy_q = resolver.resolve(
        PeriodRequest(preset=PeriodPreset.FY_QUARTER, fy_quarter_year="Q2-2026-27")
    )
    assert fy_q.start_date == date(2026, 7, 1)
    assert fy_q.end_date == date(2026, 9, 30)

    sel_m = resolver.resolve(
        PeriodRequest(preset=PeriodPreset.SELECTED_MONTH, selected_month="2026-11")
    )
    assert sel_m.start_date == date(2026, 11, 1)
    assert sel_m.end_date == date(2026, 11, 30)

    inception = resolver.resolve(PeriodRequest(preset=PeriodPreset.SINCE_INCEPTION))
    assert inception.start_date == date(2024, 4, 1)
    assert inception.end_date == date(2026, 11, 24)


def test_rolling_windows_and_comparison_modes(
    semantic_profile: PFETLProfile,
) -> None:
    """Verify rolling 3M/6M/12M/24M spans and previous/YoY comparison arithmetic."""
    resolver = PeriodResolver(DuckDBReader(semantic_profile))

    r3 = resolver.resolve(
        PeriodRequest(
            preset=PeriodPreset.ROLLING_3M,
            comparison_mode=ComparisonMode.PREVIOUS_PERIOD,
        )
    )
    assert r3.start_date == date(2026, 8, 1)
    assert r3.end_date == date(2026, 10, 31)
    assert r3.comparison is not None
    assert r3.comparison.start_date == date(2026, 5, 1)
    assert r3.comparison.end_date == date(2026, 7, 31)

    r6 = resolver.resolve(PeriodRequest(preset=PeriodPreset.ROLLING_6M))
    assert r6.start_date == date(2026, 5, 1)
    assert r6.end_date == date(2026, 10, 31)

    r12 = resolver.resolve(
        PeriodRequest(
            preset=PeriodPreset.ROLLING_12M,
            comparison_mode=ComparisonMode.SAME_PERIOD_PREVIOUS_YEAR,
        )
    )
    assert r12.start_date == date(2025, 11, 1)
    assert r12.end_date == date(2026, 10, 31)
    assert r12.comparison is not None
    assert r12.comparison.start_date == date(2024, 11, 1)
    assert r12.comparison.end_date == date(2025, 10, 31)

    r24 = resolver.resolve(PeriodRequest(preset=PeriodPreset.ROLLING_24M))
    assert r24.start_date == date(2024, 11, 1)
    assert r24.end_date == date(2026, 10, 31)


def test_custom_period_validation_and_empty_period_safety(
    semantic_profile: PFETLProfile,
) -> None:
    """Verify custom start > end raises ValidationError and unmatched queries return empty context."""
    with pytest.raises(ValidationError, match="cannot exceed end_date"):
        PeriodRequest(
            preset=PeriodPreset.CUSTOM,
            custom_start=date(2026, 5, 1),
            custom_end=date(2026, 4, 1),
        )

    custom_req = PeriodRequest(
        preset=PeriodPreset.CUSTOM,
        custom_start=date(2026, 2, 10),
        custom_end=date(2026, 2, 20),
        comparison_mode=ComparisonMode.PREVIOUS_PERIOD,
    )
    resolver = PeriodResolver(DuckDBReader(semantic_profile))
    ctx = resolver.resolve(custom_req)
    assert ctx.start_date == date(2026, 2, 10)
    assert ctx.end_date == date(2026, 2, 20)
    assert ctx.comparison is not None
    assert ctx.comparison.start_date == date(2026, 1, 30)
    assert ctx.comparison.end_date == date(2026, 2, 9)

    missing = resolver.resolve(
        PeriodRequest(preset=PeriodPreset.SELECTED_MONTH, selected_month="1999-01")
    )
    assert missing.is_empty is True
    assert missing.start_date is None
    assert missing.end_date is None

    # Empty dim_calendar table returns empty context without crashing
    dcon = duckdb.connect(str(semantic_profile.duckdb_path))
    try:
        dcon.execute("DELETE FROM silver.dim_calendar;")
    finally:
        dcon.close()

    empty_ctx = resolver.resolve(PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH))
    assert empty_ctx.is_empty is True


def test_filter_normalization_chips_and_parameterized_sql() -> None:
    """Verify canonical filter normalization, UI chips, whitelist check, and SQL binding."""
    f_a = FilterSet(
        filters=(
            FilterSpec(
                field=FilterField.SECTOR,
                operator=FilterOperator.IN,
                value=("IT", "Finance", "IT"),
            ),
            FilterSpec(field=FilterField.ISIN, operator=FilterOperator.EQ, value="INF123"),
        )
    )
    f_b = FilterSet(
        filters=(
            FilterSpec(field=FilterField.ISIN, operator=FilterOperator.EQ, value="INF123"),
            FilterSpec(
                field=FilterField.SECTOR,
                operator=FilterOperator.IN,
                value=("Finance", "IT"),
            ),
        )
    )
    assert f_a.cache_key() == f_b.cache_key()

    chips = f_a.to_chips()
    assert len(chips) == 2
    assert any(c.value_display == "Finance, IT" for c in chips)

    col_map = {
        FilterField.ISIN: "ISIN",
        FilterField.SECTOR: "SECTOR",
        FilterField.CURRENCY_ID: "CURRENCY_ID",
    }
    sql_frag, params = compile_filter_clause(f_a, col_map)
    assert '"ISIN" = ?' in sql_frag
    assert '"SECTOR" IN (?, ?)' in sql_frag
    assert params == ["INF123", "Finance", "IT"]

    # Reject disallowed filter fields
    disallowed = FilterSet(
        filters=(FilterSpec(field=FilterField.FY, operator=FilterOperator.EQ, value="2026-27"),)
    )
    with pytest.raises(ValueError, match="Disallowed filter field"):
        compile_filter_clause(disallowed, col_map)

    # Reject inverted range bounds
    with pytest.raises(ValidationError, match="cannot exceed upper bound"):
        FilterSpec(
            field=FilterField.FY,
            operator=FilterOperator.BETWEEN,
            value=(100, 10),
        )


def test_dataset_and_metric_registry_guards() -> None:
    """Verify every default dataset maps to SURFACE_ATLAS and non-additive rules hold."""
    for ds in DEFAULT_DATASETS.list_datasets():
        assert ds.physical_table in SURFACE_ATLAS

    # Duplicate dataset ID rejected
    with pytest.raises(ValueError, match="Duplicate dataset ID"):
        DatasetRegistry(
            (
                DEFAULT_DATASETS.get("core_monthly_fact"),
                DEFAULT_DATASETS.get("core_monthly_fact"),
            )
        )

    # Duplicate metric ID rejected
    reg = MetricRegistry(DEFAULT_DATASETS, DEFAULT_METRICS.list_metrics())
    with pytest.raises(ValueError, match="Duplicate metric ID"):
        reg.register(DEFAULT_METRICS.get("total_income"))

    # Non-additive metric cannot be registered with SUM
    with pytest.raises(ValidationError, match="cannot use default aggregation 'sum'"):
        MetricSpec(
            id="bad_xirr",
            label="Bad XIRR",
            dataset="investment_by_portfolio",
            expression='"XIRR"',
            aggregation=MetricAggregation.SUM,
            format=MetricFormat.PERCENT,
            unit="percent",
            allowed_dimensions=("Closing_Date",),
            additive=False,
            description="Invalid",
        )

    # Query-time guards for XIRR, weights, net worth stocks, and disallowed dimensions
    with pytest.raises(ValueError, match="non-additive"):
        DEFAULT_METRICS.validate_query(
            "portfolio_xirr", ("Closing_Date",), requested_aggregation=MetricAggregation.SUM
        )
    with pytest.raises(ValueError, match="non-additive"):
        DEFAULT_METRICS.validate_query(
            "isin_weight", ("ISIN",), requested_aggregation=MetricAggregation.AVG
        )
    with pytest.raises(ValueError, match="non-additive"):
        DEFAULT_METRICS.validate_query(
            "net_worth_market",
            ("MONTH_END_DATE",),
            requested_aggregation=MetricAggregation.SUM,
        )
    with pytest.raises(ValueError, match="does not permit dimension"):
        DEFAULT_METRICS.validate_query("total_income", ("ISIN",))
    # Income additive vs non-additive metric guards
    assert DEFAULT_METRICS.validate_query(
        "income_active_total", ("YEAR_MONTH",), requested_aggregation=MetricAggregation.SUM
    ).additive
    with pytest.raises(ValueError, match="non-additive"):
        DEFAULT_METRICS.validate_query(
            "income_trailing_3m_avg",
            ("YEAR_MONTH",),
            requested_aggregation=MetricAggregation.SUM,
        )
    with pytest.raises(ValueError, match="non-additive"):
        DEFAULT_METRICS.validate_query(
            "income_cumulative_ytd",
            ("YEAR_MONTH",),
            requested_aggregation=MetricAggregation.SUM,
        )
    # Expense additive vs non-additive metric guards
    assert DEFAULT_METRICS.validate_query(
        "expense_core_total", ("YEAR_MONTH",), requested_aggregation=MetricAggregation.SUM
    ).additive
    assert DEFAULT_METRICS.validate_query(
        "expense_cash_total", ("YEAR_MONTH",), requested_aggregation=MetricAggregation.SUM
    ).additive
    with pytest.raises(ValueError, match="non-additive"):
        DEFAULT_METRICS.validate_query(
            "expense_real_core_growth",
            ("MONTH_END_DATE",),
            requested_aggregation=MetricAggregation.SUM,
        )
    with pytest.raises(ValueError, match="non-additive"):
        DEFAULT_METRICS.validate_query(
            "expense_to_nw",
            ("YEAR_MONTH",),
            requested_aggregation=MetricAggregation.AVG,
        )

    # Unknown dataset reference rejected
    local_ds_reg = DatasetRegistry(
        (
            DatasetSpec(
                id="only_one",
                physical_table="gold.core_monthly_fact",
                label="Only",
                domain="Overview",
                grain="Month",
                date_field="MONTH_END_DATE",
                allowed_dimensions=("MONTH_END_DATE",),
            ),
        )
    )
    m_reg = MetricRegistry(local_ds_reg)
    with pytest.raises(KeyError, match="Unknown semantic dataset ID"):
        m_reg.register(
            MetricSpec(
                id="orphan",
                label="Orphan",
                dataset="nonexistent_ds",
                expression='"X"',
                aggregation=MetricAggregation.SUM,
                format=MetricFormat.NUMBER,
                unit="u",
                allowed_dimensions=(),
                additive=True,
                description="Orphan",
            )
        )


def test_query_cache_isolation_invalidation_and_lru(
    semantic_profile: PFETLProfile,
) -> None:
    """Verify QueryCache hits cleanly for identical run+spec and misses when run changes."""
    resolver = PeriodResolver(DuckDBReader(semantic_profile))
    period = resolver.resolve(PeriodRequest(preset=PeriodPreset.ROLLING_3M))
    filters_1 = FilterSet(
        filters=(
            FilterSpec(field=FilterField.SECTOR, operator=FilterOperator.IN, value=("B", "A")),
        )
    )
    filters_2 = FilterSet(
        filters=(
            FilterSpec(field=FilterField.SECTOR, operator=FilterOperator.IN, value=("A", "B")),
        )
    )

    cache = QueryCache(max_entries=2)
    key_run1 = build_cache_key("prof1", "run-001", "overview", period=period, filters=filters_1)
    key_run1_reordered = build_cache_key(
        "prof1", "run-001", "overview", period=period, filters=filters_2
    )
    key_run2 = build_cache_key("prof1", "run-002", "overview", period=period, filters=filters_1)

    assert key_run1 == key_run1_reordered
    assert key_run1 != key_run2

    calls = 0

    def _compute() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"val": 42}

    res1 = cache.get_or_compute(key_run1, _compute)
    res1_hit = cache.get_or_compute(key_run1_reordered, _compute)
    assert res1 == res1_hit == {"val": 42}
    assert calls == 1

    # New PFETL run MUST miss
    assert cache.get(key_run2) is None
    cache.set(key_run2, {"val": 99})
    assert cache.get(key_run2) == {"val": 99}

    # LRU eviction test (max_entries=2)
    key_3 = build_cache_key("prof1", "run-002", "extra_query")
    cache.set(key_3, {"val": 100})
    assert cache.get(key_run1) is None  # evicted LRU entry
    assert cache.get(key_run2) == {"val": 99}
    assert cache.get(key_3) == {"val": 100}
    assert cache.stats().evictions == 1

    removed = cache.invalidate_run("prof1", "run-002")
    assert removed == 2
    assert cache.stats().entries == 0


def test_period_catalog_options_and_overview_metrics(
    semantic_profile: PFETLProfile,
) -> None:
    """Verify PeriodResolver enumerates descending months/FYs and Overview metrics register."""
    opts = PeriodResolver(DuckDBReader(semantic_profile)).list_available_options()
    assert len(opts.months) >= 12
    assert opts.months[0][0] == "2026-11"
    assert "(2026-11)" in opts.months[0][1]
    assert "2026-27" in opts.financial_years
    assert opts.default_selected_month == "2026-10"
    assert opts.default_custom_start == date(2026, 10, 1)
    assert opts.default_custom_end == date(2026, 10, 31)

    for metric_id in (
        "net_savings_flow",
        "market_return_flow",
        "liquidity_ratio_months",
        "portfolio_bm_xirr",
        "portfolio_effective_holdings",
        "fire_gap",
    ):
        assert DEFAULT_METRICS.has(metric_id)


def test_extended_period_presets_comparison_modes_and_filter_catalog(
    semantic_profile: PFETLProfile,
) -> None:
    """Verify MTD, prior completed FY, Rolling 36M, FY comparison strategies, and FilterCatalogResolver."""
    reader = DuckDBReader(semantic_profile)
    resolver = PeriodResolver(reader)

    mtd = resolver.resolve(PeriodRequest(preset=PeriodPreset.CURRENT_MONTH_MTD))
    assert mtd.start_date == date(2026, 11, 1)
    assert mtd.end_date == date(2026, 11, 24)

    prev_fy = resolver.resolve(PeriodRequest(preset=PeriodPreset.LAST_COMPLETED_FY))
    assert prev_fy.start_date == date(2025, 4, 1)
    assert prev_fy.end_date == date(2026, 3, 31)
    assert prev_fy.financial_year == "2025-26"

    r36 = resolver.resolve(PeriodRequest(preset=PeriodPreset.ROLLING_36M))
    assert r36.is_empty is False

    fy_base = resolver.resolve(
        PeriodRequest(
            preset=PeriodPreset.LATEST_COMPLETE_MONTH,
            comparison_mode=ComparisonMode.FY_START_MONTH,
        )
    )
    assert fy_base.comparison is not None
    assert fy_base.comparison.start_date == date(2026, 4, 1)
    assert fy_base.comparison.end_date == date(2026, 4, 30)
    assert format_comparison_label(fy_base.comparison) is not None

    prior_fy_comp = resolver.resolve(
        PeriodRequest(
            preset=PeriodPreset.FYTD,
            comparison_mode=ComparisonMode.PRIOR_COMPLETED_FY,
        )
    )
    assert prior_fy_comp.comparison is not None
    assert prior_fy_comp.comparison.financial_year == "2025-26"

    f_opts = FilterCatalogResolver(reader).list_available_options()
    assert "2026-27" in f_opts.options_for(FilterField.FY)
    assert f_opts.options_for(FilterField.IS_CORE_EXPENSE) == ("true", "false")

    # Budget & Cashflow datasets and metrics are registered
    assert DEFAULT_DATASETS.has("forecast_budget_variance")
    assert DEFAULT_DATASETS.has("cashflow_activity_summary")
    assert DEFAULT_DATASETS.has("silver_fact_transfer_transactions")
    assert DEFAULT_METRICS.get("budget_core_expense").additive is True
    assert DEFAULT_METRICS.get("emergency_fund_runway_months").additive is False
    assert DEFAULT_METRICS.get("net_operating_cashflow").additive is True
    assert DEFAULT_METRICS.get("unreconciled_difference").additive is True
    assert DEFAULT_METRICS.get("closing_cash_balance").additive is False

    # Wealth datasets and stock vs flow metrics
    assert DEFAULT_DATASETS.has("silver_fact_opening_balances")
    assert DEFAULT_METRICS.get("total_assets_book").additive is False
    assert DEFAULT_METRICS.get("attribution_residual").additive is True
    assert DEFAULT_METRICS.get("asset_market_delta").additive is True

    # FIRE non-additive metrics reject SUM aggregation
    for fire_metric_id in (
        "fire_coast_today",
        "fire_lean_today",
        "fire_months_p50",
        "fire_runway_p50",
    ):
        spec = DEFAULT_METRICS.get(fire_metric_id)
        assert spec.additive is False
        assert spec.aggregation == MetricAggregation.LATEST
        with pytest.raises(ValueError, match="cannot use default aggregation 'sum'"):
            MetricSpec.model_validate({**spec.model_dump(), "aggregation": MetricAggregation.SUM})
