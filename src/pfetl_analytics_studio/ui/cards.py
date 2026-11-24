"""Reusable status badges, KPI cards, and empty/error/locked/loading state panels.

Owns
----
- `BadgeTone` and `status_badge()` helper.
- `kpi_card()` with optional source/grain tooltip and drill navigation action.
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
    """Semantic status tone mapped to Core palette colors."""

    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"
    RECOVERY = "recovery"
    NEUTRAL = "neutral"


_TONE_TO_QUASAR: dict[BadgeTone, str] = {
    BadgeTone.SUCCESS: "positive",
    BadgeTone.WARNING: "warning",
    BadgeTone.ERROR: "negative",
    BadgeTone.INFO: "info",
    BadgeTone.RECOVERY: "accent",
    BadgeTone.NEUTRAL: "grey-7",
}


def status_badge(
    label: str,
    tone: BadgeTone = BadgeTone.NEUTRAL,
    outline: bool = False,
) -> ui.badge:
    """Render a consistent dark-theme Quasar status badge.

    Parameters
    ----------
    label : str
        Text rendered inside the badge.
    tone : BadgeTone, optional
        Semantic status tone controlling badge color.
    outline : bool, optional
        Whether to render outlined instead of solid fill.

    Returns
    -------
    ui.badge
        Constructed NiceGUI badge element.
    """
    color = _TONE_TO_QUASAR[tone]
    if outline:
        return ui.badge(label).props(f"outline color={color}")
    return ui.badge(label).props(f"color={color} text-color=white")


def kpi_card(
    title: str,
    value: str,
    subtitle: str | None = None,
    delta: str | None = None,
    delta_tone: BadgeTone = BadgeTone.NEUTRAL,
    tooltip: str | None = None,
    drill_route: str | None = None,
    icon: str | None = None,
) -> ui.card:
    """Render a standardized KPI card with optional source tooltip and drill link.

    Parameters
    ----------
    title : str
        Concise metric label.
    value : str
        Formatted headline metric string.
    subtitle : str | None, optional
        Supporting context (e.g., as-of date or source grain).
    delta : str | None, optional
        Comparison change indicator string.
    delta_tone : BadgeTone, optional
        Semantic color tone for comparison delta.
    tooltip : str | None, optional
        Concise hover tooltip explaining source table, grain, and meaning.
    drill_route : str | None, optional
        Target application route opened when drill button is clicked.
    icon : str | None, optional
        Optional Material icon name for card header.

    Returns
    -------
    ui.card
        Constructed NiceGUI card container.
    """
    card = ui.card().classes("studio-card p-4 rounded-lg shadow-sm justify-between")
    with card:
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                if icon:
                    ui.icon(icon, size="xs").style(f"color: {StudioColor.ACCENT}")
                ui.label(title).classes("text-xs font-semibold uppercase tracking-wide").style(
                    f"color: {StudioColor.MUTED}"
                )
            if delta:
                status_badge(delta, tone=delta_tone)

        ui.label(value).classes("text-2xl font-bold mt-2").style(f"color: {StudioColor.TEXT}")

        with ui.row().classes("items-center justify-between w-full mt-2"):
            if subtitle:
                ui.label(subtitle).classes("text-xs").style(f"color: {StudioColor.MUTED}")
            else:
                ui.label("")
            if drill_route:
                ui.button(
                    "Drill",
                    icon="open_in_new",
                    on_click=_nav_callback(drill_route),
                ).props("flat dense size=xs color=primary")

        if tooltip:
            ui.tooltip(tooltip)

    return card


def empty_state_panel(
    title: str,
    message: str,
    icon: str = "inbox",
    hint: str | None = None,
    action_label: str | None = None,
    action_target: str | None = None,
) -> ui.card:
    """Render a designed empty-state card with explanatory text and optional navigation action.

    Parameters
    ----------
    title : str
        Heading describing the empty state.
    message : str
        Clear explanation for why data or context is unavailable.
    icon : str, optional
        Material icon name.
    hint : str | None, optional
        Optional secondary hint or planned scope details.
    action_label : str | None, optional
        Optional navigation button label.
    action_target : str | None, optional
        Target route path triggered by the optional action button.

    Returns
    -------
    ui.card
        Constructed empty-state panel.
    """
    card = ui.card().classes("studio-card w-full p-6 rounded-lg")
    with card:
        with ui.row().classes("items-start gap-4 w-full"):
            ui.icon(icon, size="lg").style(f"color: {StudioColor.ACCENT}")
            with ui.column().classes("gap-1 flex-1"):
                ui.label(title).classes("text-lg font-semibold").style(f"color: {StudioColor.TEXT}")
                ui.label(message).classes("text-sm leading-relaxed").style(
                    f"color: {StudioColor.MUTED}"
                )
                if hint:
                    ui.label(hint).classes("text-xs mt-1").style(f"color: {StudioColor.DIM}")
                if action_label and action_target:
                    ui.button(
                        action_label,
                        on_click=_nav_callback(action_target),
                    ).props("outline size=sm color=primary").classes("mt-3")
    return card


def error_state_panel(
    title: str,
    message: str,
    details: Sequence[str] | None = None,
) -> ui.card:
    """Render a non-crashing error or compatibility block diagnostic panel.

    Parameters
    ----------
    title : str
        Summary title for the error/block condition.
    message : str
        Actionable explanation safe for end-user presentation.
    details : Sequence[str] | None, optional
        Bounded diagnostic bullet lines (rendered strictly as plain text).

    Returns
    -------
    ui.card
        Constructed diagnostic error panel.
    """
    card = (
        ui.card()
        .classes("w-full p-5 rounded-lg")
        .style(
            f"background-color: #1F1215; border: 1px solid {StudioColor.ERROR}; color: {StudioColor.TEXT}"
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
    """Render persistent non-modal banner when PFETL pipeline.lock is active on DuckDB.

    Parameters
    ----------
    message : str | None, optional
        Custom lock message overriding default PFETL lock explanation.
    operations_route : str, optional
        Route to Operations diagnostics page which stays accessible during lock.

    Returns
    -------
    ui.row
        Constructed persistent banner row.
    """
    text = message or (
        "PFETL Core is currently writing to DuckDB (pipeline.lock active). "
        "Warehouse analytical pages are paused, while Profile and Operations remain accessible."
    )
    banner = (
        ui.row()
        .classes("w-full items-center justify-between px-4 py-3 rounded-lg gap-3")
        .style(
            f"background-color: #261B07; border: 1px solid {StudioColor.WARNING}; color: {StudioColor.TEXT}"
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
        ).props("outline size=sm color=warning")
    return banner


def loading_state_card(
    title: str = "Loading analytical view...",
    subtitle: str | None = None,
) -> ui.card:
    """Render standardized loading indicator card with spinner and optional caption."""
    card = ui.card().classes("studio-card w-full p-6 rounded-lg")
    with card:
        with ui.row().classes("items-center gap-3"):
            ui.spinner("dots", size="md", color="primary")
            with ui.column().classes("gap-0"):
                ui.label(title).classes("text-sm font-medium").style(f"color: {StudioColor.TEXT}")
                if subtitle:
                    ui.label(subtitle).classes("text-xs").style(f"color: {StudioColor.MUTED}")
    return card
