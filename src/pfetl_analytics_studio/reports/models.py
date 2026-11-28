"""Deterministic report domain models, block unions, and exporter-neutral outputs.

Owns
----
- Frozen `ReportContext` pinning every report run to one PFETL run & snapshot pair.
- Frozen discriminated union `ReportBlock` (10 block types) and `ReportDefinition`.
- Exporter-neutral `RenderedBlock` union, `ReportWarning`, and `ReportResult`.

Does not own
------------
- NiceGUI widgets (`pages/reports.py`), PDF/XLSX serialization, or DB I/O.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pfetl_analytics_studio.semantic import FilterSet, PeriodContext, PeriodRequest

CurrencyMode = Literal["base", "local"]


class ReportContext(BaseModel):
    """Immutable provenance & semantic context pinned to one PFETL run."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    pfetl_run_id: str
    core_version: str | None = None
    schema_version: str | None = None
    settings_snapshot_id: str | None = None
    rules_snapshot_id: str | None = None
    period: PeriodContext
    filters: FilterSet = FilterSet()
    studio_version: str
    generated_at: datetime
    currency_mode: CurrencyMode = "base"


class TitleBlock(BaseModel):
    """Primary report cover/header title block."""

    model_config = ConfigDict(frozen=True)

    block_type: Literal["title"] = "title"
    block_id: str = "title"
    title: str
    subtitle: str = ""


class SubtitleBlock(BaseModel):
    """Section heading or subtitle block."""

    model_config = ConfigDict(frozen=True)

    block_type: Literal["subtitle"] = "subtitle"
    block_id: str = "subtitle"
    text: str


class NarrativeBlock(BaseModel):
    """Static/user-authored narrative commentary block (never AI-generated)."""

    model_config = ConfigDict(frozen=True)

    block_type: Literal["narrative"] = "narrative"
    block_id: str = "narrative"
    heading: str = ""
    body: str


class ProvenanceBlock(BaseModel):
    """Mandatory provenance, snapshot IDs, and indicative-tax disclaimer block."""

    model_config = ConfigDict(frozen=True)

    block_type: Literal["provenance"] = "provenance"
    block_id: str = "provenance"
    show_snapshots: bool = True
    show_disclaimer: bool = True
    disclaimer_text: str = (
        "Indicative / estimated figures derived from PFETL canonical outputs; "
        "not statutory tax or financial advice."
    )


class KPIGroupBlock(BaseModel):
    """Registered KPI group block backed by an analytics registry source."""

    model_config = ConfigDict(frozen=True)

    block_type: Literal["kpi_group"] = "kpi_group"
    block_id: str = "kpi_group"
    source_id: str
    title: str = ""
    period_override: PeriodRequest | None = None
    filter_override: FilterSet | None = None
    required: bool = False


class ChartBlock(BaseModel):
    """Registered chart block backed by an analytics registry source."""

    model_config = ConfigDict(frozen=True)

    block_type: Literal["chart"] = "chart"
    block_id: str = "chart"
    source_id: str
    title: str = ""
    period_override: PeriodRequest | None = None
    filter_override: FilterSet | None = None
    required: bool = False


class TableBlock(BaseModel):
    """Registered table block backed by an analytics registry source."""

    model_config = ConfigDict(frozen=True)

    block_type: Literal["table"] = "table"
    block_id: str = "table"
    source_id: str
    title: str = ""
    max_rows: int = Field(default=200, ge=1, le=5000)
    period_override: PeriodRequest | None = None
    filter_override: FilterSet | None = None
    required: bool = False


class SavedViewBlock(BaseModel):
    """Validated Explorer Saved View block replayed against current run context."""

    model_config = ConfigDict(frozen=True)

    block_type: Literal["saved_view"] = "saved_view"
    block_id: str = "saved_view"
    view_id: str
    title: str = ""
    max_rows: int = Field(default=200, ge=1, le=5000)
    period_override: PeriodRequest | None = None
    filter_override: FilterSet | None = None
    required: bool = False


class DividerBlock(BaseModel):
    """Visual section divider block."""

    model_config = ConfigDict(frozen=True)

    block_type: Literal["divider"] = "divider"
    block_id: str = "divider"
    label: str = ""


class PageBreakBlock(BaseModel):
    """Exporter page-break hint block (rendered as subtle break marker in web preview)."""

    model_config = ConfigDict(frozen=True)

    block_type: Literal["page_break"] = "page_break"
    block_id: str = "page_break"


