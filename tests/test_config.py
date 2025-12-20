"""Tests for configuration and cache management."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from queued.config import HostCache, QueueCache, SettingsManager, TransferStateCache
from queued.models import Host, Transfer, TransferDirection, TransferStatus


class TestSettingsManager:
    """Tests for SettingsManager."""

    def test_settings_defaults(self):
        """Default settings should have expected values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_config_dir", return_value=Path(tmpdir)):
                manager = SettingsManager()
                settings = manager.settings

                assert settings.max_concurrent_transfers == 3
                assert settings.bandwidth_limit is None
                assert settings.download_dir == "~/Downloads"
                assert settings.auto_refresh_interval == 30
                assert settings.verify_checksums is True
                assert settings.resume_transfers is True

    def test_settings_save_load_roundtrip(self):
        """Settings should persist correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_config_dir", return_value=Path(tmpdir)):
                # Save settings
                manager = SettingsManager()
                manager.update(
                    max_concurrent_transfers=5,
                    bandwidth_limit=1024 * 1024,
                    download_dir="/custom/downloads",
                    auto_refresh_interval=60,
                    verify_checksums=False,
                    resume_transfers=False,
                )

                # Load in new instance
                manager2 = SettingsManager()
                settings = manager2.settings

                assert settings.max_concurrent_transfers == 5
                assert settings.bandwidth_limit == 1024 * 1024
                assert settings.download_dir == "/custom/downloads"
                assert settings.auto_refresh_interval == 60
                assert settings.verify_checksums is False
                assert settings.resume_transfers is False

    def test_settings_partial_update(self):
        """Update should only modify specified fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_config_dir", return_value=Path(tmpdir)):
                manager = SettingsManager()
                original_dir = manager.settings.download_dir

                manager.update(max_concurrent_transfers=10)

                assert manager.settings.max_concurrent_transfers == 10
                assert manager.settings.download_dir == original_dir


