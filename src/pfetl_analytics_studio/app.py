"""NiceGUI application factory and shell layout for PFETL Analytics Studio."""

from __future__ import annotations

from dataclasses import dataclass

from nicegui import ui

from pfetl_analytics_studio import __version__
from pfetl_analytics_studio.data import (
    ArchivedLogReader,
    CompatibilityReport,
    CompatibilityStatus,
    DriftCheckResult,
    PFETLCatalog,
    RunProvenance,
    SurfaceLayer,
    evaluate_profile_drift,
)
from pfetl_analytics_studio.profile import PFETLProfile, ProfileDriftStatus
from pfetl_analytics_studio.state import StudioStateStore


@dataclass(frozen=True)
class _ShellDiagnostics:
    """Lightweight presentation container for shell compatibility and provenance readout."""

    report: CompatibilityReport
    provenance: RunProvenance | None
    drift: DriftCheckResult
    has_any_log: bool


def _collect_shell_diagnostics(profile: PFETLProfile) -> _ShellDiagnostics:
    """Gather read-only compatibility, provenance, and drift summary for the active profile."""
    catalog = PFETLCatalog(profile)
    _, report = catalog.discover()
    provenance = catalog.get_latest_provenance()
    drift = evaluate_profile_drift(profile, provenance)
    has_any_log = False
    if provenance is not None:
        has_any_log = provenance.has_execution_log or ArchivedLogReader(profile).has_log(
            provenance.run_id
        )
    return _ShellDiagnostics(
        report=report,
        provenance=provenance,
        drift=drift,
        has_any_log=has_any_log,
    )


