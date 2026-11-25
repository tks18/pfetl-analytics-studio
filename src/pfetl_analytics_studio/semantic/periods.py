"""Canonical period and comparison resolution backed by `silver.dim_calendar`.

Owns
----
- PeriodPreset, ComparisonMode, PeriodRequest, ComparisonPeriod, and PeriodContext models.
- SQL resolution against `silver.dim_calendar` (and Gold month boundaries) via `CalendarQueryPort`.
- Display label and cache serialization helpers for periods.

Does not own
------------
- Database connection creation or lifecycle (delegates reads through `CalendarQueryPort`).
- Financial year rule calculation in Python; `silver.dim_calendar` is authoritative.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, model_validator


class PeriodPreset(StrEnum):
    """Supported standardized analysis time horizons."""

    LATEST_COMPLETE_MONTH = "latest_complete_month"
    CURRENT_MONTH_MTD = "current_month_mtd"
    SELECTED_MONTH = "selected_month"
    CALENDAR_QUARTER = "calendar_quarter"
    FY_QUARTER = "fy_quarter"
    CALENDAR_YTD = "calendar_ytd"
    FYTD = "fytd"
    SELECTED_FY = "selected_fy"
    LAST_COMPLETED_FY = "last_completed_fy"
    ROLLING_3M = "rolling_3m"
    ROLLING_6M = "rolling_6m"
    ROLLING_12M = "rolling_12m"
    ROLLING_24M = "rolling_24m"
    ROLLING_36M = "rolling_36m"
    SINCE_INCEPTION = "since_inception"
    CUSTOM = "custom"


PRESET_LABELS: dict[PeriodPreset, str] = {
    PeriodPreset.LATEST_COMPLETE_MONTH: "Latest Complete Month",
    PeriodPreset.CURRENT_MONTH_MTD: "Current Month (MTD)",
    PeriodPreset.SELECTED_MONTH: "Selected Month",
    PeriodPreset.CALENDAR_QUARTER: "Calendar Quarter",
    PeriodPreset.FY_QUARTER: "FY Quarter",
    PeriodPreset.CALENDAR_YTD: "Calendar YTD",
    PeriodPreset.FYTD: "FYTD",
    PeriodPreset.SELECTED_FY: "Selected Financial Year",
    PeriodPreset.LAST_COMPLETED_FY: "Previous Financial Year",
    PeriodPreset.ROLLING_3M: "Rolling 3M",
    PeriodPreset.ROLLING_6M: "Rolling 6M",
    PeriodPreset.ROLLING_12M: "Rolling 12M",
    PeriodPreset.ROLLING_24M: "Rolling 24M",
    PeriodPreset.ROLLING_36M: "Rolling 36M",
    PeriodPreset.SINCE_INCEPTION: "Since Inception",
    PeriodPreset.CUSTOM: "Custom Range",
}


class ComparisonMode(StrEnum):
    """Comparison period strategies relative to the active period."""

    NONE = "none"
    PREVIOUS_PERIOD = "previous_period"
    SAME_PERIOD_PREVIOUS_YEAR = "same_period_previous_year"
    FY_START_MONTH = "fy_start_month"
    PRIOR_COMPLETED_FY = "prior_completed_fy"
    CUSTOM = "custom"


class CalendarQueryPort(Protocol):
    """Structural read-only query protocol satisfied by `DuckDBReader`."""

    def fetch_one(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> dict[str, Any] | None:
        """Execute a single-row parameterized SQL query."""
        ...

    def fetch_all(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> list[dict[str, Any]]:
        """Execute a multi-row parameterized SQL query."""
        ...

    def table_exists(self, schema: str, table: str) -> bool:
        """Check whether a table exists in the given schema."""
        ...


def _parse_year_month(value: str) -> tuple[int, int]:
    """Parse `YYYY-MM` month specifier into `(year, month)` integers."""
    parts = value.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"selected_month must use YYYY-MM format, got {value!r}.")
    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"selected_month contains non-numeric components: {value!r}.") from exc
    if month < 1 or month > 12:
        raise ValueError(f"selected_month month must be between 1 and 12, got {month}.")
    return year, month


class PeriodRequest(BaseModel):
    """Immutable period selection specification passed from UI or report definitions."""

    model_config = ConfigDict(frozen=True)

    preset: PeriodPreset = PeriodPreset.LATEST_COMPLETE_MONTH
    selected_month: str | None = None
    quarter_year: str | None = None
    fy_quarter_year: str | None = None
    financial_year: str | None = None
    custom_start: date | None = None
    custom_end: date | None = None
    comparison_mode: ComparisonMode = ComparisonMode.NONE
    custom_comparison_start: date | None = None
    custom_comparison_end: date | None = None

    @model_validator(mode="after")
    def _validate_request(self) -> PeriodRequest:
        """Enforce cross-field period bounds invariants."""
        if self.preset == PeriodPreset.SELECTED_MONTH and self.selected_month is not None:
            _parse_year_month(self.selected_month)
        if self.preset == PeriodPreset.CUSTOM:
            if self.custom_start is None or self.custom_end is None:
                raise ValueError("Custom period requires both custom_start and custom_end.")
            if self.custom_start > self.custom_end:
                raise ValueError("Custom period start_date cannot exceed end_date.")
        if self.comparison_mode == ComparisonMode.CUSTOM:
            if self.custom_comparison_start is None or self.custom_comparison_end is None:
                raise ValueError(
                    "Custom comparison requires both custom_comparison_start and "
                    "custom_comparison_end."
                )
            if self.custom_comparison_start > self.custom_comparison_end:
                raise ValueError("Custom comparison start_date cannot exceed end_date.")
        return self


class ComparisonPeriod(BaseModel):
    """Resolved comparison date window and display label."""

    model_config = ConfigDict(frozen=True)

    mode: ComparisonMode
    start_date: date | None
    end_date: date | None
    label: str
    financial_year: str | None = None
    is_empty: bool = False


class PeriodCatalogOptions(BaseModel):
    """Selectable calendar options discovered read-only from `silver.dim_calendar`."""

    model_config = ConfigDict(frozen=True)

    months: tuple[tuple[str, str], ...] = ()
    financial_years: tuple[str, ...] = ()
    calendar_quarters: tuple[str, ...] = ()
    fy_quarters: tuple[str, ...] = ()
    min_date: date | None = None
    max_date: date | None = None
    default_selected_month: str | None = None
    default_financial_year: str | None = None
    default_calendar_quarter: str | None = None
    default_fy_quarter: str | None = None
    default_custom_start: date | None = None
    default_custom_end: date | None = None


class PeriodContext(BaseModel):
    """Resolved inclusive analytical date range anchored to `silver.dim_calendar`."""

    model_config = ConfigDict(frozen=True)

    start_date: date | None
    end_date: date | None
    label: str
    preset: PeriodPreset
    financial_year: str | None = None
    as_of_date: date | None = None
    comparison: ComparisonPeriod | None = None
    is_empty: bool = False

    def cache_key(self) -> str:
        """Serialize resolved period and comparison boundaries for cache key stability."""
        s = self.start_date.isoformat() if self.start_date else "none"
        e = self.end_date.isoformat() if self.end_date else "none"
        a = self.as_of_date.isoformat() if self.as_of_date else "none"
        comp = "none"
        if self.comparison is not None:
            cs = self.comparison.start_date.isoformat() if self.comparison.start_date else "none"
            ce = self.comparison.end_date.isoformat() if self.comparison.end_date else "none"
            comp = f"{self.comparison.mode.value}:{cs}:{ce}:{self.comparison.is_empty}"
        return (
            f"{self.preset.value}|{s}|{e}|{self.financial_year or 'none'}|"
            f"{a}|{self.is_empty}|{comp}"
        )


def preset_display_name(preset: PeriodPreset) -> str:
    """Return human-readable label for a `PeriodPreset`."""
    return PRESET_LABELS.get(preset, preset.value)


def format_period_label(context: PeriodContext) -> str:
    """Format a concise UI badge string describing an active `PeriodContext`."""
    if context.is_empty or context.start_date is None or context.end_date is None:
        return f"{context.label} (No data)"
    return f"{context.label} ({context.start_date.isoformat()} to {context.end_date.isoformat()})"


def format_comparison_label(comparison: ComparisonPeriod | None) -> str | None:
    """Format a concise comparison window label for active comparison badges."""
    if comparison is None:
        return None
    if comparison.is_empty or comparison.start_date is None or comparison.end_date is None:
        return f"{comparison.label} (Unavailable)"
    return (
        f"{comparison.label} "
        f"({comparison.start_date.isoformat()} to {comparison.end_date.isoformat()})"
    )


def _coerce_date(value: Any) -> date | None:
    """Convert DuckDB date/datetime/ISO string values into `datetime.date`."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _shift_year_safe(target: date, years: int) -> date:
    """Shift a calendar date by `years`, clamping Feb 29 to Feb 28 in non-leap years."""
    new_year = target.year + years
    try:
        return target.replace(year=new_year)
    except ValueError:
        return target.replace(year=new_year, day=28)


