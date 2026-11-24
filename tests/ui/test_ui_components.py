"""Tests for reusable presentation widgets, bounded tables, theme, and UI dependency rules."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from nicegui import Client
from nicegui.page import page

from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
    PeriodPreset,
)
from pfetl_analytics_studio.ui import (
    BadgeTone,
    TableColumn,
    apply_studio_theme,
    bounded_data_table,
    empty_state_panel,
    error_state_panel,
    filter_chips_bar,
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
            fset = FilterSet(
                filters=(
                    FilterSpec(field=FilterField.FY, operator=FilterOperator.EQ, value="2026-27"),
                )
            )
            filter_chips_bar(fset, on_remove=lambda _f: None, on_clear=lambda: None)

        rendered = " ".join(_collect_client_texts(client))
        assert "Showing bounded preview: 3 of 10 rows" in rendered
        assert "Empty Table" in rendered
        assert "Market Net Worth" in rendered
    finally:
        client.delete()


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
