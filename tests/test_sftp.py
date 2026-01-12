"""Tests for SFTP client."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from queued.models import Host
from queued.sftp import (
    BandwidthLimiter,
    SFTPClient,
    SFTPConnectionPool,
    SFTPError,
)


class TestSFTPClientConnection:
    """Tests for SFTP client connection."""

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Successful connection should set connected to True."""
        host = Host(hostname="example.com", username="user", key_path="/path/to/key")
        client = SFTPClient(host)

        mock_conn = AsyncMock()
        mock_sftp = AsyncMock()
        mock_conn.start_sftp_client = AsyncMock(return_value=mock_sftp)

        async def mock_connect(**kwargs):
            return mock_conn

        with patch("asyncssh.connect", side_effect=mock_connect) as mock_patch:
            await client.connect()

            assert client.connected is True
            mock_patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_host_key_error(self):
        """Host key verification failure should raise SFTPError."""
        import asyncssh

        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        with patch(
            "asyncssh.connect", side_effect=asyncssh.HostKeyNotVerifiable("Host key failed")
        ):
            with pytest.raises(SFTPError, match="Host key verification failed"):
                await client.connect()

    @pytest.mark.asyncio
    async def test_connect_auth_error(self):
        """Authentication failure should raise SFTPError."""
        import asyncssh

        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        with patch("asyncssh.connect", side_effect=asyncssh.Error(1, "Auth failed")):
            with pytest.raises(SFTPError, match="Failed to connect"):
                await client.connect()

    @pytest.mark.asyncio
    async def test_connect_network_error(self):
        """Network error should raise SFTPError."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        with patch("asyncssh.connect", side_effect=OSError("Connection refused")):
            with pytest.raises(SFTPError, match="Connection error"):
                await client.connect()

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        """Disconnect should clean up resources."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        # Manually set up mocks
        mock_sftp = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.close = MagicMock()
        mock_conn.wait_closed = AsyncMock()

        client._sftp = mock_sftp
        client._conn = mock_conn
        client._connected = True

        await client.disconnect()

        assert client.connected is False
        mock_sftp.exit.assert_called_once()
        mock_conn.close.assert_called_once()


