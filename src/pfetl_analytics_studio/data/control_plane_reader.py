"""Read-only gateway for PFETL SQLite Control Plane and Raw Store databases.

Enforces SQLite read-only URI mode and defensive query_only pragma.
Ensures connections are closed within each operation to prevent resource leaks.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

import polars as pl

from pfetl_analytics_studio.data.duckdb_reader import validate_identifier
from pfetl_analytics_studio.data.errors import (
    ControlPlaneState,
    DatabaseCorruptedError,
    DatabaseNotFoundError,
    PFETLDataAccessError,
    ReadOnlyViolationError,
)
from pfetl_analytics_studio.data.exports import write_rows_csv_file
from pfetl_analytics_studio.profile import PFETLProfile


class ControlPlaneReader:
    """Immutable read-only gateway for querying the PFETL SQLite Control Plane."""

    def __init__(self, profile: PFETLProfile) -> None:
        """Initialize Control Plane reader handle bound to an active profile.

        Parameters
        ----------
        profile : PFETLProfile
            Resolved immutable PFETL profile.
        """
        self.profile = profile

    @property
    def profile_id(self) -> str:
        """Return deterministic profile identifier."""
        return self.profile.profile_id

    @property
    def db_path(self) -> Path:
        """Return resolved Control Plane SQLite file path."""
        return self.profile.control_plane_path

    def get_state(self) -> ControlPlaneState:
        """Evaluate current availability state of the Control Plane SQLite store.

        Returns
        -------
        ControlPlaneState
            UNAVAILABLE if file does not exist, else AVAILABLE.
        """
        if not self.db_path.exists() or not self.db_path.is_file():
            return ControlPlaneState.UNAVAILABLE
        return ControlPlaneState.AVAILABLE

    def is_available(self) -> bool:
        """Check whether the Control Plane SQLite database is available.

        Returns
        -------
        bool
            True if database file exists and is readable.
        """
        return self.get_state() == ControlPlaneState.AVAILABLE

    def open_connection(self) -> sqlite3.Connection:
        """Open a read-only SQLite connection using URI mode."""
        return self._open_connection()

    def _open_connection(self) -> sqlite3.Connection:
        """Open a read-only SQLite connection using URI mode.

        Returns
        -------
        sqlite3.Connection
            Configured read-only SQLite connection.

        Raises
        ------
        DatabaseNotFoundError
            If database file does not exist.
        DatabaseCorruptedError
            If file is unreadable or not a valid SQLite database.
        PFETLDataAccessError
            For other internal connection failures.
        """
        if not self.db_path.exists() or not self.db_path.is_file():
            raise DatabaseNotFoundError(
                f"Control Plane SQLite database file not found: '{self.db_path}'.",
                path=self.db_path,
                profile_id=self.profile_id,
            )

        try:
            # POSIX-normalized URI file:<path>?mode=ro
            posix_path = self.db_path.resolve().as_posix()
            uri = f"file:{posix_path}?mode=ro"
            conn = sqlite3.connect(
                uri,
                uri=True,
                timeout=15.0,
                autocommit=True,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            return conn
        except sqlite3.DatabaseError as exc:
            msg = str(exc).lower()
            if "malformed" in msg or "not a database" in msg or "corrupt" in msg:
                raise DatabaseCorruptedError(
                    f"Control Plane SQLite database appears corrupted: {exc}",
                    path=self.db_path,
                    profile_id=self.profile_id,
                ) from exc
            raise PFETLDataAccessError(
                f"Failed to open Control Plane SQLite database: {exc}",
                path=self.db_path,
                profile_id=self.profile_id,
            ) from exc

    def fetch_df(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> pl.DataFrame:
        """Execute a read-only query and return results as a Polars DataFrame.

        Parameters
        ----------
        sql : str
            Parametrized SQL query string.
        params : Sequence[Any] | dict[str, Any], optional
            Positional or named query parameters, by default ().

        Returns
        -------
        pl.DataFrame
            Results converted to Polars DataFrame.
        """
        with contextlib.closing(self._open_connection()) as conn:
            try:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                cols = [desc[0] for desc in cursor.description or []]
                if not rows:
                    return pl.DataFrame(schema=cols)
                return pl.DataFrame([dict(row) for row in rows])
            except sqlite3.Error as exc:
                self._handle_query_error(exc)

    def fetch_all(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> list[dict[str, Any]]:
        """Execute query and return all rows as a list of column-keyed dictionaries.

        Parameters
        ----------
        sql : str
            Parametrized SQL query string.
        params : Sequence[Any] | dict[str, Any], optional
            Positional or named query parameters, by default ().

        Returns
        -------
        list[dict[str, Any]]
            Rows converted to dictionaries.
        """
        with contextlib.closing(self._open_connection()) as conn:
            try:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]
            except sqlite3.Error as exc:
                self._handle_query_error(exc)

    def fetch_one(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> dict[str, Any] | None:
        """Execute query and return the first row as a dictionary, or None.

        Parameters
        ----------
        sql : str
            Parametrized SQL query string.
        params : Sequence[Any] | dict[str, Any], optional
            Positional or named query parameters, by default ().

        Returns
        -------
        dict[str, Any] | None
            Single row dictionary, or None if no records match.
        """
        with contextlib.closing(self._open_connection()) as conn:
            try:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                row = cursor.fetchone()
                return dict(row) if row is not None else None
            except sqlite3.Error as exc:
                self._handle_query_error(exc)

    def export_csv_file(
        self,
        sql: str,
        target_path: Path,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> int:
        """Stream a read-only Control Plane SQLite query to `target_path` as CSV."""
        with contextlib.closing(self._open_connection()) as conn:
            try:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                cols = [desc[0] for desc in cursor.description or []]

                def _iter_chunks() -> Any:
                    while True:
                        chunk = cursor.fetchmany(4096)
                        if not chunk:
                            break
                        yield [tuple(row) for row in chunk]

                return write_rows_csv_file(cols, _iter_chunks(), target_path)
            except sqlite3.Error as exc:
                self._handle_query_error(exc)

    def fetch_scalar(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> Any:
        """Execute query and return a single scalar value from the first row and column.

        Parameters
        ----------
        sql : str
            Parametrized SQL query string.
        params : Sequence[Any] | dict[str, Any], optional
            Positional or named query parameters, by default ().

        Returns
        -------
        Any
            First scalar value, or None if no records return.
        """
        with contextlib.closing(self._open_connection()) as conn:
            try:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                row = cursor.fetchone()
                return row[0] if row is not None else None
            except sqlite3.Error as exc:
                self._handle_query_error(exc)

    def table_exists(self, table: str) -> bool:
        """Verify whether a table exists in the Control Plane database.

        Parameters
        ----------
        table : str
            Table name.

        Returns
        -------
        bool
            True if table exists, False otherwise.
        """
        valid_table = validate_identifier(table)
        query = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1;"
        return self.fetch_scalar(query, (valid_table,)) is not None

    def bounded_preview(
        self,
        table: str,
        limit: int = 100,
    ) -> pl.DataFrame:
        """Fetch bounded preview of rows from a Control Plane table.

        Parameters
        ----------
        table : str
            Table name.
        limit : int, optional
            Requested row limit (clamped between 1 and 500), by default 100.

        Returns
        -------
        pl.DataFrame
            Bounded preview DataFrame.
        """
        valid_table = validate_identifier(table)
        safe_limit = max(1, min(limit, 500))
        sql = f'SELECT * FROM "{valid_table}" LIMIT ?;'
        return self.fetch_df(sql, (safe_limit,))

    def _handle_query_error(self, exc: sqlite3.Error) -> NoReturn:
        """Translate SQLite query errors into typed Studio domain exceptions."""
        msg = str(exc)
        lower_msg = msg.lower()
        if (
            "readonly" in lower_msg
            or "read-only" in lower_msg
            or "attempt to write a readonly database" in lower_msg
            or "cannot modify" in lower_msg
        ):
            raise ReadOnlyViolationError(
                f"Security violation: Write statement rejected on read-only Control Plane: {exc}",
                path=self.db_path,
                profile_id=self.profile_id,
            ) from exc
        raise PFETLDataAccessError(
            f"Control Plane SQLite query execution error: {exc}",
            path=self.db_path,
            profile_id=self.profile_id,
        ) from exc
