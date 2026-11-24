"""Global application presentation context, navigation routes, and session state controller.

Owns
----
- `NavRoute`, `NAV_ROUTES`, `RunSelectionMode`, `RunOption`, and `AppContext` contracts.
- `AppSessionManager` coordinating profile/run/period/filter updates and `QueryCache` invalidation.

Does not own
------------
- NiceGUI widget rendering (`app.py`, `ui/`, `pages/`).
- Domain analytical SQL queries (`analytics/`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from pfetl_analytics_studio.data import (
    CompatibilityReport,
    CompatibilityStatus,
    ControlPlaneReader,
    ControlPlaneState,
    DriftCheckResult,
    DuckDBReader,
    PFETLCatalog,
    PFETLDataAccessError,
    QueryCache,
    RunProvenance,
    WarehouseState,
    evaluate_profile_drift,
)
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.semantic import (
    FilterSet,
    PeriodContext,
    PeriodRequest,
    PeriodResolver,
)


class RunSelectionMode(StrEnum):
    """Mode determining which PFETL execution run supplies provenance and cache scope."""

    LATEST_SUCCESSFUL = "latest_successful"
    HISTORICAL_RUN = "historical_run"


@dataclass(frozen=True)
class NavRoute:
    """Fixed left-navigation route metadata descriptor."""

    route_id: str
    path: str
    label: str
    icon: str
    description: str
    requires_warehouse: bool = True


NAV_ROUTES: tuple[NavRoute, ...] = (
    NavRoute(
        route_id="overview",
        path="/overview",
        label="Overview",
        icon="dashboard",
        description="Monthly financial cockpit, attribution bridge, and data-health strip",
    ),
    NavRoute(
        route_id="household",
        path="/household",
        label="Household",
        icon="account_balance_wallet",
        description="Income resilience, core/non-core expenses, budget variance, and cashflow",
    ),
    NavRoute(
        route_id="wealth",
        path="/wealth",
        label="Wealth",
        icon="savings",
        description="Net worth balance sheet, liquidity runway, drawdown, and attribution",
    ),
    NavRoute(
        route_id="investments",
        path="/investments",
        label="Investments",
        icon="trending_up",
        description="Allocation, XIRR performance, benchmarks, FX, lots, and reconciliation",
    ),
    NavRoute(
        route_id="tax",
        path="/tax",
        label="Tax",
        icon="receipt_long",
        description="Financial-year tax position, reconciliation, and harvesting exposure",
    ),
    NavRoute(
        route_id="fire",
        path="/fire",
        label="FIRE",
        icon="local_fire_department",
        description="Financial independence targets, runway trajectories, and simulations",
    ),
    NavRoute(
        route_id="operations",
        path="/operations",
        label="Operations",
        icon="engineering",
        description="PFETL runs, sync health, artifacts, providers, and forensic JSONL logs",
        requires_warehouse=False,
    ),
    NavRoute(
        route_id="explorer",
        path="/explorer",
        label="Explorer",
        icon="explore",
        description="82-surface data catalog, guided safe analytics, and read-only SQL",
    ),
    NavRoute(
        route_id="reports",
        path="/reports",
        label="Reports",
        icon="description",
        description="Prebuilt and custom multi-format PDF, XLSX, CSV, and chart reports",
    ),
)

ROUTES_BY_ID: dict[str, NavRoute] = {r.route_id: r for r in NAV_ROUTES}


@dataclass(frozen=True)
class RunOption:
    """Selectable PFETL run summary shown in the shell run selector."""

    run_id: str
    label: str
    status: str | None
    commit_state: str | None
    started_at: str | None


class AppContext(BaseModel):
    """Immutable presentation context snapshot shared across shell and page renderers."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    profile: PFETLProfile | None = None
    run_mode: RunSelectionMode = RunSelectionMode.LATEST_SUCCESSFUL
    selected_run_id: str | None = None
    available_runs: tuple[RunOption, ...] = ()
    provenance: RunProvenance | None = None
    compatibility: CompatibilityReport | None = None
    drift: DriftCheckResult | None = None
    period_request: PeriodRequest = PeriodRequest()
    period_context: PeriodContext | None = None
    filters: FilterSet = FilterSet()
    refresh_token: int = 0

    @property
    def profile_id(self) -> str | None:
        """Return active profile identifier, or `None` when no profile is loaded."""
        return self.profile.profile_id if self.profile is not None else None

    @property
    def effective_run_id(self) -> str | None:
        """Return currently pinned PFETL run ID (`selected_run_id` or provenance run)."""
        if self.selected_run_id:
            return self.selected_run_id
        return self.provenance.run_id if self.provenance is not None else None

    @property
    def is_warehouse_locked(self) -> bool:
        """Return True if PFETL `pipeline.lock` currently guards DuckDB."""
        if self.compatibility is not None:
            if self.compatibility.warehouse_state == WarehouseState.LOCKED:
                return True
        return self.profile.lock_path.exists() if self.profile is not None else False

    @property
    def is_incompatible(self) -> bool:
        """Return True when upstream schema compatibility failed structurally."""
        return (
            self.compatibility is not None
            and self.compatibility.status == CompatibilityStatus.INCOMPATIBLE
        )

    @property
    def cache_namespace(self) -> tuple[str, str]:
        """Return `(profile_id, effective_run_id)` cache namespace tuple."""
        return (self.profile_id or "none", self.effective_run_id or "none")