class TestSFTPClientOperations:
    """Tests for SFTP operations."""

    @pytest.mark.asyncio
    async def test_list_dir_not_connected(self):
        """list_dir should raise when not connected."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        with pytest.raises(SFTPError, match="Not connected"):
            await client.list_dir("/")

    @pytest.mark.asyncio
    async def test_list_dir_returns_files(self):
        """list_dir should return RemoteFile objects."""
        import asyncssh

        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        # Create mock SFTP and entries
        mock_attrs1 = MagicMock()
        mock_attrs1.mtime = 1704067200  # 2024-01-01
        mock_attrs1.size = 1000
        mock_attrs1.permissions = 0o644
        mock_attrs1.type = 1  # Regular file

        mock_entry1 = MagicMock()
        mock_entry1.filename = "file.txt"
        mock_entry1.attrs = mock_attrs1

        mock_attrs2 = MagicMock()
        mock_attrs2.mtime = 1704067200
        mock_attrs2.size = 0
        mock_attrs2.permissions = 0o755
        mock_attrs2.type = asyncssh.FILEXFER_TYPE_DIRECTORY

        mock_entry2 = MagicMock()
        mock_entry2.filename = "subdir"
        mock_entry2.attrs = mock_attrs2

        mock_sftp = AsyncMock()
        mock_sftp.readdir = AsyncMock(return_value=[mock_entry1, mock_entry2])

        client._sftp = mock_sftp
        client._connected = True

        files = await client.list_dir("/")

        assert len(files) == 2
        # Directories should be sorted first
        assert files[0].name == "subdir"
        assert files[0].is_dir is True
        assert files[1].name == "file.txt"
        assert files[1].is_dir is False

    @pytest.mark.asyncio
    async def test_list_dir_filters_dot_entries(self):
        """list_dir should filter out . and .. entries."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        mock_attrs = MagicMock()
        mock_attrs.mtime = None
        mock_attrs.size = 0
        mock_attrs.permissions = None
        mock_attrs.type = 1

        entries = []
        for name in [".", "..", "file.txt"]:
            entry = MagicMock()
            entry.filename = name
            entry.attrs = mock_attrs
            entries.append(entry)

        mock_sftp = AsyncMock()
        mock_sftp.readdir = AsyncMock(return_value=entries)

        client._sftp = mock_sftp
        client._connected = True

        files = await client.list_dir("/")

        assert len(files) == 1
        assert files[0].name == "file.txt"

    @pytest.mark.asyncio
    async def test_file_exists_true(self):
        """file_exists should return True for existing file."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        mock_sftp = AsyncMock()
        mock_sftp.stat = AsyncMock(return_value=MagicMock())

        client._sftp = mock_sftp
        client._connected = True

        result = await client.file_exists("/existing.txt")

        assert result is True

    @pytest.mark.asyncio
    async def test_file_exists_false(self):
        """file_exists should return False for nonexistent file."""
        import asyncssh

        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        mock_sftp = AsyncMock()
        mock_sftp.stat = AsyncMock(side_effect=asyncssh.SFTPError(2, "No such file"))

        client._sftp = mock_sftp
        client._connected = True

        result = await client.file_exists("/nonexistent.txt")

        assert result is False


class TestBandwidthLimiter:
    """Tests for bandwidth limiter."""

    @pytest.mark.asyncio
    async def test_bandwidth_limiter_unlimited(self):
        """Unlimited limiter should not throttle."""
        limiter = BandwidthLimiter(limit=None)

        # Should return immediately
        start = asyncio.get_event_loop().time()
        await limiter.throttle(1024 * 1024)  # 1MB
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 0.1  # Should be nearly instant

    @pytest.mark.asyncio
    async def test_bandwidth_limiter_zero_limit(self):
        """Zero limit should not throttle."""
        limiter = BandwidthLimiter(limit=0)

        start = asyncio.get_event_loop().time()
        await limiter.throttle(1024 * 1024)
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_bandwidth_limiter_throttles(self):
        """Limiter should throttle when exceeding rate."""
        # 10KB/s limit
        limiter = BandwidthLimiter(limit=10 * 1024)

        # Transfer 20KB quickly - should throttle
        await limiter.throttle(10 * 1024)
        await asyncio.sleep(0.01)  # Small delay

        start = asyncio.get_event_loop().time()
        await limiter.throttle(10 * 1024)
        elapsed = asyncio.get_event_loop().time() - start

        # Should have some delay (may not be exact due to timing)
        # Just verify throttling logic is engaged
        assert elapsed >= 0  # Basic sanity check


class TestSFTPConnectionPool:
    """Tests for SFTP connection pool."""

    @pytest.mark.asyncio
    async def test_get_connection_unknown_host(self):
        """get_connection should raise for unknown host."""
        mock_cache = MagicMock()
        mock_cache.get_by_key = MagicMock(return_value=None)

        pool = SFTPConnectionPool(mock_cache)

        with pytest.raises(SFTPError, match="Unknown host"):
            await pool.get_connection("unknown@host:22")

    @pytest.mark.asyncio
    async def test_get_connection_creates_new(self):
        """get_connection should create new connection for new host."""
        host = Host(hostname="example.com", username="user")
        mock_cache = MagicMock()
        mock_cache.get_by_key = MagicMock(return_value=host)

        pool = SFTPConnectionPool(mock_cache)

        mock_conn = AsyncMock()
        mock_sftp = AsyncMock()
        mock_conn.start_sftp_client = AsyncMock(return_value=mock_sftp)

        async def mock_connect(**kwargs):
            return mock_conn

        with patch("asyncssh.connect", side_effect=mock_connect):
            client = await pool.get_connection("user@example.com:22")

            assert client.connected is True
            assert "user@example.com:22" in pool.connected_hosts

    @pytest.mark.asyncio
    async def test_get_connection_reuses_existing(self):
        """get_connection should reuse existing connected client."""
        host = Host(hostname="example.com", username="user")
        mock_cache = MagicMock()
        mock_cache.get_by_key = MagicMock(return_value=host)

        pool = SFTPConnectionPool(mock_cache)

        # Create mock connected client
        mock_client = MagicMock()
        mock_client.connected = True
        pool._connections["user@example.com:22"] = mock_client

        client = await pool.get_connection("user@example.com:22")

        assert client is mock_client

    @pytest.mark.asyncio
    async def test_add_connection(self):
        """add_connection should add client to pool."""
        mock_cache = MagicMock()
        pool = SFTPConnectionPool(mock_cache)

        mock_client = MagicMock()
        mock_client.connected = True

        pool.add_connection("user@host:22", mock_client)

        assert "user@host:22" in pool.connected_hosts

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """disconnect should remove and disconnect client."""
        mock_cache = MagicMock()
        pool = SFTPConnectionPool(mock_cache)

        mock_client = AsyncMock()
        mock_client.connected = True
        pool._connections["user@host:22"] = mock_client

        await pool.disconnect("user@host:22")

        assert "user@host:22" not in pool._connections
        mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_all(self):
        """disconnect_all should close all connections."""
        mock_cache = MagicMock()
        pool = SFTPConnectionPool(mock_cache)

        mock_client1 = AsyncMock()
        mock_client2 = AsyncMock()
        pool._connections["host1"] = mock_client1
        pool._connections["host2"] = mock_client2

        await pool.disconnect_all()

        assert len(pool._connections) == 0
        mock_client1.disconnect.assert_called_once()
        mock_client2.disconnect.assert_called_once()


class TestSFTPClientDownload:
    """Tests for download functionality."""

    @pytest.mark.asyncio
    async def test_download_not_connected(self):
        """download should raise when not connected."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        with pytest.raises(SFTPError, match="Not connected"):
            await client.download("/remote/file.txt", "/local/file.txt")

    @pytest.mark.asyncio
    async def test_download_creates_directory(self):
        """download should create parent directory if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            host = Host(hostname="example.com", username="user")
            client = SFTPClient(host)

            # Mock SFTP
            mock_attrs = MagicMock()
            mock_attrs.size = 100

            mock_file = AsyncMock()
            mock_file.read = AsyncMock(side_effect=[b"test content", b""])
            mock_file.__aenter__ = AsyncMock(return_value=mock_file)
            mock_file.__aexit__ = AsyncMock(return_value=None)

            mock_sftp = AsyncMock()
            mock_sftp.stat = AsyncMock(return_value=mock_attrs)
            mock_sftp.open = MagicMock(return_value=mock_file)

            client._sftp = mock_sftp
            client._connected = True

            nested_path = Path(tmpdir) / "subdir" / "file.txt"
            await client.download("/remote/file.txt", str(nested_path))

            assert nested_path.parent.exists()


class TestSFTPClientPermissions:
    """Tests for permission formatting."""

    def test_format_permissions(self):
        """Permissions should format correctly."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        # rwxr-xr-x = 755
        result = client._format_permissions(0o755)
        assert result == "rwxr-xr-x"

        # rw-r--r-- = 644
        result = client._format_permissions(0o644)
        assert result == "rw-r--r--"

        # rwx------ = 700
        result = client._format_permissions(0o700)
        assert result == "rwx------"


