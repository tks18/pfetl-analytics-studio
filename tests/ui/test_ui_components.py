"""Tests for reusable presentation widgets, bounded tables, theme, and UI dependency rules."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import Any

from nicegui import Client
from nicegui.page import page

from pfetl_analytics_studio.semantic import (
    ComparisonMode,
    FilterCatalogOptions,
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
    MetricFormat,
    PeriodCatalogOptions,
    PeriodContext,
    PeriodPreset,
    PeriodRequest,
)
from pfetl_analytics_studio.ui import (
    BadgeTone,
    TableColumn,
    apply_studio_theme,
    bounded_data_table,
    build_asset_class_mix_figure,
    build_cashflow_efficiency_figure,
    build_category_breakup_figure,
    build_deployment_flow_figure,
    build_fire_trajectory_figure,
    build_net_worth_attribution_figure,
    build_portfolio_return_figure,
    build_wealth_asset_split_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    filter_chips_bar,
    format_currency,
    format_metric_value,
    format_months,
    format_percent,
    kpi_card,
    loading_state_card,
    locked_warehouse_banner,
    period_selector,
    status_badge,
)


def _collect_client_texts(client: Client) -> list[str]:
    """Extract non-empty text strings across rendered NiceGUI elements."""
    texts: list[str] = []
    for elem in client.elements.values():
        text_val = getattr(elem, "text", None)
        if isinstance(text_val, str) and text_val:
            texts.append(text_val)
    return texts


def test_reusable_ui_components_and_bounded_table() -> None:
    """Verify shared UI widgets render cleanly and enforce table row clamping."""
    client = Client(page("/test_ui_components"))
    try:
        with client:
            apply_studio_theme()
            status_badge("OK", tone=BadgeTone.SUCCESS)
            kpi_card(
                title="Market Net Worth",
                value="₹15.0L",
                subtitle="As of Oct 2026",
                delta="+2.4%",
                delta_tone=BadgeTone.SUCCESS,
                tooltip="Source: gold.core_monthly_fact",
                drill_route="/wealth",
                icon="savings",
            )
            empty_state_panel(
                "Empty View",
                "No rows found.",
                action_label="Go",
                action_target="/overview",
            )
            error_state_panel(
                "Compatibility Error",
                "Missing table.",
                details=["gold.core_monthly_fact"],
            )
            locked_warehouse_banner()
            loading_state_card("Loading data...")

            cols = [TableColumn(name="id", label="ID", field="id")]
            rows: list[dict[str, Any]] = [{"id": f"r-{i}"} for i in range(10)]
            bounded_data_table(cols, rows, row_key="id", max_rows=3, title="Sample Table")
            bounded_data_table(cols, [], title="Empty Table")

            period_selector(
                current_preset=PeriodPreset.LATEST_COMPLETE_MONTH,
                on_preset_change=lambda _p: None,
                resolved_badge="Oct 2026",
            )
            opts = PeriodCatalogOptions(
                months=(("2026-10", "Oct 2026 (2026-10)"), ("2026-09", "Sep 2026 (2026-09)")),
                financial_years=("2026-27", "2025-26"),
                calendar_quarters=("Q4-2026",),
                fy_quarters=("Q3-2026-27",),
                min_date=date(2025, 4, 1),
                max_date=date(2026, 10, 31),
                default_selected_month="2026-10",
                default_financial_year="2026-27",
                default_custom_start=date(2026, 10, 1),
                default_custom_end=date(2026, 10, 31),
            )
            period_selector(
                current_request=PeriodRequest(
                    preset=PeriodPreset.SELECTED_MONTH,
                    selected_month="2026-09",
                ),
                on_request_change=lambda _r: None,
                period_options=opts,
                resolved_badge="Sep 2026",
            )
            period_selector(
                current_request=PeriodRequest(
                    preset=PeriodPreset.CUSTOM,
                    custom_start=date(2026, 8, 1),
                    custom_end=date(2026, 10, 31),
                    comparison_mode=ComparisonMode.CUSTOM,
                    custom_comparison_start=date(2026, 5, 1),
                    custom_comparison_end=date(2026, 7, 31),
                ),
                on_request_change=lambda _r: None,
                period_options=opts,
                period_context=PeriodContext(
                    start_date=date(2026, 8, 1),
                    end_date=date(2026, 10, 31),
                    label="Custom Range",
                    preset=PeriodPreset.CUSTOM,
                    financial_year="2026-27",
                ),
                resolved_badge="Custom Range (2026-08-01 to 2026-10-31)",
            )
            f_opts = FilterCatalogOptions(
                values_by_field={
                    FilterField.FY: ("2026-27", "2025-26"),
                    FilterField.INSTRUMENT_CLASS: ("Mutual Funds", "US Stocks"),
                }
            )
            fset = FilterSet(
                filters=(
                    FilterSpec(field=FilterField.FY, operator=FilterOperator.EQ, value="2026-27"),
                )
            )
            filter_chips_bar(
                fset,
                on_remove=lambda _f: None,
                on_clear=lambda: None,
                on_add=lambda _s: None,
                available_fys=("2026-27", "2025-26"),
                filter_options=f_opts,
            )
            chart_panel(
                "Sample Chart",
                "Subtitle",
                build_net_worth_attribution_figure([]),
                source_caption="Source: gold.core_monthly_fact",
                drill_route="/wealth",
            )

        rendered = " ".join(_collect_client_texts(client))
        assert "Showing bounded preview: 3 of 10 rows" in rendered
        assert "Empty Table" in rendered
        assert "Market Net Worth" in rendered
    finally:
        client.delete()


def test_formatting_and_chart_figure_builders() -> None:
    """Verify INR/USD compact formatting, fractional percent scaling, and dark Plotly builders."""
    assert format_currency(12450000.0, "INR_INR", compact=True) == "₹1.25 Cr"
    assert format_currency(845000.0, "INR_INR", compact=True) == "₹8.45 L"
    assert format_currency(12345678.0, "INR_INR", compact=False) == "₹1,23,45,678"
    assert format_currency(-50000.0, "USD_USD", compact=True) == "-$50.0K"
    assert format_percent(0.6752, decimals=1) == "67.5%"
    assert format_percent(0.0345, decimals=2, signed=True) == "+3.45%"
    assert format_months(88.4) == "88.4 mos"
    assert format_metric_value(None, MetricFormat.CURRENCY) == "Unavailable"

    class _Pt:
        month_end = date(2026, 10, 31)
        closing_date = date(2026, 10, 31)
        net_worth_market = 1000000.0
        net_worth_book = 950000.0
        net_savings_flow = 50000.0
        market_return_flow = 20000.0
        income = 200000.0
        expense = 80000.0
        net_cashflow = 120000.0
        deployed = 90000.0
        redeemed = 15000.0
        net_investment_flow = 75000.0
        current_value = 720000.0
        invested_value = 600000.0
        xirr = 0.125
        bm_xirr = 0.095
        fi_coverage = 0.28
        success_probability = 0.94
        label = "Salary"
        group = "Income from Salary"
        amount = 180000.0
        bucket = "Active"
        instrument_class = "Mutual Funds"
        weight = 0.78
        active_return = 0.05
        asset_group = "Investments"
        market_value = 800000.0
        book_value = 740000.0
        unrealized_pl = 60000.0

    pt = _Pt()
    nw_fig = build_net_worth_attribution_figure([pt])
    cf_fig = build_cashflow_efficiency_figure([pt])
    dep_fig = build_deployment_flow_figure([pt])
    p_fig = build_portfolio_return_figure([pt])
    f_fig = build_fire_trajectory_figure([pt])
    cat_fig = build_category_breakup_figure([pt])
    cls_fig = build_asset_class_mix_figure([pt])
    wab_fig = build_wealth_asset_split_figure([pt])
    assert nw_fig is not None and len(nw_fig["data"]) == 4
    assert cf_fig is not None and len(cf_fig["data"]) == 3
    assert dep_fig is not None and len(dep_fig["data"]) == 3
    assert p_fig is not None and len(p_fig["data"]) == 4
    assert f_fig is not None and len(f_fig["data"]) == 2
    assert cat_fig is not None and len(cat_fig["data"]) == 1
    assert cls_fig is not None and len(cls_fig["data"]) == 1
    assert wab_fig is not None and len(wab_fig["data"]) == 2


def test_ui_package_has_no_data_or_analytics_imports() -> None:
    """Enforce architectural boundary: ui/ must never import data/ or analytics/."""
    ui_dir = Path(__file__).resolve().parents[2] / "src" / "pfetl_analytics_studio" / "ui"
    forbidden_prefixes = (
        "pfetl_analytics_studio.data",
        "pfetl_analytics_studio.analytics",
        "duckdb",
        "sqlite3",
        "personal_finance_etl",
    )
    for py_file in ui_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_prefixes), (
                        f"Forbidden import '{alias.name}' in {py_file.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_prefixes), (
                    f"Forbidden import-from '{node.module}' in {py_file.name}"
                )
