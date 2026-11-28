"""Multi-request-safe streaming file download helper for NiceGUI.

Owns
----
- `trigger_file_download`: serves local export files through a single registered
  FastAPI route with TTL-backed tokens and `Content-Disposition: attachment`
  so Chrome / download helpers never hit NiceGUI `single_use=True` 404 races.
- `clear_download_tokens`: test helper to reset token registry cleanly.
"""

from __future__ import annotations

import secrets
import threading
import time
import urllib.parse
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from nicegui import app, ui

_DOWNLOAD_ROUTE_PREFIX = "/_studio/download"
_TOKEN_TTL_SECONDS = 300.0
_LOCK = threading.Lock()
_TOKENS: dict[str, tuple[Path, str, str, float]] = {}
_ROUTE_STATE: dict[str, bool] = {"registered": False}


def _prune_expired_locked(now: float) -> None:
    expired = [tok for tok, (_, _, _, exp) in _TOKENS.items() if exp <= now]
    for tok in expired:
        _TOKENS.pop(tok, None)


def ensure_download_route_registered() -> None:
    """Register the shared `/_studio/download/{token}/{filename}` route idempotently."""
    with _LOCK:
        if _ROUTE_STATE["registered"]:
            return

        @app.get(f"{_DOWNLOAD_ROUTE_PREFIX}/{{token}}/{{filename}}", include_in_schema=False)
        def _serve_studio_download(token: str, filename: str) -> FileResponse:  # noqa: ARG001
            now = time.monotonic()
            with _LOCK:
                _prune_expired_locked(now)
                entry = _TOKENS.get(token)
            if entry is None:
                raise HTTPException(status_code=404, detail="Download link expired or not found")
            file_path, dl_name, media_type, _ = entry
            if not file_path.is_file():
                raise HTTPException(status_code=404, detail="Export file no longer exists")
            return FileResponse(
                path=file_path,
                media_type=media_type or "application/octet-stream",
                filename=dl_name,
                headers={"Cache-Control": "no-store"},
            )

        _ROUTE_STATE["registered"] = True


def trigger_file_download(
    path: str | Path,
    *,
    filename: str | None = None,
    media_type: str = "",
) -> str:
    """Trigger a reliable file-backed browser download without `single_use=True` 404 races."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Export file not found: {resolved}")

    ensure_download_route_registered()
    dl_name = filename or resolved.name
    token = secrets.token_urlsafe(24)
    expires_at = time.monotonic() + _TOKEN_TTL_SECONDS
    with _LOCK:
        _prune_expired_locked(time.monotonic())
        _TOKENS[token] = (resolved, dl_name, media_type, expires_at)

    url = f"{_DOWNLOAD_ROUTE_PREFIX}/{token}/{urllib.parse.quote(dl_name)}"
    ui.download.from_url(url, filename=dl_name, media_type=media_type)
    return url


def clear_download_tokens() -> None:
    """Clear all active download tokens (intended for unit tests)."""
    with _LOCK:
        _TOKENS.clear()