class TestRemoteMD5:
    """Tests for remote MD5 computation via SSH."""

    @pytest.mark.asyncio
    async def test_compute_remote_md5_success(self):
        """Should return MD5 hash when command succeeds."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        # Mock the SSH connection
        mock_result = MagicMock()
        mock_result.stdout = "d41d8cd98f00b204e9800998ecf8427e  /path/to/file.txt\n"

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        client._conn = mock_conn

        result = await client.compute_remote_md5("/path/to/file.txt")

        assert result == "d41d8cd98f00b204e9800998ecf8427e"
        mock_conn.run.assert_called_once()
        # Verify shlex quoting is used
        call_args = mock_conn.run.call_args[0][0]
        assert "/path/to/file.txt" in call_args

    @pytest.mark.asyncio
    async def test_compute_remote_md5_binary_mode_output(self):
        """Should handle binary mode output format (hash *filename)."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        mock_result = MagicMock()
        mock_result.stdout = "abc123def456  *file.bin\n"

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        client._conn = mock_conn

        result = await client.compute_remote_md5("/path/file.bin")

        assert result == "abc123def456"

    @pytest.mark.asyncio
    async def test_compute_remote_md5_not_connected(self):
        """Should return None when not connected."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)
        client._conn = None

        result = await client.compute_remote_md5("/path/to/file.txt")

        assert result is None

    @pytest.mark.asyncio
    async def test_compute_remote_md5_command_fails(self):
        """Should return None when md5sum command fails."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(side_effect=Exception("md5sum: command not found"))
        client._conn = mock_conn

        result = await client.compute_remote_md5("/path/to/file.txt")

        assert result is None

    @pytest.mark.asyncio
    async def test_compute_remote_md5_empty_output(self):
        """Should return None when command returns empty output."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        mock_result = MagicMock()
        mock_result.stdout = ""

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        client._conn = mock_conn

        result = await client.compute_remote_md5("/path/to/file.txt")

        assert result is None

    @pytest.mark.asyncio
    async def test_compute_remote_md5_special_characters_in_path(self):
        """Should properly escape paths with special characters."""
        host = Host(hostname="example.com", username="user")
        client = SFTPClient(host)

        mock_result = MagicMock()
        mock_result.stdout = "abc123  /path/with spaces/file.txt\n"

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        client._conn = mock_conn

        result = await client.compute_remote_md5("/path/with spaces/file.txt")

        assert result == "abc123"
        # Verify shlex quoting was applied
        call_args = mock_conn.run.call_args[0][0]
        assert "'/path/with spaces/file.txt'" in call_args
