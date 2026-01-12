"""Tests for transfer manager."""

import hashlib
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from queued.config import QueueCache
from queued.models import (
    AppSettings,
    Host,
    RemoteFile,
    Transfer,
    TransferDirection,
    TransferStatus,
)
from queued.transfer import SpeedTracker, TransferManager, verify_file


class TestTransferManagerQueue:
    """Tests for queue operations."""

    def test_add_download_creates_transfer(self):
        """add_download should create a transfer in the queue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                settings = AppSettings(download_dir=tmpdir)
                manager = TransferManager(settings=settings)

                remote_file = RemoteFile(
                    name="test.txt",
                    path="/files/test.txt",
                    size=1000,
                    is_dir=False,
                )

                transfer = manager.add_download(remote_file)

                assert transfer is not None
                assert transfer.remote_path == "/files/test.txt"
                assert transfer.status == TransferStatus.QUEUED
                assert len(manager.queue.transfers) == 1

    def test_add_download_prevents_duplicates(self):
        """add_download should return None for duplicate files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                settings = AppSettings(download_dir=tmpdir)
                manager = TransferManager(settings=settings)

                remote_file = RemoteFile(
                    name="test.txt",
                    path="/files/test.txt",
                    size=1000,
                    is_dir=False,
                )

                transfer1 = manager.add_download(remote_file)
                transfer2 = manager.add_download(remote_file)

                assert transfer1 is not None
                assert transfer2 is None
                assert len(manager.queue.transfers) == 1

    def test_add_download_with_host(self):
        """add_download should set host_key when host provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                settings = AppSettings(download_dir=tmpdir)
                manager = TransferManager(settings=settings)

                host = Host(hostname="example.com", username="user", port=22)
                remote_file = RemoteFile(
                    name="test.txt",
                    path="/test.txt",
                    size=1000,
                    is_dir=False,
                )

                transfer = manager.add_download(remote_file, host=host)

                assert transfer is not None
                assert transfer.host_key == "user@example.com:22"

    def test_add_download_sanitizes_path_traversal(self):
        """add_download should sanitize path traversal attempts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                settings = AppSettings(download_dir=tmpdir)
                manager = TransferManager(settings=settings)

                # Path traversal attempt - should be sanitized to just "passwd"
                remote_file = RemoteFile(
                    name="../../../etc/passwd",
                    path="/files/../../../etc/passwd",
                    size=1000,
                    is_dir=False,
                )

                transfer = manager.add_download(remote_file)

                # Should succeed but sanitize to just the filename
                assert transfer is not None
                # Use Path.resolve() to handle macOS /private/var symlink
                expected = str(Path(tmpdir).resolve() / "passwd")
                assert transfer.local_path == expected
                # Verify it's within download dir (resolved)
                assert transfer.local_path.startswith(str(Path(tmpdir).resolve()))

    def test_add_download_blocks_invalid_filename(self):
        """add_download should block invalid filenames like '.' or '..'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                settings = AppSettings(download_dir=tmpdir)
                manager = TransferManager(settings=settings)

                remote_file = RemoteFile(
                    name="..",
                    path="/..",
                    size=1000,
                    is_dir=False,
                )

                with pytest.raises(ValueError, match="Invalid filename"):
                    manager.add_download(remote_file)

    def test_remove_transfer(self):
        """remove_transfer should remove transfer from queue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                settings = AppSettings(download_dir=tmpdir)
                manager = TransferManager(settings=settings)

                remote_file = RemoteFile(
                    name="test.txt",
                    path="/test.txt",
                    size=1000,
                    is_dir=False,
                )

                transfer = manager.add_download(remote_file)
                assert transfer is not None

                result = manager.remove_transfer(transfer.id)

                assert result is True
                assert len(manager.queue.transfers) == 0

    def test_remove_nonexistent_transfer(self):
        """remove_transfer should return False for nonexistent ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()
                result = manager.remove_transfer("nonexistent-id")
                assert result is False


class TestTransferManagerPauseResume:
    """Tests for pause/resume functionality."""

    def test_pause_sets_paused_status(self):
        """pause_transfer should mark transfer as paused."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()

                transfer = Transfer(
                    id="test-1",
                    remote_path="/test.txt",
                    local_path="/tmp/test.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.TRANSFERRING,
                )
                manager.queue.transfers.append(transfer)

                # Create a mock task that we can cancel
                mock_task = MagicMock()
                manager._tasks["test-1"] = mock_task

                result = manager.pause_transfer("test-1")

                assert result is True
                mock_task.cancel.assert_called_once()

    def test_resume_individual_transfer(self):
        """resume_transfer should set PAUSED transfer back to QUEUED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()

                transfer = Transfer(
                    id="test-1",
                    remote_path="/test.txt",
                    local_path="/tmp/test.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.PAUSED,
                )
                manager.queue.transfers.append(transfer)

                result = manager.resume_transfer("test-1")

                assert result is True
                assert transfer.status == TransferStatus.QUEUED

    def test_resume_works_on_stopped(self):
        """resume_transfer should resume STOPPED transfers (individual resume)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()

                transfer = Transfer(
                    id="test-1",
                    remote_path="/test.txt",
                    local_path="/tmp/test.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.STOPPED,
                )
                manager.queue.transfers.append(transfer)

                result = manager.resume_transfer("test-1")

                assert result is True
                assert transfer.status == TransferStatus.QUEUED


