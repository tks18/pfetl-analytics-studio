"""Application startup, CLI validation, route registration, profile picker, and session tests."""

from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path

import pytest
from nicegui import core

from pfetl_analytics_studio.app import (
    NAV_ROUTES,
    AppSessionManager,
    create_app,
    list_available_picker_roots,
    list_toml_picker_entries,
    open_and_persist_profile,
    pick_native_toml_file,
)
from pfetl_analytics_studio.cli import main as cli_main, validate_loopback_host
from pfetl_analytics_studio.context import RunSelectionMode
from pfetl_analytics_studio.data import QueryCache, build_cache_key
from pfetl_analytics_studio.profile import PFETLProfile, ProfileLoadErrorCode
from pfetl_analytics_studio.semantic import (
    FilterField,
    FilterOperator,
    FilterSet,
    FilterSpec,
    PeriodPreset,
    PeriodRequest,
)
from pfetl_analytics_studio.state import StudioStateStore, get_studio_state_path
from tests.fixtures.build_fixture_databases import seed_calendar_and_gold_months


def test_loopback_validation_allowed() -> None:
    """Verify loopback addresses pass validation cleanly."""
    for host in ("127.0.0.1", "localhost", "::1", "127.0.0.2"):
        validate_loopback_host(host)


def test_loopback_validation_rejected() -> None:
    """Verify non-loopback addresses are rejected with ValueError."""
    for host in ("0.0.0.0", "192.168.1.50", "10.0.0.1", "external.domain.com"):
        with pytest.raises(ValueError, match="Security violation"):
            validate_loopback_host(host)


def test_create_app_without_profile() -> None:
    """Verify create_app registers all nine fixed routes without profile."""
    create_app(profile=None)
    registered_paths: set[str] = {
        path for r in core.app.routes if isinstance((path := getattr(r, "path", None)), str)
    }
    assert "/" in registered_paths
    for route in NAV_ROUTES:
        assert route.path in registered_paths


def test_create_app_with_profile(valid_settings_path: Path, valid_rules_path: Path) -> None:
    """Verify create_app registers routes cleanly with profile and zero upfront DB I/O."""
    res = PFETLProfile.load(valid_settings_path, valid_rules_path)
    assert res.profile is not None
    create_app(profile=res.profile)
    assert len(NAV_ROUTES) == 9
    assert [r.label for r in NAV_ROUTES] == [
        "Overview",
        "Household",
        "Wealth",
        "Investments",
        "Tax",
        "FIRE",
        "Operations",
        "Explorer",
        "Reports",
    ]


def test_studio_state_path() -> None:
    """Verify Studio state database destination path resolution."""
    path = get_studio_state_path()
    assert path.name == "studio.sqlite"
    assert "PFETL Analytics Studio" in str(path)


def test_cli_profile_flags_mismatch() -> None:
    """Verify CLI rejects one profile argument without the other."""
    assert cli_main(["--settings", "some_path.toml"]) == 1
    assert cli_main(["--rules", "some_rules.toml"]) == 1


def test_cli_profile_flags_invalid_file() -> None:
    """Verify CLI returns error code 1 when given non-existent profile files."""
    assert cli_main(["--settings", "non_existent.toml", "--rules", "non_existent.toml"]) == 1


