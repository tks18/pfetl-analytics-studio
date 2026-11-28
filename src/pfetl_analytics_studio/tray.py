"""Windows-friendly system tray host for PFETL Analytics Studio."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pystray  # type: ignore[import-untyped]
from PIL import Image, ImageDraw
from platformdirs import user_data_dir

from pfetl_analytics_studio import __version__
from pfetl_analytics_studio.resources import resolve_resource_path
from pfetl_analytics_studio.server import (
    StudioServerConfig,
    append_startup_log,
    is_runtime_metadata_live,
    read_runtime_metadata,
    remove_runtime_metadata,
    studio_log_dir,
)

_MUTEX_NAME = "Local\\PFETLAnalyticsStudioTray"
_ERROR_ALREADY_EXISTS = 183


def studio_data_dir() -> Path:
    """Return the per-user Studio data directory."""
    path = Path(user_data_dir("pfetl-analytics-studio"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_runtime_file() -> Path:
    """Return the default runtime metadata path used by the tray host."""
    return studio_data_dir() / "runtime.json"


def load_tray_icon_image() -> Image.Image:
    """Load the Studio logo icon for `pystray`, falling back to a generated badge."""
    for candidate_name in ("logo.ico", "logo.png"):
        path = resolve_resource_path(candidate_name)
        if path is not None:
            try:
                with Image.open(path) as img:
                    rgba = cast("Any", img.convert("RGBA"))
                    return cast("Image.Image", rgba.resize((64, 64), Image.Resampling.LANCZOS))
            except OSError:
                continue

    fallback = Image.new("RGBA", (64, 64), (15, 23, 42, 255))
    draw = ImageDraw.Draw(fallback)
    draw.rounded_rectangle((6, 6, 58, 58), radius=12, fill=(37, 99, 235, 255))
    draw.rectangle((18, 34, 26, 48), fill=(255, 255, 255, 255))
    draw.rectangle((29, 24, 37, 48), fill=(255, 255, 255, 255))
    draw.rectangle((40, 16, 48, 48), fill=(255, 255, 255, 255))
    return fallback


def _open_path_in_explorer(path: Path) -> None:
    """Open a local folder in the OS file explorer."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32" and hasattr(os, "startfile"):
        os.startfile(str(path))  # noqa: S606
    else:
        webbrowser.open(path.resolve().as_uri())


def _acquire_single_instance_mutex() -> int | None:
    """Acquire a Windows named mutex; return handle or None if another tray owns it."""
    if sys.platform != "win32":
        return 1
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _release_single_instance_mutex(handle: int | None) -> None:
    """Release a previously acquired Windows named mutex handle."""
    if sys.platform != "win32" or handle is None:
        return
    try:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
    except OSError:
        pass


