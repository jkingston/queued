"""Tests for data models."""

from datetime import datetime

from queued.models import (
    Host,
    RemoteFile,
    Transfer,
    TransferDirection,
    TransferQueue,
    TransferStatus,
)


class TestTransferProgress:
    """Tests for Transfer.progress property."""

    def test_progress_at_zero(self):
        """Progress should be 0% when no bytes transferred."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
            bytes_transferred=0,
        )
        assert transfer.progress == 0.0

    def test_progress_at_half(self):
        """Progress should be 50% when half transferred."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
            bytes_transferred=500,
        )
        assert transfer.progress == 50.0

    def test_progress_complete(self):
        """Progress should be 100% when fully transferred."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
            bytes_transferred=1000,
        )
        assert transfer.progress == 100.0

    def test_progress_zero_size_file(self):
        """Zero size file should show 100% progress."""
        transfer = Transfer(
            id="test-1",
            remote_path="/empty.txt",
            local_path="/tmp/empty.txt",
            direction=TransferDirection.DOWNLOAD,
            size=0,
            bytes_transferred=0,
        )
        assert transfer.progress == 100.0


class TestTransferSpeedHuman:
    """Tests for Transfer.speed_human property."""

    def test_speed_zero(self):
        """Zero speed should display as 0 B/s."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
            speed=0,
        )
        assert transfer.speed_human == "0 B/s"

    def test_speed_bytes(self):
        """Speed under 1KB should display in B/s."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
            speed=512,
        )
        assert transfer.speed_human == "512.0 B/s"

    def test_speed_kilobytes(self):
        """Speed in KB range should display in KB/s."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
            speed=1024 * 50,  # 50 KB/s
        )
        assert transfer.speed_human == "50.0 KB/s"

    def test_speed_megabytes(self):
        """Speed in MB range should display in MB/s."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
            speed=1024 * 1024 * 10,  # 10 MB/s
        )
        assert transfer.speed_human == "10.0 MB/s"


class TestTransferETA:
    """Tests for Transfer.eta property."""

    def test_eta_when_not_transferring(self):
        """ETA should be None when not actively transferring."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
            status=TransferStatus.QUEUED,
            speed=100,
        )
        assert transfer.eta is None

    def test_eta_when_speed_zero(self):
        """ETA should be None when speed is zero."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
            status=TransferStatus.TRANSFERRING,
            speed=0,
        )
        assert transfer.eta is None

    def test_eta_seconds(self):
        """ETA under 1 minute should show seconds."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
            bytes_transferred=500,
            status=TransferStatus.TRANSFERRING,
            speed=100,  # 500 bytes remaining / 100 B/s = 5 seconds
        )
        assert transfer.eta == "5s"

    def test_eta_minutes(self):
        """ETA over 1 minute should show minutes and seconds."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=10000,
            bytes_transferred=0,
            status=TransferStatus.TRANSFERRING,
            speed=100,  # 10000 bytes / 100 B/s = 100 seconds = 1m 40s
        )
        assert transfer.eta == "1m 40s"

    def test_eta_hours(self):
        """ETA over 1 hour should show hours and minutes."""
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1024 * 1024 * 100,  # 100 MB
            bytes_transferred=0,
            status=TransferStatus.TRANSFERRING,
            speed=1024 * 10,  # 10 KB/s -> ~2.8 hours
        )
        eta = transfer.eta
        assert eta is not None
        assert "h" in eta


class TestTransferQueue:
    """Tests for TransferQueue methods."""

    def test_has_queued_in_directory_true(self):
        """Should return True when directory contains queued files."""
        queue = TransferQueue()
        queue.transfers.append(
            Transfer(
                id="test-1",
                remote_path="/media/files/movie.mkv",
                local_path="/tmp/movie.mkv",
                direction=TransferDirection.DOWNLOAD,
                size=1000,
                status=TransferStatus.QUEUED,
            )
        )

        assert queue.has_queued_in_directory("/media/files") is True
        assert queue.has_queued_in_directory("/media") is True

    def test_has_queued_in_directory_false(self):
        """Should return False when no queued files in directory."""
        queue = TransferQueue()
        queue.transfers.append(
            Transfer(
                id="test-1",
                remote_path="/other/file.txt",
                local_path="/tmp/file.txt",
                direction=TransferDirection.DOWNLOAD,
                size=1000,
                status=TransferStatus.QUEUED,
            )
        )

        assert queue.has_queued_in_directory("/media/files") is False

    def test_has_queued_excludes_completed(self):
        """Should not count completed transfers."""
        queue = TransferQueue()
        queue.transfers.append(
            Transfer(
                id="test-1",
                remote_path="/media/files/done.txt",
                local_path="/tmp/done.txt",
                direction=TransferDirection.DOWNLOAD,
                size=1000,
                status=TransferStatus.COMPLETED,
            )
        )

        assert queue.has_queued_in_directory("/media/files") is False

    def test_is_queued_returns_true_for_queued(self):
        """is_queued should return True for queued files."""
        queue = TransferQueue()
        queue.transfers.append(
            Transfer(
                id="test-1",
                remote_path="/file.txt",
                local_path="/tmp/file.txt",
                direction=TransferDirection.DOWNLOAD,
                size=1000,
                status=TransferStatus.QUEUED,
            )
        )

        assert queue.is_queued("/file.txt") is True

    def test_is_queued_excludes_completed(self):
        """is_queued should return False for completed files."""
        queue = TransferQueue()
        queue.transfers.append(
            Transfer(
                id="test-1",
                remote_path="/file.txt",
                local_path="/tmp/file.txt",
                direction=TransferDirection.DOWNLOAD,
                size=1000,
                status=TransferStatus.COMPLETED,
            )
        )

        assert queue.is_queued("/file.txt") is False

    def test_is_queued_excludes_failed(self):
        """is_queued should return False for failed files."""
        queue = TransferQueue()
        queue.transfers.append(
            Transfer(
                id="test-1",
                remote_path="/file.txt",
                local_path="/tmp/file.txt",
                direction=TransferDirection.DOWNLOAD,
                size=1000,
                status=TransferStatus.FAILED,
            )
        )

        assert queue.is_queued("/file.txt") is False

    def test_get_by_remote_path(self):
        """Should find transfer by remote path."""
        queue = TransferQueue()
        transfer = Transfer(
            id="test-1",
            remote_path="/file.txt",
            local_path="/tmp/file.txt",
            direction=TransferDirection.DOWNLOAD,
            size=1000,
        )
        queue.transfers.append(transfer)

        found = queue.get_by_remote_path("/file.txt")
        assert found is not None
        assert found.id == "test-1"

    def test_get_by_remote_path_not_found(self):
        """Should return None when path not found."""
        queue = TransferQueue()
        found = queue.get_by_remote_path("/nonexistent.txt")
        assert found is None

    def test_get_by_remote_path_with_host_key(self):
        """Should match by host_key when provided."""
        queue = TransferQueue()
        queue.transfers.append(
            Transfer(
                id="test-1",
                remote_path="/file.txt",
                local_path="/tmp/file.txt",
                direction=TransferDirection.DOWNLOAD,
                size=1000,
                host_key="user@host1:22",
            )
        )
        queue.transfers.append(
            Transfer(
                id="test-2",
                remote_path="/file.txt",
                local_path="/tmp/file2.txt",
                direction=TransferDirection.DOWNLOAD,
                size=1000,
                host_key="user@host2:22",
            )
        )

        found = queue.get_by_remote_path("/file.txt", "user@host2:22")
        assert found is not None
        assert found.id == "test-2"


class TestHostSerialization:
    """Tests for Host serialization."""

    def test_host_roundtrip_serialization(self):
        """Host should serialize and deserialize correctly."""
        host = Host(
            hostname="example.com",
            username="testuser",
            port=2222,
            key_path="/home/user/.ssh/id_rsa",
            last_used=datetime(2024, 1, 15, 10, 30),
            last_directory="/home/testuser",
        )

        data = host.to_dict()
        restored = Host.from_dict(data)

        assert restored.hostname == host.hostname
        assert restored.username == host.username
        assert restored.port == host.port
        assert restored.key_path == host.key_path
        assert restored.last_used == host.last_used
        assert restored.last_directory == host.last_directory

    def test_host_password_obfuscation(self):
        """Password should be base64 encoded in serialized form."""
        host = Host(
            hostname="example.com",
            username="testuser",
            password="secret123",
        )

        data = host.to_dict()
        assert "password" not in data
        assert "_pw" in data
        assert data["_pw"] != "secret123"

        # Should decode correctly
        restored = Host.from_dict(data)
        assert restored.password == "secret123"

    def test_host_key_format(self):
        """host_key should be user@hostname:port."""
        host = Host(hostname="example.com", username="testuser", port=22)
        assert host.host_key == "testuser@example.com:22"

    def test_host_str_format(self):
        """str(host) should match host_key."""
        host = Host(hostname="example.com", username="testuser", port=22)
        assert str(host) == "testuser@example.com:22"


class TestRemoteFile:
    """Tests for RemoteFile model."""

    def test_size_human_directory(self):
        """Directory should show <DIR> for size."""
        file = RemoteFile(
            name="subdir",
            path="/subdir",
            size=0,
            is_dir=True,
        )
        assert file.size_human == "<DIR>"

    def test_size_human_bytes(self):
        """Small files should show size in bytes."""
        file = RemoteFile(
            name="tiny.txt",
            path="/tiny.txt",
            size=512,
            is_dir=False,
        )
        assert file.size_human == "512.0 B"

    def test_size_human_kilobytes(self):
        """Files in KB range should show KB."""
        file = RemoteFile(
            name="file.txt",
            path="/file.txt",
            size=1024 * 5,  # 5 KB
            is_dir=False,
        )
        assert file.size_human == "5.0 KB"

    def test_size_human_megabytes(self):
        """Files in MB range should show MB."""
        file = RemoteFile(
            name="large.bin",
            path="/large.bin",
            size=1024 * 1024 * 100,  # 100 MB
            is_dir=False,
        )
        assert file.size_human == "100.0 MB"


class TestTransferSerialization:
    """Tests for Transfer serialization."""

    def test_transfer_roundtrip_serialization(self):
        """Transfer should serialize and deserialize correctly."""
        transfer = Transfer(
            id="test-123",
            remote_path="/media/file.mkv",
            local_path="/tmp/file.mkv",
            direction=TransferDirection.DOWNLOAD,
            size=1024 * 1024,
            host_key="user@host:22",
            status=TransferStatus.TRANSFERRING,
            bytes_transferred=512 * 1024,
            error=None,
            started_at=datetime(2024, 1, 15, 10, 30),
            checksum="abc123",
        )

        data = transfer.to_dict()
        restored = Transfer.from_dict(data)

        assert restored.id == transfer.id
        assert restored.remote_path == transfer.remote_path
        assert restored.local_path == transfer.local_path
        assert restored.direction == transfer.direction
        assert restored.size == transfer.size
        assert restored.host_key == transfer.host_key
        assert restored.status == transfer.status
        assert restored.bytes_transferred == transfer.bytes_transferred
        assert restored.started_at == transfer.started_at
        assert restored.checksum == transfer.checksum
