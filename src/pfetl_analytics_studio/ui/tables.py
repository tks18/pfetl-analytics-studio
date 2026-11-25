"""Bounded tabular presentation components using dark-styled NiceGUI tables.

Owns
----
- `TableColumn` descriptor and `bounded_data_table()` paginated wrapper.

Does not own
------------
- SQL execution, DuckDB pagination cursors, or file exports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

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


def bounded_data_table(
    columns: Sequence[TableColumn],
    rows: Sequence[Mapping[str, Any]],
    row_key: str | None = None,
    max_rows: int = 500,
    pagination: int = 25,
    title: str | None = None,
    empty_message: str = "No records match the current selection.",
) -> ui.card:
    """Render a bounded, client-paginated dark data table safely without raw HTML injection.

    Parameters
    ----------
    columns : Sequence[TableColumn]
        Ordered column definitions to display.
    rows : Sequence[Mapping[str, Any]]
        Row mappings sliced cleanly to `max_rows`.
    row_key : str | None, optional
        Unique identifier field across rows, by default uses first column or synthetic index.
    max_rows : int, optional
        Hard maximum rows materialized in browser DOM, clamped to >= 1.
    pagination : int, optional
        Page size per table view, by default 25.
    title : str | None, optional
        Optional card title banner above table.
    empty_message : str, optional
        Message rendered when `rows` is empty.

    Returns
    -------
    ui.card
        Constructed container wrapping the table or an empty-state panel.
    """
    safe_limit = max(1, min(max_rows, 5000))
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

    card = ui.card().classes("studio-card w-full p-4 rounded-lg")
    with card:
        if title or total_rows > safe_limit:
            with ui.row().classes("w-full items-center justify-between mb-2"):
                if title:
                    ui.label(title).classes("text-sm font-semibold").style(
                        f"color: {StudioColor.TEXT}"
                    )
                if total_rows > safe_limit:
                    ui.label(f"Showing bounded preview: {safe_limit} of {total_rows} rows").classes(
                        "text-xs"
                    ).style(f"color: {StudioColor.WARNING}")

        ui.table(
            columns=col_dicts,
            rows=bounded_rows,
            row_key=resolved_key,
            pagination=pagination,
        ).classes("studio-table w-full").props("flat dense bordered dark")

    return card
