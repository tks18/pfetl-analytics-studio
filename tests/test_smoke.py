"""Smoke tests for PFETL Analytics Studio."""

import pytest
from pfetl_analytics_studio import __version__
from pfetl_analytics_studio.app import create_app
from pfetl_analytics_studio.cli import validate_loopback_host
from pfetl_analytics_studio.state import get_studio_state_path


def test_package_version() -> None:
    """Verify package version resolves correctly."""
    assert __version__.startswith("0.1.0")


def test_loopback_validation_allowed() -> None:
    """Verify loopback addresses pass validation cleanly."""
    for host in ("127.0.0.1", "localhost", "::1", "127.0.0.2"):
        validate_loopback_host(host)


def test_loopback_validation_rejected() -> None:
    """Verify non-loopback addresses are rejected with ValueError."""
    for host in ("0.0.0.0", "192.168.1.50", "10.0.0.1", "external.domain.com"):
        with pytest.raises(ValueError, match="Security violation"):
            validate_loopback_host(host)


def test_create_app_initialization() -> None:
    """Verify create_app registers presentation shell routes without I/O errors."""
    create_app()


def test_studio_state_path() -> None:
    """Verify Studio state database destination path resolution."""
    path = get_studio_state_path()
    assert path.name == "studio.sqlite"
    assert "PFETL Analytics Studio" in str(path)

