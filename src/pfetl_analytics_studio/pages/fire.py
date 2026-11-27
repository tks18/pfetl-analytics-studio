"""FIRE & Simulation Analytics page — 3-tab financial independence cockpit."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from nicegui import ui

from pfetl_analytics_studio.analytics import (
    FireAnalytics,
    FireSummarySnapshot,
    OverviewKPI,
    SimulationHistorySnapshot,
)
from pfetl_analytics_studio.context import ROUTES_BY_ID, AppContext
from pfetl_analytics_studio.data import QueryCache
from pfetl_analytics_studio.pages import render_domain_workspace
from pfetl_analytics_studio.ui import (
    BadgeTone,
    TableColumn,
    bounded_data_table,
    build_fire_acceleration_cagr_figure,
    build_fire_corpus_targets_figure,
    build_fire_future_terminal_figure,
    build_fire_gap_coverage_figure,
    build_fire_horizon_years_figure,
    build_fire_income_vs_spend_figure,
    build_fire_monte_carlo_band_figure,
    build_fire_probability_trend_figure,
    build_fire_runway_comparison_figure,
    build_fire_savings_rate_figure,
    build_fire_scenario_projection_figure,
    build_fire_spend_vs_savings_figure,
    build_fire_velocity_accel_figure,
    chart_panel,
    empty_state_panel,
    error_state_panel,
    format_currency,
    format_metric_value,
    format_months,
    format_percent,
    kpi_card,
    locked_warehouse_banner,
    status_badge,
)

_SUB_TABS: tuple[tuple[str, str, str], ...] = (
    ("trajectory", "FI Trajectory & Targets", "trending_up"),
    ("runway", "Runway, Savings & Velocity", "speed"),
    ("provenance", "Simulation Provenance & Rules", "verified"),
)

_RAW_DICT_KEYS: frozenset[str] = frozenset(
    {
        "MF_SCHEME_MAPPINGS",
        "assets",
        "account_mappings",
        "category_mappings",
        "symbol_mappings",
    }
)


def _short_hash(val: str) -> str:
    if not val:
        return "—"
    return f"{val[:10]}…{val[-6:]}" if len(val) > 20 else val


def _extract_readable_rules(rules: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Extract clean human-readable scalar/planning parameters from FinancialRules."""
    items: list[tuple[str, str, str]] = []

    def _fmt_val(key: str, val: Any) -> str:
        if isinstance(val, bool):
            return "Enabled" if val else "Disabled"
        if isinstance(val, (int, float)):
            k_low = key.lower()
            if (
                any(t in k_low for t in ("rate", "pct", "return", "inflation", "swr", "yield"))
                and abs(float(val)) <= 1.5
            ):
                return format_percent(float(val) * 100.0)
            return f"{val:,.4g}" if isinstance(val, float) else f"{val:,}"
        return str(val)

    for top_k, top_v in sorted(rules.items()):
        if top_k in _RAW_DICT_KEYS:
            continue
        if isinstance(top_v, dict):
            sub_map = cast(dict[str, Any], top_v)
            section_lbl = str(top_k).replace("_", " ").title()
            for sub_k, sub_v in sorted(sub_map.items()):
                if isinstance(sub_v, (dict, list)):
                    continue
                lbl = str(sub_k).replace("_", " ").title()
                items.append((section_lbl, lbl, _fmt_val(str(sub_k), sub_v)))
        elif isinstance(top_v, list):
            continue
        else:
            lbl = str(top_k).replace("_", " ").title()
            items.append(("General", lbl, _fmt_val(str(top_k), top_v)))
    return items


def _render_kpi_grid(kpis: tuple[OverviewKPI, ...], currency_id: str) -> None:
    with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3"):
        for kpi in kpis:
            delta_str: str | None = None
            delta_tone = BadgeTone.NEUTRAL
            if kpi.delta_value is not None:
                delta_str = format_metric_value(
                    kpi.delta_value, kpi.format, currency_id=currency_id
                )
                if kpi.delta_value > 0:
                    delta_tone = BadgeTone.INFO
                elif kpi.delta_value < 0:
                    delta_tone = BadgeTone.WARNING
            breakups = tuple(
                (
                    b.label,
                    format_metric_value(
                        b.value,
                        b.format,
                        currency_id=currency_id,
                        compact=True,
                        unit_suffix=b.unit_suffix,
                    ),
                )
                for b in kpi.breakups
                if b.value is not None
            )
            kpi_card(
                title=kpi.label,
                value=format_metric_value(
                    kpi.value,
                    kpi.format,
                    currency_id=currency_id,
                    unit_suffix=kpi.unit_suffix,
                ),
                subtitle=kpi.subtitle or kpi.delta_label or None,
                delta=delta_str,
                delta_tone=delta_tone,
                breakups=breakups,
                icon="local_fire_department",
            )