class TestTransferManagerStopResume:
    """Tests for stop/resume queue functionality."""

    def test_stop_queue_sets_flag(self):
        """stop_queue should set _queue_paused flag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()

                manager.stop_queue()

                assert manager._queue_paused is True
                assert manager.is_queue_paused is True

    def test_stop_queue_cancels_active_transfers(self):
        """stop_queue should cancel all active transfers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()

                transfer1 = Transfer(
                    id="t1",
                    remote_path="/file1.txt",
                    local_path="/tmp/file1.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.TRANSFERRING,
                )
                transfer2 = Transfer(
                    id="t2",
                    remote_path="/file2.txt",
                    local_path="/tmp/file2.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.TRANSFERRING,
                )
                manager.queue.transfers.extend([transfer1, transfer2])

                mock_task1 = MagicMock()
                mock_task2 = MagicMock()
                manager._tasks["t1"] = mock_task1
                manager._tasks["t2"] = mock_task2

                count = manager.stop_queue()

                assert count == 2
                mock_task1.cancel.assert_called_once()
                mock_task2.cancel.assert_called_once()

    def test_resume_queue_clears_flag(self):
        """resume_queue should clear _queue_paused flag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()
                manager._queue_paused = True

                manager.resume_queue()

                assert manager._queue_paused is False
                assert manager.is_queue_paused is False

    def test_resume_queue_only_resumes_stopped(self):
        """resume_queue should only resume STOPPED, not PAUSED transfers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()
                manager._queue_paused = True

                stopped = Transfer(
                    id="t1",
                    remote_path="/stopped.txt",
                    local_path="/tmp/stopped.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.STOPPED,
                )
                paused = Transfer(
                    id="t2",
                    remote_path="/paused.txt",
                    local_path="/tmp/paused.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.PAUSED,
                )
                manager.queue.transfers.extend([stopped, paused])

                count = manager.resume_queue()

                assert count == 1
                assert stopped.status == TransferStatus.QUEUED
                assert paused.status == TransferStatus.PAUSED  # Should stay paused


