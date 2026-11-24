"""UI package for PFETL Analytics Studio.

Ownership: Reusable presentational components, styling, themes, and formatters.
Forbidden: Does not import analytics or data layers directly.
"""

from __future__ import annotations

from pfetl_analytics_studio.ui.cards import (
    BadgeTone,
    empty_state_panel,
    error_state_panel,
    kpi_card,
    loading_state_card,
    locked_warehouse_banner,
    status_badge,
)
from pfetl_analytics_studio.ui.filters import filter_chips_bar, period_selector
from pfetl_analytics_studio.ui.tables import TableColumn, bounded_data_table
from pfetl_analytics_studio.ui.theme import CHART_SERIES_COLORS, StudioColor, apply_studio_theme

__all__ = [
    "CHART_SERIES_COLORS",
    "BadgeTone",
    "StudioColor",
    "TableColumn",
    "apply_studio_theme",
    "bounded_data_table",
    "empty_state_panel",
    "error_state_panel",
    "filter_chips_bar",
    "kpi_card",
    "loading_state_card",
    "locked_warehouse_banner",
    "period_selector",
    "status_badge",
]
