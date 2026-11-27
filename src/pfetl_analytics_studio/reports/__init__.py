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

from pfetl_analytics_studio.reports.editor import (
    BlockValidationIssue,
    ReportBlockDraft,
    ReportDefinitionDraft,
    ReportValidationResult,
    clone_definition_as_draft,
    generate_unique_report_name,
    validate_report_draft,
)
from pfetl_analytics_studio.reports.exporters import (
    ExportFormat,
    ExportPrerequisiteError,
    ExportResult,
    ReportExporter,
    render_chart_image_bytes,
    sanitize_excel_sheet_name,
    sanitize_filename,
)
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
    "BlockValidationIssue",
    "ChartBlock",
    "CurrencyMode",
    "DividerBlock",
    "DuplicateReportRegistrationError",
    "ExportFormat",
    "ExportPrerequisiteError",
    "ExportResult",
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
    "ReportBlockDraft",
    "ReportContext",
    "ReportContextOverrides",
    "ReportDefinition",
    "ReportDefinitionDraft",
    "ReportExporter",
    "ReportKpiItem",
    "ReportRegistry",
    "ReportResolutionError",
    "ReportResult",
    "ReportService",
    "ReportSourceContext",
    "ReportSourceKind",
    "ReportValidationResult",
    "ReportWarning",
    "RequiredReportBlockError",
    "SavedViewBlock",
    "SubtitleBlock",
    "TableBlock",
    "TitleBlock",
    "build_default_report_registry",
    "clone_definition_as_draft",
    "generate_unique_report_name",
    "render_chart_image_bytes",
    "sanitize_excel_sheet_name",
    "sanitize_filename",
    "validate_report_draft",
]
