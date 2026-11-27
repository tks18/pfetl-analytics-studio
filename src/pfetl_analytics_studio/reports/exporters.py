"""Deterministic multi-format exporters (PDF, XLSX, CSV, PNG, SVG) for ReportResult.

Owns
----
- `ReportExporter` facade producing local PDF (ReportLab + Kaleido), XLSX (XlsxWriter),
  CSV (Polars), and PNG/SVG (Kaleido) artifacts from `ReportResult` / `RenderedBlock`.
- `ExportResult`, `ExportPrerequisiteError`, `sanitize_filename`, `sanitize_excel_sheet_name`.

Does not own
------------
- DuckDB/Control Plane SQL execution, NiceGUI widgets, or PFETL financial calculations.
"""

from __future__ import annotations

import copy
import io
import re
from collections.abc import Sequence
from datetime import date as dt_date
from pathlib import Path
from typing import Any, Literal, cast

import plotly.io as pio  # type: ignore[import-untyped]
import polars as pl
import xlsxwriter  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.enums import TA_LEFT, TA_RIGHT  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4, landscape  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.lib.units import inch  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    Flowable,
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

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
from pfetl_analytics_studio.reports.pptx_exporter import build_pptx_deck
from pfetl_analytics_studio.state import StudioStateStore

ExportFormat = Literal["pdf", "xlsx", "pptx", "csv", "png", "svg"]

_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_INVALID_SHEET_RE = re.compile(r"[\[\]:*?/\\]+")


class ExportPrerequisiteError(RuntimeError):
    """Raised when a required export dependency (e.g. Chrome/Kaleido) or path fails."""


class ExportResult(BaseModel):
    """Metadata describing a completed local report or asset export."""

    model_config = ConfigDict(frozen=True)

    path: Path
    format: ExportFormat
    bytes_written: int
    warnings: tuple[str, ...] = ()


def sanitize_filename(name: str, default: str = "report") -> str:
    """Sanitize a report or asset filename stem for local Windows/POSIX filesystems."""
    cleaned = _INVALID_FILENAME_RE.sub("_", name.strip()).strip(". ")
    return cleaned or default


def sanitize_excel_sheet_name(name: str, used: set[str]) -> str:
    """Sanitize an Excel worksheet name to <=31 chars without forbidden characters."""
    base = _INVALID_SHEET_RE.sub("_", name.strip()).strip("'") or "Sheet"
    candidate = base[:31]
    counter = 2
    while candidate.lower() in used:
        suffix = f"_{counter}"
        candidate = f"{base[: 31 - len(suffix)]}{suffix}"
        counter += 1
    used.add(candidate.lower())
    return candidate


def _prepare_output_path(output_path: Path) -> Path:
    """Ensure parent directory exists and return resolved output `Path`."""
    try:
        resolved = output_path.expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved
    except OSError as exc:
        raise ExportPrerequisiteError(f"Cannot prepare export path '{output_path}': {exc}") from exc


def render_chart_image_bytes(
    figure_dict: dict[str, Any],
    *,
    image_format: Literal["png", "svg"] = "png",
    width: int = 1100,
    height: int = 560,
    scale: float = 2.0,
) -> bytes:
    """Render a Plotly figure dict to static PNG or SVG bytes via Kaleido."""
    if not figure_dict:
        raise ExportPrerequisiteError("Cannot export empty Plotly figure dictionary.")
    try:
        pio_mod: Any = pio
        image_bytes = pio_mod.to_image(
            figure_dict,
            format=image_format,
            width=width,
            height=height,
            scale=scale,
        )
    except Exception as exc:  # noqa: BLE001
        raise ExportPrerequisiteError(
            "Static chart rendering failed via Kaleido. Ensure Chrome/Chromium is available "
            f"for Plotly Kaleido image export. Details: {exc}"
        ) from exc
    if not image_bytes:
        raise ExportPrerequisiteError("Kaleido returned empty image bytes for chart export.")
    return bytes(image_bytes)


_UNICODE_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
)

_FONT_STATE: dict[str, str | bool] = {
    "regular": "Helvetica",
    "bold": "Helvetica-Bold",
    "unicode": False,
}


