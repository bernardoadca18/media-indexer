"""Tests for HDD storage configuration validation in dependencies."""
import pytest

from app.api.dependencies import get_storage
from app.core.config import Settings


def test_get_storage_raises_when_hdd_backend_configured_without_mount(monkeypatch):
    """
    Test that get_storage raises RuntimeError when STORAGE_ROOT_HDD is set
    but MEDIA_INDEXER_HDD_MOUNT_PATH is not configured.
    """
    # Clear the lru_cache to allow fresh evaluation
    get_storage.cache_clear()

    # Create settings with HDD backend but no mount
    settings = Settings(
        storage_root="/tmp/primary",
        storage_root_hdd="/data/media-hdd",
        media_indexer_hdd_mount_path=None,  # Missing mount
    )

    monkeypatch.setattr("app.api.dependencies.settings", settings)

    with pytest.raises(RuntimeError, match="STORAGE_ROOT_HDD is set but the HDD mount is not configured"):
        get_storage()


def test_get_storage_raises_when_hdd_mount_configured_without_backend(monkeypatch):
    """
    Test that get_storage raises RuntimeError when MEDIA_INDEXER_HDD_MOUNT_PATH is set
    but STORAGE_ROOT_HDD is not configured.
    """
    # Clear the lru_cache to allow fresh evaluation
    get_storage.cache_clear()

    # Create settings with mount but no HDD backend
    settings = Settings(
        storage_root="/tmp/primary",
        storage_root_hdd=None,  # Missing backend
        media_indexer_hdd_mount_path="/host/media-hdd",
    )

    monkeypatch.setattr("app.api.dependencies.settings", settings)

    with pytest.raises(RuntimeError, match="HDD mount is configured but STORAGE_ROOT_HDD is not set"):
        get_storage()


def test_get_storage_succeeds_when_both_hdd_configured_or_neither(monkeypatch, tmp_path):
    """
    Test that get_storage succeeds when both HDD settings are configured together
    or when neither is configured.
    """
    # Clear the lru_cache to allow fresh evaluation
    get_storage.cache_clear()

    # Case 1: Both configured - mock is_mount_point to return True for test path
    settings_both = Settings(
        storage_root=str(tmp_path / "primary"),
        storage_root_hdd=str(tmp_path / "hdd"),
        media_indexer_hdd_mount_path="/host/media-hdd",
    )
    monkeypatch.setattr("app.api.dependencies.settings", settings_both)
    monkeypatch.setattr("app.services.storage.is_mount_point", lambda p: True)
    storage = get_storage()
    assert storage is not None

    # Clear cache for next test
    get_storage.cache_clear()

    # Case 2: Neither configured
    settings_neither = Settings(
        storage_root=str(tmp_path / "primary2"),
        storage_root_hdd=None,
        media_indexer_hdd_mount_path=None,
    )
    monkeypatch.setattr("app.api.dependencies.settings", settings_neither)
    storage = get_storage()
    assert storage is not None
