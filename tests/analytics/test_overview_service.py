"""Unit and integration tests for Overview cockpit Gold/Meta/Control Plane analytical queries."""

from __future__ import annotations

import ast
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from pfetl_analytics_studio.analytics import OverviewService
from pfetl_analytics_studio.data import DuckDBReader, PFETLCatalog, QueryCache
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
    PeriodPreset,
    PeriodRequest,
    PeriodResolver,
)
from tests.fixtures.build_fixture_databases import (
    create_full_atlas_databases,
    seed_overview_gold_tables,
)


def _build_profile_dir(
    tmp_path: Path,
    *,
    include_fire: bool = True,
    include_tax_fy_2026: bool = True,
    tax_incomplete: bool = True,
) -> PFETLProfile:
    """Create an isolated complete PFETL profile with seeded Overview Gold marts."""
    base_dir = tmp_path / "outputs"
    duck_path, cp_path = create_full_atlas_databases(base_dir)
    seed_overview_gold_tables(
        duck_path,
        include_fire=include_fire,
        include_tax_fy_2026=include_tax_fy_2026,
        tax_incomplete=tax_incomplete,
    )
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
        f"""
[database]
target_db_base_path = "{base_dir.as_posix()}"
target_db_name = "{duck_path.name}"
raw_document_store_name = "{cp_path.name}"
disable_file_discoverer = false

[observability]
logs_directory = "logs/runs"
""",
        encoding="utf-8",
    )
    rules_path = tmp_path / "rules.toml"
    rules_path.write_text('DEFAULT_CURRENCY_ID = "INR_INR"\n', encoding="utf-8")
    loaded = PFETLProfile.load(settings_path, rules_path, label="Overview Test")
    assert loaded.profile is not None
    return loaded.profile