def _render_compatibility_card(diag: _ShellDiagnostics) -> None:
    """Render the Phase 04 upstream compatibility and run provenance summary card."""
    report = diag.report
    prov = diag.provenance
    status_color = {
        CompatibilityStatus.COMPATIBLE: "positive",
        CompatibilityStatus.DEGRADED: "warning",
        CompatibilityStatus.INCOMPATIBLE: "negative",
    }[report.status]

    with ui.card().classes("w-full p-6 border border-slate-200 shadow-sm rounded-lg"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("verified_user", size="md").classes("text-blue-600")
                ui.label("Upstream Compatibility & Run Provenance").classes("text-xl font-semibold")
            with ui.row().classes("items-center gap-2"):
                ui.badge(report.status.value).props(f"color={status_color} text-color=white")
                if prov and prov.application_version:
                    ui.badge(f"Core {prov.application_version}").props("outline color=primary")
                if prov and prov.schema_version:
                    ui.badge(f"Schema {prov.schema_version}").props("outline color=secondary")

        ui.separator().classes("my-2")

        with ui.grid(columns=5).classes("w-full gap-3 text-center my-2"):
            expected_counts = (
                ("Bronze", SurfaceLayer.BRONZE, 20),
                ("Silver", SurfaceLayer.SILVER, 24),
                ("Gold", SurfaceLayer.GOLD, 22),
                ("Meta", SurfaceLayer.META, 6),
                ("Control Plane", SurfaceLayer.CONTROL_PLANE, 10),
            )
            for label, layer, target in expected_counts:
                found = report.layer_counts.get(layer, 0)
                with ui.column().classes(
                    "items-center p-2 bg-slate-50 border border-slate-200 rounded"
                ):
                    ui.label(label).classes("text-xs font-medium text-slate-500")
                    ui.label(f"{found}/{target}").classes("text-base font-bold text-slate-800")

        with ui.row().classes("w-full items-center justify-between text-sm mt-3 flex-wrap gap-2"):
            if prov is not None:
                finished = (
                    prov.finished_at.strftime("%Y-%m-%d %H:%M:%S")
                    if prov.finished_at
                    else "In Progress"
                )
                ui.label(
                    f"Latest Successful Run: {prov.run_id[:8]} "
                    f"({prov.commit_state or 'UNKNOWN'}) — {finished}"
                ).classes("font-medium text-slate-700")
                with ui.row().classes("items-center gap-2"):
                    ui.badge("Settings Snapshot").props(
                        f"color={'positive' if prov.has_settings_snapshot else 'grey'}"
                    )
                    ui.badge("Rules Snapshot").props(
                        f"color={'positive' if prov.has_rules_snapshot else 'grey'}"
                    )
                    ui.badge("Execution Log").props(
                        f"color={'positive' if diag.has_any_log else 'grey'}"
                    )
            else:
                ui.label("No committed successful PFETL run found in Control Plane.").classes(
                    "text-slate-500 italic"
                )

        if report.diagnostics:
            with ui.column().classes(
                "w-full gap-1 mt-3 p-3 bg-amber-50 border border-amber-200 rounded text-xs text-amber-900"
            ):
                ui.label("Compatibility Diagnostics:").classes("font-semibold")
                for line in report.diagnostics[:8]:
                    ui.label(f"• {line}")

        if diag.drift.status != ProfileDriftStatus.MATCH and diag.drift.details:
            with ui.column().classes(
                "w-full gap-1 mt-2 p-3 bg-slate-50 border border-slate-200 rounded text-xs text-slate-700"
            ):
                ui.label(f"Profile Drift Details ({diag.drift.status.value}):").classes(
                    "font-semibold"
                )
                for detail in diag.drift.details[:6]:
                    ui.label(f"• {detail}")


def _render_profile_card(profile: PFETLProfile) -> None:
    """Render resolved paths and pipeline lock indicator for the active profile."""
    with ui.card().classes("w-full p-6 border border-slate-200 shadow-sm rounded-lg"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("check_circle", size="md").classes("text-emerald-500")
                ui.label("Active PFETL Profile Context").classes("text-xl font-semibold")
            ui.badge(f"ID: {profile.profile_id}").props("outline color=grey")

        ui.separator().classes("my-2")

        with ui.grid(columns=2).classes("w-full gap-4 text-sm mt-2"):
            path_items = (
                ("Settings Configuration", str(profile.settings_path)),
                ("Financial Rules Configuration", str(profile.rules_path)),
                ("Warehouse Base Directory", str(profile.target_db_base_path)),
                ("Analytical DuckDB Warehouse", str(profile.duckdb_path)),
                ("Raw Store & Control Plane (SQLite)", str(profile.control_plane_path)),
                ("Observability Logs Directory", str(profile.logs_path or "Not configured")),
            )
            for title, val in path_items:
                with ui.column().classes("gap-1"):
                    ui.label(title).classes("font-medium text-slate-500")
                    ui.label(val).classes("font-mono text-xs bg-slate-100 p-2 rounded break-all")

        if profile.lock_path.exists():
            with ui.row().classes(
                "items-center gap-2 mt-4 p-3 bg-amber-50 border border-amber-300 rounded text-amber-800 text-sm"
            ):
                ui.icon("lock", size="sm").classes("text-amber-600")
                ui.label(
                    "Notice: PFETL pipeline lock is currently active on warehouse. "
                    "Read-only analytical queries will defer until the batch finishes."
                )


def create_app(profile: PFETLProfile | None = None) -> None:
    """Register application pages and render bootstrap shell layout.

    Parameters
    ----------
    profile : PFETLProfile | None, optional
        Active loaded PFETL profile, by default None.

    Notes
    -----
    This function registers presentation routes. It does not open databases or
    perform analytical I/O during initialization.
    """

    @ui.page("/")
    def index_page() -> None:
        """Render primary workspace shell."""
        ui.colors(
            primary="#1976D2",
            secondary="#26A69A",
            accent="#9C27B0",
            dark="#1D1D1D",
            positive="#21BA45",
            negative="#C10015",
            info="#31CCEC",
            warning="#F2C037",
        )

        diag = _collect_shell_diagnostics(profile) if profile is not None else None
        effective_drift = diag.drift.status if diag is not None else None

        # Header bar
        with ui.header().classes("items-center justify-between px-6 py-3 bg-slate-900 text-white"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("analytics", size="sm").classes("text-cyan-400")
                ui.label("PFETL Analytics Studio").classes("text-lg font-bold tracking-tight")
                ui.badge(f"v{__version__}").props("outline color=cyan")

            with ui.row().classes("items-center gap-4 text-sm text-slate-300"):
                if profile is not None and effective_drift is not None:
                    with ui.row().classes("items-center gap-2 text-emerald-400"):
                        ui.icon("folder", size="xs")
                        ui.label(f"Profile: {profile.label}").classes("font-semibold text-white")
                    drift_color = (
                        "positive" if effective_drift == ProfileDriftStatus.MATCH else "warning"
                    )
                    ui.badge(effective_drift.value).props(f"color={drift_color} text-color=white")
                else:
                    with ui.row().classes("items-center gap-1 text-amber-400"):
                        ui.icon("folder_off", size="xs")
                        ui.label("No PFETL profile selected").classes("font-medium")

        # Main workspace body
        with ui.column().classes("w-full max-w-5xl mx-auto p-8 gap-6"):
            if profile is not None and diag is not None:
                _render_profile_card(profile)
                _render_compatibility_card(diag)
            else:
                with ui.card().classes("w-full p-6 border border-slate-200 shadow-sm rounded-lg"):
                    with ui.row().classes("items-center gap-3"):
                        ui.icon("info", size="md").classes("text-blue-500")
                        ui.label("Profile Resolution & Studio State Active").classes(
                            "text-xl font-semibold"
                        )

                    ui.markdown(
                        """
                        Welcome to **PFETL Analytics Studio**.

                        To explore financial truth, start by providing your PFETL **Settings TOML**
                        and **FinancialRules TOML** pair via the `--settings` and `--rules` CLI options,
                        or choose from recently opened profiles below.
                        """
                    ).classes("text-slate-600 leading-relaxed")

            # Recent Profiles Card
            recent_profiles = []
            try:
                with StudioStateStore() as store:
                    recent_profiles = store.get_recent_profiles(limit=5)
            except OSError:
                pass

            with ui.card().classes("w-full p-6 border border-slate-200 shadow-sm rounded-lg"):
                with ui.row().classes("items-center gap-3"):
                    ui.icon("history", size="md").classes("text-slate-700")
                    ui.label("Recent Profiles").classes("text-lg font-semibold")

                if recent_profiles:
                    with ui.column().classes("w-full divide-y divide-slate-100 mt-2"):
                        for item in recent_profiles:
                            with ui.row().classes("items-center justify-between py-3"):
                                with ui.column().classes("gap-0"):
                                    ui.label(item.label).classes("font-medium text-slate-900")
                                    ui.label(
                                        f"Settings: {item.settings_path.name} | Rules: {item.rules_path.name}"
                                    ).classes("text-xs text-slate-500 font-mono")
                                with ui.row().classes("items-center gap-2"):
                                    ui.label(
                                        item.last_opened_at.strftime("%Y-%m-%d %H:%M UTC")
                                    ).classes("text-xs text-slate-400")
                                    ui.badge(item.profile_id[:8]).props("outline color=grey")
                else:
                    ui.label("No recent profiles recorded yet.").classes(
                        "text-sm text-slate-500 italic mt-2"
                    )