def test_interactive_profile_picker_persistence_and_browser(
    valid_settings_path: Path,
    valid_rules_path: Path,
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """Verify interactive profile picker validates pairs, strips quotes, and protects recents."""
    state_db = tmp_path / "picker_state.sqlite"
    session = AppSessionManager(initial_profile=None)

    # Malformed pair fails cleanly and does NOT record into StudioStateStore
    bad_res = open_and_persist_profile(
        session,
        settings_path=str(fixtures_dir / "profiles" / "malformed.toml"),
        rules_path=str(valid_rules_path),
        state_db_path=state_db,
        hydrate=False,
    )
    assert not bad_res.is_ok
    assert bad_res.error is not None
    assert bad_res.error.code == ProfileLoadErrorCode.MALFORMED_TOML
    assert session.context.profile is None
    with StudioStateStore(db_path=state_db) as store:
        assert store.get_recent_profiles() == []

    # Valid quoted Windows/POSIX path loads, switches session, and records recent profile
    ok_res = open_and_persist_profile(
        session,
        settings_path=f'"{valid_settings_path}"',
        rules_path=f"'{valid_rules_path}'",
        label="  Interactive Test Profile  ",
        state_db_path=state_db,
        hydrate=False,
    )
    assert ok_res.is_ok
    assert ok_res.profile is not None
    assert ok_res.profile.label == "Interactive Test Profile"
    assert session.context.profile_id == ok_res.profile.profile_id
    with StudioStateStore(db_path=state_db) as store:
        recents = store.get_recent_profiles()
        assert len(recents) == 1
        assert recents[0].label == "Interactive Test Profile"

    # Local filesystem TOML directory browser discovers profile TOMLs only
    resolved, _dirs, tomls = list_toml_picker_entries(valid_settings_path.parent)
    assert resolved == valid_settings_path.parent.resolve()
    toml_names = {p.name for p in tomls}
    assert "valid_settings.toml" in toml_names
    assert "valid_rules.toml" in toml_names


def test_app_session_manager_transitions_and_cache_invalidation(
    profile_with_full_atlas: PFETLProfile,
    valid_settings_path: Path,
    valid_rules_path: Path,
) -> None:
    """Verify AppSessionManager deterministically updates context, provenance, and QueryCache."""
    seed_calendar_and_gold_months(profile_with_full_atlas.duckdb_path)

    cache = QueryCache(max_entries=32)
    session = AppSessionManager(initial_profile=profile_with_full_atlas, cache=cache)
    ctx1 = session.ensure_hydrated()

    assert ctx1.profile_id == profile_with_full_atlas.profile_id
    assert ctx1.compatibility is not None
    assert ctx1.compatibility.is_compatible is True
    assert ctx1.effective_run_id == "run-latest-002"
    assert ctx1.period_context is not None
    assert ctx1.period_context.end_date == date(2026, 10, 31)
    assert len(ctx1.available_runs) == 3

    ck = build_cache_key(profile_with_full_atlas.profile_id, "run-latest-002", "overview_kpi")
    cache.set(ck, {"cached": True})
    assert cache.get(ck) == {"cached": True}

    tok_before_run = ctx1.refresh_token
    ctx2 = session.select_run("run-hist-001")
    assert ctx2.run_mode == RunSelectionMode.HISTORICAL_RUN
    assert ctx2.selected_run_id == "run-hist-001"
    assert ctx2.provenance is not None
    assert ctx2.provenance.application_version == "v11.3.1"
    assert ctx2.provenance.rules_payload is not None
    assert ctx2.provenance.rules_payload["DEFAULT_CURRENCY_ID"] == "USD_USD"
    assert ctx2.refresh_token == tok_before_run + 1

    ctx3 = session.set_period(PeriodRequest(preset=PeriodPreset.FYTD))
    assert ctx3.period_context is not None
    assert ctx3.period_context.start_date == date(2026, 4, 1)

    ctx4 = session.set_filters(
        FilterSet(
            filters=(FilterSpec(field=FilterField.FY, operator=FilterOperator.EQ, value="2026-27"),)
        )
    )
    assert len(ctx4.filters.filters) == 1
    assert ctx4.refresh_token > ctx3.refresh_token

    other_res = PFETLProfile.load(valid_settings_path, valid_rules_path)
    assert other_res.profile is not None
    ctx5 = session.switch_profile(other_res.profile, hydrate=False)
    assert ctx5.profile_id == other_res.profile.profile_id
    assert ctx5.selected_run_id is None
    assert ctx5.period_context is None
    assert ctx5.filters.filters == ()
    assert cache.get(ck) is None


def test_pick_native_toml_file_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify native OS file picker wrapper handles selection, cancel, and fallback cleanly."""
    sample_toml = tmp_path / "settings.toml"
    sample_toml.write_text("[app]\n", encoding="utf-8")
    seen_commands: list[list[str]] = []

    def _fake_success(
        cmd: list[str],
        *,
        capture_output: bool,
        text: bool,
        encoding: str,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert encoding == "utf-8"
        assert check is False
        seen_commands.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"{sample_toml}\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_success)
    chosen, supported = pick_native_toml_file("Select Settings", initial_path=f'"{sample_toml}"')
    assert chosen == str(sample_toml)
    assert supported is True
    assert seen_commands[0][3] == "Select Settings"
    assert Path(seen_commands[0][4]) == tmp_path

    def _fake_cancel(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_cancel)
    assert pick_native_toml_file("Select Settings") == (None, True)

    def _fake_error(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="no display")

    monkeypatch.setattr(subprocess, "run", _fake_error)
    assert pick_native_toml_file("Select Settings") == (None, False)

    def _fake_os_error(_cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("spawn blocked")

    monkeypatch.setattr(subprocess, "run", _fake_os_error)
    assert pick_native_toml_file("Select Settings") == (None, False)


def test_available_roots_includes_all_mounted_drives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify fallback root listing exposes drives beyond index 5 (e.g. G:\\) without capping."""
    orig_exists = Path.exists

    def _fake_exists(path_obj: Path) -> bool:
        raw = str(path_obj)
        if len(raw) == 3 and raw.endswith(":\\") and raw[0] in "ACDEFGH":
            return True
        return orig_exists(path_obj)

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(Path, "exists", _fake_exists)

    roots = list_available_picker_roots()
    assert Path("G:\\") in roots
    assert Path("H:\\") in roots
    assert len(roots) >= 7