def _ensure_pdf_fonts() -> tuple[str, str, bool]:
    """Register a Unicode TTF font with Rupee (`₹`) support when available."""
    if bool(_FONT_STATE["unicode"]):
        return str(_FONT_STATE["regular"]), str(_FONT_STATE["bold"]), True

    pdfmetrics_any: Any = pdfmetrics
    for reg_path, bold_path in _UNICODE_FONT_CANDIDATES:
        reg_file = Path(reg_path)
        if not reg_file.exists():
            continue
        try:
            pdfmetrics_any.registerFont(TTFont("StudioUnicode", str(reg_file)))
            bold_name = "StudioUnicode"
            bold_file = Path(bold_path)
            if bold_file.exists():
                pdfmetrics_any.registerFont(TTFont("StudioUnicode-Bold", str(bold_file)))
                bold_name = "StudioUnicode-Bold"
            pdfmetrics_any.registerFontFamily(
                "StudioUnicode",
                normal="StudioUnicode",
                bold=bold_name,
            )
            _FONT_STATE["regular"] = "StudioUnicode"
            _FONT_STATE["bold"] = bold_name
            _FONT_STATE["unicode"] = True
            return "StudioUnicode", bold_name, True
        except Exception:  # noqa: BLE001
            continue
    return "Helvetica", "Helvetica-Bold", False


def _escape_pdf_text(text: str, *, unicode_ok: bool = True) -> str:
    """Escape XML special characters for ReportLab `Paragraph` markup."""
    clean = text if unicode_ok else text.replace("₹", "INR ")
    return clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_pdf_styles() -> tuple[dict[str, ParagraphStyle], bool]:
    """Create consistent ReportLab paragraph styles with Unicode font support."""
    reg_font, bold_font, unicode_ok = _ensure_pdf_fonts()
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "StudioTitle",
            parent=base["Heading1"],
            fontName=bold_font,
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#0F172A"),
            spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "StudioSubtitle",
            parent=base["Heading2"],
            fontName=bold_font,
            fontSize=12.5,
            leading=16,
            textColor=colors.HexColor("#1E3A8A"),
            spaceBefore=8,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "StudioBody",
            parent=base["Normal"],
            fontName=reg_font,
            fontSize=9.5,
            leading=13,
            textColor=colors.HexColor("#1E293B"),
            spaceAfter=4,
        ),
        "muted": ParagraphStyle(
            "StudioMuted",
            parent=base["Normal"],
            fontName=reg_font,
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#475569"),
            spaceAfter=4,
        ),
        "warning": ParagraphStyle(
            "StudioWarning",
            parent=base["Normal"],
            fontName=reg_font,
            fontSize=8.5,
            leading=11.5,
            textColor=colors.HexColor("#92400E"),
            spaceAfter=4,
        ),
        "cell": ParagraphStyle(
            "StudioCell",
            parent=base["Normal"],
            fontName=reg_font,
            fontSize=7.5,
            leading=9.5,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#0F172A"),
            wordWrap="CJK",
        ),
        "cell_right": ParagraphStyle(
            "StudioCellRight",
            parent=base["Normal"],
            fontName=reg_font,
            fontSize=7.5,
            leading=9.5,
            alignment=TA_RIGHT,
            textColor=colors.HexColor("#0F172A"),
        ),
        "cell_bold": ParagraphStyle(
            "StudioCellBold",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=7.5,
            leading=9.5,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#0F172A"),
        ),
        "cell_header": ParagraphStyle(
            "StudioCellHeader",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=7.5,
            leading=9.5,
            alignment=TA_LEFT,
            textColor=colors.white,
        ),
        "cell_header_right": ParagraphStyle(
            "StudioCellHeaderRight",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=7.5,
            leading=9.5,
            alignment=TA_RIGHT,
            textColor=colors.white,
        ),
    }
    return styles, unicode_ok


def _format_pdf_cell_value(value: Any, *, unicode_ok: bool) -> tuple[str, bool]:
    """Format a table cell value cleanly for PDF output; return `(text, is_numeric)`."""
    if value is None:
        return "—", False
    if isinstance(value, bool):
        return ("Yes" if value else "No"), False
    if isinstance(value, int):
        return f"{value:,}", True
    if isinstance(value, float):
        return f"{value:,.2f}", True
    return _escape_pdf_text(str(value), unicode_ok=unicode_ok), False


