"""NiceGUI application factory and shell layout for PFETL Analytics Studio."""

from __future__ import annotations

from nicegui import ui

from pfetl_analytics_studio import __version__


def create_app() -> None:
    """Register application pages and render bootstrap shell layout.

    Notes
    -----
    This function registers presentation routes. It does not open databases or
    perform I/O during initialization.
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

        with ui.header().classes("items-center justify-between px-6 py-3 bg-slate-900 text-white"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("analytics", size="sm").classes("text-cyan-400")
                ui.label("PFETL Analytics Studio").classes("text-lg font-bold tracking-tight")
                ui.badge(f"v{__version__}").props("outline color=cyan")

            with ui.row().classes("items-center gap-4 text-sm text-slate-300"):
                with ui.row().classes("items-center gap-1 text-amber-400"):
                    ui.icon("folder_off", size="xs")
                    ui.label("No PFETL profile selected").classes("font-medium")

        with ui.column().classes("w-full max-w-5xl mx-auto p-8 gap-6"):
            with ui.card().classes("w-full p-6 border border-slate-200 shadow-sm rounded-lg"):
                with ui.row().classes("items-center gap-3"):
                    ui.icon("info", size="md").classes("text-blue-500")
                    ui.label("Bootstrap Quality Baseline Active").classes("text-xl font-semibold")

                ui.markdown(
                    """
                    Welcome to **PFETL Analytics Studio**.

                    The application shell and quality baseline are successfully initialized.
                    In upcoming phases, you will be able to select your PFETL **Settings TOML** and
                    **FinancialRules TOML** profiles to explore warehouse facts, inspect data lineage,
                    and generate certified reports.
                    """
                ).classes("text-slate-600 leading-relaxed")

                with ui.row().classes("mt-4 gap-3"):
                    ui.button("Select Profile", icon="folder_open").props(
                        "unelevated color=primary disabled"
                    )
                    ui.button("View Documentation", icon="menu_book").props(
                        "outline color=secondary disabled"
                    )
