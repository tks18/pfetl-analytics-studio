"""Theme tokens and modern dark financial terminal styling aligned with PFETL Core.

Owns
----
- High-contrast `StudioColor` palette optimized for WCAG readability on obsidian surfaces.
- `CHART_SERIES_COLORS` palette for Plotly multi-series figures.
- `apply_studio_theme()` applying dark Quasar tokens, glass surfaces, filled inputs,
  dark tooltips, and responsive CSS grids.

Does not own
------------
- Page composition, navigation state, or analytical queries.
"""

from __future__ import annotations

from enum import StrEnum

from nicegui import ui


class StudioColor(StrEnum):
    """Canonical high-contrast color palette tokens for PFETL Analytics Studio."""

    BG = "#080C14"
    SIDEBAR = "#0C1220"
    CARD = "#111827"
    SURFACE_ALT = "#1A253B"
    HEADER = "#080C14"
    BORDER = "#1E293B"
    ACCENT = "#3B82F6"
    ACC_HOV = "#60A5FA"
    SUCCESS = "#10B981"
    WARNING = "#F59E0B"
    ERROR = "#EF4444"
    RECOVERY = "#A855F7"
    TEXT = "#F8FAFC"
    MUTED = "#CBD5E1"
    DIM = "#94A3B8"
    LOG_BG = "#060910"


CHART_SERIES_COLORS: tuple[str, ...] = (
    "#60A5FA",
    "#38BDF8",
    "#34D399",
    "#C084FC",
    "#FBBF24",
    "#F472B6",
    "#2DD4BF",
    "#F87171",
)


class ChartColorRole(StrEnum):
    """Consistent semantic color roles across all domain Plotly charts."""

    ACTUAL = "actual"
    BENCHMARK = "benchmark"
    TARGET = "target"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MARKET = "market"
    BOOK = "book"
    ACTIVE = "active"
    PASSIVE = "passive"
    NON_CASH = "non_cash"
    FX = "fx"


CHART_ROLE_COLORS: dict[ChartColorRole, str] = {
    ChartColorRole.ACTUAL: "#60A5FA",
    ChartColorRole.BENCHMARK: "#FBBF24",
    ChartColorRole.TARGET: "#FBBF24",
    ChartColorRole.POSITIVE: "#34D399",
    ChartColorRole.NEGATIVE: "#F87171",
    ChartColorRole.NEUTRAL: "#94A3B8",
    ChartColorRole.MARKET: "#38BDF8",
    ChartColorRole.BOOK: "#CBD5E1",
    ChartColorRole.ACTIVE: "#38BDF8",
    ChartColorRole.PASSIVE: "#34D399",
    ChartColorRole.NON_CASH: "#C084FC",
    ChartColorRole.FX: "#2DD4BF",
}

_STUDIO_DARK_CSS = f"""
<style>
  :root {{
    color-scheme: dark;
  }}
  body, .q-page-container {{
    background: radial-gradient(ellipse at top left, #121C33 0%, {StudioColor.BG} 52%);
    background-color: {StudioColor.BG} !important;
    color: {StudioColor.TEXT} !important;
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .font-mono, code, pre {{
    font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
  }}
  .tabular-nums {{
    font-variant-numeric: tabular-nums;
  }}
  .q-drawer {{
    background-color: {StudioColor.SIDEBAR} !important;
    border-right: 1px solid rgba(255, 255, 255, 0.08) !important;
  }}
  .q-header {{
    background-color: rgba(8, 12, 20, 0.90) !important;
    backdrop-filter: blur(12px);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08) !important;
  }}
  .studio-card {{
    background: linear-gradient(180deg, rgba(22, 32, 52, 0.88) 0%, rgba(14, 21, 36, 0.97) 100%) !important;
    border: 1px solid rgba(255, 255, 255, 0.09) !important;
    box-shadow: 0 8px 24px -8px rgba(2, 6, 23, 0.55) !important;
    color: {StudioColor.TEXT} !important;
    transition: border-color 160ms ease, transform 160ms ease;
  }}
  .studio-card:hover {{
    border-color: rgba(96, 165, 250, 0.35) !important;
  }}
  .studio-toolbar {{
    background: rgba(15, 23, 42, 0.74) !important;
    border: 1px solid rgba(255, 255, 255, 0.09) !important;
    backdrop-filter: blur(10px);
    box-shadow: none !important;
  }}
  /* Responsive 3-col KPI grid and 2-col Chart grid immune to Tailwind/Quasar clashes */
  .studio-kpi-grid {{
    display: grid !important;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
    width: 100%;
  }}
  .studio-chart-grid {{
    display: grid !important;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
    width: 100%;
  }}
  @media (max-width: 1120px) {{
    .studio-kpi-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .studio-chart-grid {{
      grid-template-columns: minmax(0, 1fr);
    }}
  }}
  @media (max-width: 680px) {{
    .studio-kpi-grid {{
      grid-template-columns: minmax(0, 1fr);
    }}
  }}
  /* Modern sleek filled pill inputs replacing heavy outlined boxes */
  .studio-field .q-field__control {{
    background-color: rgba(255, 255, 255, 0.05) !important;
    border: 1px solid rgba(255, 255, 255, 0.13) !important;
    border-radius: 10px !important;
    min-height: 40px !important;
  }}
  .studio-field .q-field__control:before,
  .studio-field .q-field__control:after {{
    display: none !important;
  }}
  .studio-field.q-field--focused .q-field__control {{
    border-color: rgba(96, 165, 250, 0.65) !important;
    background-color: rgba(59, 130, 246, 0.08) !important;
  }}
  .studio-field .q-field__label {{
    color: {StudioColor.DIM} !important;
    font-size: 11px !important;
  }}
  .studio-field .q-field__native,
  .studio-field .q-select__dropdown-icon {{
    color: {StudioColor.TEXT} !important;
  }}
  /* Dark sleek tooltip styling overriding Quasar grey box */
  .q-tooltip.studio-tooltip {{
    background-color: #0F172A !important;
    color: #F8FAFC !important;
    border: 1px solid rgba(148, 163, 184, 0.35) !important;
    border-radius: 10px !important;
    padding: 8px 12px !important;
    font-size: 12px !important;
    line-height: 1.45 !important;
    max-width: 340px !important;
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.65) !important;
    white-space: pre-line !important;
  }}
  .studio-table .q-table__top,
  .studio-table .q-table__bottom,
  .studio-table thead tr:first-child th {{
    background-color: {StudioColor.SIDEBAR} !important;
    color: {StudioColor.MUTED} !important;
    border-color: rgba(255, 255, 255, 0.10) !important;
    font-weight: 600;
  }}
  .studio-table tbody td {{
    border-color: rgba(255, 255, 255, 0.08) !important;
    color: {StudioColor.TEXT} !important;
    font-variant-numeric: tabular-nums;
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
        info="#60A5FA",
        warning=StudioColor.WARNING,
    )
    ui.add_head_html(_STUDIO_DARK_CSS)