def _format_compact_chart_value(val: Any) -> str:
    """Format numeric chart values into compact INR labels for presentation legibility."""
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return ""
    abs_v = abs(float(val))
    sign = "-" if val < 0 else ""
    if abs_v >= 1e7:
        return f"{sign}₹{abs_v / 1e7:.2f}Cr"
    if abs_v >= 1e5:
        return f"{sign}₹{abs_v / 1e5:.1f}L"
    if abs_v >= 1e3:
        return f"{sign}₹{abs_v / 1e3:.0f}K"
    if abs_v >= 100:
        return f"{sign}₹{abs_v:.0f}"
    return f"{val:.1f}"


def _format_axis_category_label(val: Any) -> str:
    """Format ISO date strings into clean `Mon YYYY` labels for categorical X-axes."""
    text = str(val)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return dt_date.fromisoformat(text[:10]).strftime("%b %Y")
        except ValueError:
            return text
    return text


def _compute_inr_ticks(
    values: list[float],
    *,
    is_secondary: bool = False,
) -> tuple[list[float], list[str], tuple[float, float]] | None:
    """Compute 5 clean INR tickvals/ticktext and axis range with label headroom."""
    if not values:
        return None
    v_min = min(values)
    v_max = max(values)
    if max(abs(v_min), abs(v_max)) < 100.0:
        return None
    low = 0.0 if (is_secondary and v_min >= 0) else v_min * (0.96 if v_min >= 0 else 1.08)
    high = v_max * (1.18 if is_secondary else 1.06)
    if high <= low:
        high = low + 1.0
    step = (high - low) / 4.0
    tick_vals = [low + i * step for i in range(5)]
    tick_text = [_format_compact_chart_value(tv) for tv in tick_vals]
    return tick_vals, tick_text, (low, high)


