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
    CHART_ROLE_COLORS,
    BadgeTone,
    ChartColorRole,
    TableColumn,
    TreemapNode,
    WaterfallStep,
    apply_studio_theme,
    bounded_data_table,
    build_asset_class_mix_figure,
    build_budget_allocation_trend_figure,
    build_budget_health_heatmap_figure,
    build_budget_target_vs_actual_figure,
    build_cashflow_activity_trend_figure,
    build_cashflow_efficiency_figure,
    build_cashflow_waterfall_figure,
    build_category_breakup_figure,
    build_deployment_flow_figure,
    build_emergency_runway_figure,
    build_expense_account_mix_figure,
    build_expense_cash_vs_noncash_figure,
    build_expense_category_bars_figure,
    build_expense_core_trend_figure,
    build_expense_heatmap_figure,
    build_expense_real_vs_nominal_figure,
    build_expense_treemap_figure,
    build_fire_trajectory_figure,
    build_heatmap_figure,
    build_income_account_mix_figure,
    build_income_category_bars_figure,
    build_income_composition_figure,
    build_income_trend_figure,
    build_income_ytd_figure,
    build_net_worth_attribution_figure,
    build_passive_income_engine_figure,
    build_portfolio_return_figure,
    build_treemap_figure,
    build_tx_account_breakdown_figure,
    build_tx_daily_activity_figure,
    build_tx_kind_volume_figure,
    build_waterfall_figure,
    build_wealth_asset_split_figure,
    build_wealth_attribution_waterfall_figure,
    build_wealth_drawdown_figure,
    build_wealth_group_treemap_figure,
    build_wealth_liquidity_debt_figure,
    build_wealth_nw_comparison_figure,
    build_wealth_pnl_fx_figure,
    build_wealth_savings_vs_market_figure,
    chart_config,
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
    studio_plotly_layout,
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

    class _IncPt:
        month_end = date(2026, 10, 31)
        total_income = 150000.0
        cash_income = 144000.0
        non_cash_income = 6000.0
        active_income = 138000.0
        passive_income = 6000.0
        dividend_income = 1000.0
        interest_income = 5000.0
        trailing_3m_avg = 145000.0
        budget_income = 142000.0
        cumulative_ytd = 1200000.0
        active_dependency_pct = 0.92
        passive_core_coverage = 0.17
        category_name = "Basic"
        category_group = "Income from Salary"
        amount = 80000.0
        share_pct = 0.55
        taxability = "taxable"
        account_name = "HDFC Bank"
        tx_count = 5

    inc_pt = _IncPt()
    assert build_income_trend_figure([inc_pt]) is not None
    comp_fig = build_income_composition_figure([inc_pt])
    assert comp_fig is not None
    # Active (138k) + Passive (6k) + Non-Cash (6k) = 150k -> no residual stack needed
    bar_traces = [t for t in comp_fig["data"] if t["type"] == "bar"]
    assert sum(t["y"][0] for t in bar_traces) == 150000.0

    # Residual unclassified positive income adds "Other / Unclassified" so stacks reconcile
    inc_pt.total_income = 155000.0
    comp_res_fig = build_income_composition_figure([inc_pt])
    assert comp_res_fig is not None
    res_bars = [t for t in comp_res_fig["data"] if t["type"] == "bar"]
    assert any(t["name"] == "Other / Unclassified" for t in res_bars)
    assert sum(t["y"][0] for t in res_bars) == 155000.0

    assert build_income_category_bars_figure([inc_pt]) is not None
    assert build_income_ytd_figure([inc_pt]) is not None
    assert build_passive_income_engine_figure([inc_pt]) is not None
    assert build_income_account_mix_figure([inc_pt]) is not None

    # FI trajectory includes 100% FI Target reference line
    assert f_fig["layout"]["shapes"][0]["y0"] == 100.0

    # Generalized axis semantics + semantic color roles
    assert studio_plotly_layout(x_axis_type="date")["xaxis"]["type"] == "date"
    assert studio_plotly_layout(x_axis_type="category")["xaxis"]["type"] == "category"
    assert studio_plotly_layout(x_axis_type="linear")["xaxis"]["type"] == "linear"
    assert ChartColorRole.TARGET in CHART_ROLE_COLORS
    assert chart_config("cockpit")["displayModeBar"] is False
    assert chart_config("analytical")["displayModeBar"] is True

    # Reusable waterfall, treemap, and heatmap primitives
    wf_fig = build_waterfall_figure(
        [
            WaterfallStep("Opening", 100000.0, measure="absolute"),
            WaterfallStep("Savings", 25000.0, measure="relative"),
            WaterfallStep("Market", -5000.0, measure="relative"),
            WaterfallStep("Closing", 120000.0, measure="total"),
        ]
    )
    assert wf_fig is not None and wf_fig["data"][0]["type"] == "waterfall"
    assert wf_fig["layout"]["xaxis"]["type"] == "category"

    tm_fig = build_treemap_figure(
        [
            TreemapNode("root", "All", "", 1000.0, key="all"),
            TreemapNode("eq", "Equity", "root", 700.0, share_pct=0.7, key="EQ"),
            TreemapNode("dt", "Debt", "root", 300.0, share_pct=0.3, key="DT"),
        ]
    )
    assert tm_fig is not None and tm_fig["data"][0]["type"] == "treemap"

    hm_fig = build_heatmap_figure(
        ["2026-09", "2026-10"], ["Food", "Travel"], [[100.0, None], [50.0, 75.0]]
    )
    assert hm_fig is not None and hm_fig["data"][0]["type"] == "heatmap"

    class _ExpPt:
        month_end = date(2026, 10, 31)
        total_expense = 47000.0
        core_expense = 33000.0
        non_core_expense = 14000.0
        cash_expense = 32800.0
        non_cash_expense = 14200.0
        inflation_adjusted_core = 29000.0
        real_core_growth_pct = 0.12
        trailing_3m_avg = 46000.0
        category_id = "e1"
        category_name = "House Rent"
        category_group = "Rents"
        spend_type = "Fixed"
        is_core = True
        is_discretionary = False
        amount = 15000.0
        share_pct = 0.32
        account_name = "HDFC Bank"
        tx_count = 3
        months = ("2026-09", "2026-10")
        groups = ("Rents", "Food")
        matrix = ((15000.0, 15000.0), (5000.0, 7000.0))

    exp_pt = _ExpPt()
    assert build_expense_core_trend_figure([exp_pt]) is not None
    assert build_expense_real_vs_nominal_figure([exp_pt]) is not None
    assert build_expense_cash_vs_noncash_figure([exp_pt]) is not None
    assert build_expense_treemap_figure([exp_pt]) is not None
    assert build_expense_heatmap_figure(exp_pt) is not None
    assert build_expense_category_bars_figure([exp_pt]) is not None
    assert build_expense_account_mix_figure([exp_pt]) is not None

    # Numeric/right-aligned TableColumn attaches custom JS numeric comparator
    num_col = TableColumn(name="amt", label="Amount", field="amt", align="right").to_quasar_dict()
    txt_col = TableColumn(name="cat", label="Category", field="cat", align="left").to_quasar_dict()
    assert ":sort" in num_col
    assert ":sort" not in txt_col

    class _BudPt:
        month_end = date(2026, 10, 31)
        year_month = "2026-10"
        actual_income = 67000.0
        budget_income = 65000.0
        actual_core = 26000.0
        budget_core = 27000.0
        actual_non_core = 12000.0
        budget_non_core = 13000.0
        actual_investment = 25000.0
        budget_investment = 25000.0
        emergency_actual = 150000.0
        emergency_target = 180000.0
        runway_months = 5.5

        class flags:  # noqa: N801
            is_budget_healthy = True
            is_core_overspent = False
            is_non_core_overspent = False
            is_investment_underfunded = False
            is_income_volatile = False

    class _BudSnap:
        state = "ready"
        currency_id = "INR_INR"
        total_budget_income = 65000.0
        total_actual_income = 67000.0
        total_budget_core = 27000.0
        total_actual_core = 26000.0
        total_budget_non_core = 13000.0
        total_actual_non_core = 12000.0
        total_budget_investment = 25000.0
        total_actual_investment = 25000.0
        monthly_series = (_BudPt(),)

    class _CfPt:
        month_end = date(2026, 10, 31)
        year_month = "2026-10"
        opening_cash = 100000.0
        closing_cash = 129500.0
        net_operating = 29000.0
        net_investing = -20000.0
        net_financing = 0.0
        net_transfers = 20000.0
        unreconciled_difference = 500.0
        is_reconciled = False

    class _CfSnap:
        state = "ready"
        currency_id = "INR_INR"
        opening_cash = 100000.0
        closing_cash = 129500.0
        net_operating = 29000.0
        net_investing = -20000.0
        net_financing = 0.0
        net_transfers = 20000.0
        unreconciled_difference = 500.0
        monthly_series = (_CfPt(),)

    assert build_budget_target_vs_actual_figure(_BudSnap()) is not None
    assert build_budget_allocation_trend_figure([_BudPt()], "INR_INR") is not None
    assert build_emergency_runway_figure([_BudPt()], "INR_INR") is not None
    assert build_cashflow_waterfall_figure(_CfSnap()) is not None
    assert build_cashflow_activity_trend_figure([_CfPt()], "INR_INR") is not None
    assert build_budget_health_heatmap_figure([_BudPt()], [_CfPt()]) is not None

    class _TxDaily:
        tx_date = date(2026, 10, 15)
        income_amount = 50000.0
        expense_amount = 15000.0
        transfer_volume = 20000.0

    class _TxAcc:
        account_name = "HDFC Bank"
        volume = 85000.0
        tx_count = 4

    class _TxSummary:
        total_count = 4
        income_total = 50000.0
        expense_total = 15000.0
        transfer_volume = 20000.0
        income_count = 1
        expense_count = 2
        transfer_count = 1
        daily_series = (_TxDaily(),)
        account_breakdown = (_TxAcc(),)

    assert build_tx_kind_volume_figure(_TxSummary(), "INR_INR") is not None
    assert build_tx_daily_activity_figure(_TxSummary(), "INR_INR") is not None
    assert build_tx_account_breakdown_figure(_TxSummary(), "INR_INR") is not None

    class _WealthPt:
        month_end = date(2026, 10, 31)
        book_net_worth = 1050000.0
        market_net_worth = 1130000.0
        closing_unrealized_pl = 80000.0
        closing_asset_pnl = 74000.0
        closing_forex_pnl = 6000.0
        liquidity_ratio_months = 18.0
        debt_to_asset_pct = 0.01
        yoy_nw_growth_real_pct = 0.30

    class _WealthAttr:
        state = "ready"
        currency_id = "INR_INR"
        opening_market_assets = 1070000.0
        net_savings_flow = 50000.0
        market_return_flow = 30000.0
        attribution_residual = -5000.0
        closing_market_assets = 1145000.0

    class _WealthAsset:
        asset_id = "a_eq"
        asset_name = "Direct Equity"
        asset_group = "Equity"
        closing_balance = 535000.0
        closing_balance_market = 615000.0
        drawdown_from_peak = -0.05
        cumulative_net_savings = 400000.0
        is_liquid = True

    assert build_wealth_nw_comparison_figure([_WealthPt()], "INR_INR") is not None
    assert build_wealth_attribution_waterfall_figure(_WealthAttr()) is not None
    assert build_wealth_group_treemap_figure([_WealthAsset()], "INR_INR") is not None
    assert build_wealth_pnl_fx_figure([_WealthPt()], "INR_INR") is not None
    assert build_wealth_liquidity_debt_figure([_WealthPt()]) is not None
    assert build_wealth_drawdown_figure([_WealthAsset()], "INR_INR") is not None
    assert build_wealth_savings_vs_market_figure([_WealthAsset()], "INR_INR") is not None


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
