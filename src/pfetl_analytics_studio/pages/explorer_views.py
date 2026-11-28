"""Explorer Saved Views & Guarded Read-Only SQL workspace tabs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from pfetl_analytics_studio.analytics import (
    ChartRecommendation,
    ExplorerAnalytics,
    GuidedQuery,
    GuidedQueryResult,
    SqlQueryRequest,
    SqlQueryResult,
)
from pfetl_analytics_studio.data import CatalogSnapshot, QueryCache
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import FilterSet, PeriodRequest
from pfetl_analytics_studio.state import SavedViewDefinition, SavedViewKind, SqlDatasource
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    TableColumn,
    bounded_data_table,
    build_guided_explorer_figure,
    empty_state_panel,
    error_state_panel,
    format_metric_value,
    status_badge,
    trigger_file_download,
)


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def open_save_view_dialog(
    profile_id: str,
    *,
    kind: SavedViewKind,
    query: GuidedQuery | None = None,
    sql_text: str = "",
    datasource: SqlDatasource = SqlDatasource.DUCKDB,
    on_saved: Callable[[SavedViewDefinition], Any] | None = None,
) -> None:
    """Open modal dialog to persist a GuidedQuery or Read-Only SQL query as a Saved View."""
    dlg = ui.dialog()
    with dlg, ui.card().classes("studio-card w-[480px] max-w-full p-5 gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(
                "Save Guided View" if kind == SavedViewKind.GUIDED else "Save SQL View"
            ).classes("text-base font-semibold")
            ui.button(icon="close", on_click=dlg.close).props("flat round dense color=white")

        name_in = ui.input(label="View Name", placeholder="e.g. Monthly Equity Allocation").props(
            "dense outlined dark"
        )
        desc_in = ui.input(
            label="Description (Optional)", placeholder="Brief analytical purpose"
        ).props("dense outlined dark")

        def _commit() -> None:
            view_name = str(name_in.value or "").strip()
            view_desc = str(desc_in.value or "").strip()
            try:
                if kind == SavedViewKind.GUIDED:
                    assert query is not None
                    saved = ExplorerAnalytics.save_guided_view(
                        profile_id,
                        view_name,
                        query,
                        description=view_desc,
                    )
                else:
                    saved = ExplorerAnalytics.save_sql_view(
                        profile_id,
                        view_name,
                        sql_text,
                        datasource=datasource,
                        description=view_desc,
                    )
                dlg.close()
                ui.notify(f"Saved view '{saved.name}'", type="positive")
                if on_saved is not None:
                    on_saved(saved)
            except Exception as exc:
                ui.notify(f"Unable to save view: {exc}", type="negative")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat no-caps color=grey-5")
            ui.button("Save View", icon="bookmark_add", on_click=_commit).props(
                "no-caps color=primary"
            )
    dlg.open()


def _open_edit_view_dialog(
    view: SavedViewDefinition,
    on_updated: Callable[[], Any],
) -> None:
    """Open modal editor for renaming, recategorizing, or editing SQL of any saved/starter view."""
    dlg = ui.dialog()
    with dlg, ui.card().classes("studio-card w-[680px] max-w-full p-5 gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(f"Edit Saved View: {view.name}").classes("text-base font-semibold")
            ui.button(icon="close", on_click=dlg.close).props("flat round dense color=white")

        with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 gap-3"):
            name_in = ui.input(value=view.name, label="View Name").props("dense outlined dark")
            cat_in = ui.input(value=view.category, label="Category").props("dense outlined dark")

        desc_in = ui.input(value=view.description, label="Description").props("dense outlined dark")

        ds_sel = None
        sql_area = None
        if view.kind == SavedViewKind.SQL:
            ds_sel = ui.select(
                options={
                    SqlDatasource.DUCKDB.value: "DuckDB Warehouse",
                    SqlDatasource.CONTROL_PLANE.value: "Control Plane SQLite",
                },
                value=view.datasource.value,
                label="Datasource",
            ).props("dense outlined dark")
            sql_area = (
                ui.textarea(
                    label="Read-Only SQL (SELECT / WITH)",
                    value=view.sql_text,
                )
                .props("outlined dark rows=8")
                .classes("w-full font-mono text-xs")
            )

        def _save() -> None:
            try:
                ExplorerAnalytics.update_saved_view(
                    view.view_id,
                    name=str(name_in.value or ""),
                    description=str(desc_in.value or ""),
                    category=str(cat_in.value or "Custom"),
                    datasource=SqlDatasource(str(ds_sel.value)) if ds_sel is not None else None,
                    sql_text=str(sql_area.value or "") if sql_area is not None else None,
                )
                dlg.close()
                ui.notify("Saved view updated in studio.sqlite", type="positive")
                on_updated()
            except Exception as exc:
                ui.notify(f"Update failed: {exc}", type="negative")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat no-caps color=grey-5")
            ui.button("Save Changes", icon="save", on_click=_save).props("no-caps color=primary")
    dlg.open()


def _render_sql_result_panel(
    res: SqlQueryResult | None,
    error_msg: str | None,
) -> None:
    if error_msg:
        error_state_panel(title="SQL Validation / Execution Error", message=error_msg)
        return
    if res is None:
        empty_state_panel(
            title="Ready to Execute Read-Only SQL",
            message="Select a SQL view from studio.sqlite above or write a SELECT / WITH query.",
            icon="terminal",
        )
        return

    with (
        ui.card()
        .classes("w-full p-3")
        .style(f"background-color: {StudioColor.CARD}; border: 1px solid {StudioColor.BORDER};")
    ):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            with ui.row().classes("items-center gap-2"):
                status_badge(res.datasource.value.upper(), tone=BadgeTone.INFO)
                status_badge(f"{res.row_count:,} rows", tone=BadgeTone.SUCCESS)
                status_badge(f"{res.duration_ms:.1f} ms", tone=BadgeTone.NEUTRAL)
                if res.truncated:
                    status_badge(f"Capped at {res.limit_applied:,}", tone=BadgeTone.WARNING)
            status_badge("Read-Only Guard Active", tone=BadgeTone.SUCCESS, outline=True)
        for warn in res.warnings:
            ui.label(f"Note: {warn}").classes("text-xs").style(f"color: {StudioColor.WARNING}")

    if not res.rows:
        empty_state_panel(
            title="0 Rows Returned",
            message="The SQL query executed cleanly and returned no rows.",
            icon="table_rows",
        )
        return

    cols = [TableColumn(name=c, label=c, field=c) for c in res.columns]
    formatted_rows = [
        {c: ("" if r.get(c) is None else str(r.get(c))) for c in res.columns} for r in res.rows
    ]
    bounded_data_table(
        columns=cols,
        rows=formatted_rows,
        max_rows=500,
        pagination=25,
        title=f"SQL Query Results ({res.datasource.value})",
    )


def render_sql_tab(
    profile: PFETLProfile,
    run_id: str,
    cache: QueryCache,
    *,
    on_view_saved: Callable[[], Any] | None = None,
) -> Callable[[], Any]:
    """Render the Guarded Read-Only SQL workspace backed by studio.sqlite starter/saved views."""
    saved_sql_views = ExplorerAnalytics.list_saved_views(profile.profile_id, kind=SavedViewKind.SQL)
    first_view = saved_sql_views[0] if saved_sql_views else None

    state: dict[str, Any] = {
        "active_view_id": first_view.view_id if first_view else "",
        "datasource": first_view.datasource.value if first_view else SqlDatasource.DUCKDB.value,
        "sql": (
            first_view.sql_text
            if first_view
            else 'SELECT "MONTH_END_DATE", "Total_Net_Worth_Market" FROM "gold"."core_monthly_fact"'
        ),
        "limit": 500,
        "last_result": None,
        "error": None,
        "gen": 0,
    }

    def _run_sql() -> None:
        state["gen"] = int(state["gen"]) + 1
        captured_gen = int(state["gen"])
        req = SqlQueryRequest(
            sql=str(state["sql"]),
            datasource=SqlDatasource(str(state["datasource"])),
            limit=int(state["limit"]),
        )

        def _fetch_sql_sync() -> SqlQueryResult:
            return ExplorerAnalytics.execute_sql(profile, req, run_id=run_id, cache=cache)

        if not _has_running_loop():
            state["error"] = None
            try:
                state["last_result"] = _fetch_sql_sync()
            except Exception as exc:  # noqa: BLE001
                state["error"] = str(exc)
            _sql_results.refresh()
            return

        async def _run_sql_async() -> None:
            try:
                res = await run.io_bound(_fetch_sql_sync)
            except Exception as exc:  # noqa: BLE001
                if captured_gen == int(state["gen"]):
                    state["error"] = str(exc)
                    _sql_results.refresh()
                return
            if captured_gen == int(state["gen"]):
                state["error"] = None
                state["last_result"] = res
                _sql_results.refresh()

        ui.timer(0.01, _run_sql_async, once=True)

    def _load_view(vw: SavedViewDefinition) -> None:
        state["active_view_id"] = vw.view_id
        state["datasource"] = vw.datasource.value
        state["sql"] = vw.sql_text
        _sql_editor.refresh()
        _run_sql()

    def _update_active_view() -> None:
        vid = str(state["active_view_id"])
        if not vid:
            return
        try:
            updated = ExplorerAnalytics.update_saved_view(
                vid,
                datasource=SqlDatasource(str(state["datasource"])),
                sql_text=str(state["sql"]),
            )
            ui.notify(f"Updated '{updated.name}' in studio.sqlite", type="positive")
            _sql_editor.refresh()
            if on_view_saved is not None:
                on_view_saved()
        except Exception as exc:
            ui.notify(f"Update failed: {exc}", type="negative")

    def _restore_starters() -> None:
        added = ExplorerAnalytics.restore_default_sql_starters(profile.profile_id)
        ui.notify(
            f"Restored {added} starter SQL view(s)" if added else "All starter views present",
            type="info",
        )
        _sql_editor.refresh()
        if on_view_saved is not None:
            on_view_saved()

    async def _download_sql_csv() -> None:
        sql_str = str(state["sql"])
        ds = SqlDatasource(str(state["datasource"]))
        try:
            if not _has_running_loop():
                export_path, cnt, fname = ExplorerAnalytics.export_sql_csv(
                    profile, sql_str, datasource=ds
                )
            else:
                export_path, cnt, fname = await run.io_bound(
                    ExplorerAnalytics.export_sql_csv,
                    profile,
                    sql_str,
                    datasource=ds,
                )
            trigger_file_download(export_path, filename=fname, media_type="text/csv")
            ui.notify(f"Exported {cnt:,} rows to {fname}", type="positive")
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"SQL export failed: {exc}", type="negative")

    def _on_new_sql_saved(saved: SavedViewDefinition) -> None:
        state["active_view_id"] = saved.view_id
        _sql_editor.refresh()
        if on_view_saved is not None:
            on_view_saved()

    @ui.refreshable
    def _sql_editor() -> None:
        db_views = ExplorerAnalytics.list_saved_views(profile.profile_id, kind=SavedViewKind.SQL)
        active_vw = next((v for v in db_views if v.view_id == state["active_view_id"]), None)

        with (
            ui.card()
            .classes("w-full p-4 gap-3")
            .style(f"background-color: {StudioColor.CARD}; border: 1px solid {StudioColor.BORDER};")
        ):
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                ui.label(
                    "SQL Views & Starter Packs (Persisted & Editable in studio.sqlite)"
                ).classes("text-xs font-semibold uppercase tracking-wider").style(
                    f"color: {StudioColor.MUTED}"
                )
                ui.button(
                    "Restore Default Starters",
                    icon="restore",
                    on_click=_restore_starters,
                ).props("flat dense size=sm color=grey-4")

            if db_views:
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    for vw in db_views:
                        is_sel = vw.view_id == state["active_view_id"]
                        ui.button(
                            f"[{vw.category}] {vw.name}",
                            icon="terminal" if vw.is_starter else "bookmark",
                            on_click=lambda _e, v=vw: _load_view(v),
                        ).props(
                            f"{'unelevated' if is_sel else 'outline'} dense size=sm color=primary"
                        )

            with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                (
                    ui.select(
                        options={
                            SqlDatasource.DUCKDB.value: "DuckDB Warehouse (bronze/silver/gold/meta)",
                            SqlDatasource.CONTROL_PLANE.value: "Control Plane SQLite (cp_*)",
                        },
                        value=state["datasource"],
                        label="Datasource",
                        on_change=lambda e: state.__setitem__("datasource", str(e.value)),
                    )
                    .props("dense outlined dark")
                    .classes("w-80")
                )
                (
                    ui.number(
                        label="Row Limit (max 10,000)",
                        value=int(state["limit"]),
                        min=1,
                        max=10000,
                        on_change=lambda e: state.__setitem__("limit", int(e.value or 500)),
                    )
                    .props("dense outlined dark")
                    .classes("w-44")
                )
                if active_vw is not None:
                    status_badge(f"Editing: {active_vw.name}", tone=BadgeTone.INFO, outline=True)

            ui.textarea(
                label="Read-Only SQL (Single SELECT or WITH statement)",
                value=str(state["sql"]),
                on_change=lambda e: state.__setitem__("sql", str(e.value or "")),
            ).props("outlined dark rows=7").classes("w-full font-mono text-xs")

            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                ui.label(
                    "Enforces single SELECT/WITH + read-only DB connection; DDL/DML/ATTACH/COPY blocked."
                ).classes("text-xs").style(f"color: {StudioColor.DIM}")
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    if active_vw is not None:
                        ui.button(
                            "Update View",
                            icon="save",
                            on_click=_update_active_view,
                        ).props("outline color=positive")
                    ui.button(
                        "Save as New View",
                        icon="bookmark_add",
                        on_click=lambda: open_save_view_dialog(
                            profile.profile_id,
                            kind=SavedViewKind.SQL,
                            sql_text=str(state["sql"]),
                            datasource=SqlDatasource(str(state["datasource"])),
                            on_saved=_on_new_sql_saved,
                        ),
                    ).props("outline color=primary")
                    ui.button(
                        "Download Full CSV",
                        icon="download",
                        on_click=_download_sql_csv,
                    ).props("outline color=primary")
                    status_badge(
                        "Read-Only SQL · SELECT / WITH Only",
                        tone=BadgeTone.INFO,
                        outline=True,
                    )
                    ui.button("Run SQL", icon="play_arrow", on_click=_run_sql).props(
                        "color=primary"
                    )

    @ui.refreshable
    def _sql_results() -> None:
        _render_sql_result_panel(state["last_result"], state["error"])

    _sql_editor()
    _sql_results()
    return _sql_editor.refresh


def render_saved_views_tab(
    profile: PFETLProfile,
    run_id: str,
    catalog_snapshot: CatalogSnapshot | None,
    cache: QueryCache,
    *,
    global_period: PeriodRequest | None = None,
    global_filters: FilterSet | None = None,
    on_views_changed: Callable[[], Any] | None = None,
) -> Callable[[], Any]:
    """Render Saved Views browser (focus-safe search bar), inline editor, and replay panel."""
    v_state: dict[str, Any] = {
        "kind_filter": "all",
        "search": "",
        "replay_view": None,
        "replay_result": None,
        "replay_error": None,
        "gen": 0,
    }

    def _replay(view: SavedViewDefinition) -> None:
        v_state["gen"] = int(v_state["gen"]) + 1
        captured_gen = int(v_state["gen"])
        v_state["replay_view"] = view

        def _replay_sync() -> GuidedQueryResult | SqlQueryResult:
            return ExplorerAnalytics.replay_saved_view(
                profile,
                view,
                run_id=run_id,
                catalog_snapshot=catalog_snapshot,
                cache=cache,
                global_period=global_period,
                global_filters=global_filters,
            )

        if not _has_running_loop():
            v_state["replay_error"] = None
            try:
                v_state["replay_result"] = _replay_sync()
            except Exception as exc:  # noqa: BLE001
                v_state["replay_error"] = str(exc)
            _replay_panel.refresh()
            return

        async def _replay_async() -> None:
            try:
                res = await run.io_bound(_replay_sync)
            except Exception as exc:  # noqa: BLE001
                if captured_gen == int(v_state["gen"]):
                    v_state["replay_error"] = str(exc)
                    _replay_panel.refresh()
                return
            if captured_gen == int(v_state["gen"]):
                v_state["replay_error"] = None
                v_state["replay_result"] = res
                _replay_panel.refresh()

        ui.timer(0.01, _replay_async, once=True)

    def _delete_view(view: SavedViewDefinition) -> None:
        ExplorerAnalytics.delete_saved_view(view.view_id)
        if v_state["replay_view"] and v_state["replay_view"].view_id == view.view_id:
            v_state["replay_view"] = None
            v_state["replay_result"] = None
        ui.notify(f"Deleted view '{view.name}'", type="info")
        _views_list.refresh()
        _replay_panel.refresh()
        if on_views_changed is not None:
            on_views_changed()

    def _on_kind_change(val: Any) -> None:
        v_state["kind_filter"] = str(val)
        _views_list.refresh()

    def _on_search_change(val: Any) -> None:
        v_state["search"] = str(val or "")
        _views_list.refresh()

    # Persistent filter bar OUTSIDE @ui.refreshable so typing never drops input focus!
    with (
        ui.card()
        .classes("w-full p-3")
        .style(f"background-color: {StudioColor.CARD}; border: 1px solid {StudioColor.BORDER};")
    ):
        with ui.row().classes("w-full items-center justify-between gap-3 flex-wrap"):
            with ui.row().classes("items-center gap-3 flex-wrap"):
                ui.select(
                    options={"all": "All Kinds", "guided": "Guided Views", "sql": "SQL Views"},
                    value=v_state["kind_filter"],
                    label="View Kind",
                    on_change=lambda e: _on_kind_change(e.value),
                ).props("dense outlined dark").classes("w-44")
                ui.input(
                    label="Search saved views",
                    value=str(v_state["search"]),
                    on_change=lambda e: _on_search_change(e.value),
                ).props("dense outlined dark clearable debounce=250").classes("w-80")
            ui.button(
                "Restore Default Starters",
                icon="restore",
                on_click=lambda: (
                    ExplorerAnalytics.restore_default_sql_starters(profile.profile_id),
                    _views_list.refresh(),
                    on_views_changed() if on_views_changed else None,
                ),
            ).props("outline dense size=sm color=primary")

    @ui.refreshable
    def _views_list() -> None:
        kind_val = (
            SavedViewKind(str(v_state["kind_filter"])) if v_state["kind_filter"] != "all" else None
        )
        views = ExplorerAnalytics.list_saved_views(profile.profile_id, kind=kind_val)
        q = str(v_state["search"]).strip().lower()
        if q:
            views = [
                v
                for v in views
                if q in v.name.lower()
                or q in v.description.lower()
                or q in v.category.lower()
                or q in v.sql_text.lower()
            ]

        if not views:
            empty_state_panel(
                title="No Saved Views Match",
                message="Try clearing your search or save a query from Guided Analytics / Advanced SQL.",
                icon="bookmarks",
            )
            return

        with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"):
            for v in views:
                with (
                    ui.card()
                    .classes("studio-card p-4 gap-2")
                    .style(f"border: 1px solid {StudioColor.BORDER};")
                ):
                    with ui.row().classes("w-full items-center justify-between gap-2"):
                        ui.label(v.name).classes("text-sm font-semibold truncate")
                        with ui.row().classes("items-center gap-1 shrink-0"):
                            if v.is_starter:
                                status_badge("STARTER", tone=BadgeTone.NEUTRAL, outline=True)
                            status_badge(
                                v.kind.value.upper(),
                                tone=(
                                    BadgeTone.INFO
                                    if v.kind == SavedViewKind.GUIDED
                                    else BadgeTone.RECOVERY
                                ),
                            )
                    if v.description:
                        ui.label(v.description).classes("text-xs").style(
                            f"color: {StudioColor.MUTED}"
                        )
                    ui.label(
                        f"{v.category} · {v.datasource.value} · Updated {v.updated_at.strftime('%Y-%m-%d %H:%M')}"
                    ).classes("text-xs font-mono").style(f"color: {StudioColor.DIM}")
                    with ui.row().classes("w-full items-center justify-end gap-1 pt-1"):
                        ui.button(
                            "Edit",
                            icon="edit",
                            on_click=lambda _e, vw=v: _open_edit_view_dialog(
                                vw,
                                lambda: (
                                    _views_list.refresh(),
                                    on_views_changed() if on_views_changed else None,
                                ),
                            ),
                        ).props("flat dense size=sm color=grey-4")
                        ui.button(
                            "Delete",
                            icon="delete_outline",
                            on_click=lambda _e, vw=v: _delete_view(vw),
                        ).props("flat dense size=sm color=negative")
                        ui.button(
                            "Replay",
                            icon="play_arrow",
                            on_click=lambda _e, vw=v: _replay(vw),
                        ).props("outline dense size=sm color=primary")

    @ui.refreshable
    def _replay_panel() -> None:
        vw: SavedViewDefinition | None = v_state["replay_view"]
        if vw is None:
            return
        if v_state["replay_error"]:
            error_state_panel(
                title=f"Replay Failed: {vw.name}", message=str(v_state["replay_error"])
            )
            return
        res = v_state["replay_result"]
        if isinstance(res, SqlQueryResult):
            _render_sql_result_panel(res, None)
        elif isinstance(res, GuidedQueryResult):
            with (
                ui.card()
                .classes("w-full p-3")
                .style(
                    f"background-color: {StudioColor.CARD}; border: 1px solid {StudioColor.BORDER};"
                )
            ):
                ui.label(f"Replayed '{vw.name}': {res.semantic_summary}").classes(
                    "text-xs font-medium"
                )
            if res.rows and res.chart_suggestion.chart_type != ChartRecommendation.TABLE:
                fig = build_guided_explorer_figure(res)
                with (
                    ui.card()
                    .classes("w-full p-3")
                    .style(
                        f"background-color: {StudioColor.CARD}; border: 1px solid {StudioColor.BORDER};"
                    )
                ):
                    ui.plotly(fig).classes("w-full")
            metric_fmt_map = {m.id: m.format for m in res.metric_specs}
            cols = [
                TableColumn(
                    name=c,
                    label=c,
                    field=c,
                    align="right" if c in metric_fmt_map else "left",
                )
                for c in res.columns
            ]
            f_rows = [
                {
                    c: (
                        format_metric_value(float(r[c]), metric_fmt_map[c])
                        if c in metric_fmt_map and isinstance(r.get(c), (int, float))
                        else ("" if r.get(c) is None else str(r.get(c)))
                    )
                    for c in res.columns
                }
                for r in res.rows
            ]
            bounded_data_table(
                columns=cols,
                rows=f_rows,
                max_rows=500,
                pagination=25,
                title=f"Saved View Output: {vw.name}",
            )

    _views_list()
    _replay_panel()
    return _views_list.refresh
