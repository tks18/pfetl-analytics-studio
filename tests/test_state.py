"""Unit tests for StudioStateStore persistence and recent profiles management."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pfetl_analytics_studio.profile import PFETLProfile
from pfetl_analytics_studio.state import CURRENT_SCHEMA_VERSION, StudioStateStore


def test_state_store_initialization(temp_state_store: StudioStateStore) -> None:
    """Verify that StudioStateStore creates database file and initializes latest schema."""
    assert temp_state_store.db_path.exists()

    conn = sqlite3.connect(temp_state_store.db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA user_version;")
    version = cursor.fetchone()[0]
    conn.close()

    assert version == CURRENT_SCHEMA_VERSION


def test_recent_profiles_crud(
    temp_state_store: StudioStateStore,
    valid_settings_path: Path,
    valid_rules_path: Path,
) -> None:
    """Verify adding, ordering, removing, and clearing recent profile records."""
    load_res = PFETLProfile.load(valid_settings_path, valid_rules_path, label="Primary Profile")
    assert load_res.profile is not None
    profile1 = load_res.profile

    now = datetime.now(UTC)
    temp_state_store.record_profile_opened(profile1, opened_at=now)

    recents = temp_state_store.get_recent_profiles()
    assert len(recents) == 1
    assert recents[0].profile_id == profile1.profile_id
    assert recents[0].label == "Primary Profile"
    assert recents[0].settings_path == profile1.settings_path
    assert recents[0].rules_path == profile1.rules_path

    # Record a second mock profile with newer timestamp
    profile2 = PFETLProfile(
        profile_id="abcd1234efgh5678",
        label="Secondary Profile",
        settings_path=Path("/tmp/settings2.toml"),
        rules_path=Path("/tmp/rules2.toml"),
        target_db_base_path=Path("/tmp/outputs"),
        duckdb_path=Path("/tmp/outputs/db.duckdb"),
        control_plane_path=Path("/tmp/outputs/cp.sqlite"),
        lock_path=Path("/tmp/outputs/pipeline.lock"),
    )
    temp_state_store.record_profile_opened(profile2, opened_at=now + timedelta(minutes=5))

    recents2 = temp_state_store.get_recent_profiles()
    assert len(recents2) == 2
    # Newer profile appears first
    assert recents2[0].profile_id == "abcd1234efgh5678"
    assert recents2[1].profile_id == profile1.profile_id

    # Remove profile1
    temp_state_store.remove_recent_profile(profile1.profile_id)
    recents3 = temp_state_store.get_recent_profiles()
    assert len(recents3) == 1
    assert recents3[0].profile_id == "abcd1234efgh5678"

    # Clear all
    temp_state_store.clear_recent_profiles()
    assert len(temp_state_store.get_recent_profiles()) == 0


def test_app_preferences(temp_state_store: StudioStateStore) -> None:
    """Verify application preference get, set, update, and deletion."""
    assert temp_state_store.get_preference("reports.default_output_dir") is None
    assert (
        temp_state_store.get_preference("reports.default_output_dir", default="/default")
        == "/default"
    )

    temp_state_store.set_preference("reports.default_output_dir", "/user/reports")
    assert temp_state_store.get_preference("reports.default_output_dir") == "/user/reports"

    # Overwrite preference
    temp_state_store.set_preference("reports.default_output_dir", "/user/reports/v2")
    assert temp_state_store.get_preference("reports.default_output_dir") == "/user/reports/v2"

    # Delete preference
    temp_state_store.delete_preference("reports.default_output_dir")
    assert temp_state_store.get_preference("reports.default_output_dir") is None


def test_state_persistence_across_connections(
    tmp_path: Path,
    valid_settings_path: Path,
    valid_rules_path: Path,
) -> None:
    """Verify data persists when closing and reopening separate store instances on same file."""
    db_file = tmp_path / "persistent_studio.sqlite"

    profile_res = PFETLProfile.load(valid_settings_path, valid_rules_path)
    assert profile_res.profile is not None
    profile = profile_res.profile

    # First session writes
    with StudioStateStore(db_path=db_file) as store1:
        store1.record_profile_opened(profile)
        store1.set_preference("theme.mode", "dark")

    # Second session reads
    with StudioStateStore(db_path=db_file) as store2:
        recents = store2.get_recent_profiles()
        assert len(recents) == 1
        assert recents[0].profile_id == profile.profile_id
        assert store2.get_preference("theme.mode") == "dark"


def test_save_pin_rename_profile_and_session_clear(
    tmp_path: Path,
    valid_settings_path: Path,
    valid_rules_path: Path,
) -> None:
    """Verify saved profile pinning, renaming, and `AppSessionManager.clear_profile()`."""
    from pfetl_analytics_studio.context import AppSessionManager

    db_file = tmp_path / "profiles_v2.sqlite"
    res = PFETLProfile.load(valid_settings_path, valid_rules_path, label="Initial")
    assert res.profile is not None
    profile = res.profile

    with StudioStateStore(db_path=db_file) as store:
        store.save_profile(profile, label="Shan Prod", is_pinned=True, notes="Main warehouse")
        saved = store.get_recent_profiles()
        assert len(saved) == 1
        assert saved[0].label == "Shan Prod"
        assert saved[0].is_pinned is True
        assert saved[0].notes == "Main warehouse"

        store.rename_profile(profile.profile_id, "Shan Renamed")
        store.set_profile_pinned(profile.profile_id, False)
        updated = store.get_recent_profiles()
        assert updated[0].label == "Shan Renamed"
        assert updated[0].is_pinned is False

    session = AppSessionManager(initial_profile=profile)
    assert session.context.profile is not None
    cleared = session.clear_profile()
    assert cleared.profile is None
    assert cleared.provenance is None
