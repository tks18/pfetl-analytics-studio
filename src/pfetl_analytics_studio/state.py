"""Studio-owned SQLite persistence manager.

Persists saved views, report definitions, recent profiles, and export history.
Never writes to PFETL DuckDB, Control Plane SQLite, Settings TOML or FinancialRules TOML.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from platformdirs import user_data_dir
from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.profile import PFETLProfile

CURRENT_SCHEMA_VERSION = 1


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
    """Persisted recent profile metadata record."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    label: str
    settings_path: Path
    rules_path: Path
    last_opened_at: datetime


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

    def get_recent_profiles(self, limit: int = 10) -> list[RecentProfileRecord]:
        """Fetch list of recently opened profiles ordered by last opened timestamp.

        Parameters
        ----------
        limit : int, optional
            Maximum count of records to return, by default 10.

        Returns
        -------
        list[RecentProfileRecord]
            List of recent profiles, most recent first.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT profile_id, label, settings_path, rules_path, last_opened_at
            FROM recent_profiles
            ORDER BY last_opened_at DESC
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
