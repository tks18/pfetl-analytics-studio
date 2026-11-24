"""NiceGUI application factory and shell layout for PFETL Analytics Studio."""

from __future__ import annotations

from nicegui import ui

from pfetl_analytics_studio import __version__
from pfetl_analytics_studio.profile import PFETLProfile, ProfileDriftStatus
from pfetl_analytics_studio.state import StudioStateStore


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

        # Header bar
        with ui.header().classes("items-center justify-between px-6 py-3 bg-slate-900 text-white"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("analytics", size="sm").classes("text-cyan-400")
                ui.label("PFETL Analytics Studio").classes("text-lg font-bold tracking-tight")
                ui.badge(f"v{__version__}").props("outline color=cyan")

            with ui.row().classes("items-center gap-4 text-sm text-slate-300"):
                if profile is not None:
                    with ui.row().classes("items-center gap-2 text-emerald-400"):
                        ui.icon("folder", size="xs")
                        ui.label(f"Profile: {profile.label}").classes("font-semibold text-white")
                    drift_color = (
                        "positive"
                        if profile.drift_status == ProfileDriftStatus.MATCH
                        else "warning"
                    )
                    ui.badge(profile.drift_status.value).props(
                        f"color={drift_color} text-color=white"
                    )
                else:
                    with ui.row().classes("items-center gap-1 text-amber-400"):
                        ui.icon("folder_off", size="xs")
                        ui.label("No PFETL profile selected").classes("font-medium")

        # Main workspace body
        with ui.column().classes("w-full max-w-5xl mx-auto p-8 gap-6"):
            if profile is not None:
                # Active Profile Context Card
                with ui.card().classes("w-full p-6 border border-slate-200 shadow-sm rounded-lg"):
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.row().classes("items-center gap-3"):
                            ui.icon("check_circle", size="md").classes("text-emerald-500")
                            ui.label("Active PFETL Profile Context").classes(
                                "text-xl font-semibold"
                            )
                        ui.badge(f"ID: {profile.profile_id}").props("outline color=grey")

                    ui.separator().classes("my-2")

                    with ui.grid(columns=2).classes("w-full gap-4 text-sm mt-2"):
                        with ui.column().classes("gap-1"):
                            ui.label("Settings Configuration").classes("font-medium text-slate-500")
                            ui.label(str(profile.settings_path)).classes(
                                "font-mono text-xs bg-slate-100 p-2 rounded break-all"
                            )
                        with ui.column().classes("gap-1"):
                            ui.label("Financial Rules Configuration").classes(
                                "font-medium text-slate-500"
                            )
                            ui.label(str(profile.rules_path)).classes(
                                "font-mono text-xs bg-slate-100 p-2 rounded break-all"
                            )
                        with ui.column().classes("gap-1"):
                            ui.label("Warehouse Base Directory").classes(
                                "font-medium text-slate-500"
                            )
                            ui.label(str(profile.target_db_base_path)).classes(
                                "font-mono text-xs bg-slate-100 p-2 rounded break-all"
                            )
                        with ui.column().classes("gap-1"):
                            ui.label("Analytical DuckDB Warehouse").classes(
                                "font-medium text-slate-500"
                            )
                            ui.label(str(profile.duckdb_path)).classes(
                                "font-mono text-xs bg-slate-100 p-2 rounded break-all"
                            )
                        with ui.column().classes("gap-1"):
                            ui.label("Raw Store & Control Plane (SQLite)").classes(
                                "font-medium text-slate-500"
                            )
                            ui.label(str(profile.control_plane_path)).classes(
                                "font-mono text-xs bg-slate-100 p-2 rounded break-all"
                            )
                        with ui.column().classes("gap-1"):
                            ui.label("Observability Logs Directory").classes(
                                "font-medium text-slate-500"
                            )
                            ui.label(str(profile.logs_path or "Not configured")).classes(
                                "font-mono text-xs bg-slate-100 p-2 rounded break-all"
                            )

                    # Pipeline lock check
                    if profile.lock_path.exists():
                        with ui.row().classes(
                            "items-center gap-2 mt-4 p-3 bg-amber-50 border border-amber-300 rounded text-amber-800 text-sm"
                        ):
                            ui.icon("lock", size="sm").classes("text-amber-600")
                            ui.label(
                                "Notice: PFETL pipeline lock is currently active on warehouse. "
                                "Read-only analytical queries will defer until the batch finishes."
                            )
            else:
                # Bootstrap welcome card
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
