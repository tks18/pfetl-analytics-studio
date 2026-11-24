"""Pytest fixtures and configuration for PFETL Analytics Studio tests."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.state import StudioStateStore
from tests.fixtures.build_fixture_databases import (
    create_full_atlas_databases,
    create_test_databases,
)


@pytest.fixture
def fixtures_dir() -> Path:
    """Return path to tests/fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def valid_settings_path(fixtures_dir: Path) -> Path:
    """Return path to canonical valid_settings.toml fixture."""
    return fixtures_dir / "profiles" / "valid_settings.toml"


@pytest.fixture
def valid_rules_path(fixtures_dir: Path) -> Path:
    """Return path to canonical valid_rules.toml fixture."""
    return fixtures_dir / "profiles" / "valid_rules.toml"


@pytest.fixture
def temp_state_store(tmp_path: Path) -> Generator[StudioStateStore, None, None]:
    """Provide an isolated StudioStateStore instance backed by temporary sqlite db."""
    db_path = tmp_path / "test_studio.sqlite"
    store = StudioStateStore(db_path=db_path)
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def profile_with_databases(tmp_path: Path) -> PFETLProfile:
    """Provide a PFETLProfile bound to real hermetic DuckDB and SQLite test databases."""
    base_dir = tmp_path / "outputs"
    duckdb_path, cp_path = create_test_databases(base_dir)

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        f"""
[database]
target_db_base_path = "{base_dir.as_posix()}"
target_db_name = "{duckdb_path.name}"
raw_document_store_name = "{cp_path.name}"
disable_file_discoverer = false

[observability]
logs_directory = "logs/runs"
""",
        encoding="utf-8",
    )

    rules_file = tmp_path / "financial_rules.toml"
    rules_file.write_text('DEFAULT_CURRENCY_ID = "INR_INR"\n', encoding="utf-8")

    load_res = PFETLProfile.load(settings_file, rules_file, label="Hermetic Test Profile")
    assert load_res.profile is not None
    return load_res.profile


@pytest.fixture
def profile_with_full_atlas(tmp_path: Path) -> PFETLProfile:
    """Provide a PFETLProfile backed by all 82 synthetic surfaces, snapshots, and logs."""
    base_dir = tmp_path / "full_outputs"
    duckdb_path, cp_path = create_full_atlas_databases(base_dir)

    settings_file = tmp_path / "full_settings.toml"
    settings_file.write_text(
        f"""
[database]
target_db_base_path = "{base_dir.as_posix()}"
target_db_name = "{duckdb_path.name}"
raw_document_store_name = "{cp_path.name}"
disable_file_discoverer = false

[observability]
logs_directory = "logs/runs"
""",
        encoding="utf-8",
    )

    rules_file = tmp_path / "full_rules.toml"
    rules_file.write_text('DEFAULT_CURRENCY_ID = "INR_INR"\n', encoding="utf-8")

    load_res = PFETLProfile.load(settings_file, rules_file, label="Full 82-Surface Test Profile")
    assert load_res.profile is not None
    return load_res.profile


