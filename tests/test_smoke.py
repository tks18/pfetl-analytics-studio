"""Smoke tests for PFETL Analytics Studio."""

from __future__ import annotations

from pathlib import Path

import pytest

from pfetl_analytics_studio.app import create_app
from pfetl_analytics_studio.cli import validate_loopback_host
from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.state import get_studio_state_path


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
    """Verify create_app registers presentation shell routes without profile."""
    create_app(profile=None)


def test_create_app_with_profile(valid_settings_path: Path, valid_rules_path: Path) -> None:
    """Verify create_app registers presentation shell routes with active profile."""
    res = PFETLProfile.load(valid_settings_path, valid_rules_path)
    assert res.profile is not None
    create_app(profile=res.profile)


def test_studio_state_path() -> None:
    """Verify Studio state database destination path resolution."""
    path = get_studio_state_path()
    assert path.name == "studio.sqlite"
    assert "PFETL Analytics Studio" in str(path)


def test_cli_profile_flags_mismatch() -> None:
    """Verify CLI rejects one profile argument without the other."""
    from pfetl_analytics_studio.cli import main

    assert main(["--settings", "some_path.toml"]) == 1
    assert main(["--rules", "some_rules.toml"]) == 1


def test_cli_profile_flags_invalid_file() -> None:
    """Verify CLI returns error code 1 when given non-existent profile files."""
    from pfetl_analytics_studio.cli import main

    assert main(["--settings", "non_existent.toml", "--rules", "non_existent.toml"]) == 1