@dataclass
class StudioTrayController:
    """Manages the Studio backend server lifecycle for the tray application."""

    config: StudioServerConfig = field(default_factory=StudioServerConfig)
    runtime_file: Path = field(default_factory=default_runtime_file)
    _process: subprocess.Popen[bytes] | None = field(default=None, init=False, repr=False)
    _adopted_pid: int | None = field(default=None, init=False, repr=False)
    _live_host: str = field(default="127.0.0.1", init=False)
    _live_port: int | None = field(default=None, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _icon: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Initialize live host from launch config."""
        self._live_host = self.config.host

    @property
    def studio_url(self) -> str | None:
        """Return the active loopback URL if the server port is known."""
        if self._live_port is None:
            return None
        formatted_host = (
            f"[{self._live_host}]"
            if ":" in self._live_host and not self._live_host.startswith("[")
            else self._live_host
        )
        return f"http://{formatted_host}:{self._live_port}"

    def is_running(self) -> bool:
        """Return True if either the managed child or adopted server is live."""
        with self._lock:
            if self._process is not None and self._process.poll() is not None:
                self._process = None
                self._live_port = None
            meta = read_runtime_metadata(self.runtime_file)
            if meta is not None and is_runtime_metadata_live(meta):
                self._live_host = str(meta.get("host") or self.config.host)
                self._live_port = int(meta["port"])
                self._adopted_pid = int(meta["pid"])
                return True
            if self._process is not None and self._process.poll() is None:
                return self._live_port is not None
            return False

    def adopt_existing_if_live(self) -> bool:
        """Adopt an already-running Studio instance recorded in `runtime_file`."""
        with self._lock:
            meta = read_runtime_metadata(self.runtime_file)
            if meta is None or not is_runtime_metadata_live(meta):
                return False
            self._live_host = str(meta.get("host") or self.config.host)
            self._live_port = int(meta["port"])
            self._adopted_pid = int(meta["pid"])
            append_startup_log(
                f"tray adopted live server pid={self._adopted_pid} url={self.studio_url}"
            )
            return True

    def resolve_launch_profile(self) -> tuple[Path, Path] | None:
        """Resolve explicit CLI profile only — no automatic fallback to recent profiles.

        Returns the (settings, rules) pair only when both paths were explicitly
        supplied via CLI flags and exist on disk.  When launched standalone
        (without ``--settings`` / ``--rules``), the Studio starts profile-free
        so the UI can prompt the user or wait for pfetl-core to inject them.
        """
        if self.config.settings_path is not None and self.config.rules_path is not None:
            if self.config.settings_path.exists() and self.config.rules_path.exists():
                return self.config.settings_path, self.config.rules_path
        return None

    def _build_server_command(self) -> list[str]:
        """Build child command using sibling server exe when frozen, else `python -m`."""
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            sibling_server = exe_dir / "pfetl-studio-server.exe"
            if sibling_server.exists():
                cmd = [str(sibling_server)]
            else:
                cmd = [sys.executable, "--cli"]
        else:
            cmd = [sys.executable, "-m", "pfetl_analytics_studio.cli", "--cli"]

        cmd.extend(["--host", self.config.host])
        if self.config.port is None or self.config.port == 0:
            cmd.extend(["--port", "0", "--auto-port"])
        else:
            cmd.extend(["--port", str(self.config.port), "--auto-port"])
        cmd.extend(["--no-browser", "--runtime-file", str(self.runtime_file)])

        profile_paths = self.resolve_launch_profile()
        if profile_paths is not None:
            cmd.extend(["--settings", str(profile_paths[0]), "--rules", str(profile_paths[1])])
        return cmd

    def start_server(
        self,
        *,
        open_browser: bool = True,
        timeout_seconds: float = 45.0,
    ) -> bool:
        """Start (or adopt) the Studio server, wait for `/healthz`, and optionally open browser."""
        with self._lock:
            if self.adopt_existing_if_live():
                self._refresh_menu()
                if open_browser and self.studio_url:
                    webbrowser.open(self.studio_url)
                return True

            if self.runtime_file.exists():
                remove_runtime_metadata(self.runtime_file)

            cmd = self._build_server_command()
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            )
            append_startup_log(f"tray launching server cmd={' '.join(cmd)}")
            self._process = subprocess.Popen(  # noqa: SIM117  # pylint: disable=consider-using-with
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            proc = self._process
            self._adopted_pid = proc.pid

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                if proc.poll() is not None:
                    append_startup_log(f"tray child server exited early code={proc.returncode}")
                    self._process = None
                    self._live_port = None
                    self._refresh_menu()
                    return False
                meta = read_runtime_metadata(self.runtime_file)
                if meta is not None:
                    raw_port = meta.get("port")
                    raw_pid = meta.get("pid")
                    if isinstance(raw_port, int) and isinstance(raw_pid, int):
                        from pfetl_analytics_studio.server import probe_healthz

                        payload = probe_healthz(
                            str(meta.get("host") or self.config.host),
                            raw_port,
                            timeout=2.0,
                        )
                        if (
                            payload is not None
                            and payload.get("app") == "pfetl-analytics-studio"
                            and payload.get("status") == "ready"
                        ):
                            self._live_host = str(meta.get("host") or self.config.host)
                            self._live_port = raw_port
                            self._adopted_pid = raw_pid
                            append_startup_log(
                                f"tray server ready pid={self._adopted_pid} url={self.studio_url}"
                            )
                            self._refresh_menu()
                            if open_browser and self.studio_url:
                                webbrowser.open(self.studio_url)
                            return True
            time.sleep(0.5)

        append_startup_log("tray server startup timed out waiting for /healthz")
        self._refresh_menu()
        return False

    def stop_server(self) -> None:
        """Stop the managed or adopted Studio server cleanly."""
        with self._lock:
            proc = self._process
            pid = self._adopted_pid
            self._process = None
            self._adopted_pid = None
            self._live_port = None

        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        elif pid is not None and pid > 0:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

        remove_runtime_metadata(self.runtime_file)
        append_startup_log("tray stopped server")
        self._refresh_menu()

    def restart_server(self) -> bool:
        """Restart the Studio server without stealing browser focus."""
        self.stop_server()
        return self.start_server(open_browser=False)

    def open_studio(self) -> bool:
        """Ensure the server is running and open the Studio URL in the default browser."""
        if self.is_running() and self.studio_url:
            webbrowser.open(self.studio_url)
            return True
        return self.start_server(open_browser=True)

    def open_data_folder(self) -> None:
        """Open the Studio per-user data folder."""
        _open_path_in_explorer(studio_data_dir())

    def open_logs_folder(self) -> None:
        """Open the Studio logs folder."""
        _open_path_in_explorer(studio_log_dir())

    def diagnostics_summary(self) -> str:
        """Build a concise, secret-free runtime diagnostics summary string."""
        running = self.is_running()
        state = f"Running ({self.studio_url}, PID {self._adopted_pid})" if running else "Stopped"
        lines = [
            f"PFETL Analytics Studio v{__version__}",
            f"Server: {state}",
            f"Data Dir: {studio_data_dir()}",
            f"Logs Dir: {studio_log_dir()}",
        ]
        summary = " | ".join(lines)
        append_startup_log(f"tray diagnostics: {summary}")
        return summary

    def attach_icon(self, icon: Any) -> None:
        """Attach the active `pystray.Icon` instance to this controller."""
        self._icon = icon

    def _refresh_menu(self) -> None:
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:  # noqa: BLE001
                pass

    def build_menu(self) -> Any:
        """Build the `pystray` context menu reflecting live server status."""
        tray_api = cast("Any", pystray)

        def _status_text(_item: Any) -> str:
            if self.is_running() and self._live_port:
                return f"Status: Running (:{self._live_port})"
            return "Status: Stopped"

        def _can_start(_item: Any) -> bool:
            return not self.is_running()

        def _can_stop(_item: Any) -> bool:
            return self.is_running()

        def _wrap(fn: Callable[[], Any]) -> Callable[[Any, Any], None]:
            def _handler(_icon: Any, _item: Any) -> None:
                fn()

            return _handler

        return tray_api.Menu(
            tray_api.MenuItem(
                f"PFETL Analytics Studio v{__version__}",
                None,
                enabled=False,
            ),
            tray_api.MenuItem(_status_text, None, enabled=False),
            tray_api.Menu.SEPARATOR,
            tray_api.MenuItem("Open Studio", _wrap(self.open_studio), default=True),
            tray_api.MenuItem(
                "Start Server",
                _wrap(lambda: self.start_server(open_browser=False)),
                enabled=_can_start,
            ),
            tray_api.MenuItem("Stop Server", _wrap(self.stop_server), enabled=_can_stop),
            tray_api.MenuItem("Restart Server", _wrap(self.restart_server)),
            tray_api.Menu.SEPARATOR,
            tray_api.MenuItem("Open Data Folder", _wrap(self.open_data_folder)),
            tray_api.MenuItem("Open Logs Folder", _wrap(self.open_logs_folder)),
            tray_api.MenuItem(
                "Show Diagnostics",
                lambda icon, _item: self._notify_diagnostics(icon),  # pyright: ignore[reportUnknownLambdaType]
            ),
            tray_api.Menu.SEPARATOR,
            tray_api.MenuItem(
                "Exit",
                lambda icon, _item: self._exit_tray(icon),  # pyright: ignore[reportUnknownLambdaType]
            ),
        )

    def _notify_diagnostics(self, icon: Any) -> None:
        msg = self.diagnostics_summary()
        try:
            icon.notify(msg, "PFETL Analytics Studio")
        except Exception:  # noqa: BLE001
            pass

    def _exit_tray(self, icon: Any) -> None:
        self.stop_server()
        icon.stop()


def run_tray(
    config: StudioServerConfig | None = None,
    *,
    open_browser_on_start: bool = True,
) -> int:
    """Launch the single-instance system tray host and manage the local Studio server."""
    cfg = config or StudioServerConfig()
    mutex_handle = _acquire_single_instance_mutex()
    if mutex_handle is None:
        meta = read_runtime_metadata(default_runtime_file())
        if meta is not None and is_runtime_metadata_live(meta):
            host = str(meta.get("host") or cfg.host)
            port = int(meta["port"])
            if open_browser_on_start:
                webbrowser.open(f"http://{host}:{port}")
        append_startup_log("tray secondary launch detected; focused existing instance")
        return 0

    controller = StudioTrayController(config=cfg)
    tray_api = cast("Any", pystray)
    icon = tray_api.Icon(
        "pfetl_analytics_studio",
        load_tray_icon_image(),
        "PFETL Analytics Studio",
        menu=controller.build_menu(),
    )
    controller.attach_icon(icon)

    def _setup(tray_icon: Any) -> None:
        tray_icon.visible = True
        controller.start_server(open_browser=open_browser_on_start)

    try:
        icon.run(setup=_setup)
    finally:
        controller.stop_server()
        _release_single_instance_mutex(mutex_handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_tray())