@pytest.fixture(scope="module")
def _cached_overview_dbs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Create module-scoped seeded databases to clone quickly per test."""
    base_dir = tmp_path_factory.mktemp("overview_base")
    duck_path, cp_path = create_full_atlas_databases(base_dir)
    seed_overview_gold_tables(duck_path)
    return duck_path, cp_path


@pytest.fixture
def overview_profile(
    _cached_overview_dbs: tuple[Path, Path],
    tmp_path: Path,
) -> PFETLProfile:
    """Provide an isolated clone of the seeded Overview profile."""
    src_duck, src_cp = _cached_overview_dbs
    base_dir = tmp_path / "outputs"
    base_dir.mkdir(parents=True, exist_ok=True)
    dst_duck = base_dir / src_duck.name
    dst_cp = base_dir / src_cp.name
    shutil.copy2(src_duck, dst_duck)
    shutil.copy2(src_cp, dst_cp)

    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
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
    rules_path = tmp_path / "rules.toml"
    rules_path.write_text('DEFAULT_CURRENCY_ID = "INR_INR"\n', encoding="utf-8")
    loaded = PFETLProfile.load(settings_path, rules_path, label="Overview Nominal")
    assert loaded.profile is not None
    return loaded.profile


def test_overview_latest_month_values_and_chart_series(overview_profile: PFETLProfile) -> None:
    """Verify single-month Overview KPIs match exact Gold fields and trailing charts populate."""
    cat = PFETLCatalog(overview_profile)
    _, compat = cat.discover()
    prov = cat.get_latest_provenance()
    period = PeriodResolver(DuckDBReader(overview_profile)).resolve(
        PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH)
    )
    cache = QueryCache(max_entries=16)

    snap = OverviewService.get_snapshot(
        overview_profile,
        period,
        provenance=prov,
        compatibility=compat,
        cache=cache,
    )
    assert snap.state == "ready"
    assert snap.as_of_date == date(2026, 10, 31)

    by_id = {k.kpi_id: k for k in (*snap.primary_kpis, *snap.secondary_kpis)}
    # 19 seeded months (2025-04 idx=0 .. 2026-10 idx=18)
    idx = 18
    inc = 200000.0 + idx * 5000.0
    exp = 80000.0 + idx * 1000.0
    expected_cf = inc - exp
    expected_nw = 10000000.0 + idx * 250000.0
    expected_port = 7200000.0 + idx * 180000.0

    assert by_id["market_net_worth"].value == pytest.approx(expected_nw)
    assert by_id["total_income"].value == pytest.approx(inc)
    assert by_id["total_expense"].value == pytest.approx(exp)
    assert by_id["net_cashflow"].value == pytest.approx(expected_cf)
    assert by_id["net_investment_flow"].value == pytest.approx(75000.0 + idx * 1000.0)
    assert by_id["savings_rate"].value == pytest.approx(expected_cf / inc)
    assert by_id["portfolio_value"].value == pytest.approx(expected_port)
    assert by_id["indicative_tax_exposure"].value == pytest.approx(185000.0)
    assert by_id["indicative_tax_exposure"].status_badge == "Incomplete Estimate"
    assert by_id["fi_coverage"].value == pytest.approx(expected_nw / 30000000.0)
    assert by_id["portfolio_xirr"].value == pytest.approx(0.125 + idx * 0.001)
    assert any(b.label.startswith("Latest (15 Nov)") for b in by_id["portfolio_xirr"].breakups)
    assert by_id["portfolio_hhi"].value == pytest.approx(0.145)
    assert by_id["latest_pfetl_run"].value == "SUCCESS"
    assert "book_vs_market" in by_id
    assert "wealth_origin" in by_id
    assert "liquid_illiquid_mix" in by_id
    assert len(snap.secondary_kpis) == 9
    assert len(snap.income_breakup) == 2
    assert len(snap.expense_breakup) == 2
    assert len(snap.asset_class_mix) == 2

    # Filtered class slice narrows asset_class_mix cleanly
    cls_filter = FilterSet(
        filters=(
            FilterSpec(
                field=FilterField.INSTRUMENT_CLASS,
                operator=FilterOperator.EQ,
                value="Mutual Funds",
            ),
        )
    )
    snap_cls = OverviewService.get_snapshot(
        overview_profile,
        period,
        provenance=prov,
        compatibility=compat,
        filters=cls_filter,
        cache=cache,
    )
    assert [c.instrument_class for c in snap_cls.asset_class_mix] == ["Mutual Funds"]

    # Single-month window expands charts cleanly to trailing 12 months
    assert len(snap.net_worth_series) == 12
    assert len(snap.cashflow_series) == 12
    assert len(snap.portfolio_series) == 12
    assert len(snap.fire_series) == 12

    # Second identical fetch returns cached snapshot instance
    snap_cached = OverviewService.get_snapshot(
        overview_profile,
        period,
        provenance=prov,
        compatibility=compat,
        cache=cache,
    )
    assert snap_cached is snap


def test_overview_custom_3m_sums_flows_and_preserves_latest_stocks(
    overview_profile: PFETLProfile,
) -> None:
    """Verify multi-month Custom Range sums flows while keeping stock/rate KPIs at latest month."""
    period = PeriodResolver(DuckDBReader(overview_profile)).resolve(
        PeriodRequest(
            preset=PeriodPreset.CUSTOM,
            custom_start=date(2026, 8, 1),
            custom_end=date(2026, 10, 31),
        )
    )
    snap = OverviewService.get_snapshot(overview_profile, period)
    assert snap.state == "ready"
    by_id = {k.kpi_id: k for k in (*snap.primary_kpis, *snap.secondary_kpis)}

    # Months 2026-08 (idx=16), 2026-09 (idx=17), 2026-10 (idx=18)
    flow_sum = sum((200000.0 + i * 5000.0) - (80000.0 + i * 1000.0) for i in (16, 17, 18))
    latest_inc = 200000.0 + 18 * 5000.0
    latest_exp = 80000.0 + 18 * 1000.0
    latest_cf = latest_inc - latest_exp
    latest_nw = 10000000.0 + 18 * 250000.0

    assert by_id["net_cashflow"].value == pytest.approx(flow_sum)
    assert by_id["market_net_worth"].value == pytest.approx(latest_nw)
    assert by_id["savings_rate"].value == pytest.approx(latest_cf / latest_inc)
    assert by_id["portfolio_xirr"].value == pytest.approx(0.125 + 18 * 0.001)
    assert len(snap.cashflow_series) == 3


def test_overview_fy_filter_missing_rows_lock_and_no_run(tmp_path: Path) -> None:
    """Verify FY filter selection, unavailable missing rows, lock guard, and no-run handling."""
    # 1. Missing FIRE and missing FY 2026-27 Tax -> unavailable (never zero), plus FY filter to 2025-26
    prof = _build_profile_dir(
        tmp_path / "missing_rows",
        include_fire=False,
        include_tax_fy_2026=False,
        tax_incomplete=False,
    )
    period = PeriodResolver(DuckDBReader(prof)).resolve(
        PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH)
    )
    snap_missing = OverviewService.get_snapshot(prof, period)
    p_map = {k.kpi_id: k for k in snap_missing.primary_kpis}
    assert p_map["fi_coverage"].available is False
    assert p_map["fi_coverage"].value is None
    assert p_map["indicative_tax_exposure"].available is False
    assert p_map["indicative_tax_exposure"].value is None

    fy_filter = FilterSet(
        filters=(FilterSpec(field=FilterField.FY, operator=FilterOperator.EQ, value="2025-26"),)
    )
    snap_fy25 = OverviewService.get_snapshot(prof, period, filters=fy_filter)
    p_map_25 = {k.kpi_id: k for k in snap_fy25.primary_kpis}
    assert p_map_25["indicative_tax_exposure"].available is True
    assert p_map_25["indicative_tax_exposure"].value == pytest.approx(142000.0)
    assert p_map_25["indicative_tax_exposure"].status_badge == "Estimate Ready"

    # 2. Locked DuckDB preserves Control Plane health without opening DuckDB
    prof.lock_path.write_text("CORE RUNNING", encoding="utf-8")
    snap_locked = OverviewService.get_snapshot(prof, period)
    assert snap_locked.state == "locked"
    assert snap_locked.primary_kpis == ()
    assert snap_locked.run_health.status == "SUCCESS"
    prof.lock_path.unlink()

    # 3. No successful PFETL runs -> zero headline metrics cleanly
    scon = sqlite3.connect(prof.control_plane_path)
    try:
        scon.execute("UPDATE cp_runs SET status = 'FAILED';")
        scon.commit()
    finally:
        scon.close()
    snap_no_run = OverviewService.get_snapshot(prof, period)
    assert snap_no_run.state == "no_run"
    assert snap_no_run.primary_kpis == ()


def test_overview_module_never_touches_bronze_or_ui() -> None:
    """Enforce Gold/Meta/CP boundary and headless purity on `analytics/overview.py`."""
    src_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "pfetl_analytics_studio"
        / "analytics"
        / "overview.py"
    )
    content = src_path.read_text(encoding="utf-8")
    assert "bronze." not in content.lower()
    tree = ast.parse(content, filename=str(src_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(("nicegui", "personal_finance_etl"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(
                ("nicegui", "pfetl_analytics_studio.ui", "personal_finance_etl")
            )
