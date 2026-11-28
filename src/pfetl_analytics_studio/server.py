"""Server lifecycle, loopback port resolution, health endpoint, and runtime metadata contract.

Owns
----
- `StudioServerConfig`: typed launch configuration decouple CLI parsing from NiceGUI.
- `validate_loopback_host` & `resolve_loopback_port`: loopback-only binding & port probing.
- `register_health_endpoint`: minimal `GET /healthz` readiness probe with zero secrets.
- `write_runtime_metadata` / `read_runtime_metadata` / `is_runtime_metadata_live`: atomic
  child-process orchestration metadata without financial or profile secrets.
- `validate_startup_preflight` & `run_server`: deterministic launch entrypoint.
"""

from __future__ import annotations

import atexit
import ipaddress
import json
import os
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from nicegui import app as nicegui_app
from nicegui import ui
from platformdirs import user_data_dir

from pfetl_analytics_studio import __version__
from pfetl_analytics_studio.app import create_app
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.resources import resolve_resource_path
from pfetl_analytics_studio.state import StudioStateStore

ALLOWED_LOOPBACKS = {"127.0.0.1", "localhost", "::1"}
_PREFERRED_PORT = 8080
_HEALTH_STATE: dict[str, Any] = {"registered": False, "host": "127.0.0.1", "port": 8080}


@dataclass(frozen=True)
class StudioServerConfig:
    """Typed launch configuration for PFETL Analytics Studio."""

    host: str = "127.0.0.1"
    port: int | None = _PREFERRED_PORT
    settings_path: Path | None = None
    rules_path: Path | None = None
    open_browser: bool = True
    reload: bool = False
    runtime_file: Path | None = None


def validate_loopback_host(host: str) -> None:
    """Validate that `host` resolves to a local loopback interface unless dev override is set."""
    if os.environ.get("PFETL_STUDIO_DEV_ALLOW_REMOTE", "").lower() in ("1", "true", "yes"):
        return

    clean_host = host.strip().lower()
    if clean_host in ALLOWED_LOOPBACKS:
        return

    try:
        ip = ipaddress.ip_address(clean_host)
        if ip.is_loopback:
            return
    except ValueError:
        pass

    raise ValueError(
        f"Security violation: PFETL Analytics Studio must bind to a loopback address "
        f"(got '{host}'). Set PFETL_STUDIO_DEV_ALLOW_REMOTE=1 only for explicit dev testing."
    )


def _probe_port_available(host: str, port: int) -> bool:
    bind_host = "127.0.0.1" if host.strip().lower() == "localhost" else host.strip()
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((bind_host, port))
            return True
        except OSError:
            return False


def resolve_loopback_port(host: str, requested_port: int | None) -> int:
    """Resolve an available loopback port; fail clearly if an explicit port is occupied."""
    if requested_port is not None and requested_port > 0:
        if not _probe_port_available(host, requested_port):
            raise RuntimeError(
                f"Requested port {requested_port} on '{host}' is already in use or unavailable."
            )
        return requested_port

    if _probe_port_available(host, _PREFERRED_PORT):
        return _PREFERRED_PORT

    bind_host = "127.0.0.1" if host.strip().lower() == "localhost" else host.strip()
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.bind((bind_host, 0))
        return int(sock.getsockname()[1])


def build_health_payload(host: str, port: int) -> dict[str, Any]:
    """Build the minimal, secret-free `/healthz` readiness payload."""
    return {
        "status": "ready",
        "app": "pfetl-analytics-studio",
        "version": __version__,
        "host": host,
        "port": port,
    }


def register_health_endpoint(host: str, port: int) -> None:
    """Register `GET /healthz` idempotently on the NiceGUI FastAPI application."""
    _HEALTH_STATE["host"] = host
    _HEALTH_STATE["port"] = port
    if _HEALTH_STATE["registered"]:
        return

    @nicegui_app.get("/healthz", include_in_schema=False)
    def _healthz() -> dict[str, Any]:
        return build_health_payload(str(_HEALTH_STATE["host"]), int(_HEALTH_STATE["port"]))

    _HEALTH_STATE["registered"] = True


