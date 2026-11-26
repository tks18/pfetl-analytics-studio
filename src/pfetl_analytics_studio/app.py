"""NiceGUI application factory and unified shell layout for PFETL Analytics Studio."""

from __future__ import annotations

import os
import string
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nicegui import run, ui
from nicegui.events import ValueChangeEventArguments

from pfetl_analytics_studio import __version__
from pfetl_analytics_studio.context import (
    NAV_ROUTES,
    AppContext,
    AppSessionManager,
    NavRoute,
    RunSelectionMode,
)
from pfetl_analytics_studio.data import CompatibilityStatus, SurfaceLayer
from pfetl_analytics_studio.pages.explorer import render_explorer_page
from pfetl_analytics_studio.pages.fire import render_fire_page
from pfetl_analytics_studio.pages.household import render_household_page
from pfetl_analytics_studio.pages.investments import render_investments_page
from pfetl_analytics_studio.pages.operations import render_operations_page
from pfetl_analytics_studio.pages.overview import render_overview_page
from pfetl_analytics_studio.pages.reports import render_reports_page
from pfetl_analytics_studio.pages.tax import render_tax_page
from pfetl_analytics_studio.pages.wealth import render_wealth_page
from pfetl_analytics_studio.profile import (
    PFETLProfile,
    ProfileDriftStatus,
    ProfileLoadError,
    ProfileLoadErrorCode,
    ProfileLoadResult,
)
from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterSet,
    FilterSpec,
    PeriodRequest,
    format_period_label,
)
from pfetl_analytics_studio.state import RecentProfileRecord, StudioStateStore
from pfetl_analytics_studio.ui import (
    BadgeTone,
    StudioColor,
    apply_studio_theme,
    filter_chips_bar,
    locked_warehouse_banner,
    period_selector,
    status_badge,
)

__all__ = [
    "NAV_ROUTES",
    "AppContext",
    "AppSessionManager",
    "create_app",
    "list_available_picker_roots",
    "list_toml_picker_entries",
    "open_and_persist_profile",
    "pick_native_toml_file",
]

_LATEST_SENTINEL = "__latest__"

_PAGE_RENDERERS: dict[str, Callable[[AppContext], None]] = {
    "overview": render_overview_page,
    "household": render_household_page,
    "wealth": render_wealth_page,
    "investments": render_investments_page,
    "tax": render_tax_page,
    "fire": render_fire_page,
    "operations": render_operations_page,
    "explorer": render_explorer_page,
    "reports": render_reports_page,
}


def _strip_quotes(raw_path: str) -> str:
    """Trim whitespace and optional surrounding quotes from pasted file paths."""
    cleaned = raw_path.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ('"', "'"):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def open_and_persist_profile(
    session: AppSessionManager,
    settings_path: str,
    rules_path: str,
    label: str | None = None,
    *,
    state_db_path: Path | None = None,
    hydrate: bool = True,
) -> ProfileLoadResult:
    """Validate Settings/FinancialRules TOMLs, persist valid pairs, and update session context.

    Parameters
    ----------
    session : AppSessionManager
        Active session controller managing context and query cache invalidation.
    settings_path : str
        Filesystem path to PFETL Settings TOML.
    rules_path : str
        Filesystem path to PFETL FinancialRules TOML.
    label : str | None, optional
        Optional display label for the profile.
    state_db_path : Path | None, optional
        Optional override path for `StudioStateStore` (isolated tests).
    hydrate : bool, optional
        Whether to immediately inspect databases on load, by default True.

    Returns
    -------
    ProfileLoadResult
        Loaded `PFETLProfile` or typed `ProfileLoadError` without mutating recents on failure.
    """
    clean_settings = _strip_quotes(settings_path)
    clean_rules = _strip_quotes(rules_path)
    if not clean_settings:
        return ProfileLoadResult(
            error=ProfileLoadError(
                code=ProfileLoadErrorCode.FILE_NOT_FOUND,
                message="Settings TOML path is required.",
            )
        )
    if not clean_rules:
        return ProfileLoadResult(
            error=ProfileLoadError(
                code=ProfileLoadErrorCode.FILE_NOT_FOUND,
                message="FinancialRules TOML path is required.",
            )
        )

    clean_label = label.strip() if label and label.strip() else None
    result = PFETLProfile.load(clean_settings, clean_rules, label=clean_label)
    if not result.is_ok or result.profile is None:
        return result

    try:
        with StudioStateStore(db_path=state_db_path) as store:
            store.record_profile_opened(result.profile)
    except OSError:
        pass

    session.switch_profile(result.profile, hydrate=hydrate)
    return result


