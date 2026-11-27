"""Studio-owned SQLite persistence manager.

Persists saved views, report definitions, recent profiles, and export history.
Never writes to PFETL DuckDB, Control Plane SQLite, Settings TOML or FinancialRules TOML.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any

from platformdirs import user_data_dir
from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.profile import PFETLProfile

CURRENT_SCHEMA_VERSION = 5


def get_studio_state_path() -> Path:
    """Resolve destination path for Studio state database.

    Returns
    -------
    Path
        Path to studio.sqlite in user data directory.
    """
    data_dir = Path(user_data_dir("PFETL Analytics Studio"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "studio.sqlite"


class RecentProfileRecord(BaseModel):
    """Persisted recent or saved profile metadata record."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    label: str
    settings_path: Path
    rules_path: Path
    last_opened_at: datetime
    is_pinned: bool = False
    notes: str = ""


class SavedViewKind(StrEnum):
    """Supported persisted Explorer view kinds."""

    GUIDED = "guided"
    SQL = "sql"


class SqlDatasource(StrEnum):
    """Approved read-only datasources for Advanced SQL and SQL Saved Views."""

    DUCKDB = "duckdb"
    CONTROL_PLANE = "control_plane"


class SavedViewDefinition(BaseModel):
    """Persisted Guided or Expert SQL view definition (never stores result rows)."""

    model_config = ConfigDict(frozen=True)

    view_id: str
    profile_id: str
    name: str
    description: str = ""
    kind: SavedViewKind
    datasource: SqlDatasource = SqlDatasource.DUCKDB
    page: str = "explorer"
    query_spec: dict[str, Any] | None = None
    sql_text: str = ""
    period_override: dict[str, Any] | None = None
    filter_override: dict[str, Any] | None = None
    is_starter: bool = False
    category: str = "Custom"
    created_at: datetime
    updated_at: datetime


class SavedReportRecord(BaseModel):
    """Persisted custom or cloned report definition record in `studio.sqlite`."""

    model_config = ConfigDict(frozen=True)

    report_id: str
    profile_id: str
    name: str
    description: str = ""
    definition_json: str
    created_at: datetime
    updated_at: datetime


class ReportRunRecord(BaseModel):
    """Persisted report execution provenance & output metadata in `studio.sqlite`."""

    model_config = ConfigDict(frozen=True)

    report_run_id: str
    report_id: str
    pfetl_run_id: str
    period_json: str
    output_format: str
    output_path: str = ""
    generated_at: datetime