class TestTransferManagerPersistence:
    """Tests for queue persistence."""

    def test_queue_persisted_on_add(self):
        """Queue should be saved when transfer is added."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                settings = AppSettings(download_dir=tmpdir)
                manager = TransferManager(settings=settings)

                remote_file = RemoteFile(
                    name="test.txt",
                    path="/test.txt",
                    size=1000,
                    is_dir=False,
                )
                manager.add_download(remote_file)

                # Load in new instance
                manager2 = TransferManager(settings=settings)

                assert len(manager2.queue.transfers) == 1
                assert manager2.queue.transfers[0].remote_path == "/test.txt"

    def test_queue_starts_running_on_load(self):
        """Queue should always start running on load (not paused)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                settings = AppSettings(download_dir=tmpdir)
                manager = TransferManager(settings=settings)

                # Add a transfer so we have something to persist
                remote_file = RemoteFile(
                    name="test.txt",
                    path="/test.txt",
                    size=1000,
                    is_dir=False,
                )
                manager.add_download(remote_file)
                manager.stop_queue()

                # Load in new instance - queue should start running
                manager2 = TransferManager(settings=settings)

                # Queue always starts fresh (not paused)
                assert manager2.is_queue_paused is False

    def test_load_restores_transfers(self):
        """Loading should restore transfer list from cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                cache = QueueCache()

                # Pre-populate cache
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

                # Load in manager
                manager = TransferManager(queue_cache=cache)

                assert len(manager.queue.transfers) == 2


class TestTransferManagerCallbacks:
    """Tests for callback invocation."""

    def test_on_status_change_called_on_add(self):
        """on_status_change should be called when transfer is added."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                callback = MagicMock()
                settings = AppSettings(download_dir=tmpdir)
                manager = TransferManager(settings=settings, on_status_change=callback)

                remote_file = RemoteFile(
                    name="test.txt",
                    path="/test.txt",
                    size=1000,
                    is_dir=False,
                )
                manager.add_download(remote_file)

                callback.assert_called_once()
                call_arg = callback.call_args[0][0]
                assert call_arg.remote_path == "/test.txt"


class TestSpeedTracker:
    """Tests for SpeedTracker."""

    def test_speed_tracker_initial_zero(self):
        """Speed should be 0 initially."""
        tracker = SpeedTracker()
        speed = tracker.update(0)
        assert speed == 0.0

    def test_speed_tracker_calculates_speed(self):
        """SpeedTracker should calculate speed from samples."""
        tracker = SpeedTracker()

        # Simulate byte updates over time
        tracker.update(0)
        time.sleep(0.1)
        tracker.update(1000)
        time.sleep(0.1)
        speed = tracker.update(2000)

        # Should have positive speed
        assert speed > 0

    def test_speed_tracker_smoothing(self):
        """Speed should be smoothed across samples."""
        tracker = SpeedTracker()

        # First update establishes baseline
        tracker.update(0)
        time.sleep(0.05)

        # Several updates
        speeds = []
        for i in range(1, 6):
            time.sleep(0.05)
            speed = tracker.update(i * 1000)
            if speed > 0:
                speeds.append(speed)

        # All speeds should be relatively close (smoothed)
        if len(speeds) >= 2:
            variance = max(speeds) / min(speeds) if min(speeds) > 0 else 0
            # Variance should be reasonable (not wildly fluctuating)
            assert variance < 10, f"Speed variance too high: {speeds}"