def _render_trajectory_tab(snap: FireSummarySnapshot) -> None:
    pts = snap.history
    with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4"):
        chart_panel(
            title="FI Corpus Targets vs Current Wealth",
            subtitle="Target FI, Coast FI, Lean FI, and FIRE-base net worth progression",
            figure=build_fire_corpus_targets_figure(pts, currency_id=snap.currency_id),
            source_caption="Source: gold.wealth_fire_analytics",
        )
        chart_panel(
            title="FI Corpus Gap & Coverage %",
            subtitle="Remaining corpus gap (bars) and coverage ratio (line)",
            figure=build_fire_gap_coverage_figure(pts, currency_id=snap.currency_id),
            source_caption="Source: gold.wealth_fire_analytics",
        )
        chart_panel(
            title="Monte Carlo Months to FI (P10 / P50 / P90)",
            subtitle="Stochastic completion horizon envelope vs linear projection",
            figure=build_fire_monte_carlo_band_figure(pts),
            source_caption="Source: gold.wealth_fire_analytics",
        )
        chart_panel(
            title="Monte Carlo Success Probability",
            subtitle="Simulated trajectory success rate against 85% comfort floor",
            figure=build_fire_probability_trend_figure(pts),
            source_caption="Source: gold.wealth_fire_analytics",
        )
        chart_panel(
            title="Terminal Wealth (P50) vs Future Nominal Target",
            subtitle="Median end-of-horizon corpus compared with inflation-adjusted FI target",
            figure=build_fire_future_terminal_figure(pts, currency_id=snap.currency_id),
            source_caption="Source: gold.wealth_fire_analytics",
        )
        chart_panel(
            title="Years to FI (P50) & Monthly Gap Delta",
            subtitle="Projected horizon in years paired with monthly FI gap compression",
            figure=build_fire_horizon_years_figure(pts, currency_id=snap.currency_id),
            source_caption="Source: gold.wealth_fire_analytics",
        )


