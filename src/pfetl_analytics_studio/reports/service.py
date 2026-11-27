"""Deterministic report rendering runtime and PFETL run-context resolver.

Owns
----
- `ReportService` resolving run-pinned `ReportContext` from `AppContext` + Control Plane snapshots.
- Ordered execution of `ReportDefinition` blocks against `ReportRegistry` & `ExplorerAnalytics`.
- Optional persistence of preview/run metadata into Studio's `report_runs` table.

Does not own
------------
- NiceGUI widgets (`pages/reports.py`) or PDF/XLSX export rendering (Phase 25).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio import __version__ as STUDIO_VERSION
from pfetl_analytics_studio.analytics import ExplorerAnalytics
from pfetl_analytics_studio.analytics.explorer_models import GuidedQueryResult, SqlQueryResult
from pfetl_analytics_studio.context import AppContext
from pfetl_analytics_studio.data import (
    DuckDBReader,
    PFETLCatalog,
    QueryCache,
    RunProvenance,
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
    RenderedProvenanceBlock,
    RenderedStructuralBlock,
    RenderedTableBlock,
    RenderedTextBlock,
    ReportContext,
    ReportDefinition,
    ReportResult,
    ReportWarning,
    SavedViewBlock,
    SubtitleBlock,
    TableBlock,
    TitleBlock,
)
from pfetl_analytics_studio.reports.registry import (
    DEFAULT_REPORT_REGISTRY,
    ReportRegistry,
    ReportSourceContext,
    ReportSourceKind,
)
from pfetl_analytics_studio.semantic import (
    FilterSet,
    PeriodContext,
    PeriodPreset,
    PeriodRequest,
    PeriodResolver,
)
from pfetl_analytics_studio.state import StudioStateStore


class ReportResolutionError(RuntimeError):
    """Raised when a report run context cannot be resolved from active profile/run."""


class RequiredReportBlockError(RuntimeError):
    """Raised when a block marked `required=True` fails to resolve or execute."""


@dataclass(frozen=True)
class ReportContextOverrides:
    """Explicit runtime overrides applied on top of a `ReportDefinition` and `AppContext`."""

    pfetl_run_id: str | None = None
    period_request: PeriodRequest | None = None
    filters: FilterSet | None = None
    currency_mode: CurrencyMode | None = None


class ProvenanceChange(BaseModel):
    """Single provenance field difference between a prior and rerun `ReportContext`."""

    model_config = ConfigDict(frozen=True)

    field: str
    label: str
    before: str
    after: str


def compare_report_provenance(
    prior: ReportContext,
    current: ReportContext,
) -> tuple[ProvenanceChange, ...]:
    """Compare two `ReportContext` stamps and return changed provenance fields."""
    pairs: tuple[tuple[str, str, str | None, str | None], ...] = (
        ("pfetl_run_id", "PFETL Run", prior.pfetl_run_id, current.pfetl_run_id),
        ("core_version", "Core Version", prior.core_version, current.core_version),
        ("schema_version", "Schema Version", prior.schema_version, current.schema_version),
        (
            "settings_snapshot_id",
            "Settings Snapshot",
            prior.settings_snapshot_id,
            current.settings_snapshot_id,
        ),
        ("rules_snapshot_id", "Rules Snapshot", prior.rules_snapshot_id, current.rules_snapshot_id),
        ("period", "Period", prior.period.label, current.period.label),
        ("currency_mode", "Currency Mode", prior.currency_mode, current.currency_mode),
    )
    changes: list[ProvenanceChange] = []
    for field, label, before_val, after_val in pairs:
        b_str = before_val or "—"
        a_str = after_val or "—"
        if b_str != a_str:
            changes.append(ProvenanceChange(field=field, label=label, before=b_str, after=a_str))
    return tuple(changes)


def _merge_filters(base: FilterSet, override: FilterSet | None) -> FilterSet:
    """Apply explicit block-level filter override over global report filters without widening."""
    if override is None or not override.filters:
        return base
    merged = {spec.field: spec for spec in base.filters}
    for spec in override.filters:
        merged[spec.field] = spec
    return FilterSet(filters=tuple(merged.values()))


def _resolve_period_context(
    app_context: AppContext,
    period_request: PeriodRequest,
) -> PeriodContext:
    """Resolve `PeriodContext` via `silver.dim_calendar`, or fallback safely if DB is absent."""
    if app_context.period_context is not None and period_request == app_context.period_request:
        return app_context.period_context

    if app_context.profile is not None and app_context.profile.duckdb_path.exists():
        reader = DuckDBReader(app_context.profile)
        return PeriodResolver(reader).resolve(period_request)

    if app_context.period_context is not None:
        return app_context.period_context

    today = date.today()
    start = period_request.custom_start or date(today.year, today.month, 1)
    end = period_request.custom_end or today
    return PeriodContext(
        preset=period_request.preset,
        label=f"{start.isoformat()} – {end.isoformat()}",
        start_date=start,
        end_date=end,
    )


def _resolve_provenance(
    app_context: AppContext,
    explicit_run_id: str | None,
) -> RunProvenance | None:
    """Resolve `RunProvenance` for the requested PFETL run ID."""
    if explicit_run_id and app_context.profile is not None:
        if (
            app_context.analytical_provenance is not None
            and app_context.analytical_provenance.run_id == explicit_run_id
        ):
            return app_context.analytical_provenance
        if app_context.provenance is not None and app_context.provenance.run_id == explicit_run_id:
            return app_context.provenance
        if app_context.profile.control_plane_path.exists():
            return PFETLCatalog(app_context.profile).get_run_provenance(explicit_run_id)
    return app_context.analytical_provenance or app_context.provenance


class ReportService:
    """Deterministic, exporter-neutral report execution runtime."""

    def __init__(
        self,
        registry: ReportRegistry = DEFAULT_REPORT_REGISTRY,
        cache: QueryCache | None = None,
    ) -> None:
        self.registry = registry
        self.cache = cache if cache is not None else QueryCache(max_entries=128)

    def resolve_context(
        self,
        app_context: AppContext,
        *,
        definition: ReportDefinition | None = None,
        overrides: ReportContextOverrides | None = None,
        generated_at: datetime | None = None,
    ) -> ReportContext:
        """Resolve one run-pinned `ReportContext` from `AppContext` and optional overrides."""
        if app_context.profile is None:
            raise ReportResolutionError(
                "An active PFETL profile is required to resolve a ReportContext."
            )

        ovr = overrides or ReportContextOverrides()
        prov = _resolve_provenance(app_context, ovr.pfetl_run_id)
        pfetl_run_id = (
            ovr.pfetl_run_id
            or (prov.run_id if prov is not None else None)
            or app_context.effective_run_id
            or "latest"
        )

        period_req = (
            ovr.period_request
            or (definition.default_period if definition is not None else None)
            or app_context.period_request
            or PeriodRequest(preset=PeriodPreset.LATEST_COMPLETE_MONTH)
        )
        period_ctx = _resolve_period_context(app_context, period_req)

        base_filters = definition.default_filters if definition is not None else app_context.filters
        effective_filters = _merge_filters(base_filters, ovr.filters)
        currency_mode: CurrencyMode = ovr.currency_mode or (
            definition.currency_mode if definition is not None else "base"
        )

        return ReportContext(
            profile_id=app_context.profile.profile_id,
            pfetl_run_id=pfetl_run_id,
            core_version=prov.application_version if prov is not None else None,
            schema_version=prov.schema_version if prov is not None else None,
            settings_snapshot_id=prov.settings_snapshot_id if prov is not None else None,
            rules_snapshot_id=prov.rules_snapshot_id if prov is not None else None,
            period=period_ctx,
            filters=effective_filters,
            studio_version=STUDIO_VERSION,
            generated_at=generated_at or datetime.now(UTC),
            currency_mode=currency_mode,
        )

    def render(
        self,
        definition: ReportDefinition,
        app_context: AppContext,
        *,
        context_overrides: ReportContextOverrides | None = None,
        record_history: bool = False,
        state_db_path: Path | None = None,
        output_format: str = "preview",
    ) -> ReportResult:
        """Execute all blocks of `definition` in order and return `ReportResult`."""
        started = time.perf_counter()
        report_ctx = self.resolve_context(
            app_context,
            definition=definition,
            overrides=context_overrides,
        )

        rendered_blocks: list[RenderedBlock] = []
        warnings: list[ReportWarning] = []

        for block in definition.blocks:
            rendered, block_warning = self._execute_block(
                block=block,
                app_context=app_context,
                report_ctx=report_ctx,
                state_db_path=state_db_path,
            )
            rendered_blocks.append(rendered)
            if block_warning is not None:
                warnings.append(block_warning)

        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)

        if record_history:
            with StudioStateStore(db_path=state_db_path) as store:
                store.record_report_run(
                    report_id=definition.id,
                    pfetl_run_id=report_ctx.pfetl_run_id,
                    period_json=report_ctx.period.model_dump_json(),
                    output_format=output_format,
                    generated_at=report_ctx.generated_at,
                )

        return ReportResult(
            definition_id=definition.id,
            definition_name=definition.name,
            context=report_ctx,
            blocks=tuple(rendered_blocks),
            warnings=tuple(warnings),
            duration_ms=duration_ms,
        )

    def render_saved_report(
        self,
        report_id: str,
        app_context: AppContext,
        *,
        state_db_path: Path | None = None,
        context_overrides: ReportContextOverrides | None = None,
        record_history: bool = True,
        output_format: str = "preview",
    ) -> ReportResult:
        """Load a saved custom report by `report_id` and execute it via `render`."""
        with StudioStateStore(db_path=state_db_path) as store:
            saved = store.get_report_definition(report_id)
        if saved is None:
            raise ReportResolutionError(
                f"Saved report '{report_id}' was not found in Studio state."
            )
        definition = ReportDefinition.model_validate_json(saved.definition_json)
        return self.render(
            definition,
            app_context,
            context_overrides=context_overrides,
            record_history=record_history,
            state_db_path=state_db_path,
            output_format=output_format,
        )

    def _execute_block(
        self,
        *,
        block: object,
        app_context: AppContext,
        report_ctx: ReportContext,
        state_db_path: Path | None,
    ) -> tuple[RenderedBlock, ReportWarning | None]:
        """Dispatch a single `ReportBlock` to static rendering, registry source, or saved view."""
        if isinstance(block, TitleBlock):
            return (
                RenderedTextBlock(
                    block_type="title",
                    block_id=block.block_id,
                    heading=block.title,
                    body=block.subtitle,
                ),
                None,
            )
        if isinstance(block, SubtitleBlock):
            return (
                RenderedTextBlock(
                    block_type="subtitle",
                    block_id=block.block_id,
                    heading=block.text,
                ),
                None,
            )
        if isinstance(block, NarrativeBlock):
            return (
                RenderedTextBlock(
                    block_type="narrative",
                    block_id=block.block_id,
                    heading=block.heading,
                    body=block.body,
                ),
                None,
            )
        if isinstance(block, ProvenanceBlock):
            return (
                RenderedProvenanceBlock(
                    block_id=block.block_id,
                    profile_id=report_ctx.profile_id,
                    pfetl_run_id=report_ctx.pfetl_run_id,
                    core_version=report_ctx.core_version,
                    schema_version=report_ctx.schema_version,
                    settings_snapshot_id=(
                        report_ctx.settings_snapshot_id if block.show_snapshots else None
                    ),
                    rules_snapshot_id=(
                        report_ctx.rules_snapshot_id if block.show_snapshots else None
                    ),
                    period_label=report_ctx.period.label,
                    studio_version=report_ctx.studio_version,
                    generated_at=report_ctx.generated_at,
                    currency_mode=report_ctx.currency_mode,
                    disclaimer_text=block.disclaimer_text if block.show_disclaimer else "",
                ),
                None,
            )
        if isinstance(block, DividerBlock):
            return (
                RenderedStructuralBlock(
                    block_type="divider",
                    block_id=block.block_id,
                    label=block.label,
                ),
                None,
            )
        if isinstance(block, PageBreakBlock):
            return (
                RenderedStructuralBlock(
                    block_type="page_break",
                    block_id=block.block_id,
                ),
                None,
            )
        if isinstance(block, (KPIGroupBlock, ChartBlock, TableBlock)):
            return self._execute_registered_source_block(
                block=block,
                app_context=app_context,
                report_ctx=report_ctx,
            )
        if isinstance(block, SavedViewBlock):
            return self._execute_saved_view_block(
                block=block,
                app_context=app_context,
                report_ctx=report_ctx,
                state_db_path=state_db_path,
            )
        raise TypeError(f"Unsupported report block type: {type(block)!r}")

    def _effective_block_context(
        self,
        *,
        app_context: AppContext,
        report_ctx: ReportContext,
        period_override: PeriodRequest | None,
        filter_override: FilterSet | None,
    ) -> ReportContext:
        """Apply optional block-level period/filter overrides within the same run."""
        if period_override is None and filter_override is None:
            return report_ctx
        period_ctx = (
            _resolve_period_context(app_context, period_override)
            if period_override is not None
            else report_ctx.period
        )
        filters = _merge_filters(report_ctx.filters, filter_override)
        return report_ctx.model_copy(update={"period": period_ctx, "filters": filters})

    def _execute_registered_source_block(
        self,
        *,
        block: KPIGroupBlock | ChartBlock | TableBlock,
        app_context: AppContext,
        report_ctx: ReportContext,
    ) -> tuple[RenderedBlock, ReportWarning | None]:
        """Resolve and invoke a registered analytics block source."""
        expected_kind = (
            ReportSourceKind.KPI_GROUP
            if isinstance(block, KPIGroupBlock)
            else (
                ReportSourceKind.CHART if isinstance(block, ChartBlock) else ReportSourceKind.TABLE
            )
        )
        source = self.registry.get_source(block.source_id)
        if source is None or source.kind != expected_kind:
            msg = (
                f"Registered source '{block.source_id}' ({expected_kind.value}) "
                "was not found in ReportRegistry."
            )
            remediation = "Verify the block's source_id against registered report sources or remove the block."
            return self._handle_block_failure(
                block_id=block.block_id,
                block_type=block.block_type,
                reference_id=block.source_id,
                code="MISSING_SOURCE",
                message=msg,
                remediation=remediation,
                required=block.required,
            )

        effective_ctx = self._effective_block_context(
            app_context=app_context,
            report_ctx=report_ctx,
            period_override=block.period_override,
            filter_override=block.filter_override,
        )
        max_rows = block.max_rows if isinstance(block, TableBlock) else 200
        source_ctx = ReportSourceContext(
            app_context=app_context,
            report_context=effective_ctx,
            cache=self.cache,
            title_override=block.title,
            max_rows=max_rows,
        )
        try:
            rendered = source.executor(source_ctx, block.block_id)
            return rendered, None
        except Exception as exc:  # noqa: BLE001
            return self._handle_block_failure(
                block_id=block.block_id,
                block_type=block.block_type,
                reference_id=block.source_id,
                code="SOURCE_EXECUTION_FAILED",
                message=f"Source '{block.source_id}' failed: {exc}",
                remediation=(
                    "Confirm profile compatibility and that required PFETL tables exist for this run."
                ),
                required=block.required,
            )

    def _execute_saved_view_block(
        self,
        *,
        block: SavedViewBlock,
        app_context: AppContext,
        report_ctx: ReportContext,
        state_db_path: Path | None,
    ) -> tuple[RenderedBlock, ReportWarning | None]:
        """Replay a validated Explorer Saved View into a `RenderedTableBlock`."""
        with StudioStateStore(db_path=state_db_path) as store:
            saved_view = store.get_saved_view(block.view_id)

        if saved_view is None:
            return self._handle_block_failure(
                block_id=block.block_id,
                block_type=block.block_type,
                reference_id=block.view_id,
                code="SAVED_VIEW_NOT_FOUND",
                message=f"Saved view '{block.view_id}' no longer exists in Studio state.",
                remediation="Edit this report to point to an active Saved View or remove the block.",
                required=block.required,
            )

        profile = app_context.profile
        if profile is None:
            return self._handle_block_failure(
                block_id=block.block_id,
                block_type=block.block_type,
                reference_id=block.view_id,
                code="NO_ACTIVE_PROFILE",
                message="Cannot replay Saved View without an active PFETL profile.",
                remediation="Open a PFETL profile before rendering this report.",
                required=block.required,
            )

        effective_ctx = self._effective_block_context(
            app_context=app_context,
            report_ctx=report_ctx,
            period_override=block.period_override,
            filter_override=block.filter_override,
        )
        period_req = block.period_override or PeriodRequest(preset=effective_ctx.period.preset)
        try:
            replay = ExplorerAnalytics.replay_saved_view(
                profile=profile,
                view=saved_view,
                run_id=effective_ctx.pfetl_run_id,
                catalog_snapshot=app_context.catalog_snapshot,
                cache=self.cache,
                global_period=period_req,
                global_filters=effective_ctx.filters,
                sql_limit=block.max_rows,
            )
            return self._saved_view_replay_to_table(block, saved_view.name, replay), None
        except Exception as exc:  # noqa: BLE001
            return self._handle_block_failure(
                block_id=block.block_id,
                block_type=block.block_type,
                reference_id=block.view_id,
                code="SAVED_VIEW_EXECUTION_FAILED",
                message=f"Saved view '{saved_view.name}' failed to execute: {exc}",
                remediation="Inspect the Saved View in Explorer and ensure its columns/tables match.",
                required=block.required,
            )

    @staticmethod
    def _saved_view_replay_to_table(
        block: SavedViewBlock,
        view_name: str,
        replay: GuidedQueryResult | SqlQueryResult,
    ) -> RenderedTableBlock:
        """Convert Guided or SQL Saved View replay output into `RenderedTableBlock`."""
        if isinstance(replay, GuidedQueryResult):
            rows = tuple(replay.rows[: block.max_rows])
            return RenderedTableBlock(
                block_id=block.block_id,
                source_id=f"saved_view:{block.view_id}",
                title=block.title or view_name,
                columns=tuple(replay.columns),
                rows=rows,
                total_rows=replay.row_count,
                truncated=replay.truncated or replay.row_count > len(rows),
                source_tables=(replay.dataset.physical_table,),
            )
        rows = tuple(replay.rows[: block.max_rows])
        return RenderedTableBlock(
            block_id=block.block_id,
            source_id=f"saved_view:{block.view_id}",
            title=block.title or view_name,
            columns=tuple(replay.columns),
            rows=rows,
            total_rows=replay.row_count,
            truncated=replay.truncated or replay.row_count > len(rows),
            source_tables=(f"{replay.datasource} (read-only SQL)",),
        )

    @staticmethod
    def _handle_block_failure(
        *,
        block_id: str,
        block_type: str,
        reference_id: str,
        code: str,
        message: str,
        remediation: str,
        required: bool,
    ) -> tuple[RenderedBrokenBlock, ReportWarning]:
        """Raise `RequiredReportBlockError` if required, else return broken placeholder + warning."""
        if required:
            raise RequiredReportBlockError(f"[{code}] Block '{block_id}': {message}")
        warning = ReportWarning(
            block_id=block_id,
            code=code,
            message=message,
            remediation=remediation,
        )
        broken = RenderedBrokenBlock(
            block_id=block_id,
            original_block_type=block_type,
            reference_id=reference_id,
            code=code,
            message=message,
            remediation=remediation,
        )
        return broken, warning
