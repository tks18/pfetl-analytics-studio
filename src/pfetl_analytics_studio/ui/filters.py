"""Reusable period selector and domain filter chip controls.

Owns
----
- `period_selector()` wrapping `PeriodPreset` and `ComparisonMode` dropdowns cleanly.
- `filter_chips_bar()` rendering normalized `FilterSet` predicates as removable chips.

Does not own
------------
- Calendar database resolution (`semantic/periods.py`) or analytics services.
"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui
from nicegui.events import ValueChangeEventArguments

from pfetl_analytics_studio.semantic.filters import FilterField, FilterSet
from pfetl_analytics_studio.semantic.periods import PRESET_LABELS, ComparisonMode, PeriodPreset
from pfetl_analytics_studio.ui.cards import BadgeTone, status_badge
from pfetl_analytics_studio.ui.theme import StudioColor

_COMPARISON_LABELS: dict[ComparisonMode, str] = {
    ComparisonMode.NONE: "No Comparison",
    ComparisonMode.PREVIOUS_PERIOD: "Previous Period",
    ComparisonMode.SAME_PERIOD_PREVIOUS_YEAR: "Same Period Prior Year",
    ComparisonMode.CUSTOM: "Custom Comparison",
}


def period_selector(
    current_preset: PeriodPreset,
    on_preset_change: Callable[[PeriodPreset], None],
    current_comparison: ComparisonMode = ComparisonMode.NONE,
    on_comparison_change: Callable[[ComparisonMode], None] | None = None,
    resolved_badge: str | None = None,
) -> ui.row:
    """Render standardized period preset and optional comparison selectors.

    Parameters
    ----------
    current_preset : PeriodPreset
        Currently selected period preset key.
    on_preset_change : Callable[[PeriodPreset], None]
        Callback invoked when user chooses a new preset.
    current_comparison : ComparisonMode, optional
        Currently selected comparison mode.
    on_comparison_change : Callable[[ComparisonMode], None] | None, optional
        Optional callback invoked when comparison mode changes.
    resolved_badge : str | None, optional
        Resolved date window label rendered next to the dropdown.

    Returns
    -------
    ui.row
        Constructed control bar row.
    """
    preset_options: dict[str, str] = {p.value: label for p, label in PRESET_LABELS.items()}

    def _handle_preset(evt: ValueChangeEventArguments[str]) -> None:
        if evt.value:
            on_preset_change(PeriodPreset(str(evt.value)))

    row = ui.row().classes("items-center gap-2 flex-nowrap")
    with row:
        ui.select(
            options=preset_options,
            value=current_preset.value,
            label="Period",
            on_change=_handle_preset,
        ).props("dense outlined dark options-dark").classes("min-w-[175px]")

        if on_comparison_change is not None:
            comp_options: dict[str, str] = {m.value: lbl for m, lbl in _COMPARISON_LABELS.items()}

            def _handle_comp(evt: ValueChangeEventArguments[str]) -> None:
                if evt.value:
                    on_comparison_change(ComparisonMode(str(evt.value)))

            ui.select(
                options=comp_options,
                value=current_comparison.value,
                label="Compare",
                on_change=_handle_comp,
            ).props("dense outlined dark options-dark").classes("min-w-[165px]")

        if resolved_badge:
            status_badge(resolved_badge, tone=BadgeTone.INFO, outline=True)

    return row


def filter_chips_bar(
    filters: FilterSet,
    on_remove: Callable[[FilterField], None] | None = None,
    on_clear: Callable[[], None] | None = None,
) -> ui.row:
    """Render active semantic filter predicates as interactive chips.

    Parameters
    ----------
    filters : FilterSet
        Normalized active semantic filters.
    on_remove : Callable[[FilterField], None] | None, optional
        Callback invoked when a specific filter chip remove button is clicked.
    on_clear : Callable[[], None] | None, optional
        Callback invoked when clear-all button is clicked.

    Returns
    -------
    ui.row
        Constructed filter chip ribbon row.
    """
    row = ui.row().classes("w-full items-center gap-2 flex-wrap")
    with row:
        chips = filters.to_chips()
        if not chips:
            ui.label("No active domain filters").classes("text-xs italic").style(
                f"color: {StudioColor.DIM}"
            )
            return row

        ui.label("Filters:").classes("text-xs font-semibold uppercase").style(
            f"color: {StudioColor.MUTED}"
        )
        for chip in chips:
            with (
                ui.row()
                .classes("items-center gap-1 px-2 py-1 rounded text-xs")
                .style(
                    f"background-color: {StudioColor.SIDEBAR}; border: 1px solid {StudioColor.BORDER}"
                )
            ):
                ui.label(f"{chip.label}: {chip.value_display}").style(f"color: {StudioColor.TEXT}")
                if on_remove is not None:

                    def _make_remove(target_field: FilterField = chip.field) -> Callable[[], None]:
                        return lambda: on_remove(target_field)

                    ui.button(
                        icon="close",
                        on_click=_make_remove(),
                    ).props("flat round dense size=xs color=grey-5")

        if on_clear is not None:
            ui.button("Clear All", on_click=on_clear).props("flat dense size=xs color=warning")

    return row