def _render_sensitivity_sandbox(snap: FireSummarySnapshot) -> None:
    """Visually isolated arithmetic sensitivity card (non-canonical)."""
    latest = snap.latest
    if latest is None:
        return
    base_corpus = max(latest.target_fi_today - latest.fi_gap, 0.0)
    cov_ratio = base_corpus / latest.target_fi_today if latest.target_fi_today > 0 else 0.0
    base_spend = max(latest.trailing_12m_avg_spend, 1.0)
    base_savings = latest.trailing_12m_avg_savings
    base_target = max(latest.target_fi_today, 1.0)
    base_real_ret = (
        (latest.real_return_assumed_pct * 100.0)
        if latest.real_return_assumed_pct is not None and abs(latest.real_return_assumed_pct) <= 1.5
        else (latest.real_return_assumed_pct or 5.0)
    )
    default_ret = round(max(2.0, min(10.0, base_real_ret)), 1)

    with (
        ui.card()
        .classes("w-full p-4 gap-4 rounded-lg")
        .style("background: rgba(245, 158, 11, 0.05); border: 1px dashed rgba(245, 158, 11, 0.45);")
    ):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("tune", size="22px").style("color: #FBBF24;")
                with ui.column().classes("gap-0"):
                    ui.label("What-If Sensitivity Scenario Sandbox").classes(
                        "text-subtitle1 font-semibold"
                    )
                    ui.label(
                        "Test lifestyle spend, extra SIP savings, and real return levers against your latest PFETL snapshot (arithmetic only — does not re-run Monte Carlo)."
                    ).classes("text-caption").style("color: var(--studio-text-muted);")
            with ui.row().classes("items-center gap-2 flex-wrap"):
                status_badge("Scenario only — not PFETL canonical output", tone=BadgeTone.WARNING)

        with ui.grid().classes("w-full grid-cols-1 md:grid-cols-3 gap-4"):
            with (
                ui.column()
                .classes("gap-2 p-3 rounded-lg")
                .style(
                    "background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(245, 158, 11, 0.22);"
                )
            ):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("1. Monthly Spend Multiplier").classes("text-body2 font-semibold")
                    spend_badge = (
                        ui.label("100%")
                        .classes("text-caption font-mono font-semibold")
                        .style("color: #FBBF24;")
                    )
                ui.label(
                    "Scales your trailing 12M spend & required FI corpus target (e.g. 85% = 15% leaner lifestyle)."
                ).classes("text-caption").style("color: var(--studio-text-muted);")
                spend_mult = (
                    ui.slider(min=70, max=130, step=5, value=100)
                    .props("color=amber")
                    .classes("w-full mt-1")
                )

            with (
                ui.column()
                .classes("gap-2 p-3 rounded-lg")
                .style(
                    "background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(245, 158, 11, 0.22);"
                )
            ):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("2. Extra Monthly Savings").classes("text-body2 font-semibold")
                    save_badge = (
                        ui.label("+0/mo")
                        .classes("text-caption font-mono font-semibold")
                        .style("color: #34D399;")
                    )
                ui.label(
                    "Additional monthly SIP / surplus invested on top of your trailing 12M savings."
                ).classes("text-caption").style("color: var(--studio-text-muted);")
                extra_save = (
                    ui.slider(min=0, max=200000, step=10000, value=0)
                    .props("color=amber")
                    .classes("w-full mt-1")
                )

            with (
                ui.column()
                .classes("gap-2 p-3 rounded-lg")
                .style(
                    "background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(245, 158, 11, 0.22);"
                )
            ):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("3. Assumed Real Return").classes("text-body2 font-semibold")
                    ret_badge = (
                        ui.label(f"{default_ret:.1f}% p.a.")
                        .classes("text-caption font-mono font-semibold")
                        .style("color: #60A5FA;")
                    )
                ui.label(
                    "Annualized portfolio compounding rate above inflation used for linear projection."
                ).classes("text-caption").style("color: var(--studio-text-muted);")
                real_ret = (
                    ui.slider(min=2.0, max=10.0, step=0.5, value=default_ret)
                    .props("color=amber")
                    .classes("w-full mt-1")
                )

        def _apply_preset(s_m: int, e_s: int, r_r: float) -> None:
            spend_mult.set_value(s_m)
            extra_save.set_value(e_s)
            real_ret.set_value(r_r)
            _recompute()

        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            ui.label("Quick Presets:").classes("text-caption font-semibold").style(
                "color: var(--studio-text-muted);"
            )
            ui.button("Baseline", on_click=lambda: _apply_preset(100, 0, default_ret)).props(
                "dense outline size=sm color=amber"
            )
            ui.button(
                "Lean (-15% Spend)", on_click=lambda: _apply_preset(85, 0, default_ret)
            ).props("dense outline size=sm color=amber")
            ui.button(
                "Boost (+50K/mo)", on_click=lambda: _apply_preset(100, 50000, default_ret)
            ).props("dense outline size=sm color=amber")
            ui.button("Stress (3% Return)", on_click=lambda: _apply_preset(100, 0, 3.0)).props(
                "dense outline size=sm color=amber"
            )

        out_row = ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3")
        chart_box = ui.column().classes("w-full")

        def _recompute() -> None:
            out_row.clear()
            chart_box.clear()
            m_pct = int(float(spend_mult.value or 100))
            m_spend = m_pct / 100.0
            add_save = float(extra_save.value or 0.0)
            r_pct = float(real_ret.value or default_ret)
            r_annual = r_pct / 100.0

            scen_spend = base_spend * m_spend
            spend_badge.set_text(f"{m_pct}% ({format_currency(scen_spend, snap.currency_id)}/mo)")
            save_badge.set_text(f"+{format_currency(add_save, snap.currency_id)}/mo")
            ret_badge.set_text(f"{r_pct:.1f}% p.a.")

            scenario_target = base_target * m_spend
            scenario_cov = (base_corpus / scenario_target) if scenario_target > 0 else 0.0
            monthly_rate = (1.0 + r_annual) ** (1.0 / 12.0) - 1.0
            monthly_contrib = max(base_savings + add_save - base_spend * (m_spend - 1.0), 0.0)
            corpus = base_corpus
            months = 0
            while corpus < scenario_target and months < 600:
                corpus = corpus * (1.0 + monthly_rate) + monthly_contrib
                months += 1
            months_txt = ">= 600m" if corpus < scenario_target else format_months(months)
            scen_runway = base_corpus / scen_spend if scen_spend > 0 else 0.0
            base_runway = base_corpus / base_spend if base_spend > 0 else 0.0

            with out_row:
                kpi_card(
                    title="Scenario Target FI",
                    value=format_currency(scenario_target, snap.currency_id),
                    subtitle=f"Canonical: {format_currency(base_target, snap.currency_id)}",
                    icon="flag",
                )
                kpi_card(
                    title="Scenario FI Coverage",
                    value=format_percent(scenario_cov),
                    subtitle=f"Canonical: {format_percent(cov_ratio)}",
                    icon="pie_chart",
                )
                kpi_card(
                    title="Scenario Linear Months to FI",
                    value=months_txt,
                    subtitle=f"Monthly savings: {format_currency(monthly_contrib, snap.currency_id)}",
                    icon="schedule",
                )
                kpi_card(
                    title="Scenario Retirement Runway",
                    value=format_months(scen_runway),
                    subtitle=f"Canonical Linear: {format_months(base_runway)}",
                    icon="timelapse",
                )
            with chart_box:
                chart_panel(
                    title="Scenario vs Canonical Trajectory (10-Year Arithmetic Preview)",
                    subtitle="Compares baseline compounding against your interactive sensitivity levers",
                    figure=build_fire_scenario_projection_figure(
                        base_corpus=base_corpus,
                        base_target=base_target,
                        base_monthly_savings=base_savings,
                        base_real_return_pct=default_ret,
                        scenario_target=scenario_target,
                        scenario_monthly_savings=monthly_contrib,
                        scenario_real_return_pct=r_pct,
                        currency_id=snap.currency_id,
                    ),
                    source_caption="Non-canonical arithmetic preview over latest gold.wealth_fire_analytics row",
                )

        spend_mult.on("update:model-value", lambda _: _recompute())
        extra_save.on("update:model-value", lambda _: _recompute())
        real_ret.on("update:model-value", lambda _: _recompute())
        _recompute()