class TestHostCache:
    """Tests for HostCache."""

    def test_host_cache_add_and_get(self):
        """Adding a host should make it retrievable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = HostCache()
                host = Host(
                    hostname="example.com",
                    username="testuser",
                    port=22,
                )

                cache.add(host)

                found = cache.get_by_hostname("example.com")
                assert found is not None
                assert found.username == "testuser"

    def test_host_cache_get_by_key(self):
        """Should find host by host_key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = HostCache()
                host = Host(
                    hostname="example.com",
                    username="testuser",
                    port=2222,
                )

                cache.add(host)

                found = cache.get_by_key("testuser@example.com:2222")
                assert found is not None
                assert found.hostname == "example.com"

    def test_host_cache_limit(self):
        """Cache should respect max_hosts limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = HostCache(max_hosts=3)

                for i in range(5):
                    cache.add(Host(hostname=f"host{i}.com", username="user"))

                # Should only have 3 hosts
                recent = cache.get_recent(10)
                assert len(recent) == 3

                # Most recent should be host4
                assert recent[0].hostname == "host4.com"

    def test_host_cache_updates_existing(self):
        """Adding same host should update, not duplicate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = HostCache()

                host1 = Host(
                    hostname="example.com",
                    username="testuser",
                    last_directory="/old",
                )
                cache.add(host1)

                host2 = Host(
                    hostname="example.com",
                    username="testuser",
                    last_directory="/new",
                )
                cache.add(host2)

                recent = cache.get_recent(10)
                assert len(recent) == 1
                assert recent[0].last_directory == "/new"

    def test_host_cache_persistence(self):
        """Cache should persist across instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache1 = HostCache()
                cache1.add(
                    Host(
                        hostname="example.com",
                        username="testuser",
                        key_path="/path/to/key",
                    )
                )

                # New instance should load from disk
                cache2 = HostCache()
                found = cache2.get_by_hostname("example.com")

                assert found is not None
                assert found.key_path == "/path/to/key"


class TestQueueCache:
    """Tests for QueueCache."""

    def test_queue_cache_save_load(self):
        """Queue should save and load correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = QueueCache()

                transfers = [
                    Transfer(
                        id="t1",
                        remote_path="/file1.txt",
                        local_path="/tmp/file1.txt",
                        direction=TransferDirection.DOWNLOAD,
                        size=1000,
                        status=TransferStatus.QUEUED,
                    ),
                    Transfer(
                        id="t2",
                        remote_path="/file2.txt",
                        local_path="/tmp/file2.txt",
                        direction=TransferDirection.DOWNLOAD,
                        size=2000,
                        status=TransferStatus.PAUSED,
                    ),
                ]

                cache.save(transfers)

                loaded, paused = cache.load()
                assert len(loaded) == 2
                assert loaded[0].id == "t1"
                assert loaded[0].status == TransferStatus.QUEUED
                assert loaded[1].status == TransferStatus.PAUSED

    def test_queue_cache_preserves_paused_state(self):
        """Queue paused state should be saved and loaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = QueueCache()

                transfers = [
                    Transfer(
                        id="t1",
                        remote_path="/file.txt",
                        local_path="/tmp/file.txt",
                        direction=TransferDirection.DOWNLOAD,
                        size=1000,
                        status=TransferStatus.QUEUED,
                    )
                ]

                cache.save(transfers, queue_paused=True)

                loaded, paused = cache.load()
                assert paused is True

    def test_queue_cache_excludes_completed(self):
        """Completed transfers should not be saved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = QueueCache()

                transfers = [
                    Transfer(
                        id="t1",
                        remote_path="/done.txt",
                        local_path="/tmp/done.txt",
                        direction=TransferDirection.DOWNLOAD,
                        size=1000,
                        status=TransferStatus.COMPLETED,
                    ),
                    Transfer(
                        id="t2",
                        remote_path="/pending.txt",
                        local_path="/tmp/pending.txt",
                        direction=TransferDirection.DOWNLOAD,
                        size=1000,
                        status=TransferStatus.QUEUED,
                    ),
                ]

                cache.save(transfers)

                loaded, _ = cache.load()
                assert len(loaded) == 1
                assert loaded[0].id == "t2"

    def test_queue_cache_excludes_failed(self):
        """Failed transfers should not be saved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = QueueCache()

                transfers = [
                    Transfer(
                        id="t1",
                        remote_path="/failed.txt",
                        local_path="/tmp/failed.txt",
                        direction=TransferDirection.DOWNLOAD,
                        size=1000,
                        status=TransferStatus.FAILED,
                    ),
                ]

                cache.save(transfers)

                loaded, _ = cache.load()
                assert len(loaded) == 0

    def test_queue_cache_transferring_becomes_stopped(self):
        """TRANSFERRING status should become STOPPED on load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = QueueCache()

                transfers = [
                    Transfer(
                        id="t1",
                        remote_path="/active.txt",
                        local_path="/tmp/active.txt",
                        direction=TransferDirection.DOWNLOAD,
                        size=1000,
                        status=TransferStatus.TRANSFERRING,
                    ),
                ]

                cache.save(transfers)

                loaded, _ = cache.load()
                assert len(loaded) == 1
                assert loaded[0].status == TransferStatus.STOPPED

    def test_queue_cache_clear(self):
        """Clear should remove the cache file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = QueueCache()

                transfers = [
                    Transfer(
                        id="t1",
                        remote_path="/file.txt",
                        local_path="/tmp/file.txt",
                        direction=TransferDirection.DOWNLOAD,
                        size=1000,
                        status=TransferStatus.QUEUED,
                    ),
                ]
                cache.save(transfers)
                assert cache.cache_file.exists()

                cache.clear()
                assert not cache.cache_file.exists()


class TestTransferStateCache:
    """Tests for TransferStateCache (resume support)."""

    def test_resume_offset_save_load(self):
        """Resume offset should be saved and retrieved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = TransferStateCache()

                # Create a partial file to simulate interrupted download
                local_path = Path(tmpdir) / "partial.txt"
                local_path.write_bytes(b"x" * 500)

                cache.save_transfer(
                    transfer_id="t1",
                    remote_path="/remote/file.txt",
                    local_path=str(local_path),
                    bytes_transferred=500,
                    total_size=1000,
                )

                offset = cache.get_resume_offset("/remote/file.txt", str(local_path))
                assert offset == 500

    def test_resume_offset_no_local_file(self):
        """If local file doesn't exist, offset should be 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = TransferStateCache()

                cache.save_transfer(
                    transfer_id="t1",
                    remote_path="/remote/file.txt",
                    local_path="/nonexistent/path.txt",
                    bytes_transferred=500,
                    total_size=1000,
                )

                offset = cache.get_resume_offset("/remote/file.txt", "/nonexistent/path.txt")
                assert offset == 0

    def test_resume_offset_size_mismatch(self):
        """If local file size doesn't match, offset should be 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = TransferStateCache()

                # Create file with different size than recorded
                local_path = Path(tmpdir) / "modified.txt"
                local_path.write_bytes(b"x" * 300)

                cache.save_transfer(
                    transfer_id="t1",
                    remote_path="/remote/file.txt",
                    local_path=str(local_path),
                    bytes_transferred=500,  # Doesn't match file size
                    total_size=1000,
                )

                offset = cache.get_resume_offset("/remote/file.txt", str(local_path))
                assert offset == 0

    def test_clear_transfer(self):
        """Clear should remove transfer from cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = TransferStateCache()

                local_path = Path(tmpdir) / "file.txt"
                local_path.write_bytes(b"x" * 500)

                cache.save_transfer(
                    transfer_id="t1",
                    remote_path="/file.txt",
                    local_path=str(local_path),
                    bytes_transferred=500,
                    total_size=1000,
                )

                cache.clear_transfer("t1")

                offset = cache.get_resume_offset("/file.txt", str(local_path))
                assert offset == 0

    def test_clear_by_path(self):
        """Clear by path should remove matching transfers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = TransferStateCache()

                local_path = Path(tmpdir) / "file.txt"
                local_path.write_bytes(b"x" * 500)

                cache.save_transfer(
                    transfer_id="t1",
                    remote_path="/file.txt",
                    local_path=str(local_path),
                    bytes_transferred=500,
                    total_size=1000,
                )

                cache.clear_by_path("/file.txt", str(local_path))

                offset = cache.get_resume_offset("/file.txt", str(local_path))
                assert offset == 0
