"""Tests for HDD mount validation and temp directory configuration."""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.storage import LocalStorage, is_mount_point


class TestIsMountPoint:
    """Tests for the is_mount_point function."""

    def test_returns_false_for_nonexistent_path(self, tmp_path: Path) -> None:
        """Nonexistent paths are not mount points."""
        nonexistent = tmp_path / "does_not_exist"
        assert not is_mount_point(nonexistent)

    def test_returns_false_for_regular_directory(self, tmp_path: Path) -> None:
        """Regular directories are not mount points."""
        assert not is_mount_point(tmp_path)

    @patch("app.services.storage.Path.exists")
    @patch("app.services.storage.Path.read_text")
    def test_returns_true_when_in_mountinfo(self, mock_read_text: MagicMock, mock_exists: MagicMock) -> None:
        """Path in /proc/self/mountinfo is recognized as mount point."""
        mock_exists.return_value = True
        mock_read_text.return_value = "42 25 0:41 / /data/media-hdd rw,relatime - ext4 /dev/sdb1 rw\n"

        result = is_mount_point(Path("/data/media-hdd"))
        assert result is True

    @patch("app.services.storage.Path.exists")
    @patch("app.services.storage.Path.read_text")
    def test_returns_false_when_not_in_mountinfo(self, mock_read_text: MagicMock, mock_exists: MagicMock) -> None:
        """Path not in /proc/self/mountinfo is not a mount point."""
        mock_exists.return_value = True
        mock_read_text.return_value = "42 25 0:41 / /other-path rw,relatime - ext4 /dev/sdb1 rw\n"

        result = is_mount_point(Path("/data/media-hdd"))
        assert result is False


class TestLocalStorageMountValidation:
    """Tests for LocalStorage HDD mount validation."""

    def test_no_hdd_configured_succeeds(self, tmp_path: Path) -> None:
        """When HDD is not configured, initialization succeeds."""
        storage = LocalStorage(tmp_path, root_hdd=None)
        assert storage.root_hdd is None

    def test_hdd_mount_validation_can_be_disabled(self, tmp_path: Path) -> None:
        """Mount validation can be disabled for testing."""
        # Create a directory that is NOT a mount point
        fake_hdd = tmp_path / "fake-hdd"
        fake_hdd.mkdir()

        # Should not raise when validation is disabled
        storage = LocalStorage(tmp_path, root_hdd=fake_hdd, validate_hdd_mount=False)
        assert storage.root_hdd == fake_hdd.resolve()

    def test_hdd_path_not_mount_raises_runtime_error(self, tmp_path: Path) -> None:
        """Non-mount HDD path raises RuntimeError when validation is enabled."""
        # Create a directory that is NOT a mount point
        fake_hdd = tmp_path / "fake-hdd"
        fake_hdd.mkdir()

        with pytest.raises(RuntimeError, match="not a mount point"):
            LocalStorage(tmp_path, root_hdd=fake_hdd, validate_hdd_mount=True)

    @patch("app.services.storage.is_mount_point")
    def test_hdd_valid_mount_succeeds(self, mock_is_mount: MagicMock, tmp_path: Path) -> None:
        """Valid mount point is accepted."""
        mock_is_mount.return_value = True
        fake_hdd = tmp_path / "real-hdd-mount"
        fake_hdd.mkdir()

        storage = LocalStorage(tmp_path, root_hdd=fake_hdd, validate_hdd_mount=True)
        assert storage.root_hdd == fake_hdd.resolve()
        mock_is_mount.assert_called_once_with(fake_hdd.resolve())


class TestTempDirectoryConfiguration:
    """Tests for temp directory configuration."""

    def test_temp_root_creates_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Setting temp_root creates the directory and sets env vars."""
        from app.core.config import Settings
        from app.main import configure_temp_directory

        temp_dir = tmp_path / "temp-hdd"
        settings = Settings(temp_root=temp_dir)
        monkeypatch.setattr("app.main.settings", settings)

        configure_temp_directory()

        assert temp_dir.exists()
        assert os.environ.get("TMPDIR") == str(temp_dir.resolve())
        assert os.environ.get("TMP") == str(temp_dir.resolve())
        assert os.environ.get("TEMP") == str(temp_dir.resolve())

    def test_temp_root_none_does_not_set_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When temp_root is None, env vars are not set."""
        from app.core.config import Settings
        from app.main import configure_temp_directory

        # Clear any existing values
        for key in ("TMPDIR", "TMP", "TEMP"):
            os.environ.pop(key, None)

        settings = Settings(temp_root=None)
        monkeypatch.setattr("app.main.settings", settings)

        configure_temp_directory()

        # Should not be set
        assert "TMPDIR" not in os.environ
        assert "TMP" not in os.environ
        assert "TEMP" not in os.environ
