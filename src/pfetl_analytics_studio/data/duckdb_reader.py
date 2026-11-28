"""Read-only gateway for PFETL DuckDB warehouse databases.

Strictly enforces read-only connections and verifies that the PFETL pipeline lock
is not active before opening any connection. Does not allow connections to escape
to analytical services or UI pages.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any, NoReturn, Protocol

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
from pfetl_analytics_studio.data.exports import write_rows_csv_file
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


class DuckDBQueryPort(Protocol):
    """Read-only DuckDB query protocol satisfied by `DuckDBReader` and `DuckDBSession`."""

    def get_state(self) -> WarehouseState:
        """Return warehouse availability state."""
        ...

    def fetch_df(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> pl.DataFrame:
        """Execute read-only SQL returning a Polars DataFrame."""
        ...

    def fetch_all(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> list[dict[str, Any]]:
        """Execute read-only SQL returning column-keyed dicts."""
        ...

    def fetch_one(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> dict[str, Any] | None:
        """Execute read-only SQL returning the first row dict or None."""
        ...

    def fetch_scalar(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> Any:
        """Execute read-only SQL returning a single scalar value."""
        ...

    def table_exists(self, schema: str, table: str) -> bool:
        """Check whether `schema.table` exists in DuckDB."""
        ...


class DuckDBSession:
    """Short-lived read-only DuckDB session reusing one connection for a single snapshot."""

    def __init__(self, reader: DuckDBReader, conn: duckdb.DuckDBPyConnection) -> None:
        self._reader = reader
        self._conn = conn

    def get_state(self) -> WarehouseState:
        """Delegate warehouse availability state to the parent reader."""
        return self._reader.get_state()

    def fetch_df(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> pl.DataFrame:
        """Execute a read-only query on the open session connection as a Polars DataFrame."""
        try:
            rel = self._conn.execute(sql, params)
            cols = [desc[0] for desc in rel.description or []]
            rows = rel.fetchall()
            if not rows:
                return pl.DataFrame(schema=cols)
            return pl.DataFrame(rows, schema=cols, orient="row")
        except duckdb.Error as exc:
            self._reader.translate_query_error(exc)
            raise

    def fetch_all(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> list[dict[str, Any]]:
        """Execute a read-only query on the open session connection as dicts."""
        try:
            rel = self._conn.execute(sql, params)
            columns = [desc[0] for desc in rel.description or []]
            rows = rel.fetchall()
            return [dict(zip(columns, row, strict=True)) for row in rows]
        except duckdb.Error as exc:
            self._reader.translate_query_error(exc)
            raise

    def iter_dict_chunks(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
        *,
        chunk_size: int = 2000,
    ) -> Generator[list[dict[str, Any]]]:
        """Execute a read-only query on the open session and yield dict chunks via fetchmany()."""
        try:
            rel = self._conn.execute(sql, params)
            columns = [desc[0] for desc in rel.description or []]
            while True:
                chunk = rel.fetchmany(chunk_size)
                if not chunk:
                    break
                yield [dict(zip(columns, row, strict=True)) for row in chunk]
        except duckdb.Error as exc:
            self._reader.translate_query_error(exc)
            raise

    def fetch_one(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> dict[str, Any] | None:
        """Execute a read-only query on the open session connection returning one row."""
        try:
            rel = self._conn.execute(sql, params)
            columns = [desc[0] for desc in rel.description or []]
            row = rel.fetchone()
            if row is None:
                return None
            return dict(zip(columns, row, strict=True))
        except duckdb.Error as exc:
            self._reader.translate_query_error(exc)
            raise

    def fetch_scalar(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> Any:
        """Execute a read-only query on the open session connection returning a scalar."""
        try:
            rel = self._conn.execute(sql, params)
            row = rel.fetchone()
            return row[0] if row is not None else None
        except duckdb.Error as exc:
            self._reader.translate_query_error(exc)
            raise

    def export_csv_file(
        self,
        sql: str,
        target_path: Path,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> int:
        """Export a read-only query to `target_path` via DuckDB COPY or chunked streaming."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        clean_sql = sql.strip().rstrip(";").strip()
        try:
            if not params:
                escaped = target_path.resolve().as_posix().replace("'", "''")
                rel = self._conn.execute(
                    f"COPY ({clean_sql}) TO '{escaped}' (HEADER, DELIMITER ',')"
                )
                row = rel.fetchone()
                return int(row[0]) if row is not None and row[0] is not None else 0

            rel = self._conn.execute(sql, params)
            cols = [desc[0] for desc in rel.description or []]

            def _iter_chunks() -> Generator[list[tuple[Any, ...]]]:
                while True:
                    chunk = rel.fetchmany(4096)
                    if not chunk:
                        break
                    yield chunk

            return write_rows_csv_file(cols, _iter_chunks(), target_path)
        except duckdb.Error as exc:
            self._reader.translate_query_error(exc)
            raise

    def table_exists(self, schema: str, table: str) -> bool:
        """Verify whether `schema.table` exists using this session connection."""
        valid_schema = validate_identifier(schema)
        valid_table = validate_identifier(table)
        query = """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            LIMIT 1;
        """
        return self.fetch_scalar(query, (valid_schema, valid_table)) is not None


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

    @contextlib.contextmanager
    def session(self) -> Generator[DuckDBSession]:
        """Open a short-lived read-only DuckDB session for one analytical snapshot."""
        with contextlib.closing(self._open_connection()) as conn:
            yield DuckDBSession(self, conn)

    def translate_query_error(self, exc: duckdb.Error) -> NoReturn:
        """Translate DuckDB query errors into typed Studio domain exceptions."""
        self._handle_query_error(exc)

    def fetch_df(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> pl.DataFrame:
        """Execute a read-only query and return results as a Polars DataFrame."""
        with self.session() as db:
            return db.fetch_df(sql, params)

    def fetch_all(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> list[dict[str, Any]]:
        """Execute query and return all rows as a list of column-keyed dictionaries."""
        with self.session() as db:
            return db.fetch_all(sql, params)

    def fetch_one(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> dict[str, Any] | None:
        """Execute query and return the first row as a dictionary, or None."""
        with self.session() as db:
            return db.fetch_one(sql, params)

    def fetch_scalar(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> Any:
        """Execute query and return a single scalar value from the first row and column."""
        with self.session() as db:
            return db.fetch_scalar(sql, params)

    def export_csv_file(
        self,
        sql: str,
        target_path: Path,
        params: Sequence[Any] | dict[str, Any] = (),
    ) -> int:
        """Export a read-only query to `target_path` without materializing full bytes."""
        with self.session() as db:
            return db.export_csv_file(sql, target_path, params)

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
