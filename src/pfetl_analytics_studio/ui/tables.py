"""Bounded tabular presentation components using dark-styled NiceGUI tables.

Owns
----
- `TableColumn` descriptor and `bounded_data_table()` paginated wrapper with 500-row UI cap
  and full-dataset CSV export.

Does not own
------------
- SQL execution, DuckDB pagination cursors, or PDF/XLSX report orchestration.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from nicegui import ui

from pfetl_analytics_studio.ui.cards import empty_state_panel
from pfetl_analytics_studio.ui.theme import StudioColor

_NUMERIC_SORT_JS = """(a, b) => {
  const parse = (v) => {
    if (v === null || v === undefined) return Number.NEGATIVE_INFINITY;
    if (typeof v === 'number') return Number.isNaN(v) ? Number.NEGATIVE_INFINITY : v;
    const s = String(v).trim();
    if (!s || s === '—' || s === '-') return Number.NEGATIVE_INFINITY;
    const m = s.match(/^([+-]?)\\s*[₹$€£]?\\s*([+-]?[\\d,]+(?:\\.\\d+)?)\\s*(Cr|L|K|M|B|%|mos|x)?$/i);
    if (!m) {
      const n = Number(s.replace(/[^0-9.+-]/g, ''));
      return Number.isNaN(n) ? Number.NEGATIVE_INFINITY : n;
    }
    const sign = (m[1] === '-' || m[2].startsWith('-')) ? -1 : 1;
    const num = Math.abs(parseFloat(m[2].replace(/,/g, '')));
    const unit = (m[3] || '').toLowerCase();
    const mult = unit === 'cr' ? 1e7 : unit === 'l' ? 1e5 : unit === 'k' ? 1e3 : unit === 'm' ? 1e6 : unit === 'b' ? 1e9 : 1;
    return sign * num * mult;
  };
  return parse(a) - parse(b);
}"""


@dataclass(frozen=True)
class TableColumn:
    """Column specification mapping row dictionary fields to Quasar table columns."""

    name: str
    label: str
    field: str
    align: str = "left"
    sortable: bool = True
    numeric: bool | None = None

    def to_quasar_dict(self) -> dict[str, Any]:
        """Serialize column descriptor into Quasar `ui.table` format."""
        col: dict[str, Any] = {
            "name": self.name,
            "label": self.label,
            "field": self.field,
            "align": self.align,
            "sortable": self.sortable,
        }
        is_num = self.numeric if self.numeric is not None else (self.align == "right")
        if self.sortable and is_num:
            col[":sort"] = _NUMERIC_SORT_JS
        return col


def rows_to_csv_bytes(
    columns: Sequence[TableColumn],
    rows: Sequence[Mapping[str, Any]],
) -> bytes:
    """Serialize all `rows` (unbounded) into UTF-8 CSV bytes using `columns`."""
    buf = io.StringIO()
    fieldnames = [c.field for c in columns]
    headers = {c.field: c.label for c in columns}
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writerow(headers)
    for row in rows:
        writer.writerow({f: ("" if row.get(f) is None else str(row.get(f))) for f in fieldnames})
    return buf.getvalue().encode("utf-8")


def _default_export_filename(title: str | None) -> str:
    if not title:
        return "table_export.csv"
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return f"{slug[:48] or 'table_export'}.csv"


def bounded_data_table(
    columns: Sequence[TableColumn],
    rows: Sequence[Mapping[str, Any]],
    row_key: str | None = None,
    max_rows: int = 500,
    pagination: int = 25,
    title: str | None = None,
    empty_message: str = "No records match the current selection.",
    on_row_click: Callable[[dict[str, Any]], Any] | None = None,
    export_filename: str | None = None,
    on_export_full: Callable[[], Any] | None = None,
    show_export: bool = True,
) -> ui.card:
    """Render a 500-row-capped dark data table with optional full-dataset CSV export.

    Parameters
    ----------
    columns : Sequence[TableColumn]
        Ordered column definitions to display.
    rows : Sequence[Mapping[str, Any]]
        Row mappings sliced cleanly to `max_rows` (hard UI cap 500).
    row_key : str | None, optional
        Unique identifier field across rows, by default uses first column or synthetic index.
    max_rows : int, optional
        Hard maximum rows materialized in browser DOM, clamped to 1..500.
    pagination : int, optional
        Page size per table view, by default 25.
    title : str | None, optional
        Optional card title banner above table.
    empty_message : str, optional
        Message rendered when `rows` is empty.
    on_row_click : Callable[[dict[str, Any]], Any] | None, optional
        Optional callback invoked with the clicked row dictionary.
    export_filename : str | None, optional
        CSV download filename when using in-memory `rows`.
    on_export_full : Callable[[], Any] | None, optional
        Optional callback for server-side paginated/capped tables to export all DB rows.
    show_export : bool, optional
        Whether to display the Export CSV button in the table header, by default True.

    Returns
    -------
    ui.card
        Constructed container wrapping the table or an empty-state panel.
    """
    safe_limit = max(1, min(max_rows, 500))
    total_rows = len(rows)
    if total_rows == 0:
        return empty_state_panel(
            title=title or "No Rows Returned",
            message=empty_message,
            icon="table_rows",
        )

    bounded_rows: list[dict[str, Any]] = [dict(r) for r in list(rows)[:safe_limit]]
    resolved_key = row_key or (columns[0].field if columns else "__row_idx")
    if resolved_key == "__row_idx":
        for idx, row in enumerate(bounded_rows):
            row.setdefault("__row_idx", idx)

    col_dicts = [c.to_quasar_dict() for c in columns]

    def _export_in_memory() -> None:
        fname = export_filename or _default_export_filename(title)
        csv_bytes = rows_to_csv_bytes(columns, rows)
        ui.download(csv_bytes, filename=fname)
        ui.notify(f"Exported {total_rows:,} rows to {fname}", type="positive")

    card = ui.card().classes("studio-card w-full p-4 rounded-lg")
    with card:
        if title or total_rows > safe_limit or show_export:
            with ui.row().classes("w-full items-center justify-between gap-2 mb-2 flex-wrap"):
                with ui.row().classes("items-center gap-3 flex-wrap"):
                    if title:
                        ui.label(title).classes("text-sm font-semibold").style(
                            f"color: {StudioColor.TEXT}"
                        )
                    if total_rows > safe_limit:
                        ui.label(
                            f"Showing bounded preview: {safe_limit} of {total_rows} rows (Export CSV for full data)"
                        ).classes("text-xs").style(f"color: {StudioColor.WARNING}")
                if show_export:
                    ui.button(
                        "Export CSV",
                        icon="download",
                        on_click=on_export_full
                        if on_export_full is not None
                        else _export_in_memory,
                    ).props("flat dense size=sm color=primary")

        tbl = (
            ui.table(
                columns=col_dicts,
                rows=bounded_rows,
                row_key=resolved_key,
                pagination=pagination,
            )
            .classes("studio-table w-full")
            .props("flat dense bordered dark")
        )
        if on_row_click is not None:
            tbl.classes("cursor-pointer")

            def _handle_row_click(e: Any) -> None:
                raw_args = cast(list[Any], getattr(e, "args", []))
                if len(raw_args) > 1 and isinstance(raw_args[1], dict):
                    on_row_click(dict(cast(dict[str, Any], raw_args[1])))

            tbl.on("rowClick", _handle_row_click)

    return card
