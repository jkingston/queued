"""Pytest fixtures for Queued tests."""

import pytest

from queued.models import AppSettings, RemoteFile

from .mocks.sftp_mock import MockSFTPClient, create_mock_files, create_mock_host


@pytest.fixture
def mock_files() -> list[RemoteFile]:
    """Provide standard mock files for testing."""
    return create_mock_files()


@pytest.fixture
def mock_sftp(mock_files: list[RemoteFile]) -> MockSFTPClient:
    """Provide a mock SFTP client."""
    client = MockSFTPClient(mock_files)
    return client


@pytest.fixture
def mock_host():
    """Provide a mock host."""
    return create_mock_host()


@pytest.fixture
def app_settings() -> AppSettings:
    """Provide default app settings for testing."""
    return AppSettings(
        max_concurrent_transfers=2,
        bandwidth_limit=None,
        download_dir="/tmp/queued-test",
        auto_refresh_interval=0,
        verify_checksums=False,
        resume_transfers=False,
    )