def _render_runway_tab(snap: FireSummarySnapshot) -> None:
    pts = snap.history
    with ui.column().classes("w-full gap-4"):
        with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4"):
            chart_panel(
                title="Portfolio Runway Comparison (Months)",
                subtitle="Linear vs Base (P50) vs Stressed (P10) survival horizon",
                figure=build_fire_runway_comparison_figure(pts),
                source_caption="Source: gold.wealth_fire_analytics",
            )
            chart_panel(
                title="Savings Rate vs Required & SWR",
                subtitle="Actual vs required savings rate and implied withdrawal rate",
                figure=build_fire_savings_rate_figure(pts),
                source_caption="Source: gold.wealth_fire_analytics",
            )
            chart_panel(
                title="Trailing Spend vs Savings Engine",
                subtitle="6M and 12M rolling spend compared with savings capacity",
                figure=build_fire_spend_vs_savings_figure(pts, currency_id=snap.currency_id),
                source_caption="Source: gold.wealth_fire_analytics",
            )
            chart_panel(
                title="Wealth Velocity & 3Y Real CAGR",
                subtitle="Monthly wealth velocity paired with FI velocity and 3Y real CAGR",
                figure=build_fire_velocity_accel_figure(pts, currency_id=snap.currency_id),
                source_caption="Source: gold.wealth_fire_analytics",
            )
            chart_panel(
                title="Monthly Income vs Trailing Spend",
                subtitle="Total & cash income inputs compared against trailing 12M spend",
                figure=build_fire_income_vs_spend_figure(pts, currency_id=snap.currency_id),
                source_caption="Source: gold.wealth_fire_analytics",
            )
            chart_panel(
                title="Wealth Acceleration & Assumed Real Return",
                subtitle="Month-over-month velocity change paired with real return assumption",
                figure=build_fire_acceleration_cagr_figure(pts, currency_id=snap.currency_id),
                source_caption="Source: gold.wealth_fire_analytics",
            )
        _render_sensitivity_sandbox(snap)