class PeriodResolver:
    """Resolves `PeriodRequest` presets against `silver.dim_calendar` and Gold boundaries."""

    def __init__(self, query_port: CalendarQueryPort) -> None:
        """Initialize resolver with a read-only SQL query executor.

        Parameters
        ----------
        query_port : CalendarQueryPort
            Read-only database gateway supporting `fetch_one`, `fetch_all`, and `table_exists`.
        """
        self.query_port = query_port

    def resolve(self, request: PeriodRequest | None = None) -> PeriodContext:
        """Resolve a `PeriodRequest` into concrete start/end dates and optional comparison.

        Parameters
        ----------
        request : PeriodRequest | None, optional
            Requested preset, parameters, and comparison mode; defaults to
            `PeriodPreset.LATEST_COMPLETE_MONTH`.

        Returns
        -------
        PeriodContext
            Resolved immutable date range and display metadata.
        """
        req = request if request is not None else PeriodRequest()
        if not self.query_port.table_exists("silver", "dim_calendar"):
            return PeriodContext(
                start_date=None,
                end_date=None,
                label=preset_display_name(req.preset),
                preset=req.preset,
                is_empty=True,
            )

        as_of_date = self._resolve_as_of_date()
        if as_of_date is None:
            return PeriodContext(
                start_date=None,
                end_date=None,
                label=preset_display_name(req.preset),
                preset=req.preset,
                is_empty=True,
            )

        ctx = self._resolve_primary(req, as_of_date)
        if req.comparison_mode != ComparisonMode.NONE:
            comp = self._resolve_comparison(req, ctx)
            return ctx.model_copy(update={"comparison": comp})
        return ctx

    def list_available_options(self) -> PeriodCatalogOptions:
        """Enumerate valid months, quarters, FYs, and date bounds from `silver.dim_calendar`.

        Returns
        -------
        PeriodCatalogOptions
            Descending selectable options and safe initial defaults for UI controls.
        """
        if not self.query_port.table_exists("silver", "dim_calendar"):
            return PeriodCatalogOptions()

        as_of_date = self._resolve_as_of_date()
        if as_of_date is None:
            return PeriodCatalogOptions()

        gold_months: set[date] = set()
        if self._gold_has_monthly_dates():
            g_rows = self.query_port.fetch_all(
                'SELECT DISTINCT "MONTH_START_DATE" AS m_start FROM gold.core_monthly_fact;'
            )
            for row in g_rows:
                parsed = _coerce_date(row.get("m_start"))
                if parsed is not None:
                    gold_months.add(parsed)

        bounds_row = self.query_port.fetch_one(
            """
            SELECT MIN("Date") AS min_d, MAX("Date") AS max_d
            FROM silver.dim_calendar
            WHERE NOT COALESCE("Is_Future_Date", FALSE);
            """
        )
        min_date = _coerce_date(bounds_row["min_d"]) if bounds_row else None
        max_date = _coerce_date(bounds_row["max_d"]) if bounds_row else as_of_date

        month_rows = self.query_port.fetch_all(
            """
            SELECT DISTINCT
                "Start_of_Month" AS s_date,
                "Year" AS yr,
                "Month" AS mo,
                "Month_Year" AS m_label
            FROM silver.dim_calendar
            WHERE "Start_of_Month" <= ?
            ORDER BY "Start_of_Month" DESC;
            """,
            (as_of_date,),
        )
        months: list[tuple[str, str]] = []
        for row in month_rows:
            yr = row.get("yr")
            mo = row.get("mo")
            if yr is None or mo is None:
                continue
            ym_key = f"{int(yr):04d}-{int(mo):02d}"
            base_lbl = str(row.get("m_label") or ym_key)
            s_dt = _coerce_date(row.get("s_date"))
            if gold_months and s_dt is not None and s_dt not in gold_months:
                display = f"{base_lbl} ({ym_key}, Partial)"
            else:
                display = f"{base_lbl} ({ym_key})"
            months.append((ym_key, display))

        fy_rows = self.query_port.fetch_all(
            """
            SELECT "Financial_Year" AS fy, MAX("Date") AS max_d
            FROM silver.dim_calendar
            WHERE "Date" <= ? AND "Financial_Year" IS NOT NULL
            GROUP BY "Financial_Year"
            ORDER BY max_d DESC;
            """,
            (as_of_date,),
        )
        fys = tuple(str(r["fy"]) for r in fy_rows if r.get("fy"))

        cq_rows = self.query_port.fetch_all(
            """
            SELECT "Quarter_Year" AS q, MAX("Date") AS max_d
            FROM silver.dim_calendar
            WHERE "Date" <= ? AND "Quarter_Year" IS NOT NULL
            GROUP BY "Quarter_Year"
            ORDER BY max_d DESC;
            """,
            (as_of_date,),
        )
        cqs = tuple(str(r["q"]) for r in cq_rows if r.get("q"))

        fq_rows = self.query_port.fetch_all(
            """
            SELECT "FY_Quarter_Year" AS q, MAX("Date") AS max_d
            FROM silver.dim_calendar
            WHERE "Date" <= ? AND "FY_Quarter_Year" IS NOT NULL
            GROUP BY "FY_Quarter_Year"
            ORDER BY max_d DESC;
            """,
            (as_of_date,),
        )
        fqs = tuple(str(r["q"]) for r in fq_rows if r.get("q"))

        latest_complete = self._resolve_latest_complete_month(as_of_date)
        default_ym = (
            f"{latest_complete.start_date.year:04d}-{latest_complete.start_date.month:02d}"
            if latest_complete.start_date is not None
            else (months[0][0] if months else None)
        )
        return PeriodCatalogOptions(
            months=tuple(months),
            financial_years=fys,
            calendar_quarters=cqs,
            fy_quarters=fqs,
            min_date=min_date,
            max_date=max_date,
            default_selected_month=default_ym,
            default_financial_year=latest_complete.financial_year or (fys[0] if fys else None),
            default_calendar_quarter=cqs[0] if cqs else None,
            default_fy_quarter=fqs[0] if fqs else None,
            default_custom_start=latest_complete.start_date or min_date,
            default_custom_end=latest_complete.end_date or max_date,
        )

    def _resolve_as_of_date(self) -> date | None:
        """Return latest non-future date in `silver.dim_calendar`."""
        row = self.query_port.fetch_one(
            """
            SELECT MAX("Date") AS as_of
            FROM silver.dim_calendar
            WHERE NOT COALESCE("Is_Future_Date", FALSE);
            """
        )
        as_of = _coerce_date(row["as_of"]) if row else None
        if as_of is not None:
            return as_of

        fallback = self.query_port.fetch_one(
            'SELECT MAX("Date") AS as_of FROM silver.dim_calendar;'
        )
        return _coerce_date(fallback["as_of"]) if fallback else None

    def _gold_has_monthly_dates(self) -> bool:
        """Check whether `gold.core_monthly_fact` exists with `MONTH_START_DATE` column."""
        if not self.query_port.table_exists("gold", "core_monthly_fact"):
            return False
        col = self.query_port.fetch_one(
            """
            SELECT 1 AS ok
            FROM information_schema.columns
            WHERE table_schema = 'gold'
              AND table_name = 'core_monthly_fact'
              AND column_name = 'MONTH_START_DATE'
            LIMIT 1;
            """
        )
        return col is not None

    def _resolve_primary(self, req: PeriodRequest, as_of_date: date) -> PeriodContext:
        """Dispatch primary window resolution by preset."""
        rolling_months: dict[PeriodPreset, int] = {
            PeriodPreset.ROLLING_3M: 3,
            PeriodPreset.ROLLING_6M: 6,
            PeriodPreset.ROLLING_12M: 12,
            PeriodPreset.ROLLING_24M: 24,
            PeriodPreset.ROLLING_36M: 36,
        }
        if req.preset in rolling_months:
            return self._resolve_rolling(req.preset, rolling_months[req.preset], as_of_date)

        if req.preset == PeriodPreset.LATEST_COMPLETE_MONTH:
            return self._resolve_latest_complete_month(as_of_date)
        if req.preset == PeriodPreset.CURRENT_MONTH_MTD:
            return self._resolve_current_month_mtd(as_of_date)
        if req.preset == PeriodPreset.SELECTED_MONTH:
            return self._resolve_selected_month(req, as_of_date)
        if req.preset == PeriodPreset.CALENDAR_QUARTER:
            return self._resolve_calendar_quarter(req, as_of_date)
        if req.preset == PeriodPreset.FY_QUARTER:
            return self._resolve_fy_quarter(req, as_of_date)
        if req.preset == PeriodPreset.CALENDAR_YTD:
            return self._resolve_calendar_ytd(as_of_date)
        if req.preset == PeriodPreset.FYTD:
            return self._resolve_fytd(as_of_date)
        if req.preset == PeriodPreset.SELECTED_FY:
            return self._resolve_selected_fy(req, as_of_date)
        if req.preset == PeriodPreset.LAST_COMPLETED_FY:
            return self._resolve_last_completed_fy(as_of_date)
        if req.preset == PeriodPreset.SINCE_INCEPTION:
            return self._resolve_since_inception(as_of_date)
        return self._resolve_custom(req, as_of_date)

    def _resolve_current_month_mtd(self, as_of_date: date) -> PeriodContext:
        """Resolve month-to-date window containing `as_of_date`."""
        row = self.query_port.fetch_one(
            """
            SELECT "Start_of_Month" AS s_date, "Financial_Year" AS fy, "Month_Year" AS m_label
            FROM silver.dim_calendar
            WHERE "Date" = ?
            LIMIT 1;
            """,
            (as_of_date,),
        )
        if row is None:
            return self._empty_context(PeriodPreset.CURRENT_MONTH_MTD, as_of_date)
        s_date = _coerce_date(row.get("s_date"))
        label = f"{row['m_label']} (MTD)" if row.get("m_label") else "Current Month (MTD)"
        return PeriodContext(
            start_date=s_date,
            end_date=as_of_date,
            label=label,
            preset=PeriodPreset.CURRENT_MONTH_MTD,
            financial_year=str(row["fy"]) if row.get("fy") else None,
            as_of_date=as_of_date,
            is_empty=s_date is None,
        )

    def _resolve_last_completed_fy(self, as_of_date: date) -> PeriodContext:
        """Resolve the most recent completed Financial Year prior to `as_of_date`'s FY."""
        curr_row = self.query_port.fetch_one(
            'SELECT "Financial_Year" AS fy FROM silver.dim_calendar WHERE "Date" = ? LIMIT 1;',
            (as_of_date,),
        )
        curr_fy = str(curr_row["fy"]) if curr_row and curr_row.get("fy") else None
        if curr_fy is None:
            return self._empty_context(PeriodPreset.LAST_COMPLETED_FY, as_of_date)
        prev_row = self.query_port.fetch_one(
            """
            SELECT "Financial_Year" AS fy, MIN("Date") AS s_date, MAX("Date") AS e_date
            FROM silver.dim_calendar
            WHERE "Date" < ? AND "Financial_Year" <> ? AND "Financial_Year" IS NOT NULL
            GROUP BY "Financial_Year"
            ORDER BY e_date DESC
            LIMIT 1;
            """,
            (as_of_date, curr_fy),
        )
        if prev_row is None or prev_row.get("s_date") is None:
            return self._empty_context(PeriodPreset.LAST_COMPLETED_FY, as_of_date)
        s_date = _coerce_date(prev_row["s_date"])
        e_date = _coerce_date(prev_row["e_date"])
        fy = str(prev_row["fy"])
        return PeriodContext(
            start_date=s_date,
            end_date=e_date,
            label=f"FY {fy}",
            preset=PeriodPreset.LAST_COMPLETED_FY,
            financial_year=fy,
            as_of_date=as_of_date,
            is_empty=s_date is None or e_date is None,
        )

    def _resolve_latest_complete_month(self, as_of_date: date) -> PeriodContext:
        """Resolve latest complete month using Gold monthly coverage and calendar bounds."""
        row: dict[str, Any] | None = None
        if self._gold_has_monthly_dates():
            row = self.query_port.fetch_one(
                """
                SELECT DISTINCT
                    c."Start_of_Month" AS s_date,
                    c."End_of_Month" AS e_date,
                    c."Financial_Year" AS fy,
                    c."Month_Year" AS m_label
                FROM silver.dim_calendar c
                WHERE c."End_of_Month" <= ?
                  AND c."Start_of_Month" IN (
                      SELECT DISTINCT "MONTH_START_DATE" FROM gold.core_monthly_fact
                  )
                ORDER BY c."Start_of_Month" DESC
                LIMIT 1;
                """,
                (as_of_date,),
            )

        if row is None:
            row = self.query_port.fetch_one(
                """
                SELECT DISTINCT
                    "Start_of_Month" AS s_date,
                    "End_of_Month" AS e_date,
                    "Financial_Year" AS fy,
                    "Month_Year" AS m_label
                FROM silver.dim_calendar
                WHERE "End_of_Month" <= ?
                  AND NOT COALESCE("Is_Future_Date", FALSE)
                ORDER BY "Start_of_Month" DESC
                LIMIT 1;
                """,
                (as_of_date,),
            )

        if row is None:
            return self._empty_context(PeriodPreset.LATEST_COMPLETE_MONTH, as_of_date)

        s_date = _coerce_date(row["s_date"])
        e_date = _coerce_date(row["e_date"])
        label = (
            str(row["m_label"])
            if row.get("m_label")
            else (s_date.strftime("%b %Y") if s_date else "Latest Complete Month")
        )
        return PeriodContext(
            start_date=s_date,
            end_date=e_date,
            label=label,
            preset=PeriodPreset.LATEST_COMPLETE_MONTH,
            financial_year=str(row["fy"]) if row.get("fy") else None,
            as_of_date=as_of_date,
            is_empty=s_date is None or e_date is None,
        )

    def _resolve_selected_month(self, req: PeriodRequest, as_of_date: date) -> PeriodContext:
        """Resolve an explicit `YYYY-MM` month (or latest non-future month when omitted)."""
        if req.selected_month:
            year, month = _parse_year_month(req.selected_month)
            row = self.query_port.fetch_one(
                """
                SELECT DISTINCT
                    "Start_of_Month" AS s_date,
                    "End_of_Month" AS e_date,
                    "Financial_Year" AS fy,
                    "Month_Year" AS m_label
                FROM silver.dim_calendar
                WHERE "Year" = ? AND "Month" = ?
                LIMIT 1;
                """,
                (year, month),
            )
            fallback_label = req.selected_month
        else:
            row = self.query_port.fetch_one(
                """
                SELECT DISTINCT
                    "Start_of_Month" AS s_date,
                    "End_of_Month" AS e_date,
                    "Financial_Year" AS fy,
                    "Month_Year" AS m_label
                FROM silver.dim_calendar
                WHERE "Date" = ?
                LIMIT 1;
                """,
                (as_of_date,),
            )
            fallback_label = "Selected Month"

        if row is None:
            return self._empty_context(PeriodPreset.SELECTED_MONTH, as_of_date, fallback_label)

        s_date = _coerce_date(row["s_date"])
        e_date = _coerce_date(row["e_date"])
        label = str(row["m_label"]) if row.get("m_label") else fallback_label
        return PeriodContext(
            start_date=s_date,
            end_date=e_date,
            label=label,
            preset=PeriodPreset.SELECTED_MONTH,
            financial_year=str(row["fy"]) if row.get("fy") else None,
            as_of_date=as_of_date,
            is_empty=s_date is None or e_date is None,
        )

    def _resolve_calendar_quarter(self, req: PeriodRequest, as_of_date: date) -> PeriodContext:
        """Resolve calendar quarter bounds via `Quarter_Year` or `as_of_date`."""
        if req.quarter_year:
            row = self.query_port.fetch_one(
                """
                SELECT DISTINCT
                    "Start_of_Quarter" AS s_date,
                    "End_of_Quarter" AS e_date,
                    "Quarter_Year" AS q_label,
                    "Financial_Year" AS fy
                FROM silver.dim_calendar
                WHERE "Quarter_Year" = ?
                ORDER BY "Date" DESC
                LIMIT 1;
                """,
                (req.quarter_year.strip(),),
            )
            fallback_label = req.quarter_year.strip()
        else:
            row = self.query_port.fetch_one(
                """
                SELECT
                    "Start_of_Quarter" AS s_date,
                    "End_of_Quarter" AS e_date,
                    "Quarter_Year" AS q_label,
                    "Financial_Year" AS fy
                FROM silver.dim_calendar
                WHERE "Date" = ?
                LIMIT 1;
                """,
                (as_of_date,),
            )
            fallback_label = "Calendar Quarter"

        if row is None:
            return self._empty_context(PeriodPreset.CALENDAR_QUARTER, as_of_date, fallback_label)

        s_date = _coerce_date(row["s_date"])
        e_date = _coerce_date(row["e_date"])
        return PeriodContext(
            start_date=s_date,
            end_date=e_date,
            label=str(row["q_label"]) if row.get("q_label") else fallback_label,
            preset=PeriodPreset.CALENDAR_QUARTER,
            financial_year=str(row["fy"]) if row.get("fy") else None,
            as_of_date=as_of_date,
            is_empty=s_date is None or e_date is None,
        )

    def _resolve_fy_quarter(self, req: PeriodRequest, as_of_date: date) -> PeriodContext:
        """Resolve Indian/configured Financial Year quarter via `FY_Quarter_Year`."""
        if req.fy_quarter_year:
            row = self.query_port.fetch_one(
                """
                SELECT DISTINCT
                    "FY_Start_of_Quarter" AS s_date,
                    "FY_End_of_Quarter" AS e_date,
                    "FY_Quarter_Year" AS q_label,
                    "Financial_Year" AS fy
                FROM silver.dim_calendar
                WHERE "FY_Quarter_Year" = ?
                ORDER BY "Date" DESC
                LIMIT 1;
                """,
                (req.fy_quarter_year.strip(),),
            )
            fallback_label = req.fy_quarter_year.strip()
        else:
            row = self.query_port.fetch_one(
                """
                SELECT
                    "FY_Start_of_Quarter" AS s_date,
                    "FY_End_of_Quarter" AS e_date,
                    "FY_Quarter_Year" AS q_label,
                    "Financial_Year" AS fy
                FROM silver.dim_calendar
                WHERE "Date" = ?
                LIMIT 1;
                """,
                (as_of_date,),
            )
            fallback_label = "FY Quarter"

        if row is None:
            return self._empty_context(PeriodPreset.FY_QUARTER, as_of_date, fallback_label)

        s_date = _coerce_date(row["s_date"])
        e_date = _coerce_date(row["e_date"])
        return PeriodContext(
            start_date=s_date,
            end_date=e_date,
            label=str(row["q_label"]) if row.get("q_label") else fallback_label,
            preset=PeriodPreset.FY_QUARTER,
            financial_year=str(row["fy"]) if row.get("fy") else None,
            as_of_date=as_of_date,
            is_empty=s_date is None or e_date is None,
        )

    def _resolve_calendar_ytd(self, as_of_date: date) -> PeriodContext:
        """Resolve Year-to-Date range capped at `as_of_date` (excluding future dates)."""
        row = self.query_port.fetch_one(
            """
            SELECT
                MIN(c."Date") AS s_date,
                MAX(c."Date") AS e_date,
                MAX(c."Year") AS cal_year,
                MAX(c."Financial_Year") AS fy
            FROM silver.dim_calendar c
            WHERE c."Year" = (SELECT "Year" FROM silver.dim_calendar WHERE "Date" = ? LIMIT 1)
              AND c."Date" <= ?
              AND NOT COALESCE(c."Is_Future_Date", FALSE);
            """,
            (as_of_date, as_of_date),
        )
        if row is None or row.get("s_date") is None:
            return self._empty_context(PeriodPreset.CALENDAR_YTD, as_of_date)

        s_date = _coerce_date(row["s_date"])
        e_date = _coerce_date(row["e_date"])
        cal_year = row.get("cal_year")
        return PeriodContext(
            start_date=s_date,
            end_date=e_date,
            label=f"YTD {cal_year}" if cal_year else "Calendar YTD",
            preset=PeriodPreset.CALENDAR_YTD,
            financial_year=str(row["fy"]) if row.get("fy") else None,
            as_of_date=as_of_date,
            is_empty=s_date is None or e_date is None,
        )

    def _resolve_fytd(self, as_of_date: date) -> PeriodContext:
        """Resolve Financial-Year-to-Date range capped at `as_of_date`."""
        row = self.query_port.fetch_one(
            """
            SELECT
                MIN(c."Date") AS s_date,
                MAX(c."Date") AS e_date,
                MAX(c."Financial_Year") AS fy
            FROM silver.dim_calendar c
            WHERE c."Financial_Year" = (
                SELECT "Financial_Year" FROM silver.dim_calendar WHERE "Date" = ? LIMIT 1
            )
              AND c."Date" <= ?
              AND NOT COALESCE(c."Is_Future_Date", FALSE);
            """,
            (as_of_date, as_of_date),
        )
        if row is None or row.get("s_date") is None:
            return self._empty_context(PeriodPreset.FYTD, as_of_date)

        s_date = _coerce_date(row["s_date"])
        e_date = _coerce_date(row["e_date"])
        fy = str(row["fy"]) if row.get("fy") else None
        return PeriodContext(
            start_date=s_date,
            end_date=e_date,
            label=f"FYTD {fy}" if fy else "FYTD",
            preset=PeriodPreset.FYTD,
            financial_year=fy,
            as_of_date=as_of_date,
            is_empty=s_date is None or e_date is None,
        )

    def _resolve_selected_fy(self, req: PeriodRequest, as_of_date: date) -> PeriodContext:
        """Resolve full Financial Year bounds from `silver.dim_calendar`."""
        target_fy = req.financial_year.strip() if req.financial_year else None
        if target_fy is None:
            fy_row = self.query_port.fetch_one(
                'SELECT "Financial_Year" AS fy FROM silver.dim_calendar WHERE "Date" = ? LIMIT 1;',
                (as_of_date,),
            )
            target_fy = str(fy_row["fy"]) if fy_row and fy_row.get("fy") else None

        if target_fy is None:
            return self._empty_context(PeriodPreset.SELECTED_FY, as_of_date)

        row = self.query_port.fetch_one(
            """
            SELECT MIN("Date") AS s_date, MAX("Date") AS e_date
            FROM silver.dim_calendar
            WHERE "Financial_Year" = ?;
            """,
            (target_fy,),
        )
        if row is None or row.get("s_date") is None:
            return self._empty_context(PeriodPreset.SELECTED_FY, as_of_date, f"FY {target_fy}")

        s_date = _coerce_date(row["s_date"])
        e_date = _coerce_date(row["e_date"])
        return PeriodContext(
            start_date=s_date,
            end_date=e_date,
            label=f"FY {target_fy}",
            preset=PeriodPreset.SELECTED_FY,
            financial_year=target_fy,
            as_of_date=as_of_date,
            is_empty=s_date is None or e_date is None,
        )

    def _resolve_rolling(
        self,
        preset: PeriodPreset,
        months: int,
        as_of_date: date,
    ) -> PeriodContext:
        """Resolve N-month rolling window anchored to latest complete month."""
        anchor = self._resolve_latest_complete_month(as_of_date)
        anchor_start = anchor.start_date
        if anchor_start is None:
            return self._empty_context(preset, as_of_date)

        rows = self.query_port.fetch_all(
            """
            SELECT DISTINCT "Start_of_Month" AS s_date, "End_of_Month" AS e_date, "Financial_Year" AS fy
            FROM silver.dim_calendar
            WHERE "Start_of_Month" <= ?
            ORDER BY "Start_of_Month" DESC
            LIMIT ?;
            """,
            (anchor_start, months),
        )
        if not rows:
            return self._empty_context(preset, as_of_date)

        start_date = _coerce_date(rows[-1]["s_date"])
        end_date = _coerce_date(rows[0]["e_date"])
        fy = str(rows[0]["fy"]) if rows[0].get("fy") else anchor.financial_year
        return PeriodContext(
            start_date=start_date,
            end_date=end_date,
            label=preset_display_name(preset),
            preset=preset,
            financial_year=fy,
            as_of_date=as_of_date,
            is_empty=start_date is None or end_date is None,
        )

    def _resolve_since_inception(self, as_of_date: date) -> PeriodContext:
        """Resolve earliest recorded warehouse/calendar date through `as_of_date`."""
        row = self.query_port.fetch_one(
            """
            SELECT MIN("Date") AS s_date, MAX("Date") AS e_date, MAX("Financial_Year") AS fy
            FROM silver.dim_calendar
            WHERE NOT COALESCE("Is_Future_Date", FALSE);
            """
        )
        if row is None or row.get("s_date") is None:
            return self._empty_context(PeriodPreset.SINCE_INCEPTION, as_of_date)

        s_date = _coerce_date(row["s_date"])
        e_date = _coerce_date(row["e_date"])
        if self._gold_has_monthly_dates():
            g_row = self.query_port.fetch_one(
                'SELECT MIN("MONTH_START_DATE") AS g_min FROM gold.core_monthly_fact;'
            )
            g_min = _coerce_date(g_row["g_min"]) if g_row else None
            if g_min is not None and (s_date is None or g_min > s_date):
                s_date = g_min

        return PeriodContext(
            start_date=s_date,
            end_date=e_date,
            label="Since Inception",
            preset=PeriodPreset.SINCE_INCEPTION,
            financial_year=str(row["fy"]) if row.get("fy") else None,
            as_of_date=as_of_date,
            is_empty=s_date is None or e_date is None,
        )

    def _resolve_custom(self, req: PeriodRequest, as_of_date: date) -> PeriodContext:
        """Resolve an explicit inclusive custom date window with FY context."""
        assert req.custom_start is not None and req.custom_end is not None
        fy_row = self.query_port.fetch_one(
            """
            SELECT "Financial_Year" AS fy
            FROM silver.dim_calendar
            WHERE "Date" = ?
            LIMIT 1;
            """,
            (req.custom_end,),
        )
        fy = str(fy_row["fy"]) if fy_row and fy_row.get("fy") else None
        return PeriodContext(
            start_date=req.custom_start,
            end_date=req.custom_end,
            label="Custom Range",
            preset=PeriodPreset.CUSTOM,
            financial_year=fy,
            as_of_date=as_of_date,
            is_empty=False,
        )

    def _resolve_comparison(self, req: PeriodRequest, primary: PeriodContext) -> ComparisonPeriod:
        """Compute comparison boundaries relative to the resolved primary period."""
        mode = req.comparison_mode
        if mode == ComparisonMode.CUSTOM:
            assert req.custom_comparison_start is not None
            assert req.custom_comparison_end is not None
            fy_row = self.query_port.fetch_one(
                'SELECT "Financial_Year" AS fy FROM silver.dim_calendar WHERE "Date" = ? LIMIT 1;',
                (req.custom_comparison_end,),
            )
            return ComparisonPeriod(
                mode=mode,
                start_date=req.custom_comparison_start,
                end_date=req.custom_comparison_end,
                label="Custom Comparison",
                financial_year=str(fy_row["fy"]) if fy_row and fy_row.get("fy") else None,
            )

        if primary.is_empty or primary.start_date is None or primary.end_date is None:
            return ComparisonPeriod(
                mode=mode,
                start_date=None,
                end_date=None,
                label="Comparison Unavailable",
                is_empty=True,
            )

        if mode == ComparisonMode.SAME_PERIOD_PREVIOUS_YEAR:
            comp_start = _shift_year_safe(primary.start_date, -1)
            comp_end = _shift_year_safe(primary.end_date, -1)
            fy_row = self.query_port.fetch_one(
                'SELECT "Financial_Year" AS fy FROM silver.dim_calendar WHERE "Date" = ? LIMIT 1;',
                (comp_end,),
            )
            if fy_row is None:
                return ComparisonPeriod(
                    mode=mode,
                    start_date=None,
                    end_date=None,
                    label="Prior Year Unavailable",
                    is_empty=True,
                )
            return ComparisonPeriod(
                mode=mode,
                start_date=comp_start,
                end_date=comp_end,
                label="Same Period Previous Year",
                financial_year=str(fy_row["fy"]) if fy_row.get("fy") else None,
            )

        if mode == ComparisonMode.FY_START_MONTH:
            target_fy = primary.financial_year
            row = (
                self.query_port.fetch_one(
                    """
                    SELECT
                        MIN("Start_of_Month") AS s_date,
                        MIN("End_of_Month") AS e_date
                    FROM silver.dim_calendar
                    WHERE "Financial_Year" = ?;
                    """,
                    (target_fy,),
                )
                if target_fy
                else None
            )
            s_date = _coerce_date(row.get("s_date")) if row else None
            e_date = _coerce_date(row.get("e_date")) if row else None
            if s_date is None or e_date is None:
                return ComparisonPeriod(
                    mode=mode,
                    start_date=None,
                    end_date=None,
                    label="FY Start Month Unavailable",
                    is_empty=True,
                )
            return ComparisonPeriod(
                mode=mode,
                start_date=s_date,
                end_date=e_date,
                label=f"FY {target_fy} Opening Month",
                financial_year=target_fy,
            )

        if mode == ComparisonMode.PRIOR_COMPLETED_FY:
            target_fy = primary.financial_year
            prev_fy_row = (
                self.query_port.fetch_one(
                    """
                    SELECT "Financial_Year" AS fy, MIN("Date") AS s_date, MAX("Date") AS e_date
                    FROM silver.dim_calendar
                    WHERE "Date" < ? AND "Financial_Year" <> ? AND "Financial_Year" IS NOT NULL
                    GROUP BY "Financial_Year"
                    ORDER BY e_date DESC
                    LIMIT 1;
                    """,
                    (primary.end_date, target_fy),
                )
                if target_fy
                else None
            )
            if prev_fy_row is None or prev_fy_row.get("s_date") is None:
                return ComparisonPeriod(
                    mode=mode,
                    start_date=None,
                    end_date=None,
                    label="Prior FY Unavailable",
                    is_empty=True,
                )
            fy_lbl = str(prev_fy_row["fy"])
            return ComparisonPeriod(
                mode=mode,
                start_date=_coerce_date(prev_fy_row["s_date"]),
                end_date=_coerce_date(prev_fy_row["e_date"]),
                label=f"Prior FY {fy_lbl}",
                financial_year=fy_lbl,
            )

        # PREVIOUS_PERIOD: align to prior whole months if primary aligns on calendar month bounds
        bounds_check = self.query_port.fetch_one(
            """
            SELECT
                MIN("Start_of_Month") AS first_m_start,
                MAX("End_of_Month") AS last_m_end,
                COUNT(DISTINCT "Start_of_Month") AS month_count
            FROM silver.dim_calendar
            WHERE "Date" BETWEEN ? AND ?;
            """,
            (primary.start_date, primary.end_date),
        )
        if (
            bounds_check
            and _coerce_date(bounds_check["first_m_start"]) == primary.start_date
            and _coerce_date(bounds_check["last_m_end"]) == primary.end_date
        ):
            m_count = int(bounds_check["month_count"])
            prior_rows = self.query_port.fetch_all(
                """
                SELECT DISTINCT "Start_of_Month" AS s_date, "End_of_Month" AS e_date, "Financial_Year" AS fy
                FROM silver.dim_calendar
                WHERE "End_of_Month" < ?
                ORDER BY "Start_of_Month" DESC
                LIMIT ?;
                """,
                (primary.start_date, m_count),
            )
            if len(prior_rows) == m_count:
                return ComparisonPeriod(
                    mode=mode,
                    start_date=_coerce_date(prior_rows[-1]["s_date"]),
                    end_date=_coerce_date(prior_rows[0]["e_date"]),
                    label="Previous Equivalent Period",
                    financial_year=str(prior_rows[0]["fy"]) if prior_rows[0].get("fy") else None,
                )
            return ComparisonPeriod(
                mode=mode,
                start_date=None,
                end_date=None,
                label="Previous Period Unavailable",
                is_empty=True,
            )

        # Day-aligned equivalent window shift
        span_days = (primary.end_date - primary.start_date).days
        comp_end = primary.start_date - timedelta(days=1)
        comp_start = comp_end - timedelta(days=span_days)
        fy_row = self.query_port.fetch_one(
            'SELECT "Financial_Year" AS fy FROM silver.dim_calendar WHERE "Date" = ? LIMIT 1;',
            (comp_end,),
        )
        return ComparisonPeriod(
            mode=mode,
            start_date=comp_start,
            end_date=comp_end,
            label="Previous Equivalent Period",
            financial_year=str(fy_row["fy"]) if fy_row and fy_row.get("fy") else None,
            is_empty=fy_row is None,
        )

    @staticmethod
    def _empty_context(
        preset: PeriodPreset,
        as_of_date: date | None,
        label: str | None = None,
    ) -> PeriodContext:
        """Build an explicit empty `PeriodContext` without raising SQL errors."""
        return PeriodContext(
            start_date=None,
            end_date=None,
            label=label or preset_display_name(preset),
            preset=preset,
            as_of_date=as_of_date,
            is_empty=True,
        )