def write_runtime_metadata(
    path: Path,
    *,
    pid: int,
    host: str,
    port: int,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Atomically write Studio runtime metadata without any financial or profile secrets."""
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "pid": pid,
        "host": host,
        "port": port,
        "version": __version__,
        "started_at": started_at or datetime.now(UTC).isoformat(),
        "health_url": f"http://{host}:{port}/healthz",
    }
    fd, tmp_name = tempfile.mkstemp(
        prefix=".studio_runtime_",
        suffix=".tmp",
        dir=str(resolved.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp_path, resolved)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return payload


def read_runtime_metadata(path: Path) -> dict[str, Any] | None:
    """Read Studio runtime metadata JSON if present and valid."""
    if not path.is_file():
        return None
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return dict(cast(dict[str, Any], raw)) if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def studio_log_dir() -> Path:
    """Return the per-user Studio logs directory (`<user_data_dir>/logs`)."""
    log_dir = Path(user_data_dir("pfetl-analytics-studio")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def append_startup_log(message: str, *, log_file: Path | None = None) -> Path:
    """Append one timestamped operational line to `studio_startup.log`."""
    target = log_file or (studio_log_dir() / "studio_startup.log")
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat()
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")
    return target


def probe_healthz(host: str, port: int, *, timeout: float = 0.75) -> dict[str, Any] | None:
    """Query `GET http://<host>:<port>/healthz` and return parsed JSON if valid."""
    formatted_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    url = f"http://{formatted_host}:{port}/healthz"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if getattr(resp, "status", 0) != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return cast("dict[str, Any]", payload)


def is_runtime_metadata_live(metadata: dict[str, Any]) -> bool:
    """Return True if runtime PID is alive and `/healthz` reports Studio ready."""
    raw_pid = metadata.get("pid")
    raw_port = metadata.get("port")
    host = str(metadata.get("host") or "127.0.0.1")
    if not isinstance(raw_pid, int) or not isinstance(raw_port, int):
        return False
    if not _is_pid_running(raw_pid):
        return False
    payload = probe_healthz(host, raw_port, timeout=0.75)
    if payload is None:
        return False
    return payload.get("app") == "pfetl-analytics-studio" and payload.get("status") == "ready"


def remove_runtime_metadata(path: Path) -> None:
    """Remove runtime metadata file safely if it exists."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def validate_startup_preflight(config: StudioServerConfig) -> tuple[PFETLProfile | None, int]:
    """Validate host, port, writable Studio state dir, and optional paired profile."""
    validate_loopback_host(config.host)
    resolved_port = resolve_loopback_port(config.host, config.port)

    with StudioStateStore() as store:
        _ = store.get_recent_profiles(limit=1)

    profile: PFETLProfile | None = None
    if config.settings_path is not None or config.rules_path is not None:
        if config.settings_path is None or config.rules_path is None:
            raise ValueError(
                "Both --settings and --rules must be supplied together to load a profile."
            )
        result = PFETLProfile.load(config.settings_path, config.rules_path)
        if not result.is_ok or result.profile is None:
            err_msg = result.error.message if result.error else "Unknown profile load error"
            raise ValueError(f"Error loading profile: {err_msg}")
        profile = result.profile
    return profile, resolved_port


def run_server(config: StudioServerConfig) -> int:
    """Validate startup preflight, register lifecycle endpoints, and launch NiceGUI."""
    try:
        profile, resolved_port = validate_startup_preflight(config)
    except Exception as exc:  # noqa: BLE001
        append_startup_log(f"server preflight failed: {exc}")
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    append_startup_log(
        f"server starting host={config.host} port={resolved_port} pid={os.getpid()} "
        f"profile={profile.profile_id if profile else 'none'}"
    )
    if profile is not None:
        try:
            with StudioStateStore() as store:
                store.record_profile_opened(profile)
        except OSError as exc:
            print(f"Warning: Failed to record recent profile: {exc}", file=sys.stderr)

    register_health_endpoint(config.host, resolved_port)
    if config.runtime_file is not None:
        rt_path = config.runtime_file
        write_runtime_metadata(rt_path, pid=os.getpid(), host=config.host, port=resolved_port)
        atexit.register(remove_runtime_metadata, rt_path)

    icon_path = resolve_resource_path("logo.ico") or resolve_resource_path("logo.png")
    create_app(profile=profile)
    ui.run(  # pyright: ignore[reportUnknownMemberType]
        host=config.host,
        port=resolved_port,
        title="PFETL Analytics Studio",
        favicon=str(icon_path) if icon_path is not None else "📊",
        show=config.open_browser,
        reload=config.reload,
    )
    return 0
