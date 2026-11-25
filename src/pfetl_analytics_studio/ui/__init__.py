"""UI package for PFETL Analytics Studio.

Ownership: Reusable presentational components, styling, charts, themes, and formatters.
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
from pfetl_analytics_studio.ui.charts import (
    build_asset_class_mix_figure,
    build_cashflow_efficiency_figure,
    build_category_breakup_figure,
    build_deployment_flow_figure,
    build_fire_trajectory_figure,
    build_net_worth_attribution_figure,
    build_portfolio_return_figure,
    build_wealth_asset_split_figure,
    chart_panel,
    studio_plotly_layout,
)
from pfetl_analytics_studio.ui.filters import filter_chips_bar, period_selector
from pfetl_analytics_studio.ui.formatting import (
    currency_symbol,
    format_currency,
    format_metric_value,
    format_months,
    format_percent,
    format_ratio,
)
from pfetl_analytics_studio.ui.tables import TableColumn, bounded_data_table
from pfetl_analytics_studio.ui.theme import CHART_SERIES_COLORS, StudioColor, apply_studio_theme

__all__ = [
    "CHART_SERIES_COLORS",
    "BadgeTone",
    "StudioColor",
    "TableColumn",
    "apply_studio_theme",
    "bounded_data_table",
    "build_asset_class_mix_figure",
    "build_cashflow_efficiency_figure",
    "build_category_breakup_figure",
    "build_deployment_flow_figure",
    "build_fire_trajectory_figure",
    "build_net_worth_attribution_figure",
    "build_portfolio_return_figure",
    "build_wealth_asset_split_figure",
    "chart_panel",
    "currency_symbol",
    "empty_state_panel",
    "error_state_panel",
    "filter_chips_bar",
    "format_currency",
    "format_metric_value",
    "format_months",
    "format_percent",
    "format_ratio",
    "kpi_card",
    "loading_state_card",
    "locked_warehouse_banner",
    "period_selector",
    "status_badge",
    "studio_plotly_layout",
]
