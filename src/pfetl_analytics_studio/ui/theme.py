"""Theme tokens and dark-mode styling aligned with PFETL Core visual identity.

Owns
----
- `StudioColor` palette matching PFETL Core (`#0D1117`, `#101624`, `#1E293B`, `#3B82F6`).
- `CHART_SERIES_COLORS` extending the base status palette safely for Plotly charts.
- `apply_studio_theme()` helper applying Quasar theme tokens and global dark CSS.

Does not own
------------
- Page composition, navigation state, or analytical queries.
"""

from __future__ import annotations

from enum import StrEnum

from nicegui import ui


class StudioColor(StrEnum):
    """Canonical color palette tokens matching PFETL Core desktop theme."""

    BG = "#0D1117"
    SIDEBAR = "#101624"
    CARD = "#111827"
    SURFACE_ALT = "#161F30"
    HEADER = "#0D1117"
    BORDER = "#1E293B"
    ACCENT = "#3B82F6"
    ACC_HOV = "#2563EB"
    SUCCESS = "#22C55E"
    WARNING = "#F59E0B"
    ERROR = "#EF4444"
    RECOVERY = "#C084FC"
    TEXT = "#E2E8F0"
    MUTED = "#94A3B8"
    DIM = "#4B5563"
    LOG_BG = "#080C14"


CHART_SERIES_COLORS: tuple[str, ...] = (
    StudioColor.ACCENT,
    "#38BDF8",
    StudioColor.SUCCESS,
    StudioColor.RECOVERY,
    StudioColor.WARNING,
    "#EC4899",
    "#14B8A6",
    StudioColor.ERROR,
)

_STUDIO_DARK_CSS = f"""
<style>
  body, .q-page-container {{
    background-color: {StudioColor.BG} !important;
    color: {StudioColor.TEXT} !important;
  }}
  .q-drawer {{
    background-color: {StudioColor.SIDEBAR} !important;
    border-right: 1px solid {StudioColor.BORDER} !important;
  }}
  .q-header {{
    background-color: {StudioColor.HEADER} !important;
    border-bottom: 1px solid {StudioColor.BORDER} !important;
  }}
  .studio-card {{
    background-color: {StudioColor.CARD} !important;
    border: 1px solid {StudioColor.BORDER} !important;
    color: {StudioColor.TEXT} !important;
  }}
  .studio-table .q-table__top,
  .studio-table .q-table__bottom,
  .studio-table thead tr:first-child th {{
    background-color: {StudioColor.SIDEBAR} !important;
    color: {StudioColor.MUTED} !important;
    border-color: {StudioColor.BORDER} !important;
  }}
  .studio-table tbody td {{
    border-color: {StudioColor.BORDER} !important;
    color: {StudioColor.TEXT} !important;
  }}
</style>
"""


def apply_studio_theme() -> None:
    """Apply PFETL Core-aligned dark theme tokens and stylesheet rules to active page."""
    ui.dark_mode(True)
    ui.colors(
        primary=StudioColor.ACCENT,
        secondary="#0EA5E9",
        accent=StudioColor.RECOVERY,
        dark=StudioColor.SIDEBAR,
        positive=StudioColor.SUCCESS,
        negative=StudioColor.ERROR,
        info="#38BDF8",
        warning=StudioColor.WARNING,
    )
    ui.add_head_html(_STUDIO_DARK_CSS)
