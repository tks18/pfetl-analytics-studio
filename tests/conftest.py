"""Pytest fixtures and configuration for PFETL Analytics Studio tests."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from pfetl_analytics_studio.state import StudioStateStore


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

