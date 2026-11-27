"""Mutable custom report editor draft models, cloning, and validation helpers.

Owns
----
- Mutable `ReportBlockDraft` and `ReportDefinitionDraft` models converted to/from frozen
  `ReportBlock` / `ReportDefinition` models.
- Non-mutating built-in cloning and duplicate name generation.
- Deterministic reference validation (`validate_report_draft`) detecting missing
  `source_id` or deleted `SavedView` references.

Does not own
------------
- NiceGUI widgets (`pages/reports.py`), SQLite persistence (`state.py`), or DB queries.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

from pfetl_analytics_studio.reports.models import (
    ChartBlock,
    CurrencyMode,
    DividerBlock,
    KPIGroupBlock,
    NarrativeBlock,
    PageBreakBlock,
    ProvenanceBlock,
    ReportBlock,
    ReportDefinition,
    SavedViewBlock,
    SubtitleBlock,
    TableBlock,
    TitleBlock,
)
from pfetl_analytics_studio.reports.registry import ReportRegistry, ReportSourceKind
from pfetl_analytics_studio.semantic import FilterSet, PeriodRequest
from pfetl_analytics_studio.state import SavedViewDefinition

EditorBlockType = Literal[
    "title",
    "subtitle",
    "narrative",
    "provenance",
    "kpi_group",
    "chart",
    "table",
    "saved_view",
    "divider",
    "page_break",
]


class ReportBlockDraft(BaseModel):
    """Mutable UI draft representation of any `ReportBlock` variant."""

    block_id: str
    block_type: EditorBlockType
    title: str = ""
    subtitle: str = ""
    heading: str = ""
    body: str = ""
    label: str = ""
    source_id: str = ""
    view_id: str = ""
    max_rows: int = Field(default=200, ge=1, le=5000)
    required: bool = False
    show_snapshots: bool = True
    show_disclaimer: bool = True
    disclaimer_text: str = (
        "Indicative / estimated figures derived from PFETL canonical outputs; "
        "not statutory tax or financial advice."
    )
    period_override: PeriodRequest | None = None
    filter_override: FilterSet | None = None

    @classmethod
    def from_block(cls, block: ReportBlock) -> ReportBlockDraft:
        """Convert an immutable `ReportBlock` into a mutable `ReportBlockDraft`."""
        if isinstance(block, TitleBlock):
            return cls(
                block_id=block.block_id,
                block_type="title",
                title=block.title,
                subtitle=block.subtitle,
            )
        if isinstance(block, SubtitleBlock):
            return cls(
                block_id=block.block_id,
                block_type="subtitle",
                title=block.text,
            )
        if isinstance(block, NarrativeBlock):
            return cls(
                block_id=block.block_id,
                block_type="narrative",
                heading=block.heading,
                body=block.body,
            )
        if isinstance(block, ProvenanceBlock):
            return cls(
                block_id=block.block_id,
                block_type="provenance",
                show_snapshots=block.show_snapshots,
                show_disclaimer=block.show_disclaimer,
                disclaimer_text=block.disclaimer_text,
            )
        if isinstance(block, KPIGroupBlock):
            return cls(
                block_id=block.block_id,
                block_type="kpi_group",
                source_id=block.source_id,
                title=block.title,
                period_override=block.period_override,
                filter_override=block.filter_override,
                required=block.required,
            )
        if isinstance(block, ChartBlock):
            return cls(
                block_id=block.block_id,
                block_type="chart",
                source_id=block.source_id,
                title=block.title,
                period_override=block.period_override,
                filter_override=block.filter_override,
                required=block.required,
            )
        if isinstance(block, TableBlock):
            return cls(
                block_id=block.block_id,
                block_type="table",
                source_id=block.source_id,
                title=block.title,
                max_rows=block.max_rows,
                period_override=block.period_override,
                filter_override=block.filter_override,
                required=block.required,
            )
        if isinstance(block, SavedViewBlock):
            return cls(
                block_id=block.block_id,
                block_type="saved_view",
                view_id=block.view_id,
                title=block.title,
                max_rows=block.max_rows,
                period_override=block.period_override,
                filter_override=block.filter_override,
                required=block.required,
            )
        if isinstance(block, DividerBlock):
            return cls(
                block_id=block.block_id,
                block_type="divider",
                label=block.label,
            )
        return cls(
            block_id=block.block_id,
            block_type="page_break",
        )

    def to_block(self) -> ReportBlock:
        """Convert this mutable draft into a validated immutable `ReportBlock`."""
        bid = self.block_id.strip() or f"blk_{uuid.uuid4().hex[:8]}"
        if self.block_type == "title":
            return TitleBlock(
                block_id=bid,
                title=self.title.strip() or "Untitled Report",
                subtitle=self.subtitle.strip(),
            )
        if self.block_type == "subtitle":
            return SubtitleBlock(block_id=bid, text=self.title.strip() or "Section")
        if self.block_type == "narrative":
            return NarrativeBlock(
                block_id=bid,
                heading=self.heading.strip(),
                body=self.body.strip() or "Commentary.",
            )
        if self.block_type == "provenance":
            return ProvenanceBlock(
                block_id=bid,
                show_snapshots=self.show_snapshots,
                show_disclaimer=self.show_disclaimer,
                disclaimer_text=self.disclaimer_text,
            )
        if self.block_type == "kpi_group":
            return KPIGroupBlock(
                block_id=bid,
                source_id=self.source_id.strip(),
                title=self.title.strip(),
                period_override=self.period_override,
                filter_override=self.filter_override,
                required=self.required,
            )
        if self.block_type == "chart":
            return ChartBlock(
                block_id=bid,
                source_id=self.source_id.strip(),
                title=self.title.strip(),
                period_override=self.period_override,
                filter_override=self.filter_override,
                required=self.required,
            )
        if self.block_type == "table":
            return TableBlock(
                block_id=bid,
                source_id=self.source_id.strip(),
                title=self.title.strip(),
                max_rows=self.max_rows,
                period_override=self.period_override,
                filter_override=self.filter_override,
                required=self.required,
            )
        if self.block_type == "saved_view":
            return SavedViewBlock(
                block_id=bid,
                view_id=self.view_id.strip(),
                title=self.title.strip(),
                max_rows=self.max_rows,
                period_override=self.period_override,
                filter_override=self.filter_override,
                required=self.required,
            )
        if self.block_type == "divider":
            return DividerBlock(block_id=bid, label=self.label.strip())
        return PageBreakBlock(block_id=bid)


class ReportDefinitionDraft(BaseModel):
    """Mutable custom report editor draft converted to `ReportDefinition` on save."""

    id: str
    name: str
    description: str = ""
    default_period: PeriodRequest = PeriodRequest()
    default_filters: FilterSet = FilterSet()
    currency_mode: CurrencyMode = "base"
    blocks: list[ReportBlockDraft] = []

    @classmethod
    def from_definition(cls, defn: ReportDefinition) -> ReportDefinitionDraft:
        """Create a mutable `ReportDefinitionDraft` from an immutable `ReportDefinition`."""
        return cls(
            id=defn.id,
            name=defn.name,
            description=defn.description,
            default_period=defn.default_period,
            default_filters=defn.default_filters,
            currency_mode=defn.currency_mode,
            blocks=[ReportBlockDraft.from_block(b) for b in defn.blocks],
        )

    def to_definition(self) -> ReportDefinition:
        """Convert this draft into a frozen `ReportDefinition` (`builtin=False`)."""
        return ReportDefinition(
            id=self.id.strip() or f"custom_{uuid.uuid4().hex[:10]}",
            name=self.name.strip() or "Untitled Custom Report",
            description=self.description.strip(),
            default_period=self.default_period,
            default_filters=self.default_filters,
            blocks=tuple(b.to_block() for b in self.blocks),
            builtin=False,
            currency_mode=self.currency_mode,
        )

    def move_block(self, index: int, delta: int) -> bool:
        """Move block at `index` by `delta` (`-1` up, `+1` down); return `True` if moved."""
        target = index + delta
        if not (0 <= index < len(self.blocks) and 0 <= target < len(self.blocks)):
            return False
        self.blocks[index], self.blocks[target] = self.blocks[target], self.blocks[index]
        return True

    def add_block(self, block: ReportBlockDraft, index: int | None = None) -> None:
        """Insert a `ReportBlockDraft` at `index` (or append at end)."""
        if index is None or index < 0 or index >= len(self.blocks):
            self.blocks.append(block)
        else:
            self.blocks.insert(index, block)

    def remove_block(self, index: int) -> ReportBlockDraft | None:
        """Remove and return the block at `index`, or `None` if out of range."""
        if 0 <= index < len(self.blocks):
            return self.blocks.pop(index)
        return None


class BlockValidationIssue(BaseModel):
    """Single validation warning or blocking error on a `ReportDefinitionDraft`."""

    block_id: str
    code: str
    message: str
    remediation: str = ""
    blocking: bool = False


class ReportValidationResult(BaseModel):
    """Aggregated validation outcome for a `ReportDefinitionDraft`."""

    is_valid: bool
    has_blocking_errors: bool
    issues: tuple[BlockValidationIssue, ...] = ()

    def issues_for_block(self, block_id: str) -> tuple[BlockValidationIssue, ...]:
        """Return validation issues targeting `block_id`."""
        return tuple(i for i in self.issues if i.block_id == block_id)


def generate_unique_report_name(
    base_name: str,
    existing_names: set[str],
    *,
    suffix: str = "Copy",
) -> str:
    """Return a profile-unique report name derived from `base_name`."""
    clean = base_name.strip() or "Custom Report"
    normalized = {n.strip().lower() for n in existing_names}
    candidate = f"{clean} ({suffix})"
    if candidate.lower() not in normalized:
        return candidate
    idx = 2
    while True:
        numbered = f"{clean} ({suffix} {idx})"
        if numbered.lower() not in normalized:
            return numbered
        idx += 1


def clone_definition_as_draft(
    source: ReportDefinition,
    existing_names: set[str],
    *,
    suffix: str = "Copy",
) -> ReportDefinitionDraft:
    """Clone a built-in or custom `ReportDefinition` into a fresh `ReportDefinitionDraft`."""
    draft = ReportDefinitionDraft.from_definition(source)
    draft.id = f"custom_{uuid.uuid4().hex[:12]}"
    draft.name = generate_unique_report_name(source.name, existing_names, suffix=suffix)
    return draft


def validate_report_draft(
    draft: ReportDefinitionDraft,
    *,
    registry: ReportRegistry,
    saved_views_by_id: Mapping[str, SavedViewDefinition],
    existing_names_by_id: Mapping[str, str] | None = None,
) -> ReportValidationResult:
    """Validate draft metadata, uniqueness, registered sources, and saved-view references."""
    issues: list[BlockValidationIssue] = []
    clean_name = draft.name.strip()
    if not clean_name:
        issues.append(
            BlockValidationIssue(
                block_id="__report__",
                code="EMPTY_REPORT_NAME",
                message="Report name cannot be empty.",
                remediation="Enter a descriptive report name before saving.",
                blocking=True,
            )
        )
    elif existing_names_by_id:
        for other_id, other_name in existing_names_by_id.items():
            if other_id != draft.id and other_name.strip().lower() == clean_name.lower():
                issues.append(
                    BlockValidationIssue(
                        block_id="__report__",
                        code="DUPLICATE_REPORT_NAME",
                        message=f"A saved report named '{clean_name}' already exists in this profile.",
                        remediation="Choose a unique report name for this profile.",
                        blocking=True,
                    )
                )
                break

    if not draft.blocks:
        issues.append(
            BlockValidationIssue(
                block_id="__report__",
                code="EMPTY_BLOCKS",
                message="Report has no blocks yet.",
                remediation="Add at least one KPI, Chart, Table, Saved View, or Narrative block.",
                blocking=True,
            )
        )

    expected_kind_map = {
        "kpi_group": ReportSourceKind.KPI_GROUP,
        "chart": ReportSourceKind.CHART,
        "table": ReportSourceKind.TABLE,
    }
    for blk in draft.blocks:
        if blk.block_type in expected_kind_map:
            src = registry.get_source(blk.source_id)
            expected_kind = expected_kind_map[blk.block_type]
            if src is None or src.kind != expected_kind:
                issues.append(
                    BlockValidationIssue(
                        block_id=blk.block_id,
                        code="MISSING_BLOCK_SOURCE",
                        message=(
                            f"Registered {blk.block_type} source '{blk.source_id or '<empty>'}' "
                            "was not found in ReportRegistry."
                        ),
                        remediation=(
                            "Select an active registered source, uncheck Required, or remove this block."
                        ),
                        blocking=blk.required or not blk.source_id.strip(),
                    )
                )
        elif blk.block_type == "saved_view" and blk.view_id not in saved_views_by_id:
            issues.append(
                BlockValidationIssue(
                    block_id=blk.block_id,
                    code="BROKEN_SAVED_VIEW_REF",
                    message=(
                        f"Referenced Saved View '{blk.view_id or '<empty>'}' was deleted or is unavailable."
                    ),
                    remediation=(
                        "Re-bind to an active Saved View in this profile, uncheck Required, or remove this block."
                    ),
                    blocking=blk.required or not blk.view_id.strip(),
                )
            )

    has_blocking = any(i.blocking for i in issues)
    return ReportValidationResult(
        is_valid=not issues,
        has_blocking_errors=has_blocking,
        issues=tuple(issues),
    )