_DEFAULT_SQL_STARTERS: tuple[tuple[str, str, str, SqlDatasource, str], ...] = (
    (
        "Net Worth & Savings Attribution",
        "Wealth",
        "Monthly market net worth, assets, liabilities, income, expense, and market return flow.",
        SqlDatasource.DUCKDB,
        (
            'SELECT "MONTH_END_DATE", "Total_Net_Worth_Market", "Total_Assets_Market",\n'
            '       "Total_Liabilities", "Total_Income", "Total_Expense",\n'
            '       "Net_Savings_Flow", "Market_Return_Flow"\n'
            'FROM "gold"."core_monthly_fact"\n'
            'ORDER BY "MONTH_END_DATE" DESC'
        ),
    ),
    (
        "Cashflow Efficiency & Resilience",
        "Household",
        "Savings rate, core expense share, investment rate, emergency coverage, and debt ratio.",
        SqlDatasource.DUCKDB,
        (
            'SELECT "YEAR_MONTH", "Savings_Rate_Pct", "Core_Expense_Share_Pct",\n'
            '       "Investment_Rate_Pct", "Emergency_Fund_Coverage", "Debt_to_Asset_Ratio"\n'
            'FROM "gold"."cashflow_efficiency_analytics"\n'
            'ORDER BY "MONTH_END_DATE" DESC'
        ),
    ),
    (
        "FIRE Trajectory & Runway (P50)",
        "FIRE",
        "FI coverage %, Target/Lean/Coast FI corpora, median months to FI, and portfolio runway.",
        SqlDatasource.DUCKDB,
        (
            'SELECT "YEAR_MONTH", "Current_FI_Coverage_Pct", "Target_FI_Today",\n'
            '       "Lean_FI_Today", "Coast_FI_Today", "Months_To_FI_Base_P50",\n'
            '       "Runway_Months_Base_P50"\n'
            'FROM "gold"."wealth_fire_analytics"\n'
            'ORDER BY "MONTH_END_DATE" DESC'
        ),
    ),
    (
        "Top Alpha ISINs (Latest Close)",
        "Investments",
        "Latest closing-date ISIN XIRR vs Benchmark XIRR, Active Return, and FX XIRR impact.",
        SqlDatasource.DUCKDB,
        (
            'SELECT "Closing_Date", "ISIN", "Total_Invested_Value", "Total_Current_Value",\n'
            '       "Unrealized_PL", "XIRR", "BM_XIRR", "Active_Return", "FX_XIRR_Impact"\n'
            'FROM "gold"."investment_by_isin"\n'
            'WHERE "Closing_Date" = (SELECT MAX("Closing_Date") FROM "gold"."investment_by_isin")\n'
            'ORDER BY "Active_Return" DESC'
        ),
    ),
    (
        "Tax-Loss Harvesting Lots (88-Col)",
        "Tax",
        "Open lots with unrealized losses, days to LTCG, and harvest recommendations.",
        SqlDatasource.DUCKDB,
        (
            'SELECT "Lot_ID", "ISIN", "Buy_Date", "Quantity", "Buy_Value",\n'
            '       "Close_Value", "Unrealized_Loss", "Days_To_LTCG", "Harvest_Recommendation"\n'
            'FROM "silver"."fact_investment_lot_analytics"\n'
            'WHERE "Closing_Date" = (SELECT MAX("Closing_Date") FROM "silver"."fact_investment_lot_analytics")\n'
            '  AND "Unrealized_Loss" < 0\n'
            'ORDER BY "Unrealized_Loss" ASC'
        ),
    ),
    (
        "Budget Variance & Runway",
        "Household",
        "Actual core/non-core expense vs budget target and zero-income runway months.",
        SqlDatasource.DUCKDB,
        (
            'SELECT "YEAR_MONTH", "Total_Income", "Actual_Core_Expense",\n'
            '       "Actual_NonCore_Expense", "Budget_Total_Expense_Target",\n'
            '       "Zero_Income_Runway_Months"\n'
            'FROM "gold"."forecast_budget_variance"\n'
            'ORDER BY "MONTH_END_DATE" DESC'
        ),
    ),
    (
        "Tax Events Summary by Source",
        "Tax",
        "Aggregated tax events, INR amounts, and estimated tax grouped by FY, source, and status.",
        SqlDatasource.DUCKDB,
        (
            'SELECT "FY", "Source_Type", "Income_Head", "Tax_Sub_Head", "Tax_Status",\n'
            '       COUNT(*) AS events, ROUND(SUM("Amount_INR"), 2) AS total_inr,\n'
            '       ROUND(SUM("Estimated_Tax"), 2) AS est_tax\n'
            'FROM "silver"."fact_tax_events"\n'
            "GROUP BY 1, 2, 3, 4, 5\n"
            "ORDER BY total_inr DESC"
        ),
    ),
    (
        "PFETL Run & Sync Health (CP)",
        "Operations",
        "Control Plane run history with commit state, Core version, and timestamps.",
        SqlDatasource.CONTROL_PLANE,
        (
            "SELECT run_id, started_at, finished_at, status, commit_state, application_version\n"
            "FROM cp_runs\n"
            "ORDER BY started_at DESC"
        ),
    ),
)