def _render_provenance_tab(sim_snap: SimulationHistorySnapshot) -> None:
    with ui.column().classes("w-full gap-4"):
        meta = sim_snap.latest_meta
        if meta is not None:
            horizon_str = (
                f"{meta.horizon} mos ({meta.horizon / 12.0:.1f} yrs)"
                if meta.horizon is not None
                else "—"
            )
            with ui.card().classes("studio-card w-full p-4 gap-3 rounded-lg"):
                with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("verified", size="20px").style("color: var(--studio-accent);")
                        ui.label("Latest Simulation Provenance (meta.m_Latest_Simulation)").classes(
                            "text-subtitle1 font-semibold"
                        )
                    with ui.row().classes("items-center gap-2"):
                        status_badge(
                            f"Model: {meta.model_implementation_version or 'n/a'}",
                            tone=BadgeTone.INFO,
                        )
                        status_badge(
                            f"Iterations: {meta.iterations:,}"
                            if meta.iterations
                            else "Iterations: —",
                            tone=BadgeTone.NEUTRAL,
                        )
                fields: list[tuple[str, str, str | None]] = [
                    ("Simulation ID", meta.simulation_id, meta.simulation_id),
                    ("Run ID", _short_hash(meta.run_id), meta.run_id or None),
                    ("Created At", meta.created_at or "—", None),
                    (
                        "Input As-Of Date",
                        meta.input_as_of_date.isoformat() if meta.input_as_of_date else "—",
                        None,
                    ),
                    ("Horizon", horizon_str, None),
                    ("Root Seed", _short_hash(meta.root_seed), meta.root_seed or None),
                    (
                        "Model Fingerprint",
                        _short_hash(meta.model_fingerprint),
                        meta.model_fingerprint or None,
                    ),
                    (
                        "Input Fingerprint",
                        _short_hash(meta.input_fingerprint),
                        meta.input_fingerprint or None,
                    ),
                    (
                        "Result Fingerprint",
                        _short_hash(meta.result_fingerprint),
                        meta.result_fingerprint or None,
                    ),
                    (
                        "Contract Registry",
                        _short_hash(meta.contract_registry_fingerprint),
                        meta.contract_registry_fingerprint or None,
                    ),
                    (
                        "Settings Snapshot",
                        _short_hash(meta.settings_snapshot_id),
                        meta.settings_snapshot_id or None,
                    ),
                    (
                        "Rules Snapshot",
                        _short_hash(meta.rules_snapshot_id),
                        meta.rules_snapshot_id or None,
                    ),
                ]
                with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-2"):
                    for lbl, val, full_tip in fields:
                        with (
                            ui.column()
                            .classes("gap-0 p-2.5 rounded")
                            .style(
                                "background: rgba(15, 23, 42, 0.55); border: 1px solid rgba(148, 163, 184, 0.12);"
                            )
                        ):
                            ui.label(lbl).classes("text-caption").style(
                                "color: var(--studio-text-muted);"
                            )
                            lbl_el = ui.label(val).classes("text-body2 font-mono font-semibold")
                            if full_tip and full_tip != val:
                                with lbl_el:
                                    ui.tooltip(full_tip)
        else:
            empty_state_panel(
                "No Simulation Metadata Found",
                "meta.m_Latest_Simulation is unavailable or empty in this warehouse.",
            )

        with ui.card().classes("studio-card w-full p-4 gap-3 rounded-lg"):
            ui.label("Control Plane Simulation Runs (cp_simulation_runs)").classes(
                "text-subtitle1 font-semibold"
            )
            if sim_snap.runs:
                columns = [
                    TableColumn(name="simulation_id", label="Simulation ID", field="simulation_id"),
                    TableColumn(name="run_id", label="Run ID", field="run_id"),
                    TableColumn(name="status", label="Status", field="status"),
                    TableColumn(name="created_at", label="Created At", field="created_at"),
                    TableColumn(name="root_seed", label="Root Seed", field="root_seed"),
                    TableColumn(
                        name="iterations", label="Iterations", field="iterations", align="right"
                    ),
                    TableColumn(
                        name="horizon", label="Horizon (mos)", field="horizon", align="right"
                    ),
                    TableColumn(name="model_version", label="Model Ver", field="model_version"),
                ]
                rows = [
                    {
                        "simulation_id": r.simulation_id,
                        "run_id": r.run_id or "—",
                        "status": r.status or "UNKNOWN",
                        "created_at": r.created_at or "—",
                        "root_seed": r.root_seed or "—",
                        "iterations": r.iterations if r.iterations is not None else "—",
                        "horizon": r.horizon if r.horizon is not None else "—",
                        "model_version": r.model_implementation_version or "—",
                    }
                    for r in sim_snap.runs
                ]
                bounded_data_table(
                    columns=columns,
                    rows=rows,
                    row_key="simulation_id",
                    title="Simulation Run History (All Statuses)",
                )
            else:
                ui.label(
                    "No records found in Control Plane `cp_simulation_runs` (or Control Plane unavailable)."
                ).classes("text-caption").style("color: var(--studio-text-muted);")

        if sim_snap.rules_assumptions:
            readable = _extract_readable_rules(sim_snap.rules_assumptions)
            with ui.card().classes("studio-card w-full p-4 gap-3 rounded-lg"):
                with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                    ui.label("Active FinancialRules Assumptions").classes(
                        "text-subtitle1 font-semibold"
                    )
                    status_badge(f"{len(readable)} Parameters", tone=BadgeTone.INFO)
                if readable:
                    with ui.grid().classes(
                        "w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-2"
                    ):
                        for section, lbl, val in readable:
                            with (
                                ui.column()
                                .classes("gap-0 p-2.5 rounded")
                                .style(
                                    "background: rgba(15, 23, 42, 0.55); border: 1px solid rgba(148, 163, 184, 0.12);"
                                )
                            ):
                                ui.label(f"{section} · {lbl}").classes("text-caption").style(
                                    "color: var(--studio-text-muted);"
                                )
                                ui.label(val).classes("text-body2 font-semibold").style(
                                    "color: var(--studio-text-primary);"
                                )
                with ui.expansion("View Raw Rules & Mappings JSON (Advanced)", icon="code").classes(
                    "w-full"
                ):
                    ui.code(
                        json.dumps(sim_snap.rules_assumptions, indent=2, default=str),
                        language="json",
                    ).classes("w-full text-caption")


