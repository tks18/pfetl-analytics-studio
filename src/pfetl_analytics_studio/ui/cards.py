"""Reusable status badges, uniform KPI cards, and empty/error/locked/loading state panels.

Owns
----
- `BadgeTone` and `status_badge()` using inline high-contrast pill styling.
- `kpi_card()` with fixed header alignment, inline `breakups` mini-tiles, and dark tooltips.
- `empty_state_panel()`, `error_state_panel()`, `locked_warehouse_banner()`, and
  `loading_state_card()`.

Does not own
------------
- Financial KPI computations, database connections, or page routing state.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import StrEnum

from nicegui import ui

from pfetl_analytics_studio.ui.theme import StudioColor


def _nav_callback(target: str) -> Callable[[], None]:
    """Create a zero-arg navigation callback bound cleanly to `target`."""
    return lambda: ui.navigate.to(target)


class BadgeTone(StrEnum):
    """Semantic status tone mapped to high-contrast translucent pill styles."""

    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"
    RECOVERY = "recovery"
    NEUTRAL = "neutral"


_PILL_COLORS: dict[BadgeTone, tuple[str, str, str]] = {
    BadgeTone.SUCCESS: ("rgba(16, 185, 129, 0.18)", "#6EE7B7", "rgba(52, 211, 153, 0.45)"),
    BadgeTone.WARNING: ("rgba(245, 158, 11, 0.20)", "#FDE68A", "rgba(251, 191, 36, 0.50)"),
    BadgeTone.ERROR: ("rgba(239, 68, 68, 0.20)", "#FCA5A5", "rgba(248, 113, 113, 0.48)"),
    BadgeTone.INFO: ("rgba(59, 130, 246, 0.20)", "#93C5FD", "rgba(96, 165, 250, 0.48)"),
    BadgeTone.RECOVERY: ("rgba(168, 85, 247, 0.22)", "#E9D5FF", "rgba(192, 132, 252, 0.48)"),
    BadgeTone.NEUTRAL: ("rgba(148, 163, 184, 0.18)", "#F1F5F9", "rgba(203, 213, 225, 0.32)"),
}


def status_badge(
    label: str,
    tone: BadgeTone = BadgeTone.NEUTRAL,
    outline: bool = False,
    tooltip: str | None = None,
) -> ui.badge:
    """Render a high-contrast dark-theme status pill with optional rich dark tooltip.

    Parameters
    ----------
    label : str
        Text rendered inside the pill.
    tone : BadgeTone, optional
        Semantic status tone controlling translucent background, border, and crisp text color.
    outline : bool, optional
        Preserved for caller compatibility; bordered pill styling always applies.
    tooltip : str | None, optional
        Optional multi-line tooltip attached directly to the status pill.

    Returns
    -------
    ui.badge
        Constructed NiceGUI badge element.
    """
    _ = outline
    bg, fg, border = _PILL_COLORS[tone]
    style_str = (
        f"background-color: {bg} !important; "
        f"color: {fg} !important; "
        f"border: 1px solid {border} !important; "
        "border-radius: 9999px !important; "
        "padding: 3px 10px !important; "
        "font-size: 11.5px !important; "
        "font-weight: 600 !important; "
        "line-height: 1.3 !important;"
    )
    badge = ui.badge(label).classes("whitespace-nowrap").style(style_str)
    if tooltip:
        with badge:
            ui.tooltip(tooltip).classes("studio-tooltip").props("delay=200")
    return badge


def kpi_card(
    title: str,
    value: str,
    subtitle: str | None = None,
    delta: str | None = None,
    delta_tone: BadgeTone = BadgeTone.NEUTRAL,
    tooltip: str | None = None,
    drill_route: str | None = None,
    icon: str | None = None,
    *,
    available: bool = True,
    compact: bool = False,
    status_pill: str | None = None,
    status_pill_tone: BadgeTone = BadgeTone.WARNING,
    progress_fraction: float | None = None,
    breakups: Sequence[tuple[str, str]] = (),
) -> ui.card:
    """Render a uniform dark KPI card with fixed header alignment, breakups, and dark tooltip.

    Parameters
    ----------
    title : str
        Metric title rendered cleanly beside the uniform icon badge.
    value : str
        Formatted headline metric string (or `'Unavailable'`).
    subtitle : str | None, optional
        Supporting context displayed in full without truncation.
    delta : str | None, optional
        Comparison change pill displayed cleanly next to the headline value.
    delta_tone : BadgeTone, optional
        Semantic color tone for `delta` pill.
    tooltip : str | None, optional
        Concise multi-line hover tooltip attached to a sleek header info icon.
    drill_route : str | None, optional
        Target application route opened when drill button is clicked.
    icon : str | None, optional
        Material icon name for the uniform 28x28 header badge.
    available : bool, optional
        Whether underlying metric data exists; dims value cleanly when False.
    compact : bool, optional
        Slightly tighter sizing for secondary telemetry cards while keeping identical alignment.
    status_pill : str | None, optional
        Optional inline status pill (e.g., `'Incomplete Estimate'`).
    status_pill_tone : BadgeTone, optional
        Semantic tone for `status_pill`.
    progress_fraction : float | None, optional
        Optional `0..1` completion ratio rendered as a sleek progress bar (e.g., FI coverage).
    breakups : Sequence[tuple[str, str]], optional
        Optional inline `(label, formatted_value)` sub-metric pills displayed inside the card.

    Returns
    -------
    ui.card
        Constructed NiceGUI card container.
    """
    pad_cls = "p-4" if compact else "p-5"
    val_cls = (
        "text-2xl font-bold tabular-nums leading-tight"
        if compact
        else "text-3xl font-bold tabular-nums leading-tight"
    )
    val_color = StudioColor.TEXT if available else StudioColor.DIM

    card = ui.card().classes(f"studio-card {pad_cls} rounded-2xl justify-between h-full gap-2.5")
    with card:
        with ui.row().classes("items-center justify-between w-full gap-2 no-wrap"):
            with ui.row().classes("items-center gap-2.5 min-w-0 no-wrap"):
                with (
                    ui.element("div")
                    .classes("w-7 h-7 rounded-lg flex items-center justify-center shrink-0")
                    .style("background-color: rgba(59, 130, 246, 0.16);")
                ):
                    ui.icon(icon or "insights", size="16px").style(f"color: {StudioColor.ACC_HOV}")
                ui.label(title).classes("text-xs font-semibold uppercase tracking-wider").style(
                    f"color: {StudioColor.MUTED}"
                )
            if tooltip:
                with (
                    ui.icon("info_outline", size="16px")
                    .classes("cursor-help shrink-0")
                    .style(f"color: {StudioColor.DIM}")
                ):
                    ui.tooltip(tooltip).classes("studio-tooltip").props("delay=200")

        with ui.row().classes("items-baseline justify-between w-full gap-2 flex-wrap mt-1"):
            ui.label(value).classes(val_cls).style(f"color: {val_color}; letter-spacing: -0.025em;")
            if status_pill:
                status_badge(status_pill, tone=status_pill_tone)
            elif delta:
                status_badge(delta, tone=delta_tone)

        if progress_fraction is not None and available:
            clamped = max(0.0, min(float(progress_fraction), 1.0))
            with (
                ui.element("div")
                .classes("w-full h-1.5 rounded-full overflow-hidden mt-1")
                .style("background-color: rgba(255, 255, 255, 0.08);")
            ):
                ui.element("div").classes("h-full rounded-full").style(
                    f"width: {clamped * 100:.1f}%; "
                    f"background: linear-gradient(90deg, {StudioColor.ACCENT}, {StudioColor.RECOVERY});"
                )

        if breakups:
            with ui.row().classes("w-full items-center gap-1.5 flex-wrap mt-1"):
                for b_label, b_val in breakups:
                    with (
                        ui.row()
                        .classes("items-center gap-1 px-2 py-1 rounded-md text-xs")
                        .style(
                            "background-color: rgba(255, 255, 255, 0.04); "
                            "border: 1px solid rgba(255, 255, 255, 0.07);"
                        )
                    ):
                        ui.label(f"{b_label}:").style(f"color: {StudioColor.DIM}")
                        ui.label(b_val).classes("font-semibold tabular-nums").style(
                            f"color: {StudioColor.TEXT}"
                        )

        with (
            ui.row()
            .classes("items-center justify-between w-full mt-auto pt-2.5 gap-2 no-wrap")
            .style("border-top: 1px solid rgba(255, 255, 255, 0.08)")
        ):
            if subtitle:
                ui.label(subtitle).classes("text-xs leading-snug flex-1").style(
                    f"color: {StudioColor.MUTED}"
                )
            else:
                ui.label("").classes("flex-1")
            if drill_route:
                ui.button(
                    "Explore",
                    icon="north_east",
                    on_click=_nav_callback(drill_route),
                ).props("flat dense size=sm no-caps").style(
                    f"color: {StudioColor.ACC_HOV}"
                ).classes("shrink-0 px-1.5 text-xs font-semibold")

    return card


def empty_state_panel(
    title: str,
    message: str,
    icon: str = "inbox",
    hint: str | None = None,
    action_label: str | None = None,
    action_target: str | None = None,
) -> ui.card:
    """Render a designed empty-state card with clear typography and optional navigation action."""
    card = ui.card().classes("studio-card w-full p-6 rounded-2xl")
    with card:
        with ui.row().classes("items-start gap-4 w-full"):
            with (
                ui.element("div")
                .classes("w-11 h-11 rounded-xl flex items-center justify-center shrink-0")
                .style(
                    "background-color: rgba(59, 130, 246, 0.16); "
                    "border: 1px solid rgba(96, 165, 250, 0.30);"
                )
            ):
                ui.icon(icon, size="24px").style(f"color: {StudioColor.ACC_HOV}")
            with ui.column().classes("gap-1 flex-1"):
                ui.label(title).classes("text-base font-semibold").style(
                    f"color: {StudioColor.TEXT}"
                )
                ui.label(message).classes("text-sm leading-relaxed").style(
                    f"color: {StudioColor.MUTED}"
                )
                if hint:
                    ui.label(hint).classes("text-xs font-mono mt-1").style(
                        f"color: {StudioColor.DIM}"
                    )
                if action_label and action_target:
                    ui.button(
                        action_label,
                        on_click=_nav_callback(action_target),
                    ).props("outline size=sm no-caps color=primary").classes("mt-3")
    return card


def error_state_panel(
    title: str,
    message: str,
    details: Sequence[str] | None = None,
) -> ui.card:
    """Render a non-crashing error or compatibility block diagnostic panel."""
    card = (
        ui.card()
        .classes("w-full p-5 rounded-2xl")
        .style(
            f"background-color: rgba(127, 29, 29, 0.25); "
            f"border: 1px solid rgba(248, 113, 113, 0.45); color: {StudioColor.TEXT}"
        )
    )
    with card:
        with ui.row().classes("items-center gap-3"):
            ui.icon("error_outline", size="md").style(f"color: {StudioColor.ERROR}")
            ui.label(title).classes("text-base font-semibold")
        ui.label(message).classes("text-sm mt-1").style(f"color: {StudioColor.MUTED}")
        if details:
            with ui.column().classes("gap-1 mt-3 pl-2 text-xs font-mono"):
                for item in list(details)[:10]:
                    ui.label(f"• {item}").style(f"color: {StudioColor.TEXT}")
    return card


def locked_warehouse_banner(
    message: str | None = None,
    operations_route: str = "/operations",
) -> ui.row:
    """Render persistent non-modal banner when PFETL pipeline.lock is active on DuckDB."""
    text = message or (
        "PFETL Core is currently writing to DuckDB (pipeline.lock active). "
        "Warehouse analytical pages are paused, while Profile and Operations remain accessible."
    )
    banner = (
        ui.row()
        .classes("w-full items-center justify-between px-4 py-3 rounded-2xl gap-3")
        .style(
            f"background-color: rgba(120, 53, 15, 0.30); "
            f"border: 1px solid rgba(251, 191, 36, 0.45); color: {StudioColor.TEXT}"
        )
    )
    with banner:
        with ui.row().classes("items-center gap-3 flex-1"):
            ui.icon("lock", size="sm").style(f"color: {StudioColor.WARNING}")
            ui.label(text).classes("text-sm font-medium")
        ui.button(
            "Open Operations",
            icon="engineering",
            on_click=_nav_callback(operations_route),
        ).props("outline size=sm no-caps color=warning")
    return banner


def loading_state_card(
    title: str = "Loading analytical view...",
    subtitle: str | None = None,
) -> ui.card:
    """Render standardized loading indicator card with spinner and optional caption."""
    card = ui.card().classes("studio-card w-full p-6 rounded-2xl")
    with card:
        with ui.row().classes("items-center gap-3"):
            ui.spinner("dots", size="md", color="primary")
            with ui.column().classes("gap-0"):
                ui.label(title).classes("text-sm font-medium").style(f"color: {StudioColor.TEXT}")
                if subtitle:
                    ui.label(subtitle).classes("text-xs").style(f"color: {StudioColor.MUTED}")
    return card
