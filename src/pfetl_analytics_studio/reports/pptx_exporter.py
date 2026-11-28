"""Editable 16:9 PowerPoint (`.pptx`) exporter for PFETL Analytics Studio `ReportResult`.

Owns
----
- `build_pptx_deck` generating native editable PowerPoint shapes, KPI cards, tables,
  native charts (for single-axis bar/line) and high-DPI labeled chart images (for combo charts).

Does not own
------------
- DuckDB/Control Plane SQL execution, NiceGUI widgets, or PFETL financial methodology.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from pfetl_analytics_studio.reports.models import (
    RenderedChartBlock,
    RenderedKPIGroupBlock,
    RenderedProvenanceBlock,
    RenderedStructuralBlock,
    RenderedTableBlock,
    RenderedTextBlock,
    ReportContext,
    ReportResult,
)


def _rgb(r: int, g: int, b: int) -> Any:
    """Construct `RGBColor` safely across untyped `python-pptx` initializers."""
    ctor: Any = RGBColor
    return ctor(r, g, b)


_SLIDE_W = Inches(13.333)
_SLIDE_H = Inches(7.5)
_NAVY = _rgb(0x1E, 0x3A, 0x8A)
_SLATE_DARK = _rgb(0x0F, 0x17, 0x2A)
_SLATE_MUTED = _rgb(0x47, 0x55, 0x69)
_HEADER_BG = _rgb(0x1E, 0x29, 0x3B)
_CARD_BG = _rgb(0xF8, 0xFA, 0xFC)
_BORDER_GRAY = _rgb(0xCB, 0xD5, 0xE1)
_AMBER_BG = _rgb(0xFE, 0xF3, 0xC7)
_AMBER_TEXT = _rgb(0x92, 0x40, 0x0E)
_WHITE = _rgb(0xFF, 0xFF, 0xFF)


def _add_slide_footer(slide: Any, result: ReportResult, slide_num: int) -> None:
    """Add an editable provenance footer to the bottom of a PowerPoint slide."""
    box = slide.shapes.add_textbox(Inches(0.6), Inches(7.08), Inches(12.1), Inches(0.32))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = (
        f"{result.definition_id}  |  PFETL Run: {result.context.pfetl_run_id}  |  "
        f"Period: {result.context.period.label}  |  Studio v{result.context.studio_version}  |  "
        f"Slide {slide_num}"
    )
    p.font.size = Pt(8.5)
    p.font.color.rgb = _SLATE_MUTED


def _add_slide_header(slide: Any, title: str, subtitle: str = "") -> None:
    """Add a clean editable slide title and optional subtitle."""
    accent_bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), _SLIDE_W, Inches(0.12)
    )
    accent_bar.fill.solid()
    accent_bar.fill.fore_color.rgb = _NAVY
    accent_bar.line.fill.background()

    title_box = slide.shapes.add_textbox(Inches(0.6), Inches(0.28), Inches(12.1), Inches(0.55))
    tf = title_box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(22)
    p.font.bold = True
    p.font.color.rgb = _SLATE_DARK

    if subtitle:
        sub_box = slide.shapes.add_textbox(Inches(0.6), Inches(0.82), Inches(12.1), Inches(0.35))
        stf = sub_box.text_frame
        stf.word_wrap = True
        sp = stf.paragraphs[0]
        sp.text = subtitle
        sp.font.size = Pt(11.5)
        sp.font.color.rgb = _SLATE_MUTED


def _format_cell_text(val: Any) -> tuple[str, bool]:
    """Format table cell values cleanly for PowerPoint native tables."""
    if val is None:
        return "—", False
    if isinstance(val, bool):
        return ("Yes" if val else "No"), False
    if isinstance(val, int):
        return f"{val:,}", True
    if isinstance(val, float):
        return f"{val:,.2f}", True
    return str(val), False


def _style_pptx_table(
    table: Any,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    numeric_cols: set[int],
) -> None:
    """Apply clean header, zebra row, and alignment styling to a native PowerPoint table."""
    for c_idx, h_text in enumerate(headers):
        cell = table.cell(0, c_idx)
        cell.fill.solid()
        cell.fill.fore_color.rgb = _HEADER_BG
        p = cell.text_frame.paragraphs[0]
        p.text = h_text
        p.font.size = Pt(9.5)
        p.font.bold = True
        p.font.color.rgb = _WHITE
        p.alignment = PP_ALIGN.RIGHT if c_idx in numeric_cols else PP_ALIGN.LEFT

    for r_idx, row_vals in enumerate(rows, start=1):
        bg = _WHITE if r_idx % 2 == 1 else _CARD_BG
        for c_idx, val in enumerate(row_vals):
            cell = table.cell(r_idx, c_idx)
            cell.fill.solid()
            cell.fill.fore_color.rgb = bg
            p = cell.text_frame.paragraphs[0]
            p.text = val
            p.font.size = Pt(9)
            p.font.color.rgb = _SLATE_DARK
            p.alignment = PP_ALIGN.RIGHT if c_idx in numeric_cols else PP_ALIGN.LEFT


def _try_add_native_chart(slide: Any, block: RenderedChartBlock) -> bool:
    """Add a native editable PowerPoint chart for simple single-axis bar/line traces."""
    fig = block.figure_dict
    raw_traces: list[Any] = list(fig.get("data") or [])
    traces: list[dict[str, Any]] = [
        dict(cast(dict[str, Any], t)) for t in raw_traces if isinstance(t, dict)
    ]
    layout: dict[str, Any] = dict(fig.get("layout") or {})
    if not traces or "yaxis2" in layout:
        return False

    trace_types = {str(t.get("type") or "scatter").lower() for t in traces}
    if not trace_types.issubset({"bar", "scatter"}):
        return False
    if len(trace_types) > 1:
        return False

    first_x: list[Any] = list(traces[0].get("x") or [])
    if not first_x:
        return False
    categories = [str(x) for x in first_x]

    data_ctor: Any = CategoryChartData
    chart_data: Any = data_ctor()
    chart_data.categories = categories
    for idx, t in enumerate(traces):
        y_raw: list[Any] = list(t.get("y") or [])
        y_vals = [float(v) if isinstance(v, (int, float)) else 0.0 for v in y_raw]
        if len(y_vals) != len(categories):
            return False
        chart_data.add_series(str(t.get("name") or f"Series {idx + 1}"), y_vals)

    is_horizontal = any(str(t.get("orientation") or "").lower() == "h" for t in traces)
    if is_horizontal:
        xl_type = XL_CHART_TYPE.BAR_CLUSTERED
    elif "bar" in trace_types:
        xl_type = XL_CHART_TYPE.COLUMN_CLUSTERED
    else:
        xl_type = XL_CHART_TYPE.LINE_MARKERS
    chart_frame = slide.shapes.add_chart(
        xl_type,
        Inches(0.6),
        Inches(1.25),
        Inches(12.1),
        Inches(5.35),
        chart_data,
    )
    chart = chart_frame.chart
    chart.has_legend = len(traces) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.TOP
        chart.legend.font.size = Pt(10)
    chart.category_axis.tick_labels.font.size = Pt(9)
    chart.value_axis.tick_labels.font.size = Pt(9)
    plot = chart.plots[0]
    plot.has_data_labels = len(categories) <= 16
    if plot.has_data_labels:
        plot.data_labels.show_value = True
        plot.data_labels.font.size = Pt(8.5)
    return True


def _add_provenance_slide(
    prs: Any,
    result: ReportResult,
    title_text: str,
    subtitle_text: str,
    prov_rows: list[list[str]],
    slide_num: int,
    *,
    notes: Sequence[tuple[str, str]] = (),
) -> None:
    """Create the editable Cover + Provenance Audit slide with optional methodology callout."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_slide_header(slide, title_text, subtitle_text)

    tbl_h = Inches(4.25 if notes else 5.2)
    tbl_shape = slide.shapes.add_table(
        len(prov_rows) + 1,
        2,
        Inches(0.6),
        Inches(1.28),
        Inches(12.1),
        tbl_h,
    )
    table = tbl_shape.table
    table.columns[0].width = Inches(2.8)
    table.columns[1].width = Inches(9.3)
    _style_pptx_table(table, ["Provenance Field", "Value"], prov_rows, set())

    if notes:
        callout = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(0.6),
            Inches(5.8),
            Inches(12.1),
            Inches(1.1),
        )
        callout.fill.solid()
        callout.fill.fore_color.rgb = _CARD_BG
        callout.line.color.rgb = _BORDER_GRAY
        tf = callout.text_frame
        tf.word_wrap = True
        tf.margin_left = Inches(0.16)
        tf.margin_right = Inches(0.16)
        tf.margin_top = Inches(0.1)
        first_para = True
        for heading, body in notes[:2]:
            p = tf.paragraphs[0] if first_para else tf.add_paragraph()
            first_para = False
            p.text = f"{heading}: {body}" if heading and body else (heading or body)
            p.font.size = Pt(10.5)
            p.font.color.rgb = _SLATE_DARK

    _add_slide_footer(slide, result, slide_num)