def render_fire_page(
    context: AppContext,
    *,
    cache: QueryCache | None = None,
    initial_tab: str = "trajectory",
    on_tab_change: Callable[[str], Any] | None = None,
) -> None:
    """Render the FIRE & Simulation Analytics page."""
    route = ROUTES_BY_ID["fire"]
    with ui.column().classes("w-full gap-4"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
            with ui.row().classes("items-center gap-3"):
                ui.icon(route.icon, size="28px").style("color: var(--studio-accent);")
                with ui.column().classes("gap-0"):
                    ui.label(route.label).classes("text-h5 font-semibold").style(
                        "color: var(--studio-text-primary);"
                    )
                    ui.label(route.description).classes("text-caption").style(
                        "color: var(--studio-text-muted);"
                    )

        if context.profile is None:
            render_domain_workspace(
                context=context,
                route=route,
                workspace_views=tuple(lbl for _, lbl, _ in _SUB_TABS),
                upstream_sources=(
                    "gold.wealth_fire_analytics",
                    "meta.m_Latest_Simulation",
                    "cp_simulation_runs",
                ),
            )
            return

        try:
            snap = FireAnalytics.summary(
                context.profile,
                context.period_context,
                run_id=context.effective_run_id,
                provenance=context.provenance,
                catalog_snapshot=context.catalog_snapshot,
                cache=cache,
            )
        except Exception as exc:
            error_state_panel("Unable to load FIRE Analytics", str(exc))
            return

        if snap.state == "locked":
            locked_warehouse_banner(
                snap.diagnostics[0] if snap.diagnostics else "DuckDB warehouse is locked."
            )
            return
        if snap.state == "no_run":
            error_state_panel(
                "No Completed PFETL Run",
                snap.diagnostics[0] if snap.diagnostics else "Run PFETL before viewing FIRE data.",
            )
            return
        if snap.state == "empty" or snap.latest is None:
            empty_state_panel(
                "No FIRE Analytics Available",
                snap.diagnostics[0] if snap.diagnostics else "No rows found for selected period.",
            )
            return

        sim_snap = FireAnalytics.simulation_history(
            context.profile,
            run_id=context.effective_run_id,
            provenance=context.provenance,
            catalog_snapshot=context.catalog_snapshot,
            cache=cache,
        )

        _render_kpi_grid(snap.kpis, snap.currency_id)

        valid_tab = initial_tab if any(t[0] == initial_tab for t in _SUB_TABS) else "trajectory"
        with ui.tabs().classes("w-full studio-subtabs") as tabs:
            for tab_id, tab_label, tab_icon in _SUB_TABS:
                ui.tab(tab_id, label=tab_label, icon=tab_icon)
        tabs.set_value(valid_tab)
        if on_tab_change is not None:
            tabs.on_value_change(lambda e: on_tab_change(str(e.value)))

        with ui.tab_panels(tabs, value=valid_tab).classes("w-full bg-transparent"):
            with ui.tab_panel("trajectory").classes("p-0 pt-3"):
                _render_trajectory_tab(snap)
            with ui.tab_panel("runway").classes("p-0 pt-3"):
                _render_runway_tab(snap)
            with ui.tab_panel("provenance").classes("p-0 pt-3"):
                _render_provenance_tab(sim_snap)