class TestTransferManagerTotalSpeed:
    """Tests for total speed calculation."""

    def test_total_speed_sums_active(self):
        """total_speed should sum speed of all active transfers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()

                t1 = Transfer(
                    id="t1",
                    remote_path="/file1.txt",
                    local_path="/tmp/file1.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.TRANSFERRING,
                    speed=1000,
                )
                t2 = Transfer(
                    id="t2",
                    remote_path="/file2.txt",
                    local_path="/tmp/file2.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.TRANSFERRING,
                    speed=2000,
                )
                t3 = Transfer(
                    id="t3",
                    remote_path="/file3.txt",
                    local_path="/tmp/file3.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.PAUSED,  # Not active
                    speed=500,
                )
                manager.queue.transfers.extend([t1, t2, t3])

                assert manager.total_speed == 3000  # Only t1 + t2


class TestTransferManagerUpload:
    """Tests for upload functionality."""

    def test_add_upload_creates_transfer(self):
        """add_upload should create an upload transfer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()

                # Create a local file
                local_file = Path(tmpdir) / "upload.txt"
                local_file.write_text("test content")

                transfer = manager.add_upload(str(local_file), "/remote/dir")

                assert transfer is not None
                assert transfer.direction == TransferDirection.UPLOAD
                assert transfer.remote_path == "/remote/dir/upload.txt"
                assert transfer.status == TransferStatus.QUEUED

    def test_add_upload_nonexistent_file(self):
        """add_upload should raise for nonexistent file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()

                with pytest.raises(FileNotFoundError):
                    manager.add_upload("/nonexistent/file.txt", "/remote/dir")


class TestTransferManagerPauseAll:
    """Tests for pause_all/resume_all."""

    def test_pause_all_pauses_active(self):
        """pause_all should pause all transferring transfers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()

                t1 = Transfer(
                    id="t1",
                    remote_path="/file1.txt",
                    local_path="/tmp/file1.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.TRANSFERRING,
                )
                t2 = Transfer(
                    id="t2",
                    remote_path="/file2.txt",
                    local_path="/tmp/file2.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.TRANSFERRING,
                )
                manager.queue.transfers.extend([t1, t2])

                manager._tasks["t1"] = MagicMock()
                manager._tasks["t2"] = MagicMock()

                count = manager.pause_all()

                assert count == 2

    def test_resume_all_resumes_paused(self):
        """resume_all should resume all paused transfers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                manager = TransferManager()

                t1 = Transfer(
                    id="t1",
                    remote_path="/file1.txt",
                    local_path="/tmp/file1.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.PAUSED,
                )
                t2 = Transfer(
                    id="t2",
                    remote_path="/file2.txt",
                    local_path="/tmp/file2.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.PAUSED,
                )
                manager.queue.transfers.extend([t1, t2])

                count = manager.resume_all()

                assert count == 2
                assert t1.status == TransferStatus.QUEUED
                assert t2.status == TransferStatus.QUEUED


class TestTransferManagerMaxConcurrent:
    """Tests for max concurrent transfers."""

    def test_queue_respects_max_concurrent(self):
        """Queue should respect max_concurrent setting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                settings = AppSettings(max_concurrent_transfers=2)
                manager = TransferManager(settings=settings)

                assert manager.queue.max_concurrent == 2

    def test_can_start_more_respects_limit(self):
        """can_start_more should return False when at limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("queued.config.get_cache_dir", return_value=Path(tmpdir)):
                settings = AppSettings(max_concurrent_transfers=2)
                manager = TransferManager(settings=settings)

                # Add two active transfers
                t1 = Transfer(
                    id="t1",
                    remote_path="/file1.txt",
                    local_path="/tmp/file1.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.TRANSFERRING,
                )
                t2 = Transfer(
                    id="t2",
                    remote_path="/file2.txt",
                    local_path="/tmp/file2.txt",
                    direction=TransferDirection.DOWNLOAD,
                    size=1000,
                    status=TransferStatus.TRANSFERRING,
                )
                manager.queue.transfers.extend([t1, t2])

                assert manager.queue.can_start_more is False


class TestFileVerification:
    """Tests for verify_file function."""

    @pytest.mark.asyncio
    async def test_verify_file_not_found(self):
        """Should return failure when local file doesn't exist."""
        mock_sftp = AsyncMock()

        success, message = await verify_file(
            "/remote/file.txt", "/nonexistent/file.txt", 1000, mock_sftp
        )

        assert success is False
        assert "not found" in message.lower()

    @pytest.mark.asyncio
    async def test_verify_with_remote_md5_match(self):
        """Should verify successfully when remote MD5 matches local."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a local file with known content
            local_path = Path(tmpdir) / "file.txt"
            content = b"test content for md5"
            local_path.write_bytes(content)
            expected_md5 = hashlib.md5(content).hexdigest()

            # Mock SFTP client
            mock_sftp = AsyncMock()
            mock_sftp.list_dir = AsyncMock(return_value=[])  # No checksum files
            mock_sftp.compute_remote_md5 = AsyncMock(return_value=expected_md5)

            success, message = await verify_file(
                "/remote/file.txt",
                str(local_path),
                len(content),
                mock_sftp,
            )

            assert success is True
            assert "MD5 match" in message

    @pytest.mark.asyncio
    async def test_verify_with_remote_md5_mismatch(self):
        """Should fail when remote MD5 doesn't match local."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a local file
            local_path = Path(tmpdir) / "file.txt"
            local_path.write_bytes(b"local content")

            # Mock SFTP client with different MD5
            mock_sftp = AsyncMock()
            mock_sftp.list_dir = AsyncMock(return_value=[])
            mock_sftp.compute_remote_md5 = AsyncMock(return_value="different_hash_12345")

            success, message = await verify_file(
                "/remote/file.txt", str(local_path), 100, mock_sftp
            )

            assert success is False
            assert "mismatch" in message.lower()

    @pytest.mark.asyncio
    async def test_verify_fallback_to_size_match(self):
        """Should fall back to size comparison when MD5 unavailable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a local file
            local_path = Path(tmpdir) / "file.txt"
            content = b"test content"
            local_path.write_bytes(content)

            # Mock SFTP - no checksum files, no MD5 available
            mock_sftp = AsyncMock()
            mock_sftp.list_dir = AsyncMock(return_value=[])
            mock_sftp.compute_remote_md5 = AsyncMock(return_value=None)

            success, message = await verify_file(
                "/remote/file.txt", str(local_path), len(content), mock_sftp
            )

            assert success is True
            assert "size match only" in message.lower()

    @pytest.mark.asyncio
    async def test_verify_size_mismatch(self):
        """Should fail when sizes don't match and no checksum available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "file.txt"
            local_path.write_bytes(b"short")

            mock_sftp = AsyncMock()
            mock_sftp.list_dir = AsyncMock(return_value=[])
            mock_sftp.compute_remote_md5 = AsyncMock(return_value=None)

            success, message = await verify_file(
                "/remote/file.txt",
                str(local_path),
                1000,
                mock_sftp,  # Remote is larger
            )

            assert success is False
            assert "size mismatch" in message.lower()

    @pytest.mark.asyncio
    async def test_verify_with_md5_checksum_file(self):
        """Should use .md5 checksum file when available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create local file
            local_path = Path(tmpdir) / "file.txt"
            content = b"test content for checksum"
            local_path.write_bytes(content)
            expected_md5 = hashlib.md5(content).hexdigest()

            # Mock .md5 file in remote directory
            md5_file = MagicMock()
            md5_file.name = "checksums.md5"

            mock_sftp = AsyncMock()
            mock_sftp.list_dir = AsyncMock(return_value=[md5_file])
            mock_sftp.read_file = AsyncMock(return_value=f"{expected_md5}  file.txt\n".encode())

            success, message = await verify_file(
                "/remote/file.txt", str(local_path), len(content), mock_sftp
            )

            assert success is True
            assert "MD5 match" in message

    @pytest.mark.asyncio
    async def test_verify_with_md5_file_mismatch(self):
        """Should fail when .md5 file checksum doesn't match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "file.txt"
            local_path.write_bytes(b"local content")

            md5_file = MagicMock()
            md5_file.name = "checksums.md5"

            mock_sftp = AsyncMock()
            mock_sftp.list_dir = AsyncMock(return_value=[md5_file])
            mock_sftp.read_file = AsyncMock(
                return_value=b"differenthash12345678901234567890  file.txt\n"
            )

            success, message = await verify_file(
                "/remote/file.txt", str(local_path), 100, mock_sftp
            )

            assert success is False
            assert "mismatch" in message.lower()

    @pytest.mark.asyncio
    async def test_verify_with_sfv_checksum_file(self):
        """Should use .sfv checksum file when available."""
        import zlib

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "file.txt"
            content = b"test content for sfv"
            local_path.write_bytes(content)
            expected_crc = format(zlib.crc32(content) & 0xFFFFFFFF, "08x")

            sfv_file = MagicMock()
            sfv_file.name = "checksums.sfv"

            mock_sftp = AsyncMock()
            mock_sftp.list_dir = AsyncMock(return_value=[sfv_file])
            mock_sftp.read_file = AsyncMock(return_value=f"file.txt {expected_crc}\n".encode())

            success, message = await verify_file(
                "/remote/file.txt", str(local_path), len(content), mock_sftp
            )

            assert success is True
            assert "CRC32 match" in message