def list_toml_picker_entries(directory: Path) -> tuple[Path, list[Path], list[Path]]:
    """List sorted subdirectories and `.toml` files under `directory` safely.

    Parameters
    ----------
    directory : Path
        Target directory to inspect; falls back to `Path.cwd()` if unavailable.

    Returns
    -------
    tuple[Path, list[Path], list[Path]]
        Resolved `(current_dir, subdirectories, toml_files)`.
    """
    target = directory if directory.exists() and directory.is_dir() else Path.cwd()
    try:
        resolved = target.resolve()
        children = list(resolved.iterdir())
    except OSError:
        resolved = Path.cwd().resolve()
        try:
            children = list(resolved.iterdir())
        except OSError:
            return resolved, [], []

    dirs = sorted(
        (p for p in children if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.name.lower(),
    )
    toml_files = sorted(
        (p for p in children if p.is_file() and p.suffix.lower() == ".toml"),
        key=lambda p: p.name.lower(),
    )
    return resolved, dirs, toml_files


def pick_native_toml_file(title: str, initial_path: str = "") -> tuple[str | None, bool]:
    """Open the native OS file-chooser dialog filtering `.toml` files cleanly.

    Executes inside a short-lived Python subprocess so Tcl/Tk main-thread affinity stays
    isolated from the NiceGUI web server event loop and forces the dialog to foreground.

    Parameters
    ----------
    title : str
        Window title displayed in the OS file chooser.
    initial_path : str, optional
        Existing file or directory path used as the initial directory.

    Returns
    -------
    tuple[str | None, bool]
        `(selected_path_or_None, native_dialog_supported)`. When `native_dialog_supported`
        is False, callers may fall back cleanly to the in-app directory browser.
    """
    cleaned = _strip_quotes(initial_path)
    start_dir = Path.cwd()
    if cleaned:
        candidate = Path(cleaned)
        if candidate.is_dir():
            start_dir = candidate
        elif candidate.parent.exists() and candidate.parent.is_dir():
            start_dir = candidate.parent
    if not start_dir.exists() or not start_dir.is_dir():
        start_dir = Path.cwd()

    script = (
        "import sys, tkinter as tk; from tkinter import filedialog; "
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True); r.update(); "
        "p = filedialog.askopenfilename("
        "parent=r, title=sys.argv[1], initialdir=sys.argv[2], "
        "filetypes=[('TOML files', '*.toml'), ('All files', '*.*')]); "
        "r.destroy(); print(p or '')"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script, title, str(start_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        return None, False

    if proc.returncode != 0:
        return None, False
    chosen = proc.stdout.strip()
    return (chosen or None), True


def list_available_picker_roots() -> list[Path]:
    """Return accessible filesystem roots and common user starting directories."""
    roots: list[Path] = [Path.cwd().resolve(), Path.home().resolve()]
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                roots.append(drive)
    else:
        roots.append(Path("/"))

    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root not in seen:
            seen.add(root)
            deduped.append(root)
    return deduped


def _open_toml_browser_dialog(
    title: str,
    initial_path_str: str,
    on_select: Callable[[Path], Any],
) -> None:
    """Open an in-app browser modal for selecting a local `.toml` file."""
    cleaned = _strip_quotes(initial_path_str)
    start_candidate = Path(cleaned).parent if cleaned else Path.cwd()
    state: dict[str, Path] = {"dir": start_candidate}

    dialog = ui.dialog()
    with dialog, ui.card().classes("studio-card w-[640px] max-w-full p-5 rounded-lg gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(title).classes("text-base font-semibold")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense color=white")

        path_banner = (
            ui.label("").classes("text-xs font-mono break-all").style(f"color: {StudioColor.MUTED}")
        )
        list_container = (
            ui.column()
            .classes("w-full max-h-[340px] overflow-y-auto gap-1 p-2 rounded border")
            .style(f"background-color: {StudioColor.SIDEBAR}; border-color: {StudioColor.BORDER}")
        )

        def _make_dir_cb(target: Path) -> Callable[[], None]:
            def _open_dir() -> None:
                _render_dir(target)

            return _open_dir

        def _make_file_cb(chosen: Path) -> Callable[[], None]:
            def _pick() -> None:
                on_select(chosen)
                dialog.close()

            return _pick

        def _render_dir(target_dir: Path) -> None:
            resolved, dirs, tomls = list_toml_picker_entries(target_dir)
            state["dir"] = resolved
            path_banner.set_text(str(resolved))
            list_container.clear()
            with list_container:
                if resolved.parent != resolved:
                    with (
                        ui.row()
                        .classes("w-full items-center gap-2 px-2 py-1 rounded cursor-pointer")
                        .on("click", _make_dir_cb(resolved.parent))
                    ):
                        ui.icon("arrow_upward", size="xs").style(f"color: {StudioColor.MUTED}")
                        ui.label(".. (Parent Directory)").classes("text-xs font-mono")

                for sub in dirs:
                    with (
                        ui.row()
                        .classes("w-full items-center gap-2 px-2 py-1 rounded cursor-pointer")
                        .on("click", _make_dir_cb(sub))
                    ):
                        ui.icon("folder", size="xs").style(f"color: {StudioColor.WARNING}")
                        ui.label(sub.name).classes("text-xs font-mono")

                for toml in tomls:
                    with (
                        ui.row()
                        .classes("w-full items-center gap-2 px-2 py-1 rounded cursor-pointer")
                        .style(f"background-color: {StudioColor.SURFACE_ALT}")
                        .on("click", _make_file_cb(toml))
                    ):
                        ui.icon("description", size="xs").style(f"color: {StudioColor.ACCENT}")
                        ui.label(toml.name).classes("text-xs font-mono font-semibold")

                if not dirs and not tomls:
                    ui.label("No subdirectories or .toml files found here.").classes(
                        "text-xs italic p-2"
                    ).style(f"color: {StudioColor.MUTED}")

        with ui.row().classes("w-full items-center gap-1 flex-wrap"):
            for root in list_available_picker_roots():
                ui.button(str(root), on_click=_make_dir_cb(root)).props("outline dense size=xs")

        _render_dir(start_candidate)

    dialog.open()


def _render_profile_picker_form(
    session: AppSessionManager,
    ctx: AppContext,
    *,
    redirect_path: str = "/overview",
    parent_dialog: ui.dialog | None = None,
) -> None:
    """Render cleanly aligned Settings + FinancialRules inputs with native OS file chooser."""
    default_settings = str(ctx.profile.settings_path) if ctx.profile else ""
    default_rules = str(ctx.profile.rules_path) if ctx.profile else ""
    default_label = ctx.profile.label if ctx.profile else ""

    with ui.column().classes("w-full gap-4"):

        def _render_toml_field(
            field_label: str,
            picker_title: str,
            initial_value: str,
            placeholder: str,
            fallback_hint: Callable[[], str] | None = None,
        ) -> ui.input:
            with ui.column().classes("w-full gap-1"):
                ui.label(field_label).classes("text-xs font-semibold").style(
                    f"color: {StudioColor.MUTED}"
                )
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    field_input = (
                        ui.input(value=initial_value, placeholder=placeholder)
                        .props("dense outlined dark hide-bottom-space")
                        .classes("flex-1 font-mono text-xs")
                    )

                    async def _browse() -> None:
                        hint = str(field_input.value or (fallback_hint() if fallback_hint else ""))
                        picked, supported = await run.io_bound(
                            pick_native_toml_file,
                            picker_title,
                            hint,
                        )
                        if picked:
                            field_input.set_value(picked)
                        elif not supported:
                            _open_toml_browser_dialog(
                                picker_title,
                                hint,
                                lambda chosen: field_input.set_value(str(chosen)),
                            )

                    ui.button("Browse...", icon="launch", on_click=_browse).props(
                        "outline no-caps color=primary"
                    ).classes("h-[40px] shrink-0 px-3 text-xs")
            return field_input

        settings_input = _render_toml_field(
            field_label="Settings TOML",
            picker_title="Select PFETL Settings TOML",
            initial_value=default_settings,
            placeholder=r"Select or paste Settings TOML path (e.g. G:\...\settings.toml)",
        )
        rules_input = _render_toml_field(
            field_label="FinancialRules TOML",
            picker_title="Select PFETL FinancialRules TOML",
            initial_value=default_rules,
            placeholder=r"Select or paste FinancialRules TOML path (e.g. G:\...\financial_rules.toml)",
            fallback_hint=lambda: str(settings_input.value or ""),
        )

        with ui.column().classes("w-full gap-1"):
            ui.label("Profile Label (Optional)").classes("text-xs font-semibold").style(
                f"color: {StudioColor.MUTED}"
            )
            label_input = (
                ui.input(
                    value=default_label,
                    placeholder="Optional display name (defaults to settings filename)",
                )
                .props("dense outlined dark hide-bottom-space")
                .classes("w-full text-xs")
            )

        error_box = (
            ui.label("")
            .classes("w-full text-xs p-3 rounded hidden")
            .style(
                f"background-color: #1F1215; border: 1px solid {StudioColor.ERROR}; "
                f"color: {StudioColor.TEXT}"
            )
        )

        def _submit() -> None:
            res = open_and_persist_profile(
                session,
                settings_path=str(settings_input.value or ""),
                rules_path=str(rules_input.value or ""),
                label=str(label_input.value or ""),
            )
            if not res.is_ok or res.profile is None:
                msg = res.error.message if res.error else "Unable to load profile pair."
                error_box.set_text(msg)
                error_box.classes(remove="hidden")
                return

            if parent_dialog is not None:
                parent_dialog.close()
            ui.navigate.to(redirect_path)

        with (
            ui.row()
            .classes("w-full items-center justify-between pt-2 border-t")
            .style(f"border-color: {StudioColor.BORDER}")
        ):
            ui.label("Reads both TOMLs read-only; never modifies Core configuration.").classes(
                "text-xs"
            ).style(f"color: {StudioColor.DIM}")
            with ui.row().classes("items-center gap-2"):
                if parent_dialog is not None:
                    ui.button("Cancel", on_click=parent_dialog.close).props(
                        "flat no-caps color=grey-5"
                    )
                ui.button("Open Workspace", icon="check", on_click=_submit).props(
                    "no-caps color=primary"
                ).classes("px-4 h-[38px] font-semibold")


def _open_profile_picker_dialog(
    session: AppSessionManager,
    ctx: AppContext,
    active_path: str,
) -> None:
    """Open modal dialog for choosing or switching Settings + FinancialRules TOMLs."""
    dialog = ui.dialog()
    with dialog, ui.card().classes("studio-card w-[620px] max-w-full p-6 rounded-lg gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Open PFETL Profile Pair").classes("text-lg font-semibold")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense color=white")
        ui.label(
            "Select the exact Settings TOML and FinancialRules TOML files used by PFETL Core. "
            "Studio reads both files strictly read-only."
        ).classes("text-xs").style(f"color: {StudioColor.MUTED}")
        _render_profile_picker_form(
            session,
            ctx,
            redirect_path=active_path,
            parent_dialog=dialog,
        )
    dialog.open()


def _drift_tone(status: ProfileDriftStatus) -> BadgeTone:
    """Map profile drift classification to Core semantic badge tone."""
    if status == ProfileDriftStatus.MATCH:
        return BadgeTone.SUCCESS
    return BadgeTone.WARNING


def _compat_tone(status: CompatibilityStatus) -> BadgeTone:
    """Map compatibility classification to Core semantic badge tone."""
    return {
        CompatibilityStatus.COMPATIBLE: BadgeTone.SUCCESS,
        CompatibilityStatus.DEGRADED: BadgeTone.WARNING,
        CompatibilityStatus.INCOMPATIBLE: BadgeTone.ERROR,
    }[status]


def render_navigation_drawer(active_route_id: str, ctx: AppContext) -> ui.left_drawer:
    """Render sleek left sidebar with collapsible icon rail and subtle active accent bar."""
    drawer_props = ":width=240 :mini-width=64"
    if ctx.sidebar_collapsed:
        drawer_props += " mini"
    drawer = (
        ui.left_drawer(value=True, bordered=True)
        .props(drawer_props)
        .classes("studio-drawer-inner p-3.5 justify-between")
    )
    with drawer:
        with ui.column().classes("w-full gap-1"):
            ui.label("WORKSPACE").classes(
                "studio-drawer-hide text-xs font-semibold px-3 py-1.5 tracking-wider"
            ).style(f"color: {StudioColor.DIM}")
            for idx, route in enumerate(NAV_ROUTES):
                if idx == 6:
                    ui.element("div").classes("w-full my-2").style(
                        "height: 1px; background-color: rgba(255, 255, 255, 0.08);"
                    )
                    ui.label("OPERATIONS & DATA").classes(
                        "studio-drawer-hide text-xs font-semibold px-3 py-1 tracking-wider"
                    ).style(f"color: {StudioColor.DIM}")

                is_active = route.route_id == active_route_id
                bg = "rgba(59, 130, 246, 0.16)" if is_active else "transparent"
                fg = StudioColor.TEXT if is_active else StudioColor.MUTED
                border_left = (
                    f"3px solid {StudioColor.ACC_HOV}" if is_active else "3px solid transparent"
                )
                with (
                    ui.row()
                    .classes(
                        "studio-nav-item w-full items-center gap-3 px-3 py-2.5 "
                        "rounded-lg cursor-pointer no-wrap"
                    )
                    .style(f"background-color: {bg}; color: {fg}; border-left: {border_left};")
                    .on("click", lambda _, p=route.path: ui.navigate.to(p))
                ):
                    icon_color = StudioColor.ACC_HOV if is_active else StudioColor.DIM
                    ui.icon(route.icon, size="18px").style(f"color: {icon_color}")
                    ui.label(route.label).classes(
                        "studio-drawer-hide text-sm font-semibold whitespace-nowrap"
                        if is_active
                        else "studio-drawer-hide text-sm font-medium whitespace-nowrap"
                    )
                    ui.tooltip(route.label).props(
                        "anchor='center right' self='center left' :delay=200"
                    )

        with (
            ui.column()
            .classes("w-full gap-2 pt-3 border-t")
            .style("border-color: rgba(255, 255, 255, 0.08)")
        ):
            with ui.row().classes("studio-drawer-hide items-center justify-between w-full px-1"):
                status_badge("READ-ONLY", tone=BadgeTone.SUCCESS)
                ui.label(f"v{__version__}").classes("text-xs font-mono").style(
                    f"color: {StudioColor.MUTED}"
                )
            if ctx.profile is not None:
                ui.label(f"ID: {ctx.profile.profile_id}").classes(
                    "studio-drawer-hide text-xs font-mono px-1 truncate"
                ).style(f"color: {StudioColor.DIM}")
            with ui.row().classes("studio-drawer-mini-only w-full justify-center py-1"):
                ui.icon("verified_user", size="18px").style(f"color: {StudioColor.SUCCESS}")
                ui.tooltip(f"READ-ONLY • v{__version__}").props(
                    "anchor='center right' self='center left' :delay=200"
                )

    return drawer


def _open_profile_diagnostics_dialog(session: AppSessionManager, ctx: AppContext) -> None:
    """Open crisp modal showing active profile file paths, 82-surface counts, and recent switcher."""
    if ctx.profile is None:
        return
    dialog = ui.dialog()
    with dialog, ui.card().classes("studio-card w-[760px] max-w-full p-6 rounded-xl gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2.5"):
                ui.icon("verified_user", size="sm").style(f"color: {StudioColor.ACCENT}")
                ui.label("Profile Paths & Upstream Surface Coverage").classes(
                    "text-lg font-semibold"
                )
            ui.button(icon="close", on_click=dialog.close).props("flat round dense color=white")

        with ui.grid(columns=2).classes("w-full gap-3 text-xs"):
            for title, val in (
                ("Settings TOML", str(ctx.profile.settings_path)),
                ("FinancialRules TOML", str(ctx.profile.rules_path)),
                ("DuckDB Warehouse", str(ctx.profile.duckdb_path)),
                ("Control Plane SQLite", str(ctx.profile.control_plane_path)),
            ):
                with ui.column().classes("gap-0"):
                    ui.label(title).style(f"color: {StudioColor.MUTED}")
                    ui.label(val).classes("font-mono break-all")

        if ctx.compatibility is not None:
            with ui.grid(columns=5).classes("w-full gap-2 text-center"):
                for label, layer, target in (
                    ("Bronze", SurfaceLayer.BRONZE, 20),
                    ("Silver", SurfaceLayer.SILVER, 24),
                    ("Gold", SurfaceLayer.GOLD, 22),
                    ("Meta", SurfaceLayer.META, 6),
                    ("Control Plane", SurfaceLayer.CONTROL_PLANE, 10),
                ):
                    found = ctx.compatibility.layer_counts.get(layer, 0)
                    with (
                        ui.column()
                        .classes("items-center p-2 rounded-lg border")
                        .style(
                            f"background-color: {StudioColor.SIDEBAR}; "
                            f"border-color: rgba(148, 163, 184, 0.16);"
                        )
                    ):
                        ui.label(label).classes("text-xs").style(f"color: {StudioColor.MUTED}")
                        ui.label(f"{found}/{target}").classes("text-sm font-bold tabular-nums")

        _render_recent_profiles_card(session)
    dialog.open()


def _render_shell_header(
    session: AppSessionManager,
    ctx: AppContext,
    drawer: ui.left_drawer,
    active_path: str,
) -> None:
    """Render sleek single-row top bar with profile switcher, run selector, and health pills."""
    with ui.header().classes("items-center justify-between px-4 py-2 gap-3 no-wrap"):
        with ui.row().classes("items-center gap-2.5 min-w-0 no-wrap"):
            initial_icon = "menu" if ctx.sidebar_collapsed else "menu_open"
            toggle_btn = ui.button(icon=initial_icon).props("flat round dense color=white")

            def _toggle_sidebar() -> None:
                collapsed = not session.context.sidebar_collapsed
                session.set_sidebar_collapsed(collapsed)
                if collapsed:
                    drawer.props("mini")
                else:
                    drawer.props(remove="mini")
                toggle_btn.props(f"icon={'menu' if collapsed else 'menu_open'}")

            toggle_btn.on_click(_toggle_sidebar)
            with toggle_btn:
                ui.tooltip("Toggle sidebar rail")
            ui.icon("analytics", size="sm").style(f"color: {StudioColor.ACCENT}")
            ui.label("PFETL Analytics Studio").classes(
                "text-sm font-bold tracking-tight whitespace-nowrap"
            )

            if ctx.profile is not None:
                ui.button(
                    ctx.profile.label,
                    icon="swap_horiz",
                    on_click=lambda: _open_profile_picker_dialog(session, ctx, active_path),
                ).props("outline dense size=sm color=primary no-caps").classes("px-2.5 text-xs")
                if ctx.compatibility is not None:
                    cov_text = (
                        f"{ctx.compatibility.total_discovered}/"
                        f"{ctx.compatibility.total_expected} Surfaces"
                    )
                    ui.button(
                        cov_text,
                        icon="verified_user",
                        on_click=lambda: _open_profile_diagnostics_dialog(session, ctx),
                    ).props("flat dense size=sm no-caps color=positive").classes("text-xs px-2")
            else:
                status_badge("No Profile", tone=BadgeTone.WARNING, outline=True)
                ui.button(
                    "Open Profile",
                    icon="folder_open",
                    on_click=lambda: _open_profile_picker_dialog(session, ctx, active_path),
                ).props("outline dense size=sm color=primary no-caps")

        with ui.row().classes("items-center gap-2 shrink-0 no-wrap"):
            is_ops_route = active_path == "/operations"
            if ctx.profile is not None and ctx.available_runs and is_ops_route:
                run_opts: dict[str, str] = {_LATEST_SENTINEL: "Latest Successful Run"}
                for opt in ctx.available_runs:
                    run_opts[opt.run_id] = opt.label
                current_run_val = (
                    ctx.selected_run_id
                    if ctx.run_mode == RunSelectionMode.HISTORICAL_RUN
                    and ctx.selected_run_id in run_opts
                    else _LATEST_SENTINEL
                )

                def _on_run_change(evt: ValueChangeEventArguments[str]) -> None:
                    chosen = None if evt.value == _LATEST_SENTINEL else str(evt.value)
                    session.select_run(chosen)
                    ui.navigate.to(active_path)

                ui.select(
                    options=run_opts,
                    value=current_run_val,
                    label="Inspect Run (Ops)",
                    on_change=_on_run_change,
                ).props("dense filled dark options-dark").classes("studio-field min-w-[205px]")
            elif ctx.profile is not None and ctx.effective_run_id:
                status_badge(
                    f"Warehouse Run {ctx.effective_run_id[:8]}",
                    tone=BadgeTone.INFO,
                    tooltip="Analytical views are bound to the latest committed warehouse run.",
                )

            header_prov = (
                ctx.provenance if is_ops_route else (ctx.analytical_provenance or ctx.provenance)
            )
            if header_prov and header_prov.application_version:
                status_badge(
                    f"Core {header_prov.application_version}",
                    tone=BadgeTone.NEUTRAL,
                )
            if ctx.drift is not None:
                status_badge(ctx.drift.status.value, tone=_drift_tone(ctx.drift.status))
            if ctx.compatibility is not None:
                status_badge(
                    ctx.compatibility.status.value,
                    tone=_compat_tone(ctx.compatibility.status),
                )
            if ctx.profile is not None:

                def _on_refresh() -> None:
                    session.refresh()
                    ui.navigate.to(active_path)

                ui.button(icon="refresh", on_click=_on_refresh).props(
                    "flat round dense color=white"
                )


def _render_analytical_toolbar(
    session: AppSessionManager,
    ctx: AppContext,
    active_path: str,
) -> None:
    """Render interactive semantic period controls and global domain filter bar."""
    if ctx.profile is None:
        return

    def _on_period_request(req: PeriodRequest) -> None:
        session.set_period(req)
        ui.navigate.to(active_path)

    def _on_add_filter(spec: FilterSpec) -> None:
        updated = ctx.filters.without_field(spec.field).with_filter(spec)
        session.set_filters(updated)
        ui.navigate.to(active_path)

    def _on_remove_filter(field: FilterField) -> None:
        session.set_filters(ctx.filters.without_field(field))
        ui.navigate.to(active_path)

    def _on_clear_filters() -> None:
        session.set_filters(FilterSet())
        ui.navigate.to(active_path)

    badge_text = format_period_label(ctx.period_context) if ctx.period_context else None
    avail_fys = ctx.period_options.financial_years if ctx.period_options is not None else ()

    with ui.card().classes("studio-toolbar w-full px-4 py-3.5 rounded-2xl gap-3"):
        period_selector(
            current_request=ctx.period_request,
            on_request_change=_on_period_request,
            period_options=ctx.period_options,
            period_context=ctx.period_context,
            resolved_badge=badge_text,
        )
        filter_chips_bar(
            ctx.filters,
            on_remove=_on_remove_filter,
            on_clear=_on_clear_filters,
            on_add=_on_add_filter,
            available_fys=avail_fys,
            filter_options=ctx.filter_options,
            scope_hint="Filters dynamically slice Overview breakups, FY Tax, and drill views",
        )


def _load_recent_profiles() -> list[RecentProfileRecord]:
    """Read recently opened profiles from Studio state SQLite safely."""
    try:
        with StudioStateStore() as store:
            return store.get_recent_profiles(limit=5)
    except OSError:
        return []


def _render_recent_profiles_card(session: AppSessionManager) -> None:
    """Render the recent PFETL profile list card with one-click switching.

    Parameters
    ----------
    session : AppSessionManager
        Active application session manager.
    """
    recent = _load_recent_profiles()
    with ui.card().classes("studio-card w-full p-5 rounded-xl h-full gap-2"):
        ui.label("Recent PFETL Profiles").classes("text-base font-semibold")
        if not recent:
            ui.label("No recent profiles recorded in Studio state.").classes(
                "text-xs italic mt-2"
            ).style(f"color: {StudioColor.MUTED}")
            return

        with (
            ui.column().classes("w-full divide-y mt-1").style(f"border-color: {StudioColor.BORDER}")
        ):
            for item in recent:

                def _make_switch(rec: RecentProfileRecord) -> Callable[[], None]:
                    def _do_switch() -> None:
                        open_and_persist_profile(
                            session,
                            settings_path=str(rec.settings_path),
                            rules_path=str(rec.rules_path),
                            label=rec.label,
                        )
                        ui.navigate.to("/overview")

                    return _do_switch

                with ui.row().classes("w-full items-center justify-between py-2 gap-2"):
                    with ui.column().classes("gap-0 min-w-0 flex-1"):
                        ui.label(item.label).classes("text-sm font-medium truncate")
                        ui.label(f"{item.settings_path.name} | {item.rules_path.name}").classes(
                            "text-xs font-mono truncate"
                        ).style(f"color: {StudioColor.MUTED}")
                    with ui.row().classes("items-center gap-2 shrink-0"):
                        status_badge(item.profile_id[:8], outline=True)
                        ui.button("Open", on_click=_make_switch(item)).props(
                            "outline dense size=sm no-caps"
                        )


def _render_overview_context_cards(session: AppSessionManager, ctx: AppContext) -> None:
    """Render onboarding profile picker & recent profiles when no profile is active.

    Parameters
    ----------
    session : AppSessionManager
        Active application session manager.
    ctx : AppContext
        Resolved runtime application context.
    """
    if ctx.profile is not None:
        return

    with ui.grid(columns=1).classes("lg:grid-cols-12 w-full gap-4 items-stretch"):
        with ui.card().classes("studio-card w-full p-5 rounded-xl gap-4 h-full lg:col-span-7"):
            with ui.row().classes("items-center gap-3"):
                with (
                    ui.element("div")
                    .classes("w-9 h-9 rounded-lg flex items-center justify-center shrink-0")
                    .style(
                        f"background-color: {StudioColor.SURFACE_ALT}; "
                        f"border: 1px solid {StudioColor.BORDER};"
                    )
                ):
                    ui.icon("folder_open", size="20px").style(f"color: {StudioColor.ACCENT}")
                with ui.column().classes("gap-0"):
                    ui.label("Connect PFETL Profile").classes("text-base font-semibold")
                    ui.label(
                        "Select Settings TOML and FinancialRules TOML files to inspect."
                    ).classes("text-xs").style(f"color: {StudioColor.MUTED}")
            _render_profile_picker_form(session, ctx, redirect_path="/overview")
        with ui.column().classes("w-full h-full lg:col-span-5"):
            _render_recent_profiles_card(session)


def create_app(profile: PFETLProfile | None = None) -> None:
    """Register all nine application routes and render the unified NiceGUI shell.

    Parameters
    ----------
    profile : PFETLProfile | None, optional
        Active loaded PFETL profile, by default None.

    Notes
    -----
    Registers routes cleanly without opening DuckDB or SQLite during initialization.
    """
    session = AppSessionManager(initial_profile=profile)

    def _render_route(route: NavRoute, active_path: str, show_profile_cards: bool) -> None:
        apply_studio_theme()
        ctx = session.ensure_hydrated()
        if session.check_run_freshness():
            ctx = session.context
        drawer = render_navigation_drawer(route.route_id, ctx)
        _render_shell_header(session, ctx, drawer, active_path)

        with ui.column().classes("w-full max-w-[1440px] mx-auto px-6 py-5 gap-4"):
            if ctx.is_warehouse_locked:
                locked_warehouse_banner()

            _render_analytical_toolbar(session, ctx, active_path)

            if route.route_id == "overview":
                render_overview_page(ctx, cache=session.cache)
            elif route.route_id == "household":
                render_household_page(
                    ctx,
                    cache=session.cache,
                    initial_tab=ctx.active_tabs.get("household", "income"),
                    on_tab_change=lambda tab_id: session.set_active_tab("household", tab_id),
                )
            elif route.route_id == "wealth":
                render_wealth_page(ctx, cache=session.cache)
            elif route.route_id == "investments":
                render_investments_page(
                    ctx,
                    cache=session.cache,
                    initial_tab=ctx.active_tabs.get("investments", "overview"),
                    on_tab_change=lambda tab_id: session.set_active_tab("investments", tab_id),
                )
            elif route.route_id == "tax":
                render_tax_page(
                    ctx,
                    cache=session.cache,
                    initial_tab=ctx.active_tabs.get("tax", "overview"),
                    on_tab_change=lambda tab_id: session.set_active_tab("tax", tab_id),
                )
            else:
                renderer = _PAGE_RENDERERS[route.route_id]
                renderer(ctx)

            if show_profile_cards:
                _render_overview_context_cards(session, ctx)

    @ui.page("/")
    def index_route() -> None:
        """Render landing Overview route cleanly inside shared shell."""
        _render_route(NAV_ROUTES[0], "/", show_profile_cards=True)

    for route in NAV_ROUTES:

        def _make_handler(
            target_route: NavRoute = route,
        ) -> Callable[[], None]:
            show_cards = target_route.route_id == "overview"

            def _page_handler() -> None:
                _render_route(target_route, target_route.path, show_profile_cards=show_cards)

            return _page_handler

        ui.page(route.path)(_make_handler(route))
