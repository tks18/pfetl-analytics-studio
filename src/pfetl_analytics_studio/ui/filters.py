"""Reusable interactive period selector, comparison range picker, and data-backed filter bar.

Owns
----
- `period_selector()` wrapping expanded `PeriodPreset` & `ComparisonMode` strategies, custom date
  pickers for both primary & comparison windows, and rich active/comparison pill tooltips.
- `filter_chips_bar()` backed dynamically by live distinct values from `FilterCatalogOptions`.

Does not own
------------
- Calendar/filter SQL execution (`semantic/periods.py`, `semantic/filters.py`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from nicegui import ui
from nicegui.events import ValueChangeEventArguments

from pfetl_analytics_studio.semantic.filters import (
    FilterCatalogOptions,
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
)
from pfetl_analytics_studio.semantic.periods import (
    PRESET_LABELS,
    ComparisonMode,
    PeriodCatalogOptions,
    PeriodContext,
    PeriodPreset,
    PeriodRequest,
    format_comparison_label,
)
from pfetl_analytics_studio.ui.cards import BadgeTone, status_badge
from pfetl_analytics_studio.ui.theme import StudioColor

_COMPARISON_LABELS: dict[ComparisonMode, str] = {
    ComparisonMode.NONE: "No Comparison",
    ComparisonMode.PREVIOUS_PERIOD: "Previous Equivalent Period",
    ComparisonMode.SAME_PERIOD_PREVIOUS_YEAR: "Same Period Prior Year (YoY)",
    ComparisonMode.FY_START_MONTH: "FY Opening Month (Baseline)",
    ComparisonMode.PRIOR_COMPLETED_FY: "Previous Completed FY",
    ComparisonMode.CUSTOM: "Custom Comparison Dates",
}

_FILTER_FIELD_LABELS: dict[FilterField, str] = {
    FilterField.FY: "Financial Year (FY)",
    FilterField.ASSET_GROUP: "Asset Group",
    FilterField.ASSET_ID: "Asset / Account",
    FilterField.INCOME_GROUP: "Income Group",
    FilterField.INCOME_CATEGORY: "Income Category",
    FilterField.EXPENSE_GROUP: "Expense Category / Group",
    FilterField.EXPENSE_CATEGORY: "Expense Item",
    FilterField.IS_CORE_EXPENSE: "Core Expense Only (True/False)",
    FilterField.IS_ACTIVE_INCOME: "Active Income (True/False)",
    FilterField.IS_PASSIVE_INCOME: "Passive Income (True/False)",
    FilterField.INSTRUMENT_CLASS: "Instrument Class",
    FilterField.SECTOR: "Sector / Asset Category",
    FilterField.CURRENCY_ID: "Currency",
    FilterField.ISIN: "ISIN",
    FilterField.INCOME_HEAD: "Tax Income Head",
    FilterField.TAX_STATUS: "Tax Status",
}


def _parse_iso_date(raw: str) -> date | None:
    """Parse `YYYY-MM-DD` string cleanly without raising."""
    text = raw.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _safe_date_bounds(opts: PeriodCatalogOptions | None) -> tuple[date, date]:
    """Return non-null default start/end bounds from catalog options or current month."""
    today = date.today()
    s = opts.default_custom_start if opts and opts.default_custom_start else today.replace(day=1)
    e = opts.default_custom_end if opts and opts.default_custom_end else today
    return s, e


def _build_request_for_preset(
    preset: PeriodPreset,
    base_req: PeriodRequest,
    opts: PeriodCatalogOptions | None,
    comparison: ComparisonMode,
) -> PeriodRequest:
    """Construct a valid `PeriodRequest` with safe defaults for parameterized presets."""
    default_start, default_end = _safe_date_bounds(opts)
    return PeriodRequest(
        preset=preset,
        selected_month=(
            base_req.selected_month
            if base_req.selected_month
            else (opts.default_selected_month if opts else None)
        ),
        quarter_year=(
            base_req.quarter_year
            if base_req.quarter_year
            else (opts.default_calendar_quarter if opts else None)
        ),
        fy_quarter_year=(
            base_req.fy_quarter_year
            if base_req.fy_quarter_year
            else (opts.default_fy_quarter if opts else None)
        ),
        financial_year=(
            base_req.financial_year
            if base_req.financial_year
            else (opts.default_financial_year if opts else None)
        ),
        custom_start=(base_req.custom_start or default_start)
        if preset == PeriodPreset.CUSTOM
        else None,
        custom_end=(base_req.custom_end or default_end) if preset == PeriodPreset.CUSTOM else None,
        comparison_mode=comparison,
        custom_comparison_start=(base_req.custom_comparison_start or default_start)
        if comparison == ComparisonMode.CUSTOM
        else None,
        custom_comparison_end=(base_req.custom_comparison_end or default_end)
        if comparison == ComparisonMode.CUSTOM
        else None,
    )


def _render_contextual_subpicker(
    req: PeriodRequest,
    opts: PeriodCatalogOptions | None,
    emit_req: Callable[[PeriodRequest], None],
) -> None:
    """Render month, quarter, FY, or custom start/end controls matching the active preset."""
    if req.preset == PeriodPreset.SELECTED_MONTH:
        month_opts: dict[str, str] = dict(opts.months) if opts and opts.months else {}
        if req.selected_month and req.selected_month not in month_opts:
            month_opts[req.selected_month] = req.selected_month
        if not month_opts:
            month_opts = {req.selected_month or "latest": req.selected_month or "Current Month"}
        cur_m = (
            req.selected_month
            if req.selected_month in month_opts
            else (opts.default_selected_month if opts else next(iter(month_opts)))
        )

        def _on_month(evt: ValueChangeEventArguments[str]) -> None:
            if evt.value and str(evt.value) != "latest":
                emit_req(req.model_copy(update={"selected_month": str(evt.value)}))

        ui.select(
            options=month_opts,
            value=cur_m,
            label="Month",
            on_change=_on_month,
        ).props("dense filled dark options-dark").classes("studio-field min-w-[190px]")
        return

    if req.preset == PeriodPreset.SELECTED_FY:
        fy_list = list(opts.financial_years) if opts and opts.financial_years else []
        if req.financial_year and req.financial_year not in fy_list:
            fy_list.insert(0, req.financial_year)
        if not fy_list:
            return
        cur_fy = (
            req.financial_year
            if req.financial_year in fy_list
            else (opts.default_financial_year if opts else fy_list[0])
        )

        def _on_fy(evt: ValueChangeEventArguments[str]) -> None:
            if evt.value:
                emit_req(req.model_copy(update={"financial_year": str(evt.value)}))

        ui.select(
            options={fy: f"FY {fy}" for fy in fy_list},
            value=cur_fy,
            label="Financial Year",
            on_change=_on_fy,
        ).props("dense filled dark options-dark").classes("studio-field min-w-[145px]")
        return

    if req.preset == PeriodPreset.CALENDAR_QUARTER:
        cq_list = list(opts.calendar_quarters) if opts and opts.calendar_quarters else []
        if not cq_list:
            return
        cur_cq = req.quarter_year if req.quarter_year in cq_list else cq_list[0]

        def _on_cq(evt: ValueChangeEventArguments[str]) -> None:
            if evt.value:
                emit_req(req.model_copy(update={"quarter_year": str(evt.value)}))

        ui.select(
            options={q: q for q in cq_list},
            value=cur_cq,
            label="Quarter",
            on_change=_on_cq,
        ).props("dense filled dark options-dark").classes("studio-field min-w-[130px]")
        return

    if req.preset == PeriodPreset.FY_QUARTER:
        fq_list = list(opts.fy_quarters) if opts and opts.fy_quarters else []
        if not fq_list:
            return
        cur_fq = req.fy_quarter_year if req.fy_quarter_year in fq_list else fq_list[0]

        def _on_fq(evt: ValueChangeEventArguments[str]) -> None:
            if evt.value:
                emit_req(req.model_copy(update={"fy_quarter_year": str(evt.value)}))

        ui.select(
            options={q: q for q in fq_list},
            value=cur_fq,
            label="FY Quarter",
            on_change=_on_fq,
        ).props("dense filled dark options-dark").classes("studio-field min-w-[140px]")
        return

    if req.preset == PeriodPreset.CUSTOM:
        s_def, e_def = _safe_date_bounds(opts)
        s_init = req.custom_start or s_def
        e_init = req.custom_end or e_def
        start_input = (
            ui.input(label="Start", value=s_init.isoformat())
            .props("type=date dense filled dark hide-bottom-space")
            .classes("studio-field w-[150px] text-xs font-mono")
        )
        end_input = (
            ui.input(label="End", value=e_init.isoformat())
            .props("type=date dense filled dark hide-bottom-space")
            .classes("studio-field w-[150px] text-xs font-mono")
        )

        def _apply_custom() -> None:
            s_val = _parse_iso_date(str(start_input.value or ""))
            e_val = _parse_iso_date(str(end_input.value or ""))
            if s_val is None or e_val is None:
                ui.notify("Enter valid Start and End dates (YYYY-MM-DD).", type="negative")
                return
            if s_val > e_val:
                ui.notify("Start date cannot be after End date.", type="negative")
                return
            emit_req(req.model_copy(update={"custom_start": s_val, "custom_end": e_val}))

        ui.button("Apply", icon="check", on_click=_apply_custom).props(
            "outline dense no-caps color=primary"
        ).classes("h-[40px] px-3 text-xs")


def _render_custom_comparison_inputs(
    req: PeriodRequest,
    opts: PeriodCatalogOptions | None,
    emit_req: Callable[[PeriodRequest], None],
) -> None:
    """Render start/end date inputs for `ComparisonMode.CUSTOM` cleanly."""
    if req.comparison_mode != ComparisonMode.CUSTOM:
        return
    s_def, e_def = _safe_date_bounds(opts)
    cs_init = req.custom_comparison_start or s_def
    ce_init = req.custom_comparison_end or e_def
    cs_input = (
        ui.input(label="Comp Start", value=cs_init.isoformat())
        .props("type=date dense filled dark hide-bottom-space")
        .classes("studio-field w-[150px] text-xs font-mono")
    )
    ce_input = (
        ui.input(label="Comp End", value=ce_init.isoformat())
        .props("type=date dense filled dark hide-bottom-space")
        .classes("studio-field w-[150px] text-xs font-mono")
    )

    def _apply_comp() -> None:
        s_val = _parse_iso_date(str(cs_input.value or ""))
        e_val = _parse_iso_date(str(ce_input.value or ""))
        if s_val is None or e_val is None:
            ui.notify("Enter valid Comparison Start and End dates.", type="negative")
            return
        if s_val > e_val:
            ui.notify("Comparison Start date cannot exceed End date.", type="negative")
            return
        emit_req(
            req.model_copy(
                update={"custom_comparison_start": s_val, "custom_comparison_end": e_val}
            )
        )

    ui.button("Apply Compare", icon="check", on_click=_apply_comp).props(
        "outline dense no-caps color=accent"
    ).classes("h-[40px] px-3 text-xs")


def _period_tooltip_text(context: PeriodContext | None, badge: str | None) -> str | None:
    """Build multi-line hover tooltip summarizing active period bounds & FY."""
    if context is None:
        return badge
    parts = [
        f"Active Preset: {PRESET_LABELS.get(context.preset, context.preset.value)}",
        f"Window: {context.start_date.isoformat() if context.start_date else '—'} to "
        f"{context.end_date.isoformat() if context.end_date else '—'}",
        f"Financial Year: {context.financial_year or '—'}",
    ]
    if context.as_of_date:
        parts.append(f"Warehouse Calendar As-Of: {context.as_of_date.isoformat()}")
    return "\n".join(parts)


def period_selector(
    current_preset: PeriodPreset = PeriodPreset.LATEST_COMPLETE_MONTH,
    on_preset_change: Callable[[PeriodPreset], None] | None = None,
    current_comparison: ComparisonMode = ComparisonMode.NONE,
    on_comparison_change: Callable[[ComparisonMode], None] | None = None,
    resolved_badge: str | None = None,
    *,
    current_request: PeriodRequest | None = None,
    on_request_change: Callable[[PeriodRequest], None] | None = None,
    period_options: PeriodCatalogOptions | None = None,
    period_context: PeriodContext | None = None,
) -> ui.row:
    """Render expanded period preset, contextual sub-pickers, comparison mode, and status chips.

    Parameters
    ----------
    current_preset : PeriodPreset, optional
        Fallback active preset when `current_request` is omitted.
    on_preset_change : Callable[[PeriodPreset], None] | None, optional
        Legacy preset-only change callback.
    current_comparison : ComparisonMode, optional
        Fallback comparison mode when `current_request` is omitted.
    on_comparison_change : Callable[[ComparisonMode], None] | None, optional
        Optional callback invoked when comparison mode changes independently.
    resolved_badge : str | None, optional
        Resolved date window label rendered inside the active-period pill.
    current_request : PeriodRequest | None, optional
        Full immutable period specification driving contextual pickers.
    on_request_change : Callable[[PeriodRequest], None] | None, optional
        Preferred full-request callback triggered by preset, sub-picker, or comparison updates.
    period_options : PeriodCatalogOptions | None, optional
        Cached calendar dropdown options discovered from `silver.dim_calendar`.
    period_context : PeriodContext | None, optional
        Resolved period context supplying comparison badge & detailed tooltips.

    Returns
    -------
    ui.row
        Constructed responsive control bar row.
    """
    active_req = (
        current_request
        if current_request is not None
        else PeriodRequest(preset=current_preset, comparison_mode=current_comparison)
    )
    preset_options: dict[str, str] = {p.value: label for p, label in PRESET_LABELS.items()}

    def _emit_request(new_req: PeriodRequest) -> None:
        if on_request_change is not None:
            on_request_change(new_req)
        elif on_preset_change is not None:
            on_preset_change(new_req.preset)

    def _handle_preset(evt: ValueChangeEventArguments[str]) -> None:
        if not evt.value:
            return
        new_preset = PeriodPreset(str(evt.value))
        updated = _build_request_for_preset(
            new_preset,
            active_req,
            period_options,
            active_req.comparison_mode,
        )
        _emit_request(updated)

    row = ui.row().classes("items-center gap-2.5 flex-wrap w-full")
    with row:
        ui.select(
            options=preset_options,
            value=active_req.preset.value,
            label="Period",
            on_change=_handle_preset,
        ).props("dense filled dark options-dark").classes("studio-field min-w-[205px]")

        _render_contextual_subpicker(active_req, period_options, _emit_request)

        if on_request_change is not None or on_comparison_change is not None:
            comp_options: dict[str, str] = {m.value: lbl for m, lbl in _COMPARISON_LABELS.items()}

            def _handle_comp(evt: ValueChangeEventArguments[str]) -> None:
                if not evt.value:
                    return
                mode = ComparisonMode(str(evt.value))
                if on_comparison_change is not None and on_request_change is None:
                    on_comparison_change(mode)
                    return
                s_def, e_def = _safe_date_bounds(period_options)
                comp_s = (
                    (active_req.custom_comparison_start or s_def)
                    if mode == ComparisonMode.CUSTOM
                    else None
                )
                comp_e = (
                    (active_req.custom_comparison_end or e_def)
                    if mode == ComparisonMode.CUSTOM
                    else None
                )
                _emit_request(
                    active_req.model_copy(
                        update={
                            "comparison_mode": mode,
                            "custom_comparison_start": comp_s,
                            "custom_comparison_end": comp_e,
                        }
                    )
                )

            ui.select(
                options=comp_options,
                value=active_req.comparison_mode.value,
                label="Compare",
                on_change=_handle_comp,
            ).props("dense filled dark options-dark").classes("studio-field min-w-[225px]")

            _render_custom_comparison_inputs(active_req, period_options, _emit_request)

        with ui.row().classes("items-center gap-2 flex-wrap"):
            if resolved_badge:
                status_badge(
                    f"Active: {resolved_badge}",
                    tone=BadgeTone.INFO,
                    tooltip=_period_tooltip_text(period_context, resolved_badge),
                )
            if period_context is not None and period_context.comparison is not None:
                comp = period_context.comparison
                comp_text = format_comparison_label(comp)
                if comp_text:
                    comp_tone = BadgeTone.WARNING if comp.is_empty else BadgeTone.RECOVERY
                    comp_tip = (
                        f"Comparison Strategy: {_COMPARISON_LABELS.get(comp.mode, comp.mode.value)}\n"
                        f"Window: {comp.start_date.isoformat() if comp.start_date else '—'} to "
                        f"{comp.end_date.isoformat() if comp.end_date else '—'}\n"
                        f"Financial Year: {comp.financial_year or '—'}"
                    )
                    status_badge(f"Compare: {comp_text}", tone=comp_tone, tooltip=comp_tip)

    return row


def _open_add_filter_dialog(
    on_add: Callable[[FilterSpec], None],
    filter_options: FilterCatalogOptions | None,
    available_fys: Sequence[str],
    allowed_fields: Sequence[FilterField] | None = None,
) -> None:
    """Open interactive dark modal with searchable data-backed value dropdowns per dimension."""
    dialog = ui.dialog()
    with dialog, ui.card().classes("studio-card w-[480px] max-w-full p-5 rounded-2xl gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Add Data Filter").classes("text-base font-semibold")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense color=white")

        allowed_set = set(allowed_fields) if allowed_fields else None
        field_opts = {
            f.value: lbl
            for f, lbl in _FILTER_FIELD_LABELS.items()
            if allowed_set is None or f in allowed_set
        }
        if not field_opts:
            field_opts = {f.value: lbl for f, lbl in _FILTER_FIELD_LABELS.items()}
        default_field = next(iter(field_opts))
        state: dict[str, str | list[str]] = {"single": "", "multi": []}

        value_slot = ui.column().classes("w-full gap-1")

        def _options_for_dim(field: FilterField) -> tuple[str, ...]:
            if filter_options is not None:
                found = filter_options.options_for(field)
                if found:
                    return found
            if field == FilterField.FY and available_fys:
                return tuple(available_fys)
            if field in (
                FilterField.IS_CORE_EXPENSE,
                FilterField.IS_ACTIVE_INCOME,
                FilterField.IS_PASSIVE_INCOME,
            ):
                return ("true", "false")
            return ()

        def _rebuild_value_control() -> None:
            value_slot.clear()
            dim = FilterField(str(field_select.value))
            op = FilterOperator(str(op_select.value))
            choices = _options_for_dim(dim)
            state["single"] = choices[0] if choices else ""
            state["multi"] = [choices[0]] if choices else []

            with value_slot:
                if choices and op in (FilterOperator.EQ, FilterOperator.IN):
                    is_multi = op == FilterOperator.IN

                    def _on_val_pick(evt: ValueChangeEventArguments[Any]) -> None:
                        if is_multi:
                            raw_val: Any = evt.value
                            if hasattr(raw_val, "__iter__") and not isinstance(
                                raw_val, (str, bytes)
                            ):
                                state["multi"] = [str(v) for v in raw_val]
                            else:
                                state["multi"] = []
                        else:
                            state["single"] = str(evt.value or "")

                    sel = ui.select(
                        options={c: c for c in choices},
                        value=state["multi"] if is_multi else state["single"],
                        label=f"Select {dim.value.replace('_', ' ').title()}",
                        multiple=is_multi,
                        with_input=len(choices) > 8,
                        on_change=_on_val_pick,
                    ).props("dense filled dark options-dark")
                    if is_multi:
                        sel.props("use-chips")
                    sel.classes("studio-field w-full")
                    ui.label(f"{len(choices)} distinct value(s) from your warehouse").classes(
                        "text-xs"
                    ).style(f"color: {StudioColor.DIM}")
                else:

                    def _on_text(evt: ValueChangeEventArguments[Any]) -> None:
                        state["single"] = str(evt.value or "")

                    ui.input(
                        label="Match Text",
                        value=str(state["single"]),
                        placeholder="Type substring or value...",
                        on_change=_on_text,
                    ).props("dense filled dark hide-bottom-space").classes(
                        "studio-field w-full font-mono text-xs"
                    )

        field_select = (
            ui.select(
                options=field_opts,
                value=default_field,
                label="Filter Dimension",
                on_change=lambda _e: _rebuild_value_control(),
            )
            .props("dense filled dark options-dark")
            .classes("studio-field w-full")
        )
        op_select = (
            ui.select(
                options={
                    FilterOperator.EQ.value: "Equals (=)",
                    FilterOperator.IN.value: "In Selected List (Multi)",
                    FilterOperator.CONTAINS.value: "Contains Text (~)",
                },
                value=FilterOperator.EQ.value,
                label="Operator",
                on_change=lambda _e: _rebuild_value_control(),
            )
            .props("dense filled dark options-dark")
            .classes("studio-field w-full")
        )
        _rebuild_value_control()

        def _parse_scalar(field: FilterField, raw: str) -> str | bool:
            if field in (
                FilterField.IS_CORE_EXPENSE,
                FilterField.IS_ACTIVE_INCOME,
                FilterField.IS_PASSIVE_INCOME,
            ) and raw.lower() in ("true", "false"):
                return raw.lower() == "true"
            return raw

        def _commit() -> None:
            dim = FilterField(str(field_select.value))
            op = FilterOperator(str(op_select.value))
            if op == FilterOperator.IN:
                picked = [str(v).strip() for v in state["multi"] if str(v).strip()]
                if not picked:
                    ui.notify("Select at least one value.", type="warning")
                    return
                spec = FilterSpec(
                    field=dim,
                    operator=FilterOperator.IN,
                    value=tuple(_parse_scalar(dim, p) for p in picked),
                )
            else:
                raw_val = str(state["single"] or "").strip()
                if not raw_val:
                    ui.notify("Select or enter a value before applying.", type="warning")
                    return
                spec = FilterSpec(
                    field=dim,
                    operator=op,
                    value=_parse_scalar(dim, raw_val),
                )
            dialog.close()
            on_add(spec)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps color=grey-5")
            ui.button("Apply Filter", icon="check", on_click=_commit).props("no-caps color=primary")

    dialog.open()


def filter_chips_bar(
    filters: FilterSet,
    on_remove: Callable[[FilterField], None] | None = None,
    on_clear: Callable[[], None] | None = None,
    *,
    on_add: Callable[[FilterSpec], None] | None = None,
    available_fys: Sequence[str] = (),
    filter_options: FilterCatalogOptions | None = None,
    allowed_fields: Sequence[FilterField] | None = None,
    scope_hint: str | None = None,
) -> ui.row:
    """Render interactive data-backed domain filter controls, removable chips, and clear action."""
    row = ui.row().classes("w-full items-center justify-between gap-2 flex-wrap")
    with row:
        with ui.row().classes("items-center gap-2 flex-wrap"):
            if on_add is not None:

                def _open_modal() -> None:
                    _open_add_filter_dialog(
                        on_add, filter_options, available_fys, allowed_fields=allowed_fields
                    )

                ui.button("Filter Data", icon="filter_list", on_click=_open_modal).props(
                    "outline dense size=sm no-caps color=primary"
                ).classes("px-3 text-xs font-semibold")

                # Quick one-click FY selector when live options exist
                fy_choices = (
                    list(filter_options.options_for(FilterField.FY))
                    if filter_options is not None and filter_options.options_for(FilterField.FY)
                    else list(available_fys)
                )
                if fy_choices:
                    active_fy = next(
                        (
                            str(f.value)
                            for f in filters.filters
                            if f.field == FilterField.FY
                            and f.operator == FilterOperator.EQ
                            and f.value is not None
                        ),
                        "__ALL__",
                    )

                    def _on_quick_fy(evt: ValueChangeEventArguments[str]) -> None:
                        val = str(evt.value or "__ALL__")
                        if val == "__ALL__":
                            if on_remove is not None:
                                on_remove(FilterField.FY)
                        else:
                            on_add(
                                FilterSpec(
                                    field=FilterField.FY,
                                    operator=FilterOperator.EQ,
                                    value=val,
                                )
                            )

                    quick_fy_opts = {
                        "__ALL__": "All FYs (Auto)",
                        **{fy: f"FY {fy}" for fy in fy_choices},
                    }
                    ui.select(
                        options=quick_fy_opts,
                        value=active_fy if active_fy in quick_fy_opts else "__ALL__",
                        label="Quick FY Scope",
                        on_change=_on_quick_fy,
                    ).props("dense filled dark options-dark").classes("studio-field min-w-[145px]")

                # Quick Instrument Class selector when live classes exist
                cls_choices = (
                    list(filter_options.options_for(FilterField.INSTRUMENT_CLASS))
                    if filter_options is not None
                    else []
                )
                if cls_choices:
                    active_cls = next(
                        (
                            str(f.value)
                            for f in filters.filters
                            if f.field == FilterField.INSTRUMENT_CLASS
                            and f.operator == FilterOperator.EQ
                            and f.value is not None
                        ),
                        "__ALL__",
                    )

                    def _on_quick_cls(evt: ValueChangeEventArguments[str]) -> None:
                        val = str(evt.value or "__ALL__")
                        if val == "__ALL__":
                            if on_remove is not None:
                                on_remove(FilterField.INSTRUMENT_CLASS)
                        else:
                            on_add(
                                FilterSpec(
                                    field=FilterField.INSTRUMENT_CLASS,
                                    operator=FilterOperator.EQ,
                                    value=val,
                                )
                            )

                    quick_cls_opts = {"__ALL__": "All Asset Classes", **{c: c for c in cls_choices}}
                    ui.select(
                        options=quick_cls_opts,
                        value=active_cls if active_cls in quick_cls_opts else "__ALL__",
                        label="Quick Class",
                        on_change=_on_quick_cls,
                    ).props("dense filled dark options-dark").classes("studio-field min-w-[155px]")

            chips = filters.to_chips()
            if not chips:
                ui.label("No active domain filters").classes("text-xs").style(
                    f"color: {StudioColor.DIM}"
                )
            else:
                for chip in chips:
                    with (
                        ui.row()
                        .classes("items-center gap-1.5 px-2.5 py-1 rounded-full text-xs")
                        .style(
                            "background-color: rgba(59, 130, 246, 0.18); "
                            "border: 1px solid rgba(96, 165, 250, 0.42);"
                        )
                    ):
                        ui.label(f"{chip.label}: {chip.value_display}").style(
                            f"color: {StudioColor.TEXT}"
                        )
                        if on_remove is not None:

                            def _make_remove(
                                target_field: FilterField = chip.field,
                            ) -> Callable[[], None]:
                                return lambda: on_remove(target_field)

                            ui.button(
                                icon="close",
                                on_click=_make_remove(),
                            ).props("flat round dense size=xs color=grey-4")

                if on_clear is not None:
                    ui.button("Clear all", on_click=on_clear).props(
                        "flat dense size=xs no-caps color=warning"
                    )

        if scope_hint:
            ui.label(scope_hint).classes("text-xs").style(f"color: {StudioColor.DIM}")

    return row