def _add_kpi_slide(
    prs: Any,
    result: ReportResult,
    block: RenderedKPIGroupBlock,
    slide_num: int,
) -> int:
    """Create a 4-up editable KPI cards slide."""
    slide_num += 1
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_slide_header(slide, block.title, f"Source: {', '.join(block.source_tables)}")
    cols_per_row = 4
    card_w = Inches(2.85)
    card_h = Inches(1.55)
    gap_x = Inches(0.23)
    gap_y = Inches(0.25)
    for idx, item in enumerate(block.items[:12]):
        r_i, c_i = divmod(idx, cols_per_row)
        left = Inches(0.6) + c_i * (card_w + gap_x)
        top = Inches(1.35) + r_i * (card_h + gap_y)
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, card_w, card_h)
        card.fill.solid()
        card.fill.fore_color.rgb = _CARD_BG
        card.line.color.rgb = _BORDER_GRAY
        tf = card.text_frame
        tf.word_wrap = True
        tf.margin_left = Inches(0.14)
        tf.margin_right = Inches(0.14)
        tf.margin_top = Inches(0.12)
        p_lbl = tf.paragraphs[0]
        p_lbl.text = item.label
        p_lbl.font.size = Pt(10)
        p_lbl.font.bold = True
        p_lbl.font.color.rgb = _SLATE_MUTED
        p_val = tf.add_paragraph()
        p_val.text = item.formatted_value
        p_val.font.size = Pt(22)
        p_val.font.bold = True
        p_val.font.color.rgb = _SLATE_DARK
        if item.delta_text:
            p_delta = tf.add_paragraph()
            p_delta.text = item.delta_text
            p_delta.font.size = Pt(8.5)
            p_delta.font.bold = True
            p_delta.font.color.rgb = _HEADER_BG
        extra = (
            " | ".join(f"{k}: {v}" for k, v in item.breakups[:2])
            if item.breakups
            else item.subtitle
        )
        if extra:
            p_sub = tf.add_paragraph()
            p_sub.text = extra
            p_sub.font.size = Pt(8.0)
            p_sub.font.color.rgb = _SLATE_MUTED
    _add_slide_footer(slide, result, slide_num)
    return slide_num