def _prepare_pdf_chart_figure(figure_dict: dict[str, Any], title: str) -> dict[str, Any]:
    """Convert a Plotly figure dict into a crisp presentation-grade light chart with data labels."""
    fig: dict[str, Any] = copy.deepcopy(figure_dict)
    raw_data: list[Any] = list(fig.get("data") or [])
    traces: list[dict[str, Any]] = [
        dict(cast(dict[str, Any], t)) for t in raw_data if isinstance(t, dict)
    ]

    primary_y: list[float] = []
    secondary_y: list[float] = []
    x_categories: list[str] = []

    for idx, trace in enumerate(traces):
        x_raw: Any = trace.get("x")
        if x_raw is not None and not isinstance(x_raw, (str, bytes)) and hasattr(x_raw, "__iter__"):
            formatted_x = [_format_axis_category_label(xv) for xv in list(x_raw)]
            trace["x"] = formatted_x
            if not x_categories:
                x_categories = formatted_x

        y_raw: Any = trace.get("y")
        if isinstance(y_raw, (str, bytes)) or not hasattr(y_raw, "__iter__"):
            continue
        y_vals: list[Any] = list(y_raw)
        n_pts = len(y_vals)
        if n_pts == 0:
            continue

        numeric_y = [
            float(v) for v in y_vals if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        if str(trace.get("yaxis") or "") == "y2":
            secondary_y.extend(numeric_y)
        else:
            primary_y.extend(numeric_y)

        step = 1 if n_pts <= 16 else max(2, n_pts // 12)
        labels = [
            _format_compact_chart_value(v) if (i % step == 0 or i == n_pts - 1) else ""
            for i, v in enumerate(y_vals)
        ]
        t_type = str(trace.get("type") or "scatter").lower()
        if t_type == "bar" and "text" not in trace:
            trace["text"] = labels
            trace["textposition"] = "outside"
            trace["cliponaxis"] = False
            trace["textfont"] = {"size": 9.5, "color": "#065F46"}
        elif t_type == "scatter" and "text" not in trace:
            mode = str(trace.get("mode") or "lines+markers")
            if "text" not in mode:
                trace["mode"] = f"{mode}+text"
            trace["text"] = labels
            trace["textposition"] = "top center" if idx == 0 else "bottom center"
            trace["textfont"] = {"size": 9.5, "color": "#1E3A8A" if idx == 0 else "#334155"}
        traces[idx] = trace

    fig["data"] = traces

    layout = dict(fig.get("layout") or {})
    layout["paper_bgcolor"] = "#FFFFFF"
    layout["plot_bgcolor"] = "#F8FAFC"
    layout["font"] = {
        "color": "#0F172A",
        "family": "Segoe UI, Arial, sans-serif",
        "size": 12,
    }
    layout["title"] = {
        "text": f"<b>{title}</b>",
        "font": {"size": 16, "color": "#0F172A"},
        "x": 0.01,
        "xanchor": "left",
    }
    layout["margin"] = {"l": 82, "r": 82, "t": 84, "b": 74}
    layout["legend"] = {
        "orientation": "h",
        "y": 1.11,
        "x": 0.0,
        "font": {"color": "#0F172A", "size": 11.5},
        "bgcolor": "rgba(255,255,255,0.85)",
    }

    for axis_key in ("xaxis", "yaxis", "yaxis2"):
        ax = dict(layout.get(axis_key) or {}) if axis_key in layout or axis_key == "xaxis" else None
        if ax is None:
            continue
        ax["color"] = "#334155"
        ax["tickfont"] = {"color": "#334155", "size": 10.5}
        title_val = ax.get("title")
        if isinstance(title_val, str):
            ax["title"] = {"text": title_val, "font": {"color": "#0F172A", "size": 11.5}}
        elif isinstance(title_val, dict):
            ax["title"] = {**title_val, "font": {"color": "#0F172A", "size": 11.5}}
        if axis_key == "xaxis":
            ax["type"] = "category"
            ax["gridcolor"] = "#E2E8F0"
            ax["linecolor"] = "#CBD5E1"
            if len(x_categories) <= 24:
                ax["dtick"] = 1
            if len(x_categories) > 8:
                ax["tickangle"] = -25
        elif axis_key == "yaxis":
            ax["gridcolor"] = "#E2E8F0"
            ax["linecolor"] = "#CBD5E1"
            ticks = _compute_inr_ticks(primary_y, is_secondary=False)
            if ticks is not None:
                ax["tickvals"], ax["ticktext"], y_range = ticks
                ax["range"] = list(y_range)
        elif axis_key == "yaxis2":
            ticks2 = _compute_inr_ticks(secondary_y, is_secondary=True)
            if ticks2 is not None:
                ax["tickvals"], ax["ticktext"], y2_range = ticks2
                ax["range"] = list(y2_range)
        layout[axis_key] = ax

    fig["layout"] = layout
    return fig


def _provenance_rows(ctx: ReportContext, disclaimer: str = "") -> list[list[str]]:
    """Build key/value provenance rows shared across PDF and XLSX exporters."""
    rows = [
        ["Profile ID", ctx.profile_id],
        ["PFETL Run ID", ctx.pfetl_run_id],
        ["Core Version", ctx.core_version or "N/A"],
        ["Schema Version", ctx.schema_version or "N/A"],
        ["Settings Snapshot", ctx.settings_snapshot_id or "N/A"],
        ["Rules Snapshot", ctx.rules_snapshot_id or "N/A"],
        ["Period", f"{ctx.period.label} ({ctx.period.start_date} to {ctx.period.end_date})"],
        ["Currency Mode", ctx.currency_mode.upper()],
        ["Studio Version", f"v{ctx.studio_version}"],
        ["Generated At (UTC)", ctx.generated_at.isoformat()],
    ]
    if disclaimer:
        rows.append(["Disclaimer", disclaimer])
    return rows


def _append_pdf_table_block(
    story: list[Flowable],
    block: RenderedTableBlock,
    styles: dict[str, ParagraphStyle],
    *,
    unicode_ok: bool,
) -> None:
    """Append a cleanly formatted, wrapped `RenderedTableBlock` to the ReportLab story."""
    story.append(
        Paragraph(_escape_pdf_text(block.title, unicode_ok=unicode_ok), styles["subtitle"])
    )
    if not block.columns or not block.rows:
        story.append(Paragraph("No rows available for this selection.", styles["muted"]))
        return

    max_cols = 10
    cols = list(block.columns[:max_cols])
    sampled_rows = block.rows[:250]
    numeric_cols: set[str] = set()
    for col in cols:
        for r in sampled_rows[:10]:
            val = r.get(col)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                numeric_cols.add(col)
                break

    header_row = [
        Paragraph(
            _escape_pdf_text(col, unicode_ok=unicode_ok),
            styles["cell_header_right"] if col in numeric_cols else styles["cell_header"],
        )
        for col in cols
    ]
    table_data: list[list[Any]] = [header_row]
    for row in sampled_rows:
        formatted_row: list[Any] = []
        for col in cols:
            cell_txt, is_num = _format_pdf_cell_value(row.get(col), unicode_ok=unicode_ok)
            cell_style = styles["cell_right"] if (is_num or col in numeric_cols) else styles["cell"]
            formatted_row.append(Paragraph(cell_txt, cell_style))
        table_data.append(formatted_row)

    avail_width = landscape(A4)[0] - 1.2 * inch
    col_width = avail_width / max(len(cols), 1)
    tbl = Table(table_data, colWidths=[col_width] * len(cols), repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(tbl)
    caption = (
        f"Showing {min(len(block.rows), 250)} of {block.total_rows} rows · "
        f"Source: {', '.join(block.source_tables)}"
    )
    story.append(Paragraph(_escape_pdf_text(caption, unicode_ok=unicode_ok), styles["muted"]))
    story.append(Spacer(1, 6))


def _record_export_run(
    result: ReportResult,
    output_format: str,
    resolved_path: Path,
    state_db_path: Path | None,
) -> None:
    """Persist export run metadata into Studio's `report_runs` table."""
    with StudioStateStore(db_path=state_db_path) as store:
        store.record_report_run(
            report_id=result.definition_id,
            pfetl_run_id=result.context.pfetl_run_id,
            period_json=result.context.period.model_dump_json(),
            output_format=output_format,
            output_path=str(resolved_path),
            generated_at=result.context.generated_at,
        )


class ReportExporter:
    """Stateless multi-format exporter facade consuming `ReportResult` / `RenderedBlock`."""

    @classmethod
    def export_chart_png(
        cls,
        figure_dict: dict[str, Any],
        output_path: Path,
        *,
        width: int = 1100,
        height: int = 560,
        scale: float = 2.0,
    ) -> ExportResult:
        """Export a Plotly figure dictionary to a local PNG image via Kaleido."""
        resolved = _prepare_output_path(output_path)
        png_bytes = render_chart_image_bytes(
            figure_dict,
            image_format="png",
            width=width,
            height=height,
            scale=scale,
        )
        resolved.write_bytes(png_bytes)
        return ExportResult(path=resolved, format="png", bytes_written=len(png_bytes))

    @classmethod
    def export_chart_svg(
        cls,
        figure_dict: dict[str, Any],
        output_path: Path,
        *,
        width: int = 1100,
        height: int = 560,
    ) -> ExportResult:
        """Export a Plotly figure dictionary to a local SVG image via Kaleido."""
        resolved = _prepare_output_path(output_path)
        svg_bytes = render_chart_image_bytes(
            figure_dict,
            image_format="svg",
            width=width,
            height=height,
            scale=1.0,
        )
        resolved.write_bytes(svg_bytes)
        return ExportResult(path=resolved, format="svg", bytes_written=len(svg_bytes))

    @classmethod
    def export_csv(
        cls,
        source: RenderedTableBlock | pl.DataFrame,
        output_path: Path,
        *,
        columns: Sequence[str] | None = None,
    ) -> ExportResult:
        """Export one `RenderedTableBlock` or `polars.DataFrame` to UTF-8 CSV via Polars."""
        resolved = _prepare_output_path(output_path)
        if isinstance(source, pl.DataFrame):
            frame_any: Any = source
            frame: pl.DataFrame = frame_any.select(list(columns)) if columns is not None else source
        else:
            cols = list(columns or source.columns)
            if not source.rows:
                frame = pl.DataFrame({col: [] for col in cols})
            else:
                frame = pl.DataFrame(list(source.rows))
                if cols:
                    present = [col for col in cols if col in frame.columns]
                    if present:
                        frame_raw: Any = frame
                        frame = frame_raw.select(present)

        csv_text = frame.write_csv()
        data = csv_text.encode("utf-8")
        resolved.write_bytes(data)
        return ExportResult(path=resolved, format="csv", bytes_written=len(data))

    @classmethod
    def export_pdf(
        cls,
        result: ReportResult,
        output_path: Path,
        *,
        record_history: bool = False,
        state_db_path: Path | None = None,
    ) -> ExportResult:
        """Compose a deterministic ReportLab PDF from `ReportResult` with Kaleido chart PNGs."""
        resolved = _prepare_output_path(output_path)
        styles, unicode_ok = _build_pdf_styles()
        reg_font = styles["body"].fontName
        story: list[Flowable] = []

        has_provenance_block = any(isinstance(b, RenderedProvenanceBlock) for b in result.blocks)

        for block in result.blocks:
            if isinstance(block, RenderedTextBlock):
                if block.block_type == "title":
                    story.append(
                        Paragraph(
                            _escape_pdf_text(block.heading, unicode_ok=unicode_ok),
                            styles["title"],
                        )
                    )
                    if block.body:
                        story.append(
                            Paragraph(
                                _escape_pdf_text(block.body, unicode_ok=unicode_ok),
                                styles["muted"],
                            )
                        )
                elif block.block_type == "subtitle":
                    story.append(
                        Paragraph(
                            _escape_pdf_text(block.heading, unicode_ok=unicode_ok),
                            styles["subtitle"],
                        )
                    )
                else:
                    if block.heading:
                        story.append(
                            Paragraph(
                                _escape_pdf_text(block.heading, unicode_ok=unicode_ok),
                                styles["subtitle"],
                            )
                        )
                    story.append(
                        Paragraph(
                            _escape_pdf_text(block.body, unicode_ok=unicode_ok),
                            styles["body"],
                        )
                    )
            elif isinstance(block, RenderedProvenanceBlock):
                story.append(Paragraph("Report Provenance & Snapshot Audit", styles["subtitle"]))
                prov_data = [
                    [
                        Paragraph(_escape_pdf_text(k, unicode_ok=unicode_ok), styles["cell_bold"]),
                        Paragraph(_escape_pdf_text(v, unicode_ok=unicode_ok), styles["cell"]),
                    ]
                    for k, v in _provenance_rows(result.context, block.disclaimer_text)
                ]
                prov_tbl = Table(prov_data, colWidths=[2.0 * inch, 8.4 * inch])
                prov_tbl.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9")),
                            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                        ]
                    )
                )
                story.append(prov_tbl)
                story.append(Spacer(1, 8))
            elif isinstance(block, RenderedKPIGroupBlock):
                story.append(
                    Paragraph(
                        _escape_pdf_text(block.title, unicode_ok=unicode_ok),
                        styles["subtitle"],
                    )
                )
                kpi_rows: list[list[Flowable]] = [
                    [
                        Paragraph("Metric", styles["cell_header"]),
                        Paragraph("Value", styles["cell_header_right"]),
                        Paragraph("Notes", styles["cell_header"]),
                    ]
                ]
                for item in block.items:
                    kpi_rows.append(
                        [
                            Paragraph(
                                _escape_pdf_text(item.label, unicode_ok=unicode_ok),
                                styles["cell"],
                            ),
                            Paragraph(
                                _escape_pdf_text(item.formatted_value, unicode_ok=unicode_ok),
                                styles["cell_right"],
                            ),
                            Paragraph(
                                _escape_pdf_text(item.subtitle, unicode_ok=unicode_ok),
                                styles["cell"],
                            ),
                        ]
                    )
                kpi_tbl = Table(
                    kpi_rows, colWidths=[2.8 * inch, 1.6 * inch, 6.0 * inch], repeatRows=1
                )
                kpi_tbl.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
                            (
                                "ROWBACKGROUNDS",
                                (0, 1),
                                (-1, -1),
                                [colors.white, colors.HexColor("#F8FAFC")],
                            ),
                            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ]
                    )
                )
                story.append(kpi_tbl)
                story.append(Spacer(1, 8))
            elif isinstance(block, RenderedChartBlock):
                chart_flowables: list[Flowable] = []
                if block.figure_dict:
                    light_fig = _prepare_pdf_chart_figure(block.figure_dict, block.title)
                    png_bytes = render_chart_image_bytes(
                        light_fig,
                        image_format="png",
                        width=1280,
                        height=600,
                        scale=2.0,
                    )
                    img = Image(io.BytesIO(png_bytes), width=9.8 * inch, height=4.6 * inch)
                    chart_flowables.append(img)
                else:
                    chart_flowables.append(
                        Paragraph(
                            _escape_pdf_text(block.title, unicode_ok=unicode_ok),
                            styles["subtitle"],
                        )
                    )
                if block.caption:
                    chart_flowables.append(
                        Paragraph(
                            _escape_pdf_text(block.caption, unicode_ok=unicode_ok),
                            styles["muted"],
                        )
                    )
                chart_flowables.append(Spacer(1, 6))
                story.append(KeepTogether(chart_flowables))
            elif isinstance(block, RenderedTableBlock):
                _append_pdf_table_block(story, block, styles, unicode_ok=unicode_ok)
            elif isinstance(block, RenderedStructuralBlock):
                if block.block_type == "page_break":
                    story.append(PageBreak())
                else:
                    story.append(
                        HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#CBD5E1"))
                    )
                    if block.label:
                        story.append(
                            Paragraph(
                                _escape_pdf_text(block.label, unicode_ok=unicode_ok),
                                styles["muted"],
                            )
                        )
            else:
                story.append(
                    Paragraph(
                        _escape_pdf_text(
                            f"[{block.code}] {block.block_id}: {block.message} "
                            f"(Remediation: {block.remediation})",
                            unicode_ok=unicode_ok,
                        ),
                        styles["warning"],
                    )
                )

        if not has_provenance_block:
            story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#CBD5E1")))
            story.append(
                Paragraph(
                    _escape_pdf_text(
                        f"PFETL Run: {result.context.pfetl_run_id} | "
                        f"Period: {result.context.period.label} | "
                        f"Studio v{result.context.studio_version}",
                        unicode_ok=unicode_ok,
                    ),
                    styles["muted"],
                )
            )

        def _draw_footer(canvas: Canvas, doc: SimpleDocTemplate) -> None:
            canvas.saveState()
            canvas.setFont(reg_font, 7.5)
            canvas.setFillColor(colors.HexColor("#64748B"))
            footer = (
                f"{result.definition_id}  |  PFETL Run: {result.context.pfetl_run_id}  |  "
                f"Studio v{result.context.studio_version}  |  Page {doc.page}"
            )
            canvas.drawString(0.6 * inch, 0.35 * inch, footer)
            canvas.restoreState()

        doc = SimpleDocTemplate(
            str(resolved),
            pagesize=landscape(A4),
            leftMargin=0.6 * inch,
            rightMargin=0.6 * inch,
            topMargin=0.55 * inch,
            bottomMargin=0.55 * inch,
            title=result.definition_name,
        )
        doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)

        if record_history:
            _record_export_run(result, "pdf", resolved, state_db_path)

        return ExportResult(
            path=resolved,
            format="pdf",
            bytes_written=resolved.stat().st_size,
        )

    @classmethod
    def export_xlsx(
        cls,
        result: ReportResult,
        output_path: Path,
        *,
        record_history: bool = False,
        state_db_path: Path | None = None,
    ) -> ExportResult:
        """Build an XlsxWriter workbook with Summary/Provenance, table sheets, and charts."""
        resolved = _prepare_output_path(output_path)
        warnings: list[str] = []
        used_sheets: set[str] = set()

        workbook: Any = xlsxwriter.Workbook(str(resolved))
        try:
            header_fmt = workbook.add_format(
                {
                    "bold": True,
                    "bg_color": "#1E293B",
                    "font_color": "#FFFFFF",
                    "border": 1,
                }
            )
            section_fmt = workbook.add_format(
                {
                    "bold": True,
                    "font_size": 12,
                    "font_color": "#1E3A8A",
                }
            )
            bold_fmt = workbook.add_format({"bold": True})
            num_fmt = workbook.add_format({"num_format": "#,##0.00"})

            summary_name = sanitize_excel_sheet_name("Summary & Provenance", used_sheets)
            ws_summary = workbook.add_worksheet(summary_name)
            ws_summary.set_column(0, 0, 26)
            ws_summary.set_column(1, 1, 56)
            ws_summary.set_column(2, 2, 36)

            ws_summary.write(0, 0, result.definition_name, section_fmt)
            row_idx = 2
            ws_summary.write(row_idx, 0, "Provenance Field", header_fmt)
            ws_summary.write(row_idx, 1, "Value", header_fmt)
            row_idx += 1

            disclaimer = ""
            for b in result.blocks:
                if isinstance(b, RenderedProvenanceBlock) and b.disclaimer_text:
                    disclaimer = b.disclaimer_text
                    break
            for key, val in _provenance_rows(result.context, disclaimer):
                ws_summary.write(row_idx, 0, key, bold_fmt)
                ws_summary.write(row_idx, 1, val)
                row_idx += 1

            for b in result.blocks:
                if isinstance(b, RenderedKPIGroupBlock):
                    row_idx += 1
                    ws_summary.write(row_idx, 0, b.title, section_fmt)
                    row_idx += 1
                    ws_summary.write(row_idx, 0, "KPI Metric", header_fmt)
                    ws_summary.write(row_idx, 1, "Formatted Value", header_fmt)
                    ws_summary.write(row_idx, 2, "Notes", header_fmt)
                    row_idx += 1
                    for item in b.items:
                        ws_summary.write(row_idx, 0, item.label)
                        ws_summary.write(row_idx, 1, item.formatted_value)
                        ws_summary.write(row_idx, 2, item.subtitle)
                        row_idx += 1

            if result.warnings:
                row_idx += 1
                ws_summary.write(row_idx, 0, "Warnings", section_fmt)
                row_idx += 1
                for warn in result.warnings:
                    ws_summary.write(row_idx, 0, f"{warn.block_id} ({warn.code})", bold_fmt)
                    ws_summary.write(row_idx, 1, warn.message)
                    ws_summary.write(row_idx, 2, warn.remediation)
                    row_idx += 1

            for b in result.blocks:
                if isinstance(b, RenderedTableBlock):
                    sheet_name = sanitize_excel_sheet_name(b.title or b.block_id, used_sheets)
                    ws = workbook.add_worksheet(sheet_name)
                    ws.freeze_panes(1, 0)
                    for col_idx, col_name in enumerate(b.columns):
                        ws.write(0, col_idx, col_name, header_fmt)
                        max_len = max(
                            [len(col_name)]
                            + [
                                len(str(r.get(col_name, "")))
                                for r in b.rows[:100]
                                if r.get(col_name) is not None
                            ]
                        )
                        ws.set_column(col_idx, col_idx, min(max(max_len + 3, 12), 42))
                    for r_idx, data_row in enumerate(b.rows, start=1):
                        for col_idx, col_name in enumerate(b.columns):
                            cell_val: Any = data_row.get(col_name)
                            if isinstance(cell_val, (int, float)) and not isinstance(
                                cell_val, bool
                            ):
                                ws.write_number(r_idx, col_idx, float(cell_val), num_fmt)
                            elif cell_val is not None:
                                ws.write(r_idx, col_idx, str(cell_val))

            chart_blocks = [
                b for b in result.blocks if isinstance(b, RenderedChartBlock) and b.figure_dict
            ]
            if chart_blocks:
                charts_sheet_name = sanitize_excel_sheet_name("Charts", used_sheets)
                ws_charts = workbook.add_worksheet(charts_sheet_name)
                chart_row = 0
                for cb in chart_blocks:
                    ws_charts.write(chart_row, 0, cb.title, section_fmt)
                    chart_row += 1
                    try:
                        light_fig = _prepare_pdf_chart_figure(cb.figure_dict, cb.title)
                        png_bytes = render_chart_image_bytes(
                            light_fig,
                            image_format="png",
                            width=960,
                            height=460,
                            scale=1.5,
                        )
                        ws_charts.insert_image(
                            chart_row,
                            0,
                            f"{sanitize_filename(cb.block_id)}.png",
                            {"image_data": io.BytesIO(png_bytes)},
                        )
                        chart_row += 26
                    except ExportPrerequisiteError as exc:
                        msg = f"Chart '{cb.block_id}' image skipped in XLSX: {exc}"
                        warnings.append(msg)
                        ws_charts.write(chart_row, 0, msg)
                        chart_row += 2
        finally:
            workbook.close()

        if record_history:
            _record_export_run(result, "xlsx", resolved, state_db_path)

        return ExportResult(
            path=resolved,
            format="xlsx",
            bytes_written=resolved.stat().st_size,
            warnings=tuple(warnings),
        )

    @classmethod
    def export_pptx(
        cls,
        result: ReportResult,
        output_path: Path,
        *,
        record_history: bool = False,
        state_db_path: Path | None = None,
    ) -> ExportResult:
        """Build an editable 16:9 PowerPoint (`.pptx`) deck from `ReportResult`."""
        resolved = _prepare_output_path(output_path)
        build_pptx_deck(
            result,
            resolved,
            provenance_rows_fn=_provenance_rows,
            prepare_chart_fn=_prepare_pdf_chart_figure,
            render_chart_png_fn=render_chart_image_bytes,
        )
        if record_history:
            _record_export_run(result, "pptx", resolved, state_db_path)

        return ExportResult(
            path=resolved,
            format="pptx",
            bytes_written=resolved.stat().st_size,
        )
