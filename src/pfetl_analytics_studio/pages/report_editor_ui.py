"""Interactive Custom Report Editor UI panel and CRUD dialogs for `pages/reports.py`.

Owns
----
- `render_custom_editor_panel`: metadata, global period/filter defaults, grouped Add Block
  chooser, ordered block list (up/down/remove/per-block filter override), and validation.
- `open_rename_report_dialog` and `open_delete_report_dialog`.

Does not own
------------
- Direct SQLite schema logic (`state.py`) or block execution (`reports/service.py`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from nicegui import ui

from pfetl_analytics_studio.reports import (
    DEFAULT_REPORT_REGISTRY,
    ReportBlockDraft,
    ReportDefinition,
    ReportDefinitionDraft,
    ReportRegistry,
    ReportSourceKind,
    validate_report_draft,
)

if TYPE_CHECKING:
    from pfetl_analytics_studio.reports.editor import EditorBlockType
from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
    PeriodRequest,
)
from pfetl_analytics_studio.state import SavedReportRecord, SavedViewDefinition
from pfetl_analytics_studio.ui import BadgeTone, StudioColor, period_selector, status_badge

_BLOCK_CATEGORY_OPTIONS: dict[str, str] = {
    "kpi_group": "KPI Group (Registered)",
    "chart": "Chart (Registered)",
    "table": "Table (Registered)",
    "saved_view": "Saved View (Table Only)",
    "title": "Title Block",
    "subtitle": "Subtitle / Heading",
    "narrative": "Narrative Commentary",
    "provenance": "Provenance & Disclaimer",
    "divider": "Section Divider",
    "page_break": "Page Break (Slide Boundary)",
}

_FILTER_FIELD_OPTIONS: dict[str, str] = {
    "": "None",
    **{f.value: f.value.replace("_", " ").title() for f in FilterField},
}


def _make_new_block_draft(
    block_type: str,
    source_id: str,
    view_id: str,
    registry: ReportRegistry,
    saved_views_by_id: dict[str, SavedViewDefinition],
) -> ReportBlockDraft:
    """Create a fresh `ReportBlockDraft` for the Add Block chooser."""
    bid = f"blk_{uuid.uuid4().hex[:8]}"
    if block_type in ("kpi_group", "chart", "table"):
        src = registry.get_source(source_id)
        return ReportBlockDraft(
            block_id=bid,
            block_type=cast("EditorBlockType", block_type),
            source_id=source_id,
            title=src.title if src else "",
        )
    if block_type == "saved_view":
        sv = saved_views_by_id.get(view_id)
        return ReportBlockDraft(
            block_id=bid,
            block_type="saved_view",
            view_id=view_id,
            title=sv.name if sv else "Saved View",
        )
    if block_type == "title":
        return ReportBlockDraft(block_id=bid, block_type="title", title="Custom Report")
    if block_type == "subtitle":
        return ReportBlockDraft(block_id=bid, block_type="subtitle", title="Section Overview")
    if block_type == "narrative":
        return ReportBlockDraft(
            block_id=bid,
            block_type="narrative",
            heading="Commentary",
            body="Add presentation notes here.",
        )
    if block_type == "provenance":
        return ReportBlockDraft(block_id=bid, block_type="provenance")
    if block_type == "divider":
        return ReportBlockDraft(block_id=bid, block_type="divider", label="Section Break")
    return ReportBlockDraft(block_id=bid, block_type="page_break")


def _apply_single_filter(field_val: str, raw_val: str) -> FilterSet:
    """Build a single-spec `FilterSet` from UI field/value inputs."""
    clean_field = field_val.strip()
    clean_val = raw_val.strip()
    if not clean_field or not clean_val:
        return FilterSet()
    return FilterSet(
        filters=(
            FilterSpec(
                field=FilterField(clean_field),
                operator=FilterOperator.EQ,
                value=clean_val,
            ),
        )
    )


def _extract_single_filter(filter_set: FilterSet | None) -> tuple[str, str]:
    """Extract `(field_str, value_str)` from the first spec in `FilterSet`."""
    if filter_set is None or not filter_set.filters:
        return "", ""
    first = filter_set.filters[0]
    raw = first.value[0] if isinstance(first.value, (tuple, list)) else first.value
    return first.field.value, str(raw)


def _render_block_row(
    idx: int,
    total: int,
    blk: ReportBlockDraft,
    issues: tuple[Any, ...],
    on_move: Callable[[int, int], Any],
    on_remove: Callable[[int], Any],
    on_validate: Callable[[], Any],
) -> None:
    """Render a single editable block card inside the ordered block list."""
    has_blocking = any(i.blocking for i in issues)
    border_color = (
        StudioColor.ERROR
        if has_blocking
        else (StudioColor.WARNING if issues else "rgba(148, 163, 184, 0.2)")
    )
    with (
        ui.card()
        .classes("studio-card w-full p-3 rounded-lg gap-2")
        .style(f"border: 1px solid {border_color};")
    ):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.row().classes("items-center gap-2"):
                status_badge(f"#{idx + 1} · {blk.block_type.upper()}", tone=BadgeTone.INFO)
                if blk.source_id:
                    ui.label(blk.source_id).classes("text-xs font-mono").style(
                        f"color: {StudioColor.MUTED}"
                    )
                elif blk.view_id:
                    ui.label(f"view:{blk.view_id[:12]}").classes("text-xs font-mono").style(
                        f"color: {StudioColor.MUTED}"
                    )
                if blk.block_type == "page_break":
                    ui.label(
                        "Slide boundary (PDF/PPTX) — aim for 4-up KPIs or 1–2 charts per slide"
                    ).classes("text-xs italic").style(f"color: {StudioColor.MUTED}")
            with ui.row().classes("items-center gap-1"):
                if blk.block_type in ("kpi_group", "chart", "table", "saved_view"):

                    def _on_req_change(e: Any, b: ReportBlockDraft = blk) -> None:
                        b.required = bool(e.value)
                        on_validate()

                    ui.checkbox(
                        "Required",
                        value=blk.required,
                        on_change=_on_req_change,
                    ).props("dense")
                up_btn = ui.button(
                    icon="arrow_upward",
                    on_click=lambda _e, i=idx: on_move(i, -1),
                ).props("flat dense size=sm")
                if idx == 0:
                    up_btn.disable()
                down_btn = ui.button(
                    icon="arrow_downward",
                    on_click=lambda _e, i=idx: on_move(i, 1),
                ).props("flat dense size=sm")
                if idx >= total - 1:
                    down_btn.disable()
                ui.button(
                    icon="delete_outline",
                    on_click=lambda _e, i=idx: on_remove(i),
                ).props("flat dense size=sm color=negative")

        if blk.block_type in ("title", "subtitle", "kpi_group", "chart", "table", "saved_view"):
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                ui.input(
                    label="Title / Heading",
                    value=blk.title,
                    on_change=lambda e, b=blk: setattr(b, "title", str(e.value or "")),
                ).props("dense outlined").classes("flex-1 min-w-48")
                if blk.block_type in ("table", "saved_view"):
                    ui.number(
                        label="Max Rows",
                        value=blk.max_rows,
                        min=1,
                        max=5000,
                        on_change=lambda e, b=blk: setattr(
                            b, "max_rows", max(1, min(5000, int(e.value or 200)))
                        ),
                    ).props("dense outlined").classes("w-28")
        elif blk.block_type == "narrative":
            with ui.row().classes("w-full gap-2 flex-wrap"):
                ui.input(
                    label="Heading",
                    value=blk.heading,
                    on_change=lambda e, b=blk: setattr(b, "heading", str(e.value or "")),
                ).props("dense outlined").classes("w-56")
                ui.input(
                    label="Body",
                    value=blk.body,
                    on_change=lambda e, b=blk: setattr(b, "body", str(e.value or "")),
                ).props("dense outlined").classes("flex-1 min-w-64")
        elif blk.block_type == "divider":
            ui.input(
                label="Divider Label",
                value=blk.label,
                on_change=lambda e, b=blk: setattr(b, "label", str(e.value or "")),
            ).props("dense outlined").classes("w-64")

        if blk.block_type in ("kpi_group", "chart", "table", "saved_view"):
            f_field, f_val = _extract_single_filter(blk.filter_override)
            f_state = {"field": f_field, "value": f_val}

            def _on_blk_filter_field(e: Any) -> None:
                f_state["field"] = str(e.value or "")
                blk.filter_override = _apply_single_filter(f_state["field"], f_state["value"])

            def _on_blk_filter_val(e: Any) -> None:
                f_state["value"] = str(e.value or "")
                blk.filter_override = _apply_single_filter(f_state["field"], f_state["value"])

            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                ui.select(
                    options=_FILTER_FIELD_OPTIONS,
                    value=f_state["field"],
                    label="Block Filter Override",
                    on_change=_on_blk_filter_field,
                ).props("dense outlined").classes("w-52")
                ui.input(
                    label="Filter Value",
                    value=f_state["value"],
                    on_change=_on_blk_filter_val,
                ).props("dense outlined").classes("w-44")

        for issue in issues:
            color = StudioColor.ERROR if issue.blocking else StudioColor.WARNING
            with ui.column().classes("gap-0 pt-1"):
                ui.label(f"[{issue.code}] {issue.message}").classes("text-xs font-medium").style(
                    f"color: {color}"
                )
                if issue.remediation:
                    ui.label(f"Remediation: {issue.remediation}").classes("text-xs").style(
                        f"color: {StudioColor.MUTED}"
                    )


def render_custom_editor_panel(
    draft: ReportDefinitionDraft,
    *,
    saved_reports: list[SavedReportRecord],
    saved_views: list[SavedViewDefinition],
    on_save: Callable[[ReportDefinitionDraft], Any],
    on_preview: Callable[[ReportDefinition], Any],
    on_close: Callable[[], Any],
    registry: ReportRegistry = DEFAULT_REPORT_REGISTRY,
) -> None:
    """Render the interactive Custom Report Editor panel for `draft`."""
    catalog = registry.list_editor_catalog()
    saved_views_by_id = {sv.view_id: sv for sv in saved_views}
    existing_names_by_id = {r.report_id: r.name for r in saved_reports}

    add_state: dict[str, str] = {
        "type": "kpi_group",
        "source_id": catalog[ReportSourceKind.KPI_GROUP][0].source_id
        if catalog[ReportSourceKind.KPI_GROUP]
        else "",
        "view_id": saved_views[0].view_id if saved_views else "",
    }
    gf_field, gf_val = _extract_single_filter(draft.default_filters)
    gf_state = {"field": gf_field, "value": gf_val}

    with (
        ui.card()
        .classes("studio-card w-full p-4 rounded-xl gap-3")
        .style("border: 1px solid rgba(59, 130, 246, 0.35);")
    ):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            ui.label("Custom Report Editor").classes("text-base font-semibold").style(
                f"color: {StudioColor.TEXT}"
            )
            ui.button("Close Editor", icon="close", on_click=on_close).props("flat dense size=sm")

        def _on_name_change(e: Any) -> None:
            draft.name = str(e.value or "")
            _editor_body.refresh()

        def _on_gf_field(e: Any) -> None:
            gf_state["field"] = str(e.value or "")
            draft.default_filters = _apply_single_filter(gf_state["field"], gf_state["value"])

        def _on_gf_val(e: Any) -> None:
            gf_state["value"] = str(e.value or "")
            draft.default_filters = _apply_single_filter(gf_state["field"], gf_state["value"])

        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            ui.input(
                label="Report Name",
                value=draft.name,
                on_change=_on_name_change,
            ).props("dense outlined debounce=200").classes("w-64")
            ui.input(
                label="Description",
                value=draft.description,
                on_change=lambda e: setattr(draft, "description", str(e.value or "")),
            ).props("dense outlined").classes("flex-1 min-w-64")
            ui.select(
                options={"base": "Base Currency", "local": "Local Currency"},
                value=draft.currency_mode,
                label="Currency",
                on_change=lambda e: setattr(
                    draft, "currency_mode", "local" if e.value == "local" else "base"
                ),
            ).props("dense outlined").classes("w-36")

        @ui.refreshable
        def _editor_period_row() -> None:
            def _on_editor_period_change(new_req: PeriodRequest) -> None:
                draft.default_period = new_req
                _editor_period_row.refresh()

            period_selector(
                current_request=draft.default_period,
                on_request_change=_on_editor_period_change,
            )

        _editor_period_row()

        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            ui.select(
                options=_FILTER_FIELD_OPTIONS,
                value=gf_state["field"],
                label="Global Default Filter",
                on_change=_on_gf_field,
            ).props("dense outlined").classes("w-52")
            ui.input(
                label="Global Filter Value",
                value=gf_state["value"],
                on_change=_on_gf_val,
            ).props("dense outlined").classes("w-48")

        @ui.refreshable
        def _add_block_chooser() -> None:
            btype = add_state["type"]
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                ui.select(
                    options=_BLOCK_CATEGORY_OPTIONS,
                    value=btype,
                    label="Block Type",
                    on_change=lambda e: (
                        add_state.update({"type": str(e.value or "kpi_group")}),
                        _add_block_chooser.refresh(),
                    ),
                ).props("dense outlined").classes("w-56")

                if btype in ("kpi_group", "chart", "table"):
                    kind = ReportSourceKind(btype)
                    opts = {s.source_id: f"{s.title} ({s.source_id})" for s in catalog[kind]}
                    if add_state["source_id"] not in opts and opts:
                        add_state["source_id"] = next(iter(opts))
                    ui.select(
                        options=opts,
                        value=add_state["source_id"] if opts else None,
                        label="Registered Source",
                        on_change=lambda e: add_state.update({"source_id": str(e.value or "")}),
                    ).props("dense outlined").classes("flex-1 min-w-64")
                elif btype == "saved_view":
                    sv_opts = {sv.view_id: f"{sv.name} ({sv.kind.value})" for sv in saved_views}
                    if add_state["view_id"] not in sv_opts and sv_opts:
                        add_state["view_id"] = next(iter(sv_opts))
                    ui.select(
                        options=sv_opts,
                        value=add_state["view_id"] if sv_opts else None,
                        label="Saved Explorer View",
                        on_change=lambda e: add_state.update({"view_id": str(e.value or "")}),
                    ).props("dense outlined").classes("flex-1 min-w-64")

                def _handle_add() -> None:
                    blk = _make_new_block_draft(
                        add_state["type"],
                        add_state["source_id"],
                        add_state["view_id"],
                        registry,
                        saved_views_by_id,
                    )
                    draft.add_block(blk)
                    _editor_body.refresh()

                ui.button("Add Block", icon="add", on_click=_handle_add).props(
                    "color=primary dense"
                )

        _add_block_chooser()

        @ui.refreshable
        def _editor_body() -> None:
            validation = validate_report_draft(
                draft,
                registry=registry,
                saved_views_by_id=saved_views_by_id,
                existing_names_by_id=existing_names_by_id,
            )

            def _move(i: int, delta: int) -> None:
                if draft.move_block(i, delta):
                    _editor_body.refresh()

            def _remove(i: int) -> None:
                draft.remove_block(i)
                _editor_body.refresh()

            with ui.column().classes("w-full gap-2"):
                for idx, blk in enumerate(draft.blocks):
                    _render_block_row(
                        idx,
                        len(draft.blocks),
                        blk,
                        validation.issues_for_block(blk.block_id),
                        _move,
                        _remove,
                        _editor_body.refresh,
                    )

            report_issues = validation.issues_for_block("__report__")
            for issue in report_issues:
                color = StudioColor.ERROR if issue.blocking else StudioColor.WARNING
                ui.label(f"{issue.message} {issue.remediation}").classes(
                    "text-xs font-medium"
                ).style(f"color: {color}")

            with ui.row().classes("w-full items-center justify-end gap-2 pt-2"):
                ui.button(
                    "Preview Draft",
                    icon="play_arrow",
                    on_click=lambda _e: on_preview(draft.to_definition()),
                ).props("outline dense")
                save_btn = ui.button(
                    "Save Definition",
                    icon="save",
                    on_click=lambda _e: on_save(draft),
                ).props("color=primary dense")
                if validation.has_blocking_errors:
                    save_btn.disable()

        _editor_body()


def open_rename_report_dialog(
    record: SavedReportRecord,
    on_confirm: Callable[[str, str], Any],
) -> None:
    """Open a modal dialog to rename a saved custom report."""
    name_ref = {"value": record.name}
    with ui.dialog() as dlg, ui.card().classes("studio-card p-4 w-96 gap-3"):
        ui.label(f"Rename '{record.name}'").classes("text-base font-semibold").style(
            f"color: {StudioColor.TEXT}"
        )
        ui.input(
            label="New Report Name",
            value=record.name,
            on_change=lambda e: name_ref.update({"value": str(e.value or "")}),
        ).props("dense outlined autofocus").classes("w-full")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat dense")
            ui.button(
                "Rename",
                on_click=lambda _e: (dlg.close(), on_confirm(record.report_id, name_ref["value"])),
            ).props("color=primary dense")
    dlg.open()


def open_delete_report_dialog(
    record: SavedReportRecord,
    on_confirm: Callable[[str], Any],
) -> None:
    """Open a confirmation dialog before deleting a saved custom report."""
    with ui.dialog() as dlg, ui.card().classes("studio-card p-4 w-96 gap-3"):
        ui.label("Delete Custom Report?").classes("text-base font-semibold").style(
            f"color: {StudioColor.TEXT}"
        )
        ui.label(
            f"Remove '{record.name}' from saved reports? Historical run records in report_runs are preserved."
        ).classes("text-xs").style(f"color: {StudioColor.MUTED}")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat dense")
            ui.button(
                "Delete",
                on_click=lambda _e: (dlg.close(), on_confirm(record.report_id)),
            ).props("color=negative dense")
    dlg.open()