def _add_chart_slide(
    prs: Any,
    result: ReportResult,
    block: RenderedChartBlock,
    slide_num: int,
    *,
    prepare_chart_fn: Callable[[dict[str, Any], str], dict[str, Any]],
    render_chart_png_fn: Callable[..., bytes],
) -> int:
    """Create a chart slide with native editable chart or high-DPI labeled PNG."""
    slide_num += 1
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_slide_header(slide, block.title, block.caption)
    if block.figure_dict:
        added_native = _try_add_native_chart(slide, block)
        if not added_native:
            light_fig = prepare_chart_fn(block.figure_dict, block.title)
            png_bytes = render_chart_png_fn(
                light_fig,
                image_format="png",
                width=1280,
                height=600,
                scale=2.0,
            )
            slide.shapes.add_picture(
                io.BytesIO(png_bytes),
                Inches(0.6),
                Inches(1.25),
                width=Inches(12.1),
                height=Inches(5.65),
            )
    _add_slide_footer(slide, result, slide_num)
    return slide_num


def _add_table_slides(
    prs: Any,
    result: ReportResult,
    block: RenderedTableBlock,
    slide_num: int,
) -> int:
    """Create paginated native editable PowerPoint table slides."""
    cols = list(block.columns[:10])
    if not cols or not block.rows:
        return slide_num
    rows_per_slide = 14
    for offset in range(0, min(len(block.rows), 140), rows_per_slide):
        chunk = block.rows[offset : offset + rows_per_slide]
        slide_num += 1
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        page_suffix = (
            f" (Rows {offset + 1}–{offset + len(chunk)} of {block.total_rows})"
            if len(block.rows) > rows_per_slide
            else f" ({block.total_rows} rows)"
        )
        _add_slide_header(
            slide,
            f"{block.title}{page_suffix}",
            f"Source: {', '.join(block.source_tables)}",
        )
        numeric_cols: set[int] = set()
        formatted_rows: list[list[str]] = []
        for row in chunk:
            row_cells: list[str] = []
            for c_i, col in enumerate(cols):
                txt, is_num = _format_cell_text(row.get(col))
                if is_num:
                    numeric_cols.add(c_i)
                row_cells.append(txt)
            formatted_rows.append(row_cells)
        tbl_shape = slide.shapes.add_table(
            len(formatted_rows) + 1,
            len(cols),
            Inches(0.6),
            Inches(1.28),
            Inches(12.1),
            Inches(0.36 * (len(formatted_rows) + 1)),
        )
        _style_pptx_table(tbl_shape.table, cols, formatted_rows, numeric_cols)
        _add_slide_footer(slide, result, slide_num)
    return slide_num


