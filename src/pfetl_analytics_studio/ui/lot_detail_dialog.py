"""Reusable 88-column Lot Detail modal dialog for Investments and Tax drill-downs.

Forbidden: Does not import `pfetl_analytics_studio.analytics` or `data/`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Protocol

from nicegui import ui

from pfetl_analytics_studio.ui.tables import TableColumn, bounded_data_table


class _LotSectionLike(Protocol):
    @property
    def title(self) -> str: ...
    @property
    def fields(self) -> Sequence[tuple[str, str, Any]]: ...


class _LotDetailLike(Protocol):
    @property
    def lot_id(self) -> str: ...
    @property
    def isin(self) -> str: ...
    @property
    def instrument_name(self) -> str: ...
    @property
    def closing_date(self) -> date: ...
    @property
    def sections(self) -> Sequence[_LotSectionLike]: ...
    @property
    def raw_columns(self) -> Mapping[str, Any]: ...


def _detail_stat(label: str, value: str) -> None:
    with ui.column().classes("gap-0"):
        ui.label(label).classes("text-caption").style("color: var(--studio-text-muted);")
        ui.label(value).classes("text-body2 font-semibold").style(
            "color: var(--studio-text-primary);"
        )


def open_lot_detail_dialog(detail: _LotDetailLike | None, lot_id: str) -> None:
    """Open the 88-column Lot Detail modal dialog for any lot from Investments or Tax."""
    with (
        ui.dialog() as dlg,
        ui.card()
        .classes("w-full max-w-4xl p-5")
        .style(
            "background: #0F172A; border: 1px solid rgba(148, 163, 184, 0.25); "
            "color: #F8FAFC; max-height: 85vh; overflow-y: auto;"
        ),
    ):
        if detail is None:
            ui.label(f"Lot {lot_id} not found in open lot analytics").classes(
                "text-subtitle1 font-semibold"
            )
            ui.button("Close", on_click=dlg.close).props("flat")
        else:
            with ui.row().classes("w-full items-center justify-between mb-3"):
                with ui.column().classes("gap-0"):
                    ui.label(detail.instrument_name or detail.isin).classes(
                        "text-h6 font-semibold"
                    ).style("color: var(--studio-text-primary);")
                    ui.label(
                        f"Lot: {detail.lot_id} · ISIN: {detail.isin} · Close: {detail.closing_date.isoformat()}"
                    ).classes("text-caption").style("color: var(--studio-text-muted);")
                with ui.row().classes("items-center gap-2"):
                    raw_toggle = ui.switch("Raw Source Fields", value=False).props("dense")
                    ui.button(icon="close", on_click=dlg.close).props("flat round dense")

            sections_box = ui.column().classes("w-full gap-3")

            def _render_sections(show_raw: bool = False) -> None:
                sections_box.clear()
                with sections_box:
                    if show_raw:
                        raw_cols = [
                            TableColumn(
                                name="col", label="Source Column", field="col", align="left"
                            ),
                            TableColumn(name="val", label="Raw Value", field="val", align="left"),
                        ]
                        raw_rows = [
                            {"col": k, "val": str(v) if v is not None else "—"}
                            for k, v in detail.raw_columns.items()
                        ]
                        bounded_data_table(
                            title=f"All Source Columns ({len(raw_rows)})",
                            columns=raw_cols,
                            rows=raw_rows,
                            pagination=20,
                        )
                    else:
                        for sec in detail.sections:
                            with (
                                ui.card()
                                .classes("w-full p-3")
                                .style(
                                    "background: rgba(15, 23, 42, 0.65); "
                                    "border: 1px solid rgba(148, 163, 184, 0.16);"
                                )
                            ):
                                ui.label(sec.title.upper()).classes(
                                    "text-caption font-semibold tracking-wider mb-2"
                                ).style("color: var(--studio-accent);")
                                with ui.grid(columns=3).classes("w-full gap-2"):
                                    for lbl, _raw_name, val in sec.fields:
                                        disp = (
                                            f"{val:,.4f}"
                                            if isinstance(val, float)
                                            else (str(val) if val is not None else "—")
                                        )
                                        _detail_stat(lbl, disp)

            raw_toggle.on_value_change(lambda e: _render_sections(bool(e.value)))
            _render_sections(False)
    dlg.open()
