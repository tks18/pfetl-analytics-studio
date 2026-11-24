"""Archived execution trace and JSONL log decoder for PFETL runs.

Owns
----
- Reading and decompressing `cp_runs.execution_log` zlib payloads from Control Plane SQLite.
- Resolving Core filesystem fallback JSONL traces (`trace_<run_id>.jsonl`) when archived BLOBs are absent.
- Parsing serialized `ObservabilityEvent` lines cleanly as data contracts without importing Core classes.

Does not own
------------
- SQLite connection creation (delegates to `ControlPlaneReader`).
- Log mutation, pruning, or forensic retention.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from pfetl_analytics_studio.data.control_plane_reader import ControlPlaneReader
from pfetl_analytics_studio.data.errors import ControlPlaneState, PFETLDataAccessError
from pfetl_analytics_studio.profile import PFETLProfile

MAX_DIAGNOSTIC_ERRORS = 25


def _empty_str_list() -> list[str]:
    """Create a typed empty list of strings."""
    return []


class LogSource(StrEnum):
    """Origin from which run execution log events were loaded."""

    SQLITE_BLOB = "sqlite_blob"
    FILESYSTEM_FALLBACK = "filesystem_fallback"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class LogReadResult:
    """Decoded JSONL execution trace alongside line-level integrity diagnostics."""

    run_id: str
    events: list[dict[str, Any]]
    total_lines: int
    malformed_lines: int
    source: LogSource
    diagnostics: list[str] = field(default_factory=_empty_str_list)


class ArchivedLogReader:
    """Read-only decoder for compressed Control Plane execution traces and fallback JSONL."""

    def __init__(self, profile: PFETLProfile) -> None:
        """Initialize log reader bound to an active PFETL profile.

        Parameters
        ----------
        profile : PFETLProfile
            Resolved PFETL profile containing Control Plane and logs directory paths.
        """
        self.profile = profile
        self.cp_reader = ControlPlaneReader(profile)

    def resolve_fallback_path(self, run_id: str) -> Path | None:
        """Locate an on-disk fallback JSONL trace file for `run_id`, if one exists.

        Parameters
        ----------
        run_id : str
            Unique PFETL run identifier.

        Returns
        -------
        Path | None
            Existing trace file path on disk, or None if absent.
        """
        logs_root = self.profile.logs_path
        if logs_root is None:
            return None

        candidates = (
            logs_root / f"trace_{run_id}.jsonl",
            logs_root / "runs" / f"trace_{run_id}.jsonl",
            logs_root / f"{run_id}.jsonl",
        )
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def has_log(self, run_id: str) -> bool:
        """Check cheaply whether archived or fallback execution logs exist for a run.

        Parameters
        ----------
        run_id : str
            Unique PFETL run identifier.

        Returns
        -------
        bool
            True if either `cp_runs.execution_log` is non-null or a fallback trace file exists.
        """
        if (
            self.cp_reader.get_state() == ControlPlaneState.AVAILABLE
            and self.cp_reader.table_exists("cp_runs")
        ):
            try:
                has_blob = self.cp_reader.fetch_scalar(
                    "SELECT (execution_log IS NOT NULL AND length(execution_log) > 0) "
                    "FROM cp_runs WHERE run_id = ?;",
                    (run_id,),
                )
                if has_blob:
                    return True
            except PFETLDataAccessError:
                pass
        return self.resolve_fallback_path(run_id) is not None

    def read_run(self, run_id: str) -> LogReadResult:
        """Decompress and parse JSONL observability events for a specific run ID.

        Prefers `cp_runs.execution_log` in Control Plane SQLite and falls back to
        filesystem JSONL traces under `profile.logs_path` only when the archived payload
        is missing or empty.

        Parameters
        ----------
        run_id : str
            Unique PFETL run identifier.

        Returns
        -------
        LogReadResult
            Parsed event dicts in original trace sequence order plus malformed-line count.
        """
        diagnostics: list[str] = []
        raw_blob = self._fetch_sqlite_log(run_id, diagnostics)

        if raw_blob is not None:
            text = self._decode_archived_payload(raw_blob, diagnostics)
            if text is None:
                return LogReadResult(
                    run_id=run_id,
                    events=[],
                    total_lines=0,
                    malformed_lines=0,
                    source=LogSource.SQLITE_BLOB,
                    diagnostics=diagnostics,
                )
            return self._parse_jsonl_text(
                run_id=run_id,
                text=text,
                source=LogSource.SQLITE_BLOB,
                diagnostics=diagnostics,
            )

        fallback_path = self.resolve_fallback_path(run_id)
        if fallback_path is not None:
            try:
                text = fallback_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                diagnostics.append(f"Failed reading fallback log file '{fallback_path}': {exc}")
                return LogReadResult(
                    run_id=run_id,
                    events=[],
                    total_lines=0,
                    malformed_lines=0,
                    source=LogSource.UNAVAILABLE,
                    diagnostics=diagnostics,
                )
            diagnostics.append(
                f"Archived blob absent; loaded fallback trace '{fallback_path.name}'."
            )
            return self._parse_jsonl_text(
                run_id=run_id,
                text=text,
                source=LogSource.FILESYSTEM_FALLBACK,
                diagnostics=diagnostics,
            )

        diagnostics.append(f"No archived blob or filesystem fallback log found for run '{run_id}'.")
        return LogReadResult(
            run_id=run_id,
            events=[],
            total_lines=0,
            malformed_lines=0,
            source=LogSource.UNAVAILABLE,
            diagnostics=diagnostics,
        )

    def _fetch_sqlite_log(
        self,
        run_id: str,
        diagnostics: list[str],
    ) -> bytes | str | None:
        """Query raw `execution_log` payload from `cp_runs` if non-empty."""
        if self.cp_reader.get_state() != ControlPlaneState.AVAILABLE:
            diagnostics.append("Control Plane database unavailable for archived log lookup.")
            return None
        if not self.cp_reader.table_exists("cp_runs"):
            diagnostics.append("Table 'cp_runs' missing from Control Plane database.")
            return None

        try:
            row = self.cp_reader.fetch_one(
                "SELECT execution_log FROM cp_runs WHERE run_id = ?;",
                (run_id,),
            )
        except PFETLDataAccessError as exc:
            diagnostics.append(f"Failed fetching archived execution_log for '{run_id}': {exc}")
            return None

        if row is None:
            return None
        payload = row.get("execution_log")
        if payload is None or payload in (b"", ""):
            return None
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
        if isinstance(payload, memoryview):
            return payload.tobytes()
        if isinstance(payload, str):
            return payload
        diagnostics.append(
            f"Unsupported execution_log payload type '{type(payload).__name__}' for '{run_id}'."
        )
        return None

    def _decode_archived_payload(
        self,
        payload: bytes | str,
        diagnostics: list[str],
    ) -> str | None:
        """Decompress zlib bytes or decode plain string trace payloads safely."""
        if isinstance(payload, str):
            return payload

        try:
            decompressed = zlib.decompress(payload)
            return decompressed.decode("utf-8", errors="replace")
        except zlib.error as exc:
            # Tolerate uncompressed UTF-8 JSONL stored directly without zlib framing
            try:
                candidate_text = payload.decode("utf-8")
            except UnicodeDecodeError:
                diagnostics.append(f"Failed to decompress zlib execution_log payload: {exc}")
                return None
            if candidate_text.lstrip().startswith("{"):
                diagnostics.append(
                    "execution_log payload lacked zlib framing; decoded as plain UTF-8 JSONL."
                )
                return candidate_text
            diagnostics.append(f"Failed to decompress zlib execution_log payload: {exc}")
            return None

    def _parse_jsonl_text(
        self,
        run_id: str,
        text: str,
        source: LogSource,
        diagnostics: list[str],
    ) -> LogReadResult:
        """Parse newline-delimited JSON event objects preserving order and error diagnostics."""
        events: list[dict[str, Any]] = []
        total_lines = 0
        malformed_lines = 0

        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            total_lines += 1
            try:
                parsed: Any = json.loads(stripped)
            except json.JSONDecodeError as exc:
                malformed_lines += 1
                if malformed_lines <= MAX_DIAGNOSTIC_ERRORS:
                    diagnostics.append(f"Malformed JSONL at line {line_no}: {exc}")
                continue

            if not isinstance(parsed, dict):
                malformed_lines += 1
                if malformed_lines <= MAX_DIAGNOSTIC_ERRORS:
                    diagnostics.append(
                        f"Expected JSON object at line {line_no}, got {type(parsed).__name__}."
                    )
                continue

            events.append(cast("dict[str, Any]", parsed))

        return LogReadResult(
            run_id=run_id,
            events=events,
            total_lines=total_lines,
            malformed_lines=malformed_lines,
            source=source,
            diagnostics=diagnostics,
        )
