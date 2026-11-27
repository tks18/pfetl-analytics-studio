"""Reports package for PFETL Analytics Studio.

Owns
----
- Deterministic report domain models (`ReportContext`, `ReportDefinition`, `RenderedBlock`, `ReportResult`).
- Registered analytical block sources & built-in templates (`ReportRegistry`, `DEFAULT_REPORT_REGISTRY`).
- Run-pinned rendering runtime (`ReportService`).

Does not own
------------
- NiceGUI presentation widgets (`pages/reports.py`) or PFETL warehouse/SQLite connections.
"""

from __future__ import annotations

from pfetl_analytics_studio.reports.models import (
    ChartBlock,
    CurrencyMode,
    DividerBlock,
    KPIGroupBlock,
    NarrativeBlock,
    PageBreakBlock,
    ProvenanceBlock,
    RenderedBlock,
    RenderedBrokenBlock,
    RenderedChartBlock,
    RenderedKPIGroupBlock,
    RenderedProvenanceBlock,
    RenderedStructuralBlock,
    RenderedTableBlock,
    RenderedTextBlock,
    ReportBlock,
    ReportContext,
    ReportDefinition,
    ReportKpiItem,
    ReportResult,
    ReportWarning,
    SavedViewBlock,
    SubtitleBlock,
    TableBlock,
    TitleBlock,
)
from pfetl_analytics_studio.reports.registry import (
    DEFAULT_REPORT_REGISTRY,
    DuplicateReportRegistrationError,
    RegisteredBlockSource,
    ReportRegistry,
    ReportSourceContext,
    ReportSourceKind,
    build_default_report_registry,
)
from pfetl_analytics_studio.reports.service import (
    ReportContextOverrides,
    ReportResolutionError,
    ReportService,
    RequiredReportBlockError,
)

__all__ = [
    "DEFAULT_REPORT_REGISTRY",
    "ChartBlock",
    "CurrencyMode",
    "DividerBlock",
    "DuplicateReportRegistrationError",
    "KPIGroupBlock",
    "NarrativeBlock",
    "PageBreakBlock",
    "ProvenanceBlock",
    "RegisteredBlockSource",
    "RenderedBlock",
    "RenderedBrokenBlock",
    "RenderedChartBlock",
    "RenderedKPIGroupBlock",
    "RenderedProvenanceBlock",
    "RenderedStructuralBlock",
    "RenderedTableBlock",
    "RenderedTextBlock",
    "ReportBlock",
    "ReportContext",
    "ReportContextOverrides",
    "ReportDefinition",
    "ReportKpiItem",
    "ReportRegistry",
    "ReportResolutionError",
    "ReportResult",
    "ReportService",
    "ReportSourceContext",
    "ReportSourceKind",
    "ReportWarning",
    "RequiredReportBlockError",
    "SavedViewBlock",
    "SubtitleBlock",
    "TableBlock",
    "TitleBlock",
    "build_default_report_registry",
]