def _add_broken_slide(
    prs: Any,
    result: ReportResult,
    block: Any,
    slide_num: int,
) -> int:
    """Create an editable warning slide for a broken or missing non-required block."""
    slide_num += 1
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_slide_header(slide, f"Unavailable Block: {block.block_id}", block.code)
    box = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(0.6),
        Inches(1.5),
        Inches(12.1),
        Inches(1.6),
    )
    box.fill.solid()
    box.fill.fore_color.rgb = _AMBER_BG
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = f"{block.message}\nRemediation: {block.remediation}"
    p.font.size = Pt(12)
    p.font.color.rgb = _AMBER_TEXT
    _add_slide_footer(slide, result, slide_num)
    return slide_num


def build_pptx_deck(
    result: ReportResult,
    output_path: Path,
    *,
    provenance_rows_fn: Callable[[ReportContext, str], list[list[str]]],
    prepare_chart_fn: Callable[[dict[str, Any], str], dict[str, Any]],
    render_chart_png_fn: Callable[..., bytes],
) -> None:
    """Build an editable 16:9 PowerPoint presentation from `ReportResult`."""
    prs: Any = Presentation()
    prs.slide_width = _SLIDE_W
    prs.slide_height = _SLIDE_H

    cover_title = result.definition_name
    cover_sub = f"PFETL Run {result.context.pfetl_run_id} · {result.context.period.label}"
    disclaimer = ""
    cover_notes: list[tuple[str, str]] = []
    for b in result.blocks:
        if isinstance(b, RenderedTextBlock):
            if b.block_type == "title":
                cover_title = b.heading
                if b.body:
                    cover_sub = b.body
            elif b.block_type == "narrative" and b.body:
                cover_notes.append((b.heading, b.body))
        elif isinstance(b, RenderedProvenanceBlock) and b.disclaimer_text:
            disclaimer = b.disclaimer_text

    slide_num = 1
    _add_provenance_slide(
        prs,
        result,
        cover_title,
        cover_sub,
        provenance_rows_fn(result.context, disclaimer),
        slide_num,
        notes=cover_notes,
    )

    for block in result.blocks:
        if isinstance(block, (RenderedTextBlock, RenderedProvenanceBlock, RenderedStructuralBlock)):
            continue
        if isinstance(block, RenderedKPIGroupBlock):
            slide_num = _add_kpi_slide(prs, result, block, slide_num)
        elif isinstance(block, RenderedChartBlock):
            slide_num = _add_chart_slide(
                prs,
                result,
                block,
                slide_num,
                prepare_chart_fn=prepare_chart_fn,
                render_chart_png_fn=render_chart_png_fn,
            )
        elif isinstance(block, RenderedTableBlock):
            slide_num = _add_table_slides(prs, result, block, slide_num)
        else:
            slide_num = _add_broken_slide(prs, result, block, slide_num)

    prs.save(str(output_path))