ReportBlock = Annotated[
    TitleBlock
    | SubtitleBlock
    | NarrativeBlock
    | ProvenanceBlock
    | KPIGroupBlock
    | ChartBlock
    | TableBlock
    | SavedViewBlock
    | DividerBlock
    | PageBreakBlock,
    Field(discriminator="block_type"),
]


class ReportDefinition(BaseModel):
    """Presentation-only report definition (contains references, never data)."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str = ""
    default_period: PeriodRequest = PeriodRequest()
    default_filters: FilterSet = FilterSet()
    blocks: tuple[ReportBlock, ...] = ()
    builtin: bool = False
    currency_mode: CurrencyMode = "base"


class ReportWarning(BaseModel):
    """Structured non-fatal warning emitted during report block execution."""

    model_config = ConfigDict(frozen=True)

    block_id: str
    code: str
    message: str
    remediation: str = ""


class ReportKpiItem(BaseModel):
    """Exporter-neutral single KPI metric item."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: float | str | None = None
    formatted_value: str
    subtitle: str = ""
    tone: str = "neutral"
    source_table: str = ""
    delta_text: str = ""
    delta_tone: str = "neutral"
    breakups: tuple[tuple[str, str], ...] = ()
    tooltip: str = ""


class RenderedTextBlock(BaseModel):
    """Rendered title, subtitle, or narrative block."""

    model_config = ConfigDict(frozen=True)

    rendered_type: Literal["text"] = "text"
    block_type: Literal["title", "subtitle", "narrative"]
    block_id: str
    heading: str = ""
    body: str = ""


class RenderedProvenanceBlock(BaseModel):
    """Rendered provenance stamp & snapshot metadata block."""

    model_config = ConfigDict(frozen=True)

    rendered_type: Literal["provenance"] = "provenance"
    block_id: str
    profile_id: str
    pfetl_run_id: str
    core_version: str | None = None
    schema_version: str | None = None
    settings_snapshot_id: str | None = None
    rules_snapshot_id: str | None = None
    period_label: str
    comparison_label: str = ""
    studio_version: str
    generated_at: datetime
    currency_mode: CurrencyMode = "base"
    disclaimer_text: str = ""


class RenderedKPIGroupBlock(BaseModel):
    """Rendered KPI group containing canonical formatted metrics."""

    model_config = ConfigDict(frozen=True)

    rendered_type: Literal["kpi_group"] = "kpi_group"
    block_id: str
    source_id: str
    title: str
    items: tuple[ReportKpiItem, ...] = ()
    source_tables: tuple[str, ...] = ()


class RenderedChartBlock(BaseModel):
    """Rendered chart block carrying a Plotly-compatible figure dictionary."""

    model_config = ConfigDict(frozen=True)

    rendered_type: Literal["chart"] = "chart"
    block_id: str
    source_id: str
    title: str
    figure_dict: dict[str, Any] = Field(default_factory=dict)
    source_tables: tuple[str, ...] = ()
    caption: str = ""


class RenderedTableBlock(BaseModel):
    """Rendered tabular block carrying exporter-neutral columns and row dictionaries."""

    model_config = ConfigDict(frozen=True)

    rendered_type: Literal["table"] = "table"
    block_id: str
    source_id: str
    title: str
    columns: tuple[str, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    total_rows: int = 0
    truncated: bool = False
    source_tables: tuple[str, ...] = ()


class RenderedStructuralBlock(BaseModel):
    """Rendered divider or page-break structural marker."""

    model_config = ConfigDict(frozen=True)

    rendered_type: Literal["structural"] = "structural"
    block_type: Literal["divider", "page_break"]
    block_id: str
    label: str = ""


class RenderedBrokenBlock(BaseModel):
    """Graceful placeholder for an unavailable or broken non-required block."""

    model_config = ConfigDict(frozen=True)

    rendered_type: Literal["broken"] = "broken"
    block_id: str
    original_block_type: str
    reference_id: str
    code: str
    message: str
    remediation: str = ""


RenderedBlock = Annotated[
    RenderedTextBlock
    | RenderedProvenanceBlock
    | RenderedKPIGroupBlock
    | RenderedChartBlock
    | RenderedTableBlock
    | RenderedStructuralBlock
    | RenderedBrokenBlock,
    Field(discriminator="rendered_type"),
]


class ReportResult(BaseModel):
    """Complete deterministic, exporter-neutral output of rendering a ReportDefinition."""

    model_config = ConfigDict(frozen=True)

    definition_id: str
    definition_name: str
    context: ReportContext
    blocks: tuple[RenderedBlock, ...] = ()
    warnings: tuple[ReportWarning, ...] = ()
    duration_ms: float = 0.0
