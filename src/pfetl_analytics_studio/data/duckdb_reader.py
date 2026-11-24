"""Read-only gateway for PFETL DuckDB warehouse databases.

Strictly enforces read-only connections and verifies that the PFETL pipeline lock
is not active before opening any connection. Does not allow connections to escape
to analytical services or UI pages.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

import duckdb
import polars as pl

from pfetl_analytics_studio.data.errors import (
    DatabaseCorruptedError,
    DatabaseNotFoundError,
    PFETLDataAccessError,
    PFETLLockedError,
    ReadOnlyViolationError,
    WarehouseState,
)
from pfetl_analytics_studio.profile import PFETLProfile

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(name: str) -> str:
    """Validate that a SQL identifier contains only alphanumeric characters and underscores.

    Parameters
    ----------
    name : str
        Schema, table, or column identifier.

    Returns
    -------
    str
        Validated identifier string.

    Raises
    ------
    ValueError
        If the identifier contains illegal characters or is empty.
    """
    clean_name = name.strip()
    if not clean_name or not IDENTIFIER_PATTERN.match(clean_name):
        raise ValueError(f"Security violation: Invalid SQL identifier '{name}'.")
    return clean_name


class DuckDBReader:
    """Immutable read-only gateway for querying the PFETL DuckDB warehouse."""

    def __init__(self, profile: PFETLProfile) -> None:
        """Initialize DuckDB reader handle bound to an active profile.

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
        """Return resolved DuckDB file path."""
        return self.profile.duckdb_path

    @property
    def lock_path(self) -> Path:
        """Return resolved PFETL pipeline lock file path."""
        return self.profile.lock_path

    def get_state(self) -> WarehouseState:
        """Evaluate current availability state of the DuckDB warehouse.

        Returns
        -------
        WarehouseState
            LOCKED if pipeline.lock exists, UNAVAILABLE if DB missing, else AVAILABLE.
        """
        if self.is_locked():
            return WarehouseState.LOCKED
        if not self.db_path.exists() or not self.db_path.is_file():
            return WarehouseState.UNAVAILABLE
        return WarehouseState.AVAILABLE

    def is_locked(self) -> bool:
        """Check whether PFETL is actively executing and pipeline.lock exists.

        Returns
        -------
        bool
            True if pipeline.lock exists on disk, False otherwise.
        """
        return self.lock_path.exists()

    def is_available(self) -> bool:
        """Check whether the warehouse is unlocked and ready for queries.

        Returns
        -------
        bool
            True if warehouse file exists and is not locked.
        """
        return self.get_state() == WarehouseState.AVAILABLE

    def _open_connection(self) -> duckdb.DuckDBPyConnection:
        """Open a single read-only DuckDB connection.

        Returns
        -------
        duckdb.DuckDBPyConnection
            Opened read-only connection.

        Raises
        ------
        PFETLLockedError
            If warehouse is currently locked by Core pipeline.
        DatabaseNotFoundError
            If DuckDB file does not exist.
        DatabaseCorruptedError
            If file is unreadable or corrupt.
        PFETLDataAccessError
            For other internal connection errors.
        """
        if self.is_locked():
            raise PFETLLockedError(
                f"DuckDB warehouse access blocked: active pipeline lock at '{self.lock_path}'.",
                path=self.lock_path,
                profile_id=self.profile_id,
            )
        if not self.db_path.exists() or not self.db_path.is_file():
            raise DatabaseNotFoundError(
                f"DuckDB warehouse file not found: '{self.db_path}'.",
                path=self.db_path,
                profile_id=self.profile_id,
            )

        try:
            return duckdb.connect(database=str(self.db_path), read_only=True)
        except duckdb.Error as exc:
            msg = str(exc)
            if "corrupt" in msg.lower() or "not a valid" in msg.lower():
                raise DatabaseCorruptedError(
                    f"DuckDB database appears corrupted: {exc}",
                    path=self.db_path,
                    profile_id=self.profile_id,
                ) from exc
            raise PFETLDataAccessError(
                f"Failed to connect to DuckDB warehouse: {exc}",
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
            Query results converted to Polars DataFrame.
        """
        with contextlib.closing(self._open_connection()) as conn:
            try:
                rel = conn.execute(sql, params)
                cols = [desc[0] for desc in rel.description or []]
                rows = rel.fetchall()
                if not rows:
                    return pl.DataFrame(schema=cols)
                return pl.DataFrame(rows, schema=cols, orient="row")
            except duckdb.Error as exc:
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
                rel = conn.execute(sql, params)
                columns = [desc[0] for desc in rel.description or []]
                rows = rel.fetchall()
                return [dict(zip(columns, row, strict=True)) for row in rows]
            except duckdb.Error as exc:
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
                rel = conn.execute(sql, params)
                columns = [desc[0] for desc in rel.description or []]
                row = rel.fetchone()
                if row is None:
                    return None
                return dict(zip(columns, row, strict=True))
            except duckdb.Error as exc:
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
                rel = conn.execute(sql, params)
                row = rel.fetchone()
                return row[0] if row is not None else None
            except duckdb.Error as exc:
                self._handle_query_error(exc)

    def table_exists(self, schema: str, table: str) -> bool:
        """Verify whether a table exists in the specified warehouse schema.

        Parameters
        ----------
        schema : str
            Schema name (e.g. 'bronze', 'silver', 'gold', 'meta').
        table : str
            Physical table name.

        Returns
        -------
        bool
            True if table exists, False otherwise.
        """
        valid_schema = validate_identifier(schema)
        valid_table = validate_identifier(table)
        query = """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            LIMIT 1;
        """
        return self.fetch_scalar(query, (valid_schema, valid_table)) is not None

    def bounded_preview(
        self,
        schema: str,
        table: str,
        limit: int = 100,
    ) -> pl.DataFrame:
        """Fetch bounded preview of rows for evidence and catalog views.

        Parameters
        ----------
        schema : str
            Schema name.
        table : str
            Table name.
        limit : int, optional
            Requested row limit (clamped between 1 and 500), by default 100.

        Returns
        -------
        pl.DataFrame
            Bounded preview DataFrame.
        """
        valid_schema = validate_identifier(schema)
        valid_table = validate_identifier(table)
        safe_limit = max(1, min(limit, 500))
        sql = f'SELECT * FROM "{valid_schema}"."{valid_table}" LIMIT ?;'
        return self.fetch_df(sql, (safe_limit,))

    def _handle_query_error(self, exc: duckdb.Error) -> NoReturn:
        """Translate DuckDB query errors into typed Studio domain exceptions."""
        msg = str(exc)
        lower_msg = msg.lower()
        if (
            "read-only" in lower_msg
            or "permission" in lower_msg
            or "cannot execute statement of type" in lower_msg
        ):
            raise ReadOnlyViolationError(
                f"Security violation: Write statement rejected on read-only warehouse: {exc}",
                path=self.db_path,
                profile_id=self.profile_id,
            ) from exc
        raise PFETLDataAccessError(
            f"DuckDB query execution error: {exc}",
            path=self.db_path,
            profile_id=self.profile_id,
        ) from exc