class AppSessionManager:
    """Stateful session controller synchronizing `AppContext` transitions and `QueryCache`."""

    def __init__(
        self,
        initial_profile: PFETLProfile | None = None,
        cache: QueryCache | None = None,
    ) -> None:
        """Initialize session state lazily without performing upfront DB I/O.

        Parameters
        ----------
        initial_profile : PFETLProfile | None, optional
            Initial resolved profile supplied from CLI or profile picker.
        cache : QueryCache | None, optional
            Bounded query cache instance bound to this session.
        """
        self.cache = cache if cache is not None else QueryCache(max_entries=256)
        self._hydrated = False
        self._context = AppContext(profile=initial_profile)

    @property
    def context(self) -> AppContext:
        """Return current immutable `AppContext` snapshot."""
        return self._context

    def ensure_hydrated(self) -> AppContext:
        """Hydrate catalog, provenance, drift, and period once when a profile is active."""
        if not self._hydrated:
            self._hydrate_from_stores()
        return self._context

    def switch_profile(
        self,
        new_profile: PFETLProfile | None,
        hydrate: bool = True,
    ) -> AppContext:
        """Switch active profile, reset run/period/filters, and evict prior profile cache."""
        old_id = self._context.profile_id
        if old_id is not None:
            self.cache.invalidate_profile(old_id)

        self._context = AppContext(
            profile=new_profile,
            run_mode=RunSelectionMode.LATEST_SUCCESSFUL,
            selected_run_id=None,
            available_runs=(),
            provenance=None,
            compatibility=None,
            drift=None,
            period_request=PeriodRequest(),
            period_context=None,
            filters=FilterSet(),
            refresh_token=self._context.refresh_token + 1,
        )
        self._hydrated = False
        if hydrate and new_profile is not None:
            self._hydrate_from_stores()
        return self._context

    def select_run(
        self,
        run_id: str | None,
        hydrate: bool = True,
    ) -> AppContext:
        """Pin provenance to a historical `run_id` or revert to latest successful run."""
        mode = (
            RunSelectionMode.HISTORICAL_RUN
            if run_id is not None
            else RunSelectionMode.LATEST_SUCCESSFUL
        )
        self._context = self._context.model_copy(
            update={
                "run_mode": mode,
                "selected_run_id": run_id,
                "refresh_token": self._context.refresh_token + 1,
            }
        )
        if hydrate and self._context.profile is not None:
            self._hydrate_provenance_and_drift()
        return self._context

    def set_period(
        self,
        request: PeriodRequest,
        hydrate: bool = True,
    ) -> AppContext:
        """Update requested `PeriodPreset` and resolve boundaries when DuckDB is available."""
        self._context = self._context.model_copy(
            update={
                "period_request": request,
                "period_context": None,
                "refresh_token": self._context.refresh_token + 1,
            }
        )
        if hydrate and self._context.profile is not None:
            self._hydrate_period()
        return self._context

    def set_filters(self, filters: FilterSet) -> AppContext:
        """Update normalized global `FilterSet` and advance refresh token."""
        self._context = self._context.model_copy(
            update={
                "filters": filters,
                "refresh_token": self._context.refresh_token + 1,
            }
        )
        return self._context

    def refresh(self, hydrate: bool = True) -> AppContext:
        """Invalidate active run cache entries and reload compatibility/provenance state."""
        prof_id, run_id = self._context.cache_namespace
        if prof_id != "none" and run_id != "none":
            self.cache.invalidate_run(prof_id, run_id)

        self._context = self._context.model_copy(
            update={"refresh_token": self._context.refresh_token + 1}
        )
        self._hydrated = False
        if hydrate and self._context.profile is not None:
            self._hydrate_from_stores()
        return self._context

    def _hydrate_from_stores(self) -> None:
        """Inspect upstream read-only DuckDB & Control Plane safely without raising."""
        profile = self._context.profile
        if profile is None:
            self._hydrated = True
            return

        catalog = PFETLCatalog(profile)
        _, report = catalog.discover()
        runs = self._load_run_options(profile)

        self._context = self._context.model_copy(
            update={
                "compatibility": report,
                "available_runs": runs,
            }
        )
        self._hydrate_provenance_and_drift(catalog=catalog)
        self._hydrate_period()
        self._hydrated = True

    def _hydrate_provenance_and_drift(self, catalog: PFETLCatalog | None = None) -> None:
        """Load selected or latest successful run provenance and evaluate TOML drift."""
        profile = self._context.profile
        if profile is None:
            return
        cat = catalog if catalog is not None else PFETLCatalog(profile)
        if (
            self._context.run_mode == RunSelectionMode.HISTORICAL_RUN
            and self._context.selected_run_id
        ):
            prov = cat.get_run_provenance(self._context.selected_run_id)
        else:
            prov = cat.get_latest_provenance()

        drift = evaluate_profile_drift(profile, prov)
        self._context = self._context.model_copy(update={"provenance": prov, "drift": drift})

    def _hydrate_period(self) -> None:
        """Resolve `PeriodRequest` against `silver.dim_calendar` when DuckDB is unlocked."""
        profile = self._context.profile
        if profile is None:
            return
        duck_reader = DuckDBReader(profile)
        if duck_reader.get_state() != WarehouseState.AVAILABLE:
            return
        try:
            period_ctx = PeriodResolver(duck_reader).resolve(self._context.period_request)
            self._context = self._context.model_copy(update={"period_context": period_ctx})
        except PFETLDataAccessError:
            pass

    @staticmethod
    def _load_run_options(profile: PFETLProfile) -> tuple[RunOption, ...]:
        """Fetch recent PFETL runs from Control Plane SQLite for the run selector dropdown."""
        cp_reader = ControlPlaneReader(profile)
        if cp_reader.get_state() != ControlPlaneState.AVAILABLE:
            return ()
        if not cp_reader.table_exists("cp_runs"):
            return ()
        try:
            rows = cp_reader.fetch_all(
                """
                SELECT run_id, started_at, status, commit_state, application_version
                FROM cp_runs
                ORDER BY started_at DESC
                LIMIT 20;
                """
            )
        except PFETLDataAccessError:
            return ()

        options: list[RunOption] = []
        for row in rows:
            run_id = str(row["run_id"])
            status = str(row["status"]) if row.get("status") else None
            commit_state = str(row["commit_state"]) if row.get("commit_state") else None
            started = str(row["started_at"])[:16] if row.get("started_at") else ""
            ver = f" {row['application_version']}" if row.get("application_version") else ""
            label = f"{run_id[:8]} ({status or 'UNKNOWN'}{ver}) — {started}".strip()
            options.append(
                RunOption(
                    run_id=run_id,
                    label=label,
                    status=status,
                    commit_state=commit_state,
                    started_at=str(row["started_at"]) if row.get("started_at") else None,
                )
            )
        return tuple(options)