class StudioStateStore:
    """Manager for Studio-owned SQLite state database."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize state store and ensure schema bootstrap.

        Parameters
        ----------
        db_path : Path | None, optional
            Path to SQLite database file, by default resolved from user data dir.
        """
        self.db_path = db_path or get_studio_state_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Retrieve active SQLite connection or establish a new one.

        Returns
        -------
        sqlite3.Connection
            Active connection.
        """
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                autocommit=True,
            )
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for local concurrent read/write isolation
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
        return self._conn

    def _init_database(self) -> None:
        """Bootstrap database tables and execute additive schema migrations."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("PRAGMA user_version;")
        row = cursor.fetchone()
        version = row[0] if row else 0

        if version < 1:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS recent_profiles (
                    profile_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    settings_path TEXT NOT NULL,
                    rules_path TEXT NOT NULL,
                    last_opened_at TEXT NOT NULL
                );
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS app_preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            cursor.execute("PRAGMA user_version = 1;")
            conn.commit()

        if version < 2:
            cols = {
                str(r["name"])
                for r in cursor.execute("PRAGMA table_info(recent_profiles);").fetchall()
            }
            if "is_pinned" not in cols:
                cursor.execute(
                    "ALTER TABLE recent_profiles ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0;"
                )
            if "notes" not in cols:
                cursor.execute(
                    "ALTER TABLE recent_profiles ADD COLUMN notes TEXT NOT NULL DEFAULT '';"
                )
            cursor.execute("PRAGMA user_version = 2;")
            conn.commit()

        if version < 3:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_views (
                    view_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    datasource TEXT NOT NULL DEFAULT 'duckdb',
                    page TEXT NOT NULL DEFAULT 'explorer',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    spec_json TEXT NOT NULL DEFAULT '{}',
                    sql_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(profile_id, name)
                );
                """
            )
            cursor.execute("PRAGMA user_version = 3;")
            conn.commit()

        if version < 4:
            sv_cols = {
                str(r["name"]) for r in cursor.execute("PRAGMA table_info(saved_views);").fetchall()
            }
            if "is_starter" not in sv_cols:
                cursor.execute(
                    "ALTER TABLE saved_views ADD COLUMN is_starter INTEGER NOT NULL DEFAULT 0;"
                )
            if "category" not in sv_cols:
                cursor.execute(
                    "ALTER TABLE saved_views ADD COLUMN category TEXT NOT NULL DEFAULT 'Custom';"
                )
            cursor.execute("PRAGMA user_version = 4;")
            conn.commit()

        if version < 5:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_reports (
                    report_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    definition_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(profile_id, name)
                );
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS report_runs (
                    report_run_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    pfetl_run_id TEXT NOT NULL,
                    period_json TEXT NOT NULL,
                    output_format TEXT NOT NULL,
                    output_path TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL
                );
                """
            )
            cursor.execute("PRAGMA user_version = 5;")
            conn.commit()

    def record_profile_opened(
        self,
        profile: PFETLProfile,
        opened_at: datetime | None = None,
    ) -> None:
        """Upsert a profile into the recent profiles table.

        Parameters
        ----------
        profile : PFETLProfile
            The active PFETL profile.
        opened_at : datetime | None, optional
            Timestamp of opening, by default UTC now.
        """
        timestamp = (opened_at or datetime.now(UTC)).isoformat()
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO recent_profiles (
                    profile_id, label, settings_path, rules_path, last_opened_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    label = excluded.label,
                    settings_path = excluded.settings_path,
                    rules_path = excluded.rules_path,
                    last_opened_at = excluded.last_opened_at;
                """,
                (
                    profile.profile_id,
                    profile.label,
                    str(profile.settings_path),
                    str(profile.rules_path),
                    timestamp,
                ),
            )

    def save_profile(
        self,
        profile: PFETLProfile,
        *,
        label: str | None = None,
        is_pinned: bool = False,
        notes: str = "",
        opened_at: datetime | None = None,
    ) -> None:
        """Save or update a profile with optional custom label, pin state, and notes."""
        timestamp = (opened_at or datetime.now(UTC)).isoformat()
        resolved_label = (label or profile.label).strip() or profile.label
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO recent_profiles (
                    profile_id, label, settings_path, rules_path, last_opened_at, is_pinned, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    label = excluded.label,
                    settings_path = excluded.settings_path,
                    rules_path = excluded.rules_path,
                    last_opened_at = excluded.last_opened_at,
                    is_pinned = CASE WHEN excluded.is_pinned = 1 THEN 1 ELSE recent_profiles.is_pinned END,
                    notes = CASE WHEN excluded.notes <> '' THEN excluded.notes ELSE recent_profiles.notes END;
                """,
                (
                    profile.profile_id,
                    resolved_label,
                    str(profile.settings_path),
                    str(profile.rules_path),
                    timestamp,
                    1 if is_pinned else 0,
                    notes.strip(),
                ),
            )

    def set_profile_pinned(self, profile_id: str, pinned: bool) -> None:
        """Pin or unpin a saved profile so it stays at the top of the Profile Hub."""
        conn = self._get_connection()
        with conn:
            conn.execute(
                "UPDATE recent_profiles SET is_pinned = ? WHERE profile_id = ?;",
                (1 if pinned else 0, profile_id),
            )

    def rename_profile(self, profile_id: str, new_label: str) -> None:
        """Rename a saved profile's display label."""
        clean = new_label.strip()
        if not clean:
            raise ValueError("Profile label cannot be empty.")
        conn = self._get_connection()
        with conn:
            conn.execute(
                "UPDATE recent_profiles SET label = ? WHERE profile_id = ?;",
                (clean, profile_id),
            )

    def get_recent_profiles(self, limit: int = 25) -> list[RecentProfileRecord]:
        """Fetch saved/recent profiles ordered by pinned first, then most recently opened."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT profile_id, label, settings_path, rules_path, last_opened_at, is_pinned, notes
            FROM recent_profiles
            ORDER BY is_pinned DESC, last_opened_at DESC
            LIMIT ?;
            """,
            (limit,),
        )
        records: list[RecentProfileRecord] = []
        for row in cursor.fetchall():
            records.append(
                RecentProfileRecord(
                    profile_id=row["profile_id"],
                    label=row["label"],
                    settings_path=Path(row["settings_path"]),
                    rules_path=Path(row["rules_path"]),
                    last_opened_at=datetime.fromisoformat(row["last_opened_at"]),
                    is_pinned=bool(row["is_pinned"]),
                    notes=str(row["notes"] or ""),
                )
            )
        return records

    def remove_recent_profile(self, profile_id: str) -> None:
        """Remove a profile from the recent profiles list.

        Parameters
        ----------
        profile_id : str
            Identifier of profile to remove.
        """
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM recent_profiles WHERE profile_id = ?;", (profile_id,))

    def clear_recent_profiles(self) -> None:
        """Clear all entries in the recent profiles table."""
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM recent_profiles;")

    def get_preference(self, key: str, default: str | None = None) -> str | None:
        """Retrieve an application preference value.

        Parameters
        ----------
        key : str
            Preference key name.
        default : str | None, optional
            Default value if key is not found, by default None.

        Returns
        -------
        str | None
            Stored preference value or default.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM app_preferences WHERE key = ?;", (key,))
        row = cursor.fetchone()
        if row is not None:
            return str(row["value"])
        return default

    def set_preference(self, key: str, value: str) -> None:
        """Set or update an application preference value.

        Parameters
        ----------
        key : str
            Preference key name.
        value : str
            Preference value to store.
        """
        timestamp = datetime.now(UTC).isoformat()
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO app_preferences (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at;
                """,
                (key, value, timestamp),
            )

    def delete_preference(self, key: str) -> None:
        """Delete an application preference entry.

        Parameters
        ----------
        key : str
            Preference key name.
        """
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM app_preferences WHERE key = ?;", (key,))

    @staticmethod
    def _row_to_saved_view(row: sqlite3.Row) -> SavedViewDefinition:
        ctx_raw = json.loads(str(row["context_json"] or "{}"))
        spec_raw = json.loads(str(row["spec_json"] or "{}"))
        return SavedViewDefinition(
            view_id=str(row["view_id"]),
            profile_id=str(row["profile_id"]),
            name=str(row["name"]),
            description=str(row["description"] or ""),
            kind=SavedViewKind(str(row["kind"])),
            datasource=SqlDatasource(str(row["datasource"] or SqlDatasource.DUCKDB.value)),
            page=str(row["page"] or "explorer"),
            query_spec=spec_raw if spec_raw else None,
            sql_text=str(row["sql_text"] or ""),
            period_override=ctx_raw.get("period_override"),
            filter_override=ctx_raw.get("filter_override"),
            is_starter=bool(row["is_starter"]) if "is_starter" in row.keys() else False,
            category=str(row["category"] or "Custom") if "category" in row.keys() else "Custom",
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def create_saved_view(
        self,
        profile_id: str,
        name: str,
        kind: SavedViewKind,
        *,
        description: str = "",
        datasource: SqlDatasource = SqlDatasource.DUCKDB,
        page: str = "explorer",
        query_spec: dict[str, Any] | None = None,
        sql_text: str = "",
        period_override: dict[str, Any] | None = None,
        filter_override: dict[str, Any] | None = None,
        is_starter: bool = False,
        category: str = "Custom",
    ) -> SavedViewDefinition:
        """Persist a new SavedViewDefinition, rejecting duplicate names per profile."""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Saved view name cannot be empty.")
        if kind == SavedViewKind.GUIDED and not query_spec:
            raise ValueError("Guided saved view requires a non-empty query_spec.")
        if kind == SavedViewKind.SQL and not sql_text.strip():
            raise ValueError("SQL saved view requires non-empty sql_text.")

        now = datetime.now(UTC).isoformat()
        view_id = f"view-{uuid.uuid4().hex[:12]}"
        ctx_json = json.dumps(
            {
                "period_override": period_override,
                "filter_override": filter_override,
            },
            sort_keys=True,
            default=str,
        )
        spec_json = json.dumps(query_spec or {}, sort_keys=True, default=str)

        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO saved_views (
                        view_id, profile_id, name, description, kind, datasource, page,
                        context_json, spec_json, sql_text, is_starter, category,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        view_id,
                        profile_id,
                        clean_name,
                        description.strip(),
                        kind.value,
                        datasource.value,
                        page,
                        ctx_json,
                        spec_json,
                        sql_text,
                        1 if is_starter else 0,
                        category.strip() or "Custom",
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"A saved view named '{clean_name}' already exists for this profile."
            ) from exc

        created = self.get_saved_view(view_id)
        assert created is not None
        return created

    def get_saved_view(self, view_id: str) -> SavedViewDefinition | None:
        """Fetch a saved view by `view_id`, or None if missing."""
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM saved_views WHERE view_id = ?;", (view_id,)).fetchone()
        return self._row_to_saved_view(row) if row is not None else None

    def list_saved_views(
        self,
        profile_id: str,
        *,
        kind: SavedViewKind | None = None,
    ) -> list[SavedViewDefinition]:
        """List saved views for `profile_id`, optionally filtered by `kind`."""
        conn = self._get_connection()
        if kind is not None:
            rows = conn.execute(
                """
                SELECT * FROM saved_views
                WHERE profile_id = ? AND kind = ?
                ORDER BY updated_at DESC, name ASC;
                """,
                (profile_id, kind.value),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM saved_views
                WHERE profile_id = ?
                ORDER BY updated_at DESC, name ASC;
                """,
                (profile_id,),
            ).fetchall()
        return [self._row_to_saved_view(r) for r in rows]

    def update_saved_view(
        self,
        view_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        datasource: SqlDatasource | None = None,
        category: str | None = None,
        query_spec: dict[str, Any] | None = None,
        sql_text: str | None = None,
        period_override: dict[str, Any] | None = None,
        filter_override: dict[str, Any] | None = None,
    ) -> SavedViewDefinition:
        """Update mutable fields of an existing saved view and refresh `updated_at`."""
        existing = self.get_saved_view(view_id)
        if existing is None:
            raise KeyError(f"Saved view '{view_id}' not found.")

        new_name = name.strip() if name is not None else existing.name
        if not new_name:
            raise ValueError("Saved view name cannot be empty.")
        new_desc = description.strip() if description is not None else existing.description
        new_ds = datasource if datasource is not None else existing.datasource
        new_cat = (category.strip() or "Custom") if category is not None else existing.category
        new_spec = query_spec if query_spec is not None else (existing.query_spec or {})
        new_sql = sql_text if sql_text is not None else existing.sql_text
        new_period = period_override if period_override is not None else existing.period_override
        new_filter = filter_override if filter_override is not None else existing.filter_override
        now = datetime.now(UTC).isoformat()

        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE saved_views
                    SET name = ?, description = ?, datasource = ?, category = ?,
                        context_json = ?, spec_json = ?, sql_text = ?, updated_at = ?
                    WHERE view_id = ?;
                    """,
                    (
                        new_name,
                        new_desc,
                        new_ds.value,
                        new_cat,
                        json.dumps(
                            {"period_override": new_period, "filter_override": new_filter},
                            sort_keys=True,
                            default=str,
                        ),
                        json.dumps(new_spec, sort_keys=True, default=str),
                        new_sql,
                        now,
                        view_id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"A saved view named '{new_name}' already exists for this profile."
            ) from exc

        updated = self.get_saved_view(view_id)
        assert updated is not None
        return updated

    def delete_saved_view(self, view_id: str) -> None:
        """Delete a saved view by `view_id`."""
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM saved_views WHERE view_id = ?;", (view_id,))

    def seed_default_sql_starters(
        self,
        profile_id: str,
        *,
        force_restore: bool = False,
    ) -> int:
        """Seed default SQL starter packs into `saved_views` in `studio.sqlite`."""
        pref_key = f"sql_starters_seeded:{profile_id}"
        if not force_restore and self.get_preference(pref_key) == "1":
            return 0

        existing_names = {v.name for v in self.list_saved_views(profile_id)}
        inserted = 0
        for name, category, desc, ds, sql in _DEFAULT_SQL_STARTERS:
            if name in existing_names:
                continue
            self.create_saved_view(
                profile_id,
                name,
                SavedViewKind.SQL,
                description=desc,
                datasource=ds,
                sql_text=sql,
                is_starter=True,
                category=category,
            )
            inserted += 1
        self.set_preference(pref_key, "1")
        return inserted

    @staticmethod
    def _row_to_saved_report(row: sqlite3.Row) -> SavedReportRecord:
        """Convert a `saved_reports` SQLite row into a frozen `SavedReportRecord`."""
        return SavedReportRecord(
            report_id=str(row["report_id"]),
            profile_id=str(row["profile_id"]),
            name=str(row["name"]),
            description=str(row["description"] or ""),
            definition_json=str(row["definition_json"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _row_to_report_run(row: sqlite3.Row) -> ReportRunRecord:
        """Convert a `report_runs` SQLite row into a frozen `ReportRunRecord`."""
        return ReportRunRecord(
            report_run_id=str(row["report_run_id"]),
            report_id=str(row["report_id"]),
            pfetl_run_id=str(row["pfetl_run_id"]),
            period_json=str(row["period_json"]),
            output_format=str(row["output_format"]),
            output_path=str(row["output_path"] or ""),
            generated_at=datetime.fromisoformat(str(row["generated_at"])),
        )

    def save_report_definition(
        self,
        profile_id: str,
        name: str,
        definition_json: str,
        *,
        report_id: str | None = None,
        description: str = "",
    ) -> SavedReportRecord:
        """Create or update a persisted report definition in `saved_reports`."""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Report name must not be empty.")
        rid = report_id or str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        conn = self._get_connection()
        existing = self.get_report_definition(rid)
        try:
            with conn:
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO saved_reports (
                            report_id, profile_id, name, description,
                            definition_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            rid,
                            profile_id,
                            clean_name,
                            description.strip(),
                            definition_json,
                            now,
                            now,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE saved_reports
                        SET name = ?, description = ?, definition_json = ?, updated_at = ?
                        WHERE report_id = ?;
                        """,
                        (
                            clean_name,
                            description.strip(),
                            definition_json,
                            now,
                            rid,
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"A saved report named '{clean_name}' already exists for this profile."
            ) from exc

        saved = self.get_report_definition(rid)
        assert saved is not None
        return saved

    def get_report_definition(self, report_id: str) -> SavedReportRecord | None:
        """Fetch a single saved report definition by `report_id`."""
        conn = self._get_connection()
        row = conn.execute(
            """
            SELECT report_id, profile_id, name, description,
                   definition_json, created_at, updated_at
            FROM saved_reports
            WHERE report_id = ?;
            """,
            (report_id,),
        ).fetchone()
        return self._row_to_saved_report(row) if row is not None else None

    def list_report_definitions(self, profile_id: str) -> list[SavedReportRecord]:
        """List saved report definitions for `profile_id`, newest first."""
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT report_id, profile_id, name, description,
                   definition_json, created_at, updated_at
            FROM saved_reports
            WHERE profile_id = ?
            ORDER BY updated_at DESC, name ASC;
            """,
            (profile_id,),
        ).fetchall()
        return [self._row_to_saved_report(r) for r in rows]

    def delete_report_definition(self, report_id: str) -> None:
        """Delete a saved report definition by `report_id`."""
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM saved_reports WHERE report_id = ?;", (report_id,))

    def record_report_run(
        self,
        report_id: str,
        pfetl_run_id: str,
        period_json: str,
        output_format: str,
        *,
        output_path: str = "",
        generated_at: datetime | None = None,
    ) -> ReportRunRecord:
        """Persist a report execution / export metadata entry in `report_runs`."""
        run_record_id = str(uuid.uuid4())
        timestamp = (generated_at or datetime.now(UTC)).isoformat()
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO report_runs (
                    report_run_id, report_id, pfetl_run_id, period_json,
                    output_format, output_path, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run_record_id,
                    report_id,
                    pfetl_run_id,
                    period_json,
                    output_format,
                    output_path,
                    timestamp,
                ),
            )
        return ReportRunRecord(
            report_run_id=run_record_id,
            report_id=report_id,
            pfetl_run_id=pfetl_run_id,
            period_json=period_json,
            output_format=output_format,
            output_path=output_path,
            generated_at=datetime.fromisoformat(timestamp),
        )

    def list_report_runs(
        self,
        *,
        report_id: str | None = None,
        limit: int = 50,
    ) -> list[ReportRunRecord]:
        """List recent report execution records, newest first."""
        conn = self._get_connection()
        if report_id is not None:
            rows = conn.execute(
                """
                SELECT report_run_id, report_id, pfetl_run_id, period_json,
                       output_format, output_path, generated_at
                FROM report_runs
                WHERE report_id = ?
                ORDER BY generated_at DESC
                LIMIT ?;
                """,
                (report_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT report_run_id, report_id, pfetl_run_id, period_json,
                       output_format, output_path, generated_at
                FROM report_runs
                ORDER BY generated_at DESC
                LIMIT ?;
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_report_run(r) for r in rows]

    def close(self) -> None:
        """Close active SQLite connection if open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> StudioStateStore:
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Context manager exit."""
        self.close()
